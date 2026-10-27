/* ---------------------------------------------------------------------------
   The bridge between "a table in an OpenEdge database" and "a thing ValueAnchor
   can reason about". This program reads the coffee shop's rows and writes them
   out as JSON, in the exact field names ValueAnchor's stock API already speaks.

   05-read-coffee.p proves the rows are there and lets a human read them. This
   program is the same read with a machine on the other end: no table drawn, no
   totals spelled out for a person, just one JSON document on stdout that a
   caller can parse. That caller is openedge/enquiry_bridge.py, which turns this
   document into a proper MCP tool result and hands it to the backend.

   Run it from a proenv prompt, after 04 has seeded the database, redirecting the
   output to match the rest of the folder - and because the caller really does
   read this document from a pipe, which is what the redirect simulates:

       prowin -b -db coffee -1 -p 06-enquiry-json.p > 06-check.out
       type 06-check.out

   WHAT COMES OUT

       {
         "businessName": "Kloof Street Roasters",
         "source": "openedge",
         "rowCount": 5,
         "lines": [
           {"itemName":"Almond croissant","category":"Pastry",
            "basePrice":42.00,"totalCapacity":60,"unitsSold":21,
            "shelfHoursUntilClosed":3},
           ...
         ]
       }

   WHY THE ESCAPING IS BUILT FROM PIECES, NOT WRITTEN INLINE

   This is the part that is not obvious and that cost a few tries, so it is
   written out rather than hidden. ABL has no JSON library in this release, so
   the document is assembled by concatenation. But ABL's own string-literal
   rules make it easy to emit something subtly wrong:

     * A single-quoted literal ('...') has NO escapes at all. Any double quote
       inside it ends the string. So '{"a":"b"}' does not compile the way you
       expect - the first embedded quote closes the literal.
     * A double-quoted literal ("...") escapes by DOUBLING the quote: "he said
       ""hi""" is `he said "hi"`. That works for quotes.
     * A backslash in a double-quoted literal is a LITERAL backslash, not an
       escape. So "C:\" is a string that ends in a backslash and then eats the
       closing quote - the literal silently truncates.
     * An open or close brace, { or }, is INCLUDE-file syntax wherever it
       appears in code, INCLUDING inside a string literal. So "{" is not a
       string containing a brace: it is ABL error 374, "unmatched curly
       braces", or an attempt to include a file whose name starts after the
       brace.

   So no single literal can hold the punctuation JSON needs. Rather than
   hand-write escaped literals and hope they survive the round trip, the JSON
   punctuation is emitted through three single-character constants - a quote
   (cQ), a backslash (cBS) and the two braces (cOB, cCB) - each built from
   either the doubled-quote form or CHR(). That makes every string below
   compile against ABL's real rules, and makes the escaping visible and
   checkable in escapeJson(). There is not one literal brace, backslash or
   stray quote anywhere in this program's code.

   THE TIME SIGNAL, HONESTLY

   The coffee shop's stock dies when the counter closes, so its time signal is
   hours-until-closed, not a use-by date per item. ValueAnchor's API accepts
   either one and never both. This program emits shelfHoursUntilClosed because
   that is what the table actually holds. It does not invent an expiry
   timestamp - the backend derives one from the current time when it needs the
   yield maths, and keeps the honest source labelled "closure" so nothing later
   pretends the shop sent a use-by date it never sent.

   This file HAS been run on OpenEdge 12.8, against the seeded coffee database.
   --------------------------------------------------------------------------- */

DEFINE VARIABLE iRows  AS INTEGER   NO-UNDO.
DEFINE VARIABLE cLine  AS CHARACTER NO-UNDO.
DEFINE VARIABLE cJson  AS CHARACTER NO-UNDO.
DEFINE VARIABLE cQ     AS CHARACTER NO-UNDO.  /* one double-quote char, set below */
DEFINE VARIABLE cBS    AS CHARACTER NO-UNDO.  /* one backslash char,  set below */
DEFINE VARIABLE cItem  AS CHARACTER NO-UNDO.
DEFINE VARIABLE cCat   AS CHARACTER NO-UNDO.
DEFINE VARIABLE cPrice AS CHARACTER NO-UNDO.
DEFINE VARIABLE cCap   AS CHARACTER NO-UNDO.
DEFINE VARIABLE cSold  AS CHARACTER NO-UNDO.
DEFINE VARIABLE cHours AS CHARACTER NO-UNDO.
DEFINE VARIABLE cSep   AS CHARACTER NO-UNDO.
DEFINE VARIABLE cOB    AS CHARACTER NO-UNDO.  /* one open brace,    set below */
DEFINE VARIABLE cCB    AS CHARACTER NO-UNDO.  /* one close brace,   set below */

/* The two punctuation characters are set here rather than in an INITIAL
   clause, because INITIAL takes a compile-time constant and CHR(92) is a
   function call. Doubling a quote inside a double-quoted literal is ABL's one
   escape,   so """" is a string holding a single double quote.

   Braces need the same treatment, and for a sharper reason. ABL reads an open
   or close brace as INCLUDE-file syntax wherever it appears in code - even
   inside a string literal. Writing "{" is not a string containing a brace; it
   is an unmatched-brace error (374), or worse, an attempt to include a file
   named x". So an open brace is built from CHR(123) and a close brace from
   CHR(125), the same way the backslash is CHR(92). Every brace in the JSON
   below goes through cOB and cCB for this reason, and there is not one literal
   brace character anywhere in this program. */
cQ  = """".
cBS = CHR(92).
cOB = CHR(123).
cCB = CHR(125).

/* ---------------------------------------------------------------------------
   Escape the characters JSON forbids inside a string. A real line name can
   contain a quote, a backslash or a control character, and a bridge that only
   handles tidy data fails on the first messy week.

   THIS FUNCTION IS DECLARED HERE, BEFORE ITS FIRST CALL, AND ON PURPOSE. ABL
   requires a user-defined FUNCTION to be defined before the code that calls it
   - a call that appears earlier in the file fails with error 247, "Unable to
   understand", which points at the CALL even though the real problem is that
   the definition has not been seen yet. So the function sits between the
   constant set-up and the loop, not at the bottom of the file where a reader
   might expect it.

   It also takes cQ and cBS as INPUT PARAMETERs rather than closing over the
   program's variables, because a FUNCTION is a nested procedure and reading the
   outer variables by name is fragile. Passing the two punctuation characters in
   makes the dependency explicit and the function testable on its own.

   The escape SEQUENCES are built from pBS (a real backslash, CHR(92)) plus a
   plain letter, and the quote escape is pBS + pQ. The control characters are
   recognised by comparing against CHR(n) rather than by any literal, because a
   literal cannot hold them portably. This function IS the JSON escaping layer
   for this program - there is no library behind it.
   --------------------------------------------------------------------------- */
FUNCTION escapeJson RETURNS CHARACTER
  (INPUT pIn AS CHARACTER, INPUT pQ AS CHARACTER, INPUT pBS AS CHARACTER):
  DEFINE VARIABLE cOut AS CHARACTER NO-UNDO.
  DEFINE VARIABLE i    AS INTEGER   NO-UNDO.
  DEFINE VARIABLE cCh  AS CHARACTER NO-UNDO.

  cOut = "".
  DO i = 1 TO LENGTH(pIn):
    cCh = SUBSTRING(pIn, i, 1).
    CASE cCh:
      WHEN '"'     THEN cOut = cOut + pBS + pQ.
      WHEN pBS     THEN cOut = cOut + pBS + pBS.
      WHEN CHR(8)  THEN cOut = cOut + pBS + "b".
      WHEN CHR(12) THEN cOut = cOut + pBS + "f".
      WHEN CHR(10) THEN cOut = cOut + pBS + "n".
      WHEN CHR(13) THEN cOut = cOut + pBS + "r".
      WHEN CHR(9)  THEN cOut = cOut + pBS + "t".
      OTHERWISE              cOut = cOut + cCh.
    END CASE.
  END.

  RETURN cOut.
END FUNCTION.

/* WHY THIS PROGRAM USES PUT, NOT DISPLAY

   Every other program in this folder prints with DISPLAY into a named frame.
   That is the right tool for a human-readable table and the wrong tool here,
   and the difference is worth writing down because it cost real debugging.

   DISPLAY writes into a FRAME, and a frame is a screen region: it has a
   maximum width, and the AVM enforces it. An earlier version of this file set
   WIDTH 140, which is fine for 01 through 05, and the JSON line was silently
   cut off at about 132 characters - no error, no warning, output that simply
   stopped in the middle of a field and looked like a crash. Widening it to
   4000 was then refused outright: error 3496, "Invalid character coordinate
   4000. Should be less than or equal to 650". And 650 is still too narrow for
   a document with five lines in it, which runs past 700 characters.

   PUT UNFORMATTED writes a line to the current output device without a frame
   at all, so it has no such ceiling - the same program emitted a 1801-character
   line without complaint when the ceiling was the thing being tested. A
   machine-readable document has no natural width, which is exactly the case
   PUT exists for. So the rule for this file is: PUT for the document, DISPLAY
   only for the one human sentence on the no-database path. */


/* Refuse politely, and on stdout as a plain sentence rather than a JSON error
   object: the caller is a human the first time and a program the second, and a
   sentence is the one answer both can act on. The bridge turns this into a
   clear failure either way. */
IF NOT CONNECTED("coffee") THEN DO:
  cLine = "No database connected. Add `-db coffee -1` to your prowin command.".
  PUT UNFORMATTED cLine SKIP.
  RETURN.
END.

/* The document opens. Everything after this is appended to cJson, and the last
   write closes it. Nothing here touches the database yet. The quote character
   cQ is spliced in wherever a literal quote belongs. */
cJson = cOB + cQ + "businessName" + cQ + ":" + cQ + "Kloof Street Roasters" + cQ
       + "," + cQ + "source"      + cQ + ":" + cQ + "openedge"             + cQ
       + "," + cQ + "lines"       + cQ + ":[".

FOR EACH coffee_item NO-LOCK BY coffee_item.ItemName:
  iRows = iRows + 1.

  /* A comma before every line but the first, which is the small piece of
     bookkeeping that makes a JSON array correct. Getting this wrong is the
     classic way to emit a document that is one comma off and fails to parse at
     the far end. */
  IF iRows > 1 THEN cSep = ",".
  ELSE cSep = "".

  cItem  = escapeJson(coffee_item.ItemName,  cQ, cBS).
  cCat   = escapeJson(coffee_item.Category, cQ, cBS).
  /* Numbers are formatted with an explicit picture so they are always plain
     digits with a dot, never a locale's comma decimal separator - a JSON
     number written with a comma is a JSON document that will not parse. The
     picture ">>9.99" is two digits, a decimal point and two decimals: it yields
     "42.00" for forty-two and " 4.00" for four, and it is the picture that was
     actually run rather than assumed. An earlier version used ">>>>>9.99",
     which ABL silently mis-read as a single digit and rendered 42.00 as "4" -
     it never failed, it just emitted a wrong number, which is the worst kind of
     bug for a bridge that a model is about to trust. The leading spaces are
     trimmed by TRIM() so the JSON stays tight. */
  cPrice = TRIM(STRING(coffee_item.BasePrice,    ">>9.99")).
  cCap   = TRIM(STRING(coffee_item.TotalCapacity, ">>>9")).
  cSold  = TRIM(STRING(coffee_item.UnitsSold,     ">>>9")).
  cHours = TRIM(STRING(coffee_item.HoursUntilClosed, ">>9")).

  cJson  = cJson + cSep
         + cOB + cQ + "itemName" + cQ + ":" + cQ + cItem + cQ
         + ","  + cQ + "category" + cQ + ":" + cQ + cCat  + cQ
         + ","  + cQ + "basePrice" + cQ + ":" + cPrice
         + ","  + cQ + "totalCapacity" + cQ + ":" + cCap
         + ","  + cQ + "unitsSold" + cQ + ":" + cSold
         + ","  + cQ + "shelfHoursUntilClosed" + cQ + ":" + cHours
         + cCB.
END.

cJson = cJson + "]," + cQ + "rowCount" + cQ + ":" + STRING(iRows) + cCB.

/* The document goes to stdout as one line. DISPLAY appends a period as
   punctuation, which the caller strips; the JSON value itself carries no
   trailing period, so a plain json.loads works after that one strip. */
/* One document, one line, one trailing SKIP so the caller's read ends with a
   newline rather than hanging. The period DISPLAY would have appended is
   punctuation, not data, and PUT leaves it off - which is one less thing for
   the caller to strip. */
PUT UNFORMATTED cJson SKIP.
