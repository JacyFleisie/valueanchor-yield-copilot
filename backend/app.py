from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import os
import sqlite3
import sys
import threading
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, TypeAdapter, field_validator

from version import VERSION

ROOT = Path(__file__).resolve().parent
# A frozen build (the desktop .exe) unpacks its code and read-only assets into a
# temporary directory that disappears on exit, so anything that has to survive a
# restart - the database, the trained-model cache, an optional `.env` - lives
# beside the executable instead.
FROZEN = bool(getattr(sys, "frozen", False))
BUNDLE = Path(getattr(sys, "_MEIPASS", ROOT))
DATA_ROOT = Path(sys.executable).resolve().parent if FROZEN else ROOT

# The database path is overridable so tests and demos can run against a scratch
# file instead of the working dataset.
DB_PATH = Path(os.environ.get("VALUEANCHOR_DB") or DATA_ROOT / "valueanchor.db")
DEFAULT_SECTOR = "coffee"
# The slug used when the active business is one a business pushed to us through
# /v1 rather than one of the built-in presets. It is deliberately not a key in
# SECTORS: a connected business supplies its own stock, so there is no preset to
# read a name, a noun or a floor from.
CONNECTED_SECTOR = "connected"
# A line with no time signal still has to live in Inventory, whose
# ExpirationTimestamp is NOT NULL. It is stored long-dated so it reads as "not
# lapsing" rather than as lapsing today; nothing pretends it has a real date.
NO_TIME_SIGNAL_EXPIRY = "9999-12-31T23:59:59+00:00"

# ---------------------------------------------------------------------------
# Sector presets
#
# The product is built for a coffee shop, but nothing here is coffee-specific:
# a sector supplies the inventory it sells, how fast that inventory perishes,
# the perks it can bundle, and the words its manager uses. Adding a sector is
# data, not code.
#
# items: (item name, category, price floor, hours until it perishes, capacity, already sold)
# perks: (perk name, perk type, cost to business, retail value, available stock)
# ---------------------------------------------------------------------------
SECTORS: dict[str, dict[str, Any]] = {
    "coffee": {
        "name": "Coffee shop",
        "businessName": "Kloof Street Roasters",
        "blurb": "Café and roastery · counter stock that perishes daily",
        "inventoryNoun": "batches",
        "itemLabel": "Batch",
        "heroLine": "Clear the counter without cutting the menu price.",
        "sampleQuestion": "Which batch do we move before close?",
        "marginFloorPct": 60,
        "items": [
            ("Flat white", "Espresso bar", 38.0, 2, 140, 92),
            ("Almond croissant", "Pastry", 42.0, 3, 60, 21),
            ("Cold brew 500 ml", "Cold bar", 52.0, 6, 48, 17),
            ("Chicken mayo baguette", "Kitchen", 68.0, 4, 36, 12),
            ("Sourdough loaf", "Bakery shelf", 65.0, 8, 40, 31),
        ],
        "perks": [
            ("Free loyalty stamp", "SoftInventory", 0, 14, 600),
            ("Size upgrade on the next cup", "SoftInventory", 4, 12, 400),
            ("Pastry pairing voucher", "PartnerVoucher", 12, 35, 240),
            ("Next-visit brew credit", "FlexCredit", 28, 60, 150),
        ],
    },
    "bakery": {
        "name": "Bakery",
        "businessName": "Yeast & Crumb",
        "blurb": "Retail bakery · goods sold on the day they are baked",
        "inventoryNoun": "bakes",
        "itemLabel": "Bake",
        "heroLine": "Sell the last bake at full price.",
        "sampleQuestion": "Which bake clears before we close?",
        "marginFloorPct": 55,
        "items": [
            ("Cinnamon bun", "Pastry", 34.0, 2, 80, 22),
            ("Chocolate éclair", "Cakes", 46.0, 3, 48, 15),
            ("Sourdough loaf", "Breads", 58.0, 4, 60, 54),
            ("Spinach and feta pie", "Savouries", 52.0, 6, 40, 33),
            ("Rye michel", "Breads", 72.0, 10, 30, 12),
        ],
        "perks": [
            ("Cookie with every loaf", "SoftInventory", 0, 16, 500),
            ("Free coffee with any pastry", "SoftInventory", 9, 30, 300),
            ("Weekend brunch voucher", "PartnerVoucher", 22, 65, 180),
            ("Next-order bake credit", "FlexCredit", 35, 80, 120),
        ],
    },
    "hospitality": {
        "name": "Hotel",
        "businessName": "Aloe Ridge Hotel",
        "blurb": "Hotel and dining · rooms, covers and treatments",
        "inventoryNoun": "inventory",
        "itemLabel": "Item",
        "heroLine": "Fill tonight without dropping the rate.",
        "sampleQuestion": "Which room or cover needs attention first?",
        "marginFloorPct": 65,
        "items": [
            ("Sunset deck table", "Dining", 320.0, 3, 30, 7),
            ("Spa treatment", "Spa", 780.0, 6, 16, 5),
            ("Breakfast covers", "Breakfast", 185.0, 12, 90, 41),
            ("Standard room", "Rooms", 1450.0, 24, 24, 14),
            ("Conference day rate", "Events", 950.0, 48, 12, 9),
        ],
        "perks": [
            ("Late checkout", "SoftInventory", 0, 260, 80),
            ("Welcome drink on arrival", "SoftInventory", 35, 90, 200),
            ("Spa partner voucher", "PartnerVoucher", 180, 420, 90),
            ("Return-stay credit", "FlexCredit", 480, 950, 60),
        ],
    },
    "wellness": {
        "name": "Salon and wellness",
        "businessName": "Salt & Steam Studio",
        "blurb": "Appointment business · empty slots cannot be resold",
        "inventoryNoun": "slots",
        "itemLabel": "Slot",
        "heroLine": "Fill today's slots without discounting the treatment.",
        "sampleQuestion": "Which slot are we losing today?",
        "marginFloorPct": 62,
        "items": [
            ("Sauna slot", "Heat", 240.0, 2, 24, 6),
            ("Deep tissue massage", "Bodywork", 690.0, 4, 12, 3),
            ("Group reformer class", "Classes", 210.0, 5, 20, 14),
            ("Signature facial", "Skin", 620.0, 8, 10, 4),
        ],
        "perks": [
            ("Hot towel ritual add-on", "SoftInventory", 0, 80, 300),
            ("Aromatherapy upgrade", "SoftInventory", 25, 70, 220),
            ("Partner smoothie voucher", "PartnerVoucher", 40, 95, 160),
            ("Next-session credit", "FlexCredit", 90, 200, 100),
        ],
    },
    "retail": {
        "name": "Fresh retail",
        "businessName": "Corner Greens Market",
        "blurb": "Grocer · fresh lines with a short shelf life",
        "inventoryNoun": "fresh lines",
        "itemLabel": "Line",
        "heroLine": "Move the short-dated stock without slashing price.",
        "sampleQuestion": "Which fresh line is closest to waste?",
        "marginFloorPct": 50,
        "items": [
            ("Strawberries 250 g", "Produce", 55.0, 2, 48, 12),
            ("Baby spinach 200 g", "Produce", 32.0, 3, 60, 18),
            ("Chicken breast 500 g", "Deli", 95.0, 5, 36, 21),
            ("Rye loaf", "Bakery", 50.0, 6, 40, 33),
            ("Milk 2 L", "Dairy", 45.0, 8, 80, 71),
        ],
        "perks": [
            ("Free recipe card", "SoftInventory", 0, 10, 600),
            ("Reusable bag", "SoftInventory", 6, 25, 400),
            ("Partner coffee voucher", "PartnerVoucher", 20, 55, 200),
            ("Next-shop credit", "FlexCredit", 30, 70, 140),
        ],
    },
    "travel": {
        "name": "Travel and transport",
        "businessName": "Coastal Line Coach",
        "blurb": "Coach and tours · departure-bound capacity",
        "inventoryNoun": "departures",
        "itemLabel": "Departure",
        "heroLine": "Sell the last seats without a fire sale.",
        "sampleQuestion": "Which departure needs attention first?",
        "marginFloorPct": 60,
        "items": [
            ("Winelands day tour", "Day tour", 1250.0, 3, 16, 4),
            ("Garden Route seat", "Coach", 400.0, 5, 48, 31),
            ("Airport transfer", "Transfer", 350.0, 6, 30, 21),
            ("Cape Town lounge pass", "Lounge", 180.0, 10, 40, 38),
            ("Overnight stopover", "Hotel room", 1450.0, 24, 24, 9),
        ],
        "perks": [
            ("Priority boarding", "SoftInventory", 0, 180, 500),
            ("Lounge coffee pass", "SoftInventory", 18, 75, 220),
            ("Partner wine tasting", "PartnerVoucher", 120, 320, 140),
            ("Next-trip Flex Credit", "FlexCredit", 45, 100, 120),
        ],
    },
}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    # Honest /v1 integration surface: ensure the owned-store table exists so the
    # read path is never started against a DB that lacks it.
    db = connect()
    try:
        from api_v1 import _ensure_v1_store
        _ensure_v1_store(db)
    finally:
        db.close()
    db = connect()
    try:
        refreshed = refresh_stale_sector(db)
    finally:
        db.close()
    if refreshed:
        print(
            f"[ValueAnchor] every line in '{refreshed}' had lapsed, so its stock was reseeded "
            "to keep the workspace usable (logged decisions were kept)",
            flush=True,
        )
    # Train the language model for the active business before serving, so the
    # first question is not the one that pays the few seconds of training.
    started = time.perf_counter()
    db = connect()
    try:
        profile, _rows, items, _perks, _history = load_context(db)
        learned = memory.training_examples(db, profile["sector"])
    finally:
        db.close()
    if items:
        db = connect()
        try:
            engine = engine_for(profile, items, learned, db)
        finally:
            db.close()
        card = engine.model_card()
        print(
            f"[ValueAnchor] local NLU ready for '{profile['sector']}': "
            f"{len(engine.classes)} intents, {card['training']['rows']} training rows "
            f"({card['training']['learnedRows']} learned here), "
            f"holdout {card['scores']['holdoutAccuracy']:.0%}, "
            f"{time.perf_counter() - started:.1f}s",
            flush=True,
        )
        print(
            "[ValueAnchor] assistant: "
            + (
                f"enabled with free models {', '.join(_assistant().chain[:2])}"
                if _openrouter_key()
                else "offline (no OPENROUTER_API_KEY set) - the copilot answers fully without it"
            ),
            flush=True,
        )
    yield


app = FastAPI(title="ValueAnchor Yield Copilot", version=VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Honest /v1 integration surface for a mobile app or backend to call.

# This is the owned mapped store and read path a mobile app actually integrates
# against. It is intentionally separate from the copilot's /api/* surface: the
# copilot answers questions; /v1 is the honest integration contract.
from api_v1 import router as v1_router  # noqa: E402
import api_v1  # noqa: E402

app.include_router(v1_router)


class DecisionRequest(BaseModel):
    """Field names match the dashboard's JSON exactly.

    Aliases are deliberately avoided: FastAPI 0.115 re-applies field metadata
    when building the body schema, which makes Pydantic 2.14 warn about every
    aliased field. Matching the wire format removes the need for translation.
    """

    itemId: int
    recommendedAction: str
    managerDecision: str
    unitsFilled: int = 0
    marginRetained: float

    @field_validator("managerDecision")
    @classmethod
    def known_decision(cls, value: str) -> str:
        if value not in {"Approved", "Modified", "Declined"}:
            raise ValueError("managerDecision must be Approved, Modified or Declined")
        return value


class CurrencyRequest(BaseModel):
    currencyCode: str
    currencySymbol: str

    @field_validator("currencyCode")
    @classmethod
    def iso_code(cls, value: str) -> str:
        code = value.strip().upper()
        if len(code) != 3 or not code.isalpha():
            raise ValueError("currencyCode must be a three-letter ISO code")
        return code

    @field_validator("currencySymbol")
    @classmethod
    def symbol(cls, value: str) -> str:
        symbol = value.strip()
        if not 1 <= len(symbol) <= 3:
            raise ValueError("currencySymbol must be one to three characters")
        return symbol


class GuardrailRequest(BaseModel):
    """An owner moving the one number the whole product is built around.

    ``null`` is a real instruction rather than a missing field: it means go back
    to the floor this workspace came with - the sector preset, or the floor the
    connected business supplied when it pushed its stock.
    """

    marginFloorPct: float | None = Field(default=None, ge=10.0, le=95.0)


class CopilotRequest(BaseModel):
    """A question for the copilot, plus the session it belongs to.

    The session is what lets a follow-up like "what about that one" resolve to
    the item discussed a moment ago, so it is part of the request rather than
    hidden server state keyed on nothing.
    """

    question: str
    sessionId: str = "default"
    intent: str | None = None

    @field_validator("intent")
    @classmethod
    def known_intent(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        slug = value.strip().lower()
        if slug not in INTENT_LABELS:
            raise ValueError(f"Unknown intent '{value}'")
        return slug

    @field_validator("question")
    @classmethod
    def usable_question(cls, value: str) -> str:
        question = value.strip()
        if not question:
            raise ValueError("question must not be empty")
        if len(question) > 400:
            raise ValueError("question must be 400 characters or fewer")
        return question

    @field_validator("sessionId")
    @classmethod
    def usable_session(cls, value: str) -> str:
        session = (value or "default").strip()
        return session[:64] or "default"


def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def ensure_columns(db, table: str, columns: dict[str, str]):
    present = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
    for name, ddl in columns.items():
        if name not in present:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def seed_sector(db, slug: str):
    """Replace inventory and perks with the active sector's lines.

    Logged decisions are deliberately left alone: history keeps the label it
    was recorded with, so switching sector never rewrites the audit trail.
    """
    sector = SECTORS[slug]
    now = datetime.now(timezone.utc)
    db.execute("DELETE FROM Inventory")
    db.execute("DELETE FROM Perks")
    db.executemany(
        "INSERT INTO Inventory (ItemID, Sector, ItemName, Category, BasePrice, ExpirationTimestamp, TotalCapacity, UnitsSold, BookingVelocity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (100 + index, slug, name, category, price, (now + timedelta(hours=window)).isoformat(), capacity, sold, 0.0)
            for index, (name, category, price, window, capacity, sold) in enumerate(sector["items"], start=1)
        ],
    )
    db.executemany(
        "INSERT INTO Perks (PerkID, PerkName, Type, CostToBusiness, RetailValue, AvailableStock) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (index, name, kind, cost, value, stock)
            for index, (name, kind, cost, value, stock) in enumerate(sector["perks"], start=1)
        ],
    )


def init_db():
    db = connect()
    db.executescript("""
    CREATE TABLE IF NOT EXISTS Inventory (ItemID INTEGER PRIMARY KEY, Category TEXT NOT NULL, BasePrice REAL NOT NULL, ExpirationTimestamp TEXT NOT NULL, TotalCapacity INTEGER NOT NULL, UnitsSold INTEGER NOT NULL, BookingVelocity REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS Perks (PerkID INTEGER PRIMARY KEY, PerkName TEXT NOT NULL, Type TEXT NOT NULL, CostToBusiness REAL NOT NULL, RetailValue REAL NOT NULL, AvailableStock INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS StrategyHistory (StrategyID INTEGER PRIMARY KEY AUTOINCREMENT, ItemID INTEGER NOT NULL, RecommendedAction TEXT NOT NULL, ManagerDecision TEXT NOT NULL, UnitsFilled INTEGER NOT NULL, MarginRetained REAL NOT NULL, CreatedTimestamp TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS Profile (ProfileID INTEGER PRIMARY KEY CHECK (ProfileID = 1), SectorSlug TEXT NOT NULL, CurrencyCode TEXT NOT NULL, CurrencySymbol TEXT NOT NULL, UpdatedTimestamp TEXT NOT NULL);
    """)
    ensure_columns(db, "Inventory", {"Sector": "TEXT", "ItemName": "TEXT"})
    ensure_columns(db, "StrategyHistory", {"ItemLabel": "TEXT", "Category": "TEXT", "Sector": "TEXT"})
    # A connected business needs to remember its own name and floor, because there
    # is no preset to read them from.
    ensure_columns(db, "Profile", {"BusinessName": "TEXT", "Connected": "INTEGER DEFAULT 0", "ConnectedFloorPct": "REAL", "ConnectedFloorSource": "TEXT"})
    # An owner-set floor is stored separately from the one a workspace came with,
    # so the original is never overwritten and can be restored by clearing it.
    ensure_columns(db, "Profile", {"OwnerFloorPct": "REAL"})
    db.execute("UPDATE Inventory SET ItemName = Category WHERE ItemName IS NULL OR ItemName = ''")
    db.execute("UPDATE Inventory SET Sector = ? WHERE Sector IS NULL OR Sector = ''", (DEFAULT_SECTOR,))
    db.execute("""
        UPDATE StrategyHistory SET
            ItemLabel = COALESCE(ItemLabel, (SELECT i.ItemName FROM Inventory i WHERE i.ItemID = StrategyHistory.ItemID)),
            Category  = COALESCE(Category,  (SELECT i.Category FROM Inventory i WHERE i.ItemID = StrategyHistory.ItemID)),
            Sector    = COALESCE(Sector,    (SELECT i.Sector   FROM Inventory i WHERE i.ItemID = StrategyHistory.ItemID))
        WHERE ItemLabel IS NULL OR Category IS NULL OR Sector IS NULL
    """)

    fresh = db.execute("SELECT 1 FROM Profile WHERE ProfileID = 1").fetchone() is None
    if fresh:
        db.execute(
            "INSERT INTO Profile (ProfileID, SectorSlug, CurrencyCode, CurrencySymbol, UpdatedTimestamp) VALUES (1, ?, ?, ?, ?)",
            (DEFAULT_SECTOR, "ZAR", "R", datetime.now(timezone.utc).isoformat()),
        )
    if fresh or db.execute("SELECT COUNT(*) FROM Inventory").fetchone()[0] == 0:
        seed_sector(db, db.execute("SELECT SectorSlug FROM Profile WHERE ProfileID = 1").fetchone()["SectorSlug"])
    # The learning tables live in the same file, so a backup or a copy of the
    # workspace carries the accumulated learning with it.
    memory.init_memory(db)
    # The model registry lives here too, so which models the assistant may use
    # survives a restart exactly like the decisions and the learning do.
    registry.init_models(db)
    db.commit()
    db.close()


def refresh_stale_sector(db) -> str | None:
    """Reseed the active sector when every line has already lapsed.

    Seeded stock is dated from the moment it is written, so a workspace left
    overnight would open with every line expired - and a copilot that answers
    "0.0 hours left" about everything teaches the owner nothing. Logged decisions
    are deliberately untouched: the audit trail keeps its own history even when
    the working set is refreshed.
    """
    rows = db.execute("SELECT ExpirationTimestamp FROM Inventory").fetchall()
    if not rows:
        return None
    if any(hours_remaining(row["ExpirationTimestamp"]) > 0 for row in rows):
        return None
    active = db.execute("SELECT SectorSlug, Connected FROM Profile WHERE ProfileID = 1").fetchone()
    if active["Connected"]:
        # A connected business's stock is the business's, not ours to replace with a
        # preset. Re-seeding here would silently throw away the pushed workspace and
        # answer questions about a coffee shop that the owner never asked about.
        return None
    seed_sector(db, active["SectorSlug"])
    db.commit()
    return active["SectorSlug"]


def sector_preset(slug: str) -> dict[str, Any]:
    if slug not in SECTORS:
        raise HTTPException(404, f"Unknown sector '{slug}'")
    return SECTORS[slug]


FLOOR_STATED = "stated by the client"
FLOOR_DERIVED = "derived from the floors the pushed lines carried"
FLOOR_ASSUMED = "assumed, because the pushed lines carried no floor"
FLOOR_PRESET = "the sector preset for this workspace"
# The floor is the one number the product is built around, so it is also the one
# an owner is allowed to move. Which of these happened to it is reported next to
# the number itself, everywhere the profile is read, so a floor that was assumed
# can never read as a floor the business stated.
FLOOR_OWNER = "set by the owner on the Settings tab"


def connected_profile(row) -> dict[str, Any]:
    """The profile for a business whose stock was read out of its own database.

    There is no preset behind it, so every field comes from the link itself or
    from an honest default. The floor is the one field with a real risk of being
    invented or mis-described, so which of the three things happened to it is
    stored at connect time and repeated here in the blurb, in the words the API
    returns to the client as ``floorSource``.
    """
    name = row["BusinessName"] or "Connected business"
    supplied = row["ConnectedFloorPct"]
    owner = row["OwnerFloorPct"]
    floor_pct = int(round(supplied)) if supplied is not None else 60
    source = row["ConnectedFloorSource"] or FLOOR_ASSUMED
    if owner is not None:
        # An owner who set a floor on the Settings tab outranks the one the link
        # carried: a person deciding about their own business beats a default the
        # database happened to imply, and the blurb says which of the two it is.
        floor_pct = int(round(owner))
        source = FLOOR_OWNER
        blurb = f"Linked business - stock read from OpenEdge, {floor_pct}% floor set by the owner"
    elif source == FLOOR_STATED:
        blurb = f"Linked business - stock read from OpenEdge, {floor_pct}% floor stated by the client"
    elif source == FLOOR_DERIVED:
        blurb = f"Linked business - stock read from OpenEdge, {floor_pct}% floor derived from the linked lines"
    else:
        blurb = (
            "Linked business - stock read from OpenEdge, with no margin floor supplied, "
            "so a 60% floor is assumed until one is given"
        )
    return {
        "sector": CONNECTED_SECTOR,
        "name": name,
        "businessName": name,
        "blurb": blurb,
        "inventoryNoun": "lines",
        "itemLabel": "Line",
        "heroLine": "Capture the next sale.",
        "sampleQuestion": "Which line needs attention first?",
        "marginFloorPct": floor_pct,
        "floorSource": source,
        "floorOwnerSet": owner is not None,
        # A pushed business is not a preset, and a screen that cannot tell the two
        # apart will offer to switch away from the owner's own stock and call the
        # pushed name a "coffee shop preset". So the distinction is reported.
        "connected": True,
        "currencyCode": row["CurrencyCode"],
        "currencySymbol": row["CurrencySymbol"],
    }


def read_profile(db) -> dict[str, Any]:
    row = db.execute("SELECT * FROM Profile WHERE ProfileID = 1").fetchone()
    if row["Connected"]:
        return connected_profile(row)
    preset = sector_preset(row["SectorSlug"])
    owner = row["OwnerFloorPct"]
    floor_pct = int(round(owner)) if owner is not None else preset["marginFloorPct"]
    return {
        "sector": row["SectorSlug"],
        "name": preset["name"],
        "businessName": preset["businessName"],
        "blurb": preset["blurb"],
        "inventoryNoun": preset["inventoryNoun"],
        "itemLabel": preset["itemLabel"],
        "heroLine": preset["heroLine"],
        "sampleQuestion": preset["sampleQuestion"],
        "marginFloorPct": floor_pct,
        "floorSource": FLOOR_OWNER if owner is not None else FLOOR_PRESET,
        "floorOwnerSet": owner is not None,
        "connected": False,
        "currencyCode": row["CurrencyCode"],
        "currencySymbol": row["CurrencySymbol"],
    }


# Yield maths lives in exactly one module so the HTTP API, the copilot planner
# and the tests can never disagree about a floor or an urgency score.
import mcp_server  # noqa: E402
import re

import openedge  # noqa: E402
import openedge_bridge  # noqa: E402
from pydantic import ValidationError  # noqa: E402
import yield_engine  # noqa: E402
from yield_engine import bundle_plan, hours_remaining, inventory_item, load_perks  # noqa: E402
from nlu import IntentEngine, build_lexicon  # noqa: E402
from nlu import memory  # noqa: E402
from nlu import models as registry  # noqa: E402
from nlu.assistant import AssistantResult, CopilotAssistant  # noqa: E402
from nlu.dataset import INTENT_LABELS  # noqa: E402
from nlu.knowledge import KnowledgeBase  # noqa: E402
from nlu.planner import plan as plan_answer  # noqa: E402


# ---------------------------------------------------------------------------
# Local NLU wiring
#
# One intent engine per (sector, inventory) shape. Training takes a few seconds,
# so the active sector is trained at startup and any other sector is trained the
# first time it is asked about, then cached. Sessions hold only the focus of the
# conversation so a follow-up like "what about that one" resolves.
# ---------------------------------------------------------------------------
_ENGINES: dict[tuple, IntentEngine] = {}
_ENGINE_LOCK = threading.Lock()
_SESSIONS: dict[str, dict[str, Any]] = {}
_SESSION_LOCK = threading.Lock()
MAX_SESSIONS = 50

# ---------------------------------------------------------------------------
# The retrieval layer and the language assistant
#
# The local engine reads the question and owns every number. Retrieval then
# selects the records that are relevant to it. Only after both of those may the
# assistant restate the answer in plainer words, and even then its wording is
# audited before the owner sees it. With no key configured nothing is lost: the
# copilot still answers, still shows its figures and still refuses off-topic
# questions, because all of that happens here and not in the model.
# ---------------------------------------------------------------------------
def _load_env_file() -> None:
    """Read ``.env`` from beside the executable (or ``backend/``) if it is there.

    Fifteen lines rather than a dependency: this is the only configuration the
    server has, and the project deliberately keeps its installed packages to the
    ones that do real work. An already-set environment variable always wins, so
    a real deployment configures the server the normal way and this is only a
    convenience for running locally. The file is gitignored, so the key is never
    committed and never reaches the browser.
    """
    candidates = [DATA_ROOT / ".env", ROOT / ".env"]
    path = next((candidate for candidate in candidates if candidate.exists()), None)
    if path is None:
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        entry = line.strip()
        if not entry or entry.startswith("#") or "=" not in entry:
            continue
        name, _, value = entry.partition("=")
        os.environ.setdefault(name.strip(), value.strip())


def _openrouter_key() -> str | None:
    """Read the key from the server environment, never from a request body.

    Keeping it out of the API means the browser never holds it, and the key can
    be added or rotated without restarting the server.
    """
    return (os.environ.get("OPENROUTER_API_KEY") or "").strip() or None


_load_env_file()


def _health_recorder():
    """A callback the assistant calls after every attempt, to store what happened.

    It opens its own short-lived connection rather than borrowing the request's,
    because the request has already finished with its connection by the time the
    assistant runs. Recording is best-effort by design: losing a rate-limit note
    must never cost the owner an answer.
    """

    def record(spec: dict, meta: dict, headers: dict) -> None:
        db = connect()
        try:
            status = str(meta.get("status") or "unknown")
            registry.record_health(
                db,
                model_id=str(spec.get("model") or ""),
                provider=str(spec.get("provider") or registry.DEFAULT_PROVIDER),
                status=status,
                detail=str(meta.get("detail") or ""),
                headers=headers,
                limit_source=str(meta.get("limitSource") or ""),
                remedy=str(meta.get("detail") or "") if status == "rate limited" else "",
            )
        finally:
            db.close()

    return record


def _assistant() -> CopilotAssistant:
    """A fresh assistant per call, built from the model registry.

    Reads the registered chain - which models, in what order, against which
    endpoint - so changing a model in the UI takes effect on the next question
    with no restart. Falls back to the environment defaults if the registry has
    never been seeded.
    """
    db = connect()
    try:
        specs = registry.resolve_chain(db)
    finally:
        db.close()
    fallbacks = [
        name.strip()
        for name in (os.environ.get("OPENROUTER_FALLBACK_MODELS") or "").split(",")
        if name.strip()
    ]
    return CopilotAssistant(
        _openrouter_key(),
        os.environ.get("OPENROUTER_MODEL"),
        fallbacks,
        chain_specs=specs if specs else None,
        on_attempt=_health_recorder(),
    )


def build_knowledge(db, profile, rows, items, perks, history) -> KnowledgeBase:
    """The retrievable corpus for this workspace, including what has been learned.

    Rebuilt per request rather than cached: the corpus is the live data, so a
    decision the owner just logged or a question they just confirmed should be
    retrievable immediately.
    """
    learned = memory.training_examples(db, profile["sector"])
    return KnowledgeBase(profile, rows, perks, history, SECTORS, learned)

def load_context(db) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Everything the copilot reads: profile, raw rows, items, perks, history."""
    profile = read_profile(db)
    raw = db.execute("SELECT * FROM Inventory ORDER BY ExpirationTimestamp").fetchall()
    rows = [dict(row) for row in raw]
    items = [inventory_item(row) for row in rows]
    perks = load_perks(db)
    history = [
        dict(row)
        for row in db.execute(
            "SELECT StrategyID, ItemID, ItemLabel, RecommendedAction, ManagerDecision, UnitsFilled, MarginRetained, CreatedTimestamp "
            "FROM StrategyHistory ORDER BY CreatedTimestamp DESC LIMIT 25"
        ).fetchall()
    ]
    return profile, rows, items, perks, history


def connect_business(
    db,
    business_name: str,
    lines: list[dict[str, Any]],
    margin_floor_pct: float | None = None,
    floor_source: str | None = None,
) -> dict[str, Any]:
    """Load a business read out of its own database into the copilot's workspace.

    This is the bridge between the two halves of the product. ``/v1`` keeps the
    honest mapped copy the integration contract promises, but the copilot, the
    planner and the retrieval corpus all read ``Inventory`` and ``Profile``. A
    business a client pushes is only *answerable* once it has been written here,
    so this writes it and makes it the active business.

    Honest rules, stated rather than hidden:

    * The floor comes from what the caller supplied, else from the floors the lines
      themselves carried. If neither exists, a default is assumed and the profile
      says so rather than quietly presenting it as the business's own.
    * A line with no time signal is stored long-dated, so it reads as "not lapsing"
      rather than as lapsing today. Nothing invents a date for it.
    * Logged decisions are left alone, exactly as a sector switch leaves them.
    """
    now = datetime.now(timezone.utc)
    db.execute("DELETE FROM Inventory")
    db.executemany(
        "INSERT INTO Inventory (ItemID, Sector, ItemName, Category, BasePrice, ExpirationTimestamp, TotalCapacity, UnitsSold, BookingVelocity) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                100 + index,
                CONNECTED_SECTOR,
                line["itemName"],
                line["category"],
                line["basePrice"],
                line.get("expirationTimestamp") or NO_TIME_SIGNAL_EXPIRY,
                line["totalCapacity"],
                line["unitsSold"],
                0.0,
            )
            for index, line in enumerate(lines, start=1)
        ],
    )

    stated = margin_floor_pct is not None
    if margin_floor_pct is None:
        # No floor was stated by the caller, so take the one the lines themselves
        # imply. The middle value is used rather than the mean, so one oddly-priced
        # line cannot drag the whole business's floor with it.
        implied = sorted(
            line["floorPrice"] / line["basePrice"] * 100
            for line in lines
            if line.get("floorPrice") is not None and line["basePrice"]
        )
        margin_floor_pct = implied[len(implied) // 2] if implied else None

    if floor_source is None:
        floor_source = (
            FLOOR_STATED if stated else FLOOR_DERIVED if margin_floor_pct is not None else FLOOR_ASSUMED
        )

    db.execute(
        "UPDATE Profile SET SectorSlug = ?, BusinessName = ?, Connected = 1, "
        "ConnectedFloorPct = ?, ConnectedFloorSource = ?, OwnerFloorPct = NULL, "
        "UpdatedTimestamp = ? WHERE ProfileID = 1",
        (CONNECTED_SECTOR, business_name, margin_floor_pct, floor_source, now.isoformat()),
    )
    db.commit()
    return read_profile(db)


def engine_for(
    profile: dict[str, Any],
    items: list[dict[str, Any]],
    learned_examples: list[dict[str, Any]] | None = None,
    db=None,
) -> IntentEngine:
    """The trained engine for this business, retrained once it has learned something.

    The cache key includes how many confirmed questions exist, so the moment the
    owner confirms a reading the next call retrains and their own phrasing is
    part of the model. Without that, learning would only take effect after a
    restart, which is not learning.
    """
    learned = list(learned_examples or [])
    key = (profile["sector"], tuple(item["itemName"] for item in items), len(learned))
    with _ENGINE_LOCK:
        cached = _ENGINES.get(key)
        if cached is None:
            # The vocabulary is sector-specific, so only the active one is kept.
            _ENGINES.clear()
            cached = IntentEngine(build_lexicon(profile, items), learned_examples=learned)
            _ENGINES[key] = cached
            if db is not None:
                card = cached.model_card()
                memory.snapshot_model(
                    db,
                    sector=profile["sector"],
                    holdout_accuracy=card["scores"]["holdoutAccuracy"],
                    holdout_worst=card["scores"]["holdoutWorst"],
                    training_rows=card["training"]["rows"],
                    learned_rows=card["training"]["learnedRows"],
                )
        return cached


def retrain_active(profile: dict[str, Any], items: list[dict[str, Any]]) -> None:
    """Drop stale conversations and warm the engine for the newly active business.

    Shared by a sector switch and by connecting a pushed business, because both
    replace the inventory the copilot was answering about. Item ids are reused
    across workspaces, so keeping the old conversation would resolve a follow-up
    question to a stranger's line.
    """
    with _SESSION_LOCK:
        _SESSIONS.clear()
    # Retraining takes a few seconds, so it happens off the request path: the
    # switch stays instant and the first question asked is still fast.
    threading.Thread(target=engine_for, args=(profile, items), daemon=True).start()


def session_state(session_id: str) -> dict[str, Any]:
    with _SESSION_LOCK:
        if session_id not in _SESSIONS and len(_SESSIONS) >= MAX_SESSIONS:
            _SESSIONS.pop(next(iter(_SESSIONS)))
        return _SESSIONS.setdefault(session_id or "default", {"turns": 0})


@app.get("/api/health")
def health():
    # The version rides along with the liveness answer because it is the one place
    # every client already looks: the interface prints it in the footer, and a
    # support question starts with "which build are you on?"
    return {"status": "ok", "service": "ValueAnchor", "version": VERSION}


# The three preset routes that used to live here - GET /api/sectors,
# POST /api/profile/sector and POST /api/profile/reseed - are gone, and so is
# the request model behind the sector switch. They were the ways a workspace
# chose which built-in demo business to seed, which is stock the product typed
# for itself rather than stock a business's own system of record holds.
#
# ``SECTORS`` itself stays: it is not a write path, it is the vocabulary the
# copilot answers in (the active preset's inventory noun and business name) and
# the retrieval corpus indexes it. The empty-workspace fallback still seeds the
# coffee preset so a first-time visitor has something to ask about, and a
# profile that was already switched to a preset still reads. What is gone is the
# ability to pick a new one from the UI, which is the whole point: stock now
# arrives through Link this database or it does not arrive.


@app.get("/api/profile")
def get_profile():
    db = connect()
    profile = read_profile(db)
    db.close()
    return profile


@app.post("/api/profile/currency")
def set_currency(request: CurrencyRequest):
    db = connect()
    db.execute(
        "UPDATE Profile SET CurrencyCode = ?, CurrencySymbol = ?, UpdatedTimestamp = ? WHERE ProfileID = 1",
        (request.currencyCode, request.currencySymbol, datetime.now(timezone.utc).isoformat()),
    )
    profile = read_profile(db)
    db.commit()
    db.close()
    return profile


@app.post("/api/profile/guardrails")
def set_guardrails(request: GuardrailRequest):
    """Store, or clear, the owner's own margin floor.

    This is not a preference: ``bundle_plan``, the pairing costs, the profit
    scenarios and every copilot answer read the floor off the profile, so writing
    it here changes what the whole workspace is allowed to recommend. That is
    exactly why the floor's source is written beside it rather than quietly
    overwriting the one the business arrived with.
    """
    db = connect()
    before = read_profile(db)["marginFloorPct"]
    floor = None if request.marginFloorPct is None else int(round(request.marginFloorPct))
    db.execute(
        "UPDATE Profile SET OwnerFloorPct = ?, UpdatedTimestamp = ? WHERE ProfileID = 1",
        (floor, datetime.now(timezone.utc).isoformat()),
    )
    db.commit()
    profile = read_profile(db)
    db.close()
    if floor is None:
        note = f"Floor cleared, back to {profile['marginFloorPct']}% - {profile['floorSource']}."
    elif before == profile["marginFloorPct"]:
        note = f"Floor unchanged at {profile['marginFloorPct']}%."
    else:
        note = (
            f"Floor moved from {before}% to {profile['marginFloorPct']}%. "
            "Every bundle, pairing and copilot answer is priced against it from now on."
        )
    return {"profile": profile, "note": note, "floorSource": profile["floorSource"]}


@app.get("/api/inventory")
def get_inventory():
    db = connect()
    profile = read_profile(db)
    items = [inventory_item(row) for row in db.execute("SELECT * FROM Inventory ORDER BY ExpirationTimestamp").fetchall()]
    db.close()
    return {"profile": profile, "items": items}


@app.post("/api/copilot")
def copilot(request: CopilotRequest):
    """Answer a question end to end: read it, retrieve, decide, then explain.

    The order is the design:

    1. The local model reads the sentence and decides what was asked.
    2. Retrieval selects the records relevant to that reading, so the answer is
       grounded in something traceable rather than in model memory.
    3. The planner answers from the live rows. Its reply already carries the
       headline, the plain meaning, the labelled figures, the next actions and
       the citations.
    4. Only then may the language assistant restate that answer in simpler
       words, and its wording is audited before it is returned.

    An off-topic question stops at step 1. It is refused by deterministic local
    code, and nothing about it is sent anywhere.
    """
    db = connect()
    try:
        profile, rows, items, perks, history = load_context(db)
    finally:
        db.close()

    if not rows:
        return {
            "answer": "There is no stock loaded yet, so there is nothing to analyse.",
            "headline": "Nothing to look at yet",
            "source": "local NLU (offline)",
            "itemId": None,
        }

    db = connect()
    try:
        learned = memory.training_examples(db, profile["sector"])
        engine = engine_for(profile, items, learned, db)
        state = session_state(request.sessionId)
        question = request.question.strip().lower()
        parsed = engine.parse(question, state)
        if request.intent:
            parsed = engine.confirm(parsed, request.intent)
        reply = plan_answer(parsed, profile=profile, rows=rows, history=history, perks=perks, sectors=SECTORS)

        # Retrieval only runs for questions that are already known to be about
        # this business, so an off-topic question never touches the index either.
        in_scope, gate_reason = CopilotAssistant.local_scope_gate(
            parsed.intent, parsed.entities, set(INTENT_LABELS)
        )
        knowledge = build_knowledge(db, profile, rows, items, perks, history)
        # The answer's own subject is pinned into the retrieved records, so the
        # assistant can always see the line whose figures it is explaining.
        sources = knowledge.retrieve(request.question, 8, reply.focus_item_id) if in_scope else []

        if in_scope:
            assistant = _assistant().rewrite(
                question=request.question,
                intent=parsed.intent,
                intent_label=parsed.to_dict()["intentLabel"],
                engine_answer=reply.answer,
                profile=profile,
                context=sources,
                glossary=reply.glossary,
                next_steps=[step["label"] for step in reply.next_steps],
                key_numbers=reply.key_numbers,
                entities=parsed.entities,
                known_intents=set(INTENT_LABELS),
            )
        else:
            assistant = AssistantResult(
                on_topic=False,
                declined=True,
                decline_reason=gate_reason,
                guardrail=f"stayed local: {gate_reason}",
            )

        if assistant.used:
            answer = assistant.answer
            if assistant.explanation:
                answer = f"{answer} {assistant.explanation}"
            source = f"local engine + {assistant.model}"
        else:
            answer = reply.answer
            source = "local engine (offline)"

        if reply.focus_item_id is not None:
            focus = next((item for item in items if item["itemId"] == reply.focus_item_id), None)
            if focus:
                state.update({"lastItemId": focus["itemId"], "lastItemLabel": focus["itemName"], "lastItemCategory": focus["category"]})
        state["turns"] = state.get("turns", 0) + 1

        payload = {
            "answer": answer,
            "engineAnswer": reply.answer,
            "source": source,
            "itemId": reply.focus_item_id,
            "headline": reply.headline,
            "meaning": reply.meaning,
            "plain": reply.plain,
            "severity": reply.severity,
            "nextSteps": reply.next_steps,
            "keyNumbers": reply.key_numbers,
            "followUps": reply.follow_ups,
            "glossary": reply.glossary,
            "sources": sources,
            "retrieval": knowledge.summary(),
            "assistant": assistant.to_dict(),
            "guardrail": assistant.guardrail,
            "inScope": in_scope,
            "nlu": {**parsed.to_dict(), "reply": reply.to_dict()},
        }

        turn_id = memory.record_turn(
            db,
            session_id=request.sessionId,
            question=request.question,
            answer=answer,
            intent=parsed.intent,
            sector=profile["sector"],
            assistant=source,
            confidence=parsed.confidence,
            sources=sources,
            payload=payload,
        )
        payload["turnId"] = turn_id

        # An intent the owner confirmed by tapping a clarifying option is a
        # labelled example, so it is stored for the next retrain.
        if parsed.confirmed and request.intent:
            memory.learn_intent(
                db,
                turn_id=turn_id,
                intent=request.intent,
                session_id=request.sessionId,
                sector=profile["sector"],
            )

        stats = memory.stats(db, profile["sector"])
        payload["learning"] = {
            "learnedExamples": stats["learnedExamples"],
            "turns": stats["turns"],
            "helpful": stats["helpful"],
        }
        return payload
    finally:
        db.close()


@app.get("/api/copilot/history")
def copilot_history(sessionId: str = "default", limit: int = 40):
    """The whole conversation for a session, so the copilot can reopen expanded."""
    db = connect()
    try:
        turns = memory.recent_turns(db, sessionId, max(1, min(limit, 200)))
    finally:
        db.close()
    return {"sessionId": sessionId, "turns": turns}


class FeedbackRequest(BaseModel):
    """The owner's verdict on one answer, plus what they meant if it was wrong.

    The rating defaults to neutral so the same body can be used to confirm what a
    question meant without also having to vote on the answer.
    """

    turnId: int
    rating: int = 0
    note: str | None = None
    intent: str | None = None

    @field_validator("rating")
    @classmethod
    def sane_rating(cls, value: int) -> int:
        if value not in (-1, 0, 1):
            raise ValueError("rating must be -1, 0 or 1")
        return value

    @field_validator("intent")
    @classmethod
    def known_intent(cls, value: str | None) -> str | None:
        if not value:
            return None
        slug = value.strip().lower()
        if slug not in INTENT_LABELS:
            raise ValueError(f"Unknown intent '{value}'")
        return slug


@app.post("/api/copilot/feedback")
def copilot_feedback(request: FeedbackRequest):
    """Record whether an answer helped, and learn from a corrected reading.

    A thumbs-up that carries the intent becomes a confirmed example. A
    thumbs-down is recorded but never taught, so a bad answer cannot be
    reinforced by the same mechanism that lets a good one be learned.
    """
    db = connect()
    try:
        profile, _rows, _items, _perks, _history = load_context(db)
        result = memory.rate_turn(
            db,
            turn_id=request.turnId,
            rating=request.rating,
            note=request.note,
            intent=request.intent,
        )
        stats = memory.stats(db, profile["sector"])
        result["learning"] = {"learnedExamples": stats["learnedExamples"], "turns": stats["turns"]}
        return result
    finally:
        db.close()


@app.post("/api/copilot/confirm")
def copilot_confirm(request: FeedbackRequest):
    """Tell the copilot what a question really meant.

    This is the strongest learning signal the product has: a human has said, in
    as many words, which intent their sentence was. The pair is stored and merged
    into training on the next rebuild.
    """
    if not request.intent:
        raise HTTPException(400, "intent is required to confirm a reading")
    db = connect()
    try:
        profile, _rows, _items, _perks, _history = load_context(db)
        result = memory.learn_intent(
            db,
            turn_id=request.turnId,
            intent=request.intent,
            sector=profile["sector"],
        )
        stats = memory.stats(db, profile["sector"])
        result["intentLabel"] = INTENT_LABELS.get(request.intent, request.intent)
        result["learning"] = {"learnedExamples": stats["learnedExamples"], "turns": stats["turns"]}
        result["meaning"] = (
            "Stored. From now on this model reads your phrasing that way, and the next answer "
            "will not need to ask."
        )
        return result
    finally:
        db.close()


@app.get("/api/learning")
def learning(sector: str | None = None):
    """What this workspace has taught the model, and how it has been received."""
    db = connect()
    try:
        profile, _rows, _items, _perks, _history = load_context(db)
        slug = sector or profile["sector"]
        stats = memory.stats(db, slug)
    finally:
        db.close()
    stats["sector"] = slug
    stats["howItLearns"] = (
        "Every question and answer is written down. When the owner corrects a reading, or marks an answer "
        "helpful, that question becomes a confirmed example of its intent and is merged into the next "
        "training run - so the model gets better at this business's own way of asking without any labelling "
        "work. Nothing off-topic is ever learned from, and a thumbs-down is never taught as an example."
    )
    return stats


@app.get("/api/knowledge")
def knowledge_card(question: str | None = None):
    """What the copilot can retrieve, and what a question would actually retrieve."""
    db = connect()
    try:
        profile, rows, items, perks, history = load_context(db)
        knowledge = build_knowledge(db, profile, rows, items, perks, history)
        sample = question or profile["sampleQuestion"]
        # Pin nothing here: this endpoint shows what a question retrieves on its
        # own, which is the honest picture of the index.
        hits = knowledge.retrieve(sample, 8)
    finally:
        db.close()
    return {
        "summary": knowledge.summary(),
        "sampleQuestion": sample,
        "sampleSources": hits,
        "grounding": (
            "Every answer is written from these records. The language assistant is handed the same records and "
            "nothing else, and any figure it produces that is not in them is rejected before the answer is shown."
        ),
    }


@app.post("/api/nlu/parse")
def nlu_parse(request: CopilotRequest):
    """The language layer on its own: parse an utterance without the prose answer."""
    db = connect()
    try:
        profile, rows, items, perks, history = load_context(db)
        if not rows:
            raise HTTPException(409, "No inventory is loaded, so there is nothing to interpret against")
        engine = engine_for(profile, items, memory.training_examples(db, profile["sector"]), db)
        parsed = engine.parse(request.question, session_state(request.sessionId))
        if request.intent:
            parsed = engine.confirm(parsed, request.intent)
        reply = plan_answer(parsed, profile=profile, rows=rows, history=history, perks=perks, sectors=SECTORS)
        return {"parse": parsed.to_dict(), "reply": reply.to_dict()}
    finally:
        db.close()


@app.get("/api/nlu/model")
def nlu_model():
    """Model card: what was trained, on how much data, and how it scored."""
    db = connect()
    try:
        profile, rows, items, perks, history = load_context(db)
        if not items:
            raise HTTPException(409, "No inventory is loaded, so there is nothing to train on")
        engine = engine_for(profile, items, memory.training_examples(db, profile["sector"]), db)
        card = engine.model_card()
    finally:
        db.close()
    card["profile"] = {
        "sector": profile["sector"],
        "businessName": profile["businessName"],
        "inventoryNoun": profile["inventoryNoun"],
        "marginFloorPct": profile["marginFloorPct"],
    }
    card["sampleUtterances"] = [
        profile["sampleQuestion"],
        "What is heading for waste?",
        "What should I bundle with one of these?",
        "Should I knock twenty percent off?",
        "What will close look like?",
        "Which sectors do you support?",
        "Who won the game last night?",
    ]
    card["runsLocally"] = True
    assistant_now = _assistant()
    db = connect()
    try:
        configured_models = registry.list_models(db)
        shared_limit = registry.account_limit(db)
    finally:
        db.close()
    ready_models = [entry for entry in configured_models if entry["enabled"] and entry["hasKey"]]
    card["leavesMachine"] = bool(assistant_now.configured and ready_models)
    card["assistant"] = {
        "enabled": bool(ready_models),
        "models": assistant_now.chain,
        "configured": len(configured_models),
        "ready": len(ready_models),
        "sharedLimit": shared_limit,
        "role": (
            "Rephrases and explains the engine's answer. It never decides, never computes a figure and is "
            "never consulted about a question that is not about this business."
        ),
        "guardrails": [
            "a local scope gate refuses off-topic questions before any network call",
            "the assistant is given retrieved records and nothing else",
            "a self-declared refusal is honoured",
            "every figure in its answer is checked against the live data, and an answer with an unknown figure is discarded",
        ],
    }
    return card


class ModelRequest(BaseModel):
    modelId: str
    provider: str | None = None
    label: str | None = None
    baseUrl: str | None = None
    keyEnv: str | None = None
    notes: str | None = None
    enabled: bool | None = None


class ModelUpdateRequest(BaseModel):
    rowId: int
    modelId: str | None = None
    provider: str | None = None
    label: str | None = None
    baseUrl: str | None = None
    keyEnv: str | None = None
    notes: str | None = None
    enabled: bool | None = None
    position: int | None = None


class ModelOrderRequest(BaseModel):
    order: list[int]


@app.get("/api/models")
def list_assistant_models():
    """The configured models, their endpoints, and when each becomes usable again.

    This is the whole point of the registry: the answer to "why is nothing being
    rewritten, and when can I use this again" is one request away, including the
    fact that free models share a single daily budget.
    """
    db = connect()
    try:
        entries = registry.list_models(db)
        shared = registry.account_limit(db)
    finally:
        db.close()

    return {
        "models": entries,
        "providers": registry.provider_catalogue(),
        "sharedLimit": shared,
        "defaults": {"provider": registry.DEFAULT_PROVIDER, "baseUrl": registry.OPENROUTER_BASE},
        "howItWorks": (
            "Models are tried in order until one answers. Each entry carries its own endpoint and the name of "
            "the environment variable holding its key, so the key itself is never stored here, never sent to "
            "the browser, and any OpenAI-compatible endpoint can be added - including a local one."
        ),
    }


@app.post("/api/models")
def add_assistant_model(request: ModelRequest):
    """Register any model id. Nothing is validated against a list, on purpose."""
    db = connect()
    try:
        try:
            result = registry.add_model(
                db,
                model_id=request.modelId,
                provider=request.provider or registry.DEFAULT_PROVIDER,
                label=request.label,
                base_url=request.baseUrl,
                key_env=request.keyEnv,
                notes=request.notes,
                enabled=True if request.enabled is None else request.enabled,
            )
        except ValueError as error:
            raise HTTPException(400, str(error)) from error
        result["models"] = registry.list_models(db)
        return result
    finally:
        db.close()


@app.post("/api/models/update")
def update_assistant_model(request: ModelUpdateRequest):
    db = connect()
    try:
        result = registry.update_model(db, request.rowId, request.model_dump())
        result["models"] = registry.list_models(db)
        return result
    finally:
        db.close()


@app.post("/api/models/order")
def order_assistant_models(request: ModelOrderRequest):
    db = connect()
    try:
        result = registry.reorder(db, request.order)
        result["models"] = registry.list_models(db)
        return result
    finally:
        db.close()


@app.delete("/api/models/{row_id}")
def delete_assistant_model(row_id: int):
    db = connect()
    try:
        result = registry.remove_model(db, row_id)
        result["models"] = registry.list_models(db)
        return result
    finally:
        db.close()


@app.post("/api/models/probe")
def probe_assistant_model(request: ModelRequest):
    """Ask one model a one-word question, and record what the provider says.

    Reachability, key and quota in a single cheap call. The response headers are
    the valuable part: on a 429 they carry the reset time, which is what turns
    "rate limited" into "back at 00:00 UTC".
    """
    db = connect()
    try:
        catalogue = registry.provider_for(request.provider or registry.DEFAULT_PROVIDER)
        spec = {
            "model": request.modelId,
            "provider": request.provider or registry.DEFAULT_PROVIDER,
            "baseUrl": request.baseUrl or catalogue["baseUrl"],
            "apiKeyEnv": request.keyEnv or catalogue["keyEnv"],
            "label": request.label or request.modelId,
        }
        outcome = _assistant().probe(spec)
        health = registry.record_health(
            db,
            model_id=spec["model"],
            provider=spec["provider"],
            status=str(outcome.get("status") or "unknown"),
            detail=str(outcome.get("detail") or ""),
            headers=outcome.get("headers") or {},
            limit_source=str(outcome.get("limitSource") or ""),
            remedy=str(outcome.get("detail") or "") if outcome.get("status") == "rate limited" else "",
        )
        return {
            "probe": {key: value for key, value in outcome.items() if key != "headers"},
            "rateLimit": {
                "limit": health.get("LimitValue"),
                "remaining": health.get("Remaining"),
                "resetAt": health.get("ResetAt"),
                "availableAt": health.get("AvailableAt"),
                "secondsRemaining": health.get("secondsRemaining"),
                "humanRemaining": health.get("humanRemaining"),
                "limitSource": health.get("LimitSource"),
            },
            "sharedLimit": registry.account_limit(db),
        }
    finally:
        db.close()


@app.get("/api/copilot/suggestions")
def copilot_suggestions():
    """Question cards built from live data, so the owner never faces a blank box.

    Each card is a question the engine can already answer, phrased around the
    line that actually matters right now rather than a generic example.
    """
    db = connect()
    try:
        profile, rows, items, perks, history = load_context(db)
    finally:
        db.close()
    if not rows:
        return {"groups": []}

    pairs = sorted(
        ((row, inventory_item(row)) for row in rows),
        key=lambda pair: pair[1]["urgencyScore"],
        reverse=True,
    )
    worst = pairs[0][1]["itemName"]
    second = pairs[1][1]["itemName"] if len(pairs) > 1 else worst

    groups = [
        {
            "title": "What needs me right now",
            "detail": "The line under the most pressure, ranked by time against stock",
            "questions": [
                "Which batch needs attention first?",
                "What will go to waste?",
                f"Compare the {worst} and the {second}",
            ],
        },
        {
            "title": "Moving stock without cutting the price",
            "detail": "Bundles and perks that keep the shelf price on the board",
            "questions": [
                f"What should I bundle with the {worst}?",
                f"What if I drop the price on the {worst}?",
                "What can I give away cheaply?",
            ],
        },
        {
            "title": "Your rules and your record",
            "detail": "The floor that governs every recommendation, and what you decided before",
            "questions": [
                "What is my margin floor?",
                f"What is my floor on the {worst}?",
                "What have I approved so far?",
            ],
        },
        {
            "title": "Where today ends up",
            "detail": "Open or active based on the live numbers, not a saved report",
            "questions": [
                "What will close look like?",
                "How is today going?",
                "What can you do?",
            ],
        },
    ]
    return {
        "groups": groups,
        "worstItem": worst,
        "focus": {
            "item": worst,
            "hoursRemaining": pairs[0][1]["hoursRemaining"],
            "riskStatus": pairs[0][1]["riskStatus"],
        },
    }


@app.post("/api/yield/analyze")
def analyze(payload: dict[str, int]):
    item_id = payload.get("itemId") or payload.get("item_id")
    db = connect()
    profile = read_profile(db)
    item = db.execute("SELECT * FROM Inventory WHERE ItemID = ?", (item_id,)).fetchone()
    if not item:
        db.close()
        raise HTTPException(404, "Inventory item not found")
    perks = db.execute("SELECT * FROM Perks WHERE AvailableStock > 0 ORDER BY Type, CostToBusiness").fetchall()
    db.close()
    return {
        "profile": profile,
        **bundle_plan(item, perks, profile),
        # The three-way money comparison travels with the plan, so the Inventory
        # tab can show what each option keeps rather than only what to do.
        "profit": yield_engine.profit_scenarios(item, perks, profile),
    }


class ProfitRequest(BaseModel):
    itemId: int
    markdownPercent: float | None = None


class PairRequest(BaseModel):
    itemIds: list[int]
    markdownPercent: float | None = None


@app.post("/api/profit/scenarios")
def profit_for_item(request: ProfitRequest):
    """Cost one live line three ways: let it lapse, cut the price, or add value.

    This is the endpoint behind "if I do this, what do I keep" - it returns the
    cash per unit for each option, the total, and what the recommendation has to
    do to be worth choosing.
    """
    db = connect()
    try:
        profile, _rows, _items, perks, _history = load_context(db)
        row = db.execute("SELECT * FROM Inventory WHERE ItemID = ?", (request.itemId,)).fetchone()
        if not row:
            raise HTTPException(404, "That line is not in the live inventory")
        percent = request.markdownPercent if request.markdownPercent is not None else 20.0
        return {
            "profile": profile,
            **yield_engine.profit_scenarios(row, perks, profile, percent),
        }
    finally:
        db.close()


@app.post("/api/profit/pair")
def profit_for_pair(request: PairRequest):
    """Cost a pairing line by line, so one bad half cannot hide in a good total.

    A line that is not in the live inventory is refused by name rather than
    quietly left out of the total. The difference matters: dropping it would
    answer a question about two lines with the cost of one, in a confident
    voice and with no sign that anything was missing - which is the exact
    failure this endpoint exists to prevent, one level up.
    """
    if not request.itemIds:
        raise HTTPException(400, "Name at least one line to pair")
    db = connect()
    try:
        profile, _rows, _items, perks, _history = load_context(db)
        rows = []
        missing = []
        for item_id in request.itemIds[:3]:
            row = db.execute("SELECT * FROM Inventory WHERE ItemID = ?", (item_id,)).fetchone()
            if row:
                rows.append(row)
            else:
                missing.append(item_id)
        if missing:
            raise HTTPException(
                404,
                f"Not in the live inventory: {', '.join(str(item_id) for item_id in missing)}."
                " Nothing was costed, because a pairing that quietly leaves a line out is"
                " a total for a different pairing.",
            )
        return {"profile": profile, **yield_engine.bundle_pair_profit(rows, perks, profile)}
    finally:
        db.close()


@app.post("/api/strategy/decide")
def decide(request: DecisionRequest):
    db = connect()
    item = db.execute("SELECT * FROM Inventory WHERE ItemID = ?", (request.itemId,)).fetchone()
    if not item:
        db.close()
        raise HTTPException(404, "Inventory item not found")
    cursor = db.execute(
        "INSERT INTO StrategyHistory (ItemID, RecommendedAction, ManagerDecision, UnitsFilled, MarginRetained, CreatedTimestamp, ItemLabel, Category, Sector) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            request.itemId,
            request.recommendedAction,
            request.managerDecision,
            request.unitsFilled,
            request.marginRetained,
            datetime.now(timezone.utc).isoformat(),
            item["ItemName"],
            item["Category"],
            item["Sector"],
        ),
    )
    db.commit()
    db.close()
    return {"strategyId": cursor.lastrowid, "status": "logged", "managerDecision": request.managerDecision}


@app.get("/api/strategy/history")
def strategy_history(limit: int = 20, sector: str | None = None):
    db = connect()
    profile = read_profile(db)
    query = (
        "SELECT StrategyID, ItemID, ItemLabel, Category, Sector, RecommendedAction, ManagerDecision, UnitsFilled, MarginRetained, CreatedTimestamp "
        "FROM StrategyHistory"
    )
    params: list[Any] = []
    if sector:
        query += " WHERE Sector = ?"
        params.append(sector)
    query += " ORDER BY CreatedTimestamp DESC LIMIT ?"
    params.append(max(1, min(limit, 100)))
    rows = db.execute(query, tuple(params)).fetchall()
    db.close()
    return {
        "profile": profile,
        "decisions": [
            {
                "strategyId": row["StrategyID"],
                "itemId": row["ItemID"],
                "itemLabel": row["ItemLabel"] or f"Item {row['ItemID']}",
                "category": row["Category"] or "Unknown",
                "sector": row["Sector"] or DEFAULT_SECTOR,
                "recommendedAction": row["RecommendedAction"],
                "managerDecision": row["ManagerDecision"],
                "unitsFilled": row["UnitsFilled"],
                "marginRetained": row["MarginRetained"],
                "createdTimestamp": row["CreatedTimestamp"],
            }
            for row in rows
        ],
    }


# ---------------------------------------------------------------------------
# The MCP surface
#
# The hackathon shape is that the OpenEdge side gets a *tool surface* rather than
# a prompt full of numbers, so this mounts ValueAnchor's own MCP server at
# POST /mcp. Every tool calls the same function its HTTP route calls, which is the
# whole point: the two surfaces cannot drift into two slightly different products,
# and a figure handed to an agent is the same figure the dashboard shows.
# ---------------------------------------------------------------------------


def _for_tool(call, *args, **kwargs):
    """Run an API function on behalf of an MCP tool.

    A refusal the HTTP API would answer with a status code - a line that is not in
    the live inventory, a decision about a line that does not exist - becomes a
    tool refusal here, so an agent reads it as a fact about the business rather
    than as a broken server.
    """
    try:
        return call(*args, **kwargs)
    except HTTPException as refusal:
        raise mcp_server.ToolRefusal(str(refusal.detail)) from refusal


def mcp_toolkit() -> "mcp_server.Toolkit":
    """The six tools, in terms of the code the HTTP API already uses."""

    def guardrails(_arguments: dict) -> dict:
        db = connect()
        try:
            profile = read_profile(db)
        finally:
            db.close()
        return {
            "activeBusiness": profile["businessName"],
            "sector": profile["sector"],
            "currencyCode": profile["currencyCode"],
            "currencySymbol": profile["currencySymbol"],
            "marginFloorPct": profile["marginFloorPct"],
            "floorSource": profile["floorSource"],
            "floorOwnerSet": profile["floorOwnerSet"],
            "connected": profile["connected"],
            "markdowns": "never - this engine has no discount path at all",
            "appliesTo": "every bundle, pairing and copilot answer in this workspace",
        }

    def urgent_stock(arguments: dict) -> dict:
        db = connect()
        try:
            profile, _rows, items, _perks, _history = load_context(db)
        finally:
            db.close()
        ranked = sorted(items, key=lambda item: item["urgencyScore"], reverse=True)
        eligible = (
            ranked
            if arguments.get("includeHealthy")
            else [item for item in ranked if item["riskStatus"] != "healthy"]
        )
        limit = int(arguments.get("limit") or 5)
        answer: dict[str, Any] = {
            "business": profile["businessName"],
            "marginFloorPct": profile["marginFloorPct"],
            "currencySymbol": profile["currencySymbol"],
            "linesOnTheBooks": len(items),
            "linesUnderPressure": len(eligible),
            "returned": len(eligible[:limit]),
            "ranking": "urgencyScore weighs hours until the stock lapses against how little of it has sold",
            "lines": eligible[:limit],
        }
        if not eligible:
            answer["note"] = (
                f"Every one of the {len(items)} live lines is healthy right now, so nothing is "
                "lapsing. Pass includeHealthy to see the whole shelf anyway."
            )
        return answer

    def analyze_line(arguments: dict) -> dict:
        return _for_tool(analyze, {"itemId": arguments["itemId"]})

    def pair_lines(arguments: dict) -> dict:
        return _for_tool(profit_for_pair, PairRequest(itemIds=arguments["itemIds"]))

    def ask(arguments: dict) -> dict:
        answer = _for_tool(
            copilot,
            CopilotRequest(
                question=arguments["question"],
                sessionId=arguments.get("sessionId") or "mcp-session",
            ),
        )
        return {
            "answer": answer.get("answer"),
            "headline": answer.get("headline"),
            "meaning": answer.get("meaning"),
            "severity": answer.get("severity"),
            "answeredBy": answer.get("source"),
            "understoodAs": (answer.get("nlu") or {}).get("intentLabel"),
            "inScope": answer.get("inScope"),
            "guardrail": answer.get("guardrail"),
            "figures": answer.get("keyNumbers") or [],
            "nextSteps": [step.get("label") for step in (answer.get("nextSteps") or [])],
            "answeredFrom": [
                {"kind": source.get("kind"), "title": source.get("title"), "detail": source.get("detail")}
                for source in (answer.get("sources") or [])[:6]
            ],
        }

    def log_decision(arguments: dict) -> dict:
        item_id = arguments["itemId"]
        decision = arguments["managerDecision"]
        # The action and the money come from the live plan rather than from what a
        # model remembered, so the log can never hold a figure the engine did not
        # compute. Anything that had to be filled in says so in the reply.
        analysis = _for_tool(analyze, {"itemId": item_id})
        item = analysis["item"]
        recommendation = analysis["recommendation"]
        defaults: list[str] = []
        action = arguments.get("recommendedAction")
        if not action:
            action = recommendation["action"]
            defaults.append("recommendedAction was taken from the live plan for this line")
        units = arguments.get("unitsFilled")
        if units is None:
            units = 0 if decision == "Declined" else max(1, round(item["unitsAvailable"] * 0.4))
            defaults.append(f"unitsFilled was taken as {units}, the same default the dashboard uses")
        margin = arguments.get("marginRetained")
        if margin is None:
            margin = recommendation["marginRetained"]
            defaults.append(f"marginRetained was taken from the live plan ({margin})")
        logged = _for_tool(
            decide,
            DecisionRequest(
                itemId=item_id,
                recommendedAction=action,
                managerDecision=decision,
                unitsFilled=int(units),
                marginRetained=float(margin),
            ),
        )
        return {
            "logged": True,
            "strategyId": logged["strategyId"],
            "item": item["itemName"],
            "managerDecision": decision,
            "recommendedAction": action,
            "unitsFilled": int(units),
            "marginRetained": float(margin),
            "defaultedFields": defaults,
            "note": (
                "The log keeps what was decided, not what was recommended. Nothing here rewrites "
                "history: to change a decision, log the decision that replaces it."
            ),
        }

    return {
        "valueanchor_guardrails": guardrails,
        "valueanchor_urgent_stock": urgent_stock,
        "valueanchor_analyze_line": analyze_line,
        "valueanchor_pair_lines": pair_lines,
        "valueanchor_ask": ask,
        "valueanchor_log_decision": log_decision,
    }


@app.post("/mcp")
async def mcp(request: Request):
    """The MCP endpoint: one POST, one JSON-RPC message, one JSON response.

    A notification carries no id and takes no response, so it is answered with 202
    and an empty body, which is what the transport asks for. A body that is not
    JSON at all is reported as a parse error rather than as a 500, because "what
    arrived was not JSON" and "something broke here" are different problems.
    """
    raw = await request.body()
    try:
        payload = mcp_server.parse_message(raw)
    except mcp_server.ParseError as broken:
        return JSONResponse(mcp_server.parse_failure(str(broken)), status_code=400)
    messages = payload if isinstance(payload, list) else [payload]
    toolkit = mcp_toolkit()
    responses = [
        response
        for message in messages
        if (response := mcp_server.handle_message(message, toolkit)) is not None
    ]
    if not responses:
        return JSONResponse({}, status_code=202)
    return JSONResponse(responses if isinstance(payload, list) else responses[0])


@app.get("/api/openedge/status")
def openedge_status(request: Request, probe: bool = False):
    """Where the OpenEdge integration stands, in both directions.

    Inbound - this server's own MCP surface - needs nothing installed, so it is
    reported as available. Outbound needs software an owner installs and points at
    their own database, so it reports nothing-called rather than unreachable until
    it is pointed at something. ``?probe=true`` is what turns a configured address
    into actual network calls, and it is opt-in because a status page should not
    quietly wait on someone else's server.
    """
    base = str(request.base_url).rstrip("/")
    return openedge.status(base, probe_endpoints=probe)


@app.get("/api/openedge/link/status")
def openedge_link_status():
    """What the local read-only bridge can see, before anything is run.

    This is not the outbound probe above. That one asks software an owner
    installs; this one looks at this machine, for the Developers Kit and for the
    coffee database the starter kit builds. The two are reported separately
    because they are different claims: a missing MCP Server is not a broken
    AVM, and an owner who has installed the kit should be told it was found.

    ``blockers`` names what is missing in the owner's own words - no AVM, no
    program, no database - because those three have three different fixes and a
    single "unavailable" would leave the owner guessing which.
    """
    return openedge_bridge.status()


class OpenEdgeLinkIn(BaseModel):
    """Ask the bridge for the rows, optionally naming the database."""

    database: str = Field(default="coffee", min_length=1, max_length=80)


@app.post("/api/openedge/link")
def openedge_link(payload: OpenEdgeLinkIn):
    """Read the business's stock out of its own OpenEdge database.

    This is the path that replaces hand-typed figures. It runs one ABL program,
    ``06-enquiry-json.p``, whose only statement over data is a ``FOR EACH`` with
    ``NO-LOCK``: there is no write path here, and no argument can name a
    different program, table or database. The database is a *name*, resolved to
    a storage file beside the backend, so it cannot be turned into a path that
    reaches elsewhere.

    A refusal is a 400 with the reason in words rather than an empty 200: no AVM
    installed, a database that was never seeded and a database somebody else is
    serving are three different problems, and the owner needs the difference.
    The rows are then handed to ``upsert_business_stock`` - the same function
    the push endpoint calls - so a linked shelf and a pushed shelf are one shelf
    and cannot drift into two.
    """
    name = (payload.database or "").strip()
    if not name:
        raise HTTPException(400, "A database name is required, e.g. 'coffee'.")
    if any(sep in name for sep in ("/", "\\", "..")):
        raise HTTPException(400, "'database' is a name, not a path; it cannot contain separators.")

    result = openedge_bridge.call_tool("openedge_read_stock", {"database": name})
    if result.get("isError"):
        reason = (result.get("structuredContent") or {}).get("reason", "The bridge refused.")
        raise HTTPException(400, reason)

    read = result["structuredContent"]
    business_name = read.get("businessName") or ""
    if not business_name:
        raise HTTPException(
            422,
            "The database returned no business name, so there is nothing honest to file "
            "these rows under. Seed it with 04-seed-coffee.p.",
        )

    # One identity, derived from the database's own name for the business. The
    # id is derived rather than invented so relinking is idempotent: the same
    # database links to the same business every time instead of forking a new
    # one on each press.
    business_id = "openedge-" + re.sub(r"[^a-z0-9]+", "-", business_name.lower()).strip("-")

    # Checked before the rows are shaped, and on purpose: an empty table is the
    # common mistake (the owner built the database and skipped the seeder), so it
    # gets the fix by name rather than a validation error about list lengths,
    # which is what StockBatchIn would say instead.
    rows = read.get("lines") or []
    if not rows:
        raise HTTPException(
            400,
            f"'{name}' has the coffee_item table but no rows in it. Run "
            "04-seed-coffee.p against it, then link again.",
        )

    try:
        batch = api_v1.StockBatchIn(
            business=api_v1.BusinessIdentity(businessId=business_id, businessName=business_name),
            lines=[
                api_v1.StockLineIn(
                    itemName=line["itemName"],
                    category=line.get("category") or "Uncategorised",
                    basePrice=float(line["basePrice"]),
                    totalCapacity=int(line["totalCapacity"]),
                    unitsSold=int(line["unitsSold"]),
                    shelfHoursUntilClosed=float(line["shelfHoursUntilClosed"]),
                )
                for line in rows
            ],
        )
    except (ValidationError, KeyError, TypeError, ValueError) as failure:
        raise HTTPException(
            422,
            "The rows the database returned do not fit the stock shape this product "
            f"reasons about. It said: {failure}",
        ) from failure

    # The two halves, in one call. First the honest mapped copy, through the
    # push path's own upsert so the two routes cannot diverge into two slightly
    # different shelves. ``api_v1._db_for_push`` rather than a bare ``connect()``
    # because it also ensures the V1Stock table exists, so linking into a fresh
    # database works rather than failing with "no such table".
    resolved = api_v1.resolved_lines(batch)
    db = api_v1._db_for_push()
    try:
        stored = api_v1.upsert_business_stock(db, batch, resolved)

        # Second, the bridge the mapped copy alone does not do: write the same
        # stock into the workspace the copilot, the planner and the retrieval
        # corpus read, and make it the active business. Without this step,
        # "I linked my database" and "the copilot can answer about it" would be
        # two different states, which is the exact lie this endpoint exists to
        # avoid. The floor is derived from the lines' own floors, so a database
        # that carries no floor prices reads as honestly assumed, not invented.
        profile = connect_business(db, business_name, resolved)
        items = [
            inventory_item(row)
            for row in db.execute("SELECT * FROM Inventory ORDER BY ExpirationTimestamp")
        ]
        retrain_active(profile, items)
    except ValueError as failure:
        raise HTTPException(400, str(failure)) from failure
    finally:
        db.close()

    return {
        "business": stored.business,
        "accepted": stored.accepted,
        "itemIds": stored.itemIds,
        "storedAsOf": stored.storedAsOf,
        "source": "openedge",
        "database": name,
        "program": "06-enquiry-json.p",
        "readOnly": True,
        "unitsLeft": read.get("unitsLeft"),
        "activeBusiness": {
            "name": business_name,
            "floorPct": profile["marginFloorPct"],
            "floorSource": profile["floorSource"],
            "lines": len(items),
            "note": (
                "The copilot, the planner and the retrieval corpus now answer about this "
                "business, off the rows just read out of OpenEdge."
            ),
        },
        "note": (
            "Read from the OpenEdge database by a FOR EACH with NO-LOCK, kept as a mapped "
            "copy, and made the business the copilot answers about. The business keeps its "
            "source of truth; relinking replaces the copy and writes nothing back."
        ),
    }


# ---------------------------------------------------------------------------
# The interface
#
# The desktop build ships the exported Next.js interface inside the executable
# and serves it from this same origin: one double-click, no Node.js, no second
# port, no CORS. A source checkout gets the same mount for `frontend/out` once it
# has been built; with no built folder the API simply has no UI, which is what
# the test suite runs against.
# ---------------------------------------------------------------------------
UI_DIR = Path(
    os.environ.get("VALUEANCHOR_UI")
    or (BUNDLE / "frontend_out" if FROZEN else ROOT.parent / "frontend" / "out")
)
if UI_DIR.is_dir():
    from fastapi.staticfiles import StaticFiles

    app.mount("/", StaticFiles(directory=UI_DIR, html=True), name="ui")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
