"""The learning layer: the assistant gets better at *this* business over time.

The local model is trained on generated phrasings. However good those are, they
are not the sentences the owner actually types. This module closes that gap:

* Every turn is written down - the question, what the engine read it as, the
  answer, which records grounded it, and whether the owner found it useful.
* When the owner corrects a reading, by picking a clarifying option or telling
  the assistant what they meant, that is a labelled example. It is stored as a
  confirmed question-to-intent pair.
* On the next rebuild, those confirmed examples are merged into the training set
  alongside the generated templates, so the classifier learns the owner's own
  vocabulary instead of only the templates somebody wrote in advance.
* Every retrain records its holdout score, so the "is it actually improving"
  question has a tracked answer rather than an assumption.

Nothing here is a black box: the learned rows are visible through the API, and a
question only becomes training data once a human has confirmed what it meant.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS CopilotTurn (
    TurnID INTEGER PRIMARY KEY AUTOINCREMENT,
    SessionID TEXT NOT NULL,
    Question TEXT NOT NULL,
    Answer TEXT NOT NULL,
    Intent TEXT NOT NULL,
    ConfirmedIntent TEXT,
    Sector TEXT NOT NULL,
    Assistant TEXT NOT NULL,
    Confidence REAL NOT NULL DEFAULT 0,
    SourcesJson TEXT NOT NULL DEFAULT '[]',
    Rating INTEGER NOT NULL DEFAULT 0,
    Note TEXT,
    PayloadJson TEXT NOT NULL DEFAULT '{}',
    CreatedTimestamp TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ModelSnapshot (
    SnapshotID INTEGER PRIMARY KEY AUTOINCREMENT,
    Sector TEXT NOT NULL,
    HoldoutAccuracy REAL NOT NULL,
    HoldoutWorst REAL NOT NULL,
    TrainingRows INTEGER NOT NULL,
    LearnedRows INTEGER NOT NULL,
    CreatedTimestamp TEXT NOT NULL
);
"""


def init_memory(db: sqlite3.Connection) -> None:
    db.executescript(MEMORY_SCHEMA)
    # The column was added after the first release, so an existing database is
    # upgraded in place rather than losing its history.
    present = {row["name"] for row in db.execute("PRAGMA table_info(CopilotTurn)")}
    if "PayloadJson" not in present:
        db.execute("ALTER TABLE CopilotTurn ADD COLUMN PayloadJson TEXT NOT NULL DEFAULT '{}'")
    db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_turn(
    db: sqlite3.Connection,
    *,
    session_id: str,
    question: str,
    answer: str,
    intent: str,
    sector: str,
    assistant: str,
    confidence: float,
    sources: list[dict[str, Any]],
    payload: dict[str, Any] | None = None,
) -> int:
    """Write one turn down and return its id, which the UI needs for feedback.

    The whole response is stored, not just the prose, so reopening the copilot
    restores the figures, the steps and the citations rather than a bare line of
    text.
    """
    cursor = db.execute(
        """
        INSERT INTO CopilotTurn
            (SessionID, Question, Answer, Intent, Sector, Assistant, Confidence, SourcesJson, PayloadJson, CreatedTimestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            session_id or "default",
            question,
            answer,
            intent,
            sector,
            assistant,
            float(confidence),
            json.dumps(sources)[:20000],
            json.dumps(payload or {})[:60000],
            _now(),
        ),
    )
    db.commit()
    return int(cursor.lastrowid)


def learn_intent(
    db: sqlite3.Connection,
    *,
    turn_id: int | None = None,
    question: str | None = None,
    intent: str,
    session_id: str = "default",
    sector: str = "",
) -> dict[str, Any]:
    """Record a reading the owner has confirmed.

    Called when a clarifying option is picked, or when a thumbs-up carries the
    intent that was used. The row becomes training data on the next rebuild.
    """
    if turn_id is not None:
        db.execute("UPDATE CopilotTurn SET ConfirmedIntent = ? WHERE TurnID = ?", (intent, int(turn_id)))
        db.commit()
        row = db.execute("SELECT Question FROM CopilotTurn WHERE TurnID = ?", (turn_id,)).fetchone()
        question = (row["Question"] if row else question) or ""
    if not question:
        return {"learned": False, "reason": "nothing to learn from"}

    existing = db.execute(
        "SELECT TurnID FROM CopilotTurn WHERE Question = ? AND ConfirmedIntent = ? AND Sector = ? LIMIT 1",
        (question, intent, sector),
    ).fetchone()
    if existing and turn_id is None:
        return {"learned": True, "reason": "already known", "turnId": int(existing["TurnID"])}

    if turn_id is None:
        cursor = db.execute(
            """
            INSERT INTO CopilotTurn
                (SessionID, Question, Answer, Intent, ConfirmedIntent, Sector, Assistant, Confidence, SourcesJson, CreatedTimestamp)
            VALUES (?, ?, '', ?, ?, ?, 'confirmed by owner', 1.0, '[]', ?)
            """,
            (session_id, question, intent, intent, sector, _now()),
        )
        db.commit()
        return {"learned": True, "reason": "stored as a confirmed example", "turnId": int(cursor.lastrowid)}

    return {"learned": True, "reason": "confirmed reading stored for the next retrain", "turnId": turn_id}


def rate_turn(
    db: sqlite3.Connection,
    *,
    turn_id: int,
    rating: int,
    note: str | None = None,
    intent: str | None = None,
) -> dict[str, Any]:
    """Store the owner's verdict on an answer.

    A thumbs-up with an intent is treated as a confirmation and becomes training
    data. A thumbs-down is recorded as a signal but is deliberately not learned
    as a positive example, so a bad answer cannot teach the model to repeat it.
    """
    db.execute("UPDATE CopilotTurn SET Rating = ?, Note = ? WHERE TurnID = ?", (rating, note, turn_id))
    if rating > 0 and intent:
        db.execute("UPDATE CopilotTurn SET ConfirmedIntent = ? WHERE TurnID = ?", (intent, turn_id))
    db.commit()
    return {
        "turnId": turn_id,
        "rating": rating,
        "learned": bool(rating > 0 and intent),
        "meaning": "Thanks - that reading is now part of what this model learns from."
        if rating > 0 and intent
        else ("Noted. That answer will not be learned from." if rating < 0 else "Noted."),
    }


def training_examples(db: sqlite3.Connection, sector: str | None = None) -> list[dict[str, Any]]:
    """Confirmed question-to-intent pairs, ready to merge into the training set.

    Only rows with a confirmed intent count, and a thumbs-down cancels the row
    out, so the model learns from readings a human stood behind.
    """
    query = """
        SELECT Question, ConfirmedIntent AS Intent, COUNT(*) AS TimesSeen
        FROM CopilotTurn
        WHERE ConfirmedIntent IS NOT NULL AND ConfirmedIntent != '' AND Rating >= 0
    """
    params: list[Any] = []
    if sector:
        query += " AND Sector = ?"
        params.append(sector)
    query += " GROUP BY Question, ConfirmedIntent ORDER BY TimesSeen DESC LIMIT 500"
    return [dict(row) for row in db.execute(query, params).fetchall()]


def recent_turns(db: sqlite3.Connection, session_id: str, limit: int = 40) -> list[dict[str, Any]]:
    """The expanded conversation for one session, oldest first."""
    rows = db.execute(
        "SELECT * FROM CopilotTurn WHERE SessionID = ? AND Answer != '' ORDER BY TurnID DESC LIMIT ?",
        (session_id or "default", limit),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows[::-1]:
        entry = dict(row)
        for column, target, fallback in (("SourcesJson", "Sources", []), ("PayloadJson", "Payload", {})):
            raw = entry.pop(column, None)
            try:
                entry[target] = json.loads(raw) if raw else fallback
            except ValueError:
                entry[target] = fallback
        out.append(entry)
    return out


def snapshot_model(
    db: sqlite3.Connection,
    *,
    sector: str,
    holdout_accuracy: float,
    holdout_worst: float,
    training_rows: int,
    learned_rows: int,
) -> None:
    """Record a retrain's score so improvement (or drift) is visible over time."""
    db.execute(
        """
        INSERT INTO ModelSnapshot
            (Sector, HoldoutAccuracy, HoldoutWorst, TrainingRows, LearnedRows, CreatedTimestamp)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (sector, float(holdout_accuracy), float(holdout_worst), int(training_rows), int(learned_rows), _now()),
    )
    db.commit()


def stats(db: sqlite3.Connection, sector: str | None = None) -> dict[str, Any]:
    """What the assistant has learned, and how it has been received."""
    where, params = ("WHERE Sector = ?", [sector]) if sector else ("", [])
    totals = db.execute(
        f"SELECT COUNT(*) AS turns, COALESCE(SUM(Rating > 0), 0) AS helpful, "
        f"COALESCE(SUM(Rating < 0), 0) AS unhelpful, "
        f"COALESCE(SUM(ConfirmedIntent IS NOT NULL AND ConfirmedIntent != ''), 0) AS confirmed "
        f"FROM CopilotTurn {where}",
        params,
    ).fetchone()

    learned = training_examples(db, sector)
    per_intent: dict[str, int] = {}
    for example in learned:
        per_intent[example["Intent"]] = per_intent.get(example["Intent"], 0) + 1

    snapshots = db.execute(
        "SELECT * FROM ModelSnapshot " + ("WHERE Sector = ? " if sector else "") + "ORDER BY SnapshotID DESC LIMIT 12",
        params,
    ).fetchall()
    history = [dict(row) for row in snapshots][::-1]

    asked_sql = (
        "SELECT Question, Intent, ConfirmedIntent, Rating, CreatedTimestamp FROM CopilotTurn "
        + ("WHERE Sector = ? AND Answer != '' " if sector else "WHERE Answer != '' ")
        + "ORDER BY TurnID DESC LIMIT 12"
    )
    asked = db.execute(asked_sql, params).fetchall()

    return {
        "turns": int(totals["turns"] or 0),
        "helpful": int(totals["helpful"] or 0),
        "unhelpful": int(totals["unhelpful"] or 0),
        "confirmedReadings": int(totals["confirmed"] or 0),
        "learnedExamples": len(learned),
        "learnedPerIntent": per_intent,
        "accuracyHistory": history,
        "recentQuestions": [dict(row) for row in asked],
        "learningRule": (
            "Only a reading a human confirmed becomes training data. A thumbs-down is "
            "recorded but never taught, so a bad answer cannot be reinforced."
        ),
    }
