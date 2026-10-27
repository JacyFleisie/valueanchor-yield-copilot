"""The OpenEdge side: what is live, what is not, and how it says so.

Three things are being protected here:

1. **Not configured never reads as connected.** A direction with no address gets
   ``reachable: None`` and the name of the variable that would fix it - never a
   default port presented as a working connection, and never a probe that quietly
   waits on somebody else's server.
2. **A configured address is asked, not assumed.** The probe reports what the
   endpoint actually said, including the error text when it says no, and it tries
   both protocol eras because the OpenEdge MCP Server ships against the handshake.
3. **A credential is used and never echoed.** The token goes into one header on one
   request; it must not appear in any response this API returns.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import app as app_module
import openedge
import version


TOKEN_SENTINEL = "eyJhbGciOi-JWT-THAT-MUST-NEVER-BE-RETURNED"


@pytest.fixture(scope="session")
def client():
    with TestClient(app_module.app) as test_client:
        yield test_client


@pytest.fixture
def no_endpoints(monkeypatch):
    """The honest default: nothing configured, and no network available."""
    for endpoint in openedge.ENDPOINTS:
        monkeypatch.delenv(endpoint.env_url, raising=False)
        monkeypatch.delenv(endpoint.env_token, raising=False)

    def refuse(*_args, **_kwargs):
        raise AssertionError("an unconfigured endpoint must never be called")

    monkeypatch.setattr(openedge, "_post", refuse)


@pytest.fixture
def openedge_configured(monkeypatch):
    endpoint = openedge.ENDPOINTS_BY_KEY["openedge"]
    monkeypatch.setenv(endpoint.env_url, "http://openedge.test:8500/http")
    monkeypatch.setenv(endpoint.env_token, TOKEN_SENTINEL)
    return endpoint


def stub_post(monkeypatch, replies):
    """Stand in for the network, keyed on the JSON-RPC method being sent."""
    calls: list[dict] = []

    def fake(url, payload, headers, timeout):
        calls.append({"url": url, "payload": payload, "headers": headers, "timeout": timeout})
        reply = replies.get(payload["method"])
        if isinstance(reply, Exception):
            raise reply
        if reply is None:
            return {"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": -32601, "message": "Method not found"}}
        return reply

    monkeypatch.setattr(openedge, "_post", fake)
    return calls


# ---------------------------------------------------------------------------
# The status report
# ---------------------------------------------------------------------------

def test_the_inbound_direction_is_live_without_anything_installed(client, no_endpoints):
    body = client.get("/api/openedge/status").json()
    inbound = body["inbound"]
    assert inbound["available"] is True
    assert inbound["endpoint"].endswith("/mcp")
    assert inbound["clientConfig"]["mcpServers"]["valueanchor"]["url"].endswith("/mcp")
    assert set(inbound["protocols"]) >= {"2026-07-28", "2025-11-25"}


def test_the_client_snippet_points_at_the_host_the_caller_used(client, no_endpoints):
    body = client.get("/api/openedge/status").json()
    assert "testserver" in body["inbound"]["clientConfig"]["mcpServers"]["valueanchor"]["url"]


def test_an_unconfigured_direction_is_not_called_and_says_what_would_configure_it(client, no_endpoints):
    body = client.get("/api/openedge/status").json()
    assert body["outbound"], "both outbound directions should be reported"
    for entry in body["outbound"]:
        assert entry["configured"] is False
        assert entry["reachable"] is None, "'nothing was called' is not 'the call failed'"
        assert entry["envUrl"] in entry["error"]
        assert entry["url"] == openedge.ENDPOINTS_BY_KEY[entry["key"]].default_url
        assert entry["install"] and entry["docs"].startswith("https://")
        assert entry["notes"], "an unconfigured direction should explain what it would add"


def test_asking_for_a_probe_with_nothing_configured_still_calls_nothing(client, no_endpoints):
    body = client.get("/api/openedge/status?probe=true").json()
    assert all(entry["reachable"] is None for entry in body["outbound"])


def test_the_documented_defaults_are_the_real_ones():
    openedge_endpoint = openedge.ENDPOINTS_BY_KEY["openedge"]
    assert openedge_endpoint.default_url == "http://localhost:8500/http"
    assert openedge_endpoint.auth_header == "X-OEMCP-SERVICEACCOUNT"
    rag = openedge.ENDPOINTS_BY_KEY["rag"]
    assert rag.default_url == "", "the RAG address carries an account and box id, so it has no default"
    assert rag.auth_header == "X-NUCLIA-SERVICEACCOUNT"


def test_a_token_is_never_returned_in_a_status_report(client, openedge_configured):
    body = client.get("/api/openedge/status").json()
    assert TOKEN_SENTINEL not in json.dumps(body)
    assert openedge_configured.env_token in json.dumps(body), "the variable name is useful; the value is not"


# ---------------------------------------------------------------------------
# Probing a configured endpoint
# ---------------------------------------------------------------------------

def test_a_handshake_endpoint_is_probed_that_way_first(monkeypatch, openedge_configured):
    calls = stub_post(
        monkeypatch,
        {
            "initialize": {
                "jsonrpc": "2.0",
                "id": 1,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "openedge-mcp", "version": "12.8"},
                },
            },
            "tools/list": {
                "jsonrpc": "2.0",
                "id": 3,
                "result": {"tools": [{"name": "get_customer"}, {"name": "list_orders"}]},
            },
        },
    )
    report = openedge.probe(openedge_configured)
    assert report["reachable"] is True
    assert report["protocol"] == "handshake"
    assert report["tools"] == ["get_customer", "list_orders"]
    assert report["serverInfo"]["name"] == "openedge-mcp"
    assert [call["payload"]["method"] for call in calls] == ["initialize", "tools/list"]
    # The credential travels as the documented header, with the documented prefix.
    assert calls[0]["headers"]["X-OEMCP-SERVICEACCOUNT"] == TOKEN_SENTINEL
    assert TOKEN_SENTINEL not in json.dumps(report)
    # The probe introduces itself with the release version, read from the one
    # constant. This used to be a second copy of the number, which a version bump
    # would have left behind in a log on somebody else's server.
    assert calls[0]["payload"]["params"]["clientInfo"] == {"name": "valueanchor", "version": version.VERSION}


def test_a_stateless_endpoint_is_probed_through_discovery(monkeypatch, openedge_configured):
    calls = stub_post(
        monkeypatch,
        {
            "initialize": {"jsonrpc": "2.0", "id": 1, "error": {"code": -32601, "message": "Method not found"}},
            "server/discover": {
                "jsonrpc": "2.0",
                "id": 2,
                "result": {"supportedVersions": ["2026-07-28"], "capabilities": {"tools": {}}},
            },
            "tools/list": {"jsonrpc": "2.0", "id": 3, "result": {"tools": [{"name": "search_orders"}]}},
        },
    )
    report = openedge.probe(openedge_configured)
    assert report["reachable"] is True
    assert report["protocol"] == "stateless"
    assert report["tools"] == ["search_orders"]
    assert report["supportedVersions"] == ["2026-07-28"]
    assert [call["payload"]["method"] for call in calls] == ["initialize", "server/discover", "tools/list"]


def test_a_server_that_refuses_both_eras_reports_what_it_said(monkeypatch, openedge_configured):
    stub_post(
        monkeypatch,
        {
            "initialize": {"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "Unauthorized"}},
            "server/discover": {"jsonrpc": "2.0", "id": 2, "error": {"code": -32601, "message": "Method not found"}},
        },
    )
    report = openedge.probe(openedge_configured)
    assert report["reachable"] is False
    assert report["handshakeError"] == "Unauthorized (code -32600)"
    assert report["error"] == "Method not found (code -32601)"


def test_a_connection_that_never_answers_reports_the_real_failure(monkeypatch, openedge_configured):
    stub_post(
        monkeypatch,
        {
            "initialize": ConnectionRefusedError("connection refused to port 8500"),
            "server/discover": ConnectionRefusedError("connection refused to port 8500"),
        },
    )
    report = openedge.probe(openedge_configured)
    assert report["reachable"] is False
    assert "connection refused to port 8500" in report["error"]
    assert "ConnectionRefusedError" in report["firstError"], "the exception type is part of the honest answer"


def test_a_probe_against_nothing_configured_makes_no_request(no_endpoints):
    report = openedge.probe(openedge.ENDPOINTS_BY_KEY["openedge"])
    assert report["reachable"] is None
    assert openedge.ENDPOINTS_BY_KEY["openedge"].env_url in report["error"]


# ---------------------------------------------------------------------------
# The transport itself, which is the part a stub would not have covered
# ---------------------------------------------------------------------------

def test_a_server_that_answers_on_a_stream_is_still_read(monkeypatch):
    """Streamable HTTP lets a server reply on an SSE stream even for one request."""
    import httpx

    class Streamed:
        status_code = 200
        text = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n\n'

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Streamed())
    body = openedge._post("http://example.test/mcp", {"jsonrpc": "2.0", "id": 1, "method": "ping"}, {}, 5)
    assert body["result"] == {"ok": True}


def test_an_http_error_is_reported_with_its_status(monkeypatch):
    import httpx

    class Unauthorised:
        status_code = 401
        text = "unauthorized"

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: Unauthorised())
    with pytest.raises(RuntimeError) as raised:
        openedge._post("http://example.test/mcp", {}, {}, 5)
    assert "401" in str(raised.value) and "unauthorized" in str(raised.value)


def test_a_body_that_is_not_json_at_all_is_a_real_error(monkeypatch):
    import httpx

    class NotJson:
        status_code = 200
        text = "<html>hello</html>"

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: NotJson())
    with pytest.raises(ValueError):
        openedge._post("http://example.test/mcp", {}, {}, 5)
