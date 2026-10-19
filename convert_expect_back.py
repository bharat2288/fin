"""Conversion step: loans to and from people become expectations (expand).

Personal-ledger 23 (spec: The conversion, step 1; ticket 32). Today a
person's money is a person line on Books: the opening figure (the person
account's anchor), plus the movement rows naming the person on the
household's accounts, in the person's currency, dated after the figure (or
after 31 Dec 2025 when there is none). This step rewrites that history as
expect-back records, per person, walking the person's rows in the sheet's
day order (ROW_DAY) and then by id, in the old line's own scope:

- a positive anchor becomes an opening back expectation on its date, a
  negative one an opening pay expectation;
- a money-out row sets off the person's open pay expectations, oldest first;
  if it sets off nothing, the whole row becomes a back expectation (a loan
  to them), and if it sets off part, the rest stays on the row to label;
- a money-in row does the mirror: it sets off open back expectations, or
  becomes a pay expectation (a loan from them);
- rows in another currency and rows before the scope's start, which the old
  line never counted, are left alone and counted.

Every record it writes has source `converted` and names its person. It skips
a row that already carries an expectation or a set-off, or a hand-made
refund link; it never skips a row whose flow was set by hand (the review
route's "a loan to a person" sets that on every row this step converts). No
row's flow, other side or amount changes, and no reader changes: the records
are read only from the switch on (expect_back.SWITCHED).

The step's invariant: for every person and every month end since 1 Jan 2026,
open back - open pay - unplaced money in + unplaced money out (the own parts
left on the person's rows), read through expect_back.open_as_at, equals the
old person line on the sheet, to the minor unit; and the records made are
the records planned. A person with more than one anchor cannot be converted:
the step is refused, rolled back, its backup kept.

From expand on, the review route's "a loan to a person" applies this same
rule to the row it labels, in the label's own change (convert_row).

It runs through the conversion runner, last in the chain. It prints counts
only, never a name or an amount.

    python convert_expect_back.py <path to database>

The down step takes every converted record back out, behind its own backup;
it is its own command, not in the chain, and is refused while a hand change
(a set-off, a let-go, an amount edit) rests on a converted expectation:

    python convert_expect_back.py --down <path to database>
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import account_kind
import balance_sheet
import conversion
import db
import expect_back
import flow

NAME = "expect-back"
DOWN_NAME = "expect-back-down"

PERSON = "person"
# The old line counts rows dated after this day when a person has no anchor.
SCOPE_AFTER = (balance_sheet.START - timedelta(days=1)).isoformat()
SCOPE_START = balance_sheet.START.isoformat()
TOTAL = balance_sheet.TOTAL_CURRENCY

_ROW_PARTS = re.compile(r"CREATE VIEW IF NOT EXISTS row_parts AS[^;]*;")


# --- reading the book -------------------------------------------------------------


@dataclass
class _Row:
    id: int
    person_id: int
    day: str
    amount: int
    currency: str
    book: str
    carries: bool


@dataclass
class _Item:
    """An expectation the walk can set off: one held, or one it plans."""
    ref: object  # an expectation id, or ("new", n)
    day: str
    direction: str
    open: int
    order: tuple


@dataclass
class Plan:
    """What the step would write, in order, and what it counts."""
    actions: list = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    refusals: Counter = field(default_factory=Counter)
    persons: set = field(default_factory=set)
    rows: set = field(default_factory=set)


def _persons(conn: sqlite3.Connection, person_id: int | None = None) -> list:
    sql = ("SELECT id, name, type, COALESCE(currency, ?) AS currency, status FROM accounts "
           "WHERE type = ? AND owner = ?")
    args = [TOTAL, PERSON, account_kind.HOUSEHOLD]
    if person_id is not None:
        sql += " AND id = ?"
        args.append(person_id)
    return conn.execute(sql + " ORDER BY id", args).fetchall()


def _person_rows(conn: sqlite3.Connection) -> dict[int, list[_Row]]:
    """Every movement row naming a household person, on a household account,
    by person, in the walk's order: the sheet's day, then id."""
    found: dict[int, list[_Row]] = {}
    for r in conn.execute(
        f"SELECT t.id, t.other_side_id, {balance_sheet.ROW_DAY} AS day, t.amount_minor, "
        "COALESCE(a.currency, ?) AS currency, COALESCE(t.book, ?) AS book, "
        "(EXISTS (SELECT 1 FROM expectation e WHERE e.row_id = t.id) "
        " OR EXISTS (SELECT 1 FROM set_off so WHERE so.payment_row_id = t.id) "
        " OR EXISTS (SELECT 1 FROM refund_link rl WHERE rl.source = 'hand' "
        "            AND (rl.refund_row_id = t.id OR rl.purchase_row_id = t.id))) AS carries "
        "FROM transactions t JOIN statements s ON s.id = t.statement_id "
        "JOIN accounts a ON a.id = s.account_id JOIN accounts p ON p.id = t.other_side_id "
        "WHERE t.flow_type = ? AND p.type = ? AND p.owner = ? AND a.owner = ? "
        "ORDER BY day, t.id",
        (TOTAL, account_kind.HOUSEHOLD, flow.MOVEMENT, PERSON, account_kind.HOUSEHOLD,
         account_kind.HOUSEHOLD),
    ):
        found.setdefault(r[1], []).append(_Row(r[0], r[1], r[2], r[3], r[4], r[5], bool(r[6])))
    return found


def _anchors(conn: sqlite3.Connection) -> dict[int, list]:
    found: dict[int, list] = {}
    for account_id, on, amount in conn.execute(
        "SELECT n.account_id, n.date, n.amount FROM anchors n JOIN accounts p ON p.id = n.account_id "
        "WHERE p.type = ? AND p.owner = ? ORDER BY n.date, n.id",
        (PERSON, account_kind.HOUSEHOLD),
    ):
        found.setdefault(account_id, []).append((on, amount))
    return found


def _expectations(conn: sqlite3.Connection) -> list:
    """Every expectation with what is set off on it and whether it is let go."""
    return conn.execute(
        "SELECT e.id, e.row_id, e.person_id, e.direction, e.amount_minor, e.opening_date, "
        "COALESCE((SELECT SUM(so.item_amount_minor) FROM set_off so WHERE so.expectation_id = e.id), 0) "
        "AS placed, EXISTS (SELECT 1 FROM let_go l WHERE l.expectation_id = e.id) AS let_go "
        "FROM expectation e ORDER BY e.id"
    ).fetchall()


# --- the per-person rule -----------------------------------------------------------


def _walk(plan: Plan, person_id: int, items: list[_Item], pending: list[_Row]) -> None:
    """The rule, row by row in order: set off what is open the other way,
    oldest first, among items dated on or before the row; with nothing set
    off the whole row becomes an expectation; a part set off leaves the rest
    on the row to label."""
    for r in pending:
        money_out = r.amount > 0
        wanted = expect_back.PAY if money_out else expect_back.BACK
        whole, placed = abs(r.amount), 0
        for item in sorted(items, key=lambda i: (i.day, i.order)):
            if placed == whole:
                break
            if item.direction != wanted or item.open <= 0 or item.day > r.day:
                continue
            take = min(item.open, whole - placed)
            plan.actions.append(("set_off", r.id, item.ref, take))
            plan.counts["set_offs"] += 1
            item.open -= take
            placed += take
        if placed == 0:
            direction = expect_back.BACK if money_out else expect_back.PAY
            ref = ("new", len(plan.actions))
            plan.actions.append(("expect", r.id, person_id, direction, whole, ref))
            plan.counts["expectations"] += 1
            items.append(_Item(ref, r.day, direction, whole, (1, len(plan.actions))))
        elif placed < whole:
            plan.counts["left_to_label"] += 1
        plan.persons.add(person_id)
        plan.rows.add(r.id)


def _plan_person(plan: Plan, person, rows: list[_Row], anchors: list, expectations: list,
                 only: set | None) -> None:
    person_id = person["id"]
    if len(anchors) > 1:
        plan.refusals["anchors"] += 1
        return
    anchor = anchors[0] if anchors else None
    after = anchor[0] if anchor else SCOPE_AFTER

    in_scope: dict[int, _Row] = {}
    pending = []
    for r in rows:
        if only is not None and r.id not in only:
            # Out of the label's reach, but still the person's line.
            if r.currency == person["currency"] and r.day > after:
                in_scope[r.id] = r
            continue
        if r.currency != person["currency"]:
            plan.counts["other_currency"] += 1
            continue
        if r.day <= after:
            plan.counts["before_start" if r.day < SCOPE_START else "before_opening"] += 1
            continue
        in_scope[r.id] = r
        if r.carries:
            plan.counts["carrying"] += 1
            continue
        if r.amount == 0:
            continue
        if r.book != account_kind.HOUSEHOLD:
            plan.refusals["company_book"] += 1
            continue
        pending.append(r)

    # What is held already on the person's line: an opening figure, or an
    # expectation on one of its rows; never one let go.
    items, has_opening = [], False
    for e in expectations:
        if e["row_id"] is None and e["person_id"] == person_id:
            has_opening, day = True, e["opening_date"]
        elif e["row_id"] in in_scope:
            day = in_scope[e["row_id"]].day
        else:
            continue
        rest = e["amount_minor"] - e["placed"]
        if not e["let_go"] and rest > 0:
            items.append(_Item(e["id"], day, e["direction"], rest, (0, e["id"])))

    if anchor and anchor[1] != 0 and not has_opening:
        direction = expect_back.BACK if anchor[1] > 0 else expect_back.PAY
        ref = ("new", len(plan.actions))
        plan.actions.append(("opening", person_id, anchor[0], direction, abs(anchor[1]), ref))
        plan.counts["expectations"] += 1
        plan.counts["openings"] += 1
        plan.persons.add(person_id)
        items.append(_Item(ref, anchor[0], direction, abs(anchor[1]), (1, len(plan.actions))))

    _walk(plan, person_id, items, pending)


def planned(conn: sqlite3.Connection, *, person_id: int | None = None, only: set | None = None) -> Plan:
    """What the step would write, every person or one, every row or `only`
    those. Empty when every person's line is already converted."""
    conn.row_factory = sqlite3.Row
    plan = Plan()
    rows, anchors, held = _person_rows(conn), _anchors(conn), _expectations(conn)
    for person in _persons(conn, person_id):
        _plan_person(plan, person, rows.get(person["id"], []), anchors.get(person["id"], []), held, only)
    return plan


def _write(conn: sqlite3.Connection, plan: Plan) -> int:
    """Write a plan through expect_back's guarded writes. Returns how many
    records it wrote."""
    made: dict = {}
    for action in plan.actions:
        if action[0] == "opening":
            _, person_id, on, direction, amount, ref = action
            made[ref] = expect_back.insert_opening(conn, person_id, on, direction, amount,
                                                   source=expect_back.CONVERTED)
        elif action[0] == "expect":
            _, row_id, person_id, direction, amount, ref = action
            made[ref] = expect_back.insert_expectation(conn, row_id, direction, amount, names=[person_id],
                                                       source=expect_back.CONVERTED)
        else:
            _, row_id, ref, amount = action
            expect_back.insert_set_off(conn, row_id, made.get(ref, ref), amount, source=expect_back.CONVERTED)
    return len(plan.actions)


def convert_row(conn: sqlite3.Connection, row_id: int) -> int:
    """The step's rule applied to one row just labelled "a loan to a person",
    in the caller's open change (the review route's). Writes nothing for a row
    outside the old line's scope, one already carrying a record, or a person
    the step itself would refuse. Returns how many records it wrote. Does not
    commit."""
    held = conn.execute(
        "SELECT t.other_side_id FROM transactions t JOIN accounts p ON p.id = t.other_side_id "
        "WHERE t.id = ? AND t.flow_type = ? AND p.type = ? AND p.owner = ?",
        (row_id, flow.MOVEMENT, PERSON, account_kind.HOUSEHOLD),
    ).fetchone()
    if held is None:
        return 0
    plan = planned(conn, person_id=held[0], only={row_id})
    if plan.refusals:
        return 0
    return _write(conn, plan)


# --- the invariant ------------------------------------------------------------------


def _month_ends(conn: sqlite3.Connection) -> list[str]:
    """Every month end from January 2026 to the month of the book's latest
    row, anchor or let-go (after it nothing moves a person's line)."""
    latest = conn.execute(
        f"SELECT MAX(d) FROM (SELECT MAX({balance_sheet.ROW_DAY}) AS d FROM transactions t "
        "JOIN statements s ON s.id = t.statement_id UNION ALL SELECT MAX(date) FROM anchors "
        "UNION ALL SELECT MAX(counts_on) FROM let_go)"
    ).fetchone()[0]
    last = max(date.fromisoformat(latest) if latest else balance_sheet.START, balance_sheet.START)
    ends, year, month = [], balance_sheet.START.year, balance_sheet.START.month
    while (year, month) <= (last.year, last.month):
        ends.append(balance_sheet.month_end(f"{year:04d}-{month:02d}").isoformat())
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return ends


def _has_row_parts(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = 'row_parts'").fetchone() is not None


def person_lines(conn: sqlite3.Connection) -> dict[tuple[int, str], tuple[int, int | None]]:
    """For every household person and month end: (the old person line on the
    sheet, the same figure from the records), by (person id, month end).

    The records' figure is open back - open pay as at the day (open_as_at),
    over the person's opening figure and the expectations on the rows the old
    line counts that day, plus the own parts left on those rows (unplaced
    money in counts against, unplaced money out for), plus what those rows
    hold waiting for a row (owed when money in). None before the records can
    be read (no row_parts yet)."""
    conn.row_factory = sqlite3.Row
    persons = _persons(conn)
    readable = _has_row_parts(conn)
    lines = {}
    for end in _month_ends(conn):
        as_at = date.fromisoformat(end)
        new: dict[int, int] = {p["id"]: 0 for p in persons}
        if readable:
            counted: dict[int, tuple[int, int]] = {}
            for person_id, row_id, amount, own in conn.execute(
                "SELECT t.other_side_id, t.id, t.amount_minor, rp.own_minor FROM transactions t "
                "JOIN statements s ON s.id = t.statement_id JOIN accounts a ON a.id = s.account_id "
                "JOIN accounts p ON p.id = t.other_side_id JOIN row_parts rp ON rp.row_id = t.id "
                "WHERE t.flow_type = ? AND p.type = ? AND p.owner = ? AND a.owner = ? "
                "AND COALESCE(a.currency, ?) = COALESCE(p.currency, ?) "
                f"AND {balance_sheet.ROW_DAY} <= ? AND {balance_sheet.ROW_DAY} > COALESCE("
                "(SELECT MAX(n.date) FROM anchors n WHERE n.account_id = p.id AND n.date <= ?), ?)",
                (flow.MOVEMENT, PERSON, account_kind.HOUSEHOLD, account_kind.HOUSEHOLD, TOTAL, TOTAL,
                 end, end, SCOPE_AFTER),
            ):
                counted[row_id] = (person_id, amount)
                new[person_id] = new.get(person_id, 0) + own
            held = expect_back.open_as_at(conn, end)
            for x in held["expectations"]:
                if x["row_id"] is None:
                    whose = x["person_id"]
                elif x["row_id"] in counted:
                    whose = counted[x["row_id"]][0]
                else:
                    continue
                if whose in new:
                    sign = 1 if x["direction"] == expect_back.BACK else -1
                    new[whose] += sign * x["open_minor"]
            for w in held["waiting"]:
                if w["payment_row_id"] in counted:
                    whose, amount = counted[w["payment_row_id"]]
                    new[whose] += w["amount_minor"] if amount > 0 else -w["amount_minor"]
        for p in persons:
            old = balance_sheet._counterparty_line(conn, p, as_at, end[:7], lambda name: name)["balance_minor"]
            lines[(p["id"], end)] = (old, new[p["id"]] if readable else None)
    return lines


def _converted(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "expectations": conn.execute("SELECT COUNT(*) FROM expectation WHERE source = ?",
                                     (expect_back.CONVERTED,)).fetchone()[0],
        "set_offs": conn.execute("SELECT COUNT(*) FROM set_off WHERE source = ?",
                                 (expect_back.CONVERTED,)).fetchone()[0],
    }


def _counted(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _refusal_words(refusals) -> list[str]:
    words = []
    if refusals.get("anchors"):
        n = refusals["anchors"]
        words.append(f"{_counted(n, 'person has', 'people have')} more than one anchor")
    if refusals.get("company_book"):
        n = refusals["company_book"]
        words.append(f"{_counted(n, 'row naming a person is', 'rows naming a person are')} in a company's book")
    return words


def _measure(conn: sqlite3.Connection) -> dict:
    plan = planned(conn)
    return {
        **conversion.measure(conn),
        "lines": person_lines(conn),
        "records": _converted(conn),
        "planned": {"expectations": plan.counts["expectations"], "set_offs": plan.counts["set_offs"]},
        "refusals": dict(plan.refusals),
    }


def _lines_problems(before: dict, after: dict, *, against_records: bool) -> list[str]:
    changed = set()
    for key, (old, new) in after["lines"].items():
        was = before["lines"].get(key, (None, None))[0]
        if old != was or (against_records and old != new):
            changed.add(key)
    if not changed:
        return []
    people, ends = len({p for p, _ in changed}), len({e for _, e in changed})
    whose = "1 person's line would" if people == 1 else f"{people} people's lines would"
    return [f"{whose} change at {_counted(ends, 'month end', 'month ends')}"]


def _invariant(before: dict, after: dict) -> list[str]:
    problems = conversion.unchanged(before, after)
    problems += _refusal_words(before["refusals"])
    if problems:
        return problems
    problems += _lines_problems(before, after, against_records=True)
    for kind, wanted in before["planned"].items():
        made = after["records"][kind] - before["records"][kind]
        if made != wanted:
            problems.append(f"{made} {kind.replace('_', '-')} made, {wanted} planned")
    return problems


def _ensure_row_parts(conn: sqlite3.Connection) -> None:
    """The own part's one home, as schema.sql declares it: fin makes it on
    start, so a book converted from before expect back may not have it yet;
    the guarded writes read it."""
    if not _has_row_parts(conn):
        conn.execute(_ROW_PARTS.search(db.SCHEMA_PATH.read_text()).group(0))


def _apply(conn: sqlite3.Connection) -> None:
    _ensure_row_parts(conn)
    plan = planned(conn)
    if plan.refusals:
        return  # the invariant names them; the runner rolls back
    _write(conn, plan)


def _is_applied(conn: sqlite3.Connection) -> bool:
    try:
        plan = planned(conn)
    except sqlite3.Error:
        return False  # a shape without the records: change-history has not run
    return not plan.actions and not plan.refusals


STEP = conversion.Step(name=NAME, apply=_apply, is_applied=_is_applied, invariant=_invariant, measure=_measure)


# --- the down step ------------------------------------------------------------------


def hand_changed(conn: sqlite3.Connection) -> int:
    """How many converted expectations carry a hand change: a set-off not
    made by the conversion, a let-go, or an amount other than the one the
    conversion gave it (its first edit's before)."""
    return conn.execute(
        "SELECT COUNT(*) FROM expectation e WHERE e.source = ? AND ("
        " EXISTS (SELECT 1 FROM set_off so WHERE so.expectation_id = e.id AND so.source != ?)"
        " OR EXISTS (SELECT 1 FROM let_go l WHERE l.expectation_id = e.id)"
        " OR COALESCE((SELECT json_extract(c.before, '$.amount_minor') FROM change_rows c"
        "     WHERE c.tbl = 'expectation' AND c.row_id = e.id AND c.op = 'update' ORDER BY c.id LIMIT 1),"
        "   e.amount_minor) != e.amount_minor)",
        (expect_back.CONVERTED, expect_back.CONVERTED),
    ).fetchone()[0]


def _down_refusal(conn: sqlite3.Connection) -> str | None:
    n = hand_changed(conn)
    if not n:
        return None
    return f"{_counted(n, 'converted item has', 'converted items have')} hand changes: unlink or undo them first"


def _down_apply(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM set_off WHERE source = ?", (expect_back.CONVERTED,))
    conn.execute("DELETE FROM expectation_name WHERE expectation_id IN "
                 "(SELECT id FROM expectation WHERE source = ?)", (expect_back.CONVERTED,))
    conn.execute("DELETE FROM expectation WHERE source = ?", (expect_back.CONVERTED,))


def _down_is_applied(conn: sqlite3.Connection) -> bool:
    return not any(_converted(conn).values())


def _down_measure(conn: sqlite3.Connection) -> dict:
    return {**conversion.measure(conn), "lines": person_lines(conn), "records": _converted(conn)}


def _down_invariant(before: dict, after: dict) -> list[str]:
    problems = conversion.unchanged(before, after)
    problems += _lines_problems(before, after, against_records=False)
    left = sum(after["records"].values())
    if left:
        problems.append(f"{left} converted records are left")
    return problems


DOWN_STEP = conversion.Step(name=DOWN_NAME, apply=_down_apply, is_applied=_down_is_applied,
                            invariant=_down_invariant, measure=_down_measure, refusal=_down_refusal)


# --- the command --------------------------------------------------------------------


def _report_plan(plan: Plan) -> None:
    c = plan.counts
    print(f"persons: {len(plan.persons)}")
    print(f"rows converted: {len(plan.rows)}")
    print(f"expectations: {c['expectations']} (opening figures: {c['openings']})")
    print(f"set-offs: {c['set_offs']}")
    print(f"{_counted(c['left_to_label'], 'row', 'rows')} with money left to label")
    print(f"rows in another currency: {c['other_currency']}")
    print(f"rows before 1 Jan 2026: {c['before_start']}")
    if c["before_opening"]:
        print(f"rows on or before a person's opening figure: {c['before_opening']}")
    if c["carrying"]:
        print(f"rows already carrying a record, left as they are: {c['carrying']}")


def main(argv: list[str]) -> int:
    down = argv[:1] == ["--down"]
    args = argv[1:] if down else argv
    if len(args) != 1:
        print("usage: python convert_expect_back.py [--down] <path to database>")
        return 2
    path = Path(args[0]).resolve()
    if not path.is_file():
        print(f"no database at {path}")
        return 1

    plan = None
    if not down:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
        try:
            plan = planned(conn)
        except sqlite3.Error:
            plan = None  # not this step's shape yet: the runner says which step runs first
        finally:
            conn.close()

    try:
        report = conversion.run_step(path, DOWN_STEP if down else STEP)
    except (conversion.ConversionRefused, conversion.ConversionFailed) as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 1

    print(f"step {report['step']}: {report['status']}")
    if report["backup"]:
        # Counts only: a name or an amount is never printed.
        print(f"backup: {report['backup']}")
        print(f"rows before: {report['before']['rows']}")
        print(f"rows after:  {report['after']['rows']}")
        if down:
            removed = report["before"]["records"]
            print(f"converted records removed: expectations {removed['expectations']}, "
                  f"set-offs {removed['set_offs']}")
        elif plan is not None:
            _report_plan(plan)
            print(f"each person's line ties at every month end: {len(_month_ends_of(report))}")
    return 0


def _month_ends_of(report: dict) -> set:
    return {end for _, end in report["after"]["lines"]}


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
