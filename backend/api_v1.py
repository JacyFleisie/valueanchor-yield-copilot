"""Honest /v1 integration surface for a mobile app or backend to call.

The surface is honest in three ways that matter for "any business":

* **Per-business scoping is real, not implied.** A business identity is supplied
  on the push path and all reads are scoped to that owned store. A read for a
  business with no stock returns an honest empty result, not a guess.
* **The backend keeps a mapped copy.** Push writes into the backend's own store;
  the business keeps its source of truth. Reads reason off that copy.
* **The carve-outs are honoured, not papered over.** Floor is optional.
  Cost of goods is optional. The time signal is split (expiry timestamp OR
  closure-with-shelf-hours), and whichever one is absent is stated as absent.
  Sell-through is its own surface with its source stated.

Nothing here knows about the copilot, the NLU, the planner, or the language
assistant. It imports ``yield_engine`` for the honest maths so the numbers the
API returns can never disagree with the numbers the copilot computes.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from yield_engine import (
    item_metrics,
    load_perks,
    projected_unsold,
    profit_scenarios,
)

router = APIRouter(prefix="/v1", tags=["v1 integration"])


# ---------------------------------------------------------------------------
# Per-business identity for the honest owned store
# ---------------------------------------------------------------------------


class BusinessIdentity(BaseModel):
    """Who this stock belongs to. Supplied on push; all reads are scoped to it."""

    businessId: str = Field(..., min_length=1, max_length=128)
    businessName: str = Field(..., min_length=1, max_length=256)


# ---------------------------------------------------------------------------
# Push path (data in)
# ---------------------------------------------------------------------------


class StockLineIn(BaseModel):
    """One line of stock for the business.

    Floor and cost are optional. The time signal is split: a line either has an
    expiry timestamp **or** a closure-with-shelf-hours signal. If one is absent
    it is stated as absent in the stored record and in the read response.
    """

    itemName: str = Field(..., min_length=1, max_length=256)
    category: str = Field(..., min_length=1, max_length=256)
    # The business's own id for this line, when it has one. It is the only key
    # that can honestly join a pushed order line back to pushed stock, so it is
    # stored verbatim and never guessed from the name.
    businessItemId: str | None = Field(default=None, max_length=128)
    basePrice: float = Field(..., ge=0.0)
    totalCapacity: int = Field(..., ge=0)
    unitsSold: int = Field(..., ge=0)
    floorPrice: float | None = Field(default=None, ge=0.0)
    costOfGoods: float | None = Field(default=None, ge=0.0)
    # Either an ISO expiry timestamp, or the number of shelf hours until the
    # line is treated as closed. Exactly one of these should be supplied for an
    # honest time signal; if both are omitted, the line is stored with no time
    # signal and reads back as such.
    expirationTimestamp: str | None = Field(default=None)
    shelfHoursUntilClosed: float | None = Field(default=None)

    @field_validator("shelfHoursUntilClosed")
    @classmethod
    def exactly_one_time_signal(cls, v: float | None, info) -> float | None:
        if info.field_name == "shelfHoursUntilClosed":
            expiry = info.data.get("expirationTimestamp")
            if expiry and v is not None:
                raise ValueError(
                    "A line must have exactly one time signal: either expirationTimestamp "
                    "OR shelfHoursUntilClosed, not both."
                )
        return v


class StockBatchIn(BaseModel):
    business: BusinessIdentity
    lines: list[StockLineIn] = Field(..., min_length=1)


class StockBatchOut(BaseModel):
    business: BusinessIdentity
    accepted: int
    itemIds: list[int]
    storedAsOf: str


def _resolve_time_signal(line: StockLineIn) -> tuple[str | None, float | None, str]:
    """Return (expiry_timestamp, shelf_hours, timeSignal) stored for a line."""
    expiry = line.expirationTimestamp
    hours = line.shelfHoursUntilClosed
    if expiry and hours:
        raise ValueError(
            "A line must have exactly one time signal: either expirationTimestamp "
            "OR shelfHoursUntilClosed, not both."
        )
    if expiry:
        return expiry, None, "expiry"
    if hours is not None:
        # Closure-based lines get an expirationTimestamp derived from the
        # current time plus the stated shelf hours, so the existing yield maths
        # can still read a timestamp. The honest source of the time signal is
        # kept separately as "closure" so the response never pretends this was
        # an expiry date the business sent.
        now = datetime.now(timezone.utc)
        derived_dt = datetime.fromtimestamp(
            now.timestamp() + hours * 3600, tz=timezone.utc
        )
        derived = derived_dt.isoformat()
        return derived, hours, "closure"
    return None, None, "none"


def resolved_lines(batch: StockBatchIn) -> list[dict[str, Any]]:
    """Each line with its time signal already resolved, computed exactly once.

    Both the push path and the bridge into the copilot need the same resolved
    value, and resolving a closure line twice would produce two slightly different
    timestamps for the same line. Doing it once here is what keeps them equal.
    """
    resolved: list[dict[str, Any]] = []
    for line in batch.lines:
        expiry, shelf_hours, time_signal = _resolve_time_signal(line)
        resolved.append(
            {
                "itemName": line.itemName,
                "category": line.category,
                "businessItemId": line.businessItemId,
                "basePrice": line.basePrice,
                "totalCapacity": line.totalCapacity,
                "unitsSold": line.unitsSold,
                "floorPrice": line.floorPrice,
                "costOfGoods": line.costOfGoods,
                "expirationTimestamp": expiry,
                "shelfHoursUntilClosed": shelf_hours,
                "timeSignal": time_signal,
            }
        )
    return resolved


def upsert_business_stock(
    db: Any, batch: StockBatchIn, resolved: list[dict[str, Any]] | None = None
) -> StockBatchOut:
    """Write a business's mapped stock into the owned store.

    Honest rule: this keeps a mapped copy for the business, replacing any
    previous mapped copy for the same business. The business keeps its source of
    truth; this is what the API reasons off.
    """
    business_id = batch.business.businessId
    lines = resolved if resolved is not None else resolved_lines(batch)
    item_ids: list[int] = []
    now_iso = datetime.now(timezone.utc).isoformat()

    db.execute("DELETE FROM V1Stock WHERE BusinessID = ?", (business_id,))

    for line in lines:
        db.execute(
            """INSERT INTO V1Stock
               (BusinessID, BusinessName, ItemName, Category, BusinessItemId, BasePrice,
                TotalCapacity, UnitsSold, FloorPrice, CostOfGoods,
                ExpirationTimestamp, ShelfHoursUntilClosed, TimeSignal,
                StoredAsOf)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                business_id,
                batch.business.businessName,
                line["itemName"],
                line["category"],
                line["businessItemId"],
                line["basePrice"],
                line["totalCapacity"],
                line["unitsSold"],
                line["floorPrice"],
                line["costOfGoods"],
                line["expirationTimestamp"],
                line["shelfHoursUntilClosed"],
                line["timeSignal"],
                now_iso,
            ),
        )
        item_ids.append(int(db.execute("SELECT last_insert_rowid()").fetchone()[0]))

    db.commit()
    return StockBatchOut(
        business=batch.business,
        accepted=len(lines),
        itemIds=item_ids,
        storedAsOf=now_iso,
    )


# ---------------------------------------------------------------------------
# Stock in (data in) now arrives only through Link this database
#
# Every direct write path that used to live here is gone by design. There is no
# POST /v1/stock/batch, no spreadsheet/CSV on-ramp and no POST /v1/stock/sync
# any more: those were the three ways a caller could hand the product stock
# that was typed, pasted or uploaded rather than read from the system of record.
# The product now has exactly one way in - the read-only OpenEdge bridge behind
# "Link this database" - and it is read-only, so it cannot live on this router.
#
# What that deletes, honestly: the CSV parser and its sample sheet, the sync
# endpoint that bridged a push into the copilot, and the JSON batch endpoint.
# What it keeps: the read surfaces below, which are scoped to a business and
# return an honest empty list for one with no stock, and the shared line
# validation (StockLineIn, StockBatchIn, resolved_lines, upsert_business_stock)
# that the link path reuses so a linked shelf is stored exactly as a pushed one
# would have been.
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Read path (data out)
# ---------------------------------------------------------------------------


class V1StockLineOut(BaseModel):
    itemId: int
    itemName: str
    category: str
    # The business's own id for the line, when it supplied one; null otherwise,
    # because inventing an id here would break every round-trip back to the
    # business's system.
    businessItemId: str | None = None
    businessId: str
    businessName: str
    basePrice: float
    totalCapacity: int
    unitsSold: int
    unitsAvailable: int
    unitsLeft: int
    sellThroughShare: float
    floorPrice: float | None
    costOfGoods: float | None
    timeSignal: str
    expirationTimestamp: str | None
    shelfHoursUntilClosed: float | None
    # Honest metrics from yield_engine, present when a time signal exists.
    hoursRemaining: float | None = None
    velocityIndex: float | None = None
    urgencyScore: float | None = None
    riskStatus: str | None = None
    # Honest closing forecast, present when a time signal exists.
    projectedUnsold: dict[str, Any] | None = None


def _row_for_yield_engine(row: Any) -> dict[str, Any]:
    """Adapt a V1Stock row into the shape ``yield_engine`` expects.

    ``yield_engine`` was written for the existing Inventory rows, which have
    different columns (notably Sector and a non-None ExpirationTimestamp). This
    adapter maps a V1Stock row onto those columns honestly.

    For a line with no time signal (``TimeSignal == "none"``), the honest maths
    still need a parseable timestamp. The honest mapping is a far-future
    placeholder so hours remaining/urgency stay computable, while the response
    layer keeps the honest ``timeSignal: "none"`` and suppresses the projected
    forecast for that line.
    """
    expiration: str
    stored_expiration = row["ExpirationTimestamp"]
    if stored_expiration is None:
        expiration = "9999-12-31T23:59:59+00:00"
    else:
        expiration = stored_expiration
    return {
        "ItemID": row["ItemID"],
        "ItemName": row["ItemName"],
        "Category": row["Category"],
        "Sector": "custom",
        "BasePrice": row["BasePrice"],
        "ExpirationTimestamp": expiration,
        "TotalCapacity": row["TotalCapacity"],
        "UnitsSold": row["UnitsSold"],
    }


def _row_to_v1(row: Any) -> V1StockLineOut:
    unsold = row["TotalCapacity"] - row["UnitsSold"]
    share = round(row["UnitsSold"] / max(row["TotalCapacity"], 1), 3)
    time_signal = row["TimeSignal"]
    if time_signal != "none":
        yr = _row_for_yield_engine(row)
        metrics = item_metrics(yr)
        proj = projected_unsold(yr)
    else:
        metrics = {"velocityIndex": None, "urgencyScore": None, "hoursRemaining": None, "riskStatus": None}
        proj = None
    return V1StockLineOut(
        itemId=row["ItemID"],
        itemName=row["ItemName"],
        category=row["Category"],
        businessItemId=row["BusinessItemId"],
        businessId=row["BusinessID"],
        businessName=row["BusinessName"],
        basePrice=row["BasePrice"],
        totalCapacity=row["TotalCapacity"],
        unitsSold=row["UnitsSold"],
        unitsAvailable=unsold,
        unitsLeft=unsold,
        sellThroughShare=share,
        floorPrice=row["FloorPrice"],
        costOfGoods=row["CostOfGoods"],
        timeSignal=row["TimeSignal"],
        expirationTimestamp=row["ExpirationTimestamp"],
        shelfHoursUntilClosed=row["ShelfHoursUntilClosed"],
        hoursRemaining=metrics.get("hoursRemaining"),
        velocityIndex=metrics.get("velocityIndex"),
        urgencyScore=metrics.get("urgencyScore"),
        riskStatus=metrics.get("riskStatus"),
        projectedUnsold=proj,
    )


@router.get("/stock", response_model=list[V1StockLineOut])
def get_stock(businessId: str):
    """Read one business's mapped stock.

    Scoped to ``businessId``. An honest empty list is returned for a business
    with no stock loaded, not a guess at another business's stock.
    """
    rows = _db_for_read().execute(
        "SELECT * FROM V1Stock WHERE BusinessID = ? ORDER BY ItemID",
        (businessId,),
    ).fetchall()
    return [_row_to_v1(row) for row in rows]


@router.get("/stock/{itemId}", response_model=V1StockLineOut)
def get_stock_line(itemId: int, businessId: str):
    """Read one line, scoped to the business that owns it."""
    row = _db_for_read().execute(
        "SELECT * FROM V1Stock WHERE BusinessID = ? AND ItemID = ?",
        (businessId, itemId),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Stock line not found for this business")
    return _row_to_v1(row)


class SellThroughOut(BaseModel):
    businessId: str
    businessName: str
    totalCapacity: int
    unitsSold: int
    unitsAvailable: int
    sellThroughShare: float
    source: str
    asOf: str
    # Honest about the other surface: how many orders this business has pushed,
    # and a note saying they are counted elsewhere rather than hidden in here.
    ordersStored: int | None = None
    note: str | None = None


@router.get("/sell-through", response_model=SellThroughOut)
def get_sell_through(businessId: str):
    """Sell-through for one business, as its own surface with its source stated.

    Honest: this is derived from the stored mapped stock (``unitsSold`` /
    ``totalCapacity``), not from a live POS feed. The source field says so.
    A business with no stock returns a 404 rather than a fake 0% number.
    """
    db = _db_for_read()
    row = db.execute(
        "SELECT BusinessName, COALESCE(SUM(TotalCapacity), 0) AS totalCapacity, "
        "COALESCE(SUM(UnitsSold), 0) AS unitsSold "
        "FROM V1Stock WHERE BusinessID = ?",
        (businessId,),
    ).fetchone()
    if row is None or row["totalCapacity"] == 0:
        raise HTTPException(404, "No stock loaded for this business")
    unsold = row["totalCapacity"] - row["unitsSold"]
    share = round(row["unitsSold"] / max(row["totalCapacity"], 1), 3)
    now_iso = datetime.now(timezone.utc).isoformat()
    orders_stored = int(
        db.execute(
            "SELECT COUNT(*) FROM V1Orders WHERE BusinessID = ?", (businessId,)
        ).fetchone()[0]
    )
    note = (
        f"{orders_stored} orders are stored for this business and are NOT counted in this "
        "number: this surface counts the pushed stock batch (unitsSold / totalCapacity) "
        "only. GET /v1/orders/sellthrough counts the orders and states its own source."
        if orders_stored
        else None
    )
    return SellThroughOut(
        businessId=businessId,
        businessName=row["BusinessName"],
        totalCapacity=int(row["totalCapacity"]),
        unitsSold=int(row["unitsSold"]),
        unitsAvailable=unsold,
        sellThroughShare=share,
        source="derived from stored stock batch (unitsSold / totalCapacity)",
        asOf=now_iso,
        ordersStored=orders_stored,
        note=note,
    )


# ---------------------------------------------------------------------------
# Orders surface (real orders, and sell-through honestly derived from them)
#
# Stock says what is on the shelf; orders say what actually sold. The rule this
# surface exists to honour: a business that has orders pushes them here, and the
# sell-through it gets back states whether the number came from orders, from
# stock, or from both - never a number whose source is unstated.
#
# Two honest limits, stated in code rather than buried in a doc:
#   * an event moves the *whole order's* status. Partial line fulfilment is not
#     modelled, and the response says so instead of quietly counting a spread
#     order of three as fully sold.
#   * timestamps are normalised to UTC once, at ingest, so window comparisons
#     are text comparisons over one offset rather than per-request guessing.
# ---------------------------------------------------------------------------

ORDER_STATUSES = ("placed", "fulfilled", "cancelled", "returned")


class OrderLineIn(BaseModel):
    """One line of an order.

    Typed loosely on purpose: per-order problems (empty name, zero qty, a total
    that does not match the lines) come back as ``rejected`` entries naming the
    order, not as a 422 for the whole batch.
    """

    businessItemId: str | None = Field(default=None, max_length=128)
    itemName: str = ""
    qty: int = 1
    unitPrice: float = 0.0
    subtotal: float | None = None


class CustomerIn(BaseModel):
    id: str | None = None
    anonymous: bool | None = None


class OrderIn(BaseModel):
    """One order, as a business's backend would send it."""

    businessOrderId: str = ""
    placedAt: str = ""
    status: str = "placed"
    items: list[OrderLineIn] = Field(default_factory=list)
    total: float | None = None
    currency: str | None = None
    source: str | None = None
    customer: CustomerIn | None = None
    notes: str | None = None


class OrderBatchIn(BaseModel):
    business: BusinessIdentity
    orders: list[OrderIn] = Field(default_factory=list)


class AcceptedOrder(BaseModel):
    businessOrderId: str
    orderId: int
    # True when this ref was already stored and the batch replaced it, so a
    # sync can tell "new order" from "re-synced order" without counting rows.
    replaced: bool


class RejectedOrder(BaseModel):
    businessOrderId: str
    reason: str


class OrderBatchOut(BaseModel):
    business: BusinessIdentity
    accepted: list[AcceptedOrder]
    rejected: list[RejectedOrder]
    storedAsOf: str


def _parse_ts(raw: str) -> str:
    """Normalise a client timestamp to UTC ISO text, once, at ingest.

    Storage comparisons are string comparisons, which only hold when every
    stored timestamp carries the same offset. A timestamp sent without an
    offset is read as UTC - stated in the guide rather than guessed per call.
    """
    parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _validated_order(order: OrderIn) -> tuple[dict[str, Any] | None, str | None]:
    """Validate one pushed order.

    Returns ``(record, None)`` when it is honest to store, else ``(None, reason)``
    where the reason names the order and the exact problem. Nothing here is
    repaired silently: a bad total is refused, not overwritten with ours.
    """
    ref = (order.businessOrderId or "").strip()
    if not ref:
        return None, "businessOrderId is required and was empty."
    placed_raw = (order.placedAt or "").strip()
    if not placed_raw:
        return None, "placedAt is required and was empty."
    try:
        placed_at = _parse_ts(placed_raw)
    except ValueError:
        return None, f"placedAt must be an ISO date/time, got {placed_raw!r}."
    status = (order.status or "").strip().lower()
    if status not in ORDER_STATUSES:
        return None, (
            f"status must be one of {', '.join(ORDER_STATUSES)}, got {order.status!r}."
        )
    if not order.items:
        return None, "the order has no items."

    lines: list[dict[str, Any]] = []
    computed = 0.0
    for index, item in enumerate(order.items, start=1):
        name = (item.itemName or "").strip()
        if not name:
            return None, f"item {index}: itemName is required and was empty."
        if item.qty < 1:
            return None, f"item {index} ({name}): qty must be at least 1, got {item.qty}."
        if item.unitPrice < 0:
            return None, (
                f"item {index} ({name}): unitPrice cannot be negative, got {item.unitPrice}."
            )
        subtotal = round(item.qty * item.unitPrice, 2)
        if item.subtotal is not None and abs(item.subtotal - subtotal) > 0.01:
            return None, (
                f"item {index} ({name}): subtotal {item.subtotal} does not match "
                f"qty x unitPrice ({subtotal:.2f})."
            )
        computed += subtotal
        lines.append(
            {
                "businessItemId": (item.businessItemId or "").strip() or None,
                "itemName": name,
                "qty": item.qty,
                "unitPrice": item.unitPrice,
                "subtotal": subtotal,
            }
        )
    computed = round(computed, 2)
    if order.total is not None and abs(order.total - computed) > 0.01:
        return None, (
            f"total {order.total} does not match the sum of the lines ({computed:.2f})."
        )

    customer: dict[str, Any] | None = None
    if order.customer is not None and (order.customer.id or order.customer.anonymous):
        customer = {
            "id": (order.customer.id or "").strip() or None,
            "anonymous": bool(order.customer.anonymous),
        }
    return {
        "ref": ref,
        "placedAt": placed_at,
        "status": status,
        "lines": lines,
        "total": computed,
        "currency": (order.currency or "").strip() or None,
        "source": (order.source or "").strip() or None,
        "customer": customer,
        "notes": (order.notes or "").strip() or None,
    }, None


@router.post("/orders/batch", response_model=OrderBatchOut)
def post_orders_batch(payload: OrderBatchIn):
    """Push a business's orders into the owned mapped store.

    Accepted and rejected are reported separately, per the contract, so a sync
    can store the honest rows and fix the bad ones instead of losing the whole
    batch to one bad row. Re-pushing a ``businessOrderId`` already stored
    replaces that order (idempotent by design), and orders are scoped per
    business, so one business's push can never touch another's.
    """
    if not payload.orders:
        raise HTTPException(400, "Supply at least one order.")

    business = payload.business
    business_id = business.businessId
    db = _db_for_push()
    now_iso = datetime.now(timezone.utc).isoformat()
    accepted: list[AcceptedOrder] = []
    rejected: list[RejectedOrder] = []
    seen: set[str] = set()

    for order in payload.orders:
        raw_ref = (order.businessOrderId or "").strip()
        if raw_ref and raw_ref in seen:
            rejected.append(
                RejectedOrder(
                    businessOrderId=raw_ref,
                    reason="businessOrderId appears more than once in this batch; only the first copy was stored.",
                )
            )
            continue

        record, reason = _validated_order(order)
        if reason is not None:
            rejected.append(RejectedOrder(businessOrderId=raw_ref, reason=reason))
            continue
        assert record is not None
        seen.add(record["ref"])

        existing = db.execute(
            "SELECT OrderID FROM V1Orders WHERE BusinessID = ? AND BusinessOrderRef = ?",
            (business_id, record["ref"]),
        ).fetchone()
        if existing:
            order_id = int(existing["OrderID"])
            replaced = True
            db.execute("DELETE FROM V1OrderLines WHERE OrderID = ?", (order_id,))
            db.execute(
                """UPDATE V1Orders SET BusinessName = ?, PlacedAt = ?, Status = ?,
                   StatusAt = ?, Currency = ?, Source = ?, CustomerID = ?,
                   CustomerAnonymous = ?, Total = ?, Notes = ?, StoredAsOf = ?
                   WHERE OrderID = ?""",
                (
                    business.businessName,
                    record["placedAt"],
                    record["status"],
                    record["placedAt"],
                    record["currency"],
                    record["source"],
                    record["customer"] and record["customer"]["id"],
                    (
                        int(record["customer"]["anonymous"])
                        if record["customer"]
                        else None
                    ),
                    record["total"],
                    record["notes"],
                    now_iso,
                    order_id,
                ),
            )
        else:
            replaced = False
            db.execute(
                """INSERT INTO V1Orders
                   (BusinessID, BusinessName, BusinessOrderRef, PlacedAt, Status,
                    StatusAt, Currency, Source, CustomerID, CustomerAnonymous,
                    Total, Notes, StoredAsOf)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    business_id,
                    business.businessName,
                    record["ref"],
                    record["placedAt"],
                    record["status"],
                    record["placedAt"],
                    record["currency"],
                    record["source"],
                    record["customer"] and record["customer"]["id"],
                    (
                        int(record["customer"]["anonymous"])
                        if record["customer"]
                        else None
                    ),
                    record["total"],
                    record["notes"],
                    now_iso,
                ),
            )
            order_id = int(db.execute("SELECT last_insert_rowid()").fetchone()[0])

        db.executemany(
            """INSERT INTO V1OrderLines
               (OrderID, BusinessItemId, ItemName, Qty, UnitPrice, Subtotal)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [
                (
                    order_id,
                    line["businessItemId"],
                    line["itemName"],
                    line["qty"],
                    line["unitPrice"],
                    line["subtotal"],
                )
                for line in record["lines"]
            ],
        )
        accepted.append(
            AcceptedOrder(
                businessOrderId=record["ref"], orderId=order_id, replaced=replaced
            )
        )

    db.commit()
    return OrderBatchOut(
        business=business,
        accepted=accepted,
        rejected=rejected,
        storedAsOf=now_iso,
    )


class OrderEventIn(BaseModel):
    """One event against a stored order.

    ``itemId`` and ``qty`` are recorded on the event so the log can carry the
    line an event was raised against; an event still moves the whole order's
    status, because partial line fulfilment is not modelled here and the
    response says so.
    """

    event: str
    at: str | None = None
    itemId: str | None = None
    qty: int | None = None
    note: str | None = None


class OrderEventOut(BaseModel):
    businessOrderId: str
    orderId: int
    status: str
    previousStatus: str
    eventAt: str
    countedAsSold: bool
    storedAsOf: str
    note: str


@router.post("/orders/{businessOrderId}/events", response_model=OrderEventOut)
def post_order_event(businessOrderId: str, businessId: str, payload: OrderEventIn):
    """Record an event against one of this business's stored orders.

    Honest rules, each refused with a specific status rather than absorbed:

    * the order must exist for this business, else 404 - an event against an
      order the product does not hold is a mistake, not something to invent.
    * the event must be one of placed/fulfilled/cancelled/returned, else 400.
    * only ``fulfilled`` counts as sold. A later ``returned`` event makes the
      same order stop counting, which is why this is the live path rather than
      re-pushing stock.
    """
    event = (payload.event or "").strip().lower()
    if event not in ORDER_STATUSES:
        raise HTTPException(
            400,
            f"event must be one of {', '.join(ORDER_STATUSES)}, got {payload.event!r}.",
        )
    try:
        at = _parse_ts(payload.at) if payload.at else datetime.now(timezone.utc).isoformat()
    except ValueError:
        raise HTTPException(400, f"at must be an ISO date/time, got {payload.at!r}.")
    if payload.qty is not None and payload.qty < 1:
        raise HTTPException(400, f"qty must be at least 1, got {payload.qty}.")

    db = _db_for_push()
    order = db.execute(
        "SELECT * FROM V1Orders WHERE BusinessID = ? AND BusinessOrderRef = ?",
        (businessId, businessOrderId),
    ).fetchone()
    if order is None:
        raise HTTPException(
            404,
            f"No order with reference {businessOrderId!r} is stored for this business. "
            "Push the order first, then raise events against it.",
        )

    if payload.itemId:
        ref = payload.itemId.strip()
        line = db.execute(
            "SELECT 1 FROM V1OrderLines WHERE OrderID = ? AND (BusinessItemId = ? OR ItemName = ?) LIMIT 1",
            (order["OrderID"], ref, ref),
        ).fetchone()
        if line is None:
            raise HTTPException(
                400,
                f"This order has no line whose businessItemId or itemName is {ref!r}.",
            )

    previous = order["Status"]
    now_iso = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE V1Orders SET Status = ?, StatusAt = ? WHERE OrderID = ?",
        (event, at, order["OrderID"]),
    )
    db.execute(
        """INSERT INTO V1OrderEvents
           (OrderID, BusinessID, Status, At, ItemRef, Qty, Note, StoredAsOf)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            order["OrderID"],
            businessId,
            event,
            at,
            payload.itemId,
            payload.qty,
            payload.note,
            now_iso,
        ),
    )
    db.commit()

    counted = event == "fulfilled"
    note = (
        "This event set the whole order's status: only 'fulfilled' counts as sold, and "
        "'cancelled' or 'returned' stops an order counting again. Partial line fulfilment "
        "is not modelled, so this event is not a per-item claim."
    )
    return OrderEventOut(
        businessOrderId=order["BusinessOrderRef"],
        orderId=int(order["OrderID"]),
        status=event,
        previousStatus=previous,
        eventAt=at,
        countedAsSold=counted,
        storedAsOf=now_iso,
        note=note,
    )


class CustomerOut(BaseModel):
    id: str | None = None
    anonymous: bool | None = None


class OrderLineOut(BaseModel):
    businessItemId: str | None = None
    itemName: str
    qty: int
    unitPrice: float
    subtotal: float


class OrderOut(BaseModel):
    id: int
    businessOrderId: str
    placedAt: str
    status: str
    statusAt: str | None = None
    items: list[OrderLineOut]
    total: float
    currency: str | None = None
    source: str | None = None
    customer: CustomerOut | None = None
    notes: str | None = None
    storedAsOf: str


class OrdersOut(BaseModel):
    businessId: str
    orders: list[OrderOut]
    asOf: str
    note: str | None = None


def _order_to_out(db: Any, row: Any) -> OrderOut:
    lines = db.execute(
        "SELECT * FROM V1OrderLines WHERE OrderID = ? ORDER BY OrderLineID",
        (row["OrderID"],),
    ).fetchall()
    customer = None
    if row["CustomerID"] is not None:
        customer = CustomerOut(
            id=row["CustomerID"],
            anonymous=True if row["CustomerAnonymous"] else None,
        )
    elif row["CustomerAnonymous"]:
        customer = CustomerOut(anonymous=True)
    return OrderOut(
        id=int(row["OrderID"]),
        businessOrderId=row["BusinessOrderRef"],
        placedAt=row["PlacedAt"],
        status=row["Status"],
        statusAt=row["StatusAt"],
        items=[
            OrderLineOut(
                businessItemId=line["BusinessItemId"],
                itemName=line["ItemName"],
                qty=int(line["Qty"]),
                unitPrice=float(line["UnitPrice"]),
                subtotal=float(line["Subtotal"]),
            )
            for line in lines
        ],
        total=float(row["Total"]),
        currency=row["Currency"],
        source=row["Source"],
        customer=customer,
        notes=row["Notes"],
        storedAsOf=row["StoredAsOf"],
    )


@router.get("/orders", response_model=OrdersOut)
def get_orders(
    businessId: str,
    status: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    itemId: str | None = None,
):
    """Read one business's stored orders, optionally filtered.

    Scoped to ``businessId``: another business's orders never appear here, and a
    business with no orders gets an honest empty list plus a note saying which
    is the case - never another business's rows or an invented one.
    """
    clauses = ["o.BusinessID = ?"]
    params: list[Any] = [businessId]
    if status is not None:
        status = status.strip().lower()
        if status not in ORDER_STATUSES:
            raise HTTPException(
                400,
                f"status must be one of {', '.join(ORDER_STATUSES)}, got {status!r}.",
            )
        clauses.append("o.Status = ?")
        params.append(status)
    for raw, column, label in ((from_, "o.PlacedAt >= ?", "from"), (to, "o.PlacedAt <= ?", "to")):
        if raw:
            try:
                params.append(_parse_ts(raw))
            except ValueError:
                raise HTTPException(400, f"{label} must be an ISO date/time, got {raw!r}.")
            clauses.append(column)
    if itemId:
        ref = itemId.strip()
        clauses.append(
            "EXISTS (SELECT 1 FROM V1OrderLines l WHERE l.OrderID = o.OrderID "
            "AND (l.BusinessItemId = ? OR l.ItemName = ?))"
        )
        params.extend([ref, ref])

    db = _db_for_read()
    rows = db.execute(
        "SELECT o.* FROM V1Orders o WHERE "
        + " AND ".join(clauses)
        + " ORDER BY o.PlacedAt DESC, o.OrderID DESC",
        params,
    ).fetchall()
    filtered = bool(status or from_ or to or itemId)
    note = (
        "No stored order matches this filter."
        if (rows == [] and filtered)
        else "No orders are stored for this business yet." if rows == [] else None
    )
    return OrdersOut(
        businessId=businessId,
        orders=[_order_to_out(db, row) for row in rows],
        asOf=datetime.now(timezone.utc).isoformat(),
        note=note,
    )


class OrdersSellThroughOut(BaseModel):
    businessId: str
    itemId: str | None = None
    window: str
    windowHours: float
    soldInWindow: int
    pendingUnits: int
    # Null, not zero: without mapped stock the remaining capacity is unknown,
    # and a guessed zero would read as "sold out".
    unitsAvailable: int | None = None
    velocity: float
    velocityUnit: str
    lastSaleAt: str | None = None
    ordersCounted: int
    source: str
    asOf: str
    note: str


def _parse_window(raw: str) -> tuple[str, float]:
    """Parse a window like ``90m``, ``24h`` or ``7d`` into (label, hours)."""
    value = (raw or "").strip().lower()
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(s|m|h|d)", value)
    if not match:
        raise HTTPException(
            400,
            f"window must look like 90m, 24h or 7d, got {raw!r}.",
        )
    amount = float(match.group(1))
    hours = amount * {"s": 1 / 3600, "m": 1 / 60, "h": 1.0, "d": 24.0}[match.group(2)]
    if hours <= 0:
        raise HTTPException(400, f"window must be greater than zero, got {raw!r}.")
    return value, hours


@router.get("/orders/sellthrough", response_model=OrdersSellThroughOut)
def get_orders_sellthrough(
    businessId: str,
    window: str = "24h",
    itemId: str | None = None,
):
    """"How fast this business's lines are moving, from its real orders.

    The source of every figure is stated, per the contract:

    * units sold are counted here from orders whose status is ``fulfilled`` and
      which were **placed** inside the window (counted on placedAt, said so in
      the note), so nothing is attributed to a sprint it did not happen in.
    * units still available come from the mapped stock when this business has
      stock - business-wide, or the one line ``itemId`` names.
    * ``source`` is therefore ``mixed`` when stock contributes the availability
      and ``orders`` when orders are the only thing this business has pushed.
    * a business with no orders gets an honest 404 pointing at the stock-derived
      surface rather than a 0% velocity that would mean "never sold anything".
    """
    label, hours = _parse_window(window)
    db = _db_for_read()

    order_count = int(
        db.execute(
            "SELECT COUNT(*) FROM V1Orders WHERE BusinessID = ?", (businessId,)
        ).fetchone()[0]
    )
    if order_count == 0:
        raise HTTPException(
            404,
            "No orders are stored for this business. Push orders first, or use "
            "GET /v1/sell-through for the stock-derived picture.",
        )

    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    line_filter = ""
    line_params: list[Any] = []
    if itemId:
        ref = itemId.strip()
        line_filter = " AND (l.BusinessItemId = ? OR l.ItemName = ?)"
        line_params = [ref, ref]

    sold_row = db.execute(
        "SELECT COALESCE(SUM(l.Qty), 0) AS units, COUNT(DISTINCT o.OrderID) AS orders_, "
        "MAX(o.PlacedAt) AS last_at "
        "FROM V1OrderLines l JOIN V1Orders o ON o.OrderID = l.OrderID "
        "WHERE o.BusinessID = ? AND o.PlacedAt >= ? AND o.Status = 'fulfilled'"
        + line_filter,
        [businessId, cutoff, *line_params],
    ).fetchone()
    pending_units = int(
        db.execute(
            "SELECT COALESCE(SUM(l.Qty), 0) FROM V1OrderLines l "
            "JOIN V1Orders o ON o.OrderID = l.OrderID "
            "WHERE o.BusinessID = ? AND o.PlacedAt >= ? AND o.Status = 'placed'"
            + line_filter,
            [businessId, cutoff, *line_params],
        ).fetchone()[0]
    )
    sold = int(sold_row["units"])

    # Availability from the mapped stock, if this business has any. Joined on
    # the business's own item id first, then the name, and never guessed.
    units_available: int | None = None
    stock_rows = 0
    if itemId:
        ref = itemId.strip()
        line = db.execute(
            "SELECT * FROM V1Stock WHERE BusinessID = ? AND BusinessItemId = ? LIMIT 1",
            (businessId, ref),
        ).fetchone()
        if line is None:
            line = db.execute(
                "SELECT * FROM V1Stock WHERE BusinessID = ? AND ItemName = ? LIMIT 1",
                (businessId, ref),
            ).fetchone()
        if line is not None:
            stock_rows = 1
            units_available = int(line["TotalCapacity"] - line["UnitsSold"])
    else:
        stock_count = int(
            db.execute(
                "SELECT COUNT(*) FROM V1Stock WHERE BusinessID = ?", (businessId,)
            ).fetchone()[0]
        )
        stock_rows = stock_count
        if stock_count:
            units_available = int(
                db.execute(
                    "SELECT COALESCE(SUM(TotalCapacity - UnitsSold), 0) FROM V1Stock "
                    "WHERE BusinessID = ?",
                    (businessId,),
                ).fetchone()[0]
            )

    source = "mixed" if stock_rows else "orders"
    note_parts = [
        f"Counted from stored orders placed in the last {label} whose status is "
        "'fulfilled', on placedAt rather than fulfilment time.",
    ]
    if pending_units:
        note_parts.append(
            f"{pending_units} units are placed but not yet fulfilled, so they are not counted as sold."
        )
    note_parts.append(
        "Availability comes from the mapped stock batch."
        if units_available is not None
        else "This business has no mapped stock, so unitsAvailable is unknown rather than guessed."
    )
    if itemId:
        note_parts.append(
            f"Filtered to the line whose businessItemId or itemName is {itemId.strip()!r}."
        )

    now_iso = datetime.now(timezone.utc).isoformat()
    return OrdersSellThroughOut(
        businessId=businessId,
        itemId=itemId.strip() if itemId else None,
        window=label,
        windowHours=hours,
        soldInWindow=sold,
        pendingUnits=pending_units,
        unitsAvailable=units_available,
        velocity=round(sold / hours, 3),
        velocityUnit="units per hour",
        lastSaleAt=sold_row["last_at"],
        ordersCounted=int(sold_row["orders_"]),
        source=source,
        asOf=now_iso,
        note=" ".join(note_parts),
    )


class AnalyzeSummaryOut(BaseModel):
    businessId: str
    businessName: str
    totalLines: int
    totalCapacity: int
    unitsSold: int
    unitsAvailable: int
    sellThroughShare: float
    exposureAtShelfPrice: float
    criticalLines: int
    watchLines: int
    healthyLines: int
    source: str
    asOf: str


@router.get("/analyze", response_model=AnalyzeSummaryOut)
def get_analyze(businessId: str):
    """Honest day summary off the mapped store for one business.

    Reads the mapped copy, not a live source. Sell-through is stated with its
    source. Risk counts come from ``yield_engine.item_metrics``.
    """
    db = _db_for_read()
    rows = db.execute(
        "SELECT * FROM V1Stock WHERE BusinessID = ?",
        (businessId,),
    ).fetchall()
    if not rows:
        raise HTTPException(404, "No stock loaded for this business")

    total_capacity = sum(r["TotalCapacity"] for r in rows)
    units_sold = sum(r["UnitsSold"] for r in rows)
    unsold = total_capacity - units_sold
    exposure = sum((r["TotalCapacity"] - r["UnitsSold"]) * r["BasePrice"] for r in rows)
    share = round(units_sold / max(total_capacity, 1), 3)

    critical = watch = healthy = 0
    for r in rows:
        m = item_metrics(_row_for_yield_engine(r))
        if m["riskStatus"] == "critical":
            critical += 1
        elif m["riskStatus"] == "watch":
            watch += 1
        else:
            healthy += 1

    now_iso = datetime.now(timezone.utc).isoformat()
    first = rows[0]
    return AnalyzeSummaryOut(
        businessId=businessId,
        businessName=first["BusinessName"],
        totalLines=len(rows),
        totalCapacity=total_capacity,
        unitsSold=units_sold,
        unitsAvailable=unsold,
        sellThroughShare=share,
        exposureAtShelfPrice=round(exposure, 2),
        criticalLines=critical,
        watchLines=watch,
        healthyLines=healthy,
        source="derived from stored stock batch",
        asOf=now_iso,
    )


class AnalyzeLineOut(BaseModel):
    item: V1StockLineOut
    floor: dict[str, Any]
    scenarios: list[dict[str, Any]]
    recommended: str
    breakeven: dict[str, Any] | None
    deferred: dict[str, Any]
    notModelled: str
    source: str


@router.get("/analyze/{itemId}", response_model=AnalyzeLineOut)
def get_analyze_line(itemId: int, businessId: str):
    """Honest profit scenarios for one line, from ``yield_engine.profit_scenarios``.

    Honest carve-outs: if the line has no shelf price the endpoint refuses; if
    the business has no perks loaded, the scenarios still run but the bundle
    option is honest about having no perks to fund. Floor and cost of goods are
    stated as present/absent in the line; this endpoint does not invent them.
    """
    row = _db_for_read().execute(
        "SELECT * FROM V1Stock WHERE BusinessID = ? AND ItemID = ?",
        (businessId, itemId),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Stock line not found for this business")

    perks = load_perks(_db_for_read())
    profile = _profile_for_profit(row)
    yr = _row_for_yield_engine(row)
    scenarios_body = profit_scenarios(yr, perks, profile)
    return AnalyzeLineOut(
        item=_row_to_v1(row),
        floor={
            "percent": profile["marginFloorPct"],
            "perUnit": round(float(row["BasePrice"]) * profile["marginFloorPct"] / 100, 2),
            "floorPriceKnown": row["FloorPrice"] is not None,
            "floorPrice": row["FloorPrice"],
        },
        scenarios=scenarios_body["scenarios"],
        recommended=scenarios_body["recommended"],
        breakeven=scenarios_body["breakeven"],
        deferred=scenarios_body["deferred"],
        notModelled=scenarios_body["notModelled"],
        source="computed from stored stock batch and stored perks",
    )


# ---------------------------------------------------------------------------
# Honest store wiring
# ---------------------------------------------------------------------------


def _ensure_v1_store(db: Any) -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS V1Stock (
            ItemID INTEGER PRIMARY KEY AUTOINCREMENT,
            BusinessID TEXT NOT NULL,
            BusinessName TEXT NOT NULL,
            ItemName TEXT NOT NULL,
            Category TEXT NOT NULL,
            BasePrice REAL NOT NULL,
            TotalCapacity INTEGER NOT NULL,
            UnitsSold INTEGER NOT NULL,
            FloorPrice REAL,
            CostOfGoods REAL,
            ExpirationTimestamp TEXT,
            ShelfHoursUntilClosed REAL,
            TimeSignal TEXT NOT NULL DEFAULT 'none',
            StoredAsOf TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS V1Stock_BusinessID ON V1Stock(BusinessID);

        -- The orders surface: stock says what is on the shelf, orders say what
        -- actually sold. Status is per order, moved only by an explicit event or
        -- by the business re-pushing the order; nothing here decrements stock.
        CREATE TABLE IF NOT EXISTS V1Orders (
            OrderID INTEGER PRIMARY KEY AUTOINCREMENT,
            BusinessID TEXT NOT NULL,
            BusinessName TEXT NOT NULL,
            BusinessOrderRef TEXT NOT NULL,
            PlacedAt TEXT NOT NULL,
            Status TEXT NOT NULL DEFAULT 'placed',
            StatusAt TEXT,
            Currency TEXT,
            Source TEXT,
            CustomerID TEXT,
            CustomerAnonymous INTEGER,
            Total REAL NOT NULL,
            Notes TEXT,
            StoredAsOf TEXT NOT NULL,
            UNIQUE (BusinessID, BusinessOrderRef)
        );
        CREATE INDEX IF NOT EXISTS V1Orders_BusinessID ON V1Orders(BusinessID);

        CREATE TABLE IF NOT EXISTS V1OrderLines (
            OrderLineID INTEGER PRIMARY KEY AUTOINCREMENT,
            OrderID INTEGER NOT NULL,
            BusinessItemId TEXT,
            ItemName TEXT NOT NULL,
            Qty INTEGER NOT NULL,
            UnitPrice REAL NOT NULL,
            Subtotal REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS V1OrderLines_OrderID ON V1OrderLines(OrderID);

        CREATE TABLE IF NOT EXISTS V1OrderEvents (
            EventID INTEGER PRIMARY KEY AUTOINCREMENT,
            OrderID INTEGER NOT NULL,
            BusinessID TEXT NOT NULL,
            Status TEXT NOT NULL,
            At TEXT NOT NULL,
            ItemRef TEXT,
            Qty INTEGER,
            Note TEXT,
            StoredAsOf TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS V1OrderEvents_OrderID ON V1OrderEvents(OrderID);
        """
    )
    # Migration for databases created before the orders surface existed: the
    # business's own line id is the key that joins an order line back to pushed
    # stock, so the column has to exist even on an old install. sqlite has no
    # IF NOT EXISTS for columns, hence the guarded ALTER.
    try:
        db.execute("ALTER TABLE V1Stock ADD COLUMN BusinessItemId TEXT")
    except sqlite3.OperationalError:
        pass  # "duplicate column name" - it is already there from a prior run.


def _db_for_push() -> Any:
    """The push path uses a writable connection and ensures the honest store exists."""
    db = _connect()
    _ensure_v1_store(db)
    return db


def _db_for_read() -> Any:
    """Reads use a plain connection; the store is ensured at app startup."""
    return _connect()


def _connect() -> Any:
    import app as app_module

    db = app_module.connect()
    db.row_factory = sqlite3.Row
    return db


def _profile_for_profit(row: Any) -> dict[str, Any]:
    """A minimal honest profile for the profit maths, built from the stored line.

    The profit maths needs a floor percentage and a currency symbol. The honest
    slice does not require the business to have configured the full copilot
    profile; it uses the stored floor when present, otherwise a sensible default
    stated honestly. Currency is defaulted honestly when unknown.
    """
    import app as app_module

    db = _connect()
    try:
        profile_row = db.execute("SELECT * FROM Profile WHERE ProfileID = 1").fetchone()
    finally:
        db.close()

    default_floor = 60
    if profile_row is not None:
        try:
            default_floor = int(profile_row["SectorSlug"] and 60 or 60)
        except Exception:
            default_floor = 60

    floor_pct = 60 if row["FloorPrice"] is None else default_floor
    return {
        "marginFloorPct": floor_pct,
        "currencyCode": "USD",
        "currencySymbol": "$",
        "businessName": row["BusinessName"],
        "sector": "custom",
    }


import sqlite3  # noqa: E402, late import to mirror app.py style
