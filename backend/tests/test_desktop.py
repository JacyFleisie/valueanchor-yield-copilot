"""The pieces of the packaged app that are not the app: port and data placement.

A double-click has no terminal to explain itself in, so the two decisions it
makes on its own - which port is free, and where it may write - are tested
rather than trusted. The rest of the desktop entry point starts uvicorn and is
exercised by actually running the built executable.
"""

from __future__ import annotations

import socket
from pathlib import Path

import pytest

import desktop


def _free() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_the_requested_port_is_used_when_it_is_free():
    port = _free()
    assert desktop._free_port(port) == port


def test_a_busy_port_is_skipped_rather_than_failing_the_launch():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as busy:
        busy.bind(("127.0.0.1", 0))
        busy.listen(1)
        taken = busy.getsockname()[1]
        chosen = desktop._free_port(taken)
    assert chosen != taken
    # And the chosen one really is bindable.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", chosen))


def test_a_port_outside_the_fallback_range_is_still_honoured():
    # Regression: a custom --port above the fallback window used to return
    # nothing at all, so the app refused to start on the port it was asked for.
    port = desktop.LAST_PORT + 1234
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", port))
    except OSError:
        pytest.skip(f"port {port} is in use on this machine")
    assert desktop._free_port(port) == port


def test_the_data_folder_is_writable_and_is_a_real_directory():
    data = desktop._data_dir()
    assert data.is_dir()
    assert desktop._writable(data)
    assert isinstance(data, Path)


def test_a_read_only_location_is_reported_unwritable():
    # A path under a *file* can never be a directory, which is the same refusal
    # the writability check has to make for Program Files without admin rights.
    blocker = Path(__file__).resolve()
    assert desktop._writable(blocker / "valueanchor-data") is False
