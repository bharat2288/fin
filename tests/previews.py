"""A doctored import preview, stored as the upload stores one.

The confirm writes the facts the upload stored on its batch_imports row, never
the facts a request sends back. The confirm's own checks (the tie, whole
cents, a real day, a known currency) still guard those stored facts; a test of
one doctors the preview and stores it with `store_preview`, as if the upload
had read it so."""

from __future__ import annotations

import app as fin_app
import db


def store_preview(preview: dict) -> dict:
    """Replace the open preview's stored facts with `preview`'s groups, as
    api_import_upload stores them. Returns the preview."""
    conn = db.get_connection()
    try:
        done = conn.execute(
            "UPDATE batch_imports SET result_json = ? WHERE id = ? AND status = 'preview'",
            (fin_app._stored_preview(preview["groups"]), preview["import_id"]),
        )
        assert done.rowcount == 1, "no open preview to doctor"
        conn.commit()
    finally:
        conn.close()
    return preview
