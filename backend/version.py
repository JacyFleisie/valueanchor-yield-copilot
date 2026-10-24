"""The version this build reports, in one place.

Three surfaces used to state it separately - the FastAPI app, the MCP server's
``serverInfo`` and the footer of the interface - and they drifted: a tag was cut
as v1.0.0 while the running app called itself 1.1. An owner, a reviewer and an
MCP client all read that number, so it is defined once here and the release tag
is the only other place it may appear.
"""

from __future__ import annotations

VERSION = "1.1.0"

__all__ = ["VERSION"]
