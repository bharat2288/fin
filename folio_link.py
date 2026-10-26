"""A folio reference on a row, an account or a party (fin-redesign 25; spec
part 2, case 11).

folio keeps an LP's units and what they mean; fin keeps the money legs. A
row, an account or a party (a merchant) may carry one read-only link to its
folio record: an LP's page (`#/lps/<id>`) or one row of folio's ledger
(`#/transactions?tx=<id>`), folio's own addresses. The operator pastes the
address, or says "LP 18"; fin keeps only which record it is (folio_link) and
draws the link from folio's address, FOLIO_URL. fin never reads from or
writes to folio, and a link moves no figure.

app.py registers the blueprint with one line and merges SUMMARIES into the
change history's words with another. No module here imports app.py.

  GET    /api/folio-links/<subject>/<id>   {link}: the link, or null
  PUT    /api/folio-links/<subject>/<id>   {link: the address}: set or replace, one change
  DELETE /api/folio-links/<subject>/<id>   take it off, one change

<subject> is row, account or party. A refusal is a 4xx with {"error": words}
and writes nothing.
"""

from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlsplit

from flask import Blueprint, g, jsonify, request

from db import get_db

# folio's own address; FIN_FOLIO_URL names another.
DEFAULT_FOLIO_URL = "https://folio.bhasuri.ai"
FOLIO_URL_VARIABLE = "FIN_FOLIO_URL"

# subject -> (table, the column that names it in words, the noun)
SUBJECTS = {
    "row": ("transactions", "description", "the row"),
    "account": ("accounts", "name", "the account"),
    "party": ("services", "name", "the party"),
}

# target -> (folio's address for the record, the record in words)
TARGETS = {
    "lp": ("#/lps/{id}", "folio LP {id}"),
    "tx": ("#/transactions?tx={id}", "folio transaction {id}"),
}

NOTHING = "Nothing was changed."
PASTE = f"paste the folio address of an LP's page or a row of folio's ledger, or say \"LP 18\". {NOTHING}"
NOT_A_RECORD = (f"that folio address is not an LP's page or a row of folio's ledger "
                f"(#/lps/<number> or #/transactions?tx=<number>). {NOTHING}")

_LP_WORDS = re.compile(r"lp\s*#?\s*([0-9]+)", re.I)
_LP_PATH = re.compile(r"/lps/([0-9]+)/?")


class Refused(ValueError):
    """An address fin cannot hold as a folio record. Nothing was written."""


def folio_url() -> str:
    """folio's address, without a trailing slash."""
    return (os.environ.get(FOLIO_URL_VARIABLE, "").strip() or DEFAULT_FOLIO_URL).rstrip("/")


# The largest id SQLite holds.
_MAX_ID = 2**63 - 1


def _record_id(text: str | None) -> int | None:
    """A folio id: ASCII digits only, above zero and within what SQLite holds."""
    if text is None or not re.fullmatch(r"[0-9]+", text):
        return None
    value = int(text)
    return value if 0 < value <= _MAX_ID else None


def parsed(address: str) -> tuple[str, int]:
    """The folio record an address names: ("lp", id) or ("tx", id). Takes a
    folio address (on folio's own host, either scheme), its part from the
    "#", or "LP 18". Raises Refused, in words, for anything else."""
    text = address.strip() if isinstance(address, str) else ""
    if not text:
        raise Refused(PASTE)
    words = _LP_WORDS.fullmatch(text)
    if words:
        lp = _record_id(words.group(1))
        if lp is None:
            raise Refused(NOT_A_RECORD)
        return "lp", lp
    if not text.startswith("#"):
        parts = urlsplit(text)
        if parts.scheme not in ("http", "https") or parts.hostname != urlsplit(folio_url()).hostname:
            raise Refused(f"not a folio address: a link from {folio_url()} is wanted. {NOTHING}")
        text = "#" + parts.fragment
    route = urlsplit(text[1:])
    lp_path = _LP_PATH.fullmatch(route.path)
    if lp_path:
        lp = _record_id(lp_path.group(1))
        if lp is not None:
            return "lp", lp
    if route.path.rstrip("/") == "/transactions":
        tx = _record_id((parse_qs(route.query).get("tx") or [None])[0])
        if tx is not None:
            return "tx", tx
    raise Refused(NOT_A_RECORD)


def shown(target: str, folio_id: int) -> dict:
    """A link as the screens show it."""
    path, words = TARGETS[target]
    return {"target": target, "folio_id": folio_id, "url": f"{folio_url()}/{path.format(id=folio_id)}",
            "words": words.format(id=folio_id)}


def held(conn, subject: str, subject_id: int) -> dict | None:
    """The link a subject carries, as shown, or None."""
    r = conn.execute("SELECT target, folio_id FROM folio_link WHERE subject = ? AND subject_id = ?",
                     (subject, subject_id)).fetchone()
    return None if r is None else shown(r["target"], r["folio_id"])


def take_off(conn, subject: str, subject_id: int) -> None:
    """A subject's link goes with it: a caller deleting the subject calls
    this in the same change, so an Undo puts both back."""
    conn.execute("DELETE FROM folio_link WHERE subject = ? AND subject_id = ?", (subject, subject_id))


def carry(conn, subject: str, from_id: int, to_id: int) -> None:
    """A merge: the link of the subject merged away moves to the one it
    joins, unless that one carries its own, which stays."""
    if held(conn, subject, to_id) is None:
        conn.execute("UPDATE folio_link SET subject_id = ? WHERE subject = ? AND subject_id = ?",
                     (to_id, subject, from_id))
    else:
        take_off(conn, subject, from_id)


def _name(conn, subject: str, subject_id: int) -> str | None:
    """The subject's name, or None when it is no longer in fin."""
    table, column, _ = SUBJECTS[subject]
    r = conn.execute(f"SELECT {column} FROM {table} WHERE id = ?", (subject_id,)).fetchone()
    return None if r is None else r[0]


def _gone(subject: str):
    return jsonify({"error": f"This {subject} is no longer in fin. {NOTHING}"}), 404


def _name_change(conn, words: str) -> None:
    """Name this request's change in Changes (app.py's _name_change)."""
    entry_id = g.get("change_entry")
    if entry_id is not None:
        conn.execute("UPDATE change_entries SET summary = ? WHERE id = ? AND summary = ''", (words, entry_id))


bp = Blueprint("folio", __name__)

# endpoint (without the blueprint's "folio." prefix) -> what the change did;
# each route names its subject and record in its own summary.
SUMMARIES: dict[str, str] = {
    "folio_link_set": "Linked to folio",
    "folio_link_off": "Took a folio link off",
}

_ROUTE = "/api/folio-links/<any(row, account, party):subject>/<int:subject_id>"


@bp.route(_ROUTE)
def folio_link_get(subject, subject_id):
    with get_db() as conn:
        if _name(conn, subject, subject_id) is None:
            return _gone(subject)
        return jsonify({"link": held(conn, subject, subject_id)})


@bp.route(_ROUTE, methods=["PUT"])
def folio_link_set(subject, subject_id):
    """Set the subject's folio link, or replace the one it carries."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(data.get("link"), str):
        return jsonify({"error": f"send {{\"link\": the folio address}}. {NOTHING}"}), 400
    with get_db() as conn:
        name = _name(conn, subject, subject_id)
        if name is None:
            return _gone(subject)
        try:
            target, folio_id = parsed(data["link"])
        except Refused as e:
            return jsonify({"error": str(e)}), 400
        conn.execute(
            "INSERT INTO folio_link (subject, subject_id, target, folio_id) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (subject, subject_id) DO UPDATE SET target = excluded.target, folio_id = excluded.folio_id"
            " WHERE target IS NOT excluded.target OR folio_id IS NOT excluded.folio_id",
            (subject, subject_id, target, folio_id))
        link = shown(target, folio_id)
        _name_change(conn, f"Linked {SUBJECTS[subject][2]} {name} to {link['words']}")
        conn.commit()
    return jsonify({"link": link})


@bp.route(_ROUTE, methods=["DELETE"])
def folio_link_off(subject, subject_id):
    """Take the subject's folio link off."""
    with get_db() as conn:
        name = _name(conn, subject, subject_id)
        if name is None:
            return _gone(subject)
        gone = conn.execute("DELETE FROM folio_link WHERE subject = ? AND subject_id = ?", (subject, subject_id))
        if gone.rowcount == 0:
            return jsonify({"error": f"This {subject} carries no folio link. {NOTHING}"}), 404
        _name_change(conn, f"Took the folio link off {SUBJECTS[subject][2]} {name}")
        conn.commit()
    return jsonify({"link": None})
