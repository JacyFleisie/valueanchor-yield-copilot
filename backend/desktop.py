"""The desktop entry point: the whole product behind one double-click.

``ValueAnchor.exe`` is this module frozen with PyInstaller. It only does the
things a packaged desktop app needs and that a source checkout gets from the
shell: it picks a writable place for the database and the trained-model cache,
finds a free port, starts the same FastAPI app the checkout runs, and opens the
browser at the address it is listening on.

The interface travels inside the executable as static files (see
``app.py`` -> ``UI_DIR``), so there is no Node.js on the machine, no second
process and no CORS between an interface on one port and an engine on another.
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

APP_NAME = "ValueAnchor"
FIRST_PORT = 8000
LAST_PORT = 8050


def _writable(path: Path) -> bool:
    """Whether this app can create and write inside ``path``.

    The exe is portable by design - its data lives beside it - but an owner can
    still drop it into a read-only folder such as Program Files, so the answer
    is checked rather than assumed.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".{APP_NAME.lower()}-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _data_dir() -> Path:
    """Where the database, the model cache and an optional ``.env`` live.

    Beside the executable when that folder is writable, which keeps a copied
    ``ValueAnchor.exe`` self-contained; otherwise the per-user application-data
    folder, so the app still runs from a read-only location.
    """
    if getattr(sys, "frozen", False):
        beside_the_app = Path(sys.executable).resolve().parent
    else:
        beside_the_app = Path(__file__).resolve().parent
    if _writable(beside_the_app):
        return beside_the_app
    fallback = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / APP_NAME
    _writable(fallback)
    return fallback


def _free_port(preferred: int) -> int:
    """``preferred`` when it is free, otherwise the first free port near it.

    A second copy of the app, or anything else already on 8000, must not turn
    the first double-click into a crash. The interface uses relative URLs, so it
    works on whichever port the engine actually got.
    """
    candidates = [preferred] + [
        port for port in range(FIRST_PORT, LAST_PORT + 1) if port != preferred
    ]
    for port in candidates:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise SystemExit(f"No free port between {FIRST_PORT} and {LAST_PORT}.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="ValueAnchor - the yield copilot, running on this machine.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("VALUEANCHOR_PORT") or FIRST_PORT),
        help=f"port to listen on (default {FIRST_PORT}; the next free one is used if taken)",
    )
    parser.add_argument(
        "--db",
        default="",
        help="database file to use (default: valueanchor.db beside the executable)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="start the server without opening a browser window",
    )
    args = parser.parse_args(argv)

    data = _data_dir()
    # Set before importing the app: its paths are resolved at import time, and
    # its `.env` loader looks beside the executable first.
    if args.db:
        os.environ["VALUEANCHOR_DB"] = str(Path(args.db).expanduser().resolve())
    else:
        os.environ.setdefault("VALUEANCHOR_DB", str(data / "valueanchor.db"))
    os.environ.setdefault("VALUEANCHOR_MODEL_CACHE", str(data / ".model_cache"))

    import uvicorn

    from app import app as application

    port = _free_port(args.port)
    url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=port,
            log_level="info",
            # Nothing in this app speaks WebSocket; "none" keeps uvicorn from
            # looking for an optional protocol library the exe does not ship.
            ws="none",
        )
    )

    def open_when_ready() -> None:
        # uvicorn raises `started` once the socket is accepting; opening earlier
        # gives the owner a connection-refused page on a slow first start.
        for _ in range(600):
            if server.started:
                webbrowser.open(url)
                return
            time.sleep(0.1)

    if not args.no_browser:
        threading.Thread(target=open_when_ready, daemon=True).start()

    print("", flush=True)
    print(f"  {APP_NAME} is running at {url}", flush=True)
    print(f"  Your data: {os.environ['VALUEANCHOR_DB']}", flush=True)
    print("  Keep this window open while you use it; close it to stop.", flush=True)
    print("", flush=True)

    server.run()
    return 0


if __name__ == "__main__":
    # Required for a frozen build on Windows: without it, a process spawned by
    # a library (joblib, for one) would re-run the exe instead of the worker.
    multiprocessing.freeze_support()
    raise SystemExit(main())
