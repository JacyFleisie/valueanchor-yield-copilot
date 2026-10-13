from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import os
import sqlite3
import threading
import time
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, TypeAdapter, field_validator

ROOT = Path(__file__).resolve().parent
# The database path is overridable so tests and demos can run against a scratch
# file instead of the working dataset.
DB_PATH = Path(os.environ.get("VALUEANCHOR_DB") or ROOT / "valueanchor.db")
DEFAULT_SECTOR = "coffee"

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


app = FastAPI(title="ValueAnchor Yield Copilot", version="1.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


class SectorRequest(BaseModel):
    sector: str

    @field_validator("sector")
    @classmethod
    def known_sector(cls, value: str) -> str:
        slug = value.strip().lower()
        if slug not in SECTORS:
            raise ValueError(f"Unknown sector '{value}'")
        return slug


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
    slug = db.execute("SELECT SectorSlug FROM Profile WHERE ProfileID = 1").fetchone()["SectorSlug"]
    seed_sector(db, slug)
    db.commit()
    return slug


def sector_preset(slug: str) -> dict[str, Any]:
    if slug not in SECTORS:
        raise HTTPException(404, f"Unknown sector '{slug}'")
    return SECTORS[slug]


def read_profile(db) -> dict[str, Any]:
    row = db.execute("SELECT * FROM Profile WHERE ProfileID = 1").fetchone()
    preset = sector_preset(row["SectorSlug"])
    return {
        "sector": row["SectorSlug"],
        "name": preset["name"],
        "businessName": preset["businessName"],
        "blurb": preset["blurb"],
        "inventoryNoun": preset["inventoryNoun"],
        "itemLabel": preset["itemLabel"],
        "heroLine": preset["heroLine"],
        "sampleQuestion": preset["sampleQuestion"],
        "marginFloorPct": preset["marginFloorPct"],
        "currencyCode": row["CurrencyCode"],
        "currencySymbol": row["CurrencySymbol"],
    }


# Yield maths lives in exactly one module so the HTTP API, the copilot planner
# and the tests can never disagree about a floor or an urgency score.
from yield_engine import bundle_plan, hours_remaining, inventory_item, load_perks  # noqa: E402
from nlu import IntentEngine, build_lexicon  # noqa: E402
from nlu import memory  # noqa: E402
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
    """Read ``backend/.env`` into the environment if it is there.

    Fifteen lines rather than a dependency: this is the only configuration the
    server has, and the project deliberately keeps its installed packages to the
    ones that do real work. An already-set environment variable always wins, so
    a real deployment configures the server the normal way and this is only a
    convenience for running locally. The file is gitignored, so the key is never
    committed and never reaches the browser.
    """
    path = ROOT / ".env"
    if not path.exists():
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


def _assistant() -> CopilotAssistant:
    """A fresh assistant per call, built from the current environment."""
    fallbacks = [
        name.strip()
        for name in (os.environ.get("OPENROUTER_FALLBACK_MODELS") or "").split(",")
        if name.strip()
    ]
    return CopilotAssistant(_openrouter_key(), os.environ.get("OPENROUTER_MODEL"), fallbacks)


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


def session_state(session_id: str) -> dict[str, Any]:
    with _SESSION_LOCK:
        if session_id not in _SESSIONS and len(_SESSIONS) >= MAX_SESSIONS:
            _SESSIONS.pop(next(iter(_SESSIONS)))
        return _SESSIONS.setdefault(session_id or "default", {"turns": 0})


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "ValueAnchor"}


@app.get("/api/sectors")
def list_sectors():
    return [
        {"slug": slug, "name": preset["name"], "businessName": preset["businessName"], "blurb": preset["blurb"], "inventoryNoun": preset["inventoryNoun"]}
        for slug, preset in SECTORS.items()
    ]


@app.get("/api/profile")
def get_profile():
    db = connect()
    profile = read_profile(db)
    db.close()
    return profile


@app.post("/api/profile/sector")
def set_sector(request: SectorRequest):
    db = connect()
    db.execute("UPDATE Profile SET SectorSlug = ?, UpdatedTimestamp = ? WHERE ProfileID = 1", (request.sector, datetime.now(timezone.utc).isoformat()))
    seed_sector(db, request.sector)
    profile = read_profile(db)
    items = [inventory_item(row) for row in db.execute("SELECT * FROM Inventory").fetchall()]
    db.commit()
    db.close()
    # Item ids are reused across sectors, so any conversation that was pointing
    # at the old inventory is dropped rather than resolved to a stranger's line.
    with _SESSION_LOCK:
        _SESSIONS.clear()
    # Retraining takes a few seconds, so it happens off the request path: the
    # switch stays instant and the first question in the new sector is fast.
    threading.Thread(target=engine_for, args=(profile, items), daemon=True).start()
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
    card["leavesMachine"] = bool(_openrouter_key() and not _assistant().chain == [])
    card["assistant"] = {
        "enabled": bool(_openrouter_key()),
        "models": _assistant().chain,
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
    return {"profile": profile, **bundle_plan(item, perks, profile)}


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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
