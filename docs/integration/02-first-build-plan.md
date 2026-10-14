# /v1 first build plan

> **Status: plan, not implemented.** This is the honest order to build the public `/v1` surface, written so the next step is obvious and the honest caveats don't get lost in implementation.

## 1. What this plan is for

ValueAnchor's current public surface is the internal `/api/*` layer plus the model registry, profit endpoints, and strategy log. The integration surface is a **new owned layer** on top of that: a public, versioned, per-business `/v1` surface that a business app or backend can call to push stock/orders, read sell-through, get recommendations, and log decisions, with your product keeping an owned mapped store in the middle.

The honest goal of this plan is not "build every endpoint." It's **build the surfaces in the order that makes the next one possible, and keep the honest caveats in the bones of the thing.**

## 2. Order of build

### Build 1: the owned store and the mapping layer

Before any `/v1` call works, the product needs a place to hold a per-business mapped copy of the business's data, and a way to map each business's items/categories/prices/floors/orders/time onto that schema.

That's the honest first build, because everything else is a call that reads from or writes to that store.

**What this build is:**

- One internal mapped schema, shared by every business (the one the reasoning runs off), with the honest optional fields in it from the start:
  - `Stock`: id, businessId, businessItemId, name, category, price, currency, **cost?**, **floor?**, floorKind, capacity, **sold?**, **timeLeft?**, **timeSignal**, lastUpdated
  - `Orders`: id, businessId, businessOrderId, placedAt, status, minimal customer handle, items (businessItemId, qty, unitPrice, subtotal), total, currency, source, notes?
  - `SellThrough` (derived or pushed): businessId, itemId, soldInWindow, unitsAvailable, velocity, lastSaleAt, source
- A **mapping record per business** that stores:
  - how their item id becomes your businessItemId
  - how their category names map (or whether they don't have categories and you infer one)
  - how their price/currency map
  - how their floor maps (or whether they have none and you mark it absent)
  - how their time signal maps (expiry / closure / appointment / none / unknown)
  - how their orders map (if they have them)
  - how their cost maps (if they have it; absent is okay)
- A push path for stock and orders (the product accepts batches and events, maps them, stores them).

This is the part that makes "any business" actually work: the API contract is the same for everyone, but each business maps its schema onto the product's once, kept per business.

**Honest caveat built in at this stage:** do not pretend the mapped store is the business's source of truth. It's a copy. Do not pretend every business has floor, cost, or time signal. Decide the honest shape now, or you'll leak "every business has a floor" into the reasoning later.

### Build 2: push stock + orders + events

Build the surfaces the business's system calls to give your product data:

- `POST /v1/stock/batch` — push stock, return accepted/rejected.
- `POST /v1/orders/batch` — push orders, return accepted/rejected.
- `POST /v1/orders/{id}/events` — push an order event so sell-through stays current without re-pushing everything.

Why push first: it's the cleanest consent path. The business's system calls yours; you never touch their internals in a way that needs their access. It's the easiest honest on-ramp for a business that has *any* system that can call you.

**Honest caveat built in at this stage:** accept the optional fields (cost, floor, timeSignal, sold) as optional in the batch shape, and let the product store them as absent without crashing. Don't require a business to have every field.

### Build 3: the spreadsheet / CSV on-ramp

Build a way for a business to drop a sheet or CSV (stock, orders, or both) and have the product read and map it.

Why this is in the plan early, not last: it's the path that lands the businesses that cannot push a system call, and it's the lowest-friction way in for the largest number of small businesses. If you wait until you have DB/POS connectors to "reach any business," you'll miss the businesses that are just a spreadsheet on a laptop.

**What this build is:**

- Accept a spreadsheet/CSV (stock, orders, or both).
- Map it using the per-business mapping record (same mapping as the push path; this is the point).
- Store the mapped result in the owned store.

Keep it as a real first-class path in the contract, not a hack.

### Build 4: the read surfaces (stock/compact + sell-through)

Now the business's own app or backend can call the product for data:

- `GET /v1/stock` — mapped stock.
- `GET /v1/stock/compact` — cheap shape for the business's own list UI.
- `GET /v1/orders/sellthrough` — the derived cheap sell-through surface, with its honest source field.

**Why sell-through as its own surface:** it's what the business's app uses when it wants *"how fast is this line moving"* without plumbing through all orders, and it's the thing the reasoning hangs off. It also has to state its source honestly (`orders` / `stock` / `mixed`), because that's the honest thing about what velocity means for this business.

**Honest caveat built in at this stage:** if a business only has stock and no orders, sell-through is weak or absent, and the reasoning should mark it honestly. If a business only has orders, sell-through is derived from them. If a business has neither well, the surface says so rather than pretending.

### Build 5: recommends / analysis + scenarios + pair

Now the product can return its reasoning in a checkable shape a business's app can actually use:

- `POST /v1/recommends/analyze` — the core: bundle plan + three-way money + break-even, with sources and figuresTraceable.
- `POST /v1/recommends/scenarios` — the full three-way comparison for one line.
- `POST /v1/recommends/pair` — the bundle coster for two or three lines.

**Why this is build 5 and not build 1:** these surfaces read from the mapped store. They only work honestly once the store has honest stock/orders/sell-through, with the honest optional fields in it. Build the store first, then the recommendations.

**Honest caveat built in at this stage:** the recommendations surface must carry `figuresTraceable: true` (or false) and `sources` honestly, so the business's app knows the numbers came from the mapped stock and not from model memory. Don't skip the sources list because it's "just provenance"; it's what makes the surface trustworthy enough for a business's own app to embed.

### Build 6: decision surface + shared decision log

Now the business's app or backend can close the loop:

- `POST /v1/strategy/decide` — log what the business actually did, with idempotency key so an integrator can retry safely.
- `GET /v1/strategy/history` — read what it's decided, for the "what we actually did this week" view and optional item filtering.

**Why this is the last reasoning surface:** it's the audit half. The product makes a recommendation; the business's app records what was actually done; both read the same shared log. That's the thing that makes the surface an *integration* rather than a separate chat box.

**Honest caveat built in at this stage:** the decision log is shared, not owned by the business or by the product alone. Both sides can read it; the business's app can write to it; the product can write to it. Decide that ownership model now, or the later reads will be wrong.

### Build 7: auth scopes, rate limits, error shapes

Now harden the surface:

- **Scopes:** stock:read, orders:read, orders:write, recommends:read, strategy:write, admin. Keep write-scopes separate from read-scopes, so an integration that only wants to read stock and show recommendations can't push stock into the product.
- **Rate limits:** per-business, per key; read-heavy and write-heavy differentiated; decision writes on a smaller limit (real audit event).
- **Error shapes:** 400, 401/403, 404, 409 (idempotency conflict), 429 (with Retry-After), 422 (business's data isn't ready yet). Build the honest 422 now, because the surface will call it when a business's app asks for a line the product doesn't yet hold.

**Honest caveat built in at this stage:** the honest 422 is important. If the business's app calls `/v1/recommends/analyze` for a line the product doesn't yet hold, the product should say *"I don't have mapped stock for this yet"* rather than pretending to know.

### Build 8: connector pull (later, for the businesses that have it and want it)

Only after the push + spreadsheet + read + recommends + decisions surfaces are honest, build the heavier pull connectors:

- DB read (consented)
- POS/booking API
- anything else a business has and wants you to read

Don't build a connector zoo before you've landed any business. Push + spreadsheet is the easier first reach; connectors are for the businesses that have them and want you to read.

## 3. The owned-store mapping layer, written as a plan

This is the part that actually makes "any business" work. Write it down in the code's own design doc or as part of this plan, not as an afterthought:

- One **internal mapped schema** shared by every business (the one the reasoning runs off).
- One **mapping record per business** (how their items/categories/prices/floors/orders/time map onto your schema).
- The mapping is done once, or refreshed when their schema changes, kept per business, owned by the product.
- The push path and the spreadsheet path both use the same mapping record: that's the point. The connector path uses it too.
- The reasoning reads from the mapped copy, not from the business's raw schema live.

Honest caveat for this build: the mapped store is a copy. Don't pretend it's the business's source of truth. Don't pretend every business has every field. Accept the optional fields from the start.

## 4. The honest caveats, written into the plan

These are the things that would be wrong to leave implicit. They belong in the plan, not just in the README note:

- **Floor is optional everywhere.** Some businesses have a floor; some don't. Absent is allowed and stated.
- **Cost is optional everywhere.** Profit means price - perk cost when cost is absent; the plan says so.
- **Time signal is split.** Expiry / closure / appointment / none / unknown. The plan doesn't assume every business is a per-item expiry business.
- **Sell-through is its own thing, with source stated.** Orders are real where the business has them; sell-through is derived from them, from stock, from a mix, or weak/absent, and the surface says which.
- **Per-business mapping is the real integration work.** The API contract is the same; the mapping is the per-business bit.
- **The product holds a mapped copy, not a live query of the business's raw schema.** Decide that now.

## 5. Reading order for the build

If someone picks this up to build the `/v1` layer, the honest order is:

1. **Owned store and mapping layer** — build 1.
2. **Push stock + orders + events** — build 2.
3. **Spreadsheet/CSV on-ramp** — build 3.
4. **Read surfaces (stock/compact + sell-through)** — build 4.
5. **Recommends / analyze + scenarios + pair** — build 5.
6. **Decision surface + shared decision log** — build 6.
7. **Auth scopes, rate limits, error shapes** — build 7.
8. **Connector pull** — build 8 (later, for businesses that have it).

That's the honest order: store + mapping first (everything else reads or writes from it), push easiest first, spreadsheet on-ramp next (so the largest number of small businesses can land), then read surfaces, then reasoning, then audit, then hardening, then connectors for the businesses that have them.

## 6. What this plan is not

- It is not an implementation. It's the shape and the order.
- It is not pretending the product is the business's POS or DB. The contract says the business keeps its source of truth.
- It is not pretending "any business" plugs in magically. The honest version is: same API contract for everyone, per-business mapping as integration work, push + spreadsheet as the easy reach, connectors later for the businesses that have them.
- It is not a full server-side spec (no auth mechanism detail, no DB choice, no actual handler code). That's the next step after the plan.

If someone wants the next step after this plan, it's one of:

- the real OpenAPI-ish document (every endpoint with request/response shapes, status codes, auth scopes, rate limits, push/spreadsheet/owned-store paths, sell-through source honesty) **— the document a builder would build the /v1 layer from**, or
- the actual endpoint scaffolding + handlers + owned-store schema + mapping-record code, which is the implementation step after the plan.

This plan is the honest middle ground: enough to tell the truth about what to build and in what order, without pretending it's built.
