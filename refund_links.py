"""Refund links (spec 23, E7): a refund row tied to the purchase it refunds.

A linker in pairing.py's shape: for each refund row with no link, the
purchases it could belong to; exactly one fit links by itself, a tie links
nothing and is asked in the Queue, and a hand link is never moved. It runs
inside the import confirm's change and on demand.

Until its lane builds it (ticket 38) the linker links nothing: the import
confirm already calls it, once, inside its change, so filling it in changes
no route.
"""

from __future__ import annotations

import sqlite3


def link_on_import(conn: sqlite3.Connection) -> list[dict]:
    """Link the refunds that have exactly one purchase that fits, inside the
    caller's open change; returns the links made. Links nothing yet."""
    return []
