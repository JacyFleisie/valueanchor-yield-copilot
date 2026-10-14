# Gate 0 — one honest business on paper

> **Purpose:** the honest first artifact the honest path starts from.
>
> Before any `/v1` code, write down one concrete business with its stock, its orders (if any), its sell-through, and its mapping onto the contract's owned-store schema. If this isn't honest, nothing later is.

## The honest business

**Name:** Parkside Coffee

**What it is:** a small walk-in coffee shop. One counter, no delivery, no table service, no online orders, no loyalty numbers in the till. Customers are anonymous; the till knows a sale happened, not who.

**Why this business:** it's the honest smallest real thing the product might integrate with, and it exposes the honest carve-outs honestly — it has stock, it has a floor (because it chooses one), it has **no cost of goods handed over**, and its time signal is **closure-based, not per-item expiry** (the batch perishes, but the reason is "we close at 4pm" rather than "this batch expires at 3:15"). It's a good honest test of the whole honest path without pretending every business is the same.

## 1. Stock, honestly

5 live lines. Only one line has the floor the business actually thinks about; the others are "we keep them above the floor because we price them high enough."

| businessItemId | name | category | price | currency | cost | floor | floorKind | capacity | sold | timeLeft | timeSignal |
|---|---|---|---|---|---|---|---|---|---|---|---|
| SKU-101 | Flat white | Espresso bar | 38.00 | ZAR | (absent) | 23.00 | percent | 140 | 92 | 2 | closure |
| SKU-102 | Almond croissant | Pastry | 42.00 | ZAR | (absent) | 25.00 | percent | 60 | 21 | 3 | closure |
| SKU-103 | Cold brew 500 ml | Cold bar | 52.00 | ZAR | (absent) | (absent) | absent | 48 | 17 | 6 | closure |
| SKU-104 | Chicken mayo baguette | Kitchen | 68.00 | ZAR | (absent) | (absent) | absent | 36 | 12 | 4 | closure |
| SKU-105 | Sourdough loaf | Bakery shelf | 65.00 | ZAR | (absent) | 39.00 | percent | 40 | 31 | 8 | closure |

Honest notes on this honest stock:

- **Cost is absent everywhere.** The business has not handed over cost of goods. Any profit the product shows is price − perk cost, not true economic profit, and the contract/README/plan say so.
- **Floor is not everywhere.** SKU-101, 102, and 105 have an explicit floor. 103 and 104 have **no floor** — the business keeps them high enough that the floor isn't the live concern. The product should not invent a floor for 103/104.
- **Time signal is closure, not expiry.** Every batch has a `timeLeft` because the business closes at a fixed time and the batch is still on the counter then. That's `closure`, not `expiry`. The contract says the product must not pretend this is per-item expiry.
- `sold` is in the sheet the business hands over (it's a stock sheet the business exports from its till at the end of a shift), not derived from orders on day one. That's honest: the business has stock push, and sell-through is derivable from that pushed sold number, but it has **no order events yet** on day one — that comes later if it wants to push them.
- Category names are messy: "Espresso bar", "Pastry", "Cold bar", "Kitchen", "Bakery shelf". The mapping must handle that honestly (categories map, or the product infers a sensible internal category, or it keeps the messy one — just don't pretend the business has clean categories).

## 2. Orders, honestly

**None yet.** The business does not push orders on day one. It could — it has a till — but the honest path starts with a business that hands over a stock sheet and nothing else. That's the honest smallest real thing.

So for Gate 0, the honest state is:

- Orders: **absent.**
- Sell-through: **derived from pushed stock sold**, source = `stock` (for the business's pushed sold column), until/unless the business pushes order events later.
- `orders` store: **empty**, honestly.
- `orders/sellthrough` record for each line: `soldInWindow` derived from the pushed sold column over the window the business says (e.g. 24h), `velocity` from that sold number over the window, `source: "stock"`.

This is the honest point: a business with stock and no orders is a real, honest case. The product's reasoning still works (it can still say "this flat white is critical"), but its sell-through is `stock`-derived and weak, and it says so honestly. Don't pretend it has orders.

## 3. Sell-through, honestly

Sell-through for this honest business, as the product would derive it on day one from the pushed stock:

| businessItemId | soldInWindow | unitsAvailable | velocity | lastSaleAt | source |
|---|---|---|---|---|---|
| SKU-101 | 92 | 48 | ~3.8/h over the 24h window if we take 92 sold over the day | yesterday, last hour | stock |
| SKU-102 | 21 | 39 | ~0.9/h | yesterday, 2 hours ago | stock |
| SKU-103 | 17 | 31 | ~0.7/h | 2 days ago | stock |
| SKU-104 | 12 | 24 | ~0.5/h | 3 days ago | stock |
| SKU-105 | 31 | 9 | ~1.3/h | yesterday, 3 hours ago | stock |

Honest notes on this honest sell-through:

- The numbers are made up for the honest example, but the honest shape is real: high velocity on the espresso and pastry, low on the baguette, the sourdough is low units but faster than the baguette. That's the honest thing the product would see.
- The source is `stock` because that's what the business pushed. If the business later pushes order events, the source becomes `mixed` or `orders`. The contract/README/plan say the source must be stated, so the surface is honest about what "velocity" means here.
- `unitsAvailable` = capacity − sold, honestly.
- The window is whatever the business's sheet says it covers; the product should not invent a window. In this example, assume the sheet is a "previous 24h" sheet.

## 4. Mapping, honestly

Mapping record for Parkside Coffee onto the contract's owned-store schema:

- **item id mapping:** `businessItemId` = the business's SKU. The product's `id` is its own internal id.
- **category mapping:** map the messy categories to a cleaner internal set, or keep them as-is and just not pretend they're clean. Honest option: keep them as-is, with a note that the business's categories are free text. The internal reasoning doesn't need them to be clean; it needs them to exist.
- **price/currency mapping:** direct — ZAR, 2 decimal places. The contract's stock record shape holds `price` and `currency`.
- **floor mapping:** 101, 102, 105 have a floor; 103 and 104 have `floor: absent`, `floorKind: "absent"`, with an honest `floorNote`. The contract/README/plan say the product must not invent a floor for 103/104.
- **cost mapping:** absent for all five. The contract/README/plan say profit here means price − perk cost, not true economic profit, and the recommends answers must say so.
- **time signal mapping:** all five are `closure` with a `timeLeft` in hours until close. The contract/README/plan say the product must not pretend this is per-item expiry.
- **orders mapping:** none on day one. The orders store is empty. `sold` on the stock record is the pushed sold number; sell-through is derived from that.

Honest note on the mapping: this is the real integration work. The contract is the same for every business, but this mapping is the per-business bit. If a later business has a different catalog shape, a different floor logic, or a different time signal, the mapping record is where that lives, not the contract.

## 5. What the honest path proves for this honest business

This honest business proves, honestly:

- **Floor is optional everywhere.** 103 and 104 have no floor; the honest path must not invent one, and the recommends answers must not pretend one exists for them.
- **Cost is optional everywhere.** None of the five lines have cost; recommends answers must say profit means price − perk cost, not true economic profit.
- **Time signal is split, not assumed.** All five are `closure`, not `expiry`; the surface must say `closure`, not pretend they're per-item expiry.
- **Sell-through is its own thing, with source stated.** Sell-through on day one is `stock`-derived from the pushed sold column; if the business later pushes order events, the source becomes `mixed` or `orders`. Honest at every step.
- **The product holds a mapped copy, not the source of truth.** The business's till is the truth; the product holds the mapped copy it was handed.
- **A business with stock but no orders is a real, honest case**, and the honest path handles it without pretending it has orders.

This is Gate 0. One honest business, on paper, with the carve-outs honest. Everything else in the honest path is "can the build actually hold this honest business honestly, gate by gate."

## 6. What's intentionally not in Gate 0

- **No orders pushed.** Honest: the business only hands a stock sheet on day one.
- **No cost of goods.** Honest: the business hasn't handed it over.
- **No per-item expiry.** Honest: the business closes at a fixed time; batches don't each have their own expiry timestamp.
- **No clean categories.** Honest: the business's categories are messy free text.
- **No connector yet.** Honest: the business landed via a pushed stock sheet, not a DB/POS connector. Connectors come later, only if this business (or another) has one and wants one.

These are the dishonestly-excluded things if you pretend every business is the same. This honest business is honest precisely because it does not pretend those things exist.

## 7. Reading this with the other honest docs

- The contract's stock surface shape for this business is the shape in **section 5** of `01-api-contract.md`, with 103/104 having `floor: absent`, `floorNote` honest, `cost` absent, `timeSignal: "closure"`.
- The contract's sell-through shape for this business is the shape in **section 6** of the contract, with `source: "stock"` on day one.
- The plan's Build 1 is what stores this mapping; Build 2 is what accepts the stock sheet; Build 4 is what returns stock/compact and sell-through for this business; Build 5 is what returns recommends for this business's mapped stock.
- The approach plan's Gate 0 is this doc. Gate 1 is "can the owned store hold this honest business honestly." Gate 4 is "can the read surfaces be honest for this honest business." Gate 5 is "can the recommends surfaces be honest for this honest business."

That's the honest first artifact. If the contract, the plan, and the approach plan can't hold this honest business honestly, the honest path is broken before it starts.
