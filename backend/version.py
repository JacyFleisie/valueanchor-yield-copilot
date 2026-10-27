"""The version this build reports, in one place.

Every surface that states it reads it from here - the FastAPI app, the MCP
server's ``serverInfo``, the footer of the interface and the ``clientInfo`` the
outbound probe introduces itself with. They used to be separate copies, and they
drifted: a tag was cut as v1.0.0 while the running app called itself 1.1. An
owner, a reviewer, an MCP host and somebody else's server log all read that
number, so it is defined once and the release tag is the only other place it may
appear. The footer prints what ``/api/health`` returns, and three tests hold that
endpoint, the MCP ``serverInfo`` and the probe's ``clientInfo`` to this constant
rather than to a literal.
"""

from __future__ import annotations

VERSION = "1.1.1"

__all__ = ["VERSION"]
