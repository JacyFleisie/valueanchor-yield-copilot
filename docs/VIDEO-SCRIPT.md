# ValueAnchor — demo video script

A shot-by-shot script for the submission recording. Read the **DO** lines as
actions, the **SAY** lines out loud more or less verbatim, and the *timing* column
as a target, not a rule. Total runtime lands around **4:25**.

Everything below was walked through on this machine against the published `v1.1.0`
build, so the on-screen text quoted here is the text you will actually see.

---

## Before you hit record (10 minutes)

1. **Terminal at the repo root** — `Hackathon Project Submission`. The app finds
   `openedge\coffee.db` from the working directory, so this is not optional.
2. **Warm the AVM once** so the first click in the video is not a 20-second pause:

   ```bash
   C:\Progress\OpenEdge\bin\prowin.exe -b -p openedge\01-hello.p
   C:\Progress\OpenEdge\bin\prowin.exe -b -db openedge\coffee.db -1 -p openedge\06-enquiry-json.p
   ```

3. **Fresh demo data** (optional, gives a clean Overview): close the app, delete
   `dist\valueanchor.db` and `dist\.model_cache`, then start it again.
4. **Empty chat**: DevTools → Application → Local Storage → delete
   `valueanchor-session`, reload. Otherwise last night's conversation is on screen.
5. **Floor back at 60%** before you start: Settings → *Use the floor it came with*.
6. **Recording setup**: 1080p, 30 fps, browser window maximised, cursor visible,
   notifications off, one monitor. Do not resize the window mid-take — the table
   reflows and it looks like a glitch.
7. **Have this open in a second window**: the repo page, so the closing shot can
   show it without navigating.

> Small privacy note: the console window prints your data path, which contains your
> Windows user name. If you would rather not show it, record the browser only and
> crop the console, or put the exe in a short path like `C:\demo\`.

---

## 0:00 – 0:18 · Cold open: one file, double-click

**DO**
- Start on the folder with `ValueAnchor.exe`.
- Double-click it. A console window appears, then the browser opens by itself.
- Let the console be visible for two seconds: it prints `ValueAnchor is running at
  http://127.0.0.1:8000`.

**SAY**
> "This is ValueAnchor. It's one executable — no Python, no Node, no installer, no
> cloud account. Double-click, and the whole product is running on this laptop."

---

## 0:18 – 0:48 · The Overview

**DO**
- Scroll slowly to about the middle of the page and stop with the margin-floor tile
  and the table both visible.

**SAY**
> "This is a coffee shop's morning. Five lines on the counter, one hundred and fifty-one
> units that will be unsold when it closes at the end of the day. The tile top right is
> the one number that governs everything: my margin floor, sixty percent of shelf price.
>
> Everything on this screen is ranked by how little time is left and how slowly it is
> selling. Flat white first — two hours to go, fifty-six percent sold. That's the line
> under the most pressure."

**DO**
- Do not click anything yet. Keep it to one slow scroll.

---

## 0:48 – 1:12 · Raise the floor (this sets up the next shot)

**DO**
- Click **Settings** in the left rail.
- Point at **Minimum retained margin** — a picker, not a text box.
- Choose **80%** → click **Save floor**.
- Stay long enough for the note to appear under the list.

**SAY**
> "Settings is where the policy lives. The floor is a picker, because it's a decision,
> not a typo — forty to eighty percent. I'm going to raise mine from sixty to eighty,
> which is deliberately aggressive, because I want to show you what the engine does
> when a recommendation would break it.
>
> Note what it says underneath: 'Where that floor came from — set by the owner on the
> Settings tab.' The app always states whether the floor is mine or assumed."

---

## 1:12 – 2:05 · The line review — the core of the product

**DO**
- Click **Overview**.
- Click the **Flat white** row.
- The dialog opens on *"Hold the R38 price. Add value instead."* Hold two seconds.
- Scroll inside the dialog to the **tiers** (Tier 1 / 2 / 3).
- Keep scrolling to **WHAT EACH CHOICE KEEPS** — the three scenarios.
- Stop on **Where the bundle wins** and the grey note under it.

**SAY**
> "Click any line and it doesn't give me a discount. It gives me the opposite:
> 'Hold the R38 price. Add value instead.'
>
> Three ways to add value. A free loyalty stamp, funded. A pastry pairing voucher —
> and look, *held back: breaks the floor.* At eighty percent I don't keep enough on
> this line to give that away, so the engine refuses to price it rather than quietly
> suggesting it. Tier three is a credit earned on the next visit, so it costs me
> nothing today.
>
> Further down, the same three choices costed per unit: do nothing, mark it down
> twenty percent, or add value. Every row is flagged 'floor held'. And then the honest
> part — it says the extra units a bundle might sell are *not* estimated, because
> inventing that uplift would make the recommendation win by construction."

**DO**
- Do not click Approve or Decline yet. Press **Escape** to close.

---

## 2:05 – 2:50 · Ask in plain words

**DO**
- Click into the copilot box on the Overview.
- Type exactly: `What should I bundle with the flat white?`
- Press Enter. Let the answer land, then read the first two lines.
- Click **Where this came from** to open the retrieved records, then close it again.

**SAY**
> "Now the same question in my own words. It routes locally — no cloud round-trip to
> understand me — and answers from the live numbers: keep the Flat white at R38, bundle
> a free loyalty stamp, and the pastry voucher is held back because it would push
> margin under the eighty percent floor.
>
> 'Where this came from' lists the records it actually retrieved — the live lines, the
> perks, my own rule, the glossary. Every figure is engine output. A language model may
> reword this, but it never multiplies a price by a percentage and hopes."

**DO**
- Optional, 5 seconds: hover the severity chip at the top of the answer so the tooltip
  shows. Do not open every fold — the video gets long.

---

## 2:50 – 3:40 · OpenEdge: where the stock actually comes from

**DO**
- Click **OpenEdge** in the rail.
- Point at **LIVE NOW**, the endpoint `http://127.0.0.1:8000/mcp`, and the four protocol
  revisions.
- Open **What an agent can call (6)** and let the six tool names show.
- Close it, then press **Link this database**.
- Let the four figures appear: business, **5** lines, **151** units, **60%** floor.

**SAY**
> "Two directions, and both are real. ValueAnchor is an MCP server — an agent can call
> the yield engine as six tools instead of guessing. That surface is live right now,
> and this list is read from the endpoint on every load, not hard-coded.
>
> The other direction is where the stock comes from. Press Link, and it runs one ABL
> program against the shop's own OpenEdge database — a single `FOR EACH` with `NO-LOCK`.
> Five lines, a hundred and fifty-one units, read out of OpenEdge 12.8 in under half a
> second. Nothing was written back; the database stays the system of record, and this
> is a mapped copy.
>
> And it tells me the floor dropped back to sixty percent, *assumed* — because the rows
> that came out of OpenEdge don't carry a floor. It won't pretend otherwise."

---

## 3:40 – 4:10 · Optional: prove the AVM with your own eyes

Skip this if you are short on time; the link above already proved it. It is worth 20
seconds because it shows it is a real database, not a fixture.

**DO**
- Switch to the terminal and run:

  ```bash
  C:\Progress\OpenEdge\bin\prowin.exe -b -p openedge\01-hello.p
  C:\Progress\OpenEdge\bin\prowin.exe -b -db openedge\coffee.db -1 -p openedge\06-enquiry-json.p
  ```

- Let the second command's single JSON line print. Do not scroll.

**SAY**
> "Same machine, no app involved: OpenEdge 12.8 reporting itself, then the exact read
> the button just did — one JSON document, five rows. That's the whole bridge. One
> program, no writes, no credentials."

---

## 4:10 – 4:25 · Close

**DO**
- Switch back to the browser. Let the footer show: `ValueAnchor v1.1.0 · ZAR Kloof
  Street Roasters · All systems operational`.
- Cut to the repo page (or a title card with the repo URL).

**SAY**
> "Every number here is traceable, the floor survives or the recommendation isn't
> made, and it all runs offline on one laptop. ValueAnchor — protect the price floor,
> capture the next sale."

---

## Exact things to type (so nothing is fat-fingered)

| Where | Type |
|---|---|
| Copilot box | `What should I bundle with the flat white?` |
| Settings floor | pick `80%`, then click **Save floor** |
| Terminal 1 | `C:\Progress\OpenEdge\bin\prowin.exe -b -p openedge\01-hello.p` |
| Terminal 2 | `C:\Progress\OpenEdge\bin\prowin.exe -b -db openedge\coffee.db -1 -p openedge\06-enquiry-json.p` |

---

## If something goes wrong mid-take

- **OpenEdge tab says "not on this machine" / blockers listed.** You launched the exe
  from the wrong folder. Close it, `cd` to the repo root, run `.\dist\ValueAnchor.exe`
  again. The tab names the missing piece — say that sentence out loud instead of
  skipping it, it is one of the better moments in the product.
- **The AVM is slow on the first click** (10–20 s). Let it run; the button says
  *"Reading the database…"*. Do not click twice.
- **Port 8000 busy.** The app moves to the next free port and the address bar shows it.
  Nothing to fix.
- **The floor stayed at 80 for the OpenEdge shot.** Fine — the link re-derives it and
  says so; use that line.
- **A question gets answered with "I could read that more than one way".** That is the
  model refusing to guess. Pick the first chip; it teaches the phrasing and answers.

---

## After the take

1. Set the floor back with **Use the floor it came with** if you want the workspace as
   you found it.
2. Stop the app by closing the console window.
3. Check the recording: no secrets, no key values, no personal email on screen, and the
   repo URL legible in the final frame.

---

## Do not say

- "It connects to the OpenEdge MCP Server" — it reports that as *not configured* until
  you install it. Say "it can, and it's honest that it isn't set up here."
- "It syncs automatically." You press Link; it reads then.
- "The AI calculates the margins." The engine does; a model only rewords.
- Any number you have not just seen on screen.
