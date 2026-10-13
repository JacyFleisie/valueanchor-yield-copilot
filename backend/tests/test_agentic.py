"""Retrieval, guardrails and the learning loop.

These cover what makes the copilot an answerable agent rather than a chat box:
that an answer can be traced to records, that an off-topic question is refused
deterministically and never sent anywhere, that a language model cannot smuggle a
changed figure into a reply, and that the local model genuinely improves from the
owner's own confirmed questions.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import app as app_module
from nlu import IntentEngine, build_lexicon
from nlu.assistant import CopilotAssistant
from nlu.dataset import INTENT_LABELS
from nlu.knowledge import GLOSSARY, KnowledgeBase


@pytest.fixture(scope="module")
def client():
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def context():
    """The live workspace plus a trained engine, shared across this module."""
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


def knowledge_for(context) -> KnowledgeBase:
    return KnowledgeBase(
        context["profile"], context["rows"], context["perks"], context["history"], app_module.SECTORS
    )


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------
def test_retrieval_indexes_the_whole_workspace(context):
    summary = knowledge_for(context).summary()
    assert summary["byKind"]["stock"] == len(context["rows"])
    assert summary["byKind"]["perk"] == len(context["perks"])
    assert summary["byKind"]["glossary"] == len(GLOSSARY)
    assert summary["byKind"]["rule"] >= 3
    assert summary["documents"] > 20


def test_retrieval_finds_the_records_a_question_is_about(context):
    kb = knowledge_for(context)
    hits = kb.retrieve("what does urgency mean", 3)
    assert any(hit["kind"] == "glossary" for hit in hits)
    assert all(hit["text"] for hit in hits), "every record must carry the text that grounded the answer"
    assert all(hit["relevance"] > 0 for hit in hits), "only records that actually matched are returned"


def test_the_line_an_answer_is_about_is_always_retrieved(context):
    # The grounding that matters most: the record behind the figures in the
    # answer must be in front of the assistant even when the wording of the
    # question would not have found it on its own.
    kb = knowledge_for(context)
    target = context["items"][0]
    hits = kb.retrieve("what now", 4, target["itemId"])
    assert hits[0]["id"] == f"item:{target['itemId']}" or any(
        hit["itemId"] == target["itemId"] for hit in hits
    )
    assert any(hit["kind"] == "stock" for hit in hits)


def test_retrieval_still_answers_from_records_when_nothing_matches(context):
    hits = knowledge_for(context).retrieve("zzzz qqqq unrelated", 6)
    assert hits, "a vague question must be grounded in something, never in nothing"
    assert any(hit["kind"] == "rule" for hit in hits)
    assert all(hit["text"] for hit in hits)


def test_a_named_item_is_retrieved_for_its_own_question(context):
    kb = knowledge_for(context)
    name = context["items"][0]["itemName"]
    hits = kb.retrieve(f"what should i bundle with the {name}", 5)
    assert any(name.lower() in hit["title"].lower() for hit in hits)


# ---------------------------------------------------------------------------
# Guardrails
# ---------------------------------------------------------------------------
def test_scope_gate_refuses_what_the_local_model_read_as_off_topic():
    allowed, reason = CopilotAssistant.local_scope_gate("out_of_scope", [], set(INTENT_LABELS))
    assert allowed is False
    assert reason, "a refusal must be explainable"


def test_scope_gate_refuses_an_unknown_intent_with_no_business_vocabulary():
    allowed, _reason = CopilotAssistant.local_scope_gate("invented_intent", [], set(INTENT_LABELS))
    assert allowed is False


def test_scope_gate_allows_a_known_business_intent():
    allowed, _reason = CopilotAssistant.local_scope_gate("priority", [], set(INTENT_LABELS))
    assert allowed is True


def test_scope_gate_allows_a_sentence_that_names_a_business_entity():
    allowed, _reason = CopilotAssistant.local_scope_gate(
        "invented_intent", [{"type": "money", "value": 20}], set(INTENT_LABELS)
    )
    assert allowed is True


def test_an_unconfigured_assistant_declines_rather_than_answering():
    assistant = CopilotAssistant(None)
    assert assistant.configured is False
    result = assistant.rewrite(
        question="what needs attention first",
        intent="priority",
        intent_label="Priority",
        engine_answer="Start with the flat white.",
        profile={"businessName": "Test", "name": "Coffee shop", "marginFloorPct": 60},
        context=[],
        glossary=[],
        next_steps=[],
        key_numbers=[],
        entities=[],
        known_intents=set(INTENT_LABELS),
    )
    assert result.used is False
    assert result.answer == ""
    assert result.guardrail, "a declined assistant must say why it declined"


def test_the_number_audit_accepts_the_same_figure_written_differently():
    context = "Shelf price R38. Floor R22.80. You keep R23.40 of margin."
    assert CopilotAssistant._invented_numbers("It sells at 38 and you keep 23.4.", context) == []
    assert CopilotAssistant._invented_numbers("The floor is 22.8.", context) == []


def test_the_number_audit_rejects_a_figure_that_is_not_in_the_live_data():
    context = "Shelf price R38. Floor R22.80. You keep R23.40 of margin."
    assert CopilotAssistant._invented_numbers("You keep R27 of margin.", context) == ["27"]


def test_the_assistant_tolerates_a_double_encoded_json_reply():
    # One of the free models in the chain returns its object wrapped in a string.
    assert CopilotAssistant._unwrap('"{\\"onTopic\\": true, \\"answer\\": \\"hi\\"}"') == {
        "onTopic": True,
        "answer": "hi",
    }
    assert CopilotAssistant._unwrap("```json\n{\"answer\": \"x\"}\n```") == {"answer": "x"}
    assert CopilotAssistant._unwrap(None) is None
    assert CopilotAssistant._unwrap("not json at all") is None


def test_off_topic_questions_are_refused_without_calling_any_model(client):
    body = client.post(
        "/api/copilot", json={"question": "who won the soccer game last night?", "sessionId": "scope"}
    ).json()
    assert body["inScope"] is False
    assert body["assistant"]["used"] is False
    # An empty attempted list is the proof: no model was called, so the off-topic
    # text never left the machine.
    assert body["assistant"]["attempted"] == []
    assert body["sources"] == [], "retrieval does not run for a question that is out of scope"
    assert body["guardrail"]


@pytest.mark.parametrize(
    "question",
    [
        "what is the weather in cape town tomorrow",
        "write me a python function to scrape a website",
        "who won the soccer game last night",
        "give me medical advice for a headache",
    ],
)
def test_these_off_topic_questions_never_reach_the_model(client, question):
    body = client.post("/api/copilot", json={"question": question, "sessionId": "offtopic"}).json()
    assert body["inScope"] is False
    assert body["assistant"]["attempted"] == []


# ---------------------------------------------------------------------------
# Answer shape
# ---------------------------------------------------------------------------
def test_every_answer_carries_a_headline_meaning_figures_and_actions(client):
    body = client.post(
        "/api/copilot", json={"question": "which batch needs attention first?", "sessionId": "shape"}
    ).json()
    assert body["headline"]
    assert body["meaning"]
    assert body["severity"] in {"act-now", "opportunity", "watch", "caution", "blocked", "info"}
    assert body["nextSteps"], "an owner needs something to do, not just something to know"
    assert all(step["label"] for step in body["nextSteps"])
    assert body["keyNumbers"], "figures must be labelled, never bare"
    assert all(entry["label"] and entry["value"] for entry in body["keyNumbers"])
    assert body["answer"] and body["engineAnswer"]
    assert body["sources"]
    assert body["retrieval"]["documents"] > 0


def test_the_engine_answer_is_kept_even_when_the_assistant_rephrases(client):
    body = client.post(
        "/api/copilot", json={"question": "should i knock 20 percent off the flat white?", "sessionId": "keep"}
    ).json()
    assert body["engineAnswer"]
    assert "floor" in body["engineAnswer"].lower() or "floor" in body["meaning"].lower()


def test_a_jargon_question_is_explained_rather_than_refused(client):
    body = client.post("/api/copilot", json={"question": "what does urgency mean?", "sessionId": "jargon"}).json()
    assert body["inScope"] is True
    assert body["nlu"]["intent"] == "explain_term"
    assert body["severity"] == "info"
    assert "urgency" in body["answer"].lower()
    assert body["headline"]


def test_copilot_suggests_follow_ups_that_are_themselves_answerable(client):
    body = client.post(
        "/api/copilot", json={"question": "which batch needs attention first?", "sessionId": "follow"}
    ).json()
    assert body["followUps"]
    follow = client.post("/api/copilot", json={"question": body["followUps"][0], "sessionId": "follow"}).json()
    assert follow["inScope"] is True
    assert follow["nlu"]["intent"] != "out_of_scope"


def test_starter_questions_are_built_from_the_live_line_under_pressure(client):
    body = client.get("/api/copilot/suggestions").json()
    assert body["groups"]
    assert body["worstItem"]
    questions = [question for group in body["groups"] for question in group["questions"]]
    assert any(body["worstItem"] in question for question in questions)
    for question in questions[:3]:
        reply = client.post("/api/copilot", json={"question": question, "sessionId": "suggest"}).json()
        assert reply["inScope"] is True


def test_knowledge_endpoint_shows_what_grounds_an_answer(client):
    body = client.get("/api/knowledge").json()
    assert body["summary"]["documents"] > 0
    assert body["sampleSources"]
    assert body["grounding"]


# ---------------------------------------------------------------------------
# Learning
# ---------------------------------------------------------------------------
def test_a_helpful_rating_becomes_training_data(client):
    turn = client.post("/api/copilot", json={"question": "what will go to waste?", "sessionId": "feedback"}).json()
    assert turn["turnId"]
    rated = client.post(
        "/api/copilot/feedback",
        json={"turnId": turn["turnId"], "rating": 1, "intent": turn["nlu"]["intent"]},
    ).json()
    assert rated["learned"] is True
    assert rated["learning"]["learnedExamples"] >= 1


def test_a_thumbs_down_is_recorded_but_never_taught(client):
    turn = client.post("/api/copilot", json={"question": "what is my margin floor?", "sessionId": "downvote"}).json()
    before = client.get("/api/learning").json()["learnedExamples"]
    rated = client.post("/api/copilot/feedback", json={"turnId": turn["turnId"], "rating": -1}).json()
    assert rated["learned"] is False
    assert client.get("/api/learning").json()["learnedExamples"] == before, (
        "a bad answer must never be reinforced"
    )


def test_confirming_a_reading_teaches_the_model_the_owners_own_phrasing(client):
    # A phrasing the generated templates do not contain, so the before-and-after
    # is measuring learning rather than luck.
    phrasing = "sort me the croissants situation"
    turn = client.post("/api/copilot", json={"question": phrasing, "sessionId": "learn"}).json()
    before = client.get("/api/nlu/model").json()["training"]["learnedRows"]

    confirmed = client.post(
        "/api/copilot/confirm", json={"turnId": turn["turnId"], "intent": "priority", "rating": 1}
    ).json()
    assert confirmed["learned"] is True
    assert confirmed["learning"]["learnedExamples"] >= 1

    # The engine is rebuilt with the confirmed example, so the same sentence now
    # reads as what the owner said it meant.
    assert client.get("/api/nlu/model").json()["training"]["learnedRows"] > before
    again = client.post("/api/copilot", json={"question": phrasing, "sessionId": "learn"}).json()
    assert again["nlu"]["intent"] == "priority"


def test_confirming_requires_an_intent(client):
    assert client.post("/api/copilot/confirm", json={"turnId": 1}).status_code == 400


def test_feedback_rejects_a_rating_that_is_not_a_verdict(client):
    assert client.post("/api/copilot/feedback", json={"turnId": 1, "rating": 5}).status_code == 422


def test_learning_endpoint_explains_the_rule_it_follows(client):
    body = client.get("/api/learning").json()
    assert body["howItLearns"]
    assert "confirmed" in body["howItLearns"].lower() or "corrects" in body["howItLearns"].lower()
    assert "recentQuestions" in body
    assert "accuracyHistory" in body


def test_history_restores_the_whole_conversation_not_just_the_prose(client):
    client.post("/api/copilot", json={"question": "how is today going?", "sessionId": "restore"})
    client.post("/api/copilot", json={"question": "what should i bundle?", "sessionId": "restore"})
    turns = client.get("/api/copilot/history", params={"sessionId": "restore"}).json()["turns"]
    assert len(turns) >= 2
    assert [turn["Question"] for turn in turns][-1] == "what should i bundle?"
    assert turns[-1]["Payload"]["headline"], "a restored turn keeps what made it useful"
    assert client.get("/api/copilot/history", params={"sessionId": "nobody"}).json()["turns"] == []
