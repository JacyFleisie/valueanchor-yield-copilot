# ValueAnchor /v1 integration contract

> **Status: contract, not implemented.** This document is the honest shape of the public surface a business app or backend can call, written so someone could start building the `/v1` layer from it. Nothing in it is shipped as runtime code yet.

## 1. The contract in one paragraph

ValueAnchor's reasoning lives in one place (the product), but the business that uses it keeps its own source of truth (POS, database, booking system, spreadsheet, or whatever it actually has). The integration surface is the contract between those two: the business's system can **push stock and orders** into ValueAnchor's mapped store, your product can **pull** from the business's system through a connector if it's easier for the business, and the business's own app or backend can call ValueAnchor for recommendations and log the decisions back into a **shared decision log**.

That's the whole thing, honestly. The product does not become the business's POS or database; it sees their stock/prices/orders, reasons over a mapped copy of them, and returns a recommendation and a decision log your software can use.

## 2. Base URL, version, and scope

```
https://{host}/v1/{resource}
```

- `/v1/` is part of the path, because a business backend that stores a recommendation shape needs a stable contract it can pin to. A later `/v2/` means the shape or meaning changed in a way an integrator would notice.
- Every business-facing call is scoped by `businessId`:

```
GET  /v1/stock?businessId=acme
POST /v1/strategy/decide  { ..., businessId: "acme" }
```

- An integration key can be **bound to one business** (the key's own business is assumed, so the `businessId` param becomes redundant for that key), or **bound to several businesses** (a single integrator key touches many customers, each scoped by its own `businessId`). Both are allowed. The path-param shape is easier to document and to reuse in a multi-tenant business app, so it starts there and the key-bound shortcut is a convenience.

### Ownership model (written into the contract because it decides what can be mutated)

- **The business keeps its own source of truth.** ValueAnchor never pretends to own the POS, database, booking system, spreadsheet, or whatever it is.
- **ValueAnchor keeps an owned mapped store** — a copy, under its own schema, updated by push or by connected pull, per business, per the mapping below. Reasoning runs off that copy.
- **The decision log is shared.** A recommendation is made; the business's app or backend records what was actually done; both sides can read the same log. That's the part that makes it an integration rather than a separate chat box.

## 3. Data paths

Three ways a business's data gets into the mapped store. The contract supports all three; which one a business uses is the per-business integration work.

### Push (cleanest consent, least reach)

The business's system POSTs stock and orders into the product's store:

- `POST /v1/stock/batch` — push item records.
- `POST /v1/orders/batch` — push orders.
- `POST /v1/orders/{id}/events` — push an order event (placed/fulfilled/cancelled/returned) so sell-through stays current without re-pushing everything.

Best when the business has a system that can call yours. Lowest trust friction; no access to their internals required.

### Spreadsheet / CSV on-ramp (the easy landing)

A business drops a spreadsheet or CSV (stock, orders, or both) and the product reads and maps it. This is the path that lands the businesses that cannot push a system call — the biggest number of small businesses, and the lowest-friction way in. Keep it as a real first class in the contract, not a hack.

### Connector pull (more reach, more work)

ValueAnchor reads from the business's system through a connector: a DB read you're consented to, a POS/booking API access they hand over, a shared sheet you can read, a CSV they drop. More reach than push, but you now own a connector per type and a consent/token flow. Add later for the businesses that have them and want you to read.

### Read path (what the business's app calls)

The business's own app or backend calls the read surfaces (stock/compact, sell-through, recommends, decisions) to render its own UI, make its own calls, or log its decisions. That's the machine-to-machine half.

## 4. Honest carve-outs written into the contract

These are the things that would be wrong to pretend. They're written here as part of the contract, not as footnotes hidden later.

### Floor is optional everywhere

Some businesses have an explicit minimum margin or minimum price; some don't. The contract allows `floor` to be absent, and states an honest note rather than silence when it is. A business that has a floor can hand it over; a business that doesn't is not forced to invent one.

### Cost is optional everywhere

Profit in this surface means **price minus perk cost** unless the business hands you **cost of goods**. If cost is absent, profit is not true economic profit and the contract says so. A business that has cost should be able to hand it over; a business that doesn't is not forced to pretend it has it.

### Time signal is split, not assumed

Not every business is a per-item expiry business. The contract splits time signal into:

- `expiry` — per-item perishable stock.
- `closure` — something that lapses at a closing time (a counter batch, a room type on a given day, a shift's unsold capacity).
- `appointment` — a booked/set appointment/service window.
- `none` — the business has no per-item time signal at all.
- `unknown` — not yet told.

That split is what makes "any business that sells stock or vested capacity before it lapses" honest instead of a coffee shop pretending to be universal.

### Sell-through is its own thing, with its source stated

Sell-through is not the same thing as orders, and orders are not the same thing as stock. The contract gives:

- **Orders** — real customer orders, where the business has them.
- **Sell-through** — derived from orders if the business has them, derived from stock movement if that's all it has, pushed directly if it prefers.

The sell-through endpoint states honestly which source it used (`orders` / `stock` / `mixed`). That keeps "track orders and everything" honest: orders are real where the business has them; sell-through is always present as a concept, but it may come from orders, stock, a mix, or be weak/absent, and the contract says which.

### Per-business mapping is the real work

The API contract is the same for every business, but each business maps its items, categories, prices, floors, orders, and time signals onto the product's schema once. That is the thing that makes the surface actually work for a new business. It is not a generic plug-in-to-raw-tables thing; it is a generic contract with per-business mapping.

## 5. Stock surface

### GET /v1/stock

Mapped stock for a business.

```
GET /v1/stock?businessId=acme&sector=coffee&updatedAfter=2026-10-14T09:00Z
```

```
{
  businessId,
  items: [{
    id,                                # ValueAnchor's own id for the line
    businessItemId,                    # the business's own id, for round-trips
    name,
    category,
    price,                             # customer-facing price
    currency,
    cost?,                             # optional; absent == "profit == price - perk cost" stated honestly
    floor?,                            # optional numeric; absent == "no floor in the business's rules"
    floorKind: "percent" | "fixed" | "absent",
    floorNote?,                        # honest note when absent, not silence
    capacity,                          # units on hand / seats / rooms / slots
    sold?,                             # units sold (if known; may be derived from orders)
    timeLeft?,                         # expiry/closure/appointment remaining, in hours
    timeSignal: "expiry" | "closure" | "appointment" | "none" | "unknown",
    lastUpdated,
  }],
  asOf,
  note?,                               # honest note when part of the picture is unknown
}
```

A note in the shape: `sold` can come from orders (derived), be pushed directly, or be absent. The shape allows all three. That's the point: a business with orders can leave `sold` blank and let the product derive it; a business with a stock table can push it directly; a business with neither has a weak or absent sell-through, and the reasoning marks it honestly.

### GET /v1/stock/{id}

Full single-line record. Used when the business app wants a detail view for one line, or when reasoning needs the full record for one line.

### POST /v1/stock/batch

Push stock into the product's mapped store. Returns which lines were accepted and which were rejected (and why), so the business's sync can retry or fix.

```
POST /v1/stock/batch
  { items: [ { businessItemId, name, category, price, currency, cost?, floor?, floorKind?, capacity, sold?, timeLeft?, timeSignal? } ] }

-> { accepted: [...], rejected: [{ businessItemId, reason }], asOf }
```

Idempotency key on the body is optional but useful for a sync that retries: a retry shouldn't double-count.

### GET /v1/stock/compact

A cheap shape for a business app to render its own list without fetching full records.

```
GET /v1/stock/compact?businessId=acme
```

```
{
  businessId,
  items: [{
    id, businessItemId, name, category,
    price, currency,
    capacity, sold,
    timeLeft, timeSignal,
    risk: "healthy" | "watch" | "critical" | "unknown",
    floorOk: true | false | "unknown",
  }],
  asOf,
}
```

This is the bit a business app actually uses to show *"here's the shelf"* — enough to render a list, not enough to reason full-about-it. The reasoning surface is separate.

## 6. Orders surface

### GET /v1/orders

Mapped orders for a business, scoped by time/status/item if the business app only wants part of them.

```
GET /v1/orders?businessId=acme&from=&to=&status=fulfilled&itemId=sku-101
```

```
{
  businessId,
  orders: [{
    id,
    businessOrderId,
    placedAt,
    status: "placed" | "fulfilled" | "cancelled" | "returned",
    items: [{
      businessItemId,
      qty,
      unitPrice,
      subtotal,
    }],
    total,
    currency,
    customer: { id?, anonymous?: true },
    notes?,
    source: "pos" | "ecommerce" | "booking" | "manual" | ...,
  }],
  asOf,
}
```

Minimal customer info — only what the business hands over, never invented. If the business has no customer concept (a walk-in store), the product keeps it anonymous and only uses the sale event.

### POST /v1/orders/batch

Push orders into the product's mapped store.

```
POST /v1/orders/batch
  { orders: [...] }

-> { accepted: [...], rejected: [{ businessOrderId, reason }], asOf }
```

### POST /v1/orders/{id}/events

The live path: when an order event happens, the business's system pushes the event so sell-through stays current without re-pushing everything.

```
POST /v1/orders/{id}/events
  { event: "fulfilled", at, itemId, qty }
```

This is the "track orders" path, honestly: the business calls this per event from its POS / ecommerce backend / booking system.

### GET /v1/orders/sellthrough

The cheap derived surface a business backend calls when it wants *"how fast is this line moving"* without plumbing through all orders.

```
GET /v1/orders/sellthrough?businessId=acme&window=24h&itemId=sku-101
```

```
{
  businessId,
  itemId,
  soldInWindow,
  unitsAvailable,
  velocity,             # units per hour estimate over the window, honestly labelled
  lastSaleAt,
  source: "orders" | "stock" | "mixed",
}
```

`source` is the honest part: if the product derived this from orders, it says so; from stock only, it says so; mixed, it says so. That keeps the business honest about what "velocity" means for it.

## 7. Recommendations surface

### POST /v1/recommends/analyze

The core of the product. Ask about a specific line and get the bundle plan plus the three-way money comparison and the break-even.

```
POST /v1/recommends/analyze
  { businessId, itemId, context?: { question?: string, intent?: string, sourceOfQuestion?: "app" | "embed" | "api" } }

-> {
    id,
    businessId,
    itemId,
    itemIdInTheirSystem,
    headline,
    action,                  # "bundle the baguette: free stamp + pastry voucher, and not the markdown"

    recommendation: {
      tiers: [{
        tier, label, perk, status: "funded" | "deferred" | "withheld",
        reason?,             # why a perk was held back, if withheld
      }],
      marginRetained,
      deferredCost,
    },

    money: {
      lapse:   { cashPerUnit, unitsLeft, expectedUnsold, writeOff },
      markdown:{ cashPerUnit, cut, newPrice, breachingFloor, note },
      bundle:  { cashPerUnit, perkCost, deferred, breachingFloor },
      breakeven: { direction: "needs-volume" | "already-ahead", explanation, extraPercent? }
    },

    figuresTraceable: true,   # the honest signal that the numbers came from the business's mapped stock, not from model memory

    sources: [{
      id, kind: "stock" | "floor" | "perk" | "order" | "rule",
      detail, relevance,
    }],
  }
```

`sources` is what lets the business's app show *"this was built from this line's live price, this line's floor, these perks you can offer, and these orders"* — the thing that makes it trustworthy enough to show in the business's own UI. `figuresTraceable` is the honest flag that the numbers came from the mapped stock and not from model memory.

The breakeven is where the product says, in plain English, how the bundle compares to a markdown: either *"the bundle has to move about 17% more units to match a 20% cut — and unlike the cut, it doesn't reset what customers expect to pay"* (needs-volume), or *"the bundle keeps more per unit than the cut, so it earns more on every unit sold as well as holding the price"* (already-ahead). No `+ -2%` artifact anywhere.

### POST /v1/recommends/scenarios

The three-way comparison for one line, returned in full, so the business's app can show the whole picture directly.

```
POST /v1/recommends/scenarios
  { businessId, itemId, markdownPercent? }

-> {
    id,
    itemId,
    scenarios: [{
      key: "lapse" | "markdown" | "bundle",
      cashPerUnit,
      unitsExpectedToMove,
      writeOff?,
      floorHeld,
      note,
      source: "assumed same volume",
    }],
    source: "assumed same volume across all three",
    figuresTraceable: true,
    sources: [...],
  }
```

That's the "track orders and everything" reasoning half: the business's app asks for the money picture and gets it in full, with the honest note that all three assume the same projected volume, so none of them is winning by a made-up uplift.

### POST /v1/recommends/pair

The bundle coster: cost two or three lines separately and return a combined total, so the business's app can say *"pairing these two lines makes money on each; pairing these three keeps the price and adds perks — here's the total and the deferred cost"*.

```
POST /v1/recommends/pair
  { businessId, itemIds: [id1, id2, ?id3] }

-> {
    id,
    lines: [{
      itemId,
      shelfPrice, floorPerUnit, marginPerUnit, headroomPerUnit,
      perkCost, unitsOnShelf, valueOnShelf,
      profitIfAllSells, hoursRemaining,
      verdict: "makes money" | "thin" | "even",
      perks: [perk labels funded],
    }],
    combined: {
      valueOnShelf, profitIfAllSells, perkCostToday, deferredToNextVisit, lines,
    },
    verdict: string,
    method: string,
    figuresTraceable: true,
    sources: [...],
  }
```

That's the second "track orders and everything" reasoning surface: you can pair lines and see what each one keeps, with the honest note that profit here means shelf price less perk cost, and that cost-of-goods is absent if the business didn't hand it over (so profit is price-minus-perk, not true economic profit, stated honestly).

### GET /v1/recommends/{id} and GET /v1/recommends/history

- `GET /v1/recommends/{id}` — read one recommendation by id, for the business's app to re-render it later (e.g. after a page reload).
- `GET /v1/recommends/history?businessId=acme&itemId=&createdAfter=&limit=` — read history for a business (for the "what we actually did this week" view, optionally filtered by item).

## 8. Decision surface

### POST /v1/strategy/decide

Log what the business actually did. Idempotency key required, so an integrator can retry safely without double-logging a decision.

```
POST /v1/strategy/decide
  {
    businessId,
    recommendId?, itemId, itemIdInTheirSystem?,
    managerDecision: "Approved" | "Declined" | ...,
    unitsFilled?, marginRetained?,
    decidedBy,
    source: "app" | "embed" | "api" | ...,
    idempotencyKey?,
  }

-> {
    decisionId,
    recommendId?,
    itemId,
    decision,
    unitsFilled,
    marginRetained,
    decidedAt,
  }
```

This is the write that makes the product an integration rather than a chat box: the recommendation was made; the business's app logged what was actually done; the shared log now has both.

### GET /v1/strategy/history

What the business's own UI calls to show "what we actually did about stock this week."

```
GET /v1/strategy/history?businessId=acme&itemId=&createdAfter=&limit=
```

```
{
  decisions: [{
    decisionId, itemId, itemIdInTheirSystem,
    recommendation: { headline, action, money, tiers? },
    decision, unitsFilled, marginRetained, decidedAt,
  }],
  asOf,
}
```

That's the "track orders and everything" audit half: you can see what was recommended, what was decided, and how much margin each decision kept.

## 9. Supporting assumptions

### Auth and scopes

An integration key is issued per business (or per integration), with a scope. At least:

- `stock:read` — read `/v1/stock` and `/v1/stock/compact`
- `orders:read` / `orders:write` — read orders and push orders/events (write is separately scoped because pushing stock into ValueAnchor is consent)
- `recommends:read` — read recommendations and scenarios
- `strategy:write` — log a decision
- `admin` — push stock, push orders, change configuration; kept out of the read-only integrations

That scoping is real, because an integration that only wants to read stock and show recommendations shouldn't also be able to push stock into ValueAnchor and pretend to be the business's sync.

### Rate limits

Per-business, per key. The surface should state at least that a read-heavy integration and a write-heavy sync have different limits, and that a decision write has a separate, smaller limit because it's a real audit event. The rate-limit error is 429 with a clear `Retry-After` and a message that says what limit was hit, so the business's backend can back off properly.

### Error shapes

A real surface needs honest errors, not "500" for "the business's sync hasn't pushed stock yet." At least:

- `400` — bad shape / missing required fields
- `401` / `403` — bad or scoped-out key
- `404` — a line/order/recommendation not found for that business
- `409` — conflict (e.g. an idempotency key reused with a different body)
- `429` — rate limit (with `Retry-After`)
- `422` — "the business's data isn't ready" — e.g. a recommend call for a line that hasn't been mapped into the product's store yet

The `422` one is the honest one: if the business's app calls `/v1/recommends/analyze` for a line the product doesn't yet hold, the product should say *"I don't have mapped stock for this yet"* rather than pretending to know.

## 10. What "track orders and everything" means in this contract

To tie it back to your wording: your software would allow the business's backend to:

- **see** its stock and prices (`/v1/stock`, `/v1/stock/compact`)
- **see** its orders and sell-through (`/v1/orders`, `/v1/orders/sellthrough`, `/v1/orders/{id}/events`)
- **see** its floor and time signal (in the stock record, with the honest "absent" carve-outs)
- **get** a recommendation for a specific line, the full money comparison, or a bundle cost
- **log** what it actually did and **read** what it's decided
- **push** stock and orders into the product's mapped store, or **let** the product read from the business's system through a connector on an agreed basis

That's the full shape. Your software doesn't become the business's POS or DB; it sees their stock/prices/orders, reasons over the mapped copy, and gives back a recommendation and a decision log the business's app can use.

## 11. Honest limits written into the contract

- **Sell-through is the anchor; orders are a source.** If a business only has stock, the surface still works but urgency is weaker and marked. If a business only has orders, sell-through is derived from them. The surface treats both as real, neither as universal.
- **Floor is optional everywhere.** Some businesses have an explicit minimum margin or price; some don't. The surface accepts "absent" and doesn't pretend otherwise.
- **Per-business mapping is the real integration work.** The API can be generic, but each business maps its items/categories/prices/floor/order schema onto the product's schema once. That's the honest carve-out: same contract for everyone, per-business mapping as the thing that actually makes it work for a new business.
- **Cost is optional.** If the business doesn't hand you cost-of-goods, profit means price-minus-perk, not true economic profit, and the surface says so.
- **Time signal is split** (expiry / closure / appointment / none / unknown) so the contract is not pretending every business is a per-item expiry business.
- **The product holds a mapped copy, not a live query of the business's raw schema every call.** The contract is against the mapped copy. That's the honest version of "any business."

## 12. Reading order

If someone picks this up to build the `/v1` layer, the honest reading order is:

1. **Contract and ownership model** (this document, sections 1-4) — what the surface promises and what it doesn't.
2. **Stock surface** (section 5) — the floor the reasoning needs.
3. **Orders and sell-through** (section 6) — the live signal.
4. **Recommendations** (section 7) — the reasoning surface a business's app actually calls.
5. **Decision surface** (section 8) — the audit half that makes it an integration.
6. **Supporting assumptions** (section 9) — auth, rates, errors.
7. **Honest limits** (section 11) — the carve-outs that keep the contract honest.
