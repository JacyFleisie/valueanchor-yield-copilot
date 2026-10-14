# Honest approach plan for the "any business" product

> **Status: plan, not built.** This is the honest next deliverable after the `/v1` contract and the first-build plan in `docs/integration/`. It does not design more surfaces. It says what success actually looks like for the honest path, in what order, with what gates, and where the hard bits are.

## 1. The honest goal

The honest goal of this product, as an "any business" thing, is small and sharp:

- **A business keeps its own source of truth.**
- **ValueAnchor keeps an owned mapped copy** of that business's stock, orders (optional), and sell-through, mapped once per business onto one internal schema.
- **A business's app or backend can call the `/v1` surface** to push stock/orders, read sell-through, get recommendations, and log decisions into a **shared log** both sides read.
- **The reasoning is honest about what it knows and what it doesn't** — floor optional, cost optional, time signal split, sell-through stated with its source, the product holding a mapped copy rather than pretending to be the business's POS/DB.

That's the goal. Not "every business instantly works." The honest version is "the contract and the build order are right, the honest carve-outs are in the bones, and there's a path from 'one spreadsheet business' to 'a business with a POS' without the product having lied about what it can reach."

That's the honest north star. Everything in this plan is in service of it.

## 2. Gates and phases, honestly

The honest build is phased, and each phase has a **gate** — a thing that must be true before the next phase starts, or the next phase is building on a lie.

### Gate 0 — the honest shape of a business, on paper

Before any code, write down the honest shape of a business that the product can actually integrate with, as a **hand-sized example on paper**:

- Give one concrete business a name.
- Write down its stock: a few items with name, category, price, capacity, sold, floor (or no floor), time signal (or none), cost (or none).
- Write down its orders (if any): a few orders with id, placedAt, status, items (businessItemId, qty, unitPrice, subtotal), total, currency, source, customer handle (or anonymous).
- Write down its sell-through as the product would see it: soldInWindow, unitsAvailable, velocity, lastSaleAt, source (orders / stock / mixed).
- Write down its mapping onto the product's schema: how its item ids become the product's businessItemId, how its categories map (or don't), how its price/floor/time map, how its orders map, how its cost maps.

This gate exists because the honest shape of a business on paper is the thing that decides whether the contract and the build order are actually honest, before any code purports to be "any business." If you can't write one honest hand-shaped example on paper, you don't yet know the honest shape of the thing.

### Gate 1 — the owned store holds one honest business, mapped once

Build the owned store and the mapping layer (Build 1 in the first-build plan), and prove it holds **one** honest business mapped once, with the honest optional fields present as absent where the business doesn't have them.

Honest proof:

- The store has one business's mapped stock, with the honest optional fields (cost, floor, sold, timeLeft, timeSignal) present as absent where the business doesn't have them.
- The mapping record for that business is present and says, honestly, how the business's items/categories/prices/floors/orders/time map, and where they don't.
- The store does **not** pretend to be the business's source of truth. The README note, the contract, and the plan all say the same thing: the product holds a mapped copy.

Exit criteria:

- One honest hand-shaped business is held in the owned store, mapped once, with the optional fields present as absent where appropriate.
- The contract's stock record shape matches what the store actually holds for that business, including the honest optional fields.
- The README note, the contract, and the plan all say the same thing about the mapped copy.

If you can't write that one honest hand-shaped business on paper and hold it in the store with the optional fields honest, you don't pass Gate 1. Don't start wiring the push surfaces.

### Gate 2 — push works for one honest business

Build the push surfaces (Build 2), and prove they can push stock and orders for that one honest business, and the mapped store updates honestly.

Honest proof:

- `POST /v1/stock/batch` accepts that business's stock and maps it, storing it honestly, including the optional fields as absent where the business doesn't have them.
- `POST /v1/orders/batch` accepts that business's orders and maps them, storing them honestly.
- `POST /v1/orders/{id}/events` pushes an order event for that business and the mapped store's sell-through updates, with its source stated honestly.

Exit criteria:

- The push for one honest business updates the owned store honestly, with the optional fields present as absent where the business doesn't have them, and the sell-through source stated.
- The contract's stock, orders, and sell-through shapes match what the product actually accepts and stores for that business.
- The README note, the contract, and the plan still all say the same thing.

If pushing stock or orders for one honest business breaks the honesty (e.g. the store insists on floor when the business has none, or the sell-through source lies), you don't pass Gate 2.

### Gate 3 — the spreadsheet/CSV on-ramp lands one honest business

Build the spreadsheet/CSV on-ramp (Build 3), and prove one honest business that can't push a system call can land on the product by dropping a sheet/CSV, and the same mapping record and owned store accept it honestly.

Honest proof:

- One honest business that can't push a system call drops a sheet/CSV (stock, orders, or both) and the product reads and maps it into the same owned store and same mapping record path as the push path.
- The same honest optional fields behave the same way for the sheet/CSV path as for the push path.

Exit criteria:

- The spreadsheet/CSV on-ramp lands one honest business honestly, using the same mapping and the same owned store as the push path.
- The README note, the contract, and the plan still all say the same thing about push first, spreadsheet on-ramp next, connectors later.

If the on-ramp requires the business to do something unreasonable (like hand over DB creds before it's reasonable, or hand over more than it has), the honest on-ramp isn't landed. Don't claim "any business" yet.

### Gate 4 — the read surfaces are honest for one honest business

Build the read surfaces (Build 4), and prove they're honest for that one honest business:

- `GET /v1/stock` returns the honest mapped stock, with the honest optional fields.
- `GET /v1/stock/compact` returns a cheap honest shape the business's app can render.
- `GET /v1/orders/sellthrough` returns sell-through with its source stated honestly for that business.

Exit criteria:

- The read surfaces for one honest business are honest: the stock record has the honest optional fields, the compact shape is real, and the sell-through source is stated honestly.
- The contract's read-surface shapes match what the product actually returns for that business.

If the read surfaces lie about sell-through source, or claim a floor that isn't there, you don't pass Gate 4.

### Gate 5 — the recommends surfaces produce honest answers for one honest business

Build the recommends surfaces (Build 5), and prove they produce honest answers for that one honest business:

- `POST /v1/recommends/analyze` returns a bundle plan + three-way money + break-even, with sources and `figuresTraceable`, for that business's mapped stock.
- `POST /v1/recommends/scenarios` returns the full three-way comparison for that business's mapped stock.
- `POST /v1/recommends/pair` returns the line-by-line bundle cost plus combined total, for that business's mapped stock.

Honest proof:

- The answers are honest: the money comes from the mapped stock (so `figuresTraceable: true` when it's mapped, and honest when parts are absent), the sources list is honest, and the break-even is honest (no `+ -2%` artifact, honest needs-volume vs already-ahead).
- The honest carve-outs hold: if cost is absent, profit is price - perk cost and the surface says so; if floor is absent, the surface says so; if time signal is absent or none, the reasoning is weaker/honest rather than pretending.

Exit criteria:

- One honest business gets honest recommends answers from its mapped stock, with `figuresTraceable` honest and sources honest, and the honest carve-outs hold.
- The contract's recommends shapes match what the product actually returns for that business.
- The README note, the contract, and the plan still all say the same thing about the recommends surfaces.

If the recommends surfaces produce a number the mapped stock doesn't support, you don't pass Gate 5.

### Gate 6 — the decision surface + shared log close the loop for one honest business

Build the decision surface and the shared decision log (Build 6), and prove that for one honest business, the product makes a recommendation, the business's app logs what it actually did, and both sides can read the same log.

Honest proof:

- The product returns a recommendation (via recommends/analyze or recommends/scenarios) for that business.
- The business's app calls `POST /v1/strategy/decide` with an idempotency key and logs what was actually done.
- Both sides can read the same decision from `GET /v1/strategy/history`.

Exit criteria:

- For one honest business, the decision surface and shared log close the loop honestly: recommendation made, decision logged with idempotency, both sides can read the same log.
- The contract's decision shapes match what the product actually accepts and returns.
- The README note, the contract, and the plan still all say the same thing.

If the shared log isn't really shared (e.g. the business's app can't read what the product wrote, or the product can't read what the business's app wrote), you don't pass Gate 6.

### Gate 7 — auth, rates, errors are honest for one honest business

Build the auth scopes, rate limits, and error shapes (Build 7), and prove they're honest for that one honest business:

- Scopes keep write-paths separate from read-paths honestly (an integration that only wants to read stock and show recommendations can't push stock).
- Rate limits are honest and per-business/per-key, with at least the decision write on a smaller, honest limit.
- Error shapes are honest, including the honest 422: asking for a recommends answer for a line the mapped store doesn't yet hold returns "I don't have mapped stock for this yet," not a lie.

Exit criteria:

- For one honest business, auth/rates/errors are honest: scopes keep write-paths honest, rate limits are honest, and the 422 is honest for "not mapped yet."
- The contract's scopes, rate-limit, and error shapes match what the product actually enforces for that business.

If the 422 lies (pretends to know a line it doesn't hold), you don't pass Gate 7.

### Gate 8 — connectors later, only for businesses that have them and want them

Don't build connectors (DB read, POS/booking API, etc.) until you've landed at least one honest business via push or spreadsheet, and you've landed Gate 7 honestly. Connectors are for the businesses that have them and want the product to read. Building a connector zoo before you've landed any honest business is building reach before you've proven the honest path works for one honest business.

Honest proof:

- At least one honest business has landed honestly via push or spreadsheet.
- Gate 7 is passed honestly.
- Only then do you build a connector for a business that actually has one and wants it.

Exit criteria:

- One honest business has landed honestly.
- A connector is built only for a business that has one and wants it, not as a speculative zoo.

## 3. The honest build order, with gates

This is the honest reading order, with gates, not just a list:

1. **Gate 0 — one honest business on paper.** Write down one concrete business with its stock, orders (if any), sell-through, and mapping. Honest optional fields honest.
2. **Gate 1 — owned store + mapping holds one honest business.** Build 1. Optional fields honest. Mapped copy, not source of truth. README/contract/plan aligned.
3. **Gate 2 — push works for one honest business.** Build 2. Stock batch, orders batch, events. Sell-through source honest.
4. **Gate 3 — spreadsheet/CSV on-ramp lands one honest business.** Build 3. Same mapping + owned store path as push. Honest optional fields same behavior.
5. **Gate 4 — read surfaces are honest for one honest business.** Build 4. Stock, compact, sell-through with source honest.
6. **Gate 5 — recommends are honest for one honest business.** Build 5. Analyze/scenarios/pair honest, `figuresTraceable` honest, sources honest, break-even honest, honest carve-outs hold.
7. **Gate 6 — decision surface + shared log close the loop for one honest business.** Build 6. Idempotency honest. Shared log really shared.
8. **Gate 7 — auth/rates/errors honest for one honest business.** Build 7. Scopes honest, rate limits honest, honest 422 honest.
9. **Gate 8 — connectors later, only for businesses that have them and want them.** After one honest business has landed and Gate 7 is honest.

That's the honest build order with gates: one honest business on paper, then owned store + mapping, then push, then spreadsheet on-ramp, then read surfaces, then recommends, then decision log, then auth/hardening, then connectors for the businesses that have them — each gate honest before the next.

## 4. The honest caveats that have to hold through every gate

These are the same caveats as in the contract and the first-build plan, restated here because they have to hold through every gate. If any gate breaks one of these, the plan is lying and should be stopped:

- **Floor is optional everywhere.** Some businesses have a floor; some don't. Absent is allowed and stated, in the store, in the contract, in the recommends answers, in the README.
- **Cost is optional everywhere.** Profit means price - perk cost when cost is absent; the store, the contract, the recommends answers, the README all say so.
- **Time signal is split.** Expiry / closure / appointment / none / unknown. The store, the contract, the recommends answers, the README all honor it. The product doesn't pretend every business is a per-item expiry business.
- **Sell-through is its own thing, with source stated.** Orders are real where the business has them; sell-through is derived from them, from stock, from a mix, or weak/absent, and the surface says which, in the store, in the contract, in the read surfaces, in the README.
- **Per-business mapping is the real integration work.** The contract is the same for every business; the mapping is the per-business bit, in the store, in the contract, in the README.
- **The product holds a mapped copy, not a live query of the business's raw schema.** The README, the contract, the plan, every gate, all say the same thing: the product holds a mapped copy; the business keeps its source of truth.

If any of these is broken in any gate, the honest path is broken and the plan should be stopped, not pushed through.

## 5. The honest reading order for actually doing this

1. Write one honest business on paper. Name it. Stock, orders (if any), sell-through, mapping. Honest optional fields honest. This is Gate 0.
2. Gate 1: build the owned store + mapping, hold that one honest business honestly, with optional fields honest, mapped copy honest, README/contract/plan aligned.
3. Gate 2: build push stock/orders/events, push that one honest business honestly.
4. Gate 3: build the spreadsheet/CSV on-ramp, land that one honest business honestly if it can't push.
5. Gate 4: build read surfaces, prove they're honest for that one honest business.
6. Gate 5: build recommends, prove they're honest for that one honest business, with `figuresTraceable` and sources honest, break-even honest, honest carve-outs holding.
7. Gate 6: build decision surface + shared log, close the loop honestly for that one honest business.
8. Gate 7: build auth/rates/errors, prove they're honest for that one honest business, including the honest 422.
9. Gate 8: only then, for a business that has one and wants one, build a connector. Not a zoo.

That's the honest order to do this. Not "build everything at once," not "build connectors first," not "pretend every business is a coffee shop." One honest business on paper, hold it honestly, push/spreadsheet it honestly, read it honestly, recommend honestly, close the loop honestly, harden honestly, then reach further only where the business actually has it and wants it.

## 6. What this plan is not

- It is not a flight of fancy. It's a gate-based honest plan.
- It is not claiming "any business" is magic. It's claiming the honest path from one honest business to a real product that can hold any honest business honestly.
- It is not built. It's the honest plan after the contract and the first-build plan.
- It is not a spec for the code. It's the honest approach to build the `/v1` layer honestly, with gates, with the honest carve-outs in the bones.

If this is the next thing, the next real deliverable is: **pick one honest business, write it on paper as Gate 0, and then prove you can hold it honestly in the owned store (Gate 1)** — or admit honestly that you can't yet and stop. That's the honest next step, and it's where the honest path either starts or stops. The rest is honestly gated after that.
