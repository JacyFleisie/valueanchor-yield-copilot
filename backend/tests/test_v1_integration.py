"""Honest end-to-end verification of the one way stock enters the product.

There used to be three on-ramps - a JSON batch endpoint, a CSV paste and a sync
bridge - and all three were ways of hand-typing figures the business already
holds somewhere else. They are gone by design: the product now reads stock out
of the owner's own OpenEdge database through the read-only bridge behind
``POST /api/openedge/link``, and that is the only way in.

So this file drives *that* endpoint, end to end, with the bridge stubbed. A
stub is honest here because the thing under test is what this server does with
rows it was given: the bridge's ability to run an AVM is covered separately in
``test_openedge.py``, and no CI machine has an OpenEdge installation. What is
proved here is the promise an owner actually cares about - link a database and
the honest numbers come back through ``/v1``, the copilot starts answering about
that business, and a bad database is refused in words rather than stored.

Each test owns a scratch DB, so a run never touches the working dataset.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

import pytest
from fastapi.testclient import TestClient

import app as app_module
import openedge_bridge


@pytest.fixture
def client():
    scratch_dir = pathlib.Path(tempfile.mkdtemp(prefix="valueanchor-v1-"))
    scratch_db = scratch_dir / "valueanchor-v1.db"
    previous_env = os.environ.get("VALUEANCHOR_DB")
    previous_path = app_module.DB_PATH
    os.environ["VALUEANCHOR_DB"] = str(scratch_db)
    # Recompute the module's DB_PATH so connect() uses the scratch file.
    app_module.DB_PATH = scratch_db
    scratch_db.parent.mkdir(parents=True, exist_ok=True)
    if scratch_db.exists():
        scratch_db.unlink()
    app_module.init_db()
    try:
        with TestClient(app_module.app) as c:
            yield c
    finally:
        # Point the module back where it was. These tests replace a global the
        # rest of the suite also reads, and a leftover path - which may still
        # hold a linked business - would make a later module build its language
        # model from this test's shelf instead of its own.
        app_module.DB_PATH = previous_path
        if previous_env is None:
            os.environ.pop("VALUEANCHOR_DB", None)
        else:
            os.environ["VALUEANCHOR_DB"] = previous_env
        # Honest teardown: close any lingering connection and remove the scratch file.
        try:
            scratch_db.unlink()
        except (PermissionError, FileNotFoundError):
            # Windows can hold the file for a moment; best-effort cleanup only.
            pass


def stub_bridge(monkeypatch, *, business_name: str, lines, units_left: int | None = None):
    """Stand in for the AVM: the read result, in the shape the bridge returns.

    The link route calls exactly one function, ``openedge_bridge.call_tool``, so
    stubbing that is the whole of the substitute. Everything after it - the
    validation, the mapped copy, the copilot workspace - is the real code.
    """
    lines = [dict(line) for line in lines]
    total = sum(int(line.get("totalCapacity", 0)) - int(line.get("unitsSold", 0)) for line in lines)

    def fake_call_tool(name, arguments=None):
        assert name == "openedge_read_stock", "the link path must ask for the one read tool"
        return {
            "isError": False,
            "structuredContent": {
                "source": "openedge",
                "database": (arguments or {}).get("database", "coffee"),
                "businessName": business_name,
                "rowCount": len(lines),
                "unitsLeft": total if units_left is None else units_left,
                "lines": [dict(line) for line in lines],
                "readOnly": True,
            },
        }

    monkeypatch.setattr(openedge_bridge, "call_tool", fake_call_tool)


def stub_bridge_result(monkeypatch, result):
    """A bridge that answers with something other than stock rows."""
    monkeypatch.setattr(openedge_bridge, "call_tool", lambda *a, **k: result)


PANTRY_LINES = [
    {
        "itemName": "Kimchi pancake",
        "category": "Kitchen",
        "basePrice": 100.0,
        "totalCapacity": 20,
        "unitsSold": 4,
        "shelfHoursUntilClosed": 6.0,
    },
    {
        "itemName": "Miso soup",
        "category": "Kitchen",
        "basePrice": 40.0,
        "totalCapacity": 30,
        "unitsSold": 25,
        "shelfHoursUntilClosed": 1.0,
    },
]


# ---------------------------------------------------------------------------
# The bridge: linking is what makes a database answerable
# ---------------------------------------------------------------------------


def test_linking_a_database_makes_it_the_business_the_copilot_answers_about(client, monkeypatch):
    """The mapped copy alone changes nothing; linking is the step that bridges.

    Written as a before-and-after on purpose. Reading a database into a mapped
    copy and having the copilot answer about it are different states, and the
    only way to show the bridge is doing real work is to ask the same question
    either side of it.
    """
    before = client.post(
        "/api/copilot", json={"question": "which line needs attention first?", "sessionId": "pre"}
    ).json()
    assert "Kimchi pancake" not in before["answer"]
    assert "Miso soup" not in before["answer"]

    stub_bridge(monkeypatch, business_name="The Pantry", lines=PANTRY_LINES)
    resp = client.post("/api/openedge/link", json={"database": "coffee"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["accepted"] == 2
    assert body["readOnly"] is True
    assert body["program"] == "06-enquiry-json.p"
    assert body["source"] == "openedge"
    assert body["activeBusiness"]["name"] == "The Pantry"
    assert body["activeBusiness"]["lines"] == 2
    # The starter kit's database carries no floor price, so the floor is assumed
    # and says so rather than arriving dressed as the business's own judgement.
    assert body["activeBusiness"]["floorPct"] == 60
    assert body["activeBusiness"]["floorSource"] == "assumed, because the pushed lines carried no floor"

    # The copilot's own workspace is now the linked business, not a preset.
    profile = client.get("/api/profile").json()
    assert profile["businessName"] == "The Pantry"
    assert profile["sector"] == "connected"
    assert profile["marginFloorPct"] == 60
    assert "assumed" in profile["blurb"]

    inventory = client.get("/api/inventory").json()
    names = {item["itemName"] for item in inventory["items"]}
    assert names == {"Kimchi pancake", "Miso soup"}

    # The retrieval corpus carries the linked stock, which is what makes the
    # answer traceable rather than recalled.
    kinds = client.get("/api/knowledge").json()["summary"]["byKind"]
    assert kinds.get("stock") == 2

    # The engine is rebuilt for the linked vocabulary rather than left on coffee.
    model = client.get("/api/nlu/model").json()
    assert model["profile"]["sector"] == "connected"

    after = client.post(
        "/api/copilot", json={"question": "which line needs attention first?", "sessionId": "post"}
    ).json()
    assert after["inScope"] is True
    assert "Miso soup" in after["answer"], "the copilot must now answer about the linked line"

    # Nothing was written back: the bridge is read-only and stays that way.
    assert body["note"]
    assert "writes nothing back" in body["note"]


def test_linking_the_same_database_twice_replaces_the_copy(client, monkeypatch):
    """Relinking is idempotent, not a fork: one database, one business."""
    stub_bridge(monkeypatch, business_name="The Pantry", lines=PANTRY_LINES)
    first = client.post("/api/openedge/link", json={"database": "coffee"}).json()

    # One more line arrives on the counter between the two presses.
    stub_bridge(
        monkeypatch,
        business_name="The Pantry",
        lines=PANTRY_LINES
        + [
            {
                "itemName": "Tteokbokki",
                "category": "Kitchen",
                "basePrice": 60.0,
                "totalCapacity": 12,
                "unitsSold": 0,
                "shelfHoursUntilClosed": 3.0,
            }
        ],
    )
    second = client.post("/api/openedge/link", json={"database": "coffee"}).json()

    assert second["business"]["businessId"] == first["business"]["businessId"]
    assert second["accepted"] == 3

    stock = client.get(f"/v1/stock?businessId={second['business']['businessId']}").json()
    assert {line["itemName"] for line in stock} == {"Kimchi pancake", "Miso soup", "Tteokbokki"}
    assert len(client.get("/api/inventory").json()["items"]) == 3


# ---------------------------------------------------------------------------
# The read path (data out)
# ---------------------------------------------------------------------------


def test_linked_stock_reads_back_honestly_through_the_v1_read_path(client, monkeypatch):
    stub_bridge(
        monkeypatch,
        business_name="My Shop",
        lines=[
            {
                "itemName": "Widget A",
                "category": "Widgets",
                "basePrice": 10.0,
                "totalCapacity": 100,
                "unitsSold": 60,
                "shelfHoursUntilClosed": 9.0,
            },
            {
                "itemName": "Widget B",
                "category": "Widgets",
                "basePrice": 15.0,
                "totalCapacity": 50,
                "unitsSold": 10,
                "shelfHoursUntilClosed": 4.0,
            },
            {
                "itemName": "Widget C",
                "category": "Widgets",
                "basePrice": 20.0,
                "totalCapacity": 20,
                "unitsSold": 20,
                "shelfHoursUntilClosed": 1.0,
            },
        ],
    )
    linked = client.post("/api/openedge/link", json={"database": "coffee"})
    assert linked.status_code == 200, linked.text
    business_id = linked.json()["business"]["businessId"]
    business_name = linked.json()["business"]["businessName"]

    # Honest read scoped to the business: no other business's stock leaks in.
    stock = client.get(f"/v1/stock?businessId={business_id}").json()
    assert len(stock) == 3
    by_name = {line["itemName"]: line for line in stock}

    wa = by_name["Widget A"]
    assert wa["businessId"] == business_id
    assert wa["businessName"] == business_name
    assert wa["basePrice"] == 10.0
    assert wa["totalCapacity"] == 100
    assert wa["unitsSold"] == 60
    assert wa["unitsLeft"] == 40
    assert wa["sellThroughShare"] == 0.6
    # The starter kit's table carries a shelf price and hours to close. Nothing
    # else, so the floor and the cost are honestly absent rather than guessed.
    assert wa["floorPrice"] is None
    assert wa["costOfGoods"] is None
    assert wa["businessItemId"] is None
    assert wa["timeSignal"] == "closure"
    assert wa["shelfHoursUntilClosed"] == 9.0
    # Closure is a deadline, so the maths may derive a timestamp from it.
    assert wa["expirationTimestamp"]

    wb = by_name["Widget B"]
    assert wb["shelfHoursUntilClosed"] == 4.0
    assert wb["unitsSold"] == 10
    assert wb["unitsLeft"] == 40
    assert wb["sellThroughShare"] == 0.2
    assert wb["urgencyScore"] is not None, "a line with a deadline has an urgency score"

    wc = by_name["Widget C"]
    assert wc["unitsSold"] == wc["totalCapacity"]
    assert wc["unitsLeft"] == 0
    assert wc["sellThroughShare"] == 1.0

    # One line by id, scoped to the business.
    line = client.get(f"/v1/stock/{wc['itemId']}?businessId={business_id}").json()
    assert line["itemId"] == wc["itemId"]
    assert line["itemName"] == "Widget C"


def test_sell_through_is_its_own_thing_with_source_stated(client, monkeypatch):
    stub_bridge(
        monkeypatch,
        business_name="SellThrough Biz",
        lines=[
            {"itemName": "Sold out", "category": "X", "basePrice": 5.0, "totalCapacity": 10, "unitsSold": 10, "shelfHoursUntilClosed": 2.0},
            {"itemName": "Part sold", "category": "X", "basePrice": 5.0, "totalCapacity": 10, "unitsSold": 4, "shelfHoursUntilClosed": 2.0},
        ],
    )
    linked = client.post("/api/openedge/link", json={"database": "coffee"})
    business_id = linked.json()["business"]["businessId"]

    st = client.get(f"/v1/sell-through?businessId={business_id}").json()
    assert st["businessId"] == business_id
    assert st["totalCapacity"] == 20
    assert st["unitsSold"] == 14
    assert st["unitsAvailable"] == 6
    assert st["sellThroughShare"] == 0.7
    assert "derived from stored stock batch" in st["source"]
    assert st["asOf"]


def test_sell_through_is_honest_for_a_business_with_no_stock(client):
    resp = client.get("/v1/sell-through?businessId=no-such-biz")
    assert resp.status_code == 404, resp.text
    # Not a fake 0% number.


def test_stock_for_a_business_with_no_stock_is_an_honest_empty_list(client):
    stock = client.get("/v1/stock?businessId=no-such-biz").json()
    assert stock == []


def test_analyze_is_an_honest_day_summary_off_the_mapped_store(client, monkeypatch):
    stub_bridge(
        monkeypatch,
        business_name="Analyze Biz",
        lines=[
            {"itemName": "Hot", "category": "Hot", "basePrice": 10.0, "totalCapacity": 100, "unitsSold": 90, "shelfHoursUntilClosed": 1.0},
            {"itemName": "Cold", "category": "Cold", "basePrice": 20.0, "totalCapacity": 50, "unitsSold": 10, "shelfHoursUntilClosed": 8.0},
        ],
    )
    business_id = client.post("/api/openedge/link", json={"database": "coffee"}).json()["business"][
        "businessId"
    ]

    summary = client.get(f"/v1/analyze?businessId={business_id}").json()
    assert summary["businessId"] == business_id
    assert summary["totalLines"] == 2
    assert summary["totalCapacity"] == 150
    assert summary["unitsSold"] == 100
    assert summary["unitsAvailable"] == 50
    assert summary["sellThroughShare"] == pytest.approx(100 / 150, abs=1e-3)
    assert summary["exposureAtShelfPrice"] == pytest.approx(10 * 10 + 40 * 20, abs=1e-2)
    assert summary["source"] == "derived from stored stock batch"
    assert summary["asOf"]


def test_analyze_one_line_is_honest_profit_scenarios(client, monkeypatch):
    stub_bridge(
        monkeypatch,
        business_name="Plan Biz",
        lines=[
            {
                "itemName": "Premium Widget",
                "category": "Widgets",
                "basePrice": 100.0,
                "totalCapacity": 20,
                "unitsSold": 5,
                "shelfHoursUntilClosed": 3.0,
            },
        ],
    )
    business_id = client.post("/api/openedge/link", json={"database": "coffee"}).json()["business"][
        "businessId"
    ]

    analyzed = client.get(f"/v1/analyze/1?businessId={business_id}").json()
    assert analyzed["item"]["itemName"] == "Premium Widget"
    assert analyzed["item"]["basePrice"] == 100.0
    # The database carries no floor price, so the percent is the assumed one and
    # the flag says the price itself is unknown rather than inventing one.
    assert analyzed["floor"]["percent"] == 60
    assert analyzed["floor"]["floorPriceKnown"] is False
    assert analyzed["floor"]["floorPrice"] is None
    assert isinstance(analyzed["scenarios"], list)
    assert len(analyzed["scenarios"]) == 3
    keys = {s["key"] for s in analyzed["scenarios"]}
    assert keys == {"lapse", "markdown", "bundle"}
    assert analyzed["recommended"] in {"bundle", "review"}
    assert analyzed["source"] == "computed from stored stock batch and stored perks"


# ---------------------------------------------------------------------------
# Refusals: a bad database is refused in words, never stored
# ---------------------------------------------------------------------------


def test_a_database_with_no_rows_is_a_refusal_naming_the_fix(client, monkeypatch):
    stub_bridge(monkeypatch, business_name="The Pantry", lines=[])
    resp = client.post("/api/openedge/link", json={"database": "coffee"})
    assert resp.status_code == 400, resp.text
    assert "04-seed-coffee.p" in resp.text, "name the program that actually fixes it"
    # Nothing was stored, so the honest empty read still holds.
    assert client.get("/v1/stock?businessId=openedge-the-pantry").json() == []


def test_a_database_with_no_business_name_is_refused_not_filed_under_nothing(client, monkeypatch):
    stub_bridge(monkeypatch, business_name="", lines=PANTRY_LINES)
    resp = client.post("/api/openedge/link", json={"database": "coffee"})
    assert resp.status_code == 422, resp.text
    assert "04-seed-coffee.p" in resp.text


def test_a_bridge_refusal_is_shown_in_words_not_swallowed(client, monkeypatch):
    stub_bridge_result(
        monkeypatch,
        {"isError": True, "structuredContent": {"refused": True, "reason": "No AVM was found."}},
    )
    resp = client.post("/api/openedge/link", json={"database": "coffee"})
    assert resp.status_code == 400, resp.text
    assert "No AVM was found." in resp.text


def test_rows_that_do_not_fit_the_stock_shape_are_refused_at_link_time(client, monkeypatch):
    """A row the product cannot reason about is refused, not half-stored.

    The starter kit's table is typed, so this is the shape a *changed* table
    produces: a column that stopped being a number. Refusing it whole is the
    honest answer - half a shelf would read as a real half.
    """
    stub_bridge(
        monkeypatch,
        business_name="Bad Biz",
        lines=[
            {
                "itemName": "Bad",
                "category": "X",
                "basePrice": "cheap",
                "totalCapacity": 1,
                "unitsSold": 0,
                "shelfHoursUntilClosed": 2.0,
            }
        ],
    )
    resp = client.post("/api/openedge/link", json={"database": "coffee"})
    assert resp.status_code == 422, resp.text
    assert "do not fit the stock shape" in resp.text
    # Refused on the way in, so the read path never has to meet it.
    assert client.get("/v1/stock?businessId=openedge-bad-biz").json() == []


def test_the_database_argument_is_a_name_not_a_path(client, monkeypatch):
    """A name that could be turned into a path is refused before any call."""
    stub_bridge(monkeypatch, business_name="The Pantry", lines=PANTRY_LINES)
    for bad in ("../etc/passwd", "coffee/../../other", "coffee\\db"):
        resp = client.post("/api/openedge/link", json={"database": bad})
        assert resp.status_code == 400, resp.text
        assert "name" in resp.text

    blank = client.post("/api/openedge/link", json={"database": "   "})
    assert blank.status_code in (400, 422), blank.text
