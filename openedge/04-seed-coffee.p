/* ---------------------------------------------------------------------------
   Seed the coffee shop: five counter lines for "Kloof Street Roasters" - the
   same business ValueAnchor's coffee preset answers about - written into the
   coffee_item table with ABL.

   This is OpenEdge on the record side for real: the numbers ValueAnchor would
   later read are rows the shop's own database holds, not figures typed into a
   demo. The five lines deliberately mirror the product's coffee preset (same
   names, prices, capacities and hours-left), so both halves of the integration
   tell one story instead of two.

   Run it from a proenv prompt, after 03-coffee-ddl.sql has created the table:

       prowin -b -db coffee -1 -p 04-seed-coffee.p

   WHAT THE TWO ABL STATEMENTS MEAN

       CREATE coffee_item.            A blank new row, the way ABL inserts.
       ASSIGN coffee_item.Field = x.  Fills it, one field per line.
   Together they are ABL's version of an INSERT. Everything else in this file
   is bookkeeping: refuse politely if the database is not connected, wipe
   yesterday's rows so the script can be run twice without doubling the shelf,
   and count what was written.

   The wipe-then-write pattern is deliberate, and worth naming: a demo script
   that silently appends is a demo script that one re-run later shows a shelf
   with ten thousand croissants on it.

   (Output style matches 01 and 02: one named frame wide enough for the text,
   one DISPLAY. DISPLAYing FORMAT "x(120)" into the default batch frame is
   refused with error 4028, and repeated DISPLAYs of the same frame field
   overwrite each other rather than printing.)
   --------------------------------------------------------------------------- */

DEFINE VARIABLE iRows AS INTEGER NO-UNDO.
DEFINE VARIABLE cLine AS CHARACTER NO-UNDO.
DEFINE VARIABLE cSum  AS CHARACTER NO-UNDO.
DEFINE VARIABLE cNext AS CHARACTER NO-UNDO.

DEFINE FRAME wide-lines
  cLine FORMAT "x(132)" SKIP
  cSum  FORMAT "x(132)" SKIP
  cNext FORMAT "x(132)"
WITH FRAME wide-lines WIDTH 140 NO-LABELS.

IF NOT CONNECTED("coffee") THEN DO:
  cLine = "No database connected. Add `-db coffee -1` to your prowin command.".
  DISPLAY cLine WITH FRAME wide-lines.
  RETURN.
END.

/* Yesterday's shelf, cleared. Running this twice is then harmless. */
FOR EACH coffee_item:
  DELETE coffee_item.
END.

/* (name, category, price, capacity, sold, hours until the counter closes) */
RUN addLine ("Flat white",           "Espresso bar", 38.00, 140, 92, 2).
RUN addLine ("Almond croissant",     "Pastry",       42.00,  60, 21, 3).
RUN addLine ("Cold brew 500 ml",     "Cold bar",     52.00,  48, 17, 6).
RUN addLine ("Chicken mayo baguette","Kitchen",      68.00,  36, 12, 4).
RUN addLine ("Sourdough loaf",       "Bakery shelf", 65.00,  40, 31, 8).

cLine = "Wrote " + STRING(iRows) + " Kloof Street Roasters lines into coffee_item.".
cSum  = "Read them back with 05-read-coffee.p, or let ValueAnchor link this database.".
cNext = "".

DISPLAY cLine cSum cNext WITH FRAME wide-lines.

/* An internal procedure, called by name above. Keeping the five lines as five
   plain calls (rather than a loop over an array) is what a reader new to ABL
   can follow without un-picking anything. */
PROCEDURE addLine:
  DEFINE INPUT PARAMETER pName     AS CHARACTER NO-UNDO.
  DEFINE INPUT PARAMETER pCategory AS CHARACTER NO-UNDO.
  DEFINE INPUT PARAMETER pPrice    AS DECIMAL   NO-UNDO.
  DEFINE INPUT PARAMETER pCapacity AS INTEGER   NO-UNDO.
  DEFINE INPUT PARAMETER pSold     AS INTEGER   NO-UNDO.
  DEFINE INPUT PARAMETER pHours    AS INTEGER   NO-UNDO.

  CREATE coffee_item.
  ASSIGN coffee_item.ItemName         = pName
         coffee_item.Category         = pCategory
         coffee_item.BasePrice        = pPrice
         coffee_item.TotalCapacity    = pCapacity
         coffee_item.UnitsSold        = pSold
         coffee_item.HoursUntilClosed = pHours.

  iRows = iRows + 1.
END PROCEDURE.
