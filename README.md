# ValueAnchor

**An agentic yield copilot for small businesses that sell stock before it lapses.**

ValueAnchor watches the stock a small business is about to lose — the coffee going cold, the
pastries going stale, the unsold rooms, the empty appointment slots — and tells the owner what
to do about it *without cutting the price*.

It answers questions about live stock in plain language, shows every figure it used, explains
where each figure came from, and refuses to answer anything that is not about that business.

---

## Table of contents

- [The problem it solves](#the-problem-it-solves)
- [What it does](#what-it-does)
- [Why bundling instead of discounting](#why-bundling-instead-of-discounting)
- [How it works: four layers](#how-it-works-four-layers)
  - [1. The local language model](#1-the-local-language-model-on-this-machine)
  - [2. The retrieval layer (the RAG half)](#2-the-retrieval-layer-the-rag-half)
  - [3. The planner](#3-the-planner-the-deciding-half)
  - [4. The assistant (the optional half)](#4-the-assistant-the-optional-half)
- [The four guardrails](#the-four-guardrails)
- [Why it is agentic, not just a chatbot](#why-it-is-agentic-not-just-a-chatbot)
- [How it learns: the self-improving loop](#how-it-learns-the-self-improving-loop)
- [The copilot chat](#the-copilot-chat)
- [How to use it](#how-to-use-it)
- [Language stack](#language-stack)
- [Sectors](#sectors)
- [Run it locally](#run-it-locally)
- [Configuration](#configuration)
- [Which language models it uses, and why](#which-language-models-it-uses-and-why)
- [API](#api)
- [Tests](#tests)
- [What leaves your machine](#what-leaves-your-machine)
- [Design decisions and trade-offs](#design-decisions-and-trade-offs)
- [What it does not do](#what-it-does-not-do)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)

---

## The problem it solves

A small business makes its money on stock it has already paid for. When stock is about to lapse,
the owner has two options, and only one of them is good:

| | What happens | What it costs |
| --- | --- | --- |
| **Mark the price down** | The unit sells | Margin on every unit, and customers learn to wait for the markdown |
| **Add value instead** | The unit sells at full price | A small perk cost, well below the discount it replaces |

Most owners reach for the first option because it is the obvious one, and because nobody is
standing next to them at 4pm saying *"a free cookie costs you R6 and keeps your R38; a 20% cut
costs you R7.60 and teaches every regular to wait."*

ValueAnchor is that voice. It also has to be **trustworthy** — a tool that confidently invents a
number about someone's money is worse than no tool at all. Most of the engineering in this project
exists to make the answers checkable rather than to make them clever.

## What it does

- **Ranks what needs attention.** Every line is scored by urgency, which weighs how little time is
  left against how much stock is still on the shelf.
- **Prices the waste.** Says what the stock that will not sell is actually worth today.
- **Builds floor-safe bundles.** Suggests perks that cost less than the discount they replace, and
  drops any that would break the business's own margin floor.
- **Refuses price cuts that breach the floor.** With the arithmetic shown.
- **Forecasts the close.** What is still unsold if nothing changes, from the rate of sale so far.
- **Reads its own decision history** and learns from it.
- **Explains itself** in the owner's words, with every figure labelled, plus what to do next.
- **Refuses everything else.** News, sport, weather, coding, medical, legal: refused locally,
  deterministically, before any model or network call.
- **Learns the owner's own phrasing** from corrections, and gets measurably better at it.

## Why bundling instead of discounting

The product's entire premise is one rule, enforced in code: **the price floor survives, or the
recommendation is not made.**

A bundle has three tiers, and each is treated differently on purpose:

| Tier | Perk type | How it is paid for |
| --- | --- | --- |
| 1 | Free perk (a loyalty stamp, a size upgrade) | Funded out of today's margin, but only if the floor survives |
| 2 | Partner voucher | Funded after tier 1, and only if the floor still survives |
| 3 | Next-visit credit | **Deferred** — earned on the customer's next visit, not charged against today |

Anything that would take the line below the floor is reported as **withheld**, with the reason.
It is shown rather than hidden, because an owner deciding whether to trust the tool should be able
to see what it considered and rejected.

## How it works: four layers

A question passes through four stages. The order matters, and it is the design:

```
   "should I knock 20% off the sourdough?"
                  │
                  ▼
 ┌────────────────────────────────┐
 │ 1. LOCAL MODEL  (on this box)  │  What was asked? Which line, which percentage?
 │    TF-IDF → logistic regression│  15 intents. Also repairs typos, resolves
 │    + SymSpell + RapidFuzz      │  the item, resolves "that one" from context.
 └────────────────────────────────┘
                  │   intent + entities + confidence
                  ▼
 ┌────────────────────────────────┐
 │ 2. RETRIEVAL  (on this box)    │  Pull the records that matter: the stock line,
 │    TF-IDF over a corpus built  │  the perks, the pricing rules, past decisions,
 │    from the live workspace     │  and the owner's own confirmed questions.
 └────────────────────────────────┘
                  │   the records this answer is allowed to use
                  ▼
 ┌────────────────────────────────┐
 │ 3. PLANNER  (on this box)      │  THE DECISION. Runs the yield maths, applies the
 │    pure functions over live    │  margin floor, refuses the markdown, builds the
 │    database rows               │  bundle. Every number is computed here.
 └────────────────────────────────┘
                  │   answer + headline + figures + steps + citations
                  ▼
 ┌────────────────────────────────┐
 │ 4. ASSISTANT  (optional)       │  Says the same thing in plainer words and explains
 │    A free model over the       │  the figures. Cannot change the decision, cannot
 │    network, if configured      │  introduce a number, can be discarded entirely.
 └────────────────────────────────┘
```

### 1. The local language model (on this machine)

Lives in [`backend/nlu`](backend/nlu). It is assembled from three maintained open-source projects
and trained on the active business's own vocabulary at startup. No weights are downloaded, and no
text leaves the machine.

| Stage | Built on | What it does |
| --- | --- | --- |
| Intent classification | [scikit-learn](https://github.com/scikit-learn/scikit-learn) | Word (1–2) and character (2–4) n-gram TF-IDF into a logistic regression. 15 intents, ~1000 training rows, holdout-scored. |
| Catalogue matching | [RapidFuzz](https://github.com/RapidFuzz/RapidFuzz) | Fuzzy item and category resolution, with a margin over the runner-up so "last night" never becomes a product. |
| Spelling repair | [SymSpellPy](https://github.com/wolfgarbe/symspellpy) | Repairs typos in *unknown* tokens only. Real English is never rewritten, so "joke" stays a joke. |

Regularisation `C` is chosen by cross-validation inside one split's training templates; the hold-out
split happens on templates *before* slot expansion, so it is measured on phrasings the classifier has
never seen, and it is repeated across five independent splits. A single shuffle of the same corpus
reports anywhere from 81% to 95%, so the model card reports the **mean, the standard deviation and
the worst split** rather than whichever number came out luckiest. The shipped model is then trained
on every phrasing, because that estimate already says how it generalises.

### 2. The retrieval layer (the RAG half)

Lives in [`backend/nlu/knowledge.py`](backend/nlu/knowledge.py). It turns the live workspace into a
corpus of small documents and retrieves the handful that matter for a given question:

| Kind | Source | Count in the default workspace |
| --- | --- | --- |
| `stock` | One per inventory line, with price, hours left, velocity, floor and exposure | 5 |
| `perk` | One per perk, with what it costs the business and what it is worth to the customer | 4 |
| `rule` | The margin floor, the bundle-first rule, and how answers are made | 3 |
| `glossary` | Plain-English definitions for every internal term | 16 |
| `decision` | One per logged decision, with the margin it kept | grows |
| `sector` | The other kinds of business the same engine can run | 6 |
| `learned` | Questions the owner asked and confirmed the meaning of | grows |

Two things happen beyond plain similarity search, and both exist to make grounding reliable rather
than lucky:

- **The answer's subject is pinned.** When the planner has already decided which line the answer is
  about, that line's record is included even when the question's wording scored it low. The
  assistant explaining a figure must be able to see the record that figure came from.
- **An unmatched question still gets records.** A short question often shares no vocabulary with any
  document, and returning "the best of nothing" would leave the assistant ungrounded. In that case
  the pricing rules and the most exposed lines are returned instead.

The glossary is part of the corpus on purpose. An owner asking *what does urgency mean* deserves a
grounded answer as much as one asking about croissants.

### 3. The planner (the deciding half)

Lives in [`backend/nlu/planner.py`](backend/nlu/planner.py). Each of the 15 intents has a handler that
reads live rows, runs the same yield maths the API uses (from
[`backend/yield_engine.py`](backend/yield_engine.py), so the HTTP layer, the copilot and the tests can
never disagree about a floor), and returns the answer **plus** the owner-facing layer:

- `headline` — the one-line takeaway ("Start with the Almond croissant")
- `meaning` — what the figures mean in practice, in plain words
- `keyNumbers` — every figure given a label and a reason to care
- `nextSteps` — concrete actions, each of which is a question the copilot can already answer
- `severity` — `act-now`, `opportunity`, `watch`, `caution`, `blocked` or `info`
- `glossary` — plain definitions for any term that leaked into the prose
- `followUps` — what is usually asked next
- `reasoning` — the trace, kept for the "how it worked this out" panel

This layer exists because *knowing the answer* and *being able to act on it* are different things.
"Urgency 0.42" is the right internal decision and a useless sentence for somebody running a shop.

### 4. The assistant (the optional half)

Lives in [`backend/nlu/assistant.py`](backend/nlu/assistant.py). This is the only part of the system
that can touch the network, and its contract is deliberately narrow:

- It is handed the planner's answer **plus the retrieved records**, and asked to say the same thing
  in plainer words and explain the figures.
- It is **not** asked what to do, what the numbers are, or whether the floor is respected. Those are
  already settled before it is called.
- It is only called when the local model is confident the question is about this business at all.

With no API key configured, nothing is lost: the copilot still answers, still shows its figures, still
shows its citations and still refuses off-topic questions, because all of that is local. The assistant
only changes *how the same answer is phrased*.

## The four guardrails

This is the part of the project that matters most, and every guardrail fails closed.

**1. Local scope gate — before any network call.**
`CopilotAssistant.local_scope_gate` requires either a recognised business intent or a business entity
(a stock line, a percentage, money, a duration, a quantity or a time window). Anything else is refused
by deterministic code. The model is never asked to police itself on the one question where a mistake
would be most embarrassing, and off-topic text never leaves the machine.

**2. A lexical off-topic guard.**
Fifteen intents means a stray noun can score just highly enough to be taken seriously. So a curated
list of cues that cannot belong to this trade — `medical`, `weather`, `soccer`, `python`, `election`,
`bitcoin`, `horoscope`, … — forces an off-topic reading *before classification is consulted*. It can
only ever turn a business reading into a refusal, never the other way round. Words that could
plausibly belong to this trade (`temperature`, `forecast`, `order`, `market`, `stock`, `cover`, `slot`)
are deliberately absent.

**3. Self-declared refusal.**
The assistant is required to return `onTopic: false` for anything outside the business. That refusal
is honoured. In live testing it refuses sport, weather and coding questions correctly.

**4. The number audit — the one most systems skip.**
Every figure in the model's answer must already appear in the grounded context, allowing for the same
value written differently (`R23.40` versus `23.40` versus `23`). If the model produces a figure that
is not in the live data, **its entire answer is discarded** and the engine's own answer is used
instead, with the reason reported to the owner.

A language model that rephrases a correct answer is useful. A language model that quietly turns
`R23.40` into `R27` is a liability, and no amount of prompt politeness fixes that. Checking the
arithmetic afterwards does.

## Why it is agentic, not just a chatbot

Four properties, all of them testable:

1. **It retrieves before it speaks.** Answers are written from retrieved records, and each answer
   carries the ids of the records it used. You can see exactly what grounded it.
2. **It decides, then explains.** Intent, item resolution, the floor check and every number are
   settled by deterministic code. The language model is a narrator, not an oracle.
3. **It acts on the workspace.** Recommendations feed the decision log, decisions feed the history
   intent, history feeds the retrievable corpus, and confirmed questions feed the next training run.
4. **It knows what it cannot do.** Off-topic questions are refused deterministically, and it asks for
   a missing subject rather than guessing. A wrong confident answer about money is the worst possible
   output, so the system is built to produce "which batch did you mean?" instead.

## How it learns: the self-improving loop

Lives in [`backend/nlu/memory.py`](backend/nlu/memory.py). The generated templates are not the
sentences the owner actually types. This closes the gap:

1. **Every turn is written down** — the question, the reading, the answer, the records that grounded
   it, and whether the owner found it useful.
2. **A correction is a labelled example.** When the owner picks a clarifying option, or marks an
   answer helpful, that question becomes a *confirmed* question-to-intent pair.
3. **Confirmed examples are merged into the next training run**, weighted above generated templates —
   one owner sentence has to count for more than one template when the two disagree, or a single
   correction is a rounding error against a thousand templates and the model effectively does not
   learn from being told it was wrong.
4. **The engine retrains immediately.** The engine cache key includes how many confirmed questions
   exist, so the moment a reading is confirmed, the next question is answered by a model that has
   already learned it. No restart.
5. **Every retrain records its holdout score**, so "is it actually improving" has a tracked answer
   rather than an assumption. The dashboard shows the trend across runs.

Two rules keep this honest: **nothing off-topic is ever learned from**, and **a thumbs-down is never
taught as a positive example**, so a bad answer cannot be reinforced by the same mechanism that lets
a good one be learned.

## The copilot chat

The chat is the product ([`frontend/app/CopilotChat.tsx`](frontend/app/CopilotChat.tsx)). Each answer
is laid out as a decision, not a paragraph:

- **Severity pill** — *Act now*, *Opportunity*, *Worth watching*, *Use with care*, *Not doing that*.
- **Headline** — the one-line takeaway.
- **The answer**, in plain words (rewritten by the assistant when one is connected).
- **What this means** — what the figures mean in practice.
- **Labelled figures** — `Shelf price R42`, `Still unsold 39 of 60`, `Money at risk R1,638`,
  `Your floor on this line R25`, `Room to add value R17`. Never a bare number.
- **Words explained** — any internal term that appeared, defined inline.
- **What you can do about it** — each step is a button that asks the right next question.
- **Clarifying chips** — when two readings tie, pick one; the choice is remembered and becomes
  training data.
- **"How it worked this out"** — the reasoning trace, the confidence, what was read out of the
  sentence, what it also considered, which model answered and what was rejected.
- **"Where this came from"** — the actual records, labelled by kind, with relevance scores.
- **Feedback** — thumbs up or down, with the learning rule stated.

While a question is being worked out, the waiting state shows the four stages it is going through
(reading → finding records → working out the numbers → putting it in plain words), so the pause reads
as work rather than as nothing happening.

Conversations are stored server-side, so reopening the page restores the whole conversation —
including the figures, the steps and the citations, not just the prose.

## How to use it

Once both servers are running (below), open `http://localhost:3000`.

**As an owner, in the first two minutes:**

1. Open the **Overview** tab. The copilot greets you with starter questions built from *your* live
   stock — "Which batch needs attention first?", "What should I bundle with the Almond croissant?" —
   not generic examples.
2. Tap one. You get a headline, the figures with labels, and buttons for what to do next.
3. Tap **Ask** on a step. It asks the follow-up for you and answers with the live numbers.
4. Tap **How it worked this out** to see the reasoning. Tap **Where this came from** to see the
   records. You never have to take a number on faith.
5. Open **Inventory**, pick a line, and hit **Generate yield action** to see the bundle tiers —
   including the ones that were withheld, and why.
6. Approve or decline. The decision is logged and becomes part of what the copilot knows.
7. Open **Copilot AI** to see how the model thinks, what it retrieves, what it has learned from you
   (with the accuracy trend across retrains), and to chat in the expanded view.

**Things to try that demonstrate the guardrails:**

- `who won the soccer game last night?` → refused locally, no model called.
- `write me a python function to scrape a website` → refused locally, no model called.
- `what does urgency mean?` → explained in plain words, on a live line.
- `should I knock 20 percent off the sourdough loaf?` → the arithmetic that shows whether it breaks
  the floor, and what a bundle would keep instead.
- `do something about it` → it asks which reading you meant instead of guessing, and remembers.
- Switch the sector in the sidebar to **Bakery** and ask about a *cinnamon bun*. The vocabulary,
  spelling dictionary and catalogue rebuild for that business in seconds.

## Language stack

| Layer | Technology | Notes |
| --- | --- | --- |
| Backend language | **Python 3.14** | |
| Backend framework | **FastAPI 0.115** + **Uvicorn 0.30** | Async HTTP, Pydantic request validation |
| HTTP client | **httpx 0.27** | Only used for the optional assistant |
| Intent model | **scikit-learn 1.9** | TfidfVectorizer + LogisticRegression, cross-validated |
| Fuzzy matching | **RapidFuzz 3.14** | Item and category resolution with ambiguity gating |
| Spelling repair | **SymSpellPy 6.10** | Unknown-token repair against an 82,834-word English dictionary plus the business's own vocabulary |
| Storage | **SQLite** (standard library) | Inventory, perks, decisions, profile, and everything learned |
| Frontend language | **TypeScript 5.9** | |
| Frontend framework | **Next.js 14 (App Router)** + **React 18** | Client components, no SSR data fetching |
| Icons | **lucide-react** | |
| Styling | **Hand-written CSS** | One stylesheet, CSS custom properties, no framework |
| Assistant (optional) | **OpenRouter** free models | `httpx`, JSON mode, model fallback chain |
| Tests | **pytest 8.4** + FastAPI `TestClient` | 73 tests |

There is no vector database, no embedding model and no container in the loop. Retrieval is TF-IDF
over a corpus built per request, which keeps the whole product installable offline and its behaviour
reproducible.

## Sectors

The engine is not coffee-specific. A sector supplies the inventory, how fast it perishes, the perks it
can bundle, the margin floor and the words the owner uses — so the same engine runs a different
business as data, not as code.

| Slug | Business | Sells | Floor |
| --- | --- | --- | --- |
| `coffee` (default) | Kloof Street Roasters | counter stock that perishes daily | 60% |
| `bakery` | Yeast & Crumb | bakes sold on the day | 55% |
| `hospitality` | Aloe Ridge Hotel | rooms, covers, treatments | 65% |
| `wellness` | Salt & Steam Studio | appointment slots | 62% |
| `retail` | Corner Greens Market | short-dated fresh lines | 50% |
| `travel` | Coastal Line Coach | departure-bound capacity | 60% |

Switching sector reseeds that sector's inventory and perks, rebuilds the lexicon, and retrains the
model off the request path. **Logged decisions are never rewritten** — history keeps the label it was
recorded with, so the audit trail survives a switch.

## Run it locally

### Backend

```bash
cd backend
python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

FastAPI serves on `http://localhost:8000`. SQLite is created automatically at
`backend/valueanchor.db`; set `VALUEANCHOR_DB` to point it elsewhere.

At startup it prints what it trained:

```
[ValueAnchor] local NLU ready for 'coffee': 15 intents, 1000 training rows (0 learned here), holdout 88%, 10.2s
[ValueAnchor] assistant: enabled with free models nvidia/nemotron-3-super-120b-a12b:free, liquid/lfm-2.5-2.6b:free
```

If a workspace has been left overnight, every seeded line will have lapsed. On startup the active
sector is reseeded in that case, so the workspace stays usable — logged decisions are kept.

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:3000`. To point at a different backend, set `NEXT_PUBLIC_API_URL` before
starting Next.js.

## Configuration

Configuration is one gitignored file, `backend/.env`. Copy `backend/.env.example` and fill it in:

```bash
# Leave empty to run the whole product offline with no network call at all.
OPENROUTER_API_KEY=

# Free models only, tried in order until one answers with valid JSON, so a single
# model being rate-limited or retired does not take the assistant offline.
OPENROUTER_MODEL=nvidia/nemotron-3-super-120b-a12b:free
OPENROUTER_FALLBACK_MODELS=liquid/lfm-2.5-2.6b:free,nvidia/nemotron-3-ultra-550b-a55b:free,nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free
```

The key is read from the server environment only, never from a request body, so the browser never
holds it. `.env` is gitignored; `backend/.env.example` is committed.

## Which language models it uses, and why

**Free models only.** One key covers all of them — OpenRouter routes a model id to a provider, so a
single key is enough for the whole chain. No per-model keys are needed.

The chain was chosen by probing the live free-model list on this exact task, not from documentation:

| Model | Verdict | Why |
| --- | --- | --- |
| `nvidia/nemotron-3-super-120b-a12b:free` | **Primary** | Cleanest plain-language rewrite, and it refused the off-topic probe correctly |
| `liquid/lfm-2.5-2.6b:free` | Fallback 1 | Small and fast, also refused off topic correctly |
| `nvidia/nemotron-3-ultra-550b-a55b:free` | Fallback 2 | Strong, but sometimes returns its JSON double-encoded as a string (handled) |
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | Fallback 3 | Reasoning model, occasionally returns empty content |

Rejected during probing, and deliberately absent:

| Model | Why not |
| --- | --- |
| `google/gemma-4-31b-it:free`, `google/gemma-4-26b-a4b-it:free` | `429` — rate-limited upstream on the shared pool |
| `thinkingmachines/inkling-small:free` | `403` — gated to agentic harnesses, not usable here |
| `nvidia/nemotron-3.5-lightning:free` | Spent its token budget reasoning and returned `null` content |
| `dots-studio/dots-3-note-preview:free` | Returned no content |

Notes that shaped the implementation:

- **The models in this chain are reasoning models.** One spent 609 of 679 completion tokens thinking
  before writing anything, and at a low token cap returned no content at all. `max_tokens` is set
  generously for headroom to think, not for a longer reply.
- **Free-tier limits are 20 requests/minute and 50/day.** That is *why* the local model answers first
  and the assistant is only consulted for a question that is already known to be about the business.
  On-topic questions only; nothing else reaches the network.
- **A model returning nothing is not an error.** The chain simply tries the next one, and every
  attempt is reported to the UI so you can see which model answered and which were skipped.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Liveness |
| `GET` | `/api/profile` | Active business: sector, vocabulary, margin floor, currency |
| `GET` | `/api/sectors` | Available presets |
| `POST` | `/api/profile/sector` | `{"sector": "bakery"}` — reseeds and retrains |
| `POST` | `/api/profile/currency` | `{"currencyCode": "ZAR", "currencySymbol": "R"}` |
| `GET` | `/api/inventory` | `{profile, items}` with velocity, urgency, time left, risk |
| `POST` | `/api/copilot` | **The main endpoint.** `{question, sessionId, intent?}` → the full grounded answer |
| `GET` | `/api/copilot/history` | `?sessionId=&limit=` — the whole conversation, restorable |
| `GET` | `/api/copilot/suggestions` | Starter questions built from the live line under pressure |
| `POST` | `/api/copilot/feedback` | `{turnId, rating, note?, intent?}` — a helpful vote becomes training data |
| `POST` | `/api/copilot/confirm` | `{turnId, intent}` — "this is what I meant"; becomes training data |
| `POST` | `/api/nlu/parse` | The language layer alone: `{question}` → `{parse, reply}` |
| `GET` | `/api/nlu/model` | Model card: intents, training rows, holdout scores, assistant config and guardrails |
| `GET` | `/api/learning` | What this workspace has taught the model, with the accuracy trend |
| `GET` | `/api/knowledge` | `?question=` — what is retrievable, and what a question actually retrieves |
| `POST` | `/api/yield/analyze` | `{"itemId": 101}` — the bundle plan, including withheld tiers |
| `POST` | `/api/strategy/decide` | Log a decision |
| `GET` | `/api/strategy/history` | Logged decisions, newest first |

`POST /api/copilot` returns, in one payload: `answer`, `engineAnswer`, `source`, `headline`,
`meaning`, `severity`, `nextSteps`, `keyNumbers`, `followUps`, `glossary`, `sources`, `retrieval`,
`assistant` (used, model, verdict, every attempt), `guardrail`, `inScope`, `turnId`, `learning`, and
the full `nlu` parse with its reasoning trace.

## Tests

```bash
cd backend
.venv\Scripts\python.exe -m pytest tests -q     # or: python -m pytest tests -q
```

**73 tests, all passing.** The suite runs against a scratch database and with the network assistant
switched off, so it is deterministic and fast (~100s, most of it training).

Beyond the original floor, planner and HTTP-contract tests, the suite asserts the properties this
project is actually about:

- an answer's subject is **always** among its retrieved records
- retrieval still returns records when a question matches nothing
- the scope gate refuses off-topic questions **without a network call**, proven by asserting the
  assistant's attempt list is empty
- four separate off-topic questions (`weather`, `python`, `soccer`, `medical`) never reach a model
- a figure not present in the live data is **rejected** by the number audit
- the same figure written differently is **accepted** (rounding is not inventing)
- a double-encoded JSON reply is tolerated
- every answer carries a headline, a meaning, labelled figures and at least one action
- a jargon question is **explained**, not refused
- suggested follow-up questions are themselves answerable
- a helpful vote becomes training data
- **a thumbs-down is recorded but never taught**
- a confirmed correction changes how the model reads that phrasing, and the learned-row count grows
- history restores the whole conversation, not just the prose
- **eight vague phrases ask back** ("handle it", "fix this", "what now", …) while **thirteen real
  questions answer without being sent back**, so the two behaviours are pinned down in both directions
- a clarifying answer is presented as one — its own headline, no misleading action buttons
- an assistant-about intent has to be *earned* by a word that belongs to it
- the sector's own vocabulary is **pluralised correctly** ("5 batches", not "5 batchs"),
  checked against each sector's item label rather than the default preset alone

## What leaves your machine

This matters, so it is stated plainly rather than buried:

- **By default, nothing.** With no `OPENROUTER_API_KEY`, every answer, figure, refusal and learning
  step happens locally. There is no network call at all.
- **With a key configured**, exactly one thing leaves: for a question the local model has *already*
  judged to be about this business, the question, the planner's answer, and the retrieved records for
  that answer are sent to a free OpenRouter model to be rephrased. Nothing else, and never for an
  off-topic question.
- **The API says which it is.** `GET /api/nlu/model` reports `leavesMachine`, and the Copilot AI tab
  shows it. The card reports `leavesMachine: true` when the assistant is enabled, because that is
  true, rather than claiming offline when it is not.
- The API key lives in the server environment only. It is never sent to the browser, never in a
  request body, and never committed.

## Design decisions and trade-offs

**Why a local model at all, instead of just calling a language model?**
Determinism, latency, privacy, and the free-tier limit of 50 requests a day. Intent, item resolution
and every number are reproducible and testable. The language model is then free to be a narrator.

**Why TF-IDF retrieval instead of embeddings?**
No model download, no vector database, no hidden state, and it is inspectable — you can print the
corpus and the scores. For a corpus of tens of documents built from the workspace, embeddings would
add a dependency without adding much.

**Why is the number audit so strict?**
Because the failure mode it prevents is silent and expensive. Discarding an answer that contains an
unknown figure loses a nicer sentence; not discarding it loses the owner's trust and possibly money.
Rejection is reported, not hidden.

**Why does a vague question get a clarifying question instead of an answer?**
Confidence alone cannot separate "what now" from "what is my margin floor" — both score highly. What
can is whether the sentence points at *anything*. A wrong answer about a named line is a near miss; a
confident answer to a sentence with no subject is a guess dressed up as a decision. So clarification
triggers on a weak reading, a close run-off, **or** a sentence with no subject at all.

**Why is the floor enforced in one module?**
`backend/yield_engine.py` is the only place the yield maths lives, so the HTTP API, the copilot and
the tests cannot disagree about a floor or an urgency score.

## What it does not do

- **It is not cost accounting.** "Margin" here means the shelf price, less only what a perk costs.
  Cost of goods is not modelled, so the floor is a share of price, not true profit.
- **It does not place orders, message customers, or change prices anywhere.** It recommends; a human
  approves. Approvals are logged, not executed.
- **It does not do time series.** The close forecast extends the rate of sale so far across the hours
  left. That is a projection from current behaviour, not a trained forecast.
- **The assistant is not fine-tuned.** "Learning" is the local classifier retraining on your confirmed
  phrasings, plus the retrievable corpus growing. No model weights are ever updated.
- **Sessions are per-device and local.** No accounts, no auth, no multi-user. It is a single-operator
  tool for one workspace.

## Project layout

```
.
├── backend/
│   ├── app.py                 FastAPI app: sectors, database, all endpoints
│   ├── yield_engine.py        The floor maths — the single source of truth
│   ├── nlu/
│   │   ├── dataset.py         15 intents and their training templates
│   │   ├── engine.py          Local model: classify, extract, repair, decide
│   │   ├── lexicon.py         Item and category vocabulary, fuzzy matching
│   │   ├── planner.py         Intent handlers + the plain-language layer
│   │   ├── knowledge.py       Retrieval corpus, glossary, TF-IDF index
│   │   ├── assistant.py       OpenRouter assistant + the four guardrails
│   │   └── memory.py          Turns, feedback, confirmed readings, retrain scores
│   ├── tests/
│   │   ├── conftest.py        Scratch database, assistant disabled
│   │   ├── test_valueanchor.py  Floor, planner, HTTP contract
│   │   └── test_agentic.py    Retrieval, guardrails, answer shape, learning
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── app/
│   │   ├── page.tsx           Dashboard, tabs, model and learning panels
│   │   ├── CopilotChat.tsx    The chat, the answer cards, the evidence panels
│   │   ├── styles.css         All styling
│   │   └── layout.tsx
│   └── package.json
└── README.md
```

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `Backend offline. Start FastAPI on port 8000` | Backend not running, or on another port. Set `NEXT_PUBLIC_API_URL`. |
| Every line shows `0.0 h` remaining | The workspace was left overnight. Restart the backend; it reseeds the stale sector automatically. |
| Answers are plainer and the assistant never rephrases | No `OPENROUTER_API_KEY`. Everything still works; check `GET /api/nlu/model` → `assistant.enabled`. |
| The copilot says the assistant was unavailable | Free-tier limit (20/min, 50/day) or every model in the chain was busy. The engine's own answer is used, which is the designed fallback. |
| Training takes ~10s on first request for a sector | Expected: the lexicon and model are rebuilt for that sector, then cached. |
| `sqlite3.OperationalError` on startup | `VALUEANCHOR_DB` points somewhere unwritable, or another process holds the file. |

---

Built as a Hackathon Project Submission.
