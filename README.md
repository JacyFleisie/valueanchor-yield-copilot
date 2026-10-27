<div align="center">

# ⚓ ValueAnchor

**An agentic yield copilot for businesses that sell stock before it lapses.**

Ask it about your live stock in plain words. Every figure is traceable to a record —
and the price floor survives, or the recommendation is not made.

<br>

![tests](https://img.shields.io/badge/tests-216%20passing-brightgreen)
![coverage](https://img.shields.io/badge/logic%20coverage-82%25-green)
![nlu](https://img.shields.io/badge/local%20NLU-90%25-blue)
![mcp](https://img.shields.io/badge/MCP-server-built%20%26%20tested-8a2be2)
![openedge](https://img.shields.io/badge/OpenEdge-12.8-orange)
![python](https://img.shields.io/badge/python-3.14-3776ab)
![next](https://img.shields.io/badge/Next.js-14-000000)

**Team:** Solo entry — Jacy Fleisie
**Members:** Jacy Fleisie
**Demo video:** <!-- TODO before submitting: paste the video link/URL here. Script: docs/VIDEO-SCRIPT.md -->

</div>

---

## 1. The problem

Small food businesses lose margin every single day to stock that lapses — the coffee going cold,
the pastries going stale. The instinctive fix is a markdown, and the markdown is exactly how a
thin-margin business makes itself thinner: you sell the unit, but you train the customer to wait
for the discount and you teach them the shelf price was never real.

The owner knows the stock is dying and knows discounting is a bad trade. What they don't have is
a fast, trustworthy answer to *"so what do I actually do about it?"* — and they certainly don't
have one that survives contact with their own margin floor.

ValueAnchor is for that owner: a single-person café, bakery or deli, running on the laptop in the
back office, with no analyst, no cloud account and no appetite for a tool that guesses.

> ### 🛡️ The one rule, enforced in code
> **The price floor survives, or the recommendation is not made.**
> Anything that would push a line below the owner's floor is *withheld* — with the arithmetic
> that withheld it, on screen. The owner's word still wins; the tool just can't stay quiet.

---

## 2. What we built

A desktop copilot that reads a shop's live stock from **OpenEdge**, ranks what is about to be lost,
and recommends **adding value instead of cutting price** — with every number traceable to the record
that produced it.

| | |
| --- | --- |
| 💬 **Ask in plain words** | *"What should I bundle with the flat white?"* — a local NLU engine (90% holdout accuracy, no cloud needed to understand you) routes the question to the right analysis |
| 🧮 **Every figure is traceable** | Each number links back to the retrieved record and the arithmetic that produced it. No invented uplifts, no confident guesses |
| 🚫 **No markdown path** | Ask it to cut a price and it shows you why the floor says no — per line, with the maths |
| 🔌 **OpenEdge-native** | Reads a live OpenEdge database **read-only**, and exposes itself back as an **MCP server** any AI host can call |
| 🔒 **Runs on your machine** | Local-first. At most one network request, and only for an on-topic question to a free LLM that rewrites the wording — never does the math |

Five screens: **Overview** (what's at risk today), **Inventory** (every line with its clock),
**Copilot AI** (the conversation), **OpenEdge** (the live database link and the MCP surface), and
**Settings** (floor, currency, assistant models — all pickers, no typing).

---

## 3. How it works

### OpenEdge components used

| Piece | Where | Role |
| --- | --- | --- |
| **OpenEdge 12.8 database** | `openedge/coffee.db` | The shop's system of record: five coffee-shop lines with price, capacity, units sold and hours left |
| **ABL DDL** | [`openedge/03-coffee-ddl.sql`](openedge/03-coffee-ddl.sql) | Creates the `coffee_item` table (loaded through `sqlexp` against the single-user database) |
| **ABL seeder** | [`openedge/04-seed-coffee.p`](openedge/04-seed-coffee.p) | Writes the five demo rows from inside the AVM — a real `CREATE`, not a fixture file |
| **ABL read-only enquiry** | [`openedge/06-enquiry-json.p`](openedge/06-enquiry-json.p) | The actual integration: one `FOR EACH coffee_item NO-LOCK`, emitted as a single JSON document on stdout |
| **ABL version probe** | [`openedge/01-hello.p`](openedge/01-hello.p) | Reports the AVM's own version/product, so the app can prove which OpenEdge it is talking to |

Pressing **Link this database** runs exactly one command:

```
prowin.exe -b -db openedge\coffee.db -1 -p openedge\06-enquiry-json.p
```

(The app gives it a pipe to write into. Typed by hand in a console instead, add
`> 06-check.out` and read that file — a batch AVM has no terminal, and a framed
program that prints to one stops with error 516. [docs/OPENEDGE.md §6](docs/OPENEDGE.md).)

`-b` is batch mode and `-1` is **single-user**, so there is no AdminServer, no broker and no
DBServer to start first: the app opens the database file directly. That program contains no
`CREATE`, `ASSIGN`, `UPDATE` or `DELETE`, and no argument can point the bridge at a different
program, table or database. Python ([`backend/openedge_bridge.py`](backend/openedge_bridge.py))
turns the JSON line into a stock batch through the *same* upsert the push API uses, makes it the
active business, and retrains the local NLU on its vocabulary. **The database is never written to.**

*Not used, and not claimed:* AppServer, REST Adapter, ODBC/JDBC at runtime, and the *outbound*
OpenEdge MCP Server + Progress Agentic RAG Context Box. The OpenEdge tab reports those as
**not called** rather than pretending a default port is a connection. Turning the outbound side on
is a real install (Docker images plus `./mcpgen up --spec ./openapi.yml`) and is documented in
[docs/OPENEDGE.md](docs/OPENEDGE.md) — skipping it costs nothing as long as nobody claims it.

### AI components used

- **Local NLU classifier** — TF-IDF word (1–2 gram) + character (2–4 gram) features into logistic
  regression: 15 intents, 1,862 generated training rows, measured on a held-out 20% it never saw
  (**90% accuracy**). No weights to download, no network at inference time.
- **Fuzzy catalogue matching + spelling repair** — `rapidfuzz` and `symspellpy` resolve *"flat wite"*
  and *"croisant"* to the real line before any analysis runs.
- **Agentic RAG loop** — retrieve → reason → ask again if needed → answer. The retrieval index
  ([`backend/nlu/knowledge.py`](backend/nlu/knowledge.py)) holds the live stock rows, the perks and
  their costs, the owner's floor and rule, a glossary, past decisions and phrasings the owner has
  confirmed. Every answer carries the records it used, surfaced in the UI as **"Where this came from"**.
- **MCP server** — the yield engine exposed as six tools at `POST /mcp`
  (`valueanchor_guardrails`, `valueanchor_urgent_stock`, `valueanchor_analyze_line`,
  `valueanchor_pair_lines`, `valueanchor_ask`, `valueanchor_log_decision`) over four protocol
  revisions (2026-07-28 → 2024-11-05), negotiated in the handshake. A model that wants to know what
  to bundle *calls a tool and reads the number* — it never multiplies a price by a percentage and hopes.
- **Optional LLM for wording only** — a free OpenRouter model rewrites the sentence order of an
  answer that the engine already computed. Every figure is engine output; remove the key and the
  app answers a little more plainly, with identical numbers.

### Architecture overview

```
  You:  "What should I bundle with the flat white?"
          │
          ▼
  ┌─ Agent loop ──────────────────────────────────────────────────┐
  │  1. RETRIEVE   live stock rows + guardrails + glossary        │
  │                (SQLite mirror, itself read out of OpenEdge)   │
  │  2. REASON     which lines are lapsing, what the floor costs  │
  │  3. ASK AGAIN  analyze_line → pair_lines → scenario view      │
  │  4. ANSWER     in your words, every figure cited              │
  └───────────────────────────────────────────────────────────────┘
          │                                    ▲
          ▼                                    │
   FastAPI + Next.js UI                  POST /mcp  ── any MCP host
   (one executable, one port)                      calls the same tools
          │
          ▼
  OpenEdge 12.8  ──►  prowin.exe -b -db coffee.db -1 -p 06-enquiry-json.p
                      (read-only ABL → one JSON line → mapped copy)
```

The engine is in [`backend/app.py`](backend/app.py) (HTTP + UI), [`backend/api_v1.py`](backend/api_v1.py)
(the versioned analysis API) and [`backend/mcp_server.py`](backend/mcp_server.py) (the MCP surface).
The interface is Next.js static export, served by the same process — no second port, no CORS.

---

## 4. How to run it

### Requirements

- **To run the demo:** Windows 10/11 and nothing else. The executable is self-contained.
- **OpenEdge version:** **12.8** (tested against `C:\Progress\OpenEdge`). OpenEdge is only needed
  for the *live database link* demonstration — the app ships with seeded stock, so every other
  feature works on a machine with no OpenEdge installed at all.
- **To run from source:** Python **3.14**, Node.js **20+**, and `pip install -r backend/requirements.txt`.
- **Accounts/keys:** none required. An optional free OpenRouter key only improves sentence wording
  (see `.env.example`).

### Setup

**1. Clone the repo**

```bash
git clone https://github.com/JacyFleisie/valueanchor-yield-copilot.git
cd valueanchor-yield-copilot
```

**2. Run the executable (fastest path)**

Download `ValueAnchor.exe` from [Releases](../../releases/latest) and **launch it from the repo root**,
so it can find `openedge\coffee.db`:

```bash
.\dist\ValueAnchor.exe
```

Your browser opens at `http://127.0.0.1:8000`. First start takes ~30 s while it trains the local
question model; keep the small console window open (closing it stops the app).

**3. Or run from source**

```bash
pip install -r backend/requirements.txt
cd frontend && npm ci && npm run build && cd ..   # static export -> frontend/out
cd backend && python desktop.py
```

`desktop.py` serves that export and the API from one process on one port, and opens your browser.

**4. Configuration (optional, never committed)**

```bash
cp backend/.env.example backend/.env
# OPENROUTER_API_KEY=...    # optional: better wording only, never the arithmetic
# OPENEDGE_HOME=C:\Progress\OpenEdge   # only if OpenEdge is not in the default place
```

### Quick test

1. Open the **Overview** tab — five lines are ranked by how little time is left against how slowly
   they sell. *Flat white* is first.
2. Click the **Flat white** row. You get *"Hold the R38 price. Add value instead."*, the value-add
   tiers, and the same three choices costed per unit.
3. On **Settings**, set **Minimum retained margin** to **80%** → **Save floor**, then reopen the
   Flat white row. The pastry voucher now reads **"Held back: breaks the floor"** — that is the
   product's whole thesis in one line.
4. On **Copilot AI**, ask `What should I bundle with the flat white?` and click **Where this came from**.
5. On the **OpenEdge** tab, press **Link this database** — 5 lines, 151 units read live from OpenEdge
   in under a second.

> **If you can't run it easily:** the executable needs no OpenEdge, no Python and no Node — but if
> anything blocks you, the [demo video](#5-demo) shows every step above on this machine.

---

## 5. Demo

**Video:** <!-- TODO: paste the link here once recorded -->
**Script:** [docs/VIDEO-SCRIPT.md](docs/VIDEO-SCRIPT.md) — a 2–3 minute shot list with the exact
clicks, the exact words, and what to say if something goes wrong mid-take.

The recording covers: one-file launch → the at-risk overview → the floor refusing to price a
markdown at 80% → the same question in plain words with its citations → the live OpenEdge read and
the MCP surface.

---

## 6. Challenges and learnings

- **The hard part was refusing to be helpful.** Every instinct in an assistant design points toward
  producing *something* for any question. The product only works if it withholds: a withheld
  recommendation with its arithmetic on screen is worth more than a confident one. Encoding
  "don't answer" as a first-class outcome — with the reason — was the single biggest design change.
- **LLMs and arithmetic don't mix, so we stopped letting them try.** The first version let the model
  do the margin maths in prose. It was fluent and occasionally wrong by a rand, which is worse than
  useless in a thin-margin business. Now the engine owns every number and a model only reorders words.
- **Single-user OpenEdge is the pragmatic default.** `-1` avoids requiring a broker for a demo that
  must double-click and work offline. The trade-off is that a multi-terminal shop needs the broker
  path — documented, not hidden.
- **Measured claims beat impressive ones.** The NLU's 90% is a held-out split it never trained on,
  and the coverage badge is the planner's, not the whole tree's. Reporting the weaker, honest number
  kept us from tuning to the wrong thing.
- **With more time:** a proper `TableModel`/`ProDataSet` dataset instead of one enquiry program,
  offline licence-free packaging for the AVM, and a per-day history table so the owner can see
  whether the recommendations actually held margin over a month.

---

## 7. What's next

- **The outbound OpenEdge MCP Server + RAG Context Box**, so an external agent can query the shop's
  records through Progress's own tooling rather than our single enquiry program.
- **Multi-tenant cafés** — one database per shop, with the floor and perk catalogue per tenant.
- **A decision ledger that closes the loop** — compare what the tool recommended against what sold,
  and adjust the floor from evidence rather than instinct.

---

## 🧭 Honest limits

A tool that lies once is never trusted again, so:

- **There is no markdown path.** Not "we hide it" — there is nothing to hide. The engine prices
  value-add options and refuses to price a breach.
- **The model never does arithmetic.** LLMs rewrite wording; every number is engine output.
- **The floor is the owner's, not ours.** Every profile reports whether a floor was *set by the
  owner* or *assumed by the tool* — right next to the number.

## 🧪 Verification

```bash
cd backend && python -m pytest        # 216 tests, 82% logic coverage
cd backend && python -m evals.run_evals   # 34 scored cases
```

`main` is protected by CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)); tagging a version
runs [.github/workflows/release.yml](.github/workflows/release.yml), which builds and attaches the
Windows executable.

---

<div align="center">

**Built for the OpenEdge hackathon** — MCP · Agentic RAG · local-first · open arithmetic

*The price floor survives, or the recommendation is not made.*

</div>
