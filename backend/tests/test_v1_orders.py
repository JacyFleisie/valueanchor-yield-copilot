"""Honest end-to-end verification of the /v1 orders surface.

The orders surface exists so sell-through can come from real orders instead of
a mapped stock copy, with its source stated. These tests prove exactly that:
stock is linked out of an OpenEdge database, orders go in through
``POST /v1/orders/batch``, events move them, and the sell-through read says
where its numbers came from - plus the honest failures (another business's
orders never appear; a business with no orders gets a 404, not a fake 0%).

It runs against a scratch DB the same way ``test_v1_integration.py`` does, so a
run never touches the working dataset.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import app as app_module
import openedge_bridge


@pytest.fixture
def client():
    scratch_dir = pathlib.Path(tempfile.mkdtemp(prefix="valueanchor-v1-orders-"))
    scratch_db = scratch_dir / "valueanchor-v1-orders.db"
    previous_env = os.environ.get("VALUEANCHOR_DB")
    previous_path = app_module.DB_PATH
    os.environ["VALUEANCHOR_DB"] = str(scratch_db)
    app_module.DB_PATH = scratch_db
    scratch_db.parent.mkdir(parents=True, exist_ok=True)
    if scratch_db.exists():
        scratch_db.unlink()
    app_module.init_db()
    try:
        with TestClient(app_module.app) as c:
            yield c
    finally:
        # Put the global back: a later module that reads DB_PATH must not build
        # its world from this test's scratch shelf.
        app_module.DB_PATH = previous_path
        if previous_env is None:
            os.environ.pop("VALUEANCHOR_DB", None)
        else:
            os.environ["VALUEANCHOR_DB"] = previous_env
        try:
            scratch_db.unlink()
        except (PermissionError, FileNotFoundError):
            # Windows can hold the file for a moment; best-effort cleanup only.
            pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def iso(hours_ago: float = 0.0) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def link_stock(client, monkeypatch, *, business_name: str = "Orders Biz", lines=None):
    """Give a business stock the only way it can arrive now: by linking it.

    The push endpoint is gone by design, so stock is read out of an OpenEdge
    database through ``POST /api/openedge/link``. The AVM is stubbed (it is
    covered in ``test_openedge.py``); everything after the read - validation,
    the mapped copy, the business identity - is the real path. The stored
    business id is returned because it is *derived* from the database's own name
    for the business rather than chosen here, which is what makes relinking
    idempotent instead of forking a new business on every press.
    """
    lines = [
        dict(
            line,
            shelfHoursUntilClosed=line.get("shelfHoursUntilClosed", 4.0),
        )
        for line in (
            lines
            or [
                {
                    "itemName": "Sourdough loaf",
                    "category": "Bakery",
                    "basePrice": 40.0,
                    "totalCapacity": 10,
                    "unitsSold": 2,
                }
            ]
        )
    ]

    def fake_call_tool(name, arguments=None):
        return {
            "isError": False,
            "structuredContent": {
                "source": "openedge",
                "database": (arguments or {}).get("database", "coffee"),
                "businessName": business_name,
                "rowCount": len(lines),
                "lines": [dict(line) for line in lines],
                "readOnly": True,
            },
        }

    monkeypatch.setattr(openedge_bridge, "call_tool", fake_call_tool)
    response = client.post("/api/openedge/link", json={"database": "coffee"})
    assert response.status_code == 200, response.text
    return response.json()["business"]["businessId"]


def push_orders(client, business_id: str, orders):
    response = client.post(
        "/v1/orders/batch",
        json={
            "business": {"businessId": business_id, "businessName": "Orders Biz"},
            "orders": orders,
        },
    )
    return response


# ---------------------------------------------------------------------------
# Push: accepted / rejected, per business
# ---------------------------------------------------------------------------


def test_orders_batch_keeps_the_good_orders_and_rejects_the_bad_ones(client):
    resp = push_orders(
        client,
        "order-biz-001",
        [
            # Honest: one good order.
            {
                "businessOrderId": "SO-1",
                "placedAt": iso(),
                "status": "Fulfilled",
                "items": [
                    {"businessItemId": "sku-loaf", "itemName": "Sourdough loaf", "qty": 2, "unitPrice": 40.0},
                    {"itemName": "Coffee beans", "qty": 1, "unitPrice": 120.0},
                ],
                "total": 200.0,
                "currency": "ZAR",
                "source": "pos",
                "customer": {"id": "cust-7"},
            },
            # Rejected: unknown status.
            {
                "businessOrderId": "SO-2",
                "placedAt": iso(),
                "status": "in-transit",
                "items": [{"itemName": "Bun", "qty": 1, "unitPrice": 5.0}],
            },
            # Rejected: qty below one.
            {
                "businessOrderId": "SO-3",
                "placedAt": iso(),
                "items": [{"itemName": "Bun", "qty": 0, "unitPrice": 5.0}],
            },
            # Rejected: the total does not match the lines.
            {
                "businessOrderId": "SO-4",
                "placedAt": iso(),
                "items": [{"itemName": "Bun", "qty": 2, "unitPrice": 5.0}],
                "total": 99.0,
            },
            # Rejected: typo'd date, refused here rather than breaking reads.
            {
                "businessOrderId": "SO-5",
                "placedAt": "next tuesday",
                "items": [{"itemName": "Bun", "qty": 1, "unitPrice": 5.0}],
            },
            # Rejected: the same ref twice in one batch.
            {
                "businessOrderId": "SO-1",
                "placedAt": iso(),
                "items": [{"itemName": "Bun", "qty": 1, "unitPrice": 5.0}],
            },
        ],
    )
    assert resp.status_code == 200, resp.text
    batch = resp.json()
    assert [entry["businessOrderId"] for entry in batch["accepted"]] == ["SO-1"]
    assert batch["accepted"][0]["replaced"] is False
    reasons = {entry["businessOrderId"]: entry["reason"] for entry in batch["rejected"]}
    assert set(reasons) == {"SO-2", "SO-3", "SO-4", "SO-5", "SO-1"}
    assert "in-transit" in reasons["SO-2"]
    assert "qty" in reasons["SO-3"]
    assert "99.0" in reasons["SO-4"] and "10.00" in reasons["SO-4"], reasons["SO-4"]
    assert "ISO date/time" in reasons["SO-5"]
    assert "more than once" in reasons["SO-1"]
    assert batch["storedAsOf"]

    # The stored order reads back with computed subtotals and the total checked.
    orders = client.get("/v1/orders?businessId=order-biz-001").json()
    assert len(orders["orders"]) == 1
    stored = orders["orders"][0]
    assert stored["businessOrderId"] == "SO-1"
    assert stored["status"] == "fulfilled"  # normalised, not the client's casing
    assert stored["total"] == 200.0
    assert stored["items"][0]["subtotal"] == 80.0
    assert stored["items"][0]["businessItemId"] == "sku-loaf"
    assert stored["currency"] == "ZAR"
    assert stored["source"] == "pos"
    assert stored["customer"]["id"] == "cust-7"


def test_repushing_the_same_order_replaces_it_instead_of_duplicating(client):
    first = push_orders(
        client,
        "order-biz-002",
        [
            {
                "businessOrderId": "SO-9",
                "placedAt": iso(),
                "items": [{"itemName": "Bun", "qty": 1, "unitPrice": 5.0}],
            }
        ],
    )
    assert first.json()["accepted"][0]["replaced"] is False

    second = push_orders(
        client,
        "order-biz-002",
        [
            {
                "businessOrderId": "SO-9",
                "placedAt": iso(),
                "items": [{"itemName": "Bun", "qty": 3, "unitPrice": 5.0}],
            }
        ],
    )
    assert second.json()["accepted"][0]["replaced"] is True

    orders = client.get("/v1/orders?businessId=order-biz-002").json()["orders"]
    assert len(orders) == 1, "a re-pushed ref replaces its order, never duplicates it"
    assert orders[0]["total"] == 15.0
    assert len(orders[0]["items"]) == 1, "the replaced order's own lines are replaced too"


def test_an_empty_order_batch_is_an_honest_400(client):
    resp = push_orders(client, "order-biz-003", [])
    assert resp.status_code == 400, resp.text


def test_orders_never_leak_between_businesses(client):
    push_orders(
        client,
        "order-biz-a",
        [
            {
                "businessOrderId": "A-1",
                "placedAt": iso(),
                "items": [{"itemName": "Bun", "qty": 1, "unitPrice": 5.0}],
            }
        ],
    )
    # B sees an honest empty list, not A's order.
    assert client.get("/v1/orders?businessId=order-biz-b").json()["orders"] == []
    # And an event against A's order from B's scope is a 404, not a mutation.
    resp = client.post(
        "/v1/orders/A-1/events?businessId=order-biz-b",
        json={"event": "fulfilled"},
    )
    assert resp.status_code == 404, resp.text
    assert client.get("/v1/orders?businessId=order-biz-a").json()["orders"][0]["status"] == "placed"


# ---------------------------------------------------------------------------
# Reads and filters
# ---------------------------------------------------------------------------


def test_orders_read_filters_and_honest_empty_notes(client):
    push_orders(
        client,
        "order-biz-004",
        [
            {
                "businessOrderId": "F-1",
                "placedAt": iso(hours_ago=2),
                "status": "fulfilled",
                "items": [{"businessItemId": "sku-loaf", "itemName": "Sourdough loaf", "qty": 1, "unitPrice": 40.0}],
            },
            {
                "businessOrderId": "P-1",
                "placedAt": iso(hours_ago=48),
                "items": [{"itemName": "Coffee beans", "qty": 2, "unitPrice": 120.0}],
            },
        ],
    )

    by_status = client.get("/v1/orders?businessId=order-biz-004&status=fulfilled").json()
    assert [o["businessOrderId"] for o in by_status["orders"]] == ["F-1"]

    # The timestamp goes through params so it is URL-encoded: a raw "+" in an
    # offset would otherwise arrive as a space, exactly as it would in a browser.
    recent = client.get(
        "/v1/orders",
        params={"businessId": "order-biz-004", "from": iso(hours_ago=24)},
    ).json()
    assert [o["businessOrderId"] for o in recent["orders"]] == ["F-1"]

    by_item = client.get("/v1/orders?businessId=order-biz-004&itemId=sku-loaf").json()
    assert [o["businessOrderId"] for o in by_item["orders"]] == ["F-1"]

    # Newest first, so "what happened this week" reads in order.
    all_orders = client.get("/v1/orders?businessId=order-biz-004").json()
    assert [o["businessOrderId"] for o in all_orders["orders"]] == ["F-1", "P-1"]
    assert all_orders["note"] is None

    filtered_empty = client.get("/v1/orders?businessId=order-biz-004&status=cancelled").json()
    assert filtered_empty["orders"] == []
    assert "filter" in filtered_empty["note"]

    never_pushed = client.get("/v1/orders?businessId=no-such-biz").json()
    assert never_pushed["orders"] == []
    assert "No orders are stored" in never_pushed["note"]

    # Bad filter values are refused honestly, not silently ignored.
    assert client.get("/v1/orders?businessId=order-biz-004&status=weird").status_code == 400
    assert client.get("/v1/orders?businessId=order-biz-004&from=yesterday").status_code == 400


# ---------------------------------------------------------------------------
# Events and the sell-through they make current
# ---------------------------------------------------------------------------


def test_an_event_makes_orders_derived_sellthrough_current(client):
    push_orders(
        client,
        "order-biz-005",
        [
            {
                "businessOrderId": "E-1",
                "placedAt": iso(hours_ago=1),
                "status": "placed",
                "items": [{"itemName": "Sourdough loaf", "qty": 3, "unitPrice": 40.0}],
            },
            {
                "businessOrderId": "E-2",
                "placedAt": iso(hours_ago=2),
                "status": "fulfilled",
                "items": [{"itemName": "Coffee beans", "qty": 2, "unitPrice": 120.0}],
            },
        ],
    )

    # Nothing is sold yet but E-1: E-2 is already fulfilled, E-1 is pending.
    st = client.get("/v1/orders/sellthrough?businessId=order-biz-005").json()
    assert st["soldInWindow"] == 2
    assert st["pendingUnits"] == 3
    assert st["source"] == "orders", "no stock was pushed, so orders are the source"
    assert st["unitsAvailable"] is None, "unknown, not a guessed zero"
    assert st["velocity"] == pytest.approx(2 / 24, abs=1e-3)
    assert st["velocityUnit"] == "units per hour"
    assert st["lastSaleAt"]
    assert "not yet fulfilled" in st["note"]

    # Fulfilling E-1 moves the number without re-pushing anything.
    event = client.post(
        "/v1/orders/E-1/events?businessId=order-biz-005",
        json={"event": "fulfilled", "itemId": "Sourdough loaf", "qty": 3},
    )
    assert event.status_code == 200, event.text
    body = event.json()
    assert body["status"] == "fulfilled"
    assert body["previousStatus"] == "placed"
    assert body["countedAsSold"] is True
    assert "Partial line fulfilment" in body["note"]

    st = client.get("/v1/orders/sellthrough?businessId=order-biz-005").json()
    assert st["soldInWindow"] == 5
    assert st["pendingUnits"] == 0
    assert st["ordersCounted"] == 2

    # Returning it makes it stop counting: the log keeps the history.
    returned = client.post(
        "/v1/orders/E-1/events?businessId=order-biz-005",
        json={"event": "returned"},
    )
    assert returned.status_code == 200
    assert returned.json()["countedAsSold"] is False
    st = client.get("/v1/orders/sellthrough?businessId=order-biz-005").json()
    assert st["soldInWindow"] == 2, "a returned order is not a sale"


def test_event_mistakes_are_refused_specifically(client):
    push_orders(
        client,
        "order-biz-006",
        [
            {
                "businessOrderId": "M-1",
                "placedAt": iso(),
                "items": [{"itemName": "Bun", "qty": 1, "unitPrice": 5.0}],
            }
        ],
    )
    missing = client.post(
        "/v1/orders/NOPE/events?businessId=order-biz-006", json={"event": "fulfilled"}
    )
    assert missing.status_code == 404, "an event against an unknown order is a mistake"
    assert "Push the order first" in missing.json()["detail"]

    bad_event = client.post(
        "/v1/orders/M-1/events?businessId=order-biz-006", json={"event": "shipped"}
    )
    assert bad_event.status_code == 400
    assert "placed, fulfilled, cancelled, returned" in bad_event.json()["detail"]

    bad_item = client.post(
        "/v1/orders/M-1/events?businessId=order-biz-006",
        json={"event": "fulfilled", "itemId": "customer-9"},
    )
    assert bad_item.status_code == 400
    assert "customer-9" in bad_item.json()["detail"]
    assert (
        client.get("/v1/orders?businessId=order-biz-006").json()["orders"][0]["status"]
        == "placed"
    ), "a refused event must not have moved the order"


def test_the_window_is_honest_about_what_it_counts(client):
    push_orders(
        client,
        "order-biz-007",
        [
            {
                "businessOrderId": "W-1",
                "placedAt": iso(hours_ago=30),
                "status": "fulfilled",
                "items": [{"itemName": "Bun", "qty": 4, "unitPrice": 5.0}],
            },
            {
                "businessOrderId": "W-2",
                "placedAt": iso(hours_ago=1),
                "status": "fulfilled",
                "items": [{"itemName": "Bun", "qty": 1, "unitPrice": 5.0}],
            },
        ],
    )
    default = client.get("/v1/orders/sellthrough?businessId=order-biz-007").json()
    assert default["window"] == "24h"
    assert default["soldInWindow"] == 1, "the 30-hour-old order is outside a 24h window"

    a_day_and_a_bit = client.get(
        "/v1/orders/sellthrough?businessId=order-biz-007&window=48h"
    ).json()
    assert a_day_and_a_bit["soldInWindow"] == 5
    assert a_day_and_a_bit["velocity"] == pytest.approx(5 / 48, abs=1e-3)

    assert (
        client.get("/v1/orders/sellthrough?businessId=order-biz-007&window=soon").status_code
        == 400
    )


def test_orders_sellthrough_states_mixed_when_stock_is_present_too(client, monkeypatch):
    business_id = link_stock(client, monkeypatch)
    push_orders(
        client,
        business_id,
        [
            {
                "businessOrderId": "X-1",
                "placedAt": iso(),
                "status": "fulfilled",
                "items": [{"itemName": "Sourdough loaf", "qty": 3, "unitPrice": 40.0}],
            }
        ],
    )
    st = client.get(f"/v1/orders/sellthrough?businessId={business_id}").json()
    assert st["source"] == "mixed", "sold from orders, availability from the mapped stock"
    assert st["soldInWindow"] == 3
    assert st["unitsAvailable"] == 8, "10 capacity - 2 already sold on the linked shelf"

    # Scoped to one line, named the way the database names it: the starter kit's
    # table carries no per-line id, so the join is by the item's own name.
    per_line = client.get(
        f"/v1/orders/sellthrough?businessId={business_id}&itemId=Sourdough loaf"
    ).json()
    assert per_line["soldInWindow"] == 3
    assert per_line["unitsAvailable"] == 8
    assert per_line["source"] == "mixed"


def test_orders_sellthrough_without_orders_is_an_honest_404(client):
    resp = client.get("/v1/orders/sellthrough?businessId=no-orders-biz")
    assert resp.status_code == 404, resp.text
    assert "GET /v1/sell-through" in resp.json()["detail"], "point at the stock picture"


def test_the_stock_sellthrough_says_orders_are_counted_elsewhere(client, monkeypatch):
    """The two surfaces can disagree; they must never do it silently."""
    business_id = link_stock(client, monkeypatch, business_name="Order Biz Nine")
    push_orders(
        client,
        business_id,
        [
            {
                "businessOrderId": "Y-1",
                "placedAt": iso(),
                "status": "fulfilled",
                "items": [{"itemName": "Sourdough loaf", "qty": 3, "unitPrice": 40.0}],
            }
        ],
    )
    stock_side = client.get(f"/v1/sell-through?businessId={business_id}").json()
    assert stock_side["unitsSold"] == 2, "this surface counts the linked shelf only"
    assert stock_side["ordersStored"] == 1
    assert "orders" in stock_side["note"]
    assert "derived from stored stock batch" in stock_side["source"]

    # A business with no orders keeps the original honest source, no note.
    quiet_id = link_stock(client, monkeypatch, business_name="Order Biz Ten")
    no_orders = client.get(f"/v1/sell-through?businessId={quiet_id}").json()
    assert no_orders["ordersStored"] == 0
    assert no_orders["note"] is None


def test_linked_stock_joins_to_orders_by_the_name_the_database_carries(client, monkeypatch):
    """The starter kit's table has no line ids, and nothing pretends otherwise.

    So the mapped copy stores no business line id, and the join between a stock
    line and an order line falls back to the one thing both sides share: the
    item's own name. This test pins that fallback down, because an order joined
    to the wrong loaf would be a wrong number said with confidence.
    """
    business_id = link_stock(
        client,
        monkeypatch,
        business_name="Loaf Co",
        lines=[
            {
                "itemName": "Rye loaf",
                "category": "Bakery",
                "basePrice": 50.0,
                "totalCapacity": 6,
                "unitsSold": 1,
            },
            {
                "itemName": "Seeded loaf",
                "category": "Bakery",
                "basePrice": 55.0,
                "totalCapacity": 6,
                "unitsSold": 0,
            },
        ],
    )
    stock = client.get(f"/v1/stock?businessId={business_id}").json()
    by_name = {line["itemName"]: line for line in stock}
    assert by_name["Rye loaf"]["businessItemId"] is None

    push_orders(
        client,
        business_id,
        [
            {
                "businessOrderId": "R-1",
                "placedAt": iso(),
                "status": "fulfilled",
                "items": [{"itemName": "Rye loaf", "qty": 2, "unitPrice": 50.0}],
            }
        ],
    )
    per_line = client.get(
        f"/v1/orders/sellthrough?businessId={business_id}&itemId=Rye loaf"
    ).json()
    assert per_line["unitsAvailable"] == 5, "the rye's 6 - 1 sold, not the seeded loaf's 6"
    assert per_line["soldInWindow"] == 2

    other = client.get(
        f"/v1/orders/sellthrough?businessId={business_id}&itemId=Seeded loaf"
    ).json()
    assert other["unitsAvailable"] == 6
    assert other["soldInWindow"] == 0, "the rye's sale must not land on the seeded loaf"
    assert by_name["Seeded loaf"]["businessItemId"] is None, "absent stays absent"