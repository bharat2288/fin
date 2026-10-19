"""Which past rows a proposed change would relabel, before it is made
(fin-surfaces 21; spec 24, Implementation Decisions, the preview).

One function, two ways in: a route asked for a change that would relabel
past rows answers with the preview and writes nothing (409, a body the screen
and chat both read), and asked again with the operator's answer (`past`)
writes the plan the preview counted, so the count is the rows Apply changes.

A **plan** is what the preview saw and what each answer writes:

- `rows`: each row a later change reaches whose label would change, with what
  Apply writes (the new values; each source as it was: from the merchant
  stays from the merchant, checked stays checked) and what Only new rows
  writes instead (the kept field checked, its value left);
- `fills`: rows with no type yet, given the merchant's label: they carry no
  label to change, so the preview does not count them;
- `chosen`: the rows left alone because a field the change sets is chosen;
- `token`: a stamp of the merchant's and its rules' state, the rows' labels
  and the change itself. An answer carrying another token is refused: the
  merchant, its rules or its rows changed since the preview (case 20).

`summary` is the preview as the drawer and chat read it: the count, the
checked count, the chosen count, for a book change the money moved per book
pair in S$ at fin's saved month-end rates (fin-surfaces 15's rule: the
latest saved rate on or before the month end, not before its first day; a
rupee row converts) and the months it touches, the newest three examples,
and the rows another merchant holds (none for a merchant's own change; rules
and merges, fin-surfaces 50, fill it).

This module reads; the routes write the plan through app._write_label, so
every write keeps cat_source in step and lands in the route's one change
entry.
"""

from __future__ import annotations

import calendar
import hashlib
import sqlite3
from dataclasses import dataclass, field

import balance_sheet
import book_type
import db
import expect_back
import label_source

# The answers to "this changes N past rows".
APPLY, ONLY_NEW = "apply", "only_new"
ANSWERS = (APPLY, ONLY_NEW)

# How many examples the preview carries.
EXAMPLES = 3

# A rule's override of each field.
_OVERRIDE_OF = {"book": "book_override", "type_id": "type_override_id"}

# What the plan reads of a row: the columns app._write_label reads of `held`,
# then what the summary needs (the account's currency and the row's own part).
_HELD = ("t.id, t.date, t.description, t.amount_minor, t.book, t.type_id, t.service_id, "
         "t.book_source, t.type_source, t.cat_source, t.flow_type, t.flow_type_manual, t.other_side_id, "
         "COALESCE(a.currency, 'SGD') AS currency")
_FROM = ("transactions t LEFT JOIN statements st ON st.id = t.statement_id "
         "LEFT JOIN accounts a ON a.id = st.account_id")


class Refused(ValueError):
    """An answer the route cannot take; nothing was written."""


@dataclass
class Planned:
    """One row the change relabels: `apply` and `only_new` are the label
    columns each answer writes (app._LABEL_COLUMNS)."""
    held: sqlite3.Row
    apply: dict
    only_new: dict
    checked: bool = False              # a field the change reaches is checked


@dataclass
class Plan:
    subject: dict
    change: dict                       # {field: (from, to)} for the merchant itself
    rows: list[Planned] = field(default_factory=list)
    fills: list[tuple[sqlite3.Row, dict]] = field(default_factory=list)   # (row, the label it is given)
    chosen: int = 0
    token: str = ""

    @property
    def count(self) -> int:
        return len(self.rows)


def checked_answer(value, key: str = "past") -> str | None:
    """An answer as a route takes it: apply, only_new, or none."""
    if value is None:
        return None
    if value not in ANSWERS:
        raise Refused(f"{key} must be {APPLY} (past rows change too) or {ONLY_NEW} (only new rows)")
    return value


def _source(held, field_name: str) -> str | None:
    """A field's source as label_source reads it; none for an empty field."""
    column = label_source.FIELDS[field_name]
    if held[field_name] is None and held[column] is None:
        return None
    return label_source.source_of(held[column], held["cat_source"])


def _own_rule(held, service_id: int, conn):
    """The rule that labels the row's wording now, when it is the merchant's."""
    rule = db.first_rule(held["description"] or "", conn, held["amount_minor"])
    if rule is None or rule["service_id"] != service_id:
        return None
    return rule


def _matches(rule, description: str | None) -> bool:
    """Whether a rule's wording matches a row's, as db.first_rule matches it
    (its amount limits aside: this only says whether to ask first_rule)."""
    text, pattern = (description or "").upper(), rule["pattern"].upper()
    return {"exact": text == pattern, "startswith": text.startswith(pattern),
            "contains": pattern in text}.get(rule["match_type"], False)


def _writes(reached: dict) -> tuple[dict, dict]:
    """What Apply and Only new rows write for the fields a change reaches
    ({field: (new value, held source)}): Apply the new value, its source as
    it was (checked stays checked, from fin is from the merchant); Only new
    rows the source checked, the value left."""
    apply, only_new = {}, {}
    for field_name, (value, source) in reached.items():
        column = label_source.FIELDS[field_name]
        apply[field_name] = value
        apply[column] = label_source.reached(source, value, label_source.MERCHANT)
        accepted = label_source.accepted(source)
        if accepted is not None:
            only_new[column] = accepted
    return apply, only_new


def _stamp(*parts) -> str:
    """The token: a hash of what the preview saw (its repr: the rows are
    many, and every value in them is a plain number or text)."""
    return hashlib.sha256(repr(parts).encode()).hexdigest()[:32]


def merchant_change(conn: sqlite3.Connection, service_id: int, label: dict,
                    leave_out: set[int] | frozenset = frozenset()) -> Plan:
    """The plan for a merchant's new book or type ({field: value}, the
    fields that change): each of its rows (but `leave_out`) whose field a
    later change may reach, not set by a rule and not overridden by the row's
    own rule, and whose value differs. A row whose field the change sets is
    chosen is left alone and counted apart. A row with no type yet carries no
    label to change: it waits for one, so it is given the merchant's
    (`fills`), not counted, and the merchant sheet writes it whatever the
    answer, as before the preview. (A typed row with no book reads as the
    default book, and moving it is counted.)"""
    merchant = conn.execute(
        "SELECT id, name, book, type_id, review_each_time FROM services WHERE id = ?", (service_id,)
    ).fetchone()
    if merchant is None:
        raise LookupError("This merchant is no longer in fin")
    plan = Plan(
        subject={"kind": "merchant", "id": merchant["id"], "name": merchant["name"]},
        change={f: (merchant[f], v) for f, v in label.items()},
    )
    rules = conn.execute(
        "SELECT id, pattern, match_type, priority, min_amount_minor, max_amount_minor, book_override,"
        " type_override_id FROM merchant_rules WHERE service_id = ? ORDER BY id", (service_id,)
    ).fetchall()
    # Newest first (the examples are the first three), sorted here: SQLite's
    # own sort of a merchant's every row is most of the read's time.
    held_rows = conn.execute(f"SELECT {_HELD} FROM {_FROM} WHERE t.service_id = ?", (service_id,)).fetchall()
    held_rows.sort(key=lambda r: (r["date"] or "", r["id"]), reverse=True)
    # A row's own rule matters only where one of the merchant's rules
    # overrides a field the change sets and its wording matches the row's:
    # the rule that labels the row is read then, once per row that would
    # otherwise change.
    overriding = [r for r in rules if any(r[_OVERRIDE_OF[f]] is not None for f in label)]
    for held in held_rows:
        if held["id"] in leave_out or not label:
            continue
        reached, kept = {}, False
        for field_name, value in label.items():
            source = _source(held, field_name)
            if source == label_source.CHOSEN:
                kept = True
                continue
            # A field a rule set is the rule's, not the merchant's default.
            if source is not None and (not label_source.reachable(source) or source == label_source.RULE):
                continue
            if label_source.same(field_name, value, held[field_name]):
                continue
            reached[field_name] = (value, source)
        if reached and overriding and any(_matches(r, held["description"]) for r in overriding):
            own = _own_rule(held, service_id, conn)
            if own is not None:
                reached = {f: v for f, v in reached.items() if own[_OVERRIDE_OF[f]] is None}
        if reached and held["type_id"] is None:
            plan.fills.append((held, _writes(reached)[0]))
        elif reached:
            checked = any(source == label_source.CHECKED for _, source in reached.values())
            plan.rows.append(Planned(held, *_writes(reached), checked=checked))
        elif kept:
            plan.chosen += 1
    plan.token = _stamp(
        "merchant", dict(merchant), [dict(r) for r in rules],
        [tuple(held) for held in held_rows if held["id"] not in leave_out],
        sorted(label.items()),
    )
    return plan


# --- the preview as the drawer and chat read it --------------------------------------------


def _names(conn: sqlite3.Connection) -> dict[int, str]:
    return {i: n for n, i in book_type.spending_type_ids(conn).items()}


def _word(field_name: str, value, types: dict[int, str]) -> str:
    if field_name == "book":
        return value or book_type.DEFAULT_BOOK
    return types.get(value, "no type") if value is not None else "no type"


def _label_words(fields: list[str], values: dict, types: dict[int, str]) -> str:
    return " · ".join(_word(f, values.get(f), types) for f in fields)


def _parts(conn: sqlite3.Connection, ids: list[int]) -> dict:
    """The parts (expect_back.parts) of those rows that have any: an
    expectation on them or a set-off from them. Every other row's own part
    is its amount."""
    marked = {r[0] for r in conn.execute(
        "SELECT row_id FROM expectation WHERE row_id IS NOT NULL UNION SELECT payment_row_id FROM set_off")}
    wanted = [i for i in ids if i in marked]
    found: dict = {}
    for i in range(0, len(wanted), 900):
        found.update(expect_back.parts(conn, wanted[i:i + 900]))
    return found


def _month_end(month: str) -> str:
    year, number = int(month[:4]), int(month[5:7])
    return f"{month}-{calendar.monthrange(year, number)[1]:02d}"


def _money(conn: sqlite3.Connection, plan: Plan) -> tuple[list[dict], list[dict], dict]:
    """The money moved per book pair, in S$ at the saved month-end rates;
    what could not be valued; the months touched."""
    moving = [p for p in plan.rows if "book" in p.apply]
    own = _parts(conn, [p.held["id"] for p in moving])
    sums: dict = {}
    months: set[str] = set()
    for p in moving:
        month = p.held["date"][:7]
        months.add(month)
        if (p.held["flow_type"] or "expense") not in ("expense", "refund"):
            continue
        pair = (p.held["book"] or book_type.DEFAULT_BOOK, p.apply["book"] or book_type.DEFAULT_BOOK)
        part = own.get(p.held["id"], {}).get("own_minor", p.held["amount_minor"])
        key = (pair, month, p.held["currency"])
        sums[key] = sums.get(key, 0) + (part or 0)
    moved: dict = {}
    left_out = []
    for (pair, month, currency), amount in sorted(sums.items()):
        valued = balance_sheet.valued(conn, amount, currency, _month_end(month), not_before=f"{month}-01")
        if valued["value_minor"] is None:
            left_out.append({"from": pair[0], "to": pair[1], "currency": currency, "amount_minor": amount,
                             "month": month, "reason": valued["left_out"]})
            moved.setdefault(pair, 0)
            continue
        moved[pair] = moved.get(pair, 0) + valued["value_minor"]
    money_moved = [{"from": a, "to": b, "amount_minor": n} for (a, b), n in sorted(moved.items())]
    span = {"count": len(months), "first": min(months), "last": max(months)} if months else None
    return money_moved, left_out, span


def summary(conn: sqlite3.Connection, plan: Plan) -> dict:
    """The preview: what the drawer shows and chat is told."""
    types = _names(conn)
    fields = list(plan.change)
    change = [{"field": f, "from": _word(f, was, types), "to": _word(f, now, types)}
              for f, (was, now) in plan.change.items()]
    examples = []
    for p in plan.rows[:EXAMPLES]:
        held = p.held
        after = {f: p.apply.get(f, held[f]) for f in fields}
        examples.append({
            "id": held["id"], "date": held["date"], "amount_minor": held["amount_minor"],
            "currency": held["currency"],
            "now": _label_words(fields, {f: held[f] for f in fields}, types),
            "after": _label_words(fields, after, types),
            "source": next(_source(held, f) for f in fields if f in p.apply),
        })
    checked = sum(1 for p in plan.rows if p.checked)
    out = {
        "subject": plan.subject,
        "change": change,
        "count": plan.count,
        "checked": checked,
        "chosen": plan.chosen,
        "money": None,
        "left_out": [],
        "months": None,
        "examples": examples,
        "others": [],
        "token": plan.token,
    }
    if "book" in plan.change:
        out["money"], out["left_out"], out["months"] = _money(conn, plan)
    return out


def asked(subject_name: str, count: int, answer_key: str = "past") -> str:
    """The refusal a change that reaches past rows answers with, in words."""
    rows = f"{count} past row{'' if count == 1 else 's'}"
    return (f"Nothing was saved yet: this changes {rows} of {subject_name}. "
            f"Say whether they change too ({answer_key}: {APPLY}) or only new rows take it "
            f"({answer_key}: {ONLY_NEW}).")


def stale(subject_name: str) -> str:
    """The refusal of an answer to a preview that no longer holds (case 20)."""
    return f"{subject_name} changed since this preview; look again"
