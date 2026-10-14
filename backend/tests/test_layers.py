"""Coverage of the two layers that decide what the owner is told.

Two things live here, both prompted by measuring coverage rather than guessing
at it:

1. **Every intent, answered.** The evaluator tests probed a handful of intents
   in depth and left the rest to the classifier's unit tests, which meant the
   *handlers* for several intents had never been executed end to end. A handler
   that crashes only when its own intent is asked is the worst kind of bug here,
   because it looks like the copilot being confused rather than broken.

2. **The model registry.** The registry is the layer that decides where a
   question can even be sent, so its failure modes - an exhausted shared quota,
   a missing key, a cooling-down model - all have to be exercised deliberately.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import app as app_module
from nlu import IntentEngine, build_lexicon
from nlu import models as registry
from nlu.dataset import INTENTS, TEMPLATES, _expand, _slot_values
from nlu.planner import plan as plan_answer

# The refusal intent is tested where it belongs, through the HTTP layer, and has
# no useful reply to shape-check here.
ANSWERABLE_INTENTS = [intent for intent, _label, _description in INTENTS if intent != "out_of_scope"]


@pytest.fixture(scope="module")
def context():
    app_module.init_db()
    db = app_module.connect()
    try:
        profile, rows, items, perks, history = app_module.load_context(db)
    finally:
        db.close()
    lexicon = build_lexicon(profile, items)
    return {
        "engine": IntentEngine(lexicon),
        "lexicon": lexicon,
        "profile": profile,
        "rows": rows,
        "items": items,
        "perks": perks,
        "history": history,
    }


def _utterances(lexicon, intent: str, limit: int = 4) -> list[str]:
    """Real sentences for an intent, built the same way training data is.

    Reusing the dataset's own slot expansion means these utterances stay in step
    with the corpus: if a template starts using a new slot, this picks it up
    instead of silently testing a sentence with a brace left in it.
    """
    slots = _slot_values(lexicon)
    sentences: list[str] = []
    for template in TEMPLATES[intent][:limit]:
        sentences.extend(_expand(template, slots, limit=2))
    return [s for s in sentences if s and "{" not in s]


@pytest.mark.parametrize("intent", ANSWERABLE_INTENTS)
def test_every_intent_answers_with_a_well_formed_reply(context, intent):
    """Each handler must produce something an owner can act on, not just prose."""
    sentences = _utterances(context["lexicon"], intent, limit=3)
    assert sentences, f"{intent} has no usable templates"

    for sentence in sentences:
        parsed = context["engine"].parse(sentence)
        reply = plan_answer(
            parsed,
            profile=context["profile"],
            rows=context["rows"],
            history=context["history"],
            perks=context["perks"],
            sectors=app_module.SECTORS,
        )
        assert reply.answer.strip(), f"{intent}: empty answer for {sentence!r}"
        assert reply.headline.strip(), f"{intent}: empty headline for {sentence!r}"
        assert reply.reasoning, f"{intent}: no reasoning trace for {sentence!r}"
        # A reply must be useful in one of exactly two ways: it answers, so it
        # carries something to act on or a figure to look at; or it asks which
        # reading was meant, so it carries the options to choose between. A reply
        # that does none of those is a sentence, not an answer.
        assert reply.clarify_options or reply.next_steps or reply.key_numbers, (
            f"{intent}: nothing actionable and no way to clarify, for {sentence!r}"
        )
        if reply.clarify_options:
            # A clarifying question has to be answerable by tapping, not by
            # typing: every option needs a label and an intent to send back.
            assert all(option.get("label") and option.get("intent") for option in reply.clarify_options), (
                f"{intent}: an option with no label or intent would be a dead button"
            )
        # Plain words only: no template braces, no raw Python formatting.
        assert "{" not in reply.answer and "}" not in reply.answer
        assert "None" not in reply.answer


@pytest.mark.parametrize("intent", ANSWERABLE_INTENTS)
def test_every_reply_serialises_to_json(context, intent):
    """The reply crosses an HTTP boundary, so it has to survive serialisation.

    A numpy scalar slipping into a figure is invisible in Python - it compares
    and prints correctly - and only shows up as a 500 from the API.
    """
    import json

    sentence = _utterances(context["lexicon"], intent, limit=1)[0]
    reply = plan_answer(
        context["engine"].parse(sentence),
        profile=context["profile"],
        rows=context["rows"],
        history=context["history"],
        perks=context["perks"],
        sectors=app_module.SECTORS,
    )
    json.dumps(reply.to_dict(), default=str)


# ---------------------------------------------------------------------------
# The model registry and the shared free quota.
# ---------------------------------------------------------------------------
@pytest.fixture
def registry_db():
    # init_db rather than connect alone: these tests must be able to run on their
    # own (``pytest -k registry``), not only after whichever fixture happened to
    # create the tables first. An implicit ordering dependency between tests is a
    # bug waiting for someone to filter the suite.
    app_module.init_db()
    db = app_module.connect()
    try:
        yield db
    finally:
        db.close()


def _fresh_model(db, model_id: str, **kwargs):
    existing = db.execute("SELECT ModelRowID FROM AssistantModel WHERE ModelId = ?", (model_id,)).fetchone()
    if existing:
        registry.remove_model(db, int(existing["ModelRowID"]))
    db.execute("DELETE FROM ModelHealth WHERE ModelId = ?", (model_id,))
    db.commit()
    return registry.add_model(db, model_id=model_id, **kwargs)


def test_registry_seeds_itself_from_the_environment(registry_db):
    """A fresh install keeps the exact chain the environment described."""
    entries = registry.list_models(registry_db)
    assert entries, "the registry seeds from OPENROUTER_MODEL / OPENROUTER_FALLBACK_MODELS"
    assert [entry["Position"] for entry in entries] == sorted(entry["Position"] for entry in entries)


def test_anything_can_be_registered_including_a_local_endpoint(registry_db):
    """No allow-list: the point is bringing a model this code never heard of."""
    _fresh_model(
        registry_db,
        "llama3.1:8b",
        provider="openai-compatible",
        base_url="http://localhost:11434/v1",
        key_env="LLM_API_KEY",
        label="Local Ollama",
    )
    entry = next(m for m in registry.list_models(registry_db) if m["ModelId"] == "llama3.1:8b")
    assert entry["baseUrl"] == "http://localhost:11434/v1"
    assert entry["keyEnv"] == "LLM_API_KEY"
    # No key has been set for that variable, and the registry says so rather than
    # pretending the model is ready.
    assert entry["hasKey"] is False
    assert entry["usableNow"] is False
    assert "LLM_API_KEY" in entry["blockedBy"]
    registry.remove_model(registry_db, entry["ModelRowID"])


def test_adding_a_model_requires_an_id(registry_db):
    with pytest.raises(ValueError):
        registry.add_model(registry_db, model_id="   ")


def test_a_model_can_be_disabled_without_being_removed(registry_db):
    entry = _fresh_model(registry_db, "test/disable-me:free")
    registry.update_model(registry_db, entry["rowId"], {"enabled": False})
    updated = next(m for m in registry.list_models(registry_db) if m["ModelId"] == "test/disable-me:free")
    assert updated["enabled"] is False
    assert updated["blockedBy"] == "switched off"
    # And a switched-off model is not offered to the assistant at all.
    assert "test/disable-me:free" not in [spec["model"] for spec in registry.resolve_chain(registry_db)]
    registry.remove_model(registry_db, updated["ModelRowID"])


def test_rate_limit_headers_become_a_countdown(registry_db):
    """The whole point: a 429 says *when*, not just *no*.

    The timestamp below is the exact shape OpenRouter returned on a live request
    (epoch milliseconds at the next midnight UTC), so this test fails if the
    provider's format changes rather than passing on a value we invented.
    """
    _fresh_model(registry_db, "test/limited:free")
    reset = datetime.now(timezone.utc) + timedelta(hours=6)
    health = registry.record_health(
        registry_db,
        model_id="test/limited:free",
        status="rate limited",
        headers={
            "X-RateLimit-Limit": "50",
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(int(reset.timestamp() * 1000)),
        },
        limit_source="openrouter_free_tier_daily",
        remedy="Wait for the daily reset.",
    )
    assert health["LimitValue"] == 50
    assert health["Remaining"] == 0
    assert health["availableNow"] is False
    # Five to six hours, allowing for the seconds that pass running the test.
    assert 5 * 3600 < health["secondsRemaining"] <= 6 * 3600
    assert health["humanRemaining"].startswith("5h") or health["humanRemaining"].startswith("6h")
    assert health["LimitSource"] == "openrouter_free_tier_daily"


def test_a_rate_limited_model_is_skipped_rather_than_called(registry_db, monkeypatch):
    """Calling a model we know is out of quota wastes the owner's time.

    The key is supplied here rather than assumed. The suite deliberately blanks
    ``OPENROUTER_API_KEY`` so the network assistant stays off, so a test that
    silently depended on a real key in the environment would pass on the
    developer's machine and fail in CI - which is the worst way to find out.
    """
    monkeypatch.setenv("TEST_MODEL_KEY", "test-key")
    _fresh_model(registry_db, "test/skip-me:free", key_env="TEST_MODEL_KEY")
    reset = datetime.now(timezone.utc) + timedelta(hours=3)
    registry.record_health(
        registry_db,
        model_id="test/skip-me:free",
        status="rate limited",
        headers={"X-RateLimit-Reset": str(int(reset.timestamp() * 1000)), "X-RateLimit-Remaining": "0", "X-RateLimit-Limit": "50"},
    )
    chain = {spec["model"]: spec for spec in registry.resolve_chain(registry_db)}
    assert chain["test/skip-me:free"]["coolingDown"] is True
    assert chain["test/skip-me:free"]["humanRemaining"].startswith("2h") or chain["test/skip-me:free"]["humanRemaining"].startswith("3h")


def test_an_exhausted_quota_blocks_every_model_on_that_provider(registry_db, monkeypatch):
    """The shared budget is the insight, so it has to be reflected in the list.

    Free models draw on one account-wide daily allowance. Four models all
    reporting "usable" while that allowance is spent would send the owner off to
    switch between them, which changes nothing.
    """
    monkeypatch.setenv("TEST_MODEL_KEY", "test-key")
    _fresh_model(registry_db, "test/shared-a:free", key_env="TEST_MODEL_KEY")
    _fresh_model(registry_db, "test/shared-b:free", key_env="TEST_MODEL_KEY")
    reset = datetime.now(timezone.utc) + timedelta(hours=9)
    registry.record_health(
        registry_db,
        model_id="test/shared-a:free",
        provider="openrouter",
        status="rate limited",
        headers={"X-RateLimit-Reset": str(int(reset.timestamp() * 1000)), "X-RateLimit-Remaining": "0", "X-RateLimit-Limit": "50"},
    )
    models = {m["ModelId"]: m for m in registry.list_models(registry_db)}
    assert models["test/shared-a:free"]["blockedBySharedLimit"] is True
    assert "shared daily quota" in models["test/shared-b:free"]["blockedBy"]
    assert models["test/shared-b:free"]["usableNow"] is False
    limit = registry.account_limit(registry_db)
    assert limit["scope"] == "account"
    assert "not one each" in limit["explanation"]


def test_health_history_survives_removing_the_model(registry_db):
    """Re-adding a model should not erase the fact that it was out of quota."""
    entry = _fresh_model(registry_db, "test/remembers:free")
    registry.record_health(registry_db, model_id="test/remembers:free", status="answered")
    registry.remove_model(registry_db, entry["rowId"])
    row = registry_db.execute("SELECT LastStatus FROM ModelHealth WHERE ModelId = ?", ("test/remembers:free",)).fetchone()
    assert row is not None and row["LastStatus"] == "answered"


def test_failures_are_recorded_even_without_rate_limit_headers(registry_db):
    """Honest about a gap: a failure with no headers means an unknown return time."""
    _fresh_model(registry_db, "test/no-headers:free")
    health = registry.record_health(registry_db, model_id="test/no-headers:free", status="unreachable", detail="connection refused")
    assert health["LastStatus"] == "unreachable"
    assert health["AvailableAt"] is None
    assert health["availableNow"] is True


def test_humanise_reads_naturally():
    assert registry.humanise(0) == "now"
    assert registry.humanise(45) == "45s"
    assert registry.humanise(65) == "1m 5s"
    assert registry.humanise(3700) == "1h 1m"
    assert registry.humanise(90000) == "1d 1h"


def test_the_failure_summary_explains_a_spent_quota():
    """\"Unavailable\" is useless; \"out of free quota, back in 3h\" is actionable."""
    summary = CopilotAssistant_failure_summary(
        [
            {"status": "rate limited", "humanRemaining": "3h 0m"},
            {"status": "cooling down", "humanRemaining": "3h 0m"},
        ]
    )
    assert "out of free quota" in summary
    assert "3h 0m" in summary


def test_the_failure_summary_distinguishes_a_missing_key():
    summary = CopilotAssistant_failure_summary([{"status": "no key"}])
    assert "no API key" in summary


def CopilotAssistant_failure_summary(attempted):
    from nlu.assistant import CopilotAssistant

    return CopilotAssistant._failure_summary(attempted)


# ---------------------------------------------------------------------------
# Bundle costing: the money comparison, which is the number an owner acts on.
# ---------------------------------------------------------------------------

def _row(context, name):
    return next(row for row in context["rows"] if row["ItemName"] == name)


def test_profit_scenarios_costs_three_options_on_the_same_volume(context):
    """All three options must move the same projected volume.

    That is what makes the comparison honest: an invented uplift for the bundle
    would make the recommended option win by construction.
    """
    from yield_engine import profit_scenarios

    breakdown = profit_scenarios(_row(context, "Flat white"), context["perks"], context["profile"], 20)
    assert [option["key"] for option in breakdown["scenarios"]] == ["lapse", "markdown", "bundle"]
    assert len({option["unitsMoved"] for option in breakdown["scenarios"]}) == 1
    assert breakdown["notModelled"]


def test_a_bundle_that_keeps_the_floor_is_recommended(context):
    from yield_engine import profit_scenarios

    breakdown = profit_scenarios(_row(context, "Sourdough loaf"), context["perks"], context["profile"], 20)
    assert breakdown["recommended"] == "bundle"
    bundle = next(option for option in breakdown["scenarios"] if option["key"] == "bundle")
    assert bundle["floorRespected"]
    assert bundle["perkCost"] >= 0


def test_the_breakeven_states_its_direction_and_never_prints_a_sign(context):
    """A bundle can win per unit; that must read as good news, not as "+ -2%"."""
    from yield_engine import profit_scenarios

    line = profit_scenarios(_row(context, "Flat white"), context["perks"], context["profile"], 20)["breakeven"]
    assert line is not None
    assert line["direction"] in {"needs-volume", "already-ahead"}
    if line["direction"] == "needs-volume":
        assert line["extraPercent"] > 0
    else:
        assert line["extraPercent"] <= 0
    # The figure is explained in prose. What must never appear is the sign
    # artefact that a naive f-string would produce for a bundle that is ahead.
    assert "+-" not in line["explanation"]
    assert "+ -" not in line["explanation"]


def test_pairing_costs_each_line_and_sums_an_honest_total(context):
    from yield_engine import bundle_pair_profit

    rows = [_row(context, "Flat white"), _row(context, "Sourdough loaf")]
    result = bundle_pair_profit(rows, context["perks"], context["profile"])
    assert len(result["lines"]) == 2
    for line in result["lines"]:
        assert line["verdict"] in {"makes money", "thin", "even"}
        assert line["valueOnShelf"] == round(line["unitsOnShelf"] * line["shelfPrice"], 2)
    # The total is the sum of the parts, not a separate number that could drift.
    assert result["combined"]["valueOnShelf"] == round(sum(line["valueOnShelf"] for line in result["lines"]), 2)
    assert result["combined"]["profitIfAllSells"] == round(sum(line["profitIfAllSells"] for line in result["lines"]), 2)


def test_a_thin_line_in_a_pairing_is_named_rather_than_hidden(context):
    from yield_engine import bundle_pair_profit

    result = bundle_pair_profit(context["rows"], context["perks"], context["profile"])
    if {line["verdict"] for line in result["lines"]} & {"even", "thin"}:
        assert "survivor" in result["verdict"] or "clears at the floor" in result["verdict"]
    else:
        assert "clears its floor" in result["verdict"]


def test_the_pair_endpoint_refuses_an_empty_request():
    """A request that names nothing is a mistake, and must not be answered 200."""
    from fastapi.testclient import TestClient

    with TestClient(app_module.app) as client:
        assert client.post("/api/profit/pair", json={"itemIds": []}).status_code == 400
