/* ---------------------------------------------------------------------------
   The starter kit's first exercise: run one small ABL program.

   Nothing here needs a database, a connection or a schema. If this prints, the
   Developers Kit is installed, the AVM runs, and your PATH or proenv is set up -
   which is the only thing this file is for. Everything else in this folder builds
   on it.

   Run it from a proenv prompt (the shell the kit's installer sets up), from this
   directory:

       prowin -b -p 01-hello.p

   (-b is batch: no window, output to your terminal. The flags that connect a
   database - typically `-db <name> -1` - are only needed by 02, not here.)

   This file has NOT been run in this repository's working copy: OpenEdge is not
   installed on the machine this project was built on, so it is written to be
   honest about what it does and is left for the first machine that has a kit.
   --------------------------------------------------------------------------- */

DEFINE VARIABLE cLine AS CHARACTER NO-UNDO.
DEFINE VARIABLE cDir  AS CHARACTER NO-UNDO.
DEFINE VARIABLE cNext AS CHARACTER NO-UNDO.

/* One wide frame with three rows for the three lines this program prints. Two
   earlier versions of this file were wrong in ways only running it revealed:
   DISPLAYing with FORMAT "x(120)" straight into the default frame was refused
   with error 4028 ("FILL-IN cLine will not fit in FRAME" - the default batch
   frame is narrower than 120 characters), and DISPLAYing three times into the
   SAME frame field printed only the last line, each overwriting the one before.
   Three fields in one named frame fixes both, and is worth showing rather than
   hiding: long output in a batch program needs a frame wide enough and rows of
   its own. */
DEFINE FRAME wide-lines
  cLine FORMAT "x(132)" SKIP
  cDir  FORMAT "x(132)" SKIP
  cNext FORMAT "x(132)"
WITH FRAME wide-lines WIDTH 140 NO-LABELS.

cLine = "ValueAnchor: the AVM is up. OpenEdge " + PROVERSION
      + " on " + OPSYS + " " + STRING(OS-GETENV("COMPUTERNAME"))
      + ", " + STRING(TODAY, "99/99/9999") + " " + STRING(TIME, "HH:MM:SS").

/* The second line is the one that matters for this project: it says where the
   program itself was found, which is the usual reason a procedure is "not found"
   when you are sure you typed the name right. (An earlier version asked for the
   CD environment variable, which only cmd.exe sets - run from any other shell
   it prints "?" rather than a path, so the program's own file name is the
   honest answer that works everywhere.) */
cDir = "Program file: " + THIS-PROCEDURE:FILE-NAME.

cNext = "Now run 02-read-customer.p against the demo database.".

DISPLAY cLine cDir cNext WITH FRAME wide-lines.
