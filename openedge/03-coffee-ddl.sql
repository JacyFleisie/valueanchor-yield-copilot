-- ---------------------------------------------------------------------------
-- The coffee shop's table, as SQL DDL. This is the one step in this folder
-- that is SQL rather than ABL, because creating a table is the database's job
-- and PRO/SQL (sqlexp) is the batch tool that does it.
--
-- This file HAS been run, on OpenEdge 12.8 (Windows). Everything below the
-- column list is what it took to make it actually work - the three things that
-- are not obvious and that cost a few tries to get right.
--
-- HOW TO RUN IT (three steps, not one)
--
-- sqlexp in character mode talks to the database over JDBC, so it needs a
-- *running server*, not a direct local connection. `sqlexp -db coffee ...`
-- against a database that nobody is serving fails with "You must specify either
-- a URL or a database, port and host." So the real sequence from a proenv
-- prompt is:
--
--     prodb coffee empty                                  1. make the database
--     proserve -db coffee -S 5123 -H localhost            2. serve it (port 5123)
--     sqlexp -char -db coffee -S 5123 -H localhost \
--            -infile 03-coffee-ddl.sql \
--            -outfile 03-coffee-ddl.log                   3. create the table
--
-- Stop the server afterwards with _mprshut (it is interactive: answer `2` for
-- unconditional shutdown), because 04 and 05 open the database single-user and
-- will refuse with "in use in multi-user mode" while it is up.
--
-- The three things that are not obvious, each found by running this:
--
-- 1. YOU MUST WRITE `pub.` IN FRONT OF THE TABLE NAME. sqlexp connects as your
--    Windows user (USER-PC here), and OpenEdge puts an unqualified CREATE TABLE
--    into *that* user's schema. ABL, however, resolves unqualified names in the
--    `pub` schema - so a table created as `CREATE TABLE coffee_item` exists but
--    ABL reports "Unknown or ambiguous table coffee_item (725)". Qualifying it
--    `pub.coffee_item` puts it where both sides can see it.
--
-- 2. YOU MUST END WITH `COMMIT;`. OpenEdge SQL is transactional, and sqlexp
--    does not auto-commit. A CREATE TABLE with no COMMIT is reported as
--    successful ("Errors = 0") and then quietly rolled back when the session
--    ends - the log looks fine, the table simply is not there. The COMMIT below
--    is the difference between a real table and a convincing-looking log.
--
-- 3. `-char` IS REQUIRED for a non-interactive run. Without it sqlexp starts
--    its GUI, which will not do anything useful in a scripted context.
--
-- Run it and then check the table is really there before trusting the log:
--
--     echo "SELECT COUNT(*) AS n FROM pub.coffee_item;" > _v.sql
--     sqlexp -char -db coffee -S 5123 -H localhost -infile _v.sql -outfile _v.log
--
-- WHY THESE COLUMNS, HONESTLY
--
-- The columns are ValueAnchor's stock-push fields, spelled the same way, so the
-- mapping between "a row in OpenEdge" and "a line ValueAnchor reasons about" is
-- 1:1 with no translation layer to get wrong:
--
--     itemName          -> ItemName          the thing on the counter
--     category          -> Category          Espresso bar, Pastry, ...
--     basePrice         -> BasePrice         shelf price, not the floor
--     totalCapacity     -> TotalCapacity     how many were made this morning
--     unitsSold         -> UnitsSold         how many have sold so far
--     shelfHoursUntilClosed -> HoursUntilClosed
--                         the closure time signal: hours until the counter
--                         lapses. A coffee shop's stock dies when it shuts,
--                         so this is its honest time signal - not a use-by
--                         date per item.
--
-- A real system would key this table (an ItemID foreign key, an index on the
-- business id). Left out on purpose: the kit's point here is the shape -
-- connect, read rows, count them - and a key would add ceremony without
-- teaching anything at this step.
-- ---------------------------------------------------------------------------

CREATE TABLE pub.coffee_item (
    ItemName         VARCHAR(60)  NOT NULL,
    Category         VARCHAR(40),
    BasePrice        DECIMAL(9,2) NOT NULL,
    TotalCapacity    INTEGER      NOT NULL,
    UnitsSold        INTEGER      NOT NULL,
    HoursUntilClosed INTEGER      NOT NULL
);

COMMIT;
