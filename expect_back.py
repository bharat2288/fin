"""Expect back (personal-ledger 23): the rules every lane shares.

An expectation is part or all of one row that is not the household's to keep
or spend: expected back on money out, or to pay back on money in. A set-off
places a payment row against one (or leaves it waiting for its row); a let-go
closes an open rest as mine or a gift. The records are schema.sql's seven
expect-back tables; this module holds what every writer and reader of them
shares:

- the own part of a row, through own_sql(), read from the one home of its
  formula, the view row_parts (since the switch, ticket 45, every reader
  reads it in place of the bare amount);
- the let-go terms readers add to spending and income (let_go_sql);
- the eligibility rule: where an expectation (or a set-off's payment) may sit;
- open_as_at: each expectation's open amount as at a day, and what waits;
- the derived state of an expectation, per its machine (MACHINE);
- guarded writes: each reads the open amount and the payment's remainder
  inside the transaction it writes in, under the caller's write lock, and is
  refused with the machine's words;
- invariant_check and refusal_to_undo, which history.undo runs so an undo
  from Changes never leaves the records broken or pointing at nothing.

Amounts are whole minor units in the currency of the account they are on,
always positive in the records; a row's sign says which way its money went.
Nothing here commits: the caller's route owns the transaction and the change.
"""

from __future__ import annotations

import json
import re
import sqlite3

import account_kind
import book_type
import flow

BACK = "back"   # on money out: expected back
PAY = "pay"     # on money in: to pay back
DIRECTIONS = (BACK, PAY)

HAND = "hand"
CONVERTED = "converted"
EXPECTATION_SOURCES = (HAND, CONVERTED)
SET_OFF_SOURCES = (HAND, "suggested", "refund", CONVERTED)
MINE = "mine"
GIFT = "gift"
BECAME = (MINE, GIFT)

# The aliases own_sql may be asked for: frozen, so no caller's text reaches SQL.
ROW_ALIASES = ("t", "tx", "transactions")

# The expectation machine (spec 23, Lifecycle), as ruled for the build. The
# state is derived from the records, never stored; the guarded writes below
# enforce it.
OPEN, PARTLY_BACK, CLOSED, LET_GO = "open", "partly_back", "closed", "let_go"
STATES = (OPEN, PARTLY_BACK, CLOSED, LET_GO)
MACHINE = {
    "entity": "expectation",
    "field": "derived:state",
    "states": {
        OPEN: "nothing set off, nothing let go",
        PARTLY_BACK: "0 < set off < expected, no let-go",
        CLOSED: "set off = expected",
        LET_GO: "set off < expected, the open rest let go",
    },
    "initial": {"event": "mark", "to": OPEN},
    "events": {
        OPEN: ("set_off", "let_go", "edit_amount", "undo_mark"),
        PARTLY_BACK: ("set_off", "unlink", "let_go", "edit_amount"),
        CLOSED: ("unlink", "edit_amount"),
        LET_GO: ("undo_let_go",),
    },
}
# The waiting set-off's own small machine: waiting -> placed when its row
# arrives (or split into a placed and a smaller waiting part); never back.
SET_OFF_STATES = ("waiting", "placed")

LET_GO_SINCE = "let go since; undo the let-go first"
SIGNS = {"SGD": "S$", "INR": "₹"}


class Refused(ValueError):
    """The write was refused; nothing was written. The text is the operator's."""


# --- amounts in words ---------------------------------------------------------


def _group(units: int, currency: str) -> str:
    text = str(units)
    if currency != "INR" or len(text) <= 3:
        return f"{units:,}"
    head, tail = text[:-3], text[-3:]
    pairs = []
    while len(head) > 2:
        pairs.insert(0, head[-2:])
        head = head[:-2]
    return ",".join([head] + pairs + [tail])


def full(minor: int, currency: str = "SGD") -> str:
    """'S$ 1,234.56'; rupees in Indian grouping, '₹ 1,23,456.78'."""
    units, cents = divmod(abs(minor), 100)
    sign = "-" if minor < 0 else ""
    return f"{SIGNS.get(currency, currency)} {sign}{_group(units, currency)}.{cents:02d}"


def short(minor: int, currency: str = "SGD") -> str:
    """'S$ 120' for a whole amount, 'S$ 120.50' otherwise."""
    text = full(minor, currency)
    return text[:-3] if text.endswith(".00") else text


def _listed(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


# --- the own part ---------------------------------------------------------------


def own_sql(alias: str = "t") -> str:
    """SQL for a row's own part, for a reader to use in place of
    `<alias>.amount_minor`."""
    if alias not in ROW_ALIASES:
        raise ValueError(f"own_sql takes one of {ROW_ALIASES}")
    return f"(SELECT rp.own_minor FROM row_parts rp WHERE rp.row_id = {alias}.id)"


def parts(conn: sqlite3.Connection, row_ids) -> dict[int, dict]:
    """Each row's amount, expected, placed and own part (row_parts), by id."""
    ids = [int(i) for i in row_ids]
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    return {
        r[0]: {"amount_minor": r[1], "expected_minor": r[2], "placed_minor": r[3], "own_minor": r[4]}
        for r in conn.execute(
            "SELECT row_id, amount_minor, expected_minor, placed_minor, own_minor "
            f"FROM row_parts WHERE row_id IN ({marks})", ids)
    }


# A let-go counted as a row of its own on the day it counts: a back let-go is
# spending (mine: the row's book and type; a gift: Gifts & Donations), a pay
# let-go is income (a gift to me). Columns: let_go_id, day, book, type_id,
# flow_type, amount_minor (signed as a row: spending positive, income
# negative), account_id (the account the expectation sits on: its row's, or
# an opening figure's person account) and currency.
_LET_GO_ROWS = (
    "SELECT l.id AS let_go_id, l.counts_on AS day, l.book AS book, l.type_id AS type_id, "
    "CASE WHEN e.direction = 'back' THEN 'expense' ELSE 'income' END AS flow_type, "
    "CASE WHEN e.direction = 'back' THEN l.amount_minor ELSE -l.amount_minor END AS amount_minor, "
    "COALESCE(s.account_id, e.person_id) AS account_id, e.currency AS currency "
    "FROM let_go l JOIN expectation e ON e.id = l.expectation_id "
    "LEFT JOIN transactions lt ON lt.id = e.row_id "
    "LEFT JOIN statements s ON s.id = lt.statement_id"
)


def let_go_sql() -> str:
    """A SELECT of the let-gos as rows a spending or income reader adds to its
    own (columns above)."""
    return _LET_GO_ROWS


# --- eligibility ---------------------------------------------------------------


def _row(conn: sqlite3.Connection, row_id) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT t.id, t.description, t.amount_minor, t.flow_type, t.other_side_id, "
        "COALESCE(t.book, ?) AS book, t.type_id, s.account_id, "
        "COALESCE(a.currency, 'SGD') AS currency, a.owner, o.type AS other_kind "
        "FROM transactions t JOIN statements s ON s.id = t.statement_id "
        "JOIN accounts a ON a.id = s.account_id "
        "LEFT JOIN accounts o ON o.id = t.other_side_id WHERE t.id = ?",
        (book_type.DEFAULT_BOOK, row_id),
    ).fetchone()


def _row_refusal(row, direction: str) -> str | None:
    """Why `direction` cannot sit on this row (or the row cannot pay one of the
    other direction), or None. Only where nothing else on the sheet already
    counts the row: a Household expense or a movement naming a person for
    back; an income, review or refund row or a person movement for pay."""
    if row is None:
        return "no such row"
    money_in = row["amount_minor"] < 0
    if direction == BACK and money_in:
        return "money in can only be expected to pay back"
    if direction == PAY and not money_in:
        return "money out can only be expected back"
    if row["flow_type"] in ("transfer", "payment"):
        return "a move between your own accounts"
    if row["book"] != account_kind.HOUSEHOLD or row["owner"] != account_kind.HOUSEHOLD:
        if direction == PAY:
            return "only Household money can be expected to pay back: this row is the company's"
        return "only Household spending can be expected back: this row is the company's cost"
    if row["flow_type"] == flow.MOVEMENT:
        return None if row["other_kind"] == "person" else "this move already counts on that account's line"
    allowed = ("expense",) if direction == BACK else ("income", flow.REVIEW, "refund")
    if row["flow_type"] not in allowed:
        return f"a row of flow {row['flow_type']} cannot be expected {'back' if direction == BACK else 'to pay back'}"
    return None


def refusal_to_mark(conn: sqlite3.Connection, row_id: int, direction: str) -> str | None:
    """The eligibility rule: why an expectation of `direction` may not sit on
    this row, in the operator's words, or None when it may."""
    if direction not in DIRECTIONS:
        return f"direction must be one of {', '.join(DIRECTIONS)}"
    return _row_refusal(_row(conn, row_id), direction)


def records_refusal(conn: sqlite3.Connection, tx_id: int) -> str | None:
    """What rests on a row, in a refusal's words: its expectation, else what it
    places as a payment. None when the row carries no record. A hand label
    that would make the row something the sheet already counts is refused in
    these words (label_refusal); a rule keeps such a row's flow where that
    refuses the move, and its preview says why in them
    (relabel_preview.flow_writes)."""
    marked = conn.execute(
        "SELECT direction, amount_minor, currency FROM expectation WHERE row_id = ?", (tx_id,)
    ).fetchone()
    if marked is not None:
        what = "expected back" if marked["direction"] == BACK else "to pay back"
        return (f"{short(marked['amount_minor'], marked['currency'])} of this row is {what}; "
                "undo the mark first.")
    placed = conn.execute(
        "SELECT so.amount_minor, COALESCE(a.currency, 'SGD') AS currency, so.expectation_id, "
        "COALESCE(it.description, 'Opening figure · ' || p.name) AS item "
        "FROM set_off so JOIN transactions t ON t.id = so.payment_row_id "
        "JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id "
        "LEFT JOIN expectation e ON e.id = so.expectation_id "
        "LEFT JOIN transactions it ON it.id = e.row_id LEFT JOIN accounts p ON p.id = e.person_id "
        "WHERE so.payment_row_id = ? ORDER BY so.id",
        (tx_id,),
    ).fetchall()
    if not placed:
        return None

    def where(p) -> str:
        return "waiting for its row" if p["expectation_id"] is None else f"set against {p['item']}"

    if len(placed) == 1:
        p = placed[0]
        return f"{short(p['amount_minor'], p['currency'])} of this row is {where(p)}; unlink it first."
    each = [f"{short(p['amount_minor'], p['currency'])} {where(p)}" for p in placed]
    return f"This row has {', '.join(each[:-1])} and {each[-1]}; unlink those first."


def counted_elsewhere(flow_name: str | None, book: str | None, other_kind: str | None) -> bool:
    """Whether a row with this flow, book and other side's kind is one the
    sheet already counts whole, so no record may rest on it: a move between
    the household's own accounts, a company's row, a movement naming a
    non-person."""
    if flow_name in ("transfer", "payment"):
        return True
    if (book or book_type.DEFAULT_BOOK) != account_kind.HOUSEHOLD:
        return True
    return flow_name == flow.MOVEMENT and other_kind != "person"


def label_refusal(conn: sqlite3.Connection, tx_id: int, flow_name: str | None, book: str | None,
                  other_kind: str | None) -> str | None:
    """Why a label may not make this row what it would then be (its flow, book
    and other side's kind after the write), or None: a row a record rests on
    keeps to what the sheet counts once (records_refusal's words). A hand
    label is refused so (app._label_refusal); a rule keeps such a row's flow
    where this refuses the move, and its preview says why in these words
    (relabel_preview.flow_writes)."""
    if not counted_elsewhere(flow_name, book, other_kind):
        return None
    return records_refusal(conn, tx_id)


# --- reads ---------------------------------------------------------------------


def _expectation(conn: sqlite3.Connection, expectation_id) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT e.*, t.description, t.amount_minor AS row_amount, p.name AS person_name "
        "FROM expectation e LEFT JOIN transactions t ON t.id = e.row_id "
        "LEFT JOIN accounts p ON p.id = e.person_id WHERE e.id = ?",
        (expectation_id,),
    ).fetchone()


def _item_name(x) -> str:
    """How an expectation is named in a refusal: its row's text, or the
    opening figure's person."""
    return x["description"] if x["row_id"] is not None else f"Opening figure · {x['person_name']}"


def set_off_minor(conn: sqlite3.Connection, expectation_id: int) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(item_amount_minor), 0) FROM set_off WHERE expectation_id = ?",
        (expectation_id,),
    ).fetchone()[0]


def _let_go_of(conn: sqlite3.Connection, expectation_id: int):
    return conn.execute("SELECT * FROM let_go WHERE expectation_id = ?", (expectation_id,)).fetchone()


def open_minor(conn: sqlite3.Connection, expectation_id: int) -> int:
    """What is still open on an expectation now: expected less what is set
    off and let go, in the expectation's currency."""
    x = _expectation(conn, expectation_id)
    if x is None:
        raise Refused("no such expectation")
    let_go = _let_go_of(conn, expectation_id)
    return x["amount_minor"] - set_off_minor(conn, expectation_id) - (let_go["amount_minor"] if let_go else 0)


def payment_left_minor(conn: sqlite3.Connection, row_id: int) -> int:
    """What a payment row still has to place: the size of its own part."""
    found = parts(conn, [row_id]).get(row_id)
    if found is None:
        raise Refused("no such row")
    return abs(found["own_minor"])


def state(conn: sqlite3.Connection, expectation_id: int) -> str:
    """The expectation's derived state (MACHINE)."""
    x = _expectation(conn, expectation_id)
    if x is None:
        raise Refused("no such expectation")
    if _let_go_of(conn, expectation_id) is not None:
        return LET_GO
    placed = set_off_minor(conn, expectation_id)
    if placed == 0:
        return OPEN
    return CLOSED if placed >= x["amount_minor"] else PARTLY_BACK


def set_off_state(conn: sqlite3.Connection, set_off_id: int) -> str:
    found = conn.execute("SELECT expectation_id FROM set_off WHERE id = ?", (set_off_id,)).fetchone()
    if found is None:
        raise Refused("no such set-off")
    return "waiting" if found[0] is None else "placed"


def open_as_at(conn: sqlite3.Connection, on: str) -> dict:
    """Every expectation in being on day `on` with its open amount then, and
    every set-off waiting for its row then. One read each.

    Days are the balance sheet's (its ROW_DAY: the day a row counts in the
    statement it printed on): an expectation takes its row's day (an opening
    figure its date), a set-off its payment row's, a let-go its own. As at a
    day, open = expected - set-offs dated on or before it - a let-go counted
    on or before it. A set-off dated before its expectation's day counts as
    waiting until that day, then against the expectation, so no figure goes
    negative and a month already checked never changes (case 17e).

    {"expectations": [{id, row_id, person_id, direction, currency, day,
                       amount_minor, set_off_minor, let_go_minor, open_minor}],
     "waiting": [{id, payment_row_id, expectation_id, person_id, currency,
                  day, amount_minor}]}, each oldest first.
    """
    import balance_sheet  # imported here: the sheet reads this module

    day = balance_sheet.ROW_DAY
    days = f"""
        WITH ed AS (
            SELECT e.id, e.row_id, e.person_id, e.direction, e.currency, e.amount_minor,
                   COALESCE({day}, e.opening_date) AS day
            FROM expectation e
            LEFT JOIN transactions t ON t.id = e.row_id
            LEFT JOIN statements s ON s.id = t.statement_id),
        sd AS (
            SELECT so.id, so.payment_row_id, so.expectation_id, so.person_id, so.amount_minor,
                   so.item_amount_minor, COALESCE(a.currency, 'SGD') AS currency, {day} AS day
            FROM set_off so JOIN transactions t ON t.id = so.payment_row_id
            JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id)
    """
    items = conn.execute(
        days + """
        SELECT ed.*, COALESCE(placed.total, 0) AS set_off_minor,
               COALESCE(l.amount_minor, 0) AS let_go_minor
        FROM ed
        LEFT JOIN (SELECT expectation_id, SUM(item_amount_minor) AS total FROM sd
                   WHERE day <= ? AND expectation_id IS NOT NULL
                   GROUP BY expectation_id) placed ON placed.expectation_id = ed.id
        LEFT JOIN let_go l ON l.expectation_id = ed.id AND l.counts_on <= ?
        WHERE ed.day <= ?
        ORDER BY ed.day, ed.id
        """,
        (on, on, on),
    ).fetchall()
    waiting = conn.execute(
        days + """
        SELECT sd.id, sd.payment_row_id, sd.expectation_id, sd.person_id, sd.currency,
               sd.day, sd.amount_minor
        FROM sd LEFT JOIN ed ON ed.id = sd.expectation_id
        WHERE sd.day <= ? AND (sd.expectation_id IS NULL OR ed.day > ?)
        ORDER BY sd.day, sd.id
        """,
        (on, on),
    ).fetchall()
    expectations = []
    for r in items:
        x = dict(r)
        x["open_minor"] = x["amount_minor"] - x["set_off_minor"] - x["let_go_minor"]
        expectations.append(x)
    return {"expectations": expectations, "waiting": [dict(w) for w in waiting]}


# --- who paid -----------------------------------------------------------------


def _word(name: str) -> re.Pattern:
    return re.compile(r"(?<![A-Z0-9])" + re.escape(name.upper()) + r"(?![A-Z0-9])")


def payer_name(conn: sqlite3.Connection, payment_row_id: int) -> str | None:
    """The one person a payment is from, by its text: a learnt sender wording
    it carries, or the name of a person named on what it is set against (or
    waiting for). None when no one, or more than one, matches."""
    text = (conn.execute("SELECT description FROM transactions WHERE id = ?",
                         (payment_row_id,)).fetchone() or [""])[0].upper()
    found = {
        r[0]: r[1] for r in conn.execute(
            "SELECT a.id, a.name FROM accounts a WHERE a.id IN ("
            " SELECT n.person_id FROM set_off so JOIN expectation_name n ON n.expectation_id = so.expectation_id"
            " WHERE so.payment_row_id = ?"
            " UNION SELECT so.person_id FROM set_off so WHERE so.payment_row_id = ? AND so.person_id IS NOT NULL)",
            (payment_row_id, payment_row_id))
        if _word(r[1]).search(text)
    }
    for person_id, wording, name in conn.execute(
        "SELECT w.person_id, w.wording, a.name FROM sender_wording w JOIN accounts a ON a.id = w.person_id"
    ):
        if _word(wording).search(text):
            found[person_id] = name
    return next(iter(found.values())) if len(found) == 1 else None


def _whose(conn: sqlite3.Connection, payment_row_id: int, amount: str, description: str) -> str:
    """'Jay's S$ 120', or 'S$ 120 from <text>' when no one person matches."""
    name = payer_name(conn, payment_row_id)
    return f"{name}'s {amount}" if name else f"{amount} from {description}"


# --- guarded writes -------------------------------------------------------------


def _begin(conn: sqlite3.Connection) -> None:
    """Hold the database's write lock from the first read of a guarded write,
    so its guards are read in the transaction it writes in."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")


def _checked_amount(amount_minor) -> int:
    if isinstance(amount_minor, bool) or not isinstance(amount_minor, int):
        raise Refused("an amount is a whole number of minor units")
    if amount_minor <= 0:
        raise Refused("the amount must be more than nothing")
    return amount_minor


def _checked_people(conn: sqlite3.Connection, person_ids) -> list[int]:
    ids = []
    for person_id in person_ids or ():
        held = conn.execute(
            "SELECT 1 FROM accounts WHERE id = ? AND type = 'person' AND owner = ?",
            (person_id, account_kind.HOUSEHOLD),
        ).fetchone()
        if held is None:
            raise Refused("a name on an expectation is a person's account")
        if person_id not in ids:
            ids.append(person_id)
    return ids


def insert_expectation(conn: sqlite3.Connection, row_id: int, direction: str, amount_minor: int, *,
                       names=(), note: str | None = None, source: str = HAND) -> int:
    """Mark a row: part or all of it expected back (or to pay back). Refused,
    writing nothing, per the machine's forbidden births."""
    _begin(conn)
    if source not in EXPECTATION_SOURCES:
        raise Refused(f"source must be one of {', '.join(EXPECTATION_SOURCES)}")
    amount_minor = _checked_amount(amount_minor)
    row = _row(conn, row_id)
    why = _row_refusal(row, direction) if direction in DIRECTIONS else "direction must be back or pay"
    if why:
        raise Refused(why)
    if amount_minor > abs(row["amount_minor"]):
        raise Refused(f"more than the row's {full(abs(row['amount_minor']), row['currency'])}")
    if conn.execute("SELECT 1 FROM expectation WHERE row_id = ?", (row_id,)).fetchone():
        raise Refused("this row already has an expectation: edit that one")
    people = _checked_people(conn, names)
    expectation_id = conn.execute(
        "INSERT INTO expectation (row_id, direction, amount_minor, currency, note, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (row_id, direction, amount_minor, row["currency"], note, source),
    ).lastrowid
    for person_id in people:
        conn.execute("INSERT INTO expectation_name (expectation_id, person_id) VALUES (?, ?)",
                     (expectation_id, person_id))
    return expectation_id


def insert_opening(conn: sqlite3.Connection, person_id: int, on: str, direction: str,
                   amount_minor: int, *, source: str = CONVERTED) -> int:
    """An opening figure: an expectation with no row, on a person account's
    anchor date, named for that person."""
    _begin(conn)
    amount_minor = _checked_amount(amount_minor)
    if direction not in DIRECTIONS:
        raise Refused("direction must be back or pay")
    [person_id] = _checked_people(conn, [person_id])
    currency = conn.execute("SELECT COALESCE(currency, 'SGD') FROM accounts WHERE id = ?",
                            (person_id,)).fetchone()[0]
    expectation_id = conn.execute(
        "INSERT INTO expectation (person_id, opening_date, direction, amount_minor, currency, source) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (person_id, on, direction, amount_minor, currency, source),
    ).lastrowid
    conn.execute("INSERT INTO expectation_name (expectation_id, person_id) VALUES (?, ?)",
                 (expectation_id, person_id))
    return expectation_id


def _refused_event(conn: sqlite3.Connection, x, event: str) -> None:
    """Refuse an event the expectation's state does not allow, in the
    machine's words."""
    now = state(conn, x["id"])
    if event in MACHINE["events"][now]:
        return
    if now == LET_GO:
        raise Refused(LET_GO_SINCE)
    if event in ("set_off", "let_go"):
        raise Refused(f"nothing open on {_item_name(x)}")
    if event == "undo_mark":
        raise Refused(_mark_held(conn, x["id"]))
    if event == "undo_let_go":
        raise Refused(f"nothing let go on {_item_name(x)}")
    raise Refused(f"{event} is not allowed while {now.replace('_', ' ')}")


def edit_amount(conn: sqlite3.Connection, expectation_id: int, amount_minor: int) -> None:
    """Change how much is expected: never above the row, never below what is
    already back, never while let go."""
    _begin(conn)
    amount_minor = _checked_amount(amount_minor)
    x = _expectation(conn, expectation_id)
    if x is None:
        raise Refused("no such expectation")
    _refused_event(conn, x, "edit_amount")
    if x["row_id"] is not None and amount_minor > abs(x["row_amount"]):
        raise Refused(f"more than the row's {full(abs(x['row_amount']), x['currency'])}")
    back = set_off_minor(conn, expectation_id)
    if amount_minor < back:
        raise Refused(f"{full(back, x['currency'])} has already come back")
    conn.execute("UPDATE expectation SET amount_minor = ? WHERE id = ?", (amount_minor, expectation_id))


def _mark_held(conn: sqlite3.Connection, expectation_id: int) -> str:
    """Why the mark cannot be undone now: what is set against it."""
    held = conn.execute(
        "SELECT so.payment_row_id, so.item_amount_minor, t.description, e.currency "
        "FROM set_off so JOIN transactions t ON t.id = so.payment_row_id "
        "JOIN expectation e ON e.id = so.expectation_id WHERE so.expectation_id = ? ORDER BY so.id",
        (expectation_id,),
    ).fetchall()
    listed = _listed([_whose(conn, h[0], short(h[1], h[3]), h[2]) for h in held])
    if len(held) == 1:
        return f"Can't undo the mark yet: {listed} is set against it. Unlink it first."
    return f"Can't undo the mark yet: {listed} are set against it. Unlink those first."


def delete_expectation(conn: sqlite3.Connection, expectation_id: int) -> None:
    """Undo the mark: only while nothing is set off or let go."""
    _begin(conn)
    x = _expectation(conn, expectation_id)
    if x is None:
        raise Refused("no such expectation")
    _refused_event(conn, x, "undo_mark")
    conn.execute("DELETE FROM expectation_name WHERE expectation_id = ?", (expectation_id,))
    conn.execute("DELETE FROM expectation WHERE id = ?", (expectation_id,))


def insert_set_off(conn: sqlite3.Connection, payment_row_id: int, expectation_id: int | None,
                   amount_minor: int, *, item_amount_minor: int | None = None,
                   rate: str | None = None, rate_date: str | None = None,
                   person_id: int | None = None, note: str | None = None,
                   source: str = HAND) -> int:
    """Place `amount_minor` of a payment row (in its currency) against an
    expectation, or leave it waiting for its row (expectation_id None). The
    open amount and the payment's remainder are read in this transaction.
    Across currencies the caller gives the expectation's amount and the rate
    (placement_rate); in one currency the two amounts are one."""
    _begin(conn)
    if source not in SET_OFF_SOURCES:
        raise Refused(f"source must be one of {', '.join(SET_OFF_SOURCES)}")
    amount_minor = _checked_amount(amount_minor)
    payment = _row(conn, payment_row_id)
    if payment is None:
        raise Refused("no such row")
    if expectation_id is None:
        why = _row_refusal(payment, PAY if payment["amount_minor"] < 0 else BACK)
        if why:
            raise Refused(why)
        if rate is not None or item_amount_minor not in (None, amount_minor):
            raise Refused("a set-off waiting for its row has no rate")
        if person_id is not None:
            _checked_people(conn, [person_id])
        item_amount_minor = amount_minor
    else:
        x = _expectation(conn, expectation_id)
        if x is None:
            raise Refused("no such expectation")
        # The payment goes the other way to the expectation's row.
        wanted = PAY if x["direction"] == BACK else BACK
        if (payment["amount_minor"] < 0) != (wanted == PAY):
            raise Refused(f"a payment for {_item_name(x)} is money {'in' if wanted == PAY else 'out'}")
        why = _row_refusal(payment, wanted)
        if why:
            raise Refused(why)
        _refused_event(conn, x, "set_off")
        if payment["currency"] == x["currency"]:
            if rate is not None or item_amount_minor not in (None, amount_minor):
                raise Refused("one currency: the amount placed is the amount set off")
            item_amount_minor = amount_minor
        else:
            if rate is None or rate_date is None or item_amount_minor is None:
                raise Refused(f"{_item_name(x)} is in {x['currency']}: give the rate and both amounts")
            item_amount_minor = _checked_amount(item_amount_minor)
        left = open_minor(conn, expectation_id)
        if item_amount_minor > left:
            raise Refused(f"{_item_name(x)} has {full(left, x['currency'])} open")
    remainder = payment_left_minor(conn, payment_row_id)
    if amount_minor > remainder:
        raise Refused(f"{short(amount_minor, payment['currency'])} is more than the "
                      f"{short(remainder, payment['currency'])} left on this payment")
    return conn.execute(
        "INSERT INTO set_off (payment_row_id, expectation_id, person_id, note, amount_minor, "
        "item_amount_minor, rate, rate_date, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (payment_row_id, expectation_id, person_id, note, amount_minor, item_amount_minor,
         rate, rate_date, source),
    ).lastrowid


def delete_set_off(conn: sqlite3.Connection, set_off_id: int) -> None:
    """Unlink: the set-off goes; never while its expectation is let go."""
    _begin(conn)
    found = conn.execute("SELECT expectation_id FROM set_off WHERE id = ?", (set_off_id,)).fetchone()
    if found is None:
        raise Refused("no such set-off")
    if found[0] is not None:
        _refused_event(conn, _expectation(conn, found[0]), "unlink")
    conn.execute("DELETE FROM set_off WHERE id = ?", (set_off_id,))


def insert_let_go(conn: sqlite3.Connection, expectation_id: int, became: str, *, book: str,
                  type_id: int, counts_on: str, to_whom_id: int | None = None) -> int:
    """Let the whole open rest go, counted on `counts_on`. A back item becomes
    mine (its row's book and type) or a gift; a pay item only a gift to me."""
    _begin(conn)
    x = _expectation(conn, expectation_id)
    if x is None:
        raise Refused("no such expectation")
    if became not in BECAME:
        raise Refused("let go as mine or as a gift")
    if x["direction"] == PAY and became != GIFT:
        raise Refused("money in was never spent: let it go only as a gift to me")
    _refused_event(conn, x, "let_go")
    if to_whom_id is not None:
        if became != GIFT:
            raise Refused("only a gift is to someone")
        _checked_people(conn, [to_whom_id])
    if book not in book_type.BOOK_NAMES:
        raise Refused(f"unknown book: {book!r}")
    if isinstance(type_id, bool) or not isinstance(type_id, int) or not conn.execute(
        "SELECT 1 FROM types WHERE id = ?", (type_id,)
    ).fetchone():
        raise Refused("a let-go takes a type")
    rest = open_minor(conn, expectation_id)
    return conn.execute(
        "INSERT INTO let_go (expectation_id, amount_minor, became, book, type_id, to_whom_id, counts_on) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (expectation_id, rest, became, book, type_id, to_whom_id, counts_on),
    ).lastrowid


def delete_let_go(conn: sqlite3.Connection, expectation_id: int) -> None:
    """Undo the let-go: the open rest is open again."""
    _begin(conn)
    x = _expectation(conn, expectation_id)
    if x is None:
        raise Refused("no such expectation")
    _refused_event(conn, x, "undo_let_go")
    conn.execute("DELETE FROM let_go WHERE expectation_id = ?", (expectation_id,))


# --- the undo guard -------------------------------------------------------------


def _entry_rows(conn: sqlite3.Connection, entry_id: int) -> list[tuple]:
    return [
        (tbl, row_id, op, json.loads(before) if before else {}, json.loads(after) if after else {})
        for tbl, row_id, op, before, after in conn.execute(
            "SELECT tbl, row_id, op, before, after FROM change_rows WHERE entry_id = ? ORDER BY id",
            (entry_id,))
    ]


def _undoing_what(conn: sqlite3.Connection, entry_id: int) -> str:
    summary = (conn.execute("SELECT summary FROM change_entries WHERE id = ?",
                            (entry_id,)).fetchone() or [""])[0]
    return "this import" if summary == "Imported a statement" else "this change"


def refusal_to_undo(conn: sqlite3.Connection, entry_id: int) -> str | None:
    """Before an undo writes anything: the record it would leave pointing at
    nothing, named, or None. An undo removes every row its entry inserted;
    a record another change made that rests on one of them refuses it."""
    rows = _entry_rows(conn, entry_id)
    inserted = {(tbl, row_id) for tbl, row_id, op, _, _ in rows if op == "insert"}

    def others(tbl: str, sql: str, args: tuple) -> list:
        return [r for r in conn.execute(sql, args).fetchall() if (tbl, r[0]) not in inserted]

    what = _undoing_what(conn, entry_id)
    for tbl, row_id in sorted(inserted, key=lambda k: k[1]):
        if tbl == "expectation":
            if others("let_go", "SELECT id FROM let_go WHERE expectation_id = ?", (row_id,)):
                return LET_GO_SINCE
            if others("set_off", "SELECT id FROM set_off WHERE expectation_id = ?", (row_id,)):
                return _mark_held(conn, row_id)
            if others("expectation_name", "SELECT id FROM expectation_name WHERE expectation_id = ?",
                      (row_id,)):
                return f"Can't undo {what}: names were added to the expectation since. Undo that change first."
        elif tbl == "transactions":
            description = (conn.execute("SELECT description FROM transactions WHERE id = ?",
                                        (row_id,)).fetchone() or ["this row"])[0]
            placed = others("set_off",
                            "SELECT so.id, so.amount_minor, COALESCE(a.currency, 'SGD'), "
                            "COALESCE(it.description, 'its row, when it comes') "
                            "FROM set_off so JOIN transactions t ON t.id = so.payment_row_id "
                            "JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id "
                            "LEFT JOIN expectation e ON e.id = so.expectation_id "
                            "LEFT JOIN transactions it ON it.id = e.row_id "
                            "WHERE so.payment_row_id = ? ORDER BY so.id", (row_id,))
            if placed:
                listed = _listed([f"{short(p[1], p[2])} set against {p[3]}" for p in placed])
                return (f"Can't undo {what}: {description} has {listed}. "
                        f"Unlink {'it' if len(placed) == 1 else 'those'} first.")
            marked = others("expectation",
                            "SELECT e.id, e.direction, e.currency FROM expectation e WHERE e.row_id = ?",
                            (row_id,))
            if marked:
                back = set_off_minor(conn, marked[0][0])
                expected = "expected back" if marked[0][1] == BACK else "to pay back"
                if back:
                    return (f"Can't undo {what}: {description} is {expected} with "
                            f"{short(back, marked[0][2])} set against it. Unlink those and undo the mark first.")
                return f"Can't undo {what}: {description} is {expected}. Undo the mark first."
            if others("refund_link", "SELECT id FROM refund_link WHERE refund_row_id = ? OR purchase_row_id = ?",
                      (row_id, row_id)):
                return f"Can't undo {what}: {description} is linked as a refund. Unlink it first."
            if others("dismissal", "SELECT id FROM dismissal WHERE row_id = ? OR purchase_row_id = ?",
                      (row_id, row_id)):
                return f"Can't undo {what}: {description} was set aside since. Undo that change first."
        elif tbl == "accounts":
            name = (conn.execute("SELECT name FROM accounts WHERE id = ?", (row_id,)).fetchone() or ["?"])[0]
            for other, sql in (
                ("expectation_name", "SELECT id FROM expectation_name WHERE person_id = ?"),
                ("expectation", "SELECT id FROM expectation WHERE person_id = ?"),
                ("set_off", "SELECT id FROM set_off WHERE person_id = ?"),
                ("let_go", "SELECT id FROM let_go WHERE to_whom_id = ?"),
                ("sender_wording", "SELECT id FROM sender_wording WHERE person_id = ?"),
            ):
                if others(other, sql, (row_id,)):
                    return f"Can't undo {what}: {name} is named on an expectation since. Undo that change first."
    return None


def invariant_check(conn: sqlite3.Connection, entry_id: int) -> str | None:
    """After an undo has written its entry back, before it commits: whether the
    expectations and payments the entry touched still hold. Returns the first
    broken rule in words, or None.

    - set-offs from a payment are no more than the payment (less what is
      expected on the row itself): every own part keeps its sign;
    - a let-go equals the open rest it closed;
    - set-offs and a let-go on an expectation are no more than expected;
    - an expectation is no more than its row.
    """
    expectations: set[int] = set()
    payments: set[int] = set()
    for tbl, row_id, _op, before, after in _entry_rows(conn, entry_id):
        if tbl == "expectation":
            expectations.add(row_id)
        elif tbl in ("set_off", "let_go", "expectation_name"):
            for side in (before, after):
                if side.get("expectation_id") is not None:
                    expectations.add(side["expectation_id"])
                if side.get("payment_row_id") is not None:
                    payments.add(side["payment_row_id"])
        elif tbl == "transactions":
            payments.add(row_id)
            found = conn.execute("SELECT id FROM expectation WHERE row_id = ?", (row_id,)).fetchone()
            if found:
                expectations.add(found[0])

    for payment_row_id in sorted(payments):
        row = _row(conn, payment_row_id)
        if row is None:
            continue
        placed = conn.execute("SELECT COALESCE(SUM(amount_minor), 0) FROM set_off WHERE payment_row_id = ?",
                              (payment_row_id,)).fetchone()[0]
        on_row = conn.execute("SELECT COALESCE(SUM(amount_minor), 0) FROM expectation WHERE row_id = ?",
                              (payment_row_id,)).fetchone()[0]
        whole = abs(row["amount_minor"])
        if placed + on_row > whole:
            if on_row:
                return (f"{row['description']} would have {short(placed + on_row, row['currency'])} "
                        f"expected and placed on its {short(whole, row['currency'])}")
            name = payer_name(conn, payment_row_id)
            who = f"{name}'s payment" if name else row["description"]
            return f"{who} would place {short(placed, row['currency'])} of {short(whole, row['currency'])}"

    for expectation_id in sorted(expectations):
        x = _expectation(conn, expectation_id)
        if x is None:
            continue
        back = set_off_minor(conn, expectation_id)
        let_go = _let_go_of(conn, expectation_id)
        if let_go is not None and let_go["amount_minor"] != x["amount_minor"] - back:
            return LET_GO_SINCE
        if back > x["amount_minor"]:
            return f"{full(back, x['currency'])} would be set against {full(x['amount_minor'], x['currency'])} expected"
        if x["row_id"] is not None and x["amount_minor"] > abs(x["row_amount"]):
            return f"more than the row's {full(abs(x['row_amount']), x['currency'])}"
    return None
