# ValueAnchor — demo video script (2–3 minutes)

A shot-by-shot script for the submission recording. Read the **DO** lines as actions,
the **SAY** lines out loud more or less verbatim, and the *timing* as a target, not a
rule. **Total runtime: about 2:55** — comfortably inside the 2–3 minute brief.

Everything quoted on screen below was walked through on this machine against the
published `v1.1.0` build, so the text you see here is the text you will actually see.
If you are running late, cut section 5 (the optional terminal shot is already gone from
this cut) before you cut the floor moment in section 2 — that is the product.

---

## Before you hit record (~10 minutes of prep, not filmed)

1. **Terminal at the repo root.** The app finds `openedge\coffee.db` from the working
   directory, so this matters:
   ```
   cd "C:\...\valueanchor-yield-copilot"
   .\dist\ValueAnchor.exe
   ```
2. **Warm the AVM once**, so the first click in the video is not a 20-second pause:
   ```
   C:\Progress\OpenEdge\bin\prowin.exe -b -p openedge\01-hello.p
   C:\Progress\OpenEdge\bin\prowin.exe -b -db openedge\coffee.db -1 -p openedge\06-enquiry-json.p
   ```
   You should see **5 rows** come back. If you do, the live database shot will work.
3. **Set the floor to 80% now** — Settings → **Minimum retained margin** → **80%** →
   **Save floor**. Doing it off-camera is what buys you the running time. Section 1
   explains it in one sentence instead of a whole screen visit.
4. **Fresh demo data (optional, cleaner Overview):** close the app, delete
   `dist\valueanchor.db` and `dist\.model_cache`, start it again.
5. **Empty chat:** DevTools → Application → Local Storage → delete `valueanchor-session`,
   then reload. Otherwise last night's conversation is on screen.
6. **Recording setup:** 1080p, 30 fps, browser maximised, cursor visible, notifications
   off, one monitor. Do not resize the window mid-take — the table reflows and it looks
   like a glitch.

> Privacy: the console window prints your data path, which contains your Windows user
> name. Record the browser only and crop the console, or put the exe in `C:\demo\`.

---

## 0:00 – 0:15 · Cold open: one file, double-click  *(15s)*

**DO**
- Start on the folder containing `ValueAnchor.exe`. Double-click it.
- Let the console show for two seconds — it prints `ValueAnchor is running at http://127.0.0.1:8000`.
- The browser opens by itself.

**SAY**
> "This is ValueAnchor. One executable — no Python, no Node, no installer, no cloud
> account. Double-click, and the whole product is running on this laptop."

---

## 0:15 – 0:42 · The Overview  *(27s)*

**DO**
- Scroll slowly to roughly the middle and stop with the margin-floor tile **and** the
  table on screen together.
- Do not click anything yet.

**SAY**
> "This is a coffee shop's morning. Five lines on the counter, a hundred and fifty-one
> units that will be unsold by closing. The tile is the number that governs everything:
> my margin floor, which I've set to eighty percent of shelf price — it's a picker on
> Settings, forty to eighty, because a floor is a decision, not a typo.
>
> Everything is ranked by how little time is left against how slowly it's selling.
> Flat white first — two hours to go, fifty-six percent sold. That's the line under the
> most pressure."

---

## 0:42 – 1:32 · The line review — the heart of the product  *(50s)*

**DO**
- Click the **Flat white** row.
- The dialog opens on *"Hold the R38 price. Add value instead."* Hold two seconds.
- Scroll inside the dialog to the three **tiers**. Stop on **TIER 2 · Partner voucher**,
  which reads **"Held back: breaks the floor"**.
- Continue to **WHAT EACH CHOICE KEEPS** — the three scenarios.
- Stop on **Where the bundle wins**, then the grey note beneath it.

**SAY**
> "Click any line and it doesn't give me a discount. It gives me the opposite: hold the
> R38 price, add value instead.
>
> Three ways to add value. A free loyalty stamp, funded. A pastry voucher — and look,
> *held back: breaks the floor.* At eighty percent I don't keep enough on this line to
> give that away, so the engine refuses to price it rather than quietly suggesting it and
> hoping I don't check. Tier three is a credit earned on the next visit, so it costs me
> nothing today.
>
> Further down, the same three choices costed per unit — do nothing, mark down twenty
> percent, or add value — each flagged *floor held*. And then the honest part: it says the
> extra units a bundle might sell are **not** estimated, because inventing that uplift
> would make the recommendation win by construction."

**DO**
- Press **Escape** to close. Do not click Approve or Decline.

---

## 1:32 – 2:05 · Ask in plain words  *(33s)*

**DO**
- Click into the copilot box on the Overview.
- Type exactly: `What should I bundle with the flat white?` → Enter.
- Let the answer land, read the first line aloud.
- Click **Where this came from**, hold two seconds, close it.

**SAY**
> "Same question, my own words. It routes locally — no cloud round-trip to understand me —
> and answers from the live numbers: keep the flat white at R38, bundle a free loyalty
> stamp, and the partner voucher is held back because it would push margin under the
> eighty percent floor.
>
> 'Where this came from' lists the records it actually retrieved — the live lines, the
> perks, my own rule, the glossary. Every figure is engine output. A language model may
> reword the sentence, but it never multiplies a price by a percentage and hopes."

---

## 2:05 – 2:39 · OpenEdge: where the stock actually comes from  *(34s)*

**DO**
- Click **OpenEdge** in the rail.
- Point at **LIVE NOW**, the endpoint `http://127.0.0.1:8000/mcp`, and the four protocol revisions.
- Open **What an agent can call (6)**, let the six tool names show, close it.
- Press **Link this database**. Let the figures appear: **5** lines, **151** units.

**SAY**
> "Two directions, and both are real. ValueAnchor *is* an MCP server — an agent can call
> the yield engine as six tools instead of guessing a price. That surface is live right
> now; this list is read from the endpoint on every load, not hard-coded.
>
> The other direction is where the stock comes from. Press Link, and it runs one ABL
> program against the shop's own OpenEdge database — a single `FOR EACH`, with `NO-LOCK`.
> Five lines, a hundred and fifty-one units, out of OpenEdge 12.8 in under half a second.
> Nothing is written back. OpenEdge stays the system of record; this is a mapped copy."

---

## 2:39 – 2:55 · Close  *(16s)*

**DO**
- Click **Overview** so the footer is visible: `ValueAnchor v1.1.0 · ZAR Kloof Street
  Roasters · All systems operational`.
- Hold, then cut to the repo page (or a title card with the repo URL).

**SAY**
> "Every number is traceable, the floor survives or the recommendation isn't made, and it
> all runs offline on one laptop. ValueAnchor — protect the price floor, capture the next
> sale."

---

## Exact things to type

| Where | Type |
|---|---|
| Copilot box | `What should I bundle with the flat white?` |
| Settings (prep only) | pick `80%`, then **Save floor** |

Do not type into anything else on camera. There is no command to run live in this cut —
the OpenEdge read happens through the button.

---

## If something goes wrong mid-take

- **OpenEdge tab lists blockers.** You launched the exe from the wrong folder. Close it,
  `cd` to the repo root, run `.\dist\ValueAnchor.exe` again. The tab names the missing
  piece — say that sentence out loud instead of skipping it; it's one of the better
  moments in the product.
- **The AVM is slow on the first click** (10–20 s). Let it run — the button says
  *"Reading the database…"*. Do not click twice.
- **Port 8000 is busy.** The app moves to the next free port and the address bar shows it.
  Nothing to fix.
- **The floor stayed at 60%.** Re-set it to 80% on Settings and start over from 0:42; the
  withheld voucher only appears above the floor's limit.
- **"I could read that more than one way."** That is the model refusing to guess. Pick the
  first chip; it teaches the phrasing and answers.

---

## After the take

1. Upload to YouTube (unlisted) or Drive, set link sharing on, and paste the URL into
   `README.md` at the **Demo video** line at the top and under **## 5. Demo**.
2. Check the recording for secrets: no API keys, no `.env` contents, no personal email,
   and the repo URL legible in the final frame.
3. Optional: set the floor back with **Use the floor it came with**.

---

## Do not say

- **"It connects to the OpenEdge MCP Server."** It reports that as *not configured* until
  you install it. Say *"it can, and it's honest that it isn't set up here."*
- **"It syncs automatically."** You press Link; it reads then.
- **"The AI calculates the margins."** The engine does; a model only rewords.
- Any number you have not just seen on screen. The four figures worth quoting are **R38**,
  **80%**, **5 lines**, **151 units**.
