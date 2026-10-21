/* ---------------------------------------------------------------------------
   Reading records out of a database with ABL - the second thing the kit asks you
   to do before Monday, and the shape every OpenEdge-backed tool eventually has:
   connect, read rows, print what you found.

   It runs against Sports2000, the demonstration database the Developers Kit ships
   with, so it works before you have a database of your own.

   Run it from a proenv prompt:

       prowin -b -db sports2000 -1 -p 02-read-customer.p

   THREE THINGS ABOUT BATCH OUTPUT, LEARNED BY RUNNING THIS ON WINDOWS 12.8

   Everything below the connect check is arranged around one fact the kit's
   comment version got wrong: in batch mode, DISPLAY writes into a *frame*, and
   the default batch frame is narrower than 120 characters, so the obvious
     DISPLAY cLine FORMAT "x(120)" SKIP.
   is refused outright with error 4028 ("FILL-IN will not fit in FRAME").
   And a frame that has been filled scrolls - so DISPLAYing several times in a
   loop does NOT print several lines: each statement overwrites the same row and
   a redirected run shows only the last one. The shape that works is one frame,
   wide enough, with a field per line, and ONE DISPLAY statement filling all of
   them. That is why the rows land in an array first.

   ALSO: the demo database cannot be opened where it was installed. OpenEdge
   refuses a connection to a database inside $DLC with error 1379, so a copy of
   sports2000.db (with its .pl and storage files) is kept beside this file and
   `-db sports2000` finds it here.

   WHY THE TABLE NAME IS NOT A PARAMETER, HONESTLY

   It would be tidier to pass a table name in and read whatever you asked for. ABL
   will not let you: a buffer is resolved at compile time, so a procedure that reads
   an unknown table at runtime needs a dynamic query and a buffer created by name,
   which is a different (and much longer) program. The kit's point in this exercise
   is to see the shape - connect, FOR EACH, DISPLAY - so this file shows that shape
   plainly and says where the names come from instead of hiding them behind a
   dynamic query you would have to un-pick later.

   So: the table and fields below are Sports2000's. When you have a database of
   your own, change `Customer` and the field names to yours. Nothing else changes.
   --------------------------------------------------------------------------- */

DEFINE VARIABLE iRows    AS INTEGER NO-UNDO.
DEFINE VARIABLE iShown   AS INTEGER NO-UNDO.
DEFINE VARIABLE iMaxRows AS INTEGER NO-UNDO INITIAL 10.  /* keep the output readable */
DEFINE VARIABLE cLine    AS CHARACTER NO-UNDO.
DEFINE VARIABLE ca       AS CHARACTER EXTENT 12 NO-UNDO.  /* 10 rows, a gap, a total */

/* One-line frame for the refusal message, used on the no-database path only. */
DEFINE FRAME msgLine
  cLine FORMAT "x(132)"
WITH FRAME msgLine WIDTH 140 NO-LABELS.

/* Refuse politely rather than failing with a raw error if the database is not
   connected: the most common reason someone copies this file and it does nothing
   is that they forgot the -db flag. */
IF NOT CONNECTED("sports2000") THEN DO:
  cLine = "No database connected. Add `-db sports2000 -1` to your prowin command.".
  DISPLAY cLine WITH FRAME msgLine.
  RETURN.
END.

/* The table, one field per line. An extent lets the loop fill it by number. */
DEFINE FRAME outLines
  ca[ 1] FORMAT "x(132)" SKIP
  ca[ 2] FORMAT "x(132)" SKIP
  ca[ 3] FORMAT "x(132)" SKIP
  ca[ 4] FORMAT "x(132)" SKIP
  ca[ 5] FORMAT "x(132)" SKIP
  ca[ 6] FORMAT "x(132)" SKIP
  ca[ 7] FORMAT "x(132)" SKIP
  ca[ 8] FORMAT "x(132)" SKIP
  ca[ 9] FORMAT "x(132)" SKIP
  ca[10] FORMAT "x(132)" SKIP
  ca[11] FORMAT "x(132)" SKIP
  ca[12] FORMAT "x(132)"
WITH FRAME outLines WIDTH 140 NO-LABELS.

FOR EACH Customer NO-LOCK BY Customer.Name:
  iRows = iRows + 1.
  IF iShown < iMaxRows THEN DO:
    iShown = iShown + 1.
    ca[iShown] = STRING(Customer.CustNum, ">>>>9") + "  "
               + Customer.Name                      + "  "
               + Customer.City.
  END.
END.

ca[11] = "".
ca[12] = "Read " + STRING(iRows) + " rows; showed the first " + STRING(iShown) + ".".

/* One statement, all twelve fields: this is what prints when the run is
   redirected, and it is the shape any batch program here has to use. */
DISPLAY ca[1] ca[2] ca[3] ca[4] ca[5] ca[6] ca[7] ca[8] ca[9] ca[10] ca[11] ca[12]
  WITH FRAME outLines.

/* The number to notice is iRows: it is the count of rows the AVM actually walked,
   not a number this program was told. That distinction is the whole reason the
   hackathon kit puts OpenEdge on the record side - the count comes from the
   database, and a model never gets to estimate it.
   --------------------------------------------------------------------------- */
