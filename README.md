<div align="center">

# ⚓ ValueAnchor

**An agentic yield copilot for businesses that sell stock before it lapses.**

Ask it about your live stock in plain words. Every figure is traceable to a record —
and the price floor survives, or the recommendation is not made.

<br>

![tests](https://img.shields.io/badge/tests-216%20passing-brightgreen)
![coverage](https://img.shields.io/badge/logic%20coverage-82%25-green)
![nlu](https://img.shields.io/badge/local%20NLU-90.1%25-blue)
![mcp](https://img.shields.io/badge/MCP-server-built%20%26%20tested-8a2be2)
![python](https://img.shields.io/badge/python-3.14-3776ab)
![next](https://img.shields.io/badge/Next.js-14-000000)

</div>

---

## 🍞 The problem

Small food businesses lose margin every single day to stock that lapses — the coffee going cold,
the pastries going stale. The instinctive fix is a markdown, and the markdown is exactly how a
thin-margin business makes itself thinner.

ValueAnchor watches the stock that is about to be lost and tells the owner what to do about it —
**add value, not discount** — with every number shown, sourced, and checkable.

> ### 🛡️ The one rule, enforced in code
> **The price floor survives, or the recommendation is not made.**
> Anything that would push a line below the owner's floor is *withheld* — with the arithmetic
> that withheld it, on screen. The owner's word still wins; the tool just can't stay quiet.

---

## ✨ What you get

| | |
| --- | --- |
| 💬 **Ask in plain words** | *"What should I bundle with the flat white?"* — a local NLU engine (90.1% holdout accuracy, no cloud needed to understand you) routes your question to the right analysis |
| 🧮 **Every figure is traceable** | Each number links back to the record and the arithmetic that produced it. No invented uplifts, no confident guesses |
| 🚫 **No markdown path** | Ask it to cut a price and it shows you why the floor says no — per line, with the maths |
| 🔌 **OpenEdge-native** | Reads your live OpenEdge database **read-only**, and exposes itself back as an **MCP server** any AI host can call |
| 🔒 **Runs on your machine** | Local-first. At most one network request, and only for an on-topic question to a free LLM that rewrites the wording — never does the math |

---

## 🤖 How it works: MCP & Agentic RAG

**MCP (Model Context Protocol)** is the standard way an AI assistant calls tools and systems
in a structured way. ValueAnchor implements both halves of it:

- **As an MCP server** — it exposes the yield engine as tools (`guardrails`, `urgent_stock`,
  `analyze_line`, `pair_lines`, `ask`, `log_decision`) at `POST /mcp`. A model that wants to
  know what to bundle *calls a tool and reads the number* — it never multiplies a price by a
  percentage and hopes.
- **As an MCP-style consumer of OpenEdge** — OpenEdge is the *trusted system of record*.
  ValueAnchor asks it for data through small, clear operations, never writes to it,
  and does its reasoning on its own side.

**Agentic RAG** is the loop on top: retrieve → reason → ask again if needed.

```
  You:  "What should I bundle with the flat white?"
          │
          ▼
  ┌─ Agent loop ────────────────────────────────────────────┐
  │  1. RETRIEVE  live stock rows + guardrails from OpenEdge │
  │  2. REASON    which lines are lapsing, what the floor is │
  │  3. ASK AGAIN analyze_line → pair_lines → scenario view  │
  │  4. ANSWER    in your words, every figure cited          │
  └──────────────────────────────────────────────────────────┘
          │
          ▼
  "Hold the R45. Bundle the free pastry refill — it costs R6,
   keeps R39 against your R27 floor. Withheld: the R12 voucher
   (would breach by R3)."
```

The agent decides *when* it needs more data and calls tools until it has enough —
the numbers always come from the engine, the words come from the model.

---

## 🚀 How to use it

1. **Open the app** — it starts on a demo workspace with seeded stock, so there's
   something real to talk to within seconds.
2. **Link your database** *(optional)* — on the **OpenEdge** tab, point it at your
   OpenEdge database. It reads **read-only**: your records never move, nothing is written back.
3. **See the risk** — the **Overview** tab ranks today's at-risk lines and the margin floor;
   **Inventory** shows every line with its clock.
4. **Ask the copilot** — on **Copilot AI**, ask in plain words:
   - *"What should I bundle with the croissants?"*
   - *"How much do I stand to lose today if nothing changes?"*
   - *"Do something about it."* → it confirms the plan before anything happens.
5. **Check the floor** — every answer shows the arithmetic, the floor it's priced against,
   and where that floor came from. Change floor, currency, and assistant models on **Settings**.

---

## 🖥️ Run it

**One file. Download, double-click, done.**

Grab **`ValueAnchor.exe`** from [Releases](../../releases/latest) and double-click it.
The app opens in your browser at `http://127.0.0.1:8000` — no Python, no Node.js, no
installer and no second terminal. Keep the small window that appears with it open while
you use ValueAnchor; closing that window stops it.

- **Your data stays with the file.** `valueanchor.db` and the trained question-model cache
  are created next to `ValueAnchor.exe`, so the executable and its data move together.
- **First start** takes about half a minute while the app unpacks itself and trains the
  local question model (a freshly downloaded file can take a little longer); later starts
  reuse that model and are quicker. The browser opens as soon as the address is live.
- **A different port or database** — `ValueAnchor.exe --port 9000 --db D:\shop\data.db`.
  `--no-browser` starts it without opening a tab. If 8000 is taken it moves to the next
  free port and prints the address it got.
- **An assistant key (optional)** — put a `.env` beside the exe containing
  `OPENROUTER_API_KEY=...`. Everything still works, a little more plainly, without one.

<details>
<summary><b>Building the executable (Windows, for developers)</b></summary>

```bash
pip install -r backend/requirements.txt -r packaging/requirements-build.txt
python packaging/build_exe.py
```

Needs Python 3.14 and Node.js 20. The script exports the interface, then freezes the
engine and that export into one file: `dist/ValueAnchor.exe`. The release workflow
(`.github/workflows/release.yml`) runs exactly this on a version tag and attaches the
executable to the GitHub release.

</details>

<details>
<summary><b>Optional extras</b></summary>

- **Assistant models** — the copilot rewrites its own answers through a free LLM of your
  choosing; pick and key it on the **Settings** tab. Everything still works (a little more
  plainly) without one.
- **Tests & evals** — `cd backend && python -m pytest` (216 tests) and
  `python -m evals.run_evals` (34 scored cases).
- **MCP host** — point any MCP client at `POST /mcp` to get the yield engine as tools.

</details>

---

## 🧭 Honest limits

A tool that lies once is never trusted again, so:

- **There is no markdown path.** Not "we hide it" — there is nothing to hide. The engine
  prices value-add options and refuses to price a breach.
- **The model never does arithmetic.** LLMs rewrite wording; every number is engine output.
- **The floor is the owner's, not ours.** Every profile reports whether a floor was
  *set by the owner* or *assumed by the tool* — right next to the number.

---

<div align="center">

**Built for the OpenEdge hackathon** — MCP · Agentic RAG · local-first · open arithmetic

*The price floor survives, or the recommendation is not made.*

</div>
