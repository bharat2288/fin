"""The book and type conversion, proved on a temporary database in the old shape.

Every figure, merchant and name here is invented. Each test builds its own
database from the frozen copy of the old schema beside this file.
"""

import sqlite3
from datetime import date
from pathlib import Path

import pytest

import book_type
import conversion
import convert_book_type
import retire_categories

DAY = date(2026, 3, 14)
OLD_SCHEMA = Path(__file__).parent / "schema_before_book_and_type.sql"

# Every row of the attachment's mapping table: today's category, then the book
# and type its rows take. None for the type means the row is left without one.
TYPED = [
    ("Admin > Bank Fees", "Household", "Bank & government fees"),
    ("Admin > Government", "Household", "Bank & government fees"),
    ("Admin > Insurance", "Household", "Insurance"),
    ("Admin > Tax", "Household", "Tax"),
    ("Dining", "Household", "Dining"),
    ("Dining > Quick Service", "Household", "Dining"),
    ("Dining > Coffee", "Household", "Dining"),
    ("Education", "Household", "Education"),
    ("Entertainment", "Household", "Entertainment"),
    ("Groceries", "Household", "Groceries"),
    ("Health & Beauty", "Household", "Health & Beauty"),
    ("Kids", "Household", "Kids"),
    ("Pet", "Household", "Pet"),
    ("Rent", "Household", "Rent"),
    ("Travel", "Household", "Travel"),
    ("Utilities", "Household", "Utilities"),
    ("Social Media", "Household", "Social Media"),
    ("Gifts & Donations", "Household", "Gifts & Donations"),
    ("Fitness", "Household", "Fitness"),
    ("Fitness > Gym & Classes", "Household", "Fitness"),
    ("Fitness > Golf", "Household", "Fitness > Golf"),
    ("Home", "Household", "Home"),
    ("Home > Maintenance", "Household", "Repairs & maintenance"),
    ("Medical", "Household", "Medical"),
    ("Medical > Hospital", "Household", "Medical"),
    ("Medical > Specialist", "Household", "Medical"),
    ("Shopping", "Household", "Shopping"),
    ("Shopping > Books", "Household", "Shopping"),
    ("Shopping > Online", "Household", "Shopping"),
    ("Shopping > Retail", "Household", "Shopping"),
    ("Subscriptions", "Household", "Subscriptions"),
    ("Subscriptions > AI Tools", "Household", "Software & AI tools"),
    ("Subscriptions > Cloud & Storage", "Household", "Software & AI tools"),
    ("Trading Tools", "Household", "Software & AI tools"),
    ("Transport", "Household", "Transport"),
    ("Transport > Rides", "Household", "Transport > Rides"),
    ("Transport > Public Transit", "Household", "Transport > Public Transit"),
    ("Transport > EV Charging", "Household", "Transport > EV Charging"),
    ("Transport > Fuel", "Household", "Transport > Fuel"),
    ("Kalesh > Software", "Kalesh", "Software & AI tools"),
    ("Kalesh > Payroll", "Kalesh", "People"),
    ("Kalesh > Accounting", "Kalesh", "Professional services"),
    ("Kalesh > Fees", "Kalesh", "Bank & government fees"),
    ("Moom > Klaviyo", "Moom", "Software & AI tools"),
    ("Moom > Canva", "Moom", "Software & AI tools"),
    ("Moom > Zendesk", "Moom", "Software & AI tools"),
    ("Moom > ManyChat", "Moom", "Software & AI tools"),
    ("Moom > Shopify", "Moom", "Software & AI tools"),
    ("Moom > Google Workspace", "Moom", "Software & AI tools"),
    ("Moom > Xero", "Moom", "Software & AI tools"),
    ("Moom > AI Software", "Moom", "Software & AI tools"),
    ("Moom > Google Ads", "Moom", "Advertising"),
    ("Moom > Facebook Ads", "Moom", "Advertising"),
    ("Moom > TikTok Ads", "Moom", "Advertising"),
    ("Moom > LinkedIn", "Moom", "Advertising"),
    ("Moom > Alibaba", "Moom", "Stock purchases"),
    ("Moom > Shopee (Moom)", "Moom", "Stock purchases"),
    ("Moom > Stripe/Payments", "Moom", "Payment fees"),
    ("Moom > Freelancers", "Moom", "People"),
    ("Moom > WeWork", "Moom", "Office"),
    ("Moom > Equipment", "Moom", "Equipment"),
]
# The table marks these for review: the book is known, the type is not.
MARKED_FOR_REVIEW = [
    ("Admin", "Household"),
    ("Other", "Household"),
    ("Personal", "Household"),
    ("Moom", "Moom"),
    # Not in the attachment's table: ruled on ticket 09 to map like bare Moom.
    ("Kalesh", "Kalesh"),
]
# The table says these are not spending types; they belong to the first axis.
NOT_SPENDING = [
    ("Kalesh > Refunds", "Kalesh"),
    ("Loan/EMI", "Household"),
    ("Credits", None),
    ("Credits > Salary", None),
    ("Credits > Interest", None),
    ("Payments", None),
    ("Payments > Credit Card Payments", None),
    ("Transfers", None),
    ("Transfers > Misc Transfer", None),
]
# Categories the table does not name.
UNKNOWN = ["Hobbies", "Transport > Ferries", "Business > Tools"]

ALL_PATHS = (
    [p for p, _, _ in TYPED]
    + [p for p, _ in MARKED_FOR_REVIEW]
    + [p for p, _ in NOT_SPENDING]
    + UNKNOWN
)


class OldDb:
    """A database in the old shape, with helpers that add invented rows."""

    def __init__(self, path: Path):
        self.path = path
        conn = sqlite3.connect(str(path))
        conn.executescript(OLD_SCHEMA.read_text())
        category_ids = self._insert_categories(conn)
        conn.executemany(
            "INSERT INTO accounts (id, name, short_name, type) VALUES (?, ?, ?, ?)",
            [(1, "Sample Card 0001", "Sample-0001", "credit_card"),
             (2, "Sample Bank 0002", "Sample-0002", "bank")],
        )
        conn.executemany(
            "INSERT INTO statements (id, account_id, statement_date) VALUES (?, ?, ?)",
            [(1, 1, "2026-01-31"), (2, 2, "2026-01-31")],
        )
        conn.commit()
        conn.close()
        self.categories: dict[str, int] = category_ids

    @staticmethod
    def _insert_categories(conn) -> dict[str, int]:
        """Every category the tests use, parents first, in one commit."""
        ids: dict[str, int] = {}
        for path_name in sorted(ALL_PATHS, key=lambda p: p.count(" > ")):
            parent, _, name = path_name.rpartition(" > ")
            if parent and parent not in ids:
                ids[parent] = conn.execute(
                    "INSERT INTO categories (name) VALUES (?)", (parent,)
                ).lastrowid
            ids[path_name] = conn.execute(
                "INSERT INTO categories (name, parent_id) VALUES (?, ?)",
                (name, ids[parent] if parent else None),
            ).lastrowid
        return ids

    def _run(self, sql: str, params=()) -> int:
        conn = sqlite3.connect(str(self.path))
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def category(self, path_name: str) -> int:
        """Create 'Parent > Child' (and the parent, if new); return the id."""
        if path_name in self.categories:
            return self.categories[path_name]
        parent_id = None
        name = path_name
        if " > " in path_name:
            parent, name = path_name.split(" > ", 1)
            parent_id = self.category(parent)
        cat_id = self._run(
            "INSERT INTO categories (name, parent_id) VALUES (?, ?)", (name, parent_id)
        )
        self.categories[path_name] = cat_id
        return cat_id

    def service(self, name: str, category: str | None) -> int:
        return self._run(
            "INSERT INTO services (name, category_id) VALUES (?, ?)",
            (name, self.categories[category] if category else None),
        )

    def rule(self, pattern: str, service_id: int, override: str | None = None) -> int:
        return self._run(
            "INSERT INTO merchant_rules (pattern, service_id, category_override_id)"
            " VALUES (?, ?, ?)",
            (pattern, service_id, self.categories[override] if override else None),
        )

    def subscription(self, category: str | None, service_id: int | None = None) -> int:
        return self._run(
            "INSERT INTO subscriptions (service_id, category_id, amount, frequency)"
            " VALUES (?, ?, 10.00, 'monthly')",
            (service_id, self.categories[category] if category else None),
        )

    def row(
        self,
        category: str | None,
        description: str = "SAMPLE MERCHANT",
        amount: float = 10.00,
        *,
        service_id: int | None = None,
        cat_source: str = "auto",
        statement_id: int = 1,
        flow: str | None = None,
    ) -> int:
        return self._run(
            "INSERT INTO transactions (statement_id, date, description, amount_sgd,"
            " category_id, service_id, cat_source, flow_type)"
            " VALUES (?, '2026-01-10', ?, ?, ?, ?, ?, ?)",
            (statement_id, description, amount,
             self.categories[category] if category else None, service_id, cat_source, flow),
        )

    def convert(self) -> dict:
        return conversion.run_step(self.path, convert_book_type.STEP, today=DAY)

    def query(self, sql: str, params=()) -> list[tuple]:
        conn = sqlite3.connect(str(self.path))
        try:
            return [tuple(r) for r in conn.execute(sql, params)]
        finally:
            conn.close()

    def label(self, table: str, row_id: int) -> tuple:
        """(book, type as 'Parent > Child') of one row of one of the four tables."""
        book, type_col = {
            "transactions": ("book", "type_id"),
            "services": ("book", "type_id"),
            "subscriptions": ("book", "type_id"),
            "merchant_rules": ("book_override", "type_override_id"),
        }[table]
        return self.query(
            f"""
            SELECT x.{book},
                   CASE WHEN p.name IS NULL THEN t.name ELSE p.name || ' > ' || t.name END
            FROM {table} x
            LEFT JOIN types t ON t.id = x.{type_col}
            LEFT JOIN types p ON p.id = t.parent_id
            WHERE x.id = ?
            """,
            (row_id,),
        )[0]

    def review(self) -> list[dict]:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            return convert_book_type.review_list(conn)
        finally:
            conn.close()

    def summary(self) -> dict:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            return convert_book_type.summary(conn)
        finally:
            conn.close()


@pytest.fixture
def old(tmp_path: Path) -> OldDb:
    return OldDb(tmp_path / "ledger.db")


OLD_COLUMNS = {
    "transactions": "id, statement_id, date, description, amount_sgd, amount_foreign,"
                    " currency_foreign, category_id, is_one_off, cat_source, flow_type,"
                    " flow_type_manual, notes, created_at",
    "services": "id, name, category_id, is_one_off, exclude_from_expense_views, notes",
    "merchant_rules": "id, pattern, service_id, category_override_id, match_type,"
                      " confidence, priority, min_amount, max_amount",
    "subscriptions": "id, service_id, category_id, amount, currency, frequency, periods,"
                     " account_id, last_paid, renewal_date, status, link, notes, match_pattern",
    "categories": "id, name, parent_id, is_personal",
}


def _old_columns(old: OldDb, where: str = "") -> dict:
    return {
        table: old.query(f"SELECT {cols} FROM {table} {where} ORDER BY id")
        for table, cols in OLD_COLUMNS.items()
    }


# --- the type list exists as data ----------------------------------------


def test_type_list_is_the_attachments_36_types_with_one_level_of_sub_type(old):
    old.convert()

    types = old.query(
        "SELECT t.name, p.name, t.default_one_off, t.covers FROM types t"
        " LEFT JOIN types p ON p.id = t.parent_id WHERE t.kind = 'spending'"
    )
    assert len(types) == 36
    by_name = {name: (parent, one_off, covers) for name, parent, one_off, covers in types}
    assert {n for n, (parent, _, _) in by_name.items() if parent} == {
        "Rides", "Public Transit", "EV Charging", "Fuel", "Golf",
    }
    assert by_name["Golf"][0] == "Fitness"
    assert by_name["Fuel"][0] == "Transport"
    # No sub-type has a sub-type of its own.
    parents = {parent for parent, _, _ in by_name.values() if parent}
    assert all(by_name[parent][0] is None for parent in parents)
    # Running is the default; the two one-off types are the attachment's.
    assert {n for n, (_, one_off, _) in by_name.items() if one_off} == {
        "Equipment", "Repairs & maintenance",
    }
    # Each carries its plain description.
    assert all(covers for _, _, covers in by_name.values())
    assert by_name["Dining"][2].startswith("restaurants, bars, fast food")
    assert old.query("SELECT not_for FROM types WHERE name = 'Dining'") == [("supermarkets",)]


def test_income_keeps_its_own_short_list_of_kinds(old):
    old.convert()

    kinds = old.query("SELECT name FROM types WHERE kind = 'income' ORDER BY id")
    assert [k[0] for k in kinds] == ["Salary", "Rental", "Interest", "Gift received", "Other"]


def test_books_are_declared_once_with_a_description_each():
    assert [name for name, _ in book_type.BOOKS] == ["Household", "Moom", "Kalesh"]
    assert all(description for _, description in book_type.BOOKS)


def test_a_new_database_has_the_type_list_and_the_new_columns(conn):
    # conn is the app's own temporary database, created by init_db.
    assert conn.execute("SELECT COUNT(*) FROM types WHERE kind = 'spending'").fetchone()[0] == 36
    conn.execute("SELECT book, type_id FROM transactions")
    conn.execute("SELECT book, type_id FROM services")
    conn.execute("SELECT book, type_id FROM subscriptions")
    conn.execute("SELECT book_override, type_override_id FROM merchant_rules")


def test_the_step_leaves_the_category_columns_beside_the_new_ones(old):
    # Expand only. The category columns go in the step after this one
    # (retire_categories.py), which has its own tests.
    old.convert()

    for table, category, added in (
        ("transactions", "category_id", {"book", "type_id"}),
        ("services", "category_id", {"book", "type_id"}),
        ("subscriptions", "category_id", {"book", "type_id"}),
        ("merchant_rules", "category_override_id", {"book_override", "type_override_id"}),
    ):
        present = {r[0] for r in old.query(f"SELECT name FROM pragma_table_info('{table}')")}
        assert added | {category} <= present, table
    assert old.query("SELECT COUNT(*) FROM categories")[0][0] == len(old.categories)


# --- the old database the old app had already given a type list ----------


def test_an_old_database_that_already_holds_the_type_list_converts_the_same(old):
    # The state the real database is in: the app as it stood between the two
    # halves of the split created and seeded the type list on start, and left
    # the four old tables without their new columns. The conversion starts
    # from there.
    typed = old.row("Dining", "SAMPLE CAFE", 12.30)
    marked = old.row("Other", "CORNER STALL", 4.20)
    unknown = old.row("Hobbies", "SAMPLE HOBBY SHOP", 8.00)
    conn = sqlite3.connect(str(old.path))
    conn.execute(book_type.TYPES_TABLE_SQL)
    book_type.seed_types(conn)
    conn.commit()
    conn.close()
    type_list = old.query("SELECT id, kind, name, parent_id FROM types ORDER BY id")
    assert len(type_list) == 36 + 5
    before = _old_columns(old)

    report = old.convert()

    assert report["status"] == "applied"
    assert report["after"] == report["before"]
    # The type list is the one already there: nothing added, nothing renumbered.
    assert old.query("SELECT id, kind, name, parent_id FROM types ORDER BY id") == type_list
    assert old.label("transactions", typed) == ("Household", "Dining")
    assert old.label("transactions", marked) == ("Household", None)
    assert old.label("transactions", unknown) == (None, None)
    assert [(r["id"], r["reason"]) for r in old.review()] == [
        (marked, "marked for review"),
        (unknown, "category not in the mapping table"),
    ]
    assert old.summary()["unplaced"] == 0
    assert _old_columns(old) == before
    assert old.convert()["status"] == "already-applied"


# --- P13 conservation: row count kept, every row mapped or listed --------


def test_conversion_keeps_row_count_and_account_totals(old):
    merchant = old.service("Sample Merchant", None)
    for i, path_name in enumerate(ALL_PATHS):
        old.row(path_name, amount=1.25 + i, statement_id=1 + i % 2, service_id=merchant)
    old.row(None, amount=7.77)

    report = old.convert()

    assert report["status"] == "applied"
    assert report["after"]["rows"]["transactions"] == len(ALL_PATHS) + 1
    assert report["after"] == report["before"]


@pytest.mark.parametrize("category, book, type_name", TYPED)
def test_each_category_the_table_names_maps_to_its_book_and_type(old, category, book, type_name):
    row_id = old.row(category)

    old.convert()

    assert old.label("transactions", row_id) == (book, type_name)
    assert old.review() == []


@pytest.mark.parametrize("category, book", MARKED_FOR_REVIEW)
def test_rows_the_table_marks_for_review_keep_no_type_and_are_listed(old, category, book):
    row_id = old.row(category, "CORNER STALL", 4.20)

    old.convert()

    assert old.label("transactions", row_id) == (book, None)
    assert old.review() == [{
        "id": row_id,
        "date": "2026-01-10",
        "description": "CORNER STALL",
        "amount_sgd": 4.20,
        "category": category,
        "reason": "marked for review",
    }]


@pytest.mark.parametrize("category, book", NOT_SPENDING)
def test_categories_that_are_not_spending_types_get_no_type_and_are_not_listed(old, category, book):
    row_id = old.row(category)

    old.convert()

    assert old.label("transactions", row_id) == (book, None)
    assert old.review() == []
    assert old.summary()["not_spending"] == 1


@pytest.mark.parametrize("category", UNKNOWN)
def test_a_category_the_table_does_not_name_is_listed_and_never_guessed(old, category):
    row_id = old.row(category)

    report = old.convert()

    assert report["status"] == "applied"
    assert old.label("transactions", row_id) == (None, None)
    listed = old.review()
    assert [(r["id"], r["category"], r["reason"]) for r in listed] == [
        (row_id, category, "category not in the mapping table")
    ]


def test_a_row_with_no_category_is_listed_for_review(old):
    row_id = old.row(None)

    old.convert()

    assert old.label("transactions", row_id) == (None, None)
    assert [(r["id"], r["category"], r["reason"]) for r in old.review()] == [
        (row_id, None, "no category")
    ]


@pytest.mark.parametrize("flow", ["income", "transfer", "payment"])
@pytest.mark.parametrize("category", [None, "Other", "Moom", "Hobbies"])
def test_the_review_list_holds_spending_and_refund_rows_only(old, category, flow):
    # A type is for spending. A row of another flow that the table leaves
    # without one is not waiting for a type.
    not_spending = old.row(category, "SAMPLE TRANSFER", flow=flow)
    spending = old.row(category, "CORNER STALL", flow="expense")
    refund = old.row(category, "CORNER STALL REBATE", -1.00, flow="refund")
    no_flow_yet = old.row(category, "CORNER STALL AGAIN")

    old.convert()

    assert [r["id"] for r in old.review()] == [spending, refund, no_flow_yet]
    summary = old.summary()
    assert (summary["review"], summary["not_spending"], summary["unplaced"]) == (3, 1, 0)
    # The book the table gives is written whatever the flow.
    assert old.label("transactions", not_spending) == old.label("transactions", spending)


def test_every_row_is_typed_or_not_spending_or_listed_and_none_is_left_over(old):
    ids = {path_name: old.row(path_name) for path_name in ALL_PATHS}
    no_category = old.row(None)

    old.convert()

    summary = old.summary()
    assert summary["rows"] == len(ALL_PATHS) + 1
    assert summary["typed"] == len(TYPED)
    assert summary["not_spending"] == len(NOT_SPENDING)
    assert summary["review"] == len(MARKED_FOR_REVIEW) + len(UNKNOWN) + 1
    assert summary["unplaced"] == 0
    assert summary["typed"] + summary["not_spending"] + summary["review"] == summary["rows"]
    expected_listed = (
        {ids[p] for p, _ in MARKED_FOR_REVIEW} | {ids[p] for p in UNKNOWN} | {no_category}
    )
    assert {r["id"] for r in old.review()} == expected_listed


def test_category_matching_ignores_case_and_outer_spaces(old):
    path_name = "dining > quick service "
    old.category(path_name)
    row_id = old.row(path_name)

    old.convert()

    assert old.label("transactions", row_id) == ("Household", "Dining")


# --- manual labels --------------------------------------------------------


def test_a_manual_label_is_remapped_by_the_same_table_and_stays_manual(old):
    manual = old.row("Fitness > Golf", cat_source="manual")
    auto = old.row("Fitness > Golf")

    old.convert()

    assert old.label("transactions", manual) == ("Household", "Fitness > Golf")
    assert old.label("transactions", manual) == old.label("transactions", auto)
    assert old.query("SELECT cat_source, category_id FROM transactions WHERE id = ?", (manual,)) == [
        ("manual", old.categories["Fitness > Golf"])
    ]


# --- the Amazon Web Services rows under Shopping -------------------------


def test_amazon_web_services_rows_under_shopping_go_to_kalesh_software(old):
    aws_service = old.service("Amazon Web Services", "Shopping > Online")
    by_merchant = old.row("Shopping > Online", "CLOUD BILL 0042", service_id=aws_service)
    by_text = old.row("Shopping > Online", "Amazon Web Services Sample City")
    by_short_name = old.row("Shopping", "AWS APAC SAMPLE")
    bookshop = old.row("Shopping > Online", "AMAZON SAMPLE MARKETPLACE")
    not_a_word = old.row("Shopping > Retail", "LAWSON SAMPLE STORE")

    old.convert()

    for row_id in (by_merchant, by_text, by_short_name):
        assert old.label("transactions", row_id) == ("Kalesh", "Software & AI tools")
    assert old.label("transactions", bookshop) == ("Household", "Shopping")
    assert old.label("transactions", not_a_word) == ("Household", "Shopping")
    # The merchant itself follows its rows.
    assert old.label("services", aws_service) == ("Kalesh", "Software & AI tools")
    assert old.summary()["amazon_web_services_rows"] == 3


def test_amazon_web_services_rows_outside_shopping_follow_the_table(old):
    row_id = old.row("Subscriptions > Cloud & Storage", "AWS APAC SAMPLE")

    old.convert()

    assert old.label("transactions", row_id) == ("Household", "Software & AI tools")


# --- merchant-named Moom categories become merchants ---------------------


def test_a_merchant_named_moom_category_becomes_the_merchant_of_its_bare_rows(old):
    bare = old.row("Moom > Klaviyo", "KLV SAMPLE CHARGE")
    other_service = old.service("Sample Reseller", "Moom > Klaviyo")
    already_named = old.row("Moom > Klaviyo", "RESELLER CHARGE", service_id=other_service)

    report = old.convert()

    [(service_id, name)] = old.query(
        "SELECT s.id, s.name FROM transactions t JOIN services s ON s.id = t.service_id"
        " WHERE t.id = ?", (bare,)
    )
    assert name == "Klaviyo"
    assert old.label("services", service_id) == ("Moom", "Software & AI tools")
    assert old.query("SELECT category_id FROM services WHERE id = ?", (service_id,)) == [
        (old.categories["Moom > Klaviyo"],)
    ]
    # A row that already names a merchant keeps it.
    assert old.query("SELECT service_id FROM transactions WHERE id = ?", (already_named,)) == [
        (other_service,)
    ]
    # The one new merchant is the only change to any row count.
    assert report["after"]["rows"]["services"] == report["before"]["rows"]["services"] + 1
    assert report["after"]["rows"]["transactions"] == report["before"]["rows"]["transactions"]


def test_an_existing_merchant_of_that_name_is_reused(old):
    existing = old.service("KLAVIYO", "Moom > Klaviyo")
    bare = old.row("Moom > Klaviyo")

    report = old.convert()

    assert old.query("SELECT service_id FROM transactions WHERE id = ?", (bare,)) == [(existing,)]
    assert report["after"]["rows"] == report["before"]["rows"]


def test_no_merchant_is_made_for_a_category_that_is_not_merchant_named(old):
    bare = old.row("Moom > Freelancers")
    also_bare = old.row("Dining")

    report = old.convert()

    assert old.query(
        "SELECT service_id FROM transactions WHERE id IN (?, ?)", (bare, also_bare)
    ) == [(None,), (None,)]
    assert report["after"]["rows"] == report["before"]["rows"]


# --- merchants, rules and subscriptions gain book and type ---------------


def test_merchants_rules_and_subscriptions_gain_book_and_type(old):
    cafe = old.service("Sample Cafe", "Dining > Coffee")
    unsorted = old.service("Sample Unsorted", "Other")
    blank = old.service("Sample Blank", None)
    plain_rule = old.rule("SAMPLE CAFE", cafe)
    override_rule = old.rule("SAMPLE CAFE BEANS", cafe, override="Kalesh > Software")
    with_category = old.subscription("Subscriptions > AI Tools", cafe)
    without = old.subscription(None, cafe)

    old.convert()

    assert old.label("services", cafe) == ("Household", "Dining")
    assert old.label("services", unsorted) == ("Household", None)
    assert old.label("services", blank) == (None, None)
    assert old.label("merchant_rules", plain_rule) == (None, None)
    assert old.label("merchant_rules", override_rule) == ("Kalesh", "Software & AI tools")
    assert old.label("subscriptions", with_category) == ("Household", "Software & AI tools")
    assert old.label("subscriptions", without) == (None, None)


# --- expand only: the category columns stay exactly as they were ---------


def test_the_category_columns_and_every_old_value_are_untouched(old):
    cafe = old.service("Sample Cafe", "Dining > Coffee")
    old.rule("SAMPLE CAFE", cafe, override="Shopping")
    old.subscription("Subscriptions", cafe)
    for path_name in ALL_PATHS:
        old.row(path_name, service_id=cafe, cat_source="manual")
    old.row(None)
    before = _old_columns(old)

    old.convert()

    assert _old_columns(old) == before


# --- P12 replay, for this step -------------------------------------------


def _dump(path: Path) -> str:
    conn = sqlite3.connect(str(path))
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def test_conversion_run_twice_changes_nothing(old):
    for path_name in ALL_PATHS:
        old.row(path_name)
    old.row(None)
    old.row("Shopping", "AWS APAC SAMPLE")
    old.convert()
    after_first = _dump(old.path)
    files = sorted(p.name for p in old.path.parent.iterdir())

    second = old.convert()

    assert second["status"] == "already-applied"
    assert second["backup"] is None
    assert _dump(old.path) == after_first
    assert sorted(p.name for p in old.path.parent.iterdir()) == files


def test_a_later_run_fills_only_rows_added_since_and_keeps_labels_already_set(old):
    # The old app writes categories only, so rows it adds after a first run
    # arrive unlabelled. A later run catches them up and overwrites nothing.
    first = old.row("Dining")
    old.convert()
    software = old.query("SELECT id FROM types WHERE name = 'Software & AI tools'")[0][0]
    old._run("UPDATE transactions SET book = 'Kalesh', type_id = ? WHERE id = ?", (software, first))
    late = old.row("Groceries")

    report = old.convert()

    assert report["status"] == "applied"
    assert old.label("transactions", late) == ("Household", "Groceries")
    assert old.label("transactions", first) == ("Kalesh", "Software & AI tools")
    assert old.convert()["status"] == "already-applied"


def test_conversion_runs_behind_its_backup_and_refuses_without_one(old):
    old.row("Dining")
    before = _dump(old.path)

    with pytest.raises(conversion.ConversionRefused, match="backup"):
        conversion.run_step(
            old.path, convert_book_type.STEP, today=DAY, backup=lambda source, target: None
        )
    assert _dump(old.path) == before

    report = old.convert()
    assert Path(report["backup"]).name == "ledger.db.pre-book-and-type-20260314.bak"
    assert _dump(Path(report["backup"])) == before


# --- after the categories are retired ---------------------------------------


def test_once_the_categories_are_retired_the_step_reads_as_applied(old):
    old.row("Dining")
    old.convert()
    conversion.run_step(old.path, retire_categories.STEP, today=DAY)
    files = sorted(p.name for p in old.path.parent.iterdir())

    report = old.convert()

    assert report["status"] == "already-applied"
    assert sorted(p.name for p in old.path.parent.iterdir()) == files


def test_the_command_says_so_on_a_database_with_no_categories_left(old, capsys):
    old.row("Dining")
    old.convert()
    conversion.run_step(old.path, retire_categories.STEP, today=DAY)

    assert convert_book_type.main([str(old.path)]) == 0

    out = capsys.readouterr().out
    assert "already-applied" in out
    assert "categories are retired" in out
