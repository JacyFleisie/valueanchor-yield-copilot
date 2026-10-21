"""The evaluation set itself: that it is complete, that it scores, and that it can fail.

Three things are being protected here, and the third matters most:

1. **The set still covers the checklist.** The starter kit asks for ordinary
   questions, unanswerable ones, missing or invalid identifiers, an attempt to
   override the instructions, and a request needing human approval. Every one of
   those headings must still have cases, so coverage cannot quietly shrink while
   the score stays green.
2. **The set scores clean, in its own workspace.** The scored run happens in a
   separate process with its own throwaway database, which is exactly how a
   developer or a build runs it. Sharing this suite's database would let one run's
   leftover decision change another run's result.
3. **The harness can fail.** A scorer that passes everything is worse than none,
   so one test feeds it deliberately wrong expectations and requires it to report
   them. Without that, a green run would only mean the checks had never been shown
   to work.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from evals import run_evals

# Every heading the kit's testing checklist names, plus the agent surface this
# project added on top of it. A case whose ``checklist`` is not one of these is a
# coverage claim nobody can trace back to the brief.
CHECKLIST = {
    "ordinary question",
    "unanswerable question",
    "missing or invalid identifier",
    "attempt to override instructions",
    "request needing human approval",
    "the agent surface itself",
    "tool selection",
}

KINDS = {"copilot", "endpoint", "override", "mcp", "mcp_list", "tool_selection"}

# The workspace facts a case may demand before it can be judged at all. Both are
# real properties of the product, not conveniences: a keyed backend never takes the
# offline path, and a taught workspace stops finding a phrasing ambiguous.
WORKSPACE_REQUIREMENTS = {"assistant-off", "nothing-taught-yet"}


@pytest.fixture(scope="module")
def definition():
    return json.loads(run_evals.CASES_PATH.read_text(encoding="utf-8"))


def test_the_set_is_versioned_and_traceable(definition):
    assert definition["version"].count(".") == 2, "the set needs a version that can move"
    assert definition["updated"], "a set with no date cannot be told from a stale one"
    assert len(definition["cases"]) >= 20, "the kit asks for ten to twenty cases at least"
    ids = [case["id"] for case in definition["cases"]]
    assert len(set(ids)) == len(ids), "two cases share an id, so one result would hide another"
    for case in definition["cases"]:
        assert case["kind"] in KINDS, f"{case['id']} uses an unknown kind"
        assert case.get("title"), f"{case['id']} does not say what it is checking"
        assert case.get("checklist") in CHECKLIST, f"{case['id']} does not name a checklist heading"
        # A case declares what would make it fail, in one of two shapes: the generic
        # `expect` block, or - for tool selection - the tool plus the dot-paths the
        # answer must carry. Either way it must be possible for this case to fail;
        # a case that declares nothing would score as a pass forever.
        has_expect = bool(case.get("expect"))
        has_pick = bool((case.get("answer") or {}).get("tool")) and bool((case.get("answer") or {}).get("paths"))
        assert has_expect or has_pick, f"{case['id']} declares no expectation, so it cannot fail"


def test_every_checklist_heading_still_has_a_case(definition):
    covered = {case["checklist"] for case in definition["cases"]}
    assert CHECKLIST.issubset(covered), f"nothing covers {sorted(CHECKLIST - covered)}"


def test_workspace_requirements_are_the_ones_the_runner_understands(definition):
    for case in definition["cases"]:
        requirement = case.get("workspace") or []
        if isinstance(requirement, str):
            requirement = [requirement]
        unknown = set(requirement) - WORKSPACE_REQUIREMENTS
        assert not unknown, f"{case['id']} asks for {sorted(unknown)}, which the runner cannot judge"


def test_a_workspace_that_cannot_judge_a_case_says_so_rather_than_passing_it():
    """Both requirements are checked by the reason they produce, not by a flag."""
    offline = {"workspace": "assistant-off"}
    ambiguous = {"workspace": "nothing-taught-yet"}

    fresh_and_keyless = {"_assistantEnabled": False, "_taught": False}
    assert run_evals.cannot_judge(offline, fresh_and_keyless) == ""
    assert run_evals.cannot_judge(ambiguous, fresh_and_keyless) == ""

    keyed = {"_assistantEnabled": True, "_taught": False}
    assert "assistant key" in run_evals.cannot_judge(offline, keyed)

    taught = {"_assistantEnabled": False, "_taught": True}
    assert "already been confirmed" in run_evals.cannot_judge(ambiguous, taught)

    # Both at once: both reasons are reported, so a run never says less than it knows.
    both = run_evals.cannot_judge({"workspace": ["assistant-off", "nothing-taught-yet"]}, {"_assistantEnabled": True, "_taught": True})
    assert "assistant key" in both and "already been confirmed" in both

    # A case with no requirement is always judgeable.
    assert run_evals.cannot_judge({}, {"_assistantEnabled": True, "_taught": True}) == ""


def test_the_whole_set_passes_in_its_own_workspace(tmp_path):
    """The scored run, through the same command a person would type."""
    environment = {
        **os.environ,
        # An explicit database, so this run cannot read or write the workspace the
        # rest of the suite is using - or the developer's own.
        "VALUEANCHOR_DB": str(tmp_path / "eval-run.db"),
        "OPENROUTER_API_KEY": "",
        "OPENROUTER_MODEL": "",
        "OPENROUTER_FALLBACK_MODELS": "",
    }
    completed = subprocess.run(
        [sys.executable, "-m", "evals.run_evals"],
        cwd=run_evals.BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=600,
    )
    report = f"exit {completed.returncode}\n--- stdout ---\n{completed.stdout}\n--- stderr ---\n{completed.stderr}"
    # Exit 2 is a truncated run: a case this workspace could not supply a value
    # for. That is not a pass, so it fails here too.
    assert completed.returncode == 0, f"the evaluation set did not score clean:\n{report}"
    assert "0 failed, 0 skipped" in completed.stdout, f"the run did not report a full score:\n{report}"
    assert "every case in the set was judged" in completed.stdout, f"the run did not claim full coverage:\n{report}"


def test_tool_selection_covers_the_whole_read_surface(definition):
    """Every tool an agent could pick is scored as the right pick exactly once.

    The writing tool is deliberately excluded from the picks: it acts rather than
    answers, so no question is ever answered by it - and its refusal is scored by
    the missing-decision case instead.
    """
    selections = [case for case in definition["cases"] if case["kind"] == "tool_selection"]
    assert selections, "no case scores which tool a caller should pick"
    picks = {case["answer"]["tool"] for case in selections}
    assert "valueanchor_log_decision" not in picks, "the writing tool is never the answer to a question"
    for name in [
        "valueanchor_guardrails",
        "valueanchor_urgent_stock",
        "valueanchor_analyze_line",
        "valueanchor_pair_lines",
        "valueanchor_ask",
    ]:
        assert name in picks, f"{name} is never the right pick for a question, so it is only ever judged as wrong"
    for case in selections:
        assert case["question"], f"{case['id']} has no question to pick a tool for"
        assert case["answer"]["tool"].startswith("valueanchor_"), f"{case['id']} picks an unknown tool"
        assert case["answer"]["paths"], f"{case['id']} does not say what the answer looks like"


def test_a_wrong_pick_is_reported_rather_than_passing():
    """The selection score means nothing unless it can name the wrong tool.

    Two deliberate mistakes: first, claim a tool that plainly cannot answer. Second
    - the harder one - claim a *correct* tool but name an answer path that the
    wrong tool also carries, so the check has to catch a wrong tool that looks like
    it answered. If either came back clean, the tool-selection cases above would be
    decoration.
    """
    client = run_evals.InProcessClient()
    try:
        values = run_evals.live_values(client)
        catalogue = run_evals.tool_catalogue(client)
        assert len(catalogue) >= 5, "the surface needs a catalogue worth choosing from"

        # (a) the wrong tool claimed as the pick
        claimed_wrong = {
            "id": "harness.pick",
            "kind": "tool_selection",
            "title": "A deliberately wrong pick",
            "question": "which batch needs attention first?",
            "answer": {
                "tool": "valueanchor_guardrails",
                "arguments": {},
                "paths": ["ranking", "linesOnTheBooks"],
            },
        }
        report = run_evals.check_tool_selection(client, claimed_wrong, values)
        assert "valueanchor_guardrails" in report and "does not carry" in report, (
            f"a tool that cannot answer was reported as a pass: {report!r}"
        )

        # (b) a path that a wrong tool really does carry - marginFloorPct is in both
        # the guardrails result and the ranked-stock result - so the check must
        # catch the wrong tool answering rather than trusting the pick.
        shared_path = {
            "id": "harness.ambiguous-path",
            "kind": "tool_selection",
            "title": "A correct pick with an answer the wrong tool also carries",
            "question": "which batch needs attention first?",
            "answer": {
                "tool": "valueanchor_urgent_stock",
                "arguments": {},
                "paths": ["marginFloorPct"],
            },
        }
        report = run_evals.check_tool_selection(client, shared_path, values)
        assert "valueanchor_guardrails" in report, (
            f"a wrong tool carrying the answer was not reported: {report!r}"
        )
        assert "no arguments" in report or "every argument" in report, (
            f"the report should say how the wrong tool got through: {report!r}"
        )
    finally:
        client.close()


def test_the_runner_will_not_invent_an_argument_it_was_not_given():
    """A tool needing something a caller could not have must be judged, never filled in."""
    needs_decision = {"inputSchema": {"required": ["itemId", "managerDecision"]}}
    needs_question = {"inputSchema": {"required": ["question"]}}
    values = {"itemId": 101, "itemIds": [101, 102]}
    case = {"question": "what needs attention first?"}
    assert run_evals.wrong_caller_arguments(needs_decision, case, values) is None
    assert run_evals.wrong_caller_arguments(needs_question, case, values) == {"question": "what needs attention first?"}


def test_a_wrong_expectation_is_reported_rather_than_ignored():
    """The proof the score means something: hand the checks a lie and watch them fail.

    This uses the real application and the real check functions with one
    expectation replaced by something untrue. If a pass came back anyway, every
    green run of the set above would be worthless.
    """
    client = run_evals.InProcessClient()
    try:
        values = run_evals.live_values(client)
        wrong = {
            "id": "harness.can-fail",
            "kind": "mcp",
            "title": "A deliberately wrong expectation",
            "tool": "valueanchor_guardrails",
            "arguments": {},
            "expect": {"isError": False, "equals": {"marginFloorPct": -1}},
        }
        assert run_evals.check_mcp(client, wrong, values), "a wrong floor was reported as a pass"

        right = {
            "id": "harness.can-pass",
            "kind": "mcp",
            "title": "The same call, told the truth",
            "tool": "valueanchor_guardrails",
            "arguments": {},
            "expect": {"isError": False, "equals": {"marginFloorPct": "$floorPct"}},
        }
        assert not run_evals.check_mcp(client, right, values), "a correct expectation was reported as a failure"

        absent = {
            "id": "harness.missing-path",
            "kind": "mcp",
            "title": "A path that does not exist",
            "tool": "valueanchor_guardrails",
            "arguments": {},
            "expect": {"isError": False, "equals": {"noSuchField": True}},
        }
        assert "noSuchField" in run_evals.check_mcp(client, absent, values), "a missing field was not reported"
    finally:
        client.close()


def test_a_placeholder_inside_a_sentence_is_substituted():
    """``$itemName`` in the middle of a question is replaced, longest names first."""
    values = {"itemId": 101, "itemIds": [101, 102], "itemName": "flat white"}
    assert run_evals.resolve("what about $itemName?", values) == "what about flat white?"
    assert run_evals.resolve("$itemIds", values) == [101, 102]
    assert run_evals.resolve("id $itemIds here", values) == "id [101, 102] here"
    assert run_evals.resolve("$itemId and $itemIds", values) == "101 and [101, 102]"
    assert run_evals.resolve("nothing to replace", values) == "nothing to replace"


def test_the_dot_path_reader_reports_a_path_it_cannot_walk():
    assert run_evals.at({"a": {"b": [1, 2]}}, "a.b.1") == 2
    with pytest.raises(run_evals.Missing):
        run_evals.at({"a": {"b": []}}, "a.b.0")
    with pytest.raises(run_evals.Missing):
        run_evals.at({"a": {}}, "a.b")
    with pytest.raises(run_evals.Missing):
        run_evals.at({"a": 1}, "a.b")
