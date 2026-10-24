"""Behaviour tests for the local NLU, the yield maths and the HTTP API.

Three things are being protected here, in the order they matter:

1. **The floor is never breached.** Every recommendation for every item in every
   sector has to leave the margin at or above that sector's threshold.
2. **The language layer says the right thing.** Intent, entities and spelling
   repair are asserted on utterances, and the holdout score is checked to be a
   real measurement rather than a training score in disguise.
3. **The API contract the dashboard depends on.** Status codes, the shape of the
   copilot payload, and the engine following whatever stock is active.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app as app_module
import version
from nlu import IntentEngine, build_lexicon
from nlu.assistant import CopilotAssistant
from nlu.dataset import INTENTS
from nlu.knowledge import GLOSSARY, KnowledgeBase
from nlu.planner import plan as plan_answer
from yield_engine import bundle_plan

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="session")
def client():
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def nlu():
    """One trained engine plus the live context, shared by the whole session."""
    app_module.init_db()
    db = app_module.connect()
    try:
        profile, rows, items, perks, history = app_module.load_context(db)
    finally:
        db.close()
    return {
        "engine": IntentEngine(build_lexicon(profile, items)),
        "profile": profile,
        "rows": rows,
        "items": items,
        "perks": perks,
        "history": history,
    }


def answer(nlu_context, utterance, state=None):
    engine = nlu_context["engine"]
    parsed = engine.parse(utterance, state)
    reply = plan_answer(
        parsed,
        profile=nlu_context["profile"],
        rows=nlu_context["rows"],
        history=nlu_context["history"],
        perks=nlu_context["perks"],
        sectors=app_module.SECTORS,
    )
    return parsed, reply


# ---------------------------------------------------------------------------
# The floor property
# ---------------------------------------------------------------------------
def test_every_item_in_every_sector_respects_its_floor():
    now = datetime.now(timezone.utc)
    checked = 0
    for slug, preset in app_module.SECTORS.items():
        profile = {"marginFloorPct": preset["marginFloorPct"], "currencySymbol": "R"}
        perks = [
            {"PerkID": index, "PerkName": name, "Type": kind, "CostToBusiness": cost, "RetailValue": value, "AvailableStock": stock}
            for index, (name, kind, cost, value, stock) in enumerate(preset["perks"], start=1)
        ]
        for index, (name, category, price, window, capacity, sold) in enumerate(preset["items"], start=1):
            row = {
                "ItemID": index,
                "ItemName": name,
                "Category": category,
                "Sector": slug,
                "BasePrice": price,
                "ExpirationTimestamp": (now + timedelta(hours=window)).isoformat(),
                "TotalCapacity": capacity,
                "UnitsSold": sold,
            }
            plan = bundle_plan(row, perks, profile)
            metrics = plan["metrics"]
            assert metrics["floorRespected"], f"{slug}/{name} breached the floor"
            assert metrics["marginPreserved"] >= metrics["marginThreshold"], f"{slug}/{name} margin under floor"
            assert metrics["marginPreserved"] >= 0, f"{slug}/{name} retained negative margin"
            checked += 1
    assert checked >= 25, "expected the full catalogue to be covered"


# ---------------------------------------------------------------------------
# Model quality
# ---------------------------------------------------------------------------
def test_holdout_score_is_measured_on_unseen_phrasings(nlu):
    """The reported score must survive more than one lucky shuffle."""
    card = nlu["engine"].model_card()
    scores, training = card["scores"], card["training"]
    assert training["holdoutRows"] > 0
    assert training["evaluationSplits"] >= 3
    assert scores["holdoutAccuracy"] >= 0.80, scores
    assert scores["holdoutStdDev"] <= 0.08, "the score is too split-dependent to trust"
    assert scores["holdoutWorst"] >= 0.70, scores
    assert 0 < scores["crossValidationAccuracy"] <= 1
    assert training["rows"] > training["holdoutRows"], "the shipped model trains on more than one split holds out"


def test_model_card_names_the_open_source_components(nlu):
    card = nlu["engine"].model_card()
    names = {component["name"] for component in card["components"]}
    assert {"scikit-learn", "rapidfuzz", "symspellpy"} <= names
    assert card["vocabulary"]["englishWords"] > 10_000


# ---------------------------------------------------------------------------
# Language layer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("utterance", "expected"),
    [
        ("what should i deal with first", "priority"),
        ("how much are we going to bin tonight", "waste_risk"),
        ("what is the lowest price i can take on this", "margin_floor"),
        ("how do i move this without dropping the price", "bundle_recommend"),
        ("can i put it on special for ten percent off", "discount_request"),
        ("which of these two is worse for us", "compare_items"),
        ("what does the end of the day look like", "forecast_close"),
        ("what happens if we do not act", "what_if"),
        ("give me the state of play", "capacity_status"),
        ("show me the audit trail", "decision_history"),
        ("does this work for a restaurant", "sector_info"),
        ("what kinds of questions can i ask", "capabilities"),
        ("hiya", "greeting"),
        ("what is the nicest car in the world", "out_of_scope"),
    ],
)
def test_intent_regression(nlu, utterance, expected):
    parsed = nlu["engine"].parse(utterance)
    assert parsed.intent == expected, f"{utterance!r} -> {parsed.intent} ({parsed.confidence:.2f})"


def test_domain_vocabulary_outranks_general_english(nlu):
    """A near-miss on a domain word must repair to the catalogue term."""
    engine = nlu["engine"]
    repaired, corrections = engine.repair("two almon croisant please")
    assert "almond" in repaired, repaired
    assert "croissant" in repaired, repaired
    assert {"from": "almon", "to": "almond"} in corrections


def test_english_words_are_never_rewritten(nlu):
    """Typos get fixed; ordinary words keep their meaning."""
    repaired, _corrections = nlu["engine"].repair("tell me a joke about the drop in price")
    assert repaired == "tell me a joke about the drop in price"


def test_catalogue_matching_does_not_invent_items(nlu):
    """Words that merely resemble a product must not become one."""
    lexicon = nlu["engine"].lexicon
    assert lexicon.match_item("last night") is None
    parsed = nlu["engine"].parse("who won the game last night")
    assert [entity for entity in parsed.entities if entity["type"] == "item"] == []


def test_spelled_out_percentages_are_parsed(nlu):
    engine = nlu["engine"]
    parsed = engine.parse("knock twenty five percent off")
    percents = [entity["value"] for entity in parsed.entities if entity["type"] == "percent"]
    assert 25.0 in percents, parsed.entities
    parsed_digits = engine.parse("knock 20% off")
    percents_digits = [entity["value"] for entity in parsed_digits.entities if entity["type"] == "percent"]
    assert percents_digits == [20.0]


def test_follow_up_resolves_from_conversation_state(nlu):
    engine = nlu["engine"]
    first = engine.parse("what should i bundle with the flat white")
    state = {"lastItemId": first.focus_item_id, "lastItemLabel": "Flat white", "lastItemCategory": "Espresso bar"}
    second = engine.parse("what about that one", state)
    assert second.resolved_from_context is True
    assert second.focus_item_id == first.focus_item_id


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------
def test_answer_quotes_live_numbers(nlu):
    parsed, reply = answer(nlu, "what needs attention first")
    assert parsed.intent == "priority"
    focus = next(row for row in nlu["rows"] if row["ItemID"] == reply.focus_item_id)
    rendered = f"{nlu['profile']['currencySymbol']}{focus['BasePrice']:,.0f}"
    assert rendered in reply.answer, reply.answer
    assert reply.reasoning and len(reply.reasoning) >= 3


def test_markdown_below_the_floor_is_refused(nlu):
    floor = nlu["profile"]["marginFloorPct"]
    parsed, reply = answer(nlu, "cut the price by 60 percent")
    discount = reply.evidence[0]["discount"]
    assert discount["breachesFloor"] is True
    assert discount["newPrice"] < discount["floorThreshold"]
    assert reply.answer.startswith("No.")
    assert f"{floor}%" in reply.answer, "the refusal must state the floor it is protecting"


def test_scenario_compares_bundle_against_markdown(nlu):
    parsed, reply = answer(nlu, "what if i cut ten percent instead")
    assert parsed.intent == "what_if"
    discount = reply.evidence[0]["discount"]
    bundle = reply.evidence[1]["bundle"]
    assert bundle["recommendation"]["marginRetained"] > discount["newPrice"] - discount["floorThreshold"]


def test_ambiguous_question_asks_back_with_options(nlu):
    parsed, reply = answer(nlu, "do something about it")
    assert parsed.needs_clarification is True
    assert reply.clarify_options, "a clarifying reply must offer choices"
    assert all("intent" in option and "label" in option for option in reply.clarify_options)
    assert reply.clarify_options[0]["intent"] == parsed.intent


def test_confirmed_intent_overrides_the_classifier(nlu):
    """Choosing an option must resolve the ambiguity, including "yes, that one"."""
    engine = nlu["engine"]
    ambiguous = engine.parse("do something about it")
    assert ambiguous.needs_clarification is True

    switched = engine.confirm(engine.parse("do something about it"), "bundle_recommend")
    assert switched.intent == "bundle_recommend"
    assert switched.confirmed is True
    assert switched.needs_clarification is False
    assert all(option["intent"] != "bundle_recommend" for option in switched.alternatives)

    agreed = engine.confirm(engine.parse("do something about it"), "discount_request")
    assert agreed.intent == "discount_request"
    assert agreed.needs_clarification is False, "confirming the model's own guess must also clear the question"


def test_off_topic_question_is_declined_not_guessed(nlu):
    parsed, reply = answer(nlu, "recommend a movie")
    assert parsed.intent == "out_of_scope"
    assert "outside" in reply.answer


# ---------------------------------------------------------------------------
# HTTP API
# ---------------------------------------------------------------------------
def test_health_and_inventory(client):
    assert client.get("/api/health").json()["status"] == "ok"
    inventory = client.get("/api/inventory").json()
    assert inventory["items"] and inventory["profile"]["businessName"]


def test_the_version_health_reports_is_the_one_the_package_defines(client):
    # Regression: the interface footer, the FastAPI version and the MCP server each
    # used to state a version, and a release was tagged v1.0.0 while all three said
    # 1.1. One constant, and this test is what keeps a tag honest.
    assert client.get("/api/health").json()["version"] == version.VERSION
    assert version.VERSION.count(".") == 2, "a release version is major.minor.patch"


def test_copilot_returns_an_answer_and_its_working(client):
    response = client.post("/api/copilot", json={"question": "what needs attention first?", "sessionId": "api-test"})
    assert response.status_code == 200
    body = response.json()
    assert body["answer"]
    # The source names what actually answered. With no key configured that is the
    # local engine alone, and the reply must say so rather than implying a model
    # was involved.
    assert body["source"].startswith("local")
    assert "offline" in body["source"]
    assert body["assistant"]["used"] is False
    assert body["nlu"]["intent"] == "priority"
    assert body["nlu"]["reply"]["reasoning"]


def test_copilot_rejects_an_empty_question(client):
    assert client.post("/api/copilot", json={"question": "   "}).status_code == 422
    assert client.post("/api/copilot", json={"question": "x" * 401}).status_code == 422


def test_copilot_accepts_a_confirmed_intent(client):
    body = client.post(
        "/api/copilot",
        json={"question": "do something about it", "sessionId": "confirm", "intent": "bundle_recommend"},
    ).json()
    assert body["nlu"]["intent"] == "bundle_recommend"
    assert body["nlu"]["intentConfirmed"] is True
    assert body["nlu"]["needsClarification"] is False
    assert "confirmed by the manager" in " ".join(body["nlu"]["reply"]["reasoning"])
    rejected = client.post("/api/copilot", json={"question": "hello", "intent": "make_coffee"})
    assert rejected.status_code == 422


def test_parse_endpoint_exposes_the_language_layer(client):
    body = client.post("/api/nlu/parse", json={"question": "whats heading for waste"}).json()
    assert body["parse"]["intent"] == "waste_risk"
    assert body["parse"]["alternatives"]
    assert body["reply"]["answer"]


def test_model_card_endpoint(client):
    card = client.get("/api/nlu/model").json()
    assert card["runsLocally"] is True
    assert card["training"]["rows"] > 0
    assert card["sampleUtterances"]
    assert len(card["intents"]) == len(INTENTS)
    # The deciding model always runs here; whether anything leaves the machine is
    # exactly whether the optional assistant is configured, and the card must say
    # so rather than claiming offline when it is not.
    assert card["leavesMachine"] is card["assistant"]["enabled"]
    assert card["assistant"]["enabled"] is False, "the suite disables the network assistant"
    assert card["assistant"]["guardrails"]


def test_analyze_validates_the_item(client):
    assert client.post("/api/yield/analyze", json={"itemId": 999_999}).status_code == 404
    first = client.get("/api/inventory").json()["items"][0]
    analysis = client.post("/api/yield/analyze", json={"itemId": first["itemId"]}).json()
    assert analysis["metrics"]["floorRespected"] is True
    assert len(analysis["recommendation"]["tiers"]) == 3


def test_decisions_are_validated_and_logged(client):
    first = client.get("/api/inventory").json()["items"][0]
    bad = client.post(
        "/api/strategy/decide",
        json={"itemId": first["itemId"], "recommendedAction": "hold", "managerDecision": "Maybe", "marginRetained": 10},
    )
    assert bad.status_code == 422
    logged = client.post(
        "/api/strategy/decide",
        json={"itemId": first["itemId"], "recommendedAction": "Hold the price", "managerDecision": "Approved", "unitsFilled": 3, "marginRetained": 12.5},
    )
    assert logged.status_code == 200
    decisions = client.get("/api/strategy/history").json()["decisions"]
    assert any(entry["strategyId"] == logged.json()["strategyId"] for entry in decisions)


def test_the_engine_is_rebuilt_for_the_inventory_it_is_now_answering_about(client):
    """Choosing a sector is gone; rebuilding the engine for new stock is not.

    Sector switching was a way of picking a workspace, and the product now has
    exactly one: the business whose database was linked. What the old test was
    really protecting survives that change - the intent engine follows the
    inventory instead of staying warm on the old catalogue, so a bakery shelf is
    answered in bakery vocabulary at bakery prices.
    """
    bakery = [
        ("Cinnamon bun", "Pastry", 34.0, 80, 22, 2.0),
        ("Chocolate eclair", "Cakes", 46.0, 48, 15, 3.0),
        ("Sourdough loaf", "Breads", 58.0, 60, 54, 4.0),
    ]
    lines = [
        {
            "itemName": name,
            "category": category,
            "basePrice": price,
            "totalCapacity": capacity,
            "unitsSold": sold,
            "shelfHoursUntilClosed": hours,
        }
        for name, category, price, capacity, sold, hours in bakery
    ]

    db = app_module.connect()
    try:
        profile = app_module.connect_business(db, "Yeast & Crumb", lines)
        items = [
            app_module.inventory_item(row)
            for row in db.execute("SELECT * FROM Inventory ORDER BY ExpirationTimestamp")
        ]
        app_module.retrain_active(profile, items)
    finally:
        db.close()

    try:
        response = client.post(
            "/api/copilot",
            json={"question": "what should i bundle with the cinnamon bun", "sessionId": "bakery"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["nlu"]["intent"] == "bundle_recommend"
        assert any(entity.get("value") == "Cinnamon bun" for entity in body["nlu"]["entities"])
        assert "R34" in body["answer"], "the bakery line must be priced with the bakery catalogue"
        assert client.get("/api/nlu/model").json()["profile"]["sector"] == "connected"
    finally:
        # Put the shared workspace back on the preset, so this test leaves the
        # session the way it found it rather than quietly answering the next
        # test's questions about a business that was only here for this test.
        restore = app_module.connect()
        try:
            restore.execute(
                "UPDATE Profile SET Connected = 0, SectorSlug = 'coffee' WHERE ProfileID = 1"
            )
            app_module.seed_sector(restore, "coffee")
            restore.commit()
        finally:
            restore.close()

    assert client.get("/api/profile").json()["sector"] == "coffee"
    # The engine must be rebuilt for the restored preset, not left on bakery.
    restored = client.post("/api/copilot", json={"question": "what needs attention first?", "sessionId": "restored"})
    assert restored.json()["nlu"]["intent"] == "priority"


# ---------------------------------------------------------------------------
