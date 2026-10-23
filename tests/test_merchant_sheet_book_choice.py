"""Walk-fixes 03: the merchant sheet's empty book choice says one true thing.

Both sheets (Add a merchant, Edit the merchant) offer the empty choice as "No book chosen",
with one line under the picker saying what it does in that sheet. The words are drawn by
merchantFields in static/app.js, lifted out of the real file and run under node; what each
line claims is pinned through the route that sheet saves to.

  Add (POST /api/services): with a type chosen and no book, the merchant takes the book the
  type proposes (_book_or_proposed); with no type it has none.
  Edit (PUT /api/services/<id>): no book is saved as no book, and its rows take none from it;
  a row with no book is read as Household (scope.book_expr) on every screen and in Household's
  spending. A row whose book a rule gives, or that you chose, keeps its own.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import book_type
import db
import money

STATIC = Path(__file__).resolve().parent.parent / "static"
APP_JS = (STATIC / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the sheet is drawn only in JavaScript")

ADD_LINE = "With a type chosen, fin gives it the type’s usual book when you add it."
EDIT_LINE = "With no book chosen, its rows are read as Household, unless a rule or you give one another book."


def _function(name):
    m = re.search(rf"^function {name}\(.*\}}$", APP_JS, re.M) or re.search(rf"^function {name}\(.*?^\}}$", APP_JS, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _node(code):
    out = subprocess.run([NODE, "-"], input=code, capture_output=True, text=True, timeout=30, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


PRELUDE = r"""
const esc = s => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
const plural = (n, w) => `${n} ${w}${n === 1 ? '' : 's'}`;
"""

REFS = {"books": [{"name": "Household"}, {"name": "Moom"}, {"name": "Kalesh"}],
        "types": [{"id": 3, "kind": "spending", "name": "Rides", "display_name": "Rides"}]}


def _book_field(s):
    """The Book picker's empty choice and the line under it, as the sheet draws them."""
    return _node("\n".join([PRELUDE, _function("typeOptions"), _function("bookOptions"),
                            _function("eachSettingHTML"), _function("merchantFields"), f"""
        const html = merchantFields({json.dumps(REFS)}, {json.dumps(s)});
        const field = html.match(/<label class="field"><span>Book<\\/span>[\\s\\S]*?<\\/label>/)[0];
        const blank = field.match(/<option value="">([^<]*)<\\/option>/)[1];
        const hint = (field.match(/<span class="hint">([^<]*)<\\/span>/) || [])[1] || null;
        console.log(JSON.stringify({{ blank, hint }}));
    """]))


# --- the API, as the test_book_and_type_switch helpers seed it -------------------


def _type_id(conn, name):
    return book_type.spending_type_ids(conn)[name]


def _merchant_shown(client, sid):
    return next(s for s in client.get("/api/services").get_json() if s["id"] == sid)


@pytest.fixture
def statement(conn) -> int:
    conn.execute("INSERT INTO accounts (name, short_name, type, last_four)"
                 " VALUES ('Sample Card 0001', 'Sample-0001', 'credit_card', '0001')")
    account_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO statements (account_id, statement_date, filename) VALUES (?, '2026-04-01', 'sample.csv')",
                 (account_id,))
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


# --- case 16: adding ---------------------------------------------------------------


@needs_node
def test_case_16_adding_the_empty_choice_reads_no_book_chosen_with_the_add_line():
    assert _book_field(None) == {"blank": "No book chosen", "hint": ADD_LINE}


def test_case_16_added_with_no_book_it_takes_its_types_usual_book_and_none_without_a_type(client, conn):
    def add(name, type_name=None):
        body = {"name": name, "book": None}
        if type_name:
            body["type_id"] = _type_id(conn, type_name)
        resp = client.post("/api/services", json=body)
        assert resp.status_code == 200, resp.get_json()
        return _merchant_shown(client, resp.get_json()["id"])["book"]

    # The type's usual book: Household for most types, Moom for advertising.
    assert add("Sample Diner", "Dining") == "Household"
    assert add("Sample Ads", "Advertising") == "Moom"
    # No type, no usual book: the merchant is added with none.
    assert add("Sample Stall") is None
    # A type with no usual book asks for one, and nothing is added.
    asked = client.post("/api/services", json={"name": "Sample Hosting", "book": None,
                                               "type_id": _type_id(conn, "Software & AI tools")})
    assert asked.status_code == 400
    assert "book is required" in asked.get_json()["error"]
    assert "Sample Hosting" not in {s["name"] for s in client.get("/api/services").get_json()}


# --- case 17: editing --------------------------------------------------------------


@needs_node
def test_case_17_editing_the_empty_choice_reads_no_book_chosen_with_the_edit_line():
    base = {"id": 7, "name": "Grab", "type_id": 3, "notes": None, "review_each_time": 0,
            "exclude_from_expense_views": 0}
    assert _book_field({**base, "book": None}) == {"blank": "No book chosen", "hint": EDIT_LINE}
    # The same words when the merchant has a book: the line is about the empty choice.
    assert _book_field({**base, "book": "Moom"}) == {"blank": "No book chosen", "hint": EDIT_LINE}


def test_case_17_saved_with_no_book_its_rows_are_read_as_household_but_a_rules_or_yours(client, conn, statement):
    dining = _type_id(conn, "Dining")
    conn.execute("INSERT INTO services (name, book, type_id) VALUES ('Sample Gym', 'Moom', ?)", (dining,))
    sid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute("INSERT INTO merchant_rules (pattern, service_id, match_type, confidence, book_override)"
                 " VALUES ('SAMPLE GYM KL', ?, 'contains', 'confirmed', 'Kalesh')", (sid,))

    def row(description, amount, book, book_source):
        conn.execute(
            "INSERT INTO transactions (statement_id, date, description, amount_minor, book, type_id, service_id,"
            " flow_type, book_source, type_source) VALUES (?, '2026-04-10', ?, ?, ?, ?, ?, 'expense', ?, 'merchant')",
            (statement, description, money.exact_minor(amount), book, dining, sid, book_source))
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    from_merchant = row("SAMPLE GYM", 30.0, "Moom", "merchant")
    from_rule = row("SAMPLE GYM KL", 20.0, "Kalesh", "rule")
    chosen = row("SAMPLE GYM 2", 10.0, "Moom", "chosen")
    conn.commit()
    db.invalidate_rules_cache()
    before = client.get("/api/dashboard/stat-cards?ref_month=2026-04").get_json()

    resp = client.put(f"/api/services/{sid}", json={"book": None, "past": "apply"})

    assert resp.status_code == 200, resp.get_json()
    # Saved as no book: the edit proposes none, whatever the type.
    assert _merchant_shown(client, sid)["book"] is None
    # Its rows are read as Household, but the rule's row and the one you chose keep theirs.
    shown = {t["id"]: t["book"] for t in client.get("/api/transactions?per_page=500").get_json()["transactions"]}
    assert (shown[from_merchant], shown[from_rule], shown[chosen]) == ("Household", "Kalesh", "Moom")
    # And they count in Household's spending.
    after = client.get("/api/dashboard/stat-cards?ref_month=2026-04").get_json()
    assert (before["household"], before["moom"], before["kalesh"]) == (0.0, 40.0, 20.0)
    assert (after["household"], after["moom"], after["kalesh"]) == (30.0, 10.0, 20.0)
