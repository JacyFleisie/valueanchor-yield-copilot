"""Build the Windows desktop executable.

    python packaging/build_exe.py

Produces ``dist/ValueAnchor.exe``: one file, no installer, no Python and no
Node.js needed to run it. The steps are exactly what a release needs and
nothing else:

1. Build the interface as a static export (``frontend/out``).
2. Freeze the FastAPI backend plus that export with PyInstaller.

The interface is embedded under ``frontend_out/`` and the ABL sources under
``openedge/``, which is where ``app.py`` and ``openedge_bridge.py`` look when
they are frozen. The OpenEdge *runtime* artifacts (``.b1``/``.d1``/logs) are
deliberately not embedded: they belong to the owner's installation, not ours.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
BACKEND = ROOT / "backend"
EXPORT = FRONTEND / "out"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
NAME = "ValueAnchor"


def run(command: list[str], cwd: Path) -> None:
    # On Windows `npm` is `npm.cmd`, which CreateProcess will not find from the
    # bare name, so the resolved path is used.
    executable = shutil.which(command[0])
    if executable is None:
        raise SystemExit(
            f"{command[0]} was not found on PATH. Install Node.js 20 or newer "
            "and run the build again."
        )
    command = [executable, *command[1:]]
    print(f"\n> {' '.join(command)}  (in {cwd})", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def build_interface() -> None:
    """Compile the Next.js app to static files the backend can serve."""
    if not (FRONTEND / "node_modules").is_dir():
        # `npm ci` uses the lockfile, so a release is built from exactly the
        # dependency versions that were tested.
        run(["npm", "ci"], cwd=FRONTEND)
    run(["npm", "run", "build"], cwd=FRONTEND)
    index = EXPORT / "index.html"
    if not index.is_file():
        raise SystemExit(f"Static export did not produce {index}")
    print(f"Interface built: {sum(1 for _ in EXPORT.rglob('*') if _.is_file())} files in {EXPORT}")


def freeze_backend() -> Path:
    """Freeze the backend and the export into a single executable."""
    # PyInstaller must be installed in the interpreter running this script.
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit(
            "PyInstaller is missing. Install the build dependencies first:\n"
            f"  {sys.executable} -m pip install -r packaging/requirements-build.txt"
        ) from None

    # uvicorn resolves its protocol implementations by string at runtime, so the
    # analyser cannot see them from the imports alone. Everything that can be
    # selected on this install is listed; `ws` needs nothing because the desktop
    # entry point runs uvicorn with WebSocket support switched off.
    hidden = [
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.http.httptools_impl",
        "uvicorn.lifespan.on",
    ]

    add_data = [f"{EXPORT}{os.pathsep}frontend_out"]
    # Only the ABL/SQL source travels with the app: the databases, logs and
    # generated .b1/.d1 files are the owner's runtime, not our release.
    for source in sorted((ROOT / "openedge").iterdir()):
        if source.suffix in {".p", ".i", ".sql"}:
            add_data.append(f"{source}{os.pathsep}openedge")

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--console",
        "--name",
        NAME,
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD),
        "--specpath",
        str(BUILD),
        "--paths",
        str(BACKEND),
        "--collect-data",
        "symspellpy",
        "--exclude-module",
        "pytest",
        "--exclude-module",
        "_pytest",
    ]
    for module in hidden:
        command += ["--hidden-import", module]
    for entry in add_data:
        command += ["--add-data", entry]
    command.append(str(BACKEND / "desktop.py"))

    run(command, cwd=ROOT)

    executable = DIST / f"{NAME}.exe"
    if not executable.is_file():
        raise SystemExit(f"PyInstaller finished without producing {executable}")
    return executable


def main() -> int:
    if os.name != "nt":
        raise SystemExit(
            "This builds the Windows executable and must run on Windows. "
            "The source checkout still runs cross-platform; see README.md."
        )
    build_interface()
    executable = freeze_backend()
    size_mb = executable.stat().st_size / (1024 * 1024)
    print(f"\nBuilt {executable} ({size_mb:.0f} MB)")
    print("Double-click it, or run it with --no-browser to start without a browser.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
