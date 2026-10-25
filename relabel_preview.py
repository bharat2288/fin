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
- `chosen`: the rows left alone because a field the change sets is chosen;
- `token`: a stamp of the merchant's and its rules' state, the rows' labels
  and the change itself. An answer carrying another token is refused: the
  merchant, its rules or its rows changed since the preview (case 20).

`summary` is the preview as the drawer and chat read it: the count, the
checked count, the chosen count, for a book change the money moved per book
pair in S$ at fin's saved month-end rates (fin-surfaces 15's rule: the
latest saved rate on or before the month end, not before its first day; a
rupee row converts) and the months it touches, the newest three examples,
and the rows another merchant holds (a rule's change and a taught wording
name them; none for a merchant's own change or a merge).

Six ways to a plan: merchant_change (a merchant's book or type, fin-surfaces
49), merge_change and rule_change (a merge; a rule added, edited or deleted,
fin-surfaces 50), taking (the rows another merchant holds that a wording
taught from the Queue would take, fin-surfaces 53), drift (the rows that
differ from what their rule says now, Re-run every rule's place,
fin-surfaces 51), and slot_change (the rows fin filed through one of its
own slots, type-relationships 03). A rule change's rows are
those whose winning rule, after the change, differs from before, or whose
label from that rule changes: the winner is asked of the rules as the change
would leave them (db.pick_rule), never of the cache, so nothing is written to
ask. Both rule previews, the rule's and the taught wording's, rank rules by
the one match order, db.match_order.

This module reads; the routes write the plan through app._write_label, so
every write lands in the route's one change entry.
"""

from __future__ import annotations

import calendar
import hashlib
import re
import sqlite3
from dataclasses import dataclass, field

import balance_sheet
import book_type
import db
import expect_back
import flow
import label_source
import loan_interest
import refund_links

# The answers to "this changes N past rows".
APPLY, ONLY_NEW = "apply", "only_new"
ANSWERS = (APPLY, ONLY_NEW)

# How many examples the preview carries.
EXAMPLES = 3

# A rule's override of each field.
_OVERRIDE_OF = {"book": "book_override", "type_id": "type_override_id"}

# What the plan reads of a row: the columns app._write_label reads of `held`,
# then what the summary needs (the account's currency and the row's own part),
# then what the classifier reads of the account the row is on.
_HELD = ("t.id, t.date, t.description, t.amount_minor, t.book, t.type_id, t.service_id, "
         "t.book_source, t.type_source, t.flow_type, t.flow_type_manual, t.other_side_id, "
         "COALESCE(a.currency, 'SGD') AS currency, a.id AS account_id, a.type AS account_kind, "
         "a.owner AS account_owner")
_FROM = ("transactions t LEFT JOIN statements st ON st.id = t.statement_id "
         "LEFT JOIN accounts a ON a.id = st.account_id")


class Refused(ValueError):
    """An answer the route cannot take; nothing was written."""


class Gone(LookupError):
    """The preview's two fixed refusals: the merchant or the rule it was asked
    about is no longer in fin. Its words are the client's to read; no other
    error's are."""


@dataclass
class Planned:
    """One row the change relabels: `apply` and `only_new` are the label
    columns each answer writes (app._LABEL_COLUMNS); a rule's and the
    drift's Apply carries the row's flow and other side where they change
    (flow_writes)."""
    held: sqlite3.Row
    apply: dict
    only_new: dict
    checked: bool = False              # a field the change reaches is checked


@dataclass
class Kept:
    """A row whose flow Apply would move but keeps: a record of expect back
    rests on it (flow_writes)."""
    held: sqlite3.Row
    would: dict                        # the flow and other side it would have had
    why: str                           # the record, in a hand label's refusal's words


@dataclass
class Plan:
    subject: dict
    change: dict                       # {field: (from, to)} for a merchant's own book and type
    rows: list[Planned] = field(default_factory=list)
    chosen: int = 0
    token: str = ""
    called: str = ""                   # what changed, as a refusal names it ("IndigoAir", "The rule INDIGO")
    doing: str = ""                    # the change in words ("Saving the rule INDIGO"); none for a merchant's
    words: list[dict] | None = None    # the change lines, when not a merchant's book and type (a rule)
    others: list[dict] = field(default_factory=list)   # rows another merchant holds, by merchant
    extra: dict = field(default_factory=dict)          # what one kind of change adds to the preview
    # A taught wording that takes another merchant's rows (fin-surfaces 53):
    # the wording, the merchant they move to, and the rows' merchants' names.
    wording: str | None = None
    moves_to: str | None = None
    names: dict[int, str] = field(default_factory=dict)
    kept: list[Kept] = field(default_factory=list)     # rows whose flow Apply keeps (flow_writes)

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
    return label_source.source_of(held[column])


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
                    leave_out: set[int] | frozenset = frozenset(), teaching: bool = False) -> Plan:
    """The plan for a merchant's new book or type ({field: value}, the
    fields that change): each of its rows (but `leave_out`) whose field a
    later change may reach, not set by a rule and not overridden by the row's
    own rule, and whose value differs. A row whose field the change sets is
    chosen is left alone and counted apart. A row waiting for a type takes
    its merchant's label as every row that inherits it does: Apply labels
    it, Only new rows leaves it waiting, and the preview counts it, so the
    count is the rows Apply changes (spec 24, Deepened). Only when
    `teaching` (resolve, from the Queue) is a row with no type left out: the
    teach labels the rows it matches itself (its backfill, as before the
    preview), and where it asks, the route adds those to the count
    (with_fills), so the count is still the rows the change writes. (An
    empty book reads as the default book.) A row that is neither spending
    nor a refund is not the merchant's to label (fin-redesign 05): it keeps
    the book and type it holds, and is not counted."""
    merchant = conn.execute(
        "SELECT id, name, book, type_id, review_each_time FROM services WHERE id = ?", (service_id,)
    ).fetchone()
    if merchant is None:
        raise Gone("This merchant is no longer in fin")
    plan = Plan(
        subject={"kind": "merchant", "id": merchant["id"], "name": merchant["name"]},
        change={f: (merchant[f], v) for f, v in label.items()},
        called=merchant["name"],
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
        if held["id"] in leave_out or not label or (teaching and held["type_id"] is None):
            continue
        # A row that is no one's spending takes no merchant's label
        # (label_source.fitted): it keeps what it holds, and is not counted.
        if not flow.carries_label(held["flow_type"]):
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
        if reached:
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


# --- the rule shown when teaching (fin-surfaces 53) ----------------------------------------

MATCH_TYPES = ("contains", "startswith", "exact")


@dataclass
class Reach:
    """The rows a wording taught as a merchant's rule would label: every row
    whose winning rule would then be the taught one (`rows`, newest first),
    and of them those another merchant holds (`others`: {service_id: rows})."""
    pattern: str
    match_type: str
    service_id: int | None
    rows: list[sqlite3.Row] = field(default_factory=list)
    others: dict[int, list[sqlite3.Row]] = field(default_factory=dict)
    names: dict[int, str] = field(default_factory=dict)
    taught: dict = field(default_factory=dict)   # the rule as it would be taught

    @property
    def count(self) -> int:
        return len(self.rows)

    def only(self, tx_ids) -> bool:
        """The wording matches one of the rows being taught and no other row
        (a booking number kept in it, fin-surfaces 19)."""
        return self.count == 1 and self.rows[0]["id"] in set(tx_ids or ())

    def others_named(self, kept=lambda held: False) -> list[dict]:
        """The rows another merchant holds that the rule would take, by
        merchant, most first: those `kept` (a field chosen) left out."""
        out = []
        for svc, rows in self.others.items():
            n = sum(1 for held in rows if not kept(held))
            if n:
                out.append({"service_id": svc, "name": self.names.get(svc, "another merchant"), "count": n})
        return sorted(out, key=lambda o: (-o["count"], o["name"]))


def _wins_amount(rule, amount_minor) -> bool:
    """Whether a rule's amount limits let a row's amount through, as
    db.first_rule reads them."""
    low, high = rule["min_amount_minor"], rule["max_amount_minor"]
    if amount_minor is None:
        return low is None and high is None
    return (low is None or amount_minor >= low) and (high is None or amount_minor <= high)


class _Stronger:
    """The rules that would win a row before a taught one, asked as one:
    those with no amount limits as one pattern per match type, the few with
    limits one by one (db.first_rule's matching, read over many rows)."""

    def __init__(self, rules):
        plain = [r for r in rules if r["min_amount_minor"] is None and r["max_amount_minor"] is None]
        self.limited = [r for r in rules if r not in plain]
        words = {kind: sorted({r["pattern"].upper() for r in plain if r["match_type"] == kind and r["pattern"]}, key=len, reverse=True)
                 for kind in MATCH_TYPES}
        self.contains = re.compile("|".join(map(re.escape, words["contains"]))) if words["contains"] else None
        self.starts = tuple(words["startswith"])
        self.exact = set(words["exact"])

    def wins(self, description: str | None, amount_minor) -> bool:
        text = (description or "").upper()
        if self.contains is not None and self.contains.search(text):
            return True
        if (self.starts and text.startswith(self.starts)) or text in self.exact:
            return True
        return any(_matches(r, description) and _wins_amount(r, amount_minor) for r in self.limited)


def wording_reach(conn: sqlite3.Connection, pattern: str, match_type: str = "contains",
                  service_id: int | None = None) -> Reach:
    """The rows a wording taught as `service_id`'s rule would label (None: a
    merchant fin does not hold yet). Teaching inserts the rule, or re-points
    the rule fin already has for that wording (its priority, limits and
    match kept, as resolve does): a row is reached when its wording matches
    and the taught rule would win it, in fin's one match order
    (db.match_order: priority, then the longest wording, then the oldest
    rule; a wording not yet a rule after every rule it ties with), the order
    the rule preview (rule_change) asks too. A row another rule wins is not
    reached, so rows a longer or higher rule holds are never taken."""
    pattern = pattern.strip()
    existing = conn.execute(
        "SELECT id, pattern, match_type, priority, min_amount_minor, max_amount_minor FROM merchant_rules"
        " WHERE UPPER(pattern) = ? ORDER BY id LIMIT 1", (pattern.upper(),)).fetchone()
    taught = dict(existing) if existing else {
        "id": None, "pattern": pattern, "match_type": match_type, "priority": 0,
        "min_amount_minor": None, "max_amount_minor": None}
    reach = Reach(pattern=pattern, match_type=taught["match_type"], service_id=service_id)
    like = {"contains": "UPPER(t.description) LIKE '%' || ? || '%'",
            "startswith": "UPPER(t.description) LIKE ? || '%'"}.get(taught["match_type"], "UPPER(t.description) = ?")
    # LIKE's own wildcards in a wording are matched by _matches below.
    candidates = conn.execute(
        f"SELECT {_HELD} FROM {_FROM} WHERE {like}", (taught["pattern"].upper(),)).fetchall()
    # Only a rule before the taught one in match order can win a row from
    # it: the rest are never asked (the rules read now, as rule_change reads them).
    place = db.match_order(taught)
    stronger = _Stronger([r for r in db.load_rules(conn) if r["id"] != taught["id"] and db.match_order(r) < place])
    for held in candidates:
        if not _matches(taught, held["description"]) or not _wins_amount(taught, held["amount_minor"]):
            continue
        if stronger.wins(held["description"], held["amount_minor"]):
            continue
        reach.rows.append(held)
        if held["service_id"] is not None and held["service_id"] != service_id:
            reach.others.setdefault(held["service_id"], []).append(held)
    reach.rows.sort(key=lambda r: (r["date"] or "", r["id"]), reverse=True)
    if reach.others:
        marks = ",".join("?" * len(reach.others))
        reach.names = {r[0]: r[1] for r in conn.execute(
            f"SELECT id, name FROM services WHERE id IN ({marks})", list(reach.others))}
    reach.taught = taught
    return reach


def together(reaches: list[Reach]) -> Reach:
    """One reach for a teach of several wordings (queue-naming 17): the rows
    any of them labels, each once, and of them those another merchant holds;
    the wordings named together. One wording's reach is itself."""
    if len(reaches) == 1:
        return reaches[0]
    first = reaches[0]
    reach = Reach(pattern=" · ".join(r.pattern for r in reaches), match_type=first.match_type,
                  service_id=first.service_id, taught={"wordings": [r.taught for r in reaches]})
    seen = set()
    for one in reaches:
        for held in one.rows:
            if held["id"] in seen:
                continue
            seen.add(held["id"])
            reach.rows.append(held)
            if held["service_id"] is not None and held["service_id"] != reach.service_id:
                reach.others.setdefault(held["service_id"], []).append(held)
        reach.names.update(one.names)
    reach.rows.sort(key=lambda r: (r["date"] or "", r["id"]), reverse=True)
    return reach


def kept(held) -> bool:
    """A row the rule leaves with its merchant: a field of it chosen or
    worked out."""
    return any(_source(held, f) is not None and not label_source.reachable(_source(held, f))
               for f in label_source.FIELDS)


def taking(conn: sqlite3.Connection, reach: Reach, name: str, label: dict, sources: tuple) -> Plan:
    """The plan for the rows another merchant holds that a taught wording
    would take (fin-surfaces 53, through 21's preview): Apply moves each to
    the merchant taught with its label (`label`: book and type; each field's
    source as it was, a field fin gave taking `sources`, the rule's); Only
    new rows leaves each with its merchant, the fields fin gave checked. A
    row with a field chosen or worked out stays as it is, counted apart. The
    merchant's id is the route's to add: a new one has none yet, and so are
    the rows' flows (app._taken_flows, as a teach's backfill works them out)."""
    plan = Plan(subject={"kind": "merchant", "id": reach.service_id, "name": name}, change={}, called=name)
    plan.wording = reach.pattern
    plan.moves_to = name
    for svc in reach.others:
        for held in reach.others[svc]:
            if kept(held):
                plan.chosen += 1
                continue
            apply, only_new = {}, {}
            for (field_name, value), source in zip(label.items(), sources):
                if value is None:
                    continue
                apply[field_name] = value
                apply[label_source.FIELDS[field_name]] = label_source.reached(
                    _source(held, field_name), value, source)
            for field_name in label_source.FIELDS:
                accepted = label_source.accepted(_source(held, field_name))
                if accepted is not None:
                    only_new[label_source.FIELDS[field_name]] = accepted
            checked = any(_source(held, f) == label_source.CHECKED for f in label_source.FIELDS)
            plan.rows.append(Planned(held, apply, only_new, checked=checked))
    plan.rows.sort(key=lambda p: (p.held["date"] or "", p.held["id"]), reverse=True)
    plan.names = dict(reach.names)
    plan.others = reach.others_named(kept)
    # The token stamps the rule as taught and the rows it would take, as
    # the preview saw them; the live count needs none.
    plan.token = _stamp("wording", reach.taught, reach.service_id,
                        [tuple(held) for rows in reach.others.values() for held in rows],
                        name, sorted(label.items()), sources)
    return plan


def joined(first: Plan | None, second: Plan | None) -> Plan | None:
    """One preview for a teach that changes a held merchant's default and
    whose wording takes another merchant's rows: one question, both sets of
    rows, one token."""
    if first is None or second is None:
        return first or second
    plan = Plan(subject=first.subject, change=dict(first.change), rows=first.rows + second.rows,
                chosen=first.chosen + second.chosen,
                called=first.called, doing=first.doing, kept=first.kept + second.kept)
    plan.wording, plan.moves_to = second.wording, second.moves_to
    plan.names, plan.others = second.names, second.others
    plan.token = _stamp(first.token, second.token)
    return plan


def with_fills(conn: sqlite3.Connection, plan: Plan, fills: dict[int, dict], flows: dict[int, dict] | None = None,
               kept: list[Kept] | tuple = ()) -> Plan:
    """A teach's preview (resolve, from the Queue): the plan's rows and the
    merchant's own rows the teach labels itself whatever the answer
    (`fills`, {row id: the label columns its backfill writes}: those its
    wording matches that have no type yet, which merchant_change leaves out
    when teaching). They are written in the same change entry, so the
    preview counts and shows them, as the merchant sheet does, and the count
    is the past rows the entry writes (spec 24, Deepened; case 13). The
    flow and other side Apply alone writes to a filled row (`flows`, {row
    id: flow_writes}; Only new rows leaves them) and the rows whose flow it
    keeps (`kept`) are the preview's flows and flows_kept, as a rule's are.
    The token stamps them too. The route writes them, not the plan; with no
    fills and none kept, the plan as it is."""
    flows = flows or {}
    if not fills and not kept:
        return plan
    filled = []
    for held in _held_rows(conn, list(fills)):
        sets = fills[held["id"]]
        checked = any(_source(held, f) == label_source.CHECKED for f in label_source.FIELDS if f in sets)
        # Each answer's write fitted to the flow it leaves the row
        # (label_source.fitted): Apply's with the flow it moves.
        filled.append(Planned(held, label_source.fitted(held, {**sets, **flows.get(held["id"], {})}),
                              label_source.fitted(held, sets), checked=checked))
    shown = Plan(subject=plan.subject, change=dict(plan.change), chosen=plan.chosen, called=plan.called,
                 doing=plan.doing, words=plan.words, others=list(plan.others), extra=dict(plan.extra),
                 wording=plan.wording, moves_to=plan.moves_to, names=dict(plan.names),
                 kept=[*plan.kept, *kept])
    shown.rows = sorted(plan.rows + filled, key=lambda p: (p.held["date"] or "", p.held["id"]), reverse=True)
    shown.token = _stamp(plan.token, sorted((i, tuple(sorted(s.items()))) for i, s in fills.items()),
                         [tuple(p.held) for p in filled], _stamp_flows(shown))
    return shown


def _accept_all(held) -> dict:
    """Only new rows when the row's merchant changes (a merge) or would
    have (a rule): each field fin gave is checked, its value left."""
    out = {}
    for field_name, column in label_source.FIELDS.items():
        accepted = label_source.accepted(_source(held, field_name))
        if accepted is not None:
            out[column] = accepted
    return out


def merge_change(conn: sqlite3.Connection, source_id: int, target_id: int) -> Plan:
    """The plan for merging one merchant into another: its rows move to the
    target whatever the answer (the route moves them), and those the
    target's book and type would relabel are the preview's rows, as for a
    merchant's own change (merchant_change: a chosen field stays, a field a
    rule override sets is the rule's, and the rules move with the rows).
    Only new rows keeps their book and type, each field from fin checked:
    they are the target's now, but not by its default."""
    target = conn.execute("SELECT id, name, book, type_id FROM services WHERE id = ?", (target_id,)).fetchone()
    if target is None:
        raise Gone("This merchant is no longer in fin")
    plan = merchant_change(conn, source_id, {"book": target["book"], "type_id": target["type_id"]})
    for planned in plan.rows:
        planned.only_new = _accept_all(planned.held)
    source_name = plan.subject["name"]
    plan.subject = {"kind": "merge", "id": source_id, "name": source_name,
                    "into": {"id": target["id"], "name": target["name"]}}
    plan.doing = f"Merging {source_name} into {target['name']}"
    plan.token = _stamp(plan.token, tuple(target))
    return plan


# What a rule change may set, as the rule routes take it (merchant_rules' columns).
RULE_FIELDS = ("pattern", "match_type", "service_id", "book_override", "type_override_id", "priority",
               "min_amount_minor", "max_amount_minor", "flow", "other_side_id")
_RULE_WORDS = {"pattern": "Wording", "match_type": "Match", "service_id": "Merchant",
               "book_override": "Book of its own", "type_override_id": "Type of its own",
               "priority": "Priority", "min_amount_minor": "Only from", "max_amount_minor": "Only up to",
               "flow": "What its rows are", "other_side_id": "Other side"}
_MATCH_WORDS = {"contains": "contains", "startswith": "starts with", "exact": "exactly"}
_NEW_RULE = {"id": None, "pattern": "", "match_type": "contains", "priority": 0, "min_amount_minor": None,
             "max_amount_minor": None, "service_id": None, "book_override": None, "type_override_id": None,
             "flow": None, "other_side_id": None}
# A row's flow as a preview names it.
FLOW_WORDS = {"expense": "spending", "income": "income", "transfer": "a transfer", "payment": "a card payment",
              "refund": "a refund", flow.MOVEMENT: "a movement", flow.REVIEW: "waiting for review"}
# What a rule that states no flow says of its rows.
_NO_FLOW = "what fin works out"


def _rule_key(rule):
    """What a winning rule gives a row: which rule, its merchant, its label
    and the label's sources. A row whose key is the same before and after
    the change is not the change's to relabel."""
    if rule is None:
        return None
    return (rule["id"], rule["service_id"], *db.rule_label(rule), label_source.rule_sources(rule))


def _given(rule) -> dict:
    """What a winning rule (or none) gives a row: as app._label_for gives it,
    for a rule list the change would leave."""
    service_id = book = type_id = book_source = type_source = None
    if rule is not None:
        service_id = rule["service_id"]
        book, type_id = db.rule_label(rule)
        book_source, type_source = label_source.rule_sources(rule)
    return {"service_id": service_id, "book": book, "type_id": type_id,
            "book_source": label_source.given(book, book_source or label_source.MERCHANT),
            "type_source": label_source.given(type_id, type_source or label_source.MERCHANT)}


def _rule_words(conn, field_name: str, value, types: dict[int, str]) -> str:
    if field_name == "match_type":
        return _MATCH_WORDS.get(value, value)
    if field_name == "service_id":
        found = conn.execute("SELECT name FROM services WHERE id = ?", (value,)).fetchone()
        return found["name"] if found else "no merchant"
    if field_name == "book_override":
        return value or "the merchant’s"
    if field_name == "type_override_id":
        return types.get(value, "the merchant’s") if value is not None else "the merchant’s"
    if field_name == "priority":
        return str(value or 0)
    if field_name in ("min_amount_minor", "max_amount_minor"):
        return "none" if value is None else f"S$ {value / 100:,.2f}"
    if field_name == "flow":
        return FLOW_WORDS.get(value, value) if value else _NO_FLOW
    if field_name == "other_side_id":
        found = conn.execute("SELECT name FROM accounts WHERE id = ?", (value,)).fetchone() if value else None
        return found["name"] if found else "none"
    return str(value)


def _held_rows(conn, ids: list[int]) -> list[sqlite3.Row]:
    found = []
    for i in range(0, len(ids), 900):
        part = ids[i:i + 900]
        found += conn.execute(f"SELECT {_HELD} FROM {_FROM} WHERE t.id IN ({','.join('?' * len(part))})",
                              part).fetchall()
    found.sort(key=lambda r: (r["date"] or "", r["id"]), reverse=True)
    return found


def held_account(held) -> dict:
    """The account a held row is on, its kind and owner, as the classifier
    reads them (none for a row on no account)."""
    return ({"account_id": held["account_id"], "account_kind": held["account_kind"],
             "account_owner": held["account_owner"]}
            if held["account_kind"] is not None else {})


def fins_own(held, before, account: dict, ctx: flow.ClassifierContext) -> bool:
    """Whether a row's stored flow and other side are still what fin worked
    out for it under the rules before the change: the flow and other side
    the rule that labelled it then (`before`, or None) states, where it
    states one; else what the classifier gives the row as it is. Only such a
    row's flow is worked out again on a rule change (ADR-009). A flow
    anything else said stays: an older book's refund, a pairing, the linker,
    and the classifier's flow Only new rows left under a rule that states
    another."""
    stored = (held["flow_type"], held["other_side_id"])
    return flow.worked_out_again(held, held["service_id"], held["type_id"], before, account, ctx) == stored


def flow_writes(conn: sqlite3.Connection, held, sets: dict, before, after, ctx: flow.ClassifierContext,
                account: dict | None = None) -> tuple[dict, Kept | None]:
    """The flow and other side Apply writes to a row already in the book
    besides its new label (`sets`), where they change: what
    flow.worked_out_again gives for the merchant and type the row would have
    and the rule that would label it (`after`, or None; the flow it states
    says what the row is). And the row kept, where the change would move its
    flow but a record of expect back rests on it, or None.

    Never a flow set by hand, nor its other side; nor a flow fin did not
    work out (fins_own, with the rule that labelled the row before the
    change, `before`); nor a move a hand label would refuse the row, a
    record resting on it (expect_back.label_refusal, with the flow, book and
    other side's kind the row would have): that row is kept, in the
    refusal's own words. `account` is the kind and owner of the account the
    row is on (held_account, by default)."""
    if held["flow_type_manual"]:
        return {}, None
    if account is None:
        account = held_account(held)
    worked_out = flow.worked_out_again(held, sets.get("service_id", held["service_id"]),
                                       sets.get("type_id", held["type_id"]), after, account, ctx)
    writes = {k: v for k, v in zip(("flow_type", "other_side_id"), worked_out) if held[k] != v}
    if not writes or not fins_own(held, before, account, ctx):
        return {}, None
    flow_name, other_side = worked_out
    other_kind = (conn.execute("SELECT type FROM accounts WHERE id = ?", (other_side,)).fetchone()
                  or [None])[0] if other_side is not None else None
    why = move_refusal(conn, held, flow_name, sets.get("book", held["book"]), other_kind)
    if why is not None:
        return {}, Kept(held, writes, why)
    return writes, None


def move_refusal(conn: sqlite3.Connection, held, flow_name: str | None, book: str | None,
                 other_kind: str | None) -> str | None:
    """Why a hand label would refuse a row the flow a rule gives it (its
    flow, book and other side's kind after the write), or None, asked as the
    hand label asks it (app._label_row): a row that stops being a refund
    first leaves its purchase (refund_links.drop: its link and the set-offs
    the link placed; refused while the refund is To pay back or its
    purchase's expectation is let go), then what still rests on it is
    weighed (expect_back.label_refusal). The drop is tried and taken back:
    asking writes nothing. The write itself drops the link
    (app._write_label)."""
    leaves = (held["flow_type"] == "refund" and flow_name != "refund" and conn.execute(
        "SELECT 1 FROM refund_link WHERE refund_row_id = ?", (held["id"],)).fetchone() is not None)
    if not leaves:
        return expect_back.label_refusal(conn, held["id"], flow_name, book, other_kind)
    conn.execute("SAVEPOINT move_refusal")
    try:
        try:
            refund_links.drop(conn, held["id"])
        except (expect_back.Refused, refund_links.Refused) as e:
            return str(e)
        return expect_back.label_refusal(conn, held["id"], flow_name, book, other_kind)
    finally:
        conn.execute("ROLLBACK TO move_refusal")
        conn.execute("RELEASE move_refusal")


def taught_rules(rules: list[dict], words, match_type: str,
                 said: tuple[str, int | None] | None = None, service_id: int | None = None) -> list[dict]:
    """The rules as a teach of `words` onto merchant `service_id` (None: a
    merchant fin does not hold yet) leaves them, in match order, for which
    rule wins a row: a wording fin holds a rule for keeps that rule (a teach
    changes its merchant and overrides, never its match), and the flow it
    states where it is this merchant's already; one moved here from another
    merchant states this merchant's stated flow (`said`,
    flow.merchant_stated) or none, never the old merchant's. A wording fin
    holds no rule for gains one that states `said`, or none."""
    held = {r["pattern"].upper() for r in rules}
    taught = {w.upper() for w in words}
    flow_name, other_side = said or (None, None)
    ruled = [{**r, "flow": flow_name, "other_side_id": other_side}
             if r["pattern"].upper() in taught and (service_id is None or r["service_id"] != service_id) else r
             for r in rules]
    added = [{**_NEW_RULE, "pattern": w, "match_type": match_type, "flow": flow_name, "other_side_id": other_side}
             for w in dict.fromkeys(words) if w.upper() not in held]
    return sorted(ruled + added, key=db.match_order)


def _stamp_flows(plan: Plan) -> list:
    """What a plan's token stamps of its flows: each row's writes and each
    row kept, so a preview's list is what its Apply writes."""
    return [[(p.held["id"], tuple(sorted(p.apply.items()))) for p in plan.rows],
            [(k.held["id"], k.why) for k in plan.kept]]


def rule_change(conn: sqlite3.Connection, rule_id: int | None, proposed: dict | None) -> Plan:
    """The plan for a rule added (rule_id None), edited (`proposed`: the
    RULE_FIELDS that change) or deleted (`proposed` None).

    Its rows are those whose winning rule, after the change, differs from
    before, or whose label from that rule changes, and whose label would
    then change: each field a later change may reach takes what the new
    winner gives, a checked field staying checked and not emptied, as a
    re-run of the rules writes it. So a rule's priority and amount limits are honoured, and a
    row another rule wins is not touched. A row with no rule left loses its
    merchant and waits in the Queue. Only new rows keeps each value, the
    fields that would change checked (both, where the merchant would). A row
    whose every field the change would reach is chosen is left alone and
    counted apart."""
    rules = db.load_rules(conn)
    held_rule = None
    if rule_id is not None:
        held_rule = next((r for r in rules if r["id"] == rule_id), None)
        if held_rule is None:
            raise Gone("This rule is no longer in fin")
    after_rule = None
    if proposed is not None:
        after_rule = {**(held_rule or _NEW_RULE), **proposed}
        merchant = conn.execute("SELECT book, type_id, review_each_time FROM services WHERE id = ?",
                                (after_rule["service_id"],)).fetchone()
        if merchant is None:
            raise Gone("This merchant is no longer in fin")
        after_rule.update(dict(merchant))
    after_rules = sorted([r for r in rules if r["id"] != rule_id] + ([after_rule] if after_rule else []),
                         key=db.match_order)
    shown = after_rule or held_rule
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM services")}
    verb = "add" if held_rule is None else "delete" if after_rule is None else "edit"
    plan = Plan(
        subject={"kind": "rule", "id": rule_id, "name": shown["pattern"], "verb": verb,
                 "merchant": {"id": shown["service_id"], "name": names.get(shown["service_id"])}},
        change={},
        called="The rules" if held_rule is None else f"The rule {held_rule['pattern']}",
        doing={"add": "Adding", "edit": "Saving", "delete": "Deleting"}[verb] + f" the rule {shown['pattern']}",
    )
    types = _names(conn)
    if verb == "edit":
        plan.words = [{"field": f, "label": _RULE_WORDS[f], "from": _rule_words(conn, f, held_rule[f], types),
                       "to": _rule_words(conn, f, after_rule[f], types)}
                      for f in RULE_FIELDS if f in proposed and proposed[f] != held_rule[f]]
    elif verb == "add":
        plan.words = [{"field": "rule", "label": "New rule", "from": None, "to": after_rule["pattern"]}]
    else:
        plan.words = [{"field": "rule", "label": "Rule", "from": held_rule["pattern"], "to": None}]

    # Only a row the rule matches, as it was or as it would be, can have its
    # winner change; the rest are not read. Transfers and card payments are
    # not the rules' to label, unless the rule states a flow, as it was or as
    # it would be: then it says what its rows are, whatever the classifier made
    # of them.
    versions = [r for r in (held_rule, after_rule) if r is not None]
    stating = any(flow.stated(v) for v in versions)
    ids = [r["id"] for r in conn.execute(
        "SELECT id, description, flow_type FROM transactions")
        if (stating or (r["flow_type"] or "expense") not in ("transfer", "payment"))
        and any(_matches(v, r["description"]) for v in versions)]
    held_rows = _held_rows(conn, ids)
    others: dict[int, int] = {}
    labelled = unheld = 0
    ctx = flow.build_context(conn) if held_rows else None
    for held in held_rows:
        before = db.pick_rule(rules, held["description"] or "", held["amount_minor"])
        after = db.pick_rule(after_rules, held["description"] or "", held["amount_minor"])
        sources = {f: _source(held, f) for f in label_source.FIELDS}
        # A field's mark as the rules' re-run reads it: an empty field reads
        # its own source, so a row named by hand on the review list (a
        # movement, income) with no book or type, its fields chosen, is not
        # the rules' to take.
        marks = {f: label_source.source_of(held[c]) for f, c in label_source.FIELDS.items()}
        reachable = {f: label_source.reachable(m) for f, m in marks.items()}
        if held_rule is not None and before is not None and before["id"] == rule_id and any(reachable.values()):
            labelled += 1
        relabels = _rule_key(before) != _rule_key(after)
        restates = flow.stated(before) != flow.stated(after)
        if not (relabels or restates):
            continue
        def relabelled(found):
            """What the winner's label writes to the row (each field a later
            change may reach), what Only new rows writes, the fields marked,
            and whether the row is left alone, every field reached chosen."""
            writes, only_new, marked, chosen = {}, {}, [], False
            sets, reached, kept = {"service_id": found["service_id"]}, [], False
            for field_name, column in label_source.FIELDS.items():
                source = sources[field_name]
                if not reachable[field_name]:
                    kept, chosen = True, chosen or marks[field_name] == label_source.CHOSEN
                    continue
                if source == label_source.CHECKED and found[field_name] is None:
                    kept = True
                    continue                            # a checked value is not emptied
                sets[field_name] = found[field_name]
                sets[column] = label_source.reached(source, found[field_name], found[column])
                reached.append(field_name)
            if kept and found["service_id"] is None:
                del sets["service_id"]                  # a row keeping a field keeps its merchant
            # A row whose every field the change reaches is chosen is left
            # alone, and counted apart.
            left = not reached and chosen and (found["service_id"] != held["service_id"]
                                               or any(found[f] != held[f] for f in label_source.FIELDS))
            if reached:
                writes = {k: v for k, v in sets.items() if held[k] != v}
                moves = "service_id" in writes
                marked = [f for f in reached if moves or f in writes or label_source.FIELDS[f] in writes]
                for field_name in marked:
                    accepted = label_source.accepted(sources[field_name])
                    if accepted is not None and accepted != held[label_source.FIELDS[field_name]]:
                        only_new[label_source.FIELDS[field_name]] = accepted
            return writes, only_new, marked, left

        writes, only_new, marked, left = relabelled(_given(after)) if relabels else ({}, {}, [], False)
        # The row's flow, worked out again where its label changes or the
        # flow the rule states does: Apply writes it, Only new rows leaves it.
        flows, kept = flow_writes(conn, held, writes, before, after, ctx) if writes or restates else ({}, None)
        if kept is not None:
            plan.kept.append(kept)
        # A row the change moves into spending from a flow that carries no
        # label takes the label the rule gives it, as a relabelled row does.
        if not relabels and flow.carries_label(flows.get("flow_type", held["flow_type"])) \
                and not flow.carries_label(held["flow_type"]):
            writes, only_new, marked, left = relabelled(_given(after))
        # Each answer's write fitted to the flow it leaves the row
        # (label_source.fitted): no label on a row that is no one's
        # spending, and Apply empties it on a row it moves there.
        apply = label_source.fitted(held, {**writes, **flows})
        only_new = label_source.fitted(held, only_new)
        writes = {k: v for k, v in apply.items() if k not in flows}
        if not apply:
            plan.chosen += left
            continue
        plan.rows.append(Planned(held, apply, only_new,
                                 checked=any(sources[f] == label_source.CHECKED for f in marked
                                             if f in apply or label_source.FIELDS[f] in apply)))
        if not writes:
            continue
        if held["service_id"] is None:
            unheld += 1
        elif "service_id" in writes and held["service_id"] != shown["service_id"]:
            others[held["service_id"]] = others.get(held["service_id"], 0) + 1
    plan.others = [{"service_id": i, "name": names.get(i), "count": n} for i, n in
                   sorted(others.items(), key=lambda kv: (-kv[1], names.get(kv[0]) or ""))]
    plan.extra = {"unheld": unheld, "labelled": labelled}
    plan.token = _stamp("rule", rule_id, sorted((proposed or {}).items()),
                        [tuple(sorted(r.items())) for r in rules], [tuple(h) for h in held_rows],
                        _stamp_flows(plan))
    return plan


# --- the drift count: Re-run every rule retired (fin-surfaces 51) --------------------------


class _Winner:
    """The rule that labels a row, as db.pick_rule picks it among `rules`
    (in match order), asked of many rows. A wording matches a row only if
    every GRAM-letter run of it is in the row's text, so each wording is
    indexed by the one of its runs the fewest wordings share, and a row asks
    only the rules indexed by a run of its own text (a wording shorter than
    GRAM is always asked); the first of those in match order that matches
    wins, as pick_rule's walk down the list finds it. A wording is read once
    however many rows carry it."""

    GRAM = 3

    def __init__(self, rules):
        self.grams: dict[str, list] = {}
        self.exact: dict[str, list] = {}
        self.short: list = []
        runs = lambda text: {text[i:i + self.GRAM] for i in range(len(text) - self.GRAM + 1)}
        shared: dict[str, int] = {}
        for r in rules:
            for run in runs(r["pattern"].upper()):
                shared[run] = shared.get(run, 0) + 1
        for place, r in enumerate(rules):
            pattern = r["pattern"].upper()
            entry = (place, r, pattern, r["match_type"])
            if r["match_type"] == "exact":
                self.exact.setdefault(pattern, []).append(entry)
            elif r["match_type"] in ("contains", "startswith"):
                if len(pattern) < self.GRAM:
                    self.short.append(entry)
                else:
                    rarest = min(sorted(runs(pattern)), key=lambda run: shared[run])
                    self.grams.setdefault(rarest, []).append(entry)
        self.limited = any(r["min_amount_minor"] is not None or r["max_amount_minor"] is not None for r in rules)
        self.seen: dict = {}

    def _first(self, text: str, amount_minor):
        asked = list(self.short) + self.exact.get(text, [])
        for run in {text[i:i + self.GRAM] for i in range(len(text) - self.GRAM + 1)}:
            asked += self.grams.get(run, ())
        for place, r, pattern, kind in sorted(asked, key=lambda e: e[0]):
            if kind == "contains":
                matched = pattern in text
            elif kind == "startswith":
                matched = text.startswith(pattern)
            else:
                matched = text == pattern
            if matched and _wins_amount(r, amount_minor):
                return r
        return None

    def of(self, description: str | None, amount_minor):
        key = (description, amount_minor if self.limited else None)
        if key not in self.seen:
            self.seen[key] = self._first((description or "").upper(), amount_minor)
        return self.seen[key]


def drift(conn: sqlite3.Connection) -> Plan:
    """The rows that differ from what their rule says now: Re-run every
    rule's place (fin-surfaces 21 and 51; spec 24, the drift count). A row
    counts when a field of it from the merchant or a rule holds a value
    other than what the winning rule gives now, or, every field being from
    fin, its merchant is not the rule's. A checked or chosen field is never
    counted and never written, nor is one from the wording: no rule gives
    that source now (the PayNow wordings are rules since type-relationships
    05), and the rows it labelled keep their label and source (its case 20).
    An empty field reads its own source, as the rule preview reads it.
    Apply writes what the rules give, each field taking the source that
    gives it, its flow worked out again (flow_writes: the flow the rule
    states, where it states one), in one change; there is no Only new
    rows (Leave them writes nothing). Transfers and card payments are not
    the rules' to label; nor is any row that is neither spending nor a
    refund (fin-redesign 05): such a row differs only by its merchant, and
    keeps the book and type it holds."""
    rules = db.load_rules(conn)
    winner = _Winner(rules)
    plan = Plan(subject={"kind": "drift", "id": None, "name": "the rules"}, change={},
                called="These rows", doing="Relabelling the rows that differ from their rule")
    plan.words = []
    # Read light first (plain tuples, no join): every row with a field from
    # fin; then whole, for the rows that differ only.
    cursor = conn.cursor()
    cursor.row_factory = None
    read = cursor.execute(
        "SELECT t.id, t.description, t.amount_minor, t.service_id, t.book, t.book_source, t.type_id,"
        " t.type_source, t.flow_type FROM transactions t"
        " WHERE COALESCE(t.flow_type, 'expense') NOT IN ('transfer', 'payment')"
        f" AND {label_source.from_fin_sql('t')}").fetchall()
    # A field from the wording is kept as a checked one is (case 20 above).
    rerun, source_of = (label_source.MERCHANT, label_source.RULE), label_source.source_of
    by_rule: dict = {}                     # what a rule that gives a type gives every row it wins
    writes_of: dict[int, dict] = {}
    rule_of: dict[int, dict | None] = {}   # the rule that wins each of them
    for tx_id, description, amount_minor, service_id, book, book_source, type_id, type_source, flow_type in read:
        # A row that is no one's spending differs only by its merchant: its
        # book and type are not the rules' to give (label_source.fitted).
        labelled = flow.carries_label(flow_type)
        rule = winner.of(description, amount_minor)
        key = rule["id"] if rule is not None and (rule["type_override_id"] or rule["type_id"]) else None
        found = by_rule.get(key) if key is not None else None
        if found is None:
            found = _given(rule)
            if key is not None:
                by_rule[key] = found
        sets, differs, kept = {}, False, False
        for field_name, column, value, source in (("book", "book_source", book, book_source),
                                                  ("type_id", "type_source", type_id, type_source)):
            if source_of(source) not in rerun:
                kept = True
                continue
            differs = differs or (labelled and found[field_name] != value)
            sets[field_name], sets[column] = found[field_name], found[column]
        if not kept:
            differs = differs or found["service_id"] != service_id
        if not differs:
            continue
        if not kept or found["service_id"] is not None:
            sets["service_id"] = found["service_id"]      # a row keeping a field keeps its merchant
        writes_of[tx_id] = sets
        rule_of[tx_id] = rule
    ctx = flow.build_context(conn) if writes_of else None
    for held in _held_rows(conn, list(writes_of)):        # newest first
        writes = {k: v for k, v in writes_of[held["id"]].items() if held[k] != v}
        # The rules are as they were: the rule that wins the row is the one
        # that labelled it, before and after.
        rule = rule_of[held["id"]]
        flows, kept = flow_writes(conn, held, writes, rule, rule, ctx)
        if kept is not None:
            plan.kept.append(kept)
        apply = label_source.fitted(held, {**writes, **flows})
        if apply:
            plan.rows.append(Planned(held, apply, {}))
    plan.token = _stamp("drift", [tuple(sorted(r.items())) for r in rules], [tuple(p.held) for p in plan.rows],
                        _stamp_flows(plan))
    return plan


def drift_asked(plan: Plan) -> str:
    """The refusal the drift's Apply answers with when it was not answered."""
    return (f"Nothing was relabelled yet: {plan.count} row{'' if plan.count == 1 else 's'} "
            f"differ from what their rule says now. Send past: {APPLY} to relabel them; "
            "leaving them as they are writes nothing.")


def slot_change(conn: sqlite3.Connection, slot: str, type_id: int) -> Plan:
    """The plan for moving one of fin's own filings to another type
    (type-relationships 03): the rows fin filed through the slot that are
    not under that type yet, where they can be told apart
    (book_type.PAST_TOLD: loan interest's worked-out rows). Apply moves
    each to the type, its source as it was; Only new rows writes nothing to
    them. Where they cannot be told apart there are no rows, and the preview
    offers new rows only."""
    was = book_type.slot_type(conn, slot)
    words = book_type.slot_words(slot)
    told = slot in book_type.PAST_TOLD
    plan = Plan(subject={"kind": "slot", "id": slot, "name": words}, change={"type_id": (was, type_id)},
                called=words[0].upper() + words[1:])
    plan.extra = {"past_known": told, "offers": list(ANSWERS) if told else [ONLY_NEW]}
    if told:
        for held in _held_rows(conn, loan_interest.filed_ids(conn)):
            if held["type_id"] != type_id:
                plan.rows.append(Planned(held, {"type_id": type_id}, {}))
    plan.token = _stamp("slot", slot, was, type_id, [tuple(p.held) for p in plan.rows])
    return plan


def slot_asked(plan: Plan, type_shown: str) -> str:
    """The refusal a slot move answers with before it is answered, in words."""
    if not plan.extra["past_known"]:
        return (f"Nothing was saved yet: fin cannot tell which past rows it filed as {plan.subject['name']}, "
                f"so only new rows can go under {type_shown} (past: {ONLY_NEW}).")
    return (f"Nothing was saved yet: there {'is' if plan.count == 1 else 'are'} {_rows(plan.count)} fin filed "
            f"as {plan.subject['name']}. Say whether they move to {type_shown} too (past: {APPLY}) "
            f"or only new rows go there (past: {ONLY_NEW}).")


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


def _book_moves(planned: Planned) -> bool:
    """Whether Apply moves the row to another book (an empty book reads as
    the default book). A row Apply leaves outside spending and refunds is
    in no book: its book is emptied (label_source.fitted), not moved."""
    if not flow.carries_label(planned.apply.get("flow_type", planned.held["flow_type"])):
        return False
    return "book" in planned.apply and not label_source.same("book", planned.apply["book"] or book_type.DEFAULT_BOOK,
                                                             planned.held["book"])


def _money(conn: sqlite3.Connection, plan: Plan) -> tuple[list[dict], list[dict], dict]:
    """The money moved per book pair, in S$ at the saved month-end rates;
    what could not be valued; the months touched."""
    # A row whose book Apply leaves as it is moves no money: a merchant's
    # book change, or a row a taught wording moves to a merchant whose book
    # is its own (fin-surfaces 53).
    moving = [p for p in plan.rows if _book_moves(p)]
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


# A row whose book and type a write empties, as a preview says it after
# (label_source.fitted).
NO_LABEL = "no book or type"


def _row_words(merchant: str | None, book, type_id, with_book: bool, types: dict[int, str],
               cleared: bool = False) -> str:
    """A row's label as a preview of its merchant names it: its merchant and
    type, with its book where `with_book` ("no merchant" for a row with
    neither); its merchant and NO_LABEL where the write empties its label
    (`cleared`)."""
    if merchant is None and type_id is None:
        return "no merchant"
    if cleared:
        return f"{merchant or 'no merchant'} · {NO_LABEL}"
    parts = [merchant or "no merchant"]
    if with_book:
        parts.append(_word("book", book, types))
    parts.append(_word("type_id", type_id, types))
    return " · ".join(parts)


def _held_by(plan: Plan, service_id: int | None) -> str | None:
    """The merchant a row has now, as a taught wording's preview names it:
    none, the merchant taught, or another merchant."""
    if service_id is None:
        return None
    if service_id == plan.subject["id"]:
        return plan.moves_to
    return plan.names.get(service_id, "another merchant")


def _example(conn, plan: Plan, p: Planned, types: dict[int, str], names: dict[int, str] | None) -> dict:
    """One example row, now → after, as the preview's kind of change reads
    it: a merchant's own book or type (and a merge) by those fields; a rule's
    rows by merchant and type, with the book where Apply moves it; a row a
    taught wording takes from another merchant by merchant and type ("Air
    India · Transport → IndigoAir · Travel", fin-surfaces 53)."""
    held = p.held
    out = {"id": held["id"], "date": held["date"], "amount_minor": held["amount_minor"], "currency": held["currency"]}
    after = {k: p.apply.get(k, held[k]) for k in ("service_id", "book", "type_id")}
    # A row Apply moves out of spending: its book and type emptied, and the
    # book it had named before where it was not the default book (a phone
    # line stays short).
    cleared = label_source.clears(held, p.apply)
    with_book = cleared and held["book"] not in (None, book_type.DEFAULT_BOOK)
    if plan.moves_to is not None and (held["service_id"] != plan.subject["id"] or not plan.change):
        return {**out,
                "now": _row_words(_held_by(plan, held["service_id"]), held["book"],
                                  held["type_id"], with_book, types),
                "after": _row_words(plan.moves_to, after["book"], after["type_id"], False, types, cleared),
                "source": _source(held, "type_id") or _source(held, "book")}
    if names is None and not plan.change:              # no book or type changes: by merchant and type
        names = {plan.subject["id"]: plan.called} if plan.subject.get("id") is not None else {}
    if names is None:                                  # a merchant's own book or type, or a merge
        fields = list(plan.change)
        shown = {f: p.apply.get(f, held[f]) for f in fields}
        reached = [f for f in fields if f in p.apply] or fields
        return {**out, "now": _label_words(fields, {f: held[f] for f in fields}, types),
                "after": NO_LABEL if cleared else _label_words(fields, shown, types),
                "source": _source(held, reached[0])}
    moves = _book_moves(p)
    name = lambda service_id: names.get(service_id, "no merchant") if service_id is not None else None
    return {**out,
            "now": _row_words(name(held["service_id"]), held["book"], held["type_id"], moves or with_book, types),
            "after": _row_words(name(after["service_id"]), after["book"], after["type_id"], moves, types, cleared),
            "source": _source(held, "type_id") or _source(held, "book")}


def moves_flow(planned: Planned) -> bool:
    """Whether Apply changes the row's flow or its other side."""
    return "flow_type" in planned.apply or "other_side_id" in planned.apply


def _said_flow(names: dict[int, str], flow_name, other_side) -> str:
    """A row's flow and other side in words ("a movement · the loan")."""
    word = FLOW_WORDS.get(flow_name or "expense", flow_name)
    return f"{word} · {names.get(other_side, 'an account no longer in fin')}" if other_side else word


def _flow_lines(conn: sqlite3.Connection, entries: list[tuple[int, dict]]) -> list[dict]:
    """Flow lines in their one shape, whatever shape of held row each came
    from (a preview's, a label write's): each row's date, description,
    amount and currency read by its id, then the line's own words
    (`entries`: [(row id, {now, after} or {now, would, why})]), in the
    order given."""
    facts = {r["id"]: r for r in _held_rows(conn, [i for i, _ in entries])}
    return [{"id": i, "date": facts[i]["date"], "description": facts[i]["description"],
             "amount_minor": facts[i]["amount_minor"], "currency": facts[i]["currency"], **words}
            for i, words in entries]


def _account_names(conn: sqlite3.Connection) -> dict[int, str]:
    return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM accounts")}


def _type_names(conn: sqlite3.Connection) -> dict[int, str]:
    """Every type's name as a preview says it, spending types and income kinds."""
    return {r["id"]: book_type.display_name(r["name"], r["parent"]) for r in conn.execute(
        "SELECT t.id, t.name, p.name AS parent FROM types t LEFT JOIN types p ON p.id = t.parent_id")}


def _cleared(held, writes: dict, types: dict[int, str]) -> str | None:
    """The book and type a flow move empties, in words ("Household ·
    Transport"), or None: a write that moves a row's flow out of spending and
    refunds empties both (label_source.fitted), so a line that moves a flow
    says what the row stops being as well."""
    flow_after = writes.get("flow_type", held["flow_type"])
    if "flow_type" not in writes or flow.carries_label(flow_after) or flow_after == held["flow_type"]:
        return None
    if held["book"] is None and held["type_id"] is None:
        return None
    return _label_words(["book", "type_id"], {"book": held["book"], "type_id": held["type_id"]}, types)


def _moved(names: dict[int, str], held, writes: dict, types: dict[int, str] | None = None) -> dict:
    """A row's flow and other side now → after `writes`, in words; with
    `types`, the book and type the move empties (label_cleared, or None)."""
    after = {k: writes.get(k, held[k]) for k in ("flow_type", "other_side_id")}
    out = {"now": _said_flow(names, held["flow_type"], held["other_side_id"]),
           "after": _said_flow(names, after["flow_type"], after["other_side_id"])}
    if types is not None:
        out["label_cleared"] = _cleared(held, writes, types)
    return out


def _flows(conn: sqlite3.Connection, plan: Plan) -> list[dict]:
    """Every row whose flow or other side Apply changes, newest first, now →
    after in words ("spending" → "a movement · the loan"), and the book and
    type the move empties (label_cleared): all of them, not examples, so the
    operator sees each before anything applies (ADR-009; fin-redesign 05)."""
    moving = [p for p in plan.rows if moves_flow(p)]
    if not moving:
        return []
    names, types = _account_names(conn), _type_names(conn)
    return _flow_lines(conn, [(p.held["id"], _moved(names, p.held, p.apply, types)) for p in moving])


def moved_lines(conn: sqlite3.Connection, moved: list[tuple]) -> list[dict]:
    """Rows a write moved, as flows lists them, newest first: each `moved`
    entry is (the row as it was, held or {id, flow_type, other_side_id,
    book, type_id}, the flow and other side written). A label's answer names
    its row's flow change so (app._teach), and a plan's preview each row's
    (app._run_plan)."""
    if not moved:
        return []
    names, types = _account_names(conn), _type_names(conn)
    shown = _flow_lines(conn, [(held["id"], _moved(names, held, writes, types)) for held, writes in moved])
    return sorted(shown, key=lambda line: (line["date"] or "", line["id"]), reverse=True)


def flows_kept(conn: sqlite3.Connection, plan: Plan) -> list[dict]:
    """Every row whose flow Apply would change but keeps, a record of expect
    back resting on it: now, what it would have been, and why, in the words
    a hand label's refusal uses (flow_writes)."""
    return kept_lines(conn, plan.kept)


def kept_lines(conn: sqlite3.Connection, kept: list[Kept]) -> list[dict]:
    """Rows kept (flow_writes), as flows_kept names them: why in the
    refusal's words, and `because`, the same facts as the drawer ends its
    one sentence with (because())."""
    if not kept:
        return []
    names = _account_names(conn)
    entries = []
    for k in kept:
        moved = _moved(names, k.held, k.would)
        entries.append((k.held["id"], {"now": moved["now"], "would": moved["after"], "why": k.why,
                                       "because": because(k.why)}))
    return _flow_lines(conn, entries)


def because(why: str) -> str:
    """A kept row's refusal (flow_writes) as the clause a plain sentence ends
    with after "because": the refusal's own facts, said of "it", and its fix
    in brackets. "S$ 5 of this row is expected back; undo the mark first."
    gives "S$ 5 of it is expected back (undo that mark to move it)"; a
    refusal with no fix to split off is its facts alone."""
    said = why.strip().rstrip(".")
    fact, sep, fix = said.rpartition("; ")
    if not sep:
        fact, sep, fix = said.rpartition(". ")
    if not sep:
        fact, fix = said, ""
    fact = fact.replace("This row", "it", 1) if fact.startswith("This row") else fact.replace("this row", "it")
    first = fact.split(" ", 1)[0]
    if first[:1].isupper() and first[1:].islower():
        fact = fact[0].lower() + fact[1:]
    fix = fix.removesuffix(" first").replace("undo the mark", "undo that mark")
    if not fix:
        return fact
    return f"{fact} ({fix[0].lower()}{fix[1:]} to move it)"


def summary(conn: sqlite3.Connection, plan: Plan) -> dict:
    """The preview: what the drawer shows and chat is told. `flows` lists
    every row whose flow or other side Apply changes; `flows_kept` every row
    whose flow it would change but keeps, and why."""
    types = _names(conn)
    change = plan.words if plan.words is not None else [
        {"field": f, "from": _word(f, was, types), "to": _word(f, now, types)}
        for f, (was, now) in plan.change.items()]
    if plan.wording is not None:                       # a taught wording (fin-surfaces 53)
        change = [*change, {"field": "wording", "from": None, "to": plan.wording}]
    names = None
    if plan.subject["kind"] in ("rule", "drift"):
        names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM services")}
    out = {
        "subject": plan.subject,
        "change": change,
        "count": plan.count,
        "checked": sum(1 for p in plan.rows if p.checked),
        "chosen": plan.chosen,
        "money": None,
        "left_out": [],
        "months": None,
        "examples": [_example(conn, plan, p, types, names) for p in plan.rows[:EXAMPLES]],
        "others": list(plan.others),
        "flows": _flows(conn, plan),
        "flows_kept": flows_kept(conn, plan),
        **plan.extra,
        "token": plan.token,
    }
    if any(_book_moves(p) for p in plan.rows):
        out["money"], out["left_out"], out["months"] = _money(conn, plan)
    return out


def _rows(count: int) -> str:
    return f"{count} past row{'' if count == 1 else 's'}"


def asked(plan: Plan, answer_key: str = "past") -> str:
    """The refusal a change that reaches past rows answers with, in words."""
    if plan.doing:
        said = f"{plan.doing[0].lower()}{plan.doing[1:]} changes {_rows(plan.count)}"
    else:
        said = f"this changes {_rows(plan.count)} of {plan.called}"
    moving = sum(1 for p in plan.rows if moves_flow(p))
    if moving:
        said += f", {moving} of them in what {'it is' if moving == 1 else 'they are'} (their flow; listed in flows)"
    leaving = sum(1 for p in plan.rows if moves_flow(p) and label_source.clears(p.held, p.apply))
    if leaving:
        said += (f", {leaving} of them leaving spending, so {'its' if leaving == 1 else 'their'} book and type "
                 "are emptied (label_cleared in flows)")
    if plan.kept:
        said += (f"; {len(plan.kept)} row{' keeps its' if len(plan.kept) == 1 else 's keep their'} flow, "
                 "a record of expect back resting on it (listed in flows_kept)")
    return (f"Nothing was saved yet: {said}. "
            f"Say whether they change too ({answer_key}: {APPLY}) or only new rows take it "
            f"({answer_key}: {ONLY_NEW}).")


def stale(plan: Plan) -> str:
    """The refusal of an answer to a preview that no longer holds (case 20)."""
    return f"{plan.called} changed since this preview; look again"
