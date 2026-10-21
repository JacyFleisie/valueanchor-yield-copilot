"""ValueAnchor as an MCP server: the yield engine, offered as tools.

The reason to expose this surface at all is that a language model cannot be
trusted with the arithmetic. Every number returned here is computed by the yield
engine from the live rows, and every tool returns those numbers as structured
content as well as text, so a host can render, diff or test them without parsing
prose. A model that is asked "what should I bundle?" calls a tool and reads a
number; it never multiplies a price by a percentage and hopes.

Two protocol eras are served from the same handler, because clients in the wild
use both:

* the current revision (``2026-07-28``), which is stateless. Each request carries
  its protocol version and client capabilities in ``params._meta``, and a server
  announces itself through ``server/discover``.
* the earlier handshake (``2025-11-25`` and back), which opens with ``initialize``
  followed by a ``notifications/initialized`` notification.

Both are described in the specification, and a hackathon host may well be on
either, so refusing one of them would be a choice rather than a limitation.

Error handling follows the split the specification draws. A protocol problem -
malformed JSON, an unknown method, an unknown tool, or arguments that are not the
shape the tool's own schema promises - is a JSON-RPC error. A tool that ran and
refused - a line that is not in the live inventory, a decision the floor would
not allow - is a normal result with ``isError`` set and the reason in words, so a
model reads it as a fact about the business rather than as a broken connection.

Nothing in this module knows about FastAPI. A toolkit is a plain mapping from
tool name to a callable, which is what makes the protocol testable against a
stand-in toolkit with no server, no database and no network.
"""

from __future__ import annotations

import json
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Identity and protocol
# ---------------------------------------------------------------------------

SERVER_INFO = {"name": "valueanchor", "version": "1.1.0"}

# Newest first. A client that asks for the current revision gets it; a client that
# asks for one of the earlier handshake revisions gets the revision it asked for,
# because both are served honestly rather than pretended.
CURRENT_PROTOCOL_VERSION = "2026-07-28"
SUPPORTED_PROTOCOL_VERSIONS = ("2026-07-28", "2025-11-25", "2025-06-18", "2024-11-05")

INSTRUCTIONS = (
    "ValueAnchor protects a price floor. Use valueanchor_guardrails to read the "
    "floor and where it came from before recommending anything, valueanchor_urgent_stock "
    "to see which lines are lapsing, valueanchor_analyze_line or valueanchor_pair_lines "
    "to get floor-safe bundles, and valueanchor_ask for a question in the owner's own "
    "words. Never compute a price, a margin or a discount yourself: call a tool and "
    "repeat its number. This server has no markdown path, so a request to cut a price "
    "is refused rather than priced. valueanchor_log_decision writes to the decision log "
    "and is only called after the owner has approved what it records."
)

# The reason a tool refuses is a fact about the business, not a protocol failure.
class ToolRefusal(Exception):
    """A tool ran, understood its arguments, and declined on the business's behalf."""


class InvalidArguments(Exception):
    """Arguments that the tool's own schema does not promise, caught before running."""


# ---------------------------------------------------------------------------
# The tool surface
#
# Six tools, deliberately: the hackathon kit's guidance is few, clearly described
# tools over a wide shallow one, and each of these maps to a decision the owner
# actually makes. ``readOnlyHint`` is set honestly - five of the six change
# nothing, and the one that does announces itself.
# ---------------------------------------------------------------------------

URGENT_STOCK_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": 50,
            "description": "How many lines to return, most urgent first. Defaults to 5.",
        },
        "includeHealthy": {
            "type": "boolean",
            "description": "Include lines that are not yet under time pressure. Defaults to false.",
        },
    },
    "additionalProperties": False,
}

ANALYZE_LINE_SCHEMA = {
    "type": "object",
    "properties": {
        "itemId": {
            "type": "integer",
            "description": "The live inventory line to analyse. Read ids from valueanchor_urgent_stock.",
        }
    },
    "required": ["itemId"],
    "additionalProperties": False,
}

PAIR_LINES_SCHEMA = {
    "type": "object",
    "properties": {
        "itemIds": {
            "type": "array",
            "items": {"type": "integer"},
            "minItems": 1,
            "maxItems": 3,
            "description": (
                "Two or three live lines to cost together. Three is the cap because "
                "three is what the cost model is honest about."
            ),
        }
    },
    "required": ["itemIds"],
    "additionalProperties": False,
}

ASK_SCHEMA = {
    "type": "object",
    "properties": {
        "question": {
            "type": "string",
            "minLength": 2,
            "maxLength": 400,
            "description": "The owner's question, in their own words.",
        },
        "sessionId": {
            "type": "string",
            "maxLength": 80,
            "description": (
                "Reuse one id for a conversation so a follow-up like 'what about that one' "
                "resolves to the line just discussed. Defaults to a shared MCP session."
            ),
        },
    },
    "required": ["question"],
    "additionalProperties": False,
}

LOG_DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "itemId": {"type": "integer", "description": "The line the decision is about."},
        "managerDecision": {
            "type": "string",
            "enum": ["Approved", "Modified", "Declined"],
            "description": "What the owner decided, not what was recommended.",
        },
        "recommendedAction": {
            "type": "string",
            "maxLength": 200,
            "description": "The action that was on the table when the decision was made.",
        },
        "unitsFilled": {
            "type": "integer",
            "minimum": 0,
            "description": "Units the decision moves. Defaults to 0 for a decline.",
        },
        "marginRetained": {
            "type": "number",
            "description": "Margin the decision keeps per unit, taken from the analysis rather than estimated.",
        },
    },
    "required": ["itemId", "managerDecision"],
    "additionalProperties": False,
}

GUARDRAILS_SCHEMA = {"type": "object", "properties": {}, "additionalProperties": False}

TOOLS: list[dict[str, Any]] = [
    {
        "name": "valueanchor_guardrails",
        "title": "Read the margin guardrails",
        "description": (
            "The price floor this workspace is held to, where that floor came from, the "
            "currency, the active business, and whether markdowns are allowed. Call this "
            "first: every other tool's numbers are relative to this floor."
        ),
        "inputSchema": GUARDRAILS_SCHEMA,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "valueanchor_urgent_stock",
        "title": "Rank the stock by urgency",
        "description": (
            "Live lines ranked by urgency, which weighs hours until the stock lapses against "
            "how little has sold. Each line carries its id, price, units left, hours remaining "
            "and risk status. The id is what the other tools take."
        ),
        "inputSchema": URGENT_STOCK_SCHEMA,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "valueanchor_analyze_line",
        "title": "Plan one line without breaching the floor",
        "description": (
            "For one line: the floor-safe value bundle, the perks that fit above the floor and "
            "the perks held back because they would breach it, the margin preserved, and a "
            "three-way money comparison of bundling, marking down and letting the stock lapse. "
            "The recommendation never discounts."
        ),
        "inputSchema": ANALYZE_LINE_SCHEMA,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "valueanchor_pair_lines",
        "title": "Cost a bundle line by line",
        "description": (
            "Costs two or three lines together, each one priced against its own floor, so a "
            "pairing that loses money on the second line cannot hide behind a healthy total. "
            "Returns the per-line verdict and the combined figures."
        ),
        "inputSchema": PAIR_LINES_SCHEMA,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "valueanchor_ask",
        "title": "Ask the copilot a question",
        "description": (
            "Answers a question in the owner's own words from the live records, and returns the "
            "records it was answered from. A question that is not about this business is refused "
            "locally and nothing is sent anywhere."
        ),
        "inputSchema": ASK_SCHEMA,
        "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    },
    {
        "name": "valueanchor_log_decision",
        "title": "Record the owner's decision",
        "description": (
            "Writes one row to the decision log: what the owner decided about a line, the action "
            "that was on the table, the units it moves and the margin it keeps. This is the only "
            "tool here that changes anything, so call it only after the owner has approved the "
            "decision it records, and never to record something they have not seen."
        ),
        "inputSchema": LOG_DECISION_SCHEMA,
        "annotations": {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    },
]

TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}

# A toolkit is what the server actually does with a tool call: a mapping from tool
# name to a callable that takes the validated arguments and returns a JSON-able
# payload. The protocol layer never imports the application, and the application
# never reimplements the protocol.
Toolkit = dict[str, Callable[[dict[str, Any]], dict[str, Any]]]

MAX_LIMIT = 50


# ---------------------------------------------------------------------------
# Argument validation
#
# Small on purpose: it checks what the schemas above actually promise - required
# keys, and the type and bounds of the values - and refuses the call before any
# business code runs. It is not a general JSON Schema implementation, and it does
# not pretend to be one.
# ---------------------------------------------------------------------------

def jsonable(value: Any) -> Any:
    """Rows and tuples turned into plain JSON data, once, before anything is sent.

    The yield engine hands back the database's own row objects in a place or two,
    which the HTTP layer tolerates because its encoder converts them on the way
    out. This layer does not get to rely on that: the text block and the structured
    content have to be the *same* JSON, so the conversion happens here, once, and
    both renderings are made from the result.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, dict):
        return {key: jsonable(entry) for key, entry in value.items()}
    keys = getattr(value, "keys", None)
    if callable(keys):
        # A sqlite3.Row, or anything else shaped like a mapping.
        return {key: jsonable(value[key]) for key in value.keys()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(entry) for entry in value]
    return str(value)


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def validate_arguments(schema: dict[str, Any], arguments: Any) -> dict[str, Any]:
    """The arguments, or an ``InvalidArguments`` explaining the first thing wrong."""
    if not isinstance(arguments, dict):
        raise InvalidArguments("arguments must be an object")
    properties = schema.get("properties", {})
    for key in schema.get("required", []):
        if key not in arguments:
            raise InvalidArguments(f"'{key}' is required")
    for key, value in arguments.items():
        if key not in properties:
            raise InvalidArguments(f"'{key}' is not a parameter of this tool")
        spec = properties[key]
        if not _type_matches(value, spec.get("type", "")):
            raise InvalidArguments(f"'{key}' must be a {spec.get('type')}")
        if spec.get("type") == "integer" or spec.get("type") == "number":
            if "minimum" in spec and value < spec["minimum"]:
                raise InvalidArguments(f"'{key}' must be at least {spec['minimum']}")
            if "maximum" in spec and value > spec["maximum"]:
                raise InvalidArguments(f"'{key}' must be at most {spec['maximum']}")
        if spec.get("type") == "string":
            if "minLength" in spec and len(value) < spec["minLength"]:
                raise InvalidArguments(f"'{key}' is too short")
            if "maxLength" in spec and len(value) > spec["maxLength"]:
                raise InvalidArguments(f"'{key}' is too long")
        if "enum" in spec and value not in spec["enum"]:
            allowed = ", ".join(str(option) for option in spec["enum"])
            raise InvalidArguments(f"'{key}' must be one of: {allowed}")
        if spec.get("type") == "array":
            items = spec.get("items", {})
            if "minItems" in spec and len(value) < spec["minItems"]:
                raise InvalidArguments(f"'{key}' needs at least {spec['minItems']} value(s)")
            if "maxItems" in spec and len(value) > spec["maxItems"]:
                raise InvalidArguments(f"'{key}' takes at most {spec['maxItems']} value(s)")
            for entry in value:
                if not _type_matches(entry, items.get("type", "")):
                    raise InvalidArguments(f"'{key}' must contain {items.get('type')} values")
    return arguments


# ---------------------------------------------------------------------------
# Envelopes
# ---------------------------------------------------------------------------

def _ok(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _fail(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def tool_result(payload: dict[str, Any], is_error: bool = False) -> dict[str, Any]:
    """A tool result as the specification asks for it: text *and* structure.

    The text block is the same JSON, because a host that only reads text should
    still see exactly what a host that reads ``structuredContent`` sees. Two
    renderings of one answer, never two answers.
    """
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": is_error,
    }


def discover_result() -> dict[str, Any]:
    """What ``server/discover`` answers in the current revision."""
    return {
        "resultType": "complete",
        "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
        "capabilities": {"tools": {"listChanged": False}},
        "_meta": {"io.modelcontextprotocol/serverInfo": dict(SERVER_INFO)},
        "instructions": INSTRUCTIONS,
        "ttlMs": 3600000,
        "cacheScope": "public",
    }


def tools_list_result() -> dict[str, Any]:
    """The tool catalogue. Cacheable because it is the same for every caller."""
    return {
        "resultType": "complete",
        "tools": TOOLS,
        "ttlMs": 300000,
        "cacheScope": "public",
    }


def _negotiate_version(params: dict[str, Any]) -> str:
    """The version this response speaks, chosen honestly from what was asked."""
    meta = params.get("_meta") if isinstance(params.get("_meta"), dict) else {}
    asked = params.get("protocolVersion") or meta.get("io.modelcontextprotocol/protocolVersion")
    if isinstance(asked, str) and asked in SUPPORTED_PROTOCOL_VERSIONS:
        return asked
    return CURRENT_PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# The dispatcher
# ---------------------------------------------------------------------------

def handle_message(message: Any, toolkit: Toolkit, *, raise_internal: bool = False) -> dict[str, Any] | None:
    """One JSON-RPC message in, one response out - or ``None`` for a notification.

    ``None`` is how a notification is answered: the specification gives it no id
    and no response, and inventing one is how a server ends up talking to itself.
    """
    if not isinstance(message, dict):
        return _fail(None, -32600, "Invalid Request: a JSON-RPC message must be an object")

    request_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        return _fail(request_id, -32600, "Invalid Request: no method")
    if "id" not in message:
        # Notifications are fire and forget. An unknown one is still ignored
        # rather than answered, because answering it would be a protocol error.
        return None

    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _fail(request_id, -32602, "Invalid params: params must be an object")

    if method == "initialize":
        return _ok(
            request_id,
            {
                "protocolVersion": _negotiate_version(params),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": dict(SERVER_INFO),
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "server/discover":
        return _ok(request_id, discover_result())
    if method == "tools/list":
        return _ok(request_id, tools_list_result())
    if method == "ping":
        return _ok(request_id, {})
    if method == "tools/call":
        return _call(request_id, params, toolkit, raise_internal=raise_internal)
    return _fail(request_id, -32601, f"Method not found: {method}")


def _call(
    request_id: Any,
    params: dict[str, Any],
    toolkit: Toolkit,
    *,
    raise_internal: bool,
) -> dict[str, Any]:
    name = params.get("name")
    if not isinstance(name, str) or name not in TOOLS_BY_NAME:
        known = ", ".join(sorted(TOOLS_BY_NAME))
        return _fail(request_id, -32602, f"Unknown tool: {name!r}", {"knownTools": known})
    schema = TOOLS_BY_NAME[name]["inputSchema"]
    try:
        arguments = validate_arguments(schema, params.get("arguments") or {})
    except InvalidArguments as invalid:
        return _fail(request_id, -32602, f"Invalid arguments for {name}: {invalid}")

    handler = toolkit.get(name)
    if handler is None:
        return _fail(request_id, -32603, f"{name} is advertised but is not wired up on this server")

    try:
        payload = handler(arguments)
    except ToolRefusal as refusal:
        # The tool worked. The business said no, and the words it said no in are
        # the answer, not an error code.
        said_no = jsonable({"refused": True, "reason": str(refusal)})
        return _ok(request_id, tool_result(said_no, is_error=True))
    except Exception as failure:  # noqa: BLE001 - a broken tool must not break the server
        if raise_internal:
            raise
        return _fail(request_id, -32603, f"{name} failed: {type(failure).__name__}: {failure}")
    if not isinstance(payload, dict):
        return _fail(request_id, -32603, f"{name} returned {type(payload).__name__}, not an object")
    return _ok(request_id, tool_result(jsonable(payload)))


def parse_failure(detail: str) -> dict[str, Any]:
    """The -32700 response for a body that was not JSON at all."""
    return _fail(None, -32700, f"Parse error: {detail}")


def parse_message(body: bytes | str) -> Any:
    """The JSON-RPC message in a request body, or a ``ParseError`` for bad JSON."""
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    try:
        return json.loads(body)
    except (ValueError, TypeError) as broken:
        raise ParseError(str(broken)) from broken


class ParseError(Exception):
    """The body was not JSON at all, which the protocol reports as -32700."""
