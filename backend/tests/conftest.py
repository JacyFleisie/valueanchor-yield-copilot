"""Shared test setup.

The application reads its database path from ``VALUEANCHOR_DB``, so the suite
points that at a scratch file before importing the app. A test run therefore
never touches the developer's working dataset.

The language assistant is switched off for the same reason: it is a third-party
service over the network, and a test suite that depends on one is neither fast
nor reproducible. Setting the variables to empty means ``app._load_env_file()``
leaves them alone, because it only fills in values that are not already set. The
suite therefore exercises the offline path, which is also the path a deployment
with no key configured takes.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

SCRATCH_DIR = pathlib.Path(tempfile.mkdtemp(prefix="valueanchor-tests-"))
os.environ["VALUEANCHOR_DB"] = str(SCRATCH_DIR / "valueanchor-test.db")

# Empty, not absent: an empty string is "set", so the developer's own
# backend/.env cannot leak a live key into the test run.
os.environ["OPENROUTER_API_KEY"] = ""
os.environ["OPENROUTER_MODEL"] = ""
os.environ["OPENROUTER_FALLBACK_MODELS"] = ""
