"""The model registry: which language models the assistant may use, and when.

The assistant used to be configured by two environment variables, which meant
that trying a different free model needed a file edit and a restart, and that a
model which had run out of quota failed invisibly. Both are the same problem:
the selection of models is state, and state belongs in the database where it can
be inspected and changed while the product is running.

So this module owns two tables:

* ``AssistantModel`` - an ordered list of models the owner has configured. Each
  row carries its own endpoint and the *name* of the environment variable that
  holds its key, so a second provider can be added without touching this code and
  without ever putting a key in the database or the browser.
* ``ModelHealth`` - what happened the last time each model was asked. This is
  where the answer to "when can I use this again" comes from.

The rate-limit half is grounded in what OpenRouter actually returns, verified
against a live 429 rather than guessed from documentation::

    X-RateLimit-Limit: 50
    X-RateLimit-Remaining: 0
    X-RateLimit-Reset: 1791936000000      # epoch milliseconds, midnight UTC

The free tier is counted per *account*, not per model, which is the single most
important thing a user can be told: switching to another free model does not
give you more requests, it only changes which model refuses you. The registry
surfaces that plainly instead of letting someone cycle through models wondering
why none of them work.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

# Free, and verified to answer this task. Kept in the same order that was probed
# for quality so a fresh install behaves exactly as it did before there was a
# registry to configure.
DEFAULT_MODEL_CHAIN = [
    "nvidia/nemotron-3-super-120b-a12b:free",
    "liquid/lfm-2.5-2.6b:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
]

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
DEFAULT_PROVIDER = "openrouter"

# Providers we can describe accurately, and the base URL each one talks to. The
# list is a convenience, not a limit: any OpenAI-compatible endpoint works by
# naming its own base URL and key variable.
PROVIDERS: list[dict[str, str]] = [
    {
        "slug": "openrouter",
        "name": "OpenRouter",
        "baseUrl": OPENROUTER_BASE,
        "keyEnv": "OPENROUTER_API_KEY",
        "note": "One key reaches every provider OpenRouter routes to. Free models carry a :free suffix.",
    },
    {
        "slug": "openai-compatible",
        "name": "Any OpenAI-compatible endpoint",
        "baseUrl": "",
        "keyEnv": "LLM_API_KEY",
        "note": "Point this at a local server (Ollama, llama.cpp, vLLM) or any hosted OpenAI-style API.",
    },
]

MODELS_SCHEMA = """
CREATE TABLE IF NOT EXISTS AssistantModel (
    ModelRowID INTEGER PRIMARY KEY AUTOINCREMENT,
    Provider TEXT NOT NULL DEFAULT 'openrouter',
    ModelId TEXT NOT NULL,
    Label TEXT,
    BaseUrl TEXT,
    KeyEnv TEXT,
    Position INTEGER NOT NULL DEFAULT 0,
    Enabled INTEGER NOT NULL DEFAULT 1,
    Notes TEXT,
    CreatedTimestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ModelHealth (
    ModelId TEXT PRIMARY KEY,
    Provider TEXT NOT NULL DEFAULT 'openrouter',
    LastStatus TEXT,
    LastDetail TEXT,
    LimitValue INTEGER,
    Remaining INTEGER,
    ResetAt TEXT,
    AvailableAt TEXT,
    LimitSource TEXT,
    Remedy TEXT,
    Successes INTEGER NOT NULL DEFAULT 0,
    Failures INTEGER NOT NULL DEFAULT 0,
    UpdatedTimestamp TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def init_models(db: sqlite3.Connection) -> None:
    """Create the registry and seed it from the environment on first run.

    Seeding means an existing install keeps exactly the behaviour it had, and
    anyone who already set ``OPENROUTER_MODEL`` does not silently lose it.
    """
    db.executescript(MODELS_SCHEMA)
    present = {row["name"] for row in db.execute("PRAGMA table_info(AssistantModel)")}
    for column, definition in (
        ("Provider", "TEXT NOT NULL DEFAULT 'openrouter'"),
        ("BaseUrl", "TEXT"),
        ("KeyEnv", "TEXT"),
        ("Notes", "TEXT"),
    ):
        if column not in present:
            db.execute(f"ALTER TABLE AssistantModel ADD COLUMN {column} {definition}")
    db.commit()

    if db.execute("SELECT 1 FROM AssistantModel LIMIT 1").fetchone() is not None:
        return

    primary = (os.environ.get("OPENROUTER_MODEL") or "").strip()
    extras = [name.strip() for name in (os.environ.get("OPENROUTER_FALLBACK_MODELS") or "").split(",") if name.strip()]
    chain = [name for name in [primary, *extras] if name] or list(DEFAULT_MODEL_CHAIN)
    for position, model_id in enumerate(chain):
        db.execute(
            """INSERT INTO AssistantModel (Provider, ModelId, Label, Position, Enabled, CreatedTimestamp)
               VALUES (?, ?, ?, ?, 1, ?)""",
            (DEFAULT_PROVIDER, model_id, _friendly(model_id), position, _iso(_now())),
        )
    db.commit()


def _friendly(model_id: str) -> str:
    """A readable label from a model id: 'nvidia/x-120b-a12b:free' -> 'Nemotron 3 Super'."""
    stem = model_id.split("/")[-1].split(":")[0]
    return stem.replace("-", " ").replace("_", " ").title()[:48]


def provider_catalogue() -> list[dict[str, str]]:
    return PROVIDERS


def provider_for(slug: str | None) -> dict[str, str]:
    for provider in PROVIDERS:
        if provider["slug"] == (slug or DEFAULT_PROVIDER):
            return provider
    return PROVIDERS[0]


def list_models(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every configured model, in the order it will be tried.

    The returned shape is the same for every caller, including the endpoints
    that mutate the registry. Decorating only in the read endpoint meant a
    freshly added model came back missing ``usableNow`` and the UI had to handle
    two shapes for the same object - a bug that only shows up on the response the
    user is least likely to test.
    """
    rows = db.execute("SELECT * FROM AssistantModel ORDER BY Position, ModelRowID").fetchall()
    health = {row["ModelId"]: row for row in db.execute("SELECT * FROM ModelHealth").fetchall()}
    shared = shared_limits(db)
    out: list[dict[str, Any]] = []
    for row in rows:
        entry = dict(row)
        provider = entry.get("Provider") or DEFAULT_PROVIDER
        entry["keyEnv"] = entry.get("KeyEnv") or provider_for(provider)["keyEnv"]
        entry["baseUrl"] = entry.get("BaseUrl") or provider_for(provider)["baseUrl"]
        entry["hasKey"] = bool((os.environ.get(entry["keyEnv"]) or "").strip())
        entry["enabled"] = bool(entry.get("Enabled"))
        entry["health"] = decorate_health(dict(health[entry["ModelId"]])) if entry["ModelId"] in health else None
        record = entry.get("health") or {}

        # The daily free budget is shared across every model on the provider. A
        # model with no history therefore still cannot be used while that budget
        # is spent, and saying "usable" about it would send the owner off to
        # switch models that are equally blocked.
        account = shared.get(provider) or {}
        account_spent = bool(account and account.get("Remaining") == 0 and account.get("secondsRemaining", 0) > 0)
        entry["blockedBySharedLimit"] = account_spent and entry["enabled"] and entry["hasKey"]

        if not entry["enabled"]:
            entry["blockedBy"] = "switched off"
        elif not entry["hasKey"]:
            entry["blockedBy"] = f"no key in {entry['keyEnv']}"
        elif account_spent:
            entry["blockedBy"] = f"the shared daily quota is spent, back in {account.get('humanRemaining', 'a while')}"
        elif not record.get("availableNow", True):
            entry["blockedBy"] = f"out of quota, back in {record.get('humanRemaining', 'unknown')}"
        else:
            entry["blockedBy"] = None
        entry["usableNow"] = entry["blockedBy"] is None
        out.append(entry)
    return out


def shared_limits(db: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """The exhausted-or-not daily counter per provider, keyed by provider slug.

    Kept per provider because the budget is per *account on that provider*. Two
    different providers are two different budgets, which is the one legitimate
    reason to add a second provider rather than another model.
    """
    rows = db.execute(
        """SELECT * FROM ModelHealth
           WHERE LimitValue IS NOT NULL AND Provider IS NOT NULL
           ORDER BY UpdatedTimestamp DESC"""
    ).fetchall()
    limits: dict[str, dict[str, Any]] = {}
    for row in rows:
        record = dict(row)
        provider = record.get("Provider") or DEFAULT_PROVIDER
        if provider in limits:
            continue
        limits[provider] = decorate_health(record)
    return limits


def decorate_health(record: dict[str, Any]) -> dict[str, Any]:
    """Turn stored timestamps into something the UI can count down with.

    ``secondsRemaining`` is computed at read time rather than stored, so a page
    that has been open for ten minutes still shows the truth when it refetches.
    """
    now = _now()
    available_at = record.get("AvailableAt")
    remaining = 0
    if available_at:
        try:
            when = datetime.fromisoformat(available_at)
            remaining = max(0, int((when - now).total_seconds()))
        except ValueError:
            remaining = 0
    record["secondsRemaining"] = remaining
    record["availableNow"] = remaining == 0
    record["humanRemaining"] = humanise(remaining)
    return record


def humanise(seconds: int) -> str:
    if seconds <= 0:
        return "now"
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def record_health(
    db: sqlite3.Connection,
    *,
    model_id: str,
    provider: str = DEFAULT_PROVIDER,
    status: str,
    detail: str = "",
    headers: dict[str, str] | None = None,
    limit_source: str = "",
    remedy: str = "",
) -> dict[str, Any]:
    """Store the outcome of asking one model, including when it becomes usable.

    Headers are optional because a success carries them too, but a failure
    without them still has to be recorded honestly: we then know the model
    failed and *not* when it recovers, and the UI says that rather than inventing
    a time.
    """
    header = {k.lower(): v for k, v in (headers or {}).items()}
    limit_value = _int_or_none(header.get("x-ratelimit-limit"))
    remaining = _int_or_none(header.get("x-ratelimit-remaining"))
    reset_epoch_ms = _int_or_none(header.get("x-ratelimit-reset"))

    available_at = None
    reset_at = None
    if reset_epoch_ms:
        try:
            moment = datetime.fromtimestamp(reset_epoch_ms / 1000, timezone.utc)
            reset_at = moment.isoformat()
            if status != "answered":
                available_at = reset_at
        except (OverflowError, OSError, ValueError):
            pass
    if status == "rate limited" and not available_at:
        # A 429 without a reset header: guess nothing, but say so in the remedy.
        remedy = remedy or "the provider did not say when this resets"

    existing = db.execute("SELECT Successes, Failures FROM ModelHealth WHERE ModelId = ?", (model_id,)).fetchone()
    successes = int(existing["Successes"] if existing else 0) + (1 if status == "answered" else 0)
    failures = int(existing["Failures"] if existing else 0) + (0 if status == "answered" else 1)

    db.execute(
        """
        INSERT INTO ModelHealth
            (ModelId, Provider, LastStatus, LastDetail, LimitValue, Remaining, ResetAt, AvailableAt,
             LimitSource, Remedy, Successes, Failures, UpdatedTimestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(ModelId) DO UPDATE SET
            Provider = excluded.Provider,
            LastStatus = excluded.LastStatus,
            LastDetail = excluded.LastDetail,
            LimitValue = COALESCE(excluded.LimitValue, ModelHealth.LimitValue),
            Remaining = COALESCE(excluded.Remaining, ModelHealth.Remaining),
            ResetAt = COALESCE(excluded.ResetAt, ModelHealth.ResetAt),
            AvailableAt = excluded.AvailableAt,
            LimitSource = COALESCE(NULLIF(excluded.LimitSource, ''), ModelHealth.LimitSource),
            Remedy = COALESCE(NULLIF(excluded.Remedy, ''), ModelHealth.Remedy),
            Successes = excluded.Successes,
            Failures = excluded.Failures,
            UpdatedTimestamp = excluded.UpdatedTimestamp
        """,
        (
            model_id,
            provider,
            status,
            (detail or "")[:400],
            limit_value,
            remaining,
            reset_at,
            available_at,
            limit_source,
            remedy,
            successes,
            failures,
            _iso(_now()),
        ),
    )
    db.commit()
    row = db.execute("SELECT * FROM ModelHealth WHERE ModelId = ?", (model_id,)).fetchone()
    return decorate_health(dict(row)) if row else {}


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def add_model(
    db: sqlite3.Connection,
    *,
    model_id: str,
    provider: str = DEFAULT_PROVIDER,
    label: str | None = None,
    base_url: str | None = None,
    key_env: str | None = None,
    notes: str | None = None,
    enabled: bool = True,
) -> dict[str, Any]:
    """Register a model. Any OpenAI-compatible id is accepted, on purpose.

    Nothing here validates the name against a list, because the whole point is
    that the owner can bring a model this code has never heard of - a new free
    release, a local Ollama tag, a self-hosted endpoint.
    """
    model_id = (model_id or "").strip()
    if not model_id:
        raise ValueError("a model id is required")
    provider = (provider or DEFAULT_PROVIDER).strip()
    catalogue = provider_for(provider)
    next_position = db.execute("SELECT COALESCE(MAX(Position), -1) + 1 AS n FROM AssistantModel").fetchone()["n"]
    cursor = db.execute(
        """INSERT INTO AssistantModel
               (Provider, ModelId, Label, BaseUrl, KeyEnv, Position, Enabled, Notes, CreatedTimestamp)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            provider,
            model_id,
            (label or "").strip() or _friendly(model_id),
            (base_url or "").strip() or catalogue["baseUrl"],
            (key_env or "").strip() or catalogue["keyEnv"],
            int(next_position),
            1 if enabled else 0,
            (notes or "").strip(),
            _iso(_now()),
        ),
    )
    db.commit()
    return {"added": True, "rowId": int(cursor.lastrowid), "modelId": model_id}


def update_model(db: sqlite3.Connection, row_id: int, changes: dict[str, Any]) -> dict[str, Any]:
    """Edit a registered model: rename, retarget, enable, disable, reposition."""
    allowed = {
        "ModelId": "modelId",
        "Label": "label",
        "Provider": "provider",
        "BaseUrl": "baseUrl",
        "KeyEnv": "keyEnv",
        "Notes": "notes",
        "Enabled": "enabled",
        "Position": "position",
    }
    assignments: list[str] = []
    params: list[Any] = []
    for column, key in allowed.items():
        if key not in changes or changes[key] is None:
            continue
        value = changes[key]
        if column == "Enabled":
            value = 1 if value else 0
        if column == "Position":
            value = int(value)
        assignments.append(f"{column} = ?")
        params.append(value)
    if not assignments:
        return {"updated": False, "reason": "nothing to change"}
    params.append(int(row_id))
    db.execute(f"UPDATE AssistantModel SET {', '.join(assignments)} WHERE ModelRowID = ?", params)
    db.commit()
    return {"updated": True, "rowId": int(row_id)}


def remove_model(db: sqlite3.Connection, row_id: int) -> dict[str, Any]:
    row = db.execute("SELECT ModelId FROM AssistantModel WHERE ModelRowID = ?", (row_id,)).fetchone()
    if row is None:
        return {"removed": False, "reason": "not found"}
    db.execute("DELETE FROM AssistantModel WHERE ModelRowID = ?", (row_id,))
    db.commit()
    # Health is deliberately kept: re-adding a model should not erase the fact
    # that it was rate limited five minutes ago.
    return {"removed": True, "modelId": row["ModelId"]}


def reorder(db: sqlite3.Connection, order: list[int]) -> dict[str, Any]:
    for position, row_id in enumerate(order):
        db.execute("UPDATE AssistantModel SET Position = ? WHERE ModelRowID = ?", (position, int(row_id)))
    db.commit()
    return {"reordered": len(order)}


def resolve_chain(db: sqlite3.Connection) -> list[dict[str, Any]]:
    """The models to try, with their endpoint and key resolved, skipping dead ones.

    A model whose reset time has not passed is skipped rather than called and
    refused - that is both faster and the difference between the UI saying "this
    one is out of quota until 00:00" and it saying "the assistant was
    unavailable" for a reason nobody can act on.
    """
    chain: list[dict[str, Any]] = []
    for entry in list_models(db):
        if not entry["enabled"] or not entry["hasKey"]:
            continue
        health = entry.get("health") or {}
        chain.append(
            {
                "model": entry["ModelId"],
                "provider": entry.get("Provider") or DEFAULT_PROVIDER,
                "baseUrl": entry["baseUrl"],
                "apiKeyEnv": entry["keyEnv"],
                "label": entry.get("Label"),
                "rowId": entry["ModelRowID"],
                "coolingDown": not health.get("availableNow", True),
                "secondsRemaining": health.get("secondsRemaining", 0),
                "humanRemaining": health.get("humanRemaining", "now"),
            }
        )
    return chain


def account_limit(db: sqlite3.Connection) -> dict[str, Any] | None:
    """The shared free-tier counter, read from whichever model last reported it.

    Worth surfacing separately because it is account-wide: a user who sees four
    models fail should be told they share one budget, not left to conclude that
    all four models are broken.
    """
    limits = shared_limits(db)
    if not limits:
        return None
    record = next(iter(limits.values()))
    return {
        "limit": record.get("LimitValue"),
        "remaining": record.get("Remaining"),
        "resetAt": record.get("ResetAt"),
        "availableAt": record.get("AvailableAt"),
        "secondsRemaining": record.get("secondsRemaining"),
        "humanRemaining": record.get("humanRemaining"),
        "limitSource": record.get("LimitSource"),
        "remedy": record.get("Remedy"),
        "provider": record.get("Provider"),
        "scope": "account",
        "explanation": (
            "Free models share one daily budget for the whole account, not one each. "
            "Switching models does not add requests - it only changes which model refuses you."
        ),
    }
