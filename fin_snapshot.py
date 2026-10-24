"""The baseline snapshot of a book, and its diff: what the migration's five
invariants are checked on (redesign brief, sections 9 and 10).

    python fin_snapshot.py snap <book.db> <out.json>
    python fin_snapshot.py diff <before.json> <after.json> [--added-ok]
    python fin_snapshot.py check-reference <snapshot.json> <baseline.json>

snap   reads the book and writes everything the invariants are checked on:
       every row (flow, book, type, other side, own and expected amount, and
       the rest the row list shows); spending by book and type for every
       month, and each month's stat cards; the month-end balance sheet for
       every month; the open expected-back and waiting items; the Queue's
       counts and the rows each rule labels; and the merchants, accounts,
       figures, types and change history the migration keeps.
       The book is never written: it is copied from a read-only open
       (conversion.read_copy) into a temporary file, and the app reads the
       copy, through the same GET routes the screens and chat tools call. So
       the snapshot is what this code says about this book: run it with the
       new code on the migrated book and the routes must say the same.
       init_db is not run (it seeds rules on a start, which would itself move
       invariant 5) and the network is refused throughout.
       The snapshot records the code it was taken with (git commit, "+changes"
       when the checkout has uncommitted changes) and whether Jev's key
       (OPENROUTER_API_KEY) was set: the Queue groups rows by Jev's stored
       guesses only with the key set, as the live app has it. To snapshot as
       the live app reads, set the key; any value will do, nothing is sent.
diff   compares two snapshots and says each invariant PASS or FAIL, naming the
       rows, month cells, balance-sheet lines and rules that moved. Exit 0
       when all pass, 1 otherwise. Two snapshots taken with and without Jev's
       key fail invariant 5: their Queues are not comparable. --added-ok
       takes rows only in <after> as an import's and leaves them out of
       invariant 1, so the existing rows an import changed (pairing, loan
       interest, refund links) are what it names.
check-reference
       compares a snapshot with the figures the chat read over MCP
       (fin-baseline-snapshot-2026-10-24.json: S$ figures, its own key
       names), one line per value, "same" or "different". Exit 0 when all
       are the same, 1 otherwise. A value the baseline holds as text is
       said as skipped.

Money is compared exactly: minor units as stored, and the routes' S$ figures
as the exact decimal of what they serve, never rounded.
"""

from __future__ import annotations

import calendar
import hashlib
import json
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

SNAPSHOT_VERSION = 1

# Fields that move with the clock, never with the book: left out everywhere.
VOLATILE = {"age_days", "made_at", "created_at", "imported_at", "fetched_at", "at", "old",
            "learnt_at", "asked_at", "refused_at", "changed_at"}

# Each check, and the snapshot sections it reads. The five are the brief's;
# the last is the rest its compatibility table keeps exactly.
INVARIANTS = {
    "1 rows": ("rows",),
    "2 spending by book and type": ("spending_by_month", "spending_cards"),
    "3 month-end balance sheet": ("balance_sheet",),
    "4 expected back and waiting": ("expect_back",),
    "5 Queue counts and rule rows": ("queue", "rules"),
    "kept: merchants, accounts, figures, types, history": ("merchants", "accounts", "figures", "types",
                                                            "type_slots", "history", "table_rows"),
}

# Change history and import batches grow with every write, the migration's own included.
UNCOUNTED_TABLES = ("change_", "batch_imports", "sqlite_")

ROW_FIELDS = {
    "date": "date", "account_id": "account_id", "description": "description",
    "amount_minor": "amount_minor", "currency": "currency",
    "amount_foreign": "amount_foreign", "currency_foreign": "currency_foreign", "notes": "notes",
    "flow": "flow_type", "flow_manual": "flow_type_manual", "other_side_id": "other_side_id",
    "book": "book", "type_id": "type_id", "type": "display_type",
    "own_minor": "own_minor", "expected_minor": "expected_minor",
    "placed_minor": "placed_minor",
    "book_source": "book_source", "type_source": "type_source",
    "service_id": "service_id", "is_one_off": "is_one_off", "not_checked": "not_checked",
}

STAT_CARDS = ("household", "moom", "kalesh", "household_out", "household_back", "moom_out", "moom_back",
              "kalesh_out", "kalesh_back", "loan_principal", "loan_interest", "loan_instalments", "cash_out",
              "untyped", "held_out_count", "held_out_total", "household_rows")


# --- taking the snapshot ----------------------------------------------------


def _exact(value: float) -> str:
    """A served S$ figure as the exact decimal of the float, never rounded:
    1973.0 and 1973 read the same."""
    d = Decimal(repr(value))
    return "0" if d == 0 else format(d.normalize(), "f")


def _clean(value):
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, float):
        return _exact(value)
    return value


def _months(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    out = []
    while f"{y:04d}-{m:02d}" <= end:
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def _code_version() -> str | None:
    """The commit this code is, "+changes" when the checkout differs from it."""
    here = str(Path(__file__).resolve().parent)
    try:
        head = subprocess.run(["git", "-C", here, "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
        changed = subprocess.run(["git", "-C", here, "status", "--porcelain", "--untracked-files=no"],
                                 capture_output=True, text=True).stdout.strip()
    except OSError:
        return None
    return (head + ("+changes" if changed else "")) or None


def _refuse(*_a, **_k):
    raise OSError("the snapshot makes no network calls")


def snap(book: Path | str) -> dict:
    """The snapshot of `book` (see the module's doc). Leaves fin pointed at
    the book it was pointed at before."""
    import app as fin_app
    import conversion
    import db
    import suggest

    book = Path(book).resolve()
    code = _code_version()
    tmp = Path(tempfile.mkdtemp(prefix="fin-snapshot-"))
    copy = tmp / "book.db"
    held = conversion.read_copy(book)
    try:
        into = sqlite3.connect(str(copy))
        held.backup(into)
        into.close()
    finally:
        held.close()

    was = (db.DB_PATH, socket.socket.connect, socket.create_connection, dict(fin_app.app.config))
    db.DB_PATH = copy
    db.invalidate_rules_cache()
    socket.socket.connect = _refuse
    socket.create_connection = _refuse
    fin_app.app.config.update(LOCAL_DEV=True, TESTING=True)
    conn = sqlite3.connect(str(copy))
    conn.row_factory = sqlite3.Row
    try:
        out = _take(fin_app.app.test_client(), conn)
        out["meta"] = {
            "snapshot_version": SNAPSHOT_VERSION,
            "book_sha256": hashlib.sha256(book.read_bytes()).hexdigest(),
            "code": code,
            "jev_key_set": suggest.key() is not None,
            **out["meta"],
        }
        return out
    finally:
        conn.close()
        db.DB_PATH, socket.socket.connect, socket.create_connection = was[:3]
        fin_app.app.config.clear()
        fin_app.app.config.update(was[3])
        db.invalidate_rules_cache()
        shutil.rmtree(tmp, ignore_errors=True)


def _take(c, conn: sqlite3.Connection) -> dict:
    """Every section, read through `c` (the app's test client, on the copy)
    and, for what no route lists, from `conn` on the same copy."""
    import app as fin_app
    import book_type
    import db
    import expect_back
    import suggest

    def get(path, **query):
        resp = c.get(path, query_string={k: (str(v).lower() if isinstance(v, bool) else v)
                                         for k, v in query.items() if v is not None})
        body = resp.get_json(silent=True)
        if resp.status_code != 200:
            return {"_status": resp.status_code, "_error": body.get("error") if isinstance(body, dict) else None}
        return body

    first, last = conn.execute("SELECT MIN(date), MAX(date) FROM transactions").fetchone()
    start, end = (first or "2025-01-01")[:7], (last or first or "2025-01-01")[:7]
    months = _months(start, end)
    tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    out: dict = {"meta": {
        "range": [start, end],
        "table_rows": {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables},
        "conversion_steps": [r[0] for r in conn.execute("SELECT step FROM conversion_steps ORDER BY step")],
        "last_change_entry": conn.execute("SELECT MAX(id) FROM change_entries").fetchone()[0],
    }}

    # 1: every row, as the row list (the `transactions` tool) serves it
    rows, page = {}, 1
    while True:
        body = get("/api/transactions", include_hidden=True, per_page=500, page=page, sort="date", sort_dir="asc")
        for t in body["transactions"]:
            rows[str(t["id"])] = {k: t.get(f) for k, f in ROW_FIELDS.items()}
        if page >= body["pages"]:
            break
        page += 1
    stored = conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    if stored != len(rows):
        raise SystemExit(f"the row list served {len(rows)} of {stored} rows: snapshot refused")
    out["rows"] = rows

    # 2: spending by book and type, every month, and each month's stat cards
    span = {"start": f"{start}-01", "end": f"{end}-{calendar.monthrange(*map(int, end.split('-')))[1]:02d}"}
    out["spending_by_month"] = {
        book or "_all": {
            "by_type": _clean(get("/api/dashboard/monthly", book=book, **span)),
            "by_sub_type": _clean(get("/api/dashboard/monthly", book=book, group_parent=False, **span)),
            "regular_only": _clean(get("/api/dashboard/monthly", book=book, exclude_one_off=True, **span)),
        }
        for book in [*book_type.BOOK_NAMES, None]
    }
    out["spending_cards"] = {}
    for month in months:
        cards = get("/api/dashboard/stat-cards", ref_month=month)
        out["spending_cards"][month] = _clean({k: cards.get(k) for k in STAT_CARDS})

    # 3: the month-end balance sheet, every month (a month it is not shown
    # for is held as refused, with the reason)
    out["balance_sheet"] = {}
    for month in months:
        sheet = get("/api/balance-sheet", month=month)
        if "_status" in sheet:
            out["balance_sheet"][month] = {"refused": sheet["_error"]}
            continue
        lines = {f"{s.get('key') or s.get('heading')}:{line.get('account_id', line.get('name'))}": _clean(line)
                 for s in sheet.get("sections", []) for line in s.get("lines", [])}
        check = sheet.get("month_check") or {}
        out["balance_sheet"][month] = {
            "net_worth_minor": sheet.get("net_worth_minor"),
            "lines": lines,
            "expect": _clean(sheet.get("expect")),
            "left_out": _clean(sheet.get("left_out")),
            "month_check": _clean({k: v for k, v in check.items()
                                   if k.endswith("_minor") or k in ("available", "in_check_count")}),
            "review_waiting": _clean(sheet.get("review_waiting")),
        }

    # 4: open expected-back and waiting items, each record's page, let-gos
    out["expect_back"] = {
        "open": {d: {str(i["id"]): _clean(i) for i in get("/api/expectations", direction=d).get("items", [])}
                 for d in ("back", "pay")},
        "pages": {str(eid): _clean(get(f"/api/expectations/{eid}"))
                  for (eid,) in conn.execute("SELECT id FROM expectation ORDER BY id")},
        "waiting": {
            str(r["id"]): {"row": r["payment_row_id"], "amount_minor": r["amount_minor"],
                           "person_id": r["person_id"], "person": r["person"], "note": r["note"],
                           "state": expect_back.set_off_state(conn, r["id"])}
            for r in conn.execute(
                "SELECT so.id, so.payment_row_id, so.amount_minor, so.person_id, so.note, a.name AS person"
                " FROM set_off so LEFT JOIN accounts a ON a.id = so.person_id"
                " WHERE so.expectation_id IS NULL ORDER BY so.id")
        },
        "let_go": [dict(r) for r in conn.execute(
            "SELECT expectation_id, amount_minor, became, book, type_id, to_whom_id, counts_on"
            " FROM let_go ORDER BY id")],
    }

    # 5: the Queue's counts (every card, past the cap the route cuts at), and
    # the rows each rule labels
    cards = get("/api/queue/cards")
    with db.get_db() as qc:
        every_card = fin_app._queue_card_groups(qc, suggest.key() is not None)
        rule_rows = fin_app._rows_by_rule(qc)
    review = get("/api/review")
    out["queue"] = _clean({
        "untyped_total": cards["untyped"]["total"],
        "cards_all": sorted([g["merchant"], g["side"], g["route"], sorted(r["id"] for r in g["rows"])]
                            for g in every_card),
        "cards_shown": sum(len(g["rows"]) for g in cards["untyped"]["groups"]),
        "check_each_total": cards["check_each"]["total"],
        "transfers_total": cards["transfers"]["total"],
        "review": {k: v for k, v in review.items() if k != "choices"},
        "guess_groups": sorted((g["merchant"], g["route"], len(g["tx_ids"]))
                               for g in get("/api/queue/guesses")["groups"]),
        "paybacks": len(get("/api/queue/paybacks").get("paybacks", [])),
        "refund_asks": get("/api/refund-links/asks"),
        "bills": get("/api/subscriptions"),
        "drift_count": get("/api/rules/drift").get("count"),
        "refused_statements": get("/api/statements/refused"),
        "coverage": get("/api/statements/coverage"),
    })
    out["rules"] = {
        str(r["id"]): {**{k: r.get(k) for k in ("pattern", "match_type", "confidence", "priority", "min_amount",
                                                 "max_amount", "service_id", "book_override", "type_override_id",
                                                 "book", "type_id", "rows_labelled")},
                       "rows": rule_rows.get(r["id"], [])}
        for r in get("/api/rules")
    }

    # The rest the compatibility table keeps exactly
    out["merchants"] = {str(s["id"]): _clean({k: s.get(k) for k in (
        "name", "book", "type_id", "is_one_off", "notes", "review_each_time", "exclude_from_expense_views")})
        for s in get("/api/services")}
    out["accounts"] = {str(a["id"]): _clean({k: a.get(k) for k in (
        "name", "type", "owner", "currency", "status", "last_four")}) for a in get("/api/accounts")}
    out["figures"] = {str(a["id"]): _clean({k: a.get(k) for k in (
        "account_id", "date", "amount_minor", "source", "note")}) for a in get("/api/anchors")}
    out["types"] = _clean(get("/api/types"))
    out["type_slots"] = _clean(get("/api/types/slots"))
    # Each closed change entry as the Changes screen shows it, by digest: an
    # entry the new code cannot show any more says so.
    out["history"] = []
    for (eid,) in conn.execute("SELECT id FROM change_entries WHERE open = 0 ORDER BY id"):
        shown = get(f"/api/history/{eid}")
        digest = hashlib.sha256(json.dumps(_clean(shown), sort_keys=True).encode()).hexdigest()[:16]
        out["history"].append([eid, f"unreadable {shown['_status']}" if "_status" in shown else digest])
    return out


# --- the diff ---------------------------------------------------------------


def _walk(a, b, path: str, out: list) -> None:
    """Every leaf that differs between `a` and `b`, as (path, was, now). A
    rule's row set is said as the rows it lost and gained."""
    if isinstance(a, list) and isinstance(b, list):
        if path.endswith(".rows") and all(isinstance(x, int) for x in a + b):
            if set(a) != set(b):
                out.append((path, {"lost": sorted(set(a) - set(b))}, {"gained": sorted(set(b) - set(a))}))
            return
        a, b = dict(enumerate(a)), dict(enumerate(b))
    if not (isinstance(a, dict) and isinstance(b, dict)):
        if a != b:
            out.append((path, a, b))
        return
    for k in sorted(set(a) | set(b), key=str):
        p = f"{path}.{k}" if path else str(k)
        if k not in a or k not in b:
            out.append((p, a.get(k), b.get(k)))
        else:
            _walk(a[k], b[k], p, out)


def diff(a: dict, b: dict, *, added_ok: bool = False) -> tuple[bool, dict[str, list]]:
    """Each check's differences between snapshots `a` and `b`, as
    {check: [(path, was, now)]}, and whether every check found none."""
    result = {}
    for name, sections in INVARIANTS.items():
        found: list = []
        for s in sections:
            if s == "rows" and added_ok:
                _walk(a["rows"], {k: v for k, v in b["rows"].items() if k in a["rows"]}, "rows", found)
            elif s == "table_rows":
                # Every table the before book has keeps its row count, so a
                # table no screen shows still counts; a table the migration
                # adds is expected. An import adds rows everywhere.
                if not added_ok:
                    was, now = a["meta"]["table_rows"], b["meta"]["table_rows"]
                    for t in sorted(was):
                        if not t.startswith(UNCOUNTED_TABLES) and was[t] != now.get(t):
                            found.append((f"meta.table_rows.{t}", was[t], now.get(t)))
            elif s == "history":
                # An entry the migration adds is expected; an earlier one must
                # show as it did.
                was, now = dict(map(tuple, a.get(s, []))), dict(map(tuple, b.get(s, [])))
                for eid in sorted(set(was) | set(now)):
                    if eid in was and was[eid] != now.get(eid):
                        found.append((f"history.{eid}", was[eid], now.get(eid)))
                    elif eid not in was and now[eid].startswith("unreadable"):
                        found.append((f"history.{eid}", None, now[eid]))
            else:
                _walk(a.get(s, {}), b.get(s, {}), s, found)
        if name.startswith("5") and a["meta"].get("jev_key_set") != b["meta"].get("jev_key_set"):
            found.insert(0, ("meta.jev_key_set", a["meta"].get("jev_key_set"), b["meta"].get("jev_key_set")))
        result[name] = found
    return not any(result.values()), result


def report(a: dict, b: dict, result: dict, *, added_ok: bool = False, shown: int = 40) -> list[str]:
    """The diff in words, each check PASS or FAIL with what moved."""
    lines = [f"before: code {a['meta'].get('code')}, Jev key {'set' if a['meta'].get('jev_key_set') else 'not set'}",
             f"after:  code {b['meta'].get('code')}, Jev key {'set' if b['meta'].get('jev_key_set') else 'not set'}"]
    for name, found in result.items():
        lines.append(f"{name}: " + ("PASS" if not found else f"FAIL ({len(found)} differences)"))
        for path, was, now in found[:shown]:
            lines.append(f"    {path}: {json.dumps(was, default=str)[:160]} -> {json.dumps(now, default=str)[:160]}")
        if len(found) > shown:
            lines.append(f"    ... {len(found) - shown} more")
    added = sorted(set(b["rows"]) - set(a["rows"]), key=int)
    removed = sorted(set(a["rows"]) - set(b["rows"]), key=int)
    changed = sorted({p.split(".")[1] for p, *_ in result["1 rows"]} - set(removed), key=int)
    if added_ok:
        lines.append(f"rows added (left out with --added-ok): {len(added)}"
                     + (f", ids {added[0]}..{added[-1]}" if added else ""))
    lines.append(f"rows removed: {len(removed)}" + (f" {removed[:30]}" if removed else ""))
    lines.append(f"existing rows changed: {len(changed)}" + (f" {changed[:30]}" if changed else ""))
    return lines


# --- the chat's reference ---------------------------------------------------


def _s(value) -> Decimal:
    """An S$ figure to the cent, from the baseline's floats or the snapshot's
    exact text."""
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _sd(minor) -> Decimal:
    return (Decimal(minor) / 100).quantize(Decimal("0.01"))


def check_reference(s: dict, baseline: dict) -> list[str]:
    """Each value the baseline holds against the snapshot, one line each:
    "same" or "different", then a count of each."""
    out: list[str] = []

    def say(label, want, got):
        shown = ["none" if v is None else v for v in (want, got)]
        out.append(f"{'same     ' if want == got else 'different'} {label}: baseline {shown[0]} | snapshot {shown[1]}")

    by_month = s["spending_by_month"]
    for key, months in baseline.get("spending_by_month", {}).items():
        book = key.split("_")[0]
        mine = {m: v for m, v in by_month.get(book, {}).get("by_type", {}).items()
                if isinstance(v, dict) and min(months) <= m <= max(months)}
        for month in sorted(set(months) | set(mine)):
            want, got = months.get(month, {}), mine.get(month, {})
            for t in sorted(set(want) | set(got)):
                say(f"spending_by_month.{key}.{month}.{t}",
                    _s(want[t]) if t in want else None, _s(got[t]) if t in got else None)
    for key, value in baseline.items():
        span = re.fullmatch(r"household_total_(\d{4}-\d{2})_to_(\d{4}-\d{2})", key)
        if span:
            total = sum((Decimal(v) for m, types in by_month["Household"]["by_type"].items()
                         if isinstance(types, dict) and span[1] <= m <= span[2] for v in types.values()),
                        Decimal(0))
            say(key, _s(value), _s(total))

    if "transfers_waiting_review" in baseline:
        review = s["queue"]["review"]
        for k, want in baseline["transfers_waiting_review"].items():
            got = review.get("waiting" if k == "count" else f"waiting_{k}")
            say(f"transfers_waiting_review.{k}", _s(want) if k.endswith("total") else want,
                _s(got) if k.endswith("total") and got is not None else got)

    if "expected_back_open" in baseline:
        held = s["expect_back"]["open"].get("back", {})
        listed = {str(e["id"]): e for e in baseline["expected_back_open"]}
        for eid in sorted(set(listed) | set(held), key=int):
            want, item = listed.get(eid), held.get(eid)
            if want is None or item is None:
                say(f"expected_back_open.{eid}", "listed" if want else None, "open" if item else None)
                continue
            say(f"expected_back_open.{eid}.row", want.get("row"), (item.get("row") or {}).get("id"))
            say(f"expected_back_open.{eid}.amount", _s(want["amount"]), _sd(item["amount_minor"]))
            say(f"expected_back_open.{eid}.open", _s(want["open"]), _sd(item["open_minor"]))
            if "note" in want:
                say(f"expected_back_open.{eid}.note", want["note"], item.get("note"))
            if "names" in want:
                say(f"expected_back_open.{eid}.names", want["names"], [n["name"] for n in item.get("names") or []])

    if "waiting_for_row" in baseline:
        held = {k: w for k, w in s["expect_back"]["waiting"].items() if w["state"] == "waiting"}
        listed = {str(w["set_off_id"]): w for w in baseline["waiting_for_row"]}
        for sid in sorted(set(listed) | set(held), key=int):
            want, item = listed.get(sid), held.get(sid)
            if want is None or item is None:
                say(f"waiting_for_row.{sid}", "listed" if want else None, "waiting" if item else None)
                continue
            say(f"waiting_for_row.{sid}.row", want["row"], item["row"])
            say(f"waiting_for_row.{sid}.amount", _s(want["amount"]), _sd(item["amount_minor"]))
            say(f"waiting_for_row.{sid}.person", want["person"], item["person"])

    for key, want in baseline.items():
        if not key.startswith("balance_sheet_"):
            continue
        sheet = s["balance_sheet"].get(key[len("balance_sheet_"):][:7])
        if sheet is None or "refused" in sheet:
            out.append(f"different {key}: the snapshot holds no balance sheet for that month"
                       + (f" ({sheet['refused']})" if sheet else ""))
            continue
        companies = {line.get("name"): line for line in sheet["lines"].values() if line.get("kind") == "company"}
        for field, value in want.items():
            if field == "net_worth":
                say(f"{key}.net_worth", _s(value), _sd(sheet["net_worth_minor"]))
            elif isinstance(value, dict):
                line = companies.get(field) or {}
                for part, figure in value.items():
                    minor = line.get("balance_minor") if part == "balance" else \
                        (line.get("made_of") or {}).get(f"{part}_minor")
                    say(f"{key}.{field}.{part}", _s(figure), _sd(minor) if minor is not None else None)
            else:
                out.append(f"skipped   {key}.{field}: baseline holds text ({value!r})")

    for name, want in baseline.get("loan_figures", {}).items():
        account = next((k for k, a in s["accounts"].items() if a.get("name") == name), None)
        latest = max((f for f in s["figures"].values() if str(f["account_id"]) == account),
                     key=lambda f: f["date"], default=None)
        say(f"loan_figures.{name}.amount", _s(want["amount"]), _sd(-latest["amount_minor"]) if latest else None)
        say(f"loan_figures.{name}.date", want["date"], latest["date"] if latest else None)

    if "rules" in baseline:
        rules, want = s["rules"], baseline["rules"]
        say("rules.count", want.get("count"), len(rules))
        say("rules.rows_labelled_total", want.get("rows_labelled_total"),
            sum(r["rows_labelled"] for r in rules.values()))
        listed = {str(r["id"]): r for r in want.get("list", [])}
        for rid in sorted(set(listed) | set(rules), key=int):
            fields = [k for k in ("pattern", "service_id", "rows_labelled") if k in listed.get(rid, {})] \
                or ["pattern", "service_id", "rows_labelled"]
            say(f"rules.{rid}", {k: listed[rid][k] for k in fields} if rid in listed else None,
                {k: rules[rid].get(k) for k in fields} if rid in rules else None)

    same = sum(line.startswith("same") for line in out)
    different = sum(line.startswith("different") for line in out)
    out.append(f"{same} same, {different} different")
    return out


# --- the command ------------------------------------------------------------


def main(argv: list[str]) -> int:
    cmd, rest = (argv[0], argv[1:]) if argv else (None, [])
    if cmd == "snap" and len(rest) == 2:
        s = snap(Path(rest[0]))
        Path(rest[1]).write_text(json.dumps(s, indent=1, sort_keys=True))
        print(f"snapshot: {len(s['rows'])} rows, {len(s['rules'])} rules, {len(s['balance_sheet'])} months,"
              f" code {s['meta']['code']}, Jev key {'set' if s['meta']['jev_key_set'] else 'not set'};"
              f" written to {rest[1]}")
        return 0
    if cmd == "diff" and len([x for x in rest if x != "--added-ok"]) == 2:
        a, b = (json.loads(Path(p).read_text()) for p in rest if p != "--added-ok")
        added_ok = "--added-ok" in rest
        ok, result = diff(a, b, added_ok=added_ok)
        print("\n".join(report(a, b, result, added_ok=added_ok)))
        print("ALL INVARIANTS PASS" if ok else "INVARIANTS BROKEN")
        return 0 if ok else 1
    if cmd == "check-reference" and len(rest) == 2:
        lines = check_reference(json.loads(Path(rest[0]).read_text()), json.loads(Path(rest[1]).read_text()))
        print("\n".join(lines))
        return 0 if not any(line.startswith("different") for line in lines) else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
