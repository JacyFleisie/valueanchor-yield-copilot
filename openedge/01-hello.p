/* ---------------------------------------------------------------------------
   The starter kit's first exercise: run one small ABL program.

   Nothing here needs a database, a connection or a schema. If this prints, the
   Developers Kit is installed, the AVM runs, and your PATH or proenv is set up -
   which is the only thing this file is for. Everything else in this folder builds
   on it.

   Run it from a proenv prompt (the shell the kit's installer sets up), from this
   directory, REDIRECTING THE OUTPUT:

       prowin -b -p 01-hello.p > 01-check.out
       type 01-check.out

   (-b is batch: no window. The flags that connect a database - typically
   `-db <name> -1` - are only needed by 02, not here.)

   WHY THE REDIRECT IS NOT OPTIONAL, AND WHY IT LOOKS LIKE THE PROGRAM IS BROKEN

   Run this with its output going straight to an interactive console and it fails
   with a message box, before a single line prints:

       ** Attempt to write with no current output destination. (516)

   Nothing is wrong with the file. In batch mode the AVM has no terminal, so the
   DISPLAY below has no destination to write its FRAME into and refuses rather
   than printing nothing quietly. Given a file or a pipe there IS a destination
   and the same program prints correctly. That is how it was run here - OpenEdge
   12.8, Windows 11, 27 October 2026 - and the redirect form is the one to use.

   This file HAS been run: with the redirect it prints the three lines below and
   exits 0. (`openedge\*.out` holds the captured output of these runs and is
   git-ignored, which is why it is not in the repository.)
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

/* Note this frame is 140 wide and its fields are 132. That is deliberately wider
   than a default console, and it is the reason the run instructions above insist
   on a redirect: a frame is a screen region and the AVM will not lay one out
   against a terminal it does not have in batch mode (error 516), while a file has
   no width to exceed. The narrow-bandwidth alternative for a program whose output
   must reach a terminal is PUT UNFORMATTED, which has no frame at all - see
   06-enquiry-json.p, which writes a 1801-character JSON line that way. */

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
