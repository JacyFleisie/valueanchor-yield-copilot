"""The OpenEdge side of ValueAnchor, and the honest state of it.

The hackathon kit's shape is that OpenEdge holds the records and the business
rules, and the AI layer is handed a *tool surface* rather than a prompt full of
numbers. ValueAnchor sits on the other half of that - it is the engine that
decides and explains - so the integration runs in two directions, and they are
not equally available:

* **Inbound, always on.** Any MCP host - an OpenEdge-built agent, a desktop
  assistant, a test harness - can call ValueAnchor's own MCP server at
  ``POST /mcp``. That needs nothing installed, works on this machine today, and
  is the direction a hackathon demo should lean on.
* **Outbound, off until configured.** ValueAnchor can read a business's records
  from an OpenEdge MCP Server, and facts from a Progress Agentic RAG Context Box.
  Both of those are software an owner installs and points at their own OpenEdge
  database, so they are off until the environment says otherwise. This module
  exists to say *which* of the two directions is live, in words, rather than to
  quietly fail or to pretend a connection exists.

Nothing here logs, returns or stores a credential. A token is read from the
environment, used as one header on one probe, and never echoed back; the status
report contains the header *name* and never its value.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

# The current MCP revision and the handshake revision the probe falls back to.
# The OpenEdge MCP Server ships for clients on the handshake era, so a probe that
# only knew the current one would report a working server as unreachable.
CURRENT_PROTOCOL_VERSION = "2026-07-28"
HANDSHAKE_PROTOCOL_VERSION = "2025-06-18"

PROBE_TIMEOUT_SECONDS = 6.0


@dataclass(frozen=True)
class Endpoint:
    """One outbound thing an owner can point ValueAnchor at.

    ``env_url`` decides whether it counts as configured: the default is only what
    the documentation says the thing listens on, and saying that a default port is
    therefore "connected" would be the exact lie this module exists to avoid.
    """

    key: str
    name: str
    env_url: str
    env_token: str
    default_url: str
    auth_header: str
    auth_prefix: str
    transport: str
    install: str
    docs: str
    notes: list[str] = field(default_factory=list)


ENDPOINTS: tuple[Endpoint, ...] = (
    Endpoint(
        key="openedge",
        name="OpenEdge MCP Server",
        env_url="OPENEDGE_MCP_URL",
        env_token="OPENEDGE_MCP_TOKEN",
        default_url="http://localhost:8500/http",
        auth_header="X-OEMCP-SERVICEACCOUNT",
        auth_prefix="",
        transport="Streamable HTTP (POST to one endpoint; SSE also available at /sse)",
        install=(
            "Install the OpenEdge MCP Server distribution, load its Docker images, then run "
            "`./mcpgen up --spec ./openapi.yml` from the profile directory."
        ),
        docs="https://docs.progress.com/bundle/openedge-mcp-server/page/Get-started-with-OpenEdge-MCP-Server.html",
        notes=[
            "The server generates one MCP tool per operation in the OpenAPI specification you "
            "give it, which for an ABL service comes from a Data Object Service catalog. Which "
            "tools exist is therefore a property of your specification, not of the server, so "
            "this probe asks the server and reports back what it actually advertises.",
            "Documented defaults are Streamable HTTP on 0.0.0.0:8500 at the path /http, with "
            "authentication optional: the read-only configuration runs with it disabled, and a "
            "secured one uses a service-account JWT or OAuth.",
            "GET operations are included by default; POST, PUT and DELETE are excluded unless "
            "explicitly enabled. That default is a good match for this product, which reads "
            "records and writes only its own decision log.",
        ],
    ),
    Endpoint(
        key="rag",
        name="Progress Agentic RAG Context Box",
        env_url="PROGRESS_RAG_MCP_URL",
        env_token="PROGRESS_RAG_TOKEN",
        default_url="",
        auth_header="X-NUCLIA-SERVICEACCOUNT",
        auth_prefix="Bearer ",
        transport="MCP over HTTP at /v1/kb/<account>/<zone>/<kb_uuid>/mcp, with a REST search API alongside it",
        install=(
            "Create a Context Box in Progress Agentic RAG, then copy its MCP URL - "
            "https://<zone>.rag.progress.cloud/v1/kb/<account>/<zone>/<kb_uuid>/mcp - and an API "
            "key with at least Search access."
        ),
        docs="https://docs.rag.progress.cloud/docs/context-box/connect/",
        notes=[
            "This is the retrieval half of the kit: an OpenEdge business's own documents and "
            "notes become retrievable, so an answer can cite policy rather than only rows.",
            "ValueAnchor already retrieves from its own corpus - live stock, perks, the owner's "
            "rules, logged decisions and what the owner has taught it - and every answer lists "
            "the records behind it. A Context Box adds a second, external corpus next to that "
            "one rather than replacing it.",
            "There is no sensible default URL: the address carries your account and box id, so "
            "this one stays unconfigured until it is set deliberately.",
        ],
    ),
)

ENDPOINTS_BY_KEY = {endpoint.key: endpoint for endpoint in ENDPOINTS}


def configured_url(endpoint: Endpoint) -> str:
    """The URL a probe would use, which is empty until the owner sets one."""
    return (os.environ.get(endpoint.env_url) or "").strip()


def is_configured(endpoint: Endpoint) -> bool:
    """Configured means the owner named it, not that a default port exists."""
    return bool(configured_url(endpoint))


def _headers(endpoint: Endpoint) -> dict[str, str]:
    """The headers one probe sends. The token is used here and nowhere else."""
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    token = (os.environ.get(endpoint.env_token) or "").strip()
    if token:
        headers[endpoint.auth_header] = f"{endpoint.auth_prefix}{token}"
    return headers


def _rpc(method: str, params: dict[str, Any] | None, request_id: int) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _post(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
) -> dict[str, Any]:
    """One JSON-RPC POST. Split out so a test can stand in for the network."""
    import httpx

    response = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    if response.status_code >= 400:
        raise RuntimeError(f"HTTP {response.status_code} from {url}: {response.text[:200]}")
    body = response.text
    # A server may answer on an SSE stream even for a single request, in which case
    # the message is the last `data:` line. An `event:` line usually comes first, so
    # the whole body is scanned rather than only its opening characters.
    data_lines = [line[5:].strip() for line in body.splitlines() if line.startswith("data:")]
    if data_lines:
        body = data_lines[-1]
    import json

    return json.loads(body)


def _rpc_error(response: dict[str, Any]) -> str | None:
    error = response.get("error")
    if isinstance(error, dict):
        return f"{error.get('message', 'error')} (code {error.get('code')})"
    return None


def probe(endpoint: Endpoint, timeout: float = PROBE_TIMEOUT_SECONDS) -> dict[str, Any]:
    """Ask a configured endpoint what it is, and report what it actually said.

    Two eras are tried because a client in a hackathon may be either: the
    handshake (``initialize`` then ``tools/list``), which the OpenEdge MCP Server
    ships against, and the stateless revision, whose server announces itself with
    ``server/discover``. The first error is kept and reported when both fail,
    because "connection refused" and "401 Unauthorized" are different problems
    and the owner should be told which one they have.
    """
    url = configured_url(endpoint)
    if not url:
        return {
            "reachable": None,
            "error": f"{endpoint.env_url} is not set, so nothing was called",
        }
    headers = _headers(endpoint)
    first_error = ""

    try:
        handshake = _post(
            url,
            _rpc(
                "initialize",
                {
                    "protocolVersion": HANDSHAKE_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "valueanchor", "version": "1.1.0"},
                },
                1,
            ),
            headers,
            timeout,
        )
    except Exception as failure:  # noqa: BLE001 - the reason is the whole point
        first_error = f"{type(failure).__name__}: {failure}"
        handshake = {}

    error = _rpc_error(handshake) if handshake else "no response"
    if not error:
        result = handshake.get("result") or {}
        tools = _call_tools_list(url, headers, timeout)
        return {
            "reachable": True,
            "protocol": "handshake",
            "protocolVersion": result.get("protocolVersion"),
            "serverInfo": result.get("serverInfo"),
            "tools": tools,
            "error": "",
        }

    try:
        discovered = _post(url, _rpc("server/discover", {"_meta": {}}, 2), headers, timeout)
    except Exception as failure:  # noqa: BLE001
        return {
            "reachable": False,
            "error": first_error or f"{type(failure).__name__}: {failure}",
            "firstError": first_error,
            "handshakeError": error,
        }

    discovery_error = _rpc_error(discovered)
    if discovery_error:
        return {"reachable": False, "error": discovery_error, "handshakeError": error, "firstError": first_error}
    result = discovered.get("result") or {}
    return {
        "reachable": True,
        "protocol": "stateless",
        "protocolVersion": CURRENT_PROTOCOL_VERSION,
        "supportedVersions": result.get("supportedVersions"),
        "tools": _call_tools_list(url, headers, timeout, stateless=True),
        "error": "",
    }


def _call_tools_list(
    url: str,
    headers: dict[str, str],
    timeout: float,
    stateless: bool = False,
) -> list[str]:
    """The tool names the endpoint advertises - names only, nothing invented."""
    params: dict[str, Any] = {}
    if stateless:
        params["_meta"] = {"io.modelcontextprotocol/protocolVersion": CURRENT_PROTOCOL_VERSION}
    try:
        response = _post(url, _rpc("tools/list", params, 3), headers, timeout)
    except Exception:  # noqa: BLE001 - a server that answers nothing still answered something
        return []
    if _rpc_error(response):
        return []
    tools = ((response.get("result") or {}).get("tools")) or []
    return [tool.get("name") for tool in tools if isinstance(tool, dict) and tool.get("name")]


def client_config(base_url: str) -> dict[str, Any]:
    """The snippet that points an MCP host at this ValueAnchor.

    This is the direction that needs nothing installed, so it is the one a demo
    should show first: the host gets the yield engine as a tool surface, and every
    number it returns was computed here rather than by the model.
    """
    return {
        "mcpServers": {
            "valueanchor": {
                "type": "http",
                "url": f"{base_url.rstrip('/')}/mcp",
            }
        }
    }


def status(base_url: str = "http://localhost:8000", probe_endpoints: bool = False) -> dict[str, Any]:
    """Where the integration stands, per direction, with nothing rounded up.

    A direction that is not configured reports ``reachable: None`` rather than
    false: "nothing was called" and "the call failed" are different facts, and
    collapsing them is how a status page starts lying.
    """
    report: dict[str, Any] = {
        "inbound": {
            "name": "ValueAnchor as an MCP server",
            "available": True,
            "endpoint": f"{base_url.rstrip('/')}/mcp",
            "transport": "Streamable HTTP (POST, JSON-RPC 2.0). Notifications are answered with 202.",
            "protocols": [CURRENT_PROTOCOL_VERSION, "2025-11-25", "2025-06-18", "2024-11-05"],
            "needs": "Nothing installed. This is live whenever the backend is running.",
            "clientConfig": client_config(base_url),
            "notes": [
                "This is the direction that makes the demo work on one laptop: the OpenEdge side "
                "can be absent while an agent still gets real, computed answers.",
                "Both protocol eras are served, so a host built on the earlier handshake and one "
                "built on the stateless revision can both connect.",
            ],
        },
        "outbound": [],
        "checkedAt": datetime.now(timezone.utc).isoformat(),
    }
    for endpoint in ENDPOINTS:
        configured = is_configured(endpoint)
        entry: dict[str, Any] = {
            "key": endpoint.key,
            "name": endpoint.name,
            "configured": configured,
            "url": configured_url(endpoint) or endpoint.default_url,
            "envUrl": endpoint.env_url,
            "envToken": endpoint.env_token,
            "authHeader": endpoint.auth_header,
            "transport": endpoint.transport,
            "install": endpoint.install,
            "docs": endpoint.docs,
            "notes": list(endpoint.notes),
            "reachable": None,
            "error": "",
            "tools": [],
        }
        if configured and probe_endpoints:
            entry.update(probe(endpoint))
        elif not configured:
            entry["error"] = (
                f"Not called. Set {endpoint.env_url} (and {endpoint.env_token} if it needs a "
                "credential) and ask again."
            )
        report["outbound"].append(entry)
    return report


def toolkit_probe(endpoint: Endpoint, timeout: float = PROBE_TIMEOUT_SECONDS) -> Callable[[], dict[str, Any]]:
    """A zero-argument probe, for callers that only want the function."""
    return lambda: probe(endpoint, timeout)
