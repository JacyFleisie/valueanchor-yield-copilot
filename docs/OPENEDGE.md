# OpenEdge in ValueAnchor — what it does, and what you should do

Verified on this machine on 26 October 2026 by running the published `v1.1.0`
executable against the `openedge/coffee.db` database. Every number below was
read out of a live process, not remembered.

---

## 1. The one-sentence answer

**OpenEdge is the system of record.** ValueAnchor does not own the stock — the
shop's OpenEdge database does, and ValueAnchor reads it through one read-only
ABL program. Nothing is typed in, nothing is uploaded, nothing is written back.

The reverse direction matters just as much: **ValueAnchor is itself an MCP
server**, so an agent can call the yield engine as a tool instead of doing
arithmetic in a prompt.

---

## 2. The three OpenEdge roles (only one needs the kit installed)

| # | Direction | What it is | State on this machine | Code |
|---|---|---|---|---|
| A | **Local read bridge** (the demo path) | Reads rows out of the shop's own OpenEdge database by running `06-enquiry-json.p` under `prowin` | **Ready.** `blockers: []`, 5 rows read in 0.35 s | [openedge_bridge.py](../backend/openedge_bridge.py), [app.py:1915](../backend/app.py#L1915) |
| B | **Inbound MCP server** | ValueAnchor exposes 6 tools at `POST /mcp` to any MCP host | **Live** whenever the app runs, nothing to install | [mcp_server.py](../backend/mcp_server.py), [app.py](../backend/app.py) |
| C | **Outbound: OpenEdge MCP Server + Progress Agentic RAG Context Box** | Reading a business's records/documents from Progress's own servers | **Not called** (env vars unset) — reported honestly as "not called", never as "broken" | [openedge.py](../backend/openedge.py) |

C is deliberately off. It is software somebody installs and points at their own
database; claiming a default port is a "working connection" would be exactly the
kind of lie this project refuses elsewhere. A and B together are a complete demo
and need nothing beyond the Developers Kit.

---

## 3. What happens when you press **Link this database**

1. **UI** — OpenEdge tab → `POST /api/openedge/link` with `{"database":"coffee"}`
   ([OpenEdgeTab.tsx:158](../frontend/app/OpenEdgeTab.tsx#L158)).
2. **Endpoint** — validates the name (a *name*, never a path), then calls the bridge
   ([app.py:1915](../backend/app.py#L1915)).
3. **Bridge** — locates three things separately and reports which one is missing:
   `prowin.exe`, `06-enquiry-json.p`, and the `coffee` storage file
   ([openedge_bridge.py](../backend/openedge_bridge.py)).
4. **The AVM runs one command**, and cannot be talked into another:

   ```
   prowin.exe -b -db <…>\openedge\coffee.db -1 -p <…>\06-enquiry-json.p
   ```

   `-b` batch (no window), `-1` single-user (a read wants exclusivity, and it
   refuses a database somebody else is serving), `-p` that one program.
5. **The ABL program** does `FOR EACH coffee_item NO-LOCK` and prints one JSON line
   ([06-enquiry-json.p](../openedge/06-enquiry-json.p)). `NO-LOCK` is the whole
   read-only guarantee — there is no write statement in the program at all.
6. **Back in the endpoint** the rows go through the *same* upsert the push API uses,
   then `connect_business(...)` makes that business the one the copilot, planner and
   retrieval corpus answer about, and the local NLU retrains on its vocabulary.
7. **The response** carries what was read (`accepted: 5`, `unitsLeft: 151`,
   `readOnly: true`) which the UI prints verbatim.

Relinking **replaces** the mapped copy rather than appending, and the floor is
re-derived from the pushed lines — so a database whose rows carry no floor prices
reads as *"assumed"*, not as if the owner had set one.

---

## 4. What is already true on this machine

| Thing | Value |
|---|---|
| OpenEdge install | `C:\Progress\OpenEdge` — **12.8**, `bin\prowin.exe`, `prodb.exe`, `sqlexp.bat`, `proserve.bat`, `_mprshut.exe` |
| Starter program | `prowin -b -p openedge\01-hello.p` → *"ValueAnchor: the AVM is up. OpenEdge 12.8 on WIN32 DESKTOP-JACY"* |
| Database | `openedge/coffee.db` (+ `.b1`/`.d1`), seeded with 5 rows |
| Rows read | Kloof Street Roasters — Almond croissant, Chicken mayo baguette, Cold brew 500 ml, Flat white, Sourdough loaf |
| Units on the shelf | 151 |
| Bridge status | `available: true`, `blockers: []` |
| Link latency | 0.35 s once the AVM is warm (first run of the day is slower — the AVM starts visibly) |
| MCP tools served | `valueanchor_guardrails`, `valueanchor_urgent_stock`, `valueanchor_analyze_line`, `valueanchor_pair_lines`, `valueanchor_ask`, `valueanchor_log_decision` |

Nothing else has to be installed, provisioned, or paid for.

---

## 5. Step-by-step plan

### Phase A — the 15 minutes before you record

1. **Open a terminal at the repo root** (`Hackathon Project Submission`). The app
   finds `openedge\coffee.db` relative to the working directory, so this matters.
2. **Confirm the kit answers** (10 seconds, no app involved):

   ```bash
   C:\Progress\OpenEdge\bin\prowin.exe -b -p openedge\01-hello.p
   ```

   Expect `OpenEdge 12.8 on WIN32 …`. If this fails, nothing downstream will work —
   fix that first.
3. **Confirm the database reads** (also no app involved):

   ```bash
   C:\Progress\OpenEdge\bin\prowin.exe -b -db openedge\coffee.db -1 -p openedge\06-enquiry-json.p
   ```

   Expect one JSON line, `"rowCount":5`.
4. **Start the app from the same terminal**, so the working directory is the repo root:

   ```bash
   .\dist\ValueAnchor.exe
   ```

   It opens `http://127.0.0.1:8000` by itself. Keep the console window open.
5. **Reset the workspace to a clean demo state** if you recorded before:
   close the app, delete `dist\valueanchor.db` and `dist\.model_cache`, start again.
   Both are settings/data files, not sources; the app rebuilds them.
6. **Clear the chat** so the recording starts empty: DevTools → Application →
   Local Storage → delete the `valueanchor-session` key, then reload. (A private
   window does the same thing with less clicking.)

### Phase B — only if you must rebuild the database from nothing

Already done here; keep this in case the database is deleted or you demo on another machine.

```bash
cd openedge

prodb coffee empty                                    # 1. create the database
proserve -db coffee -S 5123 -H localhost              # 2. serve it (SQL needs a server)
sqlexp -char -db coffee -S 5123 -H localhost -infile 03-coffee-ddl.sql -outfile 03-coffee-ddl.log
#    ^ creates pub.coffee_item. The `pub.` prefix and the trailing COMMIT; are both
#      mandatory — without them the table is created in your Windows user's schema
#      (ABL cannot see it) or silently rolled back.
_mprshut                                              # 3. stop the server — answer 2
prowin -b -db coffee -1 -p 04-seed-coffee.p            # 4. five lines in
prowin -b -db coffee -1 -p 06-enquiry-json.p           # 5. prove the read
```

Step 3 matters: `04`/`05`/`06` open the database **single-user** and will refuse
with *"in use in multi-user mode"* while `proserve` holds it.

### Phase C — demonstrate the link (what the video shows)

1. OpenEdge tab → the card reads **"ready on this machine"** and **"LIVE NOW"**.
2. Press **Link this database**.
3. Read the four figures out loud: business, **5** lines, **151** units left, active floor.
4. Say the read-only line while pointing at it: *"one ABL program, `FOR EACH … NO-LOCK`, nothing written back."*
5. Switch to Overview — the table is now those rows, and the copilot is grounded in them.

### Phase D — prove read-only if a judge asks

Three independent proofs, cheapest first:

- The tool advertises `readOnlyHint: true` and takes one argument (a database name).
- The only program the bridge will run is `06-enquiry-json.p`, and its only data
  statement is `FOR EACH coffee_item NO-LOCK` ([openedge/06-enquiry-json.p](../openedge/06-enquiry-json.p)).
- Every data statement in the folder is a `NO-LOCK` read — three in total, one per
  program — and `06-enquiry-json.p` has exactly one, at
  [line 194](../openedge/06-enquiry-json.p#L194), with no `CREATE`, `ASSIGN`,
  `UPDATE` or `DELETE` anywhere in it:

  ```bash
  git grep -n "NO-LOCK" -- openedge/
  git grep -niE "\b(delete|update|create|assign)\b" -- openedge/06-enquiry-json.p   # no output
  ```

### Phase E — prove the MCP side

In the app: OpenEdge tab → open **What an agent can call (6)**. The list is read
live from `POST /mcp` on every load, so it cannot go stale.

From a terminal, if you want the raw protocol on screen:

```bash
curl -s -X POST http://127.0.0.1:8000/mcp -H "Content-Type: application/json" \
  -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}"
```

### Phase F — optional: the vendor servers (leave this off unless asked)

Turning on C is a project, not a checkbox, and the app is honest about it today:

1. Install the **OpenEdge MCP Server** distribution, load its Docker images, run
   `./mcpgen up --spec ./openapi.yml` from its profile directory.
2. Put its address in `.env` beside the exe as `OPENEDGE_MCP_URL=http://localhost:8500/http`
   (`OPENEDGE_MCP_TOKEN` only if it is secured).
3. For documents: create a **Context Box** in Progress Agentic RAG, copy the MCP URL
   and a Search-scoped key into `PROGRESS_RAG_MCP_URL` / `PROGRESS_RAG_TOKEN`.
4. Back on the OpenEdge tab → *Outbound connections* → **Check now**. The probe tries
   both protocol eras and reports what the server actually advertises.

Doing this adds *"and it also reads from the vendor MCP server"*, which is a bonus,
not the core. Skipping it costs nothing as long as you say "not configured" rather
than implying it works.

---

## 6. Troubleshooting — each refusal already has its own sentence

| Message you see | What is actually wrong | Fix |
|---|---|---|
| *No AVM found…* | `prowin.exe` not where expected | Set `OPENEDGE_PROWIN` to the full path, or install to `C:\Progress\OpenEdge` |
| *06-enquiry-json.p was not found…* | program missing beside the backend | Run from the repo root, or set `OPENEDGE_HOME` |
| *No storage file for database 'coffee'…* | `coffee.db` not found | `prodb coffee empty` in `openedge\`, or set `OPENEDGE_DB` to its full path |
| *The table is not in 'coffee'…* | table created in the wrong schema, or never created | Run `03-coffee-ddl.sql` — with `pub.` and `COMMIT;` |
| *'coffee' is being served by another process…* | `proserve` still running | `_mprshut`, answer `2` |
| *…has the coffee_item table but no rows…* | DDL ran, seeder did not | `prowin -b -db coffee -1 -p 04-seed-coffee.p` |
| *The AVM did not finish… within 90 seconds* | first cold start on a slow machine | Retry once; raise `OPENEDGE_TIMEOUT` only if it is genuinely slow |

A refusal never shows an empty table — an empty read would look like "nothing to
worry about", which is the dangerous lie.

---

## 7. Claims you can defend, and one you cannot

**Can:**

- Stock is read from a real OpenEdge 12.8 database by a real AVM running real ABL.
- The read is read-only by construction: one program, one `FOR EACH … NO-LOCK`, no
  writable argument.
- The engine is a live MCP server with 6 tools, both protocol eras served.
- Every figure comes from the engine; the LLM (if any) only rewords.
- Runs offline; the only optional network call is a free LLM for phrasing.

**Cannot:**

- *"It integrates with the OpenEdge MCP Server."* It reports that server honestly as
  not configured until you install it (Phase F).
- *"It writes back to OpenEdge."* It does not, by design.
- *"It syncs live on a schedule."* You press Link; it reads then.
