/* ---------------------------------------------------------------------------
   Read the coffee shop back out of its own database - the shape every
   OpenEdge-backed tool eventually has: connect, FOR EACH, print, and let the
   count be the database's rather than anybody's guess.

   This is 02-read-customer.p's shape with the shop's own table in it, so the
   pre-work and the demo are one story: the kit's exercise proves a database
   read works; this one proves YOUR rows are in it.

   Run it from a proenv prompt:

       prowin -b -db coffee -1 -p 05-read-coffee.p

   The totals at the bottom are the part to look at: units left and value on
   the shelf are summed inside the AVM, over rows this program never typed.
   When ValueAnchor later reasons about this shop, those are the numbers its
   answers must agree with - which is the whole reason OpenEdge sits on the
   record side.

   (Output style matches 01, 02 and 04: one named frame wide enough for the
   text, one DISPLAY. DISPLAYing FORMAT "x(120)" into the default batch frame
   is refused with error 4028, and repeated DISPLAYs of the same frame field
   overwrite each other rather than printing.)
   --------------------------------------------------------------------------- */

DEFINE VARIABLE iRows      AS INTEGER NO-UNDO.
DEFINE VARIABLE iUnitsLeft AS INTEGER NO-UNDO.
DEFINE VARIABLE cValue     AS DECIMAL NO-UNDO.
DEFINE VARIABLE cHdr       AS CHARACTER NO-UNDO.
DEFINE VARIABLE cGap       AS CHARACTER NO-UNDO.
DEFINE VARIABLE cTot       AS CHARACTER NO-UNDO.
DEFINE VARIABLE ca         AS CHARACTER EXTENT 5 NO-UNDO.  /* one field per row */
DEFINE VARIABLE cLine      AS CHARACTER NO-UNDO.

DEFINE FRAME msgLine
  cLine FORMAT "x(132)"
WITH FRAME msgLine WIDTH 140 NO-LABELS.

IF NOT CONNECTED("coffee") THEN DO:
  cLine = "No database connected. Add `-db coffee -1` to your prowin command.".
  DISPLAY cLine WITH FRAME msgLine.
  RETURN.
END.

DEFINE FRAME outLines
  cHdr FORMAT "x(132)" SKIP
  ca[1] FORMAT "x(132)" SKIP
  ca[2] FORMAT "x(132)" SKIP
  ca[3] FORMAT "x(132)" SKIP
  ca[4] FORMAT "x(132)" SKIP
  ca[5] FORMAT "x(132)" SKIP
  cGap FORMAT "x(132)" SKIP
  cTot FORMAT "x(132)"
WITH FRAME outLines WIDTH 140 NO-LABELS.

FOR EACH coffee_item NO-LOCK BY coffee_item.ItemName:
  iRows      = iRows + 1.
  iUnitsLeft = iUnitsLeft + (coffee_item.TotalCapacity - coffee_item.UnitsSold).
  cValue     = cValue
             + ((coffee_item.TotalCapacity - coffee_item.UnitsSold)
                * coffee_item.BasePrice).

  IF iRows <= 5 THEN
    ca[iRows] = STRING(coffee_item.TotalCapacity - coffee_item.UnitsSold, ">>>9")
              + " left of " + STRING(coffee_item.TotalCapacity, ">>>9")
              + "  " + STRING(coffee_item.BasePrice, ">>>9.99")
              + "  in " + STRING(coffee_item.HoursUntilClosed, ">>")
              + "h  " + coffee_item.ItemName
              + " (" + coffee_item.Category + ")".
END.

cHdr = "Kloof Street Roasters - counter stock, as the database holds it:".
cGap = "----------------------------------------------------------------------".
cTot = "Read " + STRING(iRows) + " lines; " + STRING(iUnitsLeft)
     + " units left, worth " + STRING(cValue, ">>>>9.99") + " on the shelf.".

DISPLAY cHdr ca[1] ca[2] ca[3] ca[4] ca[5] cGap cTot WITH FRAME outLines.

/* The two counts above were summed by the AVM over rows it walked, not handed
   to this program. That distinction is the point: a model never gets to
   estimate them, and when ValueAnchor's copilot answers about this shop, it
   has to agree with this screen.
   --------------------------------------------------------------------------- */
