"""Run the versioned evaluation set and print a pass/fail score.

This is the starter kit's testing checklist made runnable. The checklist asks for
a set of cases that covers ordinary questions, questions the product cannot
answer, missing or invalid identifiers, an attempt to override the instructions,
and a request that needs a human to approve it. ``cases.json`` is that set, kept
under version control so the coverage cannot quietly shrink, and this module
turns it into a number.

Two honest details about what is being measured:

* The score is not a model benchmark. The optional network assistant is switched
  off for the run, exactly as it is for the test suite, so what is judged is the
  deterministic engine - which is also the path a deployment with no key takes.
  The suite already proves the assistant is never *called* on an off-topic
  question; this set proves the answers themselves are right.
* A case that needs a value this workspace cannot supply (fewer than two live
  lines, for instance) is reported as **skipped**, and skipped is not passed.
  The score line always shows all three counts, because a run that skipped half
  the set is not a green run.

Two ways to run it, deliberately:

    python -m evals.run_evals                          # in-process, scratch DB
    python -m evals.run_evals --url http://localhost:8000

The first is what a build should do: its own throwaway database, no network, no
writing to whatever an owner has been working in. The second runs the identical
cases against a backend that is already up, which is what a demo wants, and logs
one approved decision in that workspace's own decision log.

Exit code 0 means every case that ran passed. Any failure is 1, and any skip is
2, so a truncated run cannot be mistaken for a clean one in CI.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import tempfile

CASES_PATH = pathlib.Path(__file__).with_name("cases.json")
BACKEND_DIR = pathlib.Path(__file__).resolve().parent.parent
SCRATCH_DB = pathlib.Path(tempfile.mkdtemp(prefix="valueanchor-evals-")) / "evals.db"


def use_scratch_database() -> str:
    """Point the application at a throwaway database. Call this before it is imported.

    Deliberately *not* done at import time. Importing this module must not
    redirect whatever database the caller is already using - a test suite that
    imports the runner would otherwise have its own workspace swapped underneath
    it, which is the kind of hidden coupling that makes a score untrustworthy.
    The keys are set to empty strings rather than deleted, because an empty value
    is "set": a developer's own backend/.env cannot leak a live assistant key
    into a scored run.
    """
    os.environ["VALUEANCHOR_DB"] = str(SCRATCH_DB)
    os.environ["OPENROUTER_API_KEY"] = ""
    os.environ["OPENROUTER_MODEL"] = ""
    os.environ["OPENROUTER_FALLBACK_MODELS"] = ""
    return str(SCRATCH_DB)


# ---------------------------------------------------------------------------
# Two ways of talking to the same application
# ---------------------------------------------------------------------------

class InProcessClient:
    """The app itself, through the full HTTP request cycle, on a scratch file."""

    def __init__(self) -> None:
        if str(BACKEND_DIR) not in sys.path:
            sys.path.insert(0, str(BACKEND_DIR))
        from fastapi.testclient import TestClient  # imported here so --help is fast
        import app as application

        self._entered = TestClient(application.app)
        self.client = self._entered.__enter__()

    def request(self, method: str, path: str, body: dict | None = None, raw: str | None = None):
        if raw is not None:
            return self.client.request(method, path, content=raw, headers={"Content-Type": "application/json"})
        return self.client.request(method, path, json=body)

    def exchange(self, payload: dict) -> tuple[int, object]:
        """One JSON-RPC message to /mcp, exactly as an outside agent sends it."""
        response = self.client.post("/mcp", json=payload)
        body = {} if response.status_code == 202 or not response.content else response.json()
        return response.status_code, body

    def close(self) -> None:
        self._entered.__exit__(None, None, None)


class Http:
    """The same cases against a backend that is already running.

    The timeout is generous by default because a backend with an assistant key
    configured may be waiting on a language model to rephrase an answer, and a
    slow model is not the same thing as a broken one. It is a flag rather than a
    constant so a scored run can be told how long it is willing to wait.

    A request that never answers raises, and the caller reports it as that case's
    failure. Silence must never be scored as a pass.
    """

    def __init__(self, base: str, timeout: float = 90.0) -> None:
        self.base = base.rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, body: dict | None = None, raw: str | None = None):
        import urllib.error
        import urllib.request

        url = f"{self.base}{path}"
        data = (raw.encode("utf-8") if raw is not None else json.dumps(body).encode("utf-8")) if (raw is not None or body is not None) else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as answer:
                return _Response(answer.status, answer.read())
        except urllib.error.HTTPError as refusal:
            return _Response(refusal.code, refusal.read())

    def exchange(self, payload: dict) -> tuple[int, object]:
        response = self.request("POST", "/mcp", payload)
        if response.status_code == 202 or not response.content:
            return response.status_code, {}
        return response.status_code, response.json()

    def close(self) -> None:  # nothing to release
        return None


class _Response:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content

    def json(self):
        return json.loads(self.content.decode("utf-8")) if self.content else {}


# ---------------------------------------------------------------------------
# Reading a result the way the cases describe it
# ---------------------------------------------------------------------------

class Missing(Exception):
    """A dot-path the result does not carry. Reported, never silently ignored."""


def at(value, path: str):
    """The value at a dot-path, where a number means a list index."""
    for step in path.split("."):
        if isinstance(value, list):
            try:
                value = value[int(step)]
            except (ValueError, IndexError) as wrong:
                raise Missing(f"{path} does not exist ({type(wrong).__name__})") from wrong
        elif isinstance(value, dict):
            if step not in value:
                raise Missing(f"{path} is not in the answer")
            value = value[step]
        else:
            raise Missing(f"{path} does not exist: nothing to walk into at {step!r}")
    return value


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

class Outcome:
    def __init__(self, case: dict, status: str, detail: str) -> None:
        self.case = case
        self.status = status
        self.detail = detail


def resolve(value, values: dict):
    """Placeholders replaced, once, from the live workspace.

    A whole-string placeholder keeps the value's own type - ``"$itemIds"``
    becomes a list, not its printed form - while one inside a sentence is
    substituted where it sits. The longest names are substituted first, so
    ``$itemIds`` is never clobbered by ``$itemId``, which is its prefix.
    """
    if isinstance(value, str):
        if value.startswith("$") and value[1:] in values:
            return values[value[1:]]
        for name in sorted(values, key=len, reverse=True):
            if name.startswith("_"):
                continue
            token = f"${name}"
            if token in value:
                value = value.replace(token, str(values[name]))
        return value
    if isinstance(value, list):
        return [resolve(entry, values) for entry in value]
    if isinstance(value, dict):
        return {key: resolve(entry, values) for key, entry in value.items()}
    return value


def live_values(client) -> dict:
    """The values the placeholders stand for, read from the product itself.

    Two facts about the workspace travel alongside them, because a case can only be
    judged on a workspace that can judge it: whether the optional assistant is
    switched on (a keyed backend never takes the offline path some cases exist to
    prove), and whether anything has been taught here yet (the engine deliberately
    stops asking about a phrasing once the owner has confirmed what it means).
    """
    inventory = client.request("GET", "/api/inventory").json()
    profile = inventory["profile"]
    items = inventory["items"]
    model = client.request("GET", "/api/nlu/model").json()
    learning = client.request("GET", "/api/learning").json()
    taught = int(learning.get("learnedExamples") or 0) > 0 or int(learning.get("confirmedReadings") or 0) > 0
    return {
        "itemId": items[0]["itemId"],
        "itemName": str(items[0]["itemName"]).lower(),
        "itemIds": [item["itemId"] for item in items[:2]],
        "floorPct": profile["marginFloorPct"],
        "currencySymbol": profile["currencySymbol"],
        "businessName": profile["businessName"],
        "_profile": profile,
        "_items": items,
        "_assistantEnabled": bool((model.get("assistant") or {}).get("enabled")),
        "_taught": taught,
    }


def cannot_judge(case: dict, values: dict) -> str:
    """Why this workspace cannot judge this case, or an empty string if it can.

    A requirement the workspace does not meet is reported as a skip with the reason
    in words. It is never quietly ignored, and it is never turned into a pass: a
    case that was not judged has not been shown to hold.
    """
    reasons = []
    requirement = case.get("workspace") or []
    if isinstance(requirement, str):
        requirement = [requirement]
    if "assistant-off" in requirement and values["_assistantEnabled"]:
        reasons.append(
            "this backend has an assistant key configured, so it does not take the offline path "
            "this case exists to prove - point the run at a keyless backend to score it"
        )
    if "nothing-taught-yet" in requirement and values["_taught"]:
        reasons.append(
            "a reading has already been confirmed in this workspace, so the engine no longer finds "
            "this phrasing ambiguous here - a fresh workspace is what this case needs"
        )
    return " ".join(reasons)


def check_copilot(client, case, values) -> str:
    question = resolve(case["question"], values)
    session = f"eval-{case['id']}"
    body = client.request("POST", "/api/copilot", {"question": question, "sessionId": session}).json()
    expect = case["expect"]
    problems = []

    if "inScope" in expect and body.get("inScope") is not expect["inScope"]:
        problems.append(f"inScope was {body.get('inScope')}, expected {expect['inScope']}")
    nlu = body.get("nlu") or {}
    if "intent" in expect and nlu.get("intent") != expect["intent"]:
        problems.append(f"intent was {nlu.get('intent')}, expected {expect['intent']}")
    if "needsClarification" in expect and bool(nlu.get("needsClarification")) is not expect["needsClarification"]:
        problems.append(
            f"needsClarification was {nlu.get('needsClarification')}, expected {expect['needsClarification']}"
        )
    answer = body.get("answer") or ""
    if expect.get("answerNotEmpty") and not answer.strip():
        problems.append("the answer was empty")
    for literal in expect.get("answerContains", []):
        wanted = str(resolve(literal, values))
        if wanted not in answer:
            problems.append(f"the answer does not contain {wanted!r}")
    sources = body.get("sources") or []
    if "minSources" in expect and len(sources) < expect["minSources"]:
        problems.append(f"{len(sources)} retrieved records, expected at least {expect['minSources']}")
    if expect.get("sourcesEmpty") and sources:
        problems.append(f"{len(sources)} records were retrieved for a question that is not about this business")
    if expect.get("modelNotCalled"):
        attempted = (body.get("assistant") or {}).get("attempted") or []
        if attempted:
            problems.append(f"the network assistant was attempted: {attempted}")
        if (body.get("assistant") or {}).get("used"):
            problems.append("the network assistant was used")
    return "; ".join(problems)


def check_endpoint(client, case, values) -> str:
    body = resolve(case.get("body"), values)
    response = client.request(case["method"], case["path"], body)
    want = case["expect"]["expectStatus"]
    if response.status_code != want:
        return f"{case['method']} {case['path']} answered {response.status_code}, expected {want}"
    return ""


def check_override(client, case, values) -> str:
    """The floor before, the attempt, then the floor after and a live analysis."""
    expect = case["expect"]
    problems = []
    before = client.request("GET", "/api/profile").json()["marginFloorPct"]
    session = f"eval-{case['id']}"
    body = client.request(
        "POST", "/api/copilot", {"question": resolve(case["question"], values), "sessionId": session}
    ).json()
    after = client.request("GET", "/api/profile").json()["marginFloorPct"]

    if expect.get("floorUnchanged") and after != before:
        problems.append(f"the floor moved from {before} to {after}")
    if "inScope" in expect and body.get("inScope") is not expect["inScope"]:
        problems.append(f"inScope was {body.get('inScope')}, expected {expect['inScope']}")
    answer = body.get("answer") or ""
    for literal in expect.get("answerContains", []):
        wanted = str(resolve(literal, values))
        if wanted not in answer:
            problems.append(f"the answer does not contain {wanted!r}")
    if expect.get("modelNotCalled") and ((body.get("assistant") or {}).get("attempted") or []):
        problems.append("the network assistant was attempted")
    if expect.get("analyzeFloorRespected"):
        analysis = client.request("POST", "/api/yield/analyze", {"itemId": values["itemId"]}).json()
        if not (analysis.get("metrics") or {}).get("floorRespected"):
            problems.append("the engine no longer respects the floor after the attempt")
        percent = ((analysis.get("profit") or {}).get("floor") or {}).get("percent")
        if percent != after:
            problems.append(f"a live analysis is priced against {percent}%, but the floor is {after}%")
    return "; ".join(problems)


def check_mcp(client, case, values) -> str:
    expect = case["expect"]
    arguments = resolve(case.get("arguments") or {}, values)
    status, body = client.exchange(
        {
            "jsonrpc": "2.0",
            "id": case["id"],
            "method": "tools/call",
            "params": {"name": case["tool"], "arguments": arguments},
        }
    )
    problems = []

    if "jsonrpcError" in expect:
        error = (body or {}).get("error") or {}
        if error.get("code") != expect["jsonrpcError"]:
            return (
                f"expected JSON-RPC error {expect['jsonrpcError']}, got "
                f"{error.get('code')} ({error.get('message') or 'no error at all'})"
            )
        if expect.get("messageContains") and expect["messageContains"] not in str(error.get("message", "")):
            problems.append(f"the error message does not contain {expect['messageContains']!r}: {error.get('message')!r}")
        for key in expect.get("dataHas", []):
            if key not in (error.get("data") or {}):
                problems.append(f"the error data has no {key!r}")
        return "; ".join(problems)

    result = (body or {}).get("result")
    if result is None:
        return f"the tool call produced no result: {json.dumps(body)[:200]}"
    if "isError" in expect and bool(result.get("isError")) is not expect["isError"]:
        problems.append(f"isError was {result.get('isError')}, expected {expect['isError']}")
    structured = result.get("structuredContent")
    if structured is None:
        return "the result carried no structuredContent"

    if expect.get("textMatchesStructured"):
        blocks = result.get("content") or []
        text = blocks[0].get("text") if blocks else None
        try:
            from_text = json.loads(text)
        except (TypeError, ValueError):
            problems.append("the text block is not JSON")
        else:
            if from_text != structured:
                problems.append("the text block and structuredContent are not the same JSON")

    for path, wanted in (expect.get("equals") or {}).items():
        wanted = resolve(wanted, values)
        try:
            found = at(structured, path)
        except Missing as missing:
            problems.append(str(missing))
            continue
        if found != wanted:
            problems.append(f"{path} was {found!r}, expected {wanted!r}")

    for path in expect.get("nonEmpty", []):
        try:
            found = at(structured, path)
        except Missing as missing:
            problems.append(str(missing))
            continue
        if found in (None, "", [], {}):
            problems.append(f"{path} was empty")

    for path in expect.get("emptyOnes", []):
        try:
            found = at(structured, path)
        except Missing as missing:
            problems.append(str(missing))
            continue
        if not isinstance(found, list) or found:
            problems.append(f"{path} was {found!r}, expected an empty list")

    for path, least in (expect.get("minLengths") or {}).items():
        try:
            found = at(structured, path)
        except Missing as missing:
            problems.append(str(missing))
            continue
        if not isinstance(found, list) or len(found) < least:
            problems.append(f"{path} holds {len(found) if isinstance(found, list) else type(found).__name__} entries, expected at least {least}")

    for path, prefix in (expect.get("startsWith") or {}).items():
        try:
            found = at(structured, path)
        except Missing as missing:
            problems.append(str(missing))
            continue
        if not isinstance(found, str) or not found.startswith(prefix):
            problems.append(f"{path} was {found!r}, expected it to start with {prefix!r}")

    return "; ".join(problems)


def tool_catalogue(client) -> list[dict]:
    """The tools this endpoint advertises, read at run time.

    Read rather than remembered, so a tool added to the surface tomorrow is judged
    by today's cases, and a tool removed from them makes those cases fail instead of
    silently doing nothing.
    """
    status, body = client.exchange({"jsonrpc": "2.0", "id": "catalogue", "method": "tools/list"})
    tools = ((body or {}).get("result") or {}).get("tools")
    if not tools:
        raise RuntimeError(f"the tool catalogue did not answer: {json.dumps(body)[:200]}")
    return tools


def call_tool(client, case_id: str, name: str, arguments: dict) -> dict:
    status, body = client.exchange(
        {"jsonrpc": "2.0", "id": case_id, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )
    return body or {}


def jsonrpc_error(body: dict):
    """The JSON-RPC error code, or ``None`` when the call was answered."""
    error = body.get("error")
    return error.get("code") if isinstance(error, dict) else None


def carried_paths(structured: Any, paths: list[str]) -> list[str]:
    """Which of the answer's dot-paths this payload actually carries, non-empty."""
    if not isinstance(structured, dict):
        return []
    carried = []
    for path in paths:
        try:
            value = at(structured, path)
        except Missing:
            continue
        if value not in (None, "", [], {}):
            carried.append(path)
    return carried


def wrong_caller_arguments(tool: dict, case: dict, values: dict) -> dict | None:
    """The best arguments a caller who picked the *wrong* tool could still supply.

    Only what the question and the workspace could legitimately give someone: the
    resolved line ids, and the question itself. Anything else is deliberately not
    invented - in particular an owner's decision, because the writing tool must be
    shown to refuse rather than be fed one to satisfy a test.
    """
    arguments: dict[str, Any] = {}
    for key in (tool.get("inputSchema") or {}).get("required") or []:
        if key == "itemId":
            arguments[key] = values["itemId"]
        elif key == "itemIds":
            arguments[key] = values["itemIds"]
        elif key == "question":
            arguments[key] = case["question"]
        else:
            return None
    return arguments


def check_tool_selection(client, case, values) -> str:
    """Which tool a caller should pick for this question - with no model in the loop.

    The judgement is made entirely of the surface itself, in three parts:

    1. The tool that is the right pick, called with what the question resolves to,
       must carry the answer - the dot-paths the case names, present and non-empty.
    2. Every *other* tool in the live catalogue must not. It is checked twice: with
       nothing to go on (a caller who guessed a tool and supplied no arguments must
       be refused), and with everything a wrong caller could have supplied (the
       resolved ids and the question), so "it would have refused" cannot be assumed
       from missing arguments alone.
    3. The writing tool is never fed a decision, so it can only ever be observed
       refusing. Answering a question is never what it does - it acts, and acting on
       a decision nobody made would be the worse outcome of this check.

    Each answer path is unique across the whole catalogue, which is what turns
    "exactly one tool answers" into something the run can check rather than assert.
    """
    expect = case["answer"]
    paths = expect["paths"]
    if isinstance(paths, str):
        paths = [paths]
    tools = tool_catalogue(client)
    chosen = expect["tool"]
    problems = []

    if chosen not in {tool["name"] for tool in tools}:
        return f"{chosen} is not advertised by this endpoint, so it cannot be the pick for anything"

    context = {**values, "question": case["question"]}

    # 1. The pick answers.
    body = call_tool(client, case["id"], chosen, resolve(expect.get("arguments") or {}, context))
    code = jsonrpc_error(body)
    if code is not None:
        message = (body.get("error") or {}).get("message")
        return f"{chosen} was refused with JSON-RPC {code}: {message}"
    result = body.get("result") or {}
    if result.get("isError"):
        reason = ((result.get("structuredContent") or {}).get("reason")) or "no reason given"
        return f"{chosen} reported isError for the question it is the right pick for: {reason}"
    for path in paths:
        try:
            value = at(result.get("structuredContent"), path)
        except Missing:
            problems.append(f"{chosen} does not carry {path}")
        else:
            if value in (None, "", [], {}):
                problems.append(f"{chosen} carries an empty {path}")

    # 2 and 3. Every other tool refuses, or answers something that is not this.
    for tool in tools:
        name = tool["name"]
        if name == chosen:
            continue
        read_only = (tool.get("annotations") or {}).get("readOnlyHint") is not False

        if not read_only:
            body = call_tool(client, f"{case['id']}:{name}", name, {})
            if jsonrpc_error(body) is None:
                problems.append(
                    f"{name} acted on a decision nobody gave it, instead of refusing - it must never "
                    "be the answer to a question"
                )
            continue

        body = call_tool(client, f"{case['id']}:{name}", name, {})
        if jsonrpc_error(body) is None:
            hit = carried_paths((body.get("result") or {}).get("structuredContent"), paths)
            if hit:
                problems.append(f"{name} answered a call with no arguments, carrying {', '.join(hit)}")

        arguments = wrong_caller_arguments(tool, case, values)
        if arguments is None:
            problems.append(
                f"{name} could not be judged: it requires an argument this runner will not invent. "
                "Extend wrong_caller_arguments rather than leave it unjudged."
            )
            continue
        body = call_tool(client, f"{case['id']}:{name}:given", name, arguments)
        if jsonrpc_error(body) is None:
            hit = carried_paths((body.get("result") or {}).get("structuredContent"), paths)
            if hit:
                problems.append(
                    f"{name} carries {', '.join(hit)} when given every argument a wrong caller could "
                    "have had, so a caller who picked it wrongly would read it as the answer"
                )

    return "; ".join(problems)


def check_mcp_list(client, case) -> str:
    case_expect = case["expect"]
    status, body = client.exchange({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})
    tools = ((body or {}).get("result") or {}).get("tools")
    if tools is None:
        return f"tools/list produced no catalogue: {json.dumps(body)[:200]}"
    catalog = {tool["name"]: tool for tool in tools}
    problems = []
    if len(tools) != case_expect["tools"]:
        problems.append(f"the catalogue advertises {len(tools)} tools, expected {case_expect['tools']}")
    for name in case_expect["mustInclude"]:
        if name not in catalog:
            problems.append(f"{name} is missing from the catalogue")
    for name in case_expect.get("readOnlyTools", []):
        if name in catalog and catalog[name].get("annotations", {}).get("readOnlyHint") is not True:
            problems.append(f"{name} is not annotated read-only")
    for name in case_expect.get("writeTools", []):
        if name in catalog and catalog[name].get("annotations", {}).get("readOnlyHint") is not False:
            problems.append(f"{name} must say that it changes the record")
    return "; ".join(problems)


def run(base_url: str | None = None, quiet: bool = False, timeout: float = 90.0) -> tuple[list[Outcome], dict]:
    definition = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    client = Http(base_url, timeout) if base_url else InProcessClient()
    try:
        values = live_values(client)
        profile = values["_profile"]
        enough_lines = len(values["itemIds"]) >= 2
        outcomes: list[Outcome] = []
        for case in definition["cases"]:
            # A case is skipped before it runs, never after: running it and then
            # discounting the result would be a quiet way to hide a failure.
            if len(values["itemIds"]) < 2 and "$itemIds" in json.dumps(case):
                outcomes.append(Outcome(case, "skipped", "this workspace has fewer than two live lines"))
                continue
            reason = cannot_judge(case, values)
            if reason:
                outcomes.append(Outcome(case, "skipped", reason))
                continue
            try:
                if case["kind"] == "copilot":
                    detail = check_copilot(client, case, values)
                elif case["kind"] == "endpoint":
                    detail = check_endpoint(client, case, values)
                elif case["kind"] == "override":
                    detail = check_override(client, case, values)
                elif case["kind"] == "mcp_list":
                    detail = check_mcp_list(client, case)
                elif case["kind"] == "tool_selection":
                    detail = check_tool_selection(client, case, values)
                elif case["kind"] == "mcp":
                    detail = check_mcp(client, case, values)
                else:
                    outcomes.append(Outcome(case, "skipped", f"unknown kind {case['kind']!r}"))
                    continue
            except Exception as broke:  # noqa: BLE001 - a broken case is a failure, not a crash
                # A request that timed out, or a server that answered with something
                # that is not JSON at all, is this case's failure and nobody else's.
                # The rest of the set still runs, and the score still says so.
                outcomes.append(Outcome(case, "failed", f"{type(broke).__name__}: {broke}"))
                continue
            outcomes.append(Outcome(case, "failed" if detail else "passed", detail))
    finally:
        client.close()

    summary = {
        "version": definition["version"],
        "business": profile["businessName"],
        "sector": profile["sector"],
        "floorPct": profile["marginFloorPct"],
        "passed": sum(1 for outcome in outcomes if outcome.status == "passed"),
        "failed": sum(1 for outcome in outcomes if outcome.status == "failed"),
        "skipped": sum(1 for outcome in outcomes if outcome.status == "skipped"),
        "cases": len(outcomes),
        "where": base_url or "in-process, scratch database",
    }
    if not quiet:
        _report(outcomes, summary)
    return outcomes, summary


def _report(outcomes: list[Outcome], summary: dict) -> None:
    print(f"ValueAnchor evaluation set v{summary['version']}")
    print(f"  judging   {summary['business']} ({summary['sector']}), floor {summary['floorPct']}%")
    print(f"  against   {summary['where']}")
    print()
    grouped: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        grouped.setdefault(outcome.case["checklist"], []).append(outcome)
    for checklist, group in grouped.items():
        print(f"  {checklist}")
        for outcome in group:
            mark = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}[outcome.status]
            print(f"    [{mark}] {outcome.case['id']} - {outcome.case['title']}")
            if outcome.detail:
                print(f"           {outcome.detail}")
    print()
    print(
        f"  {summary['passed']} passed, {summary['failed']} failed, {summary['skipped']} skipped "
        f"of {summary['cases']} cases"
    )
    if summary["skipped"]:
        print("  skipped is not passed: a case this workspace cannot supply a value for was not judged.")
    if not summary["failed"] and not summary["skipped"]:
        print("  every case in the set was judged, and every one of them held.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the versioned ValueAnchor evaluation set.")
    parser.add_argument("--url", default=None, help="score a backend that is already running, e.g. http://localhost:8000")
    parser.add_argument("--quiet", action="store_true", help="only the score line and the exit code")
    parser.add_argument(
        "--timeout",
        type=float,
        default=90.0,
        help="seconds to wait for one request when scoring a running backend (default 90)",
    )
    arguments = parser.parse_args(argv)

    if arguments.url:
        # Scoring somebody else's running backend: it is its own database, so
        # nothing here is redirected. The writing case will land in that
        # workspace's decision log, which is why the run says so first.
        print(f"Scoring the backend at {arguments.url}. One approved decision will be logged there.")
        print(
            "  Note: if that backend has an assistant key configured, it will call a model for\n"
            "  on-topic questions, and the cases that require the offline path will fail - that is\n"
            "  the check doing its job, not a broken run. Point the run at a keyless backend to\n"
            "  score the fallback path."
        )
    else:
        use_scratch_database()

    _, summary = run(arguments.url, quiet=arguments.quiet, timeout=arguments.timeout)
    if summary["failed"]:
        return 1
    if summary["skipped"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
