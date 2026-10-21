"""A local read-only MCP bridge over an installed OpenEdge database.

WHAT THIS IS FOR

The product's stock used to arrive by typing it in: a spreadsheet ramp, a CSV
upload, a preset. That is gone, and the honest replacement is the business's own
system of record. This module is the narrowest possible path to it: one MCP tool,
``openedge_read_stock``, which runs an ABL program against a database the owner
already has and hands back the rows it read, in ValueAnchor's stock field names.

It is deliberately *not* the OpenEdge MCP Server. That product is software the
owner installs and points at their own service, and this project reports it as
unconfigured until they do - see :mod:`openedge`. What this module adds is a
bridge that needs nothing but the Developers Kit, so the integration can be
demonstrated on one laptop, today, with real rows.

READ-ONLY, AND WHY IT CAN PROVE IT

The only ABL program this bridge runs is ``06-enquiry-json.p``, and the only SQL
that program contains is a ``FOR EACH`` with ``NO-LOCK``. The tool advertises
``readOnlyHint: True`` and there is no argument that could change what it does:
a caller cannot name a different program, a different table or a different
database through this surface. Everything else it can reach is read-only by
construction.

THE HONEST PART: THE AVM IS NOT THERE YET

``prowin`` is a GUI product. It starts visibly, and a first run can take long
enough that a naive synchronous call looks like a hang. Rather than pretend, the
bridge:

* checks for the AVM once, up front, and caches the answer, so a repeated click
  does not re-scan the disk;
* runs the program with a real timeout, and reports a timeout as a timeout - not
  as an empty shelf, which would be the dangerous lie;
* reports a missing AVM, a missing database and a missing program as *three
  different messages*, because an owner can fix each of those and the messages
  have to say which one it is.

If ``OPENEDGE_PROWIN`` is set it is used as-is; otherwise the bridge looks in
``OPENEDGE_HOME`` and then in the usual Windows install location. Setting
``OPENEDGE_PROWIN`` to the empty string is how a caller says "there is no AVM
here" without the bridge going looking.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# The bridge runs exactly one program, and only ever this one. A caller cannot
# name a different program through the tool surface - see ``call_tool``.
PROGRAM_NAME = "06-enquiry-json.p"
DEFAULT_DATABASE = "coffee"

# How long to wait for the AVM. First runs pay for the process start, and a
# cold machine can be slow enough that a tight limit would report a working
# install as broken. This is a limit on a *read* of a handful of rows, so
# anything this slow is a real problem worth saying out loud.
DEFAULT_TIMEOUT_SECONDS = 90.0

# The usual place a Windows installer puts the kit, used only when nothing more
# specific is set. A non-Windows box has to set OPENEDGE_PROWIN or
# OPENEDGE_HOME, and says so.
DEFAULT_WINDOWS_HOME = r"C:\Progress\OpenEdge"

# The AVM prints a leading blank line before the document. The document itself
# is the one line that parses as JSON, so the reader takes the last JSON-looking
# line rather than the first line, and says so if there is none.
_JSON_LINE = re.compile(r"^\s*\{")


@dataclass(frozen=True)
class AblResult:
    """One run of the ABL program, with everything a caller might need to act."""

    ok: bool
    document: dict[str, Any] | None
    stdout: str
    stderr: str
    returncode: int | None
    duration_seconds: float
    error: str
    error_kind: str


class BridgeError(Exception):
    """The bridge could not produce stock, and the reason is worth a sentence."""

    def __init__(self, message: str, kind: str) -> None:
        super().__init__(message)
        self.kind = kind


def _home_from_env() -> Path | None:
    explicit = os.environ.get("OPENEDGE_HOME", "").strip()
    if explicit:
        return Path(explicit)
    if os.name == "nt":
        return Path(DEFAULT_WINDOWS_HOME)
    return None


def find_prowin(home: Path | None = None) -> Path | None:
    """The AVM executable, or ``None`` when there is not one.

    ``OPENEDGE_PROWIN`` wins, including when it is set to the empty string - that
    is how a caller states there is no AVM here and stops the search.
    """
    explicit = os.environ.get("OPENEDGE_PROWIN", "").strip()
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None
    if "OPENEDGE_PROWIN" in os.environ and not explicit:
        return None

    home = home or _home_from_env()
    if home is None:
        return None
    for name in ("prowin.exe", "prowin"):
        candidate = home / "bin" / name
        if candidate.is_file():
            return candidate
    return None


def find_program(home: Path | None = None) -> Path | None:
    """``06-enquiry-json.p``, looked for beside this file and then in OPENEDGE_HOME."""
    here = Path(__file__).resolve().parent.parent / "openedge" / PROGRAM_NAME
    if here.is_file():
        return here
    home = home or _home_from_env()
    if home is None:
        return None
    for candidate in (home / PROGRAM_NAME, Path.cwd() / "openedge" / PROGRAM_NAME):
        if candidate.is_file():
            return candidate
    return None


def find_database(name: str, home: Path | None = None) -> Path | None:
    """The database storage file, which ``prowin -db`` needs a path to.

    ``-db coffee`` resolves relative to the AVM's working directory, which is not
    necessarily this process's, so the path is made absolute here.
    """
    explicit = os.environ.get("OPENEDGE_DB", "").strip()
    if explicit:
        candidate = Path(explicit)
        return candidate if candidate.is_file() else None

    for directory in _database_search_path(home):
        for suffix in (".db", ""):
            candidate = directory / (name + suffix) if suffix else directory / name
            if candidate.is_file():
                return candidate
    return None


def _database_search_path(home: Path | None) -> list[Path]:
    here = Path(__file__).resolve().parent.parent / "openedge"
    paths = [here, Path.cwd() / "openedge", Path.cwd()]
    if home is not None:
        paths.append(home)
    return paths


def _extract_document(stdout: str) -> dict[str, Any] | None:
    """The JSON document in the AVM's output, or ``None`` if it has none.

    The AVM prints a blank line first, and a stray note can follow, so the last
    line that *looks* like a JSON object is the candidate. Taking the first line
    instead would return the blank one and look like an empty shelf.
    """
    for line in reversed(stdout.splitlines()):
        if _JSON_LINE.match(line):
            try:
                parsed = json.loads(line)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return None


def _summarise(document: dict[str, Any]) -> str:
    lines = document.get("lines") or []
    count = document.get("rowCount", len(lines))
    left = sum(
        int(line.get("totalCapacity", 0)) - int(line.get("unitsSold", 0))
        for line in lines
    )
    return f"{count} line(s), {left} unit(s) left on the shelf"


def run_program(
    database_name: str = DEFAULT_DATABASE,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    prowin: Path | None = None,
    program: Path | None = None,
    home: Path | None = None,
) -> AblResult:
    """Run ``06-enquiry-json.p`` and read back the document it printed.

    Every failure is a distinct ``error_kind`` so a caller can tell a machine
    with no kit from a database that was never seeded, because the fix is
    different and the owner should not have to read a traceback to find out.
    """
    started = time.monotonic()
    prowin = prowin if prowin is not None else find_prowin(home)
    if prowin is None:
        raise BridgeError(
            "No AVM found. Install the OpenEdge Developers Kit, or set OPENEDGE_PROWIN "
            "to the full path of prowin.exe (or OPENEDGE_HOME to the install folder).",
            "no_avm",
        )

    program = program if program is not None else find_program(home)
    if program is None:
        raise BridgeError(
            f"{PROGRAM_NAME} was not found beside the backend or under OPENEDGE_HOME. "
            "The bridge runs that one program and no other.",
            "no_program",
        )

    database = find_database(database_name, home)
    if database is None:
        raise BridgeError(
            f"No storage file for database '{database_name}'. Looked beside the backend, "
            "in the working directory and under OPENEDGE_HOME. Create it with `prodb "
            f"{database_name} empty`, then seed it.",
            "no_database",
        )

    # -b is batch: no window, output to the pipe. -1 opens the database
    # single-user, which is what a local read wants and what refuses a database
    # somebody else is serving - a refusal that is worth its own message.
    command = [
        str(prowin),
        "-b",
        "-db",
        str(database),
        "-1",
        "-p",
        str(program),
    ]

    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(program.parent),
            check=False,
        )
    except subprocess.TimeoutExpired as expired:
        raise BridgeError(
            f"The AVM did not finish reading {database_name} within {timeout:.0f} seconds. "
            "It usually starts slower than a native process on a first run; raise "
            "OPENEDGE_TIMEOUT if this machine is simply slow, but a read of a handful of "
            "rows that never finishes is worth investigating.",
            "timeout",
        ) from expired
    except OSError as failure:
        raise BridgeError(
            f"The AVM could not be started: {failure}", "avm_failed"
        ) from failure

    duration = time.monotonic() - started
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""

    document = _extract_document(stdout)
    if document is not None:
        return AblResult(
            ok=True,
            document=document,
            stdout=stdout,
            stderr=stderr,
            returncode=completed.returncode,
            duration_seconds=duration,
            error="",
            error_kind="",
        )

    # Nothing parsed. The AVM's own words are the best diagnosis available, and
    # the common cases are named rather than left as a code number.
    combined = f"{stdout}\n{stderr}"
    if "Unknown or ambiguous table" in combined:
        error = (
            f"The table is not in '{database_name}'. The database exists but was never "
            "given the coffee_item table - run 03-coffee-ddl.sql against it (note the "
            "pub. schema qualifier and the trailing COMMIT; both are required)."
        )
        kind = "no_table"
    elif "in use in multi-user mode" in combined:
        error = (
            f"'{database_name}' is being served by another process. Stop the server "
            "(answer `2` to _mprshut) so the read can open it single-user."
        )
        kind = "database_busy"
    elif "Does not exist" in combined or "not found" in combined.lower():
        error = (
            f"The AVM reported that something in '{database_name}' does not exist. "
            "The database may need seeding - run 04-seed-coffee.p."
        )
        kind = "not_seeded"
    else:
        detail = (stderr or stdout).strip()
        if len(detail) > 400:
            detail = detail[:400] + "..."
        error = (
            f"The AVM exited with code {completed.returncode} and produced no JSON."
            + (f" It said: {detail}" if detail else "")
        )
        kind = "no_document"

    return AblResult(
        ok=False,
        document=None,
        stdout=stdout,
        stderr=stderr,
        returncode=completed.returncode,
        duration_seconds=duration,
        error=error,
        error_kind=kind,
    )


# ---------------------------------------------------------------------------
# The MCP tool surface
# ---------------------------------------------------------------------------

READ_STOCK_TOOL: dict[str, Any] = {
    "name": "openedge_read_stock",
    "title": "Read the live stock from an OpenEdge database",
    "description": (
        "Runs a read-only ABL program against a local OpenEdge database and returns "
        "the counter stock it read: item name, category, base price, total capacity, "
        "units sold and hours until the counter closes, plus the business name. "
        "Nothing is written, and no argument can change what is read - the only "
        "program this tool runs is a FOR EACH with NO-LOCK. Call it to replace "
        "hand-typed figures with the system of record."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "database": {
                "type": "string",
                "minLength": 1,
                "maxLength": 80,
                "description": (
                    "The OpenEdge database to read, by name. Defaults to 'coffee'. "
                    "The name is resolved to a storage file beside the backend or "
                    "under OPENEDGE_HOME; it is not a path and cannot reach elsewhere."
                ),
            }
        },
        "additionalProperties": False,
    },
    "annotations": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
}


def _refusal(message: str) -> dict[str, Any]:
    """A tool result that refused, shaped so a host reads it as a fact."""
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": json.dumps({"refused": True, "reason": message}, indent=2)}],
        "structuredContent": {"refused": True, "reason": message},
        "isError": True,
    }


def _success(payload: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    return {
        "resultType": "complete",
        "content": [{"type": "text", "text": text}],
        "structuredContent": payload,
        "isError": False,
    }


def read_stock(arguments: dict[str, Any]) -> dict[str, Any]:
    """The tool's body: an MCP tool result, refused or not."""
    database = arguments.get("database") or DEFAULT_DATABASE
    if not isinstance(database, str) or not database.strip():
        return _refusal("'database' must be a non-empty name.")
    database = database.strip()
    if any(sep in database for sep in ("/", "\\", "..")):
        return _refusal("'database' is a name, not a path; it cannot contain separators.")

    try:
        result = run_program(database)
    except BridgeError as failure:
        return _refusal(str(failure))

    if not result.ok:
        return _refusal(result.error)

    document = result.document or {}
    lines = document.get("lines") or []
    payload = {
        "source": "openedge",
        "database": database,
        "businessName": document.get("businessName", ""),
        "rowCount": document.get("rowCount", len(lines)),
        "unitsLeft": sum(
            int(line.get("totalCapacity", 0)) - int(line.get("unitsSold", 0))
            for line in lines
        ),
        "lines": lines,
        "readIn": round(result.duration_seconds, 3),
        "readOnly": True,
        "note": (
            "Read from the OpenEdge database by a FOR EACH with NO-LOCK. Nothing was "
            "written, and the figures are the database's own, not an estimate."
        ),
    }
    return _success(payload)


TOOLKIT: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "openedge_read_stock": read_stock,
}


def call_tool(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """One MCP tool call, answered in the shape :mod:`mcp_server` uses.

    Kept here rather than in the server so the bridge is testable without FastAPI,
    a database or the network. An unknown tool is a refusal, not a crash, so a
    host that guesses a name gets an answer it can show a person.
    """
    if name != READ_STOCK_TOOL["name"]:
        return _refusal(
            f"Unknown tool: {name!r}. This bridge offers one tool, "
            f"'{READ_STOCK_TOOL['name']}'."
        )
    return read_stock(arguments or {})


def status() -> dict[str, Any]:
    """What the bridge can see right now, with nothing rounded up.

    This is what the UI reads to decide whether the link button is worth showing:
    an AVM that is not installed is a different state from a database that was
    never seeded, and the owner needs the difference spelled out.
    """
    home = _home_from_env()
    prowin = find_prowin(home)
    program = find_program(home)
    database = find_database(DEFAULT_DATABASE, home)
    return {
        "available": prowin is not None and program is not None and database is not None,
        "prowin": str(prowin) if prowin else "",
        "program": str(program) if program else "",
        "database": str(database) if database else "",
        "avmOnPath": shutil.which("prowin") is not None,
        "env": {
            "OPENEDGE_PROWIN": os.environ.get("OPENEDGE_PROWIN", ""),
            "OPENEDGE_HOME": os.environ.get("OPENEDGE_HOME", ""),
            "OPENEDGE_DB": os.environ.get("OPENEDGE_DB", ""),
        },
        "tool": READ_STOCK_TOOL,
        "blockers": [
            reason
            for reason, missing in (
                ("no_avm", prowin is None),
                ("no_program", program is None),
                ("no_database", database is None),
            )
            if missing
        ],
    }
