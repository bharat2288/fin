"""D3, the expectation's page (ticket 35).

One lane's routes on the expect-back blueprint (expect_routes.py), which
registers this module with the others. Empty until its lane fills it: it adds
its routes in register() and names each writing route's change in SUMMARIES
(endpoint name within the blueprint -> the words Changes shows). No module
here imports app.py.
"""

from __future__ import annotations

# endpoint (without the blueprint's "expect." prefix) -> what the change did
SUMMARIES: dict[str, str] = {}


def register(bp) -> None:
    """Add this lane's routes to the expect-back blueprint. None yet."""
