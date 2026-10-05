"""Database initialization and helpers for fin."""

import sqlite3
from pathlib import Path

import book_type

DB_PATH = Path(__file__).parent / "fin.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Initial merchant rules based on known data: (pattern, label, match_type).
# The label is the merchant's type, with book Household, unless SEED_LABELS
# says otherwise.
DEFAULT_MERCHANT_RULES = [
    # Groceries
    ("TANGLIN MARKET", "Groceries", "contains"),
    ("LITTLE FARMS", "Groceries", "contains"),
    ("CS FRESH", "Groceries", "contains"),
    ("FAIRPRICE", "Groceries", "contains"),
    ("COLD STORAGE", "Groceries", "contains"),
    ("COLES", "Groceries", "startswith"),
    ("ALDI", "Groceries", "startswith"),
    ("MARKS & SPENCER", "Groceries", "contains"),
    ("M & S", "Groceries", "startswith"),
    ("FOOD PANDA", "Groceries", "contains"),
    ("FP*FOOD PANDA", "Groceries", "contains"),
    ("REDMART", "Groceries", "contains"),
    ("SHENG SIONG", "Groceries", "contains"),
    ("GIANT", "Groceries", "startswith"),
    ("SCOOP WHOLEFOODS", "Groceries", "contains"),
    ("PARAGON MARKET", "Groceries", "contains"),
    # Dining
    ("DIN TAI FUNG", "Dining", "contains"),
    ("CULINA", "Dining", "contains"),
    ("SUBWAY", "Dining", "contains"),
    ("NET*SUBWAY", "Dining", "contains"),
    ("YA KUN", "Dining", "contains"),
    ("TOAST BOX", "Dining", "contains"),
    ("MCDONALD", "Dining", "contains"),
    ("STARBUCKS", "Dining", "contains"),
    ("CAFFE BEVIAMO", "Dining", "contains"),
    ("GOURMET SARAWAK", "Dining", "contains"),
    ("TORI-Q", "Dining", "contains"),
    ("NESPRESSO", "Dining", "contains"),
    ("BACHA COFFEE", "Dining", "contains"),
    ("KOPI", "Dining", "contains"),
    ("VIET TASTE", "Dining", "contains"),
    ("TWO MEN BAGEL", "Dining", "contains"),
    ("CHICHA SAN CHEN", "Dining", "contains"),
    ("OLD CHANG KEE", "Dining", "contains"),
    ("PROJECT ACAI", "Dining", "contains"),
    ("MR COCONUT", "Dining", "contains"),
    ("ALLPRESS ESPRESSO", "Dining", "contains"),
    ("IMPERIAL TREASURE", "Dining", "contains"),
    ("JOLLIBEAN", "Dining", "contains"),
    ("7-ELEVEN", "Dining", "contains"),
    ("CHEERS", "Dining", "startswith"),
    ("SUPERBIG", "Dining", "contains"),
    ("GRANDHYATT", "Dining", "contains"),
    ("HYDRATE", "Dining", "startswith"),
    ("COCOBELLA", "Dining", "contains"),
    ("ASSEMBLY GROUND", "Dining", "contains"),
    ("GELATIAMO", "Dining", "contains"),
    ("DEARBORN", "Dining", "contains"),
    ("FATTENED CALF", "Dining", "contains"),
    # Transport
    ("BUS/MRT", "Transport", "contains"),
    ("GRAB*", "Transport", "startswith"),
    ("GRAB ", "Transport", "startswith"),
    ("UBER", "Transport", "startswith"),
    ("COMFORT", "Transport", "startswith"),
    ("GOJEK", "Transport", "contains"),
    ("TESLA MOTORS", "Transport", "contains"),
    ("KIGO CHARGING", "Transport", "contains"),
    ("CHARGEPLUS", "Transport", "contains"),
    ("SP DIGITAL PL-EV", "Transport", "contains"),
    ("SHELL ", "Transport", "startswith"),
    ("SPC ", "Transport", "startswith"),
    ("PARKING.SG", "Transport", "contains"),
    ("NETS FLASHPAY", "Transport", "contains"),
    ("NETS AUTO TOP UP", "Transport", "contains"),
    # Shopping
    ("TAKASHIMAYA", "Shopping", "contains"),
    ("ZARA", "Shopping", "startswith"),
    ("SHOPEE", "Shopping", "contains"),
    ("LAZADA", "Shopping", "contains"),
    ("2C2*LAZADA", "Shopping", "contains"),
    ("AMAZON", "Shopping", "contains"),
    ("KINOKUNIYA", "Shopping", "contains"),
    ("WH SMITH", "Shopping", "contains"),
    ("WHS ", "Shopping", "startswith"),
    ("TARGET", "Shopping", "startswith"),
    ("H M HENNES", "Shopping", "contains"),
    ("LONGCHAMP", "Shopping", "contains"),
    ("ONITSUKA", "Shopping", "contains"),
    ("DAISO", "Shopping", "contains"),
    ("KINDLE SVCS", "Shopping", "contains"),
    # Health & Beauty
    ("GUARDIAN", "Health & Beauty", "startswith"),
    ("WATSONS", "Health & Beauty", "startswith"),
    ("AESOP", "Health & Beauty", "startswith"),
    ("M SPA", "Health & Beauty", "contains"),
    ("MECCA", "Health & Beauty", "startswith"),
    # Medical
    ("MOUNT ALVERNIA", "Medical", "contains"),
    ("SINGHEALTH", "Medical", "contains"),
    ("MOUNT E ORCHARD", "Medical", "contains"),
    ("KINDER CLINIC", "Medical", "contains"),
    ("ALPHA WOMEN", "Medical", "contains"),
    ("PATRICIA YUEN DERMATOL", "Medical", "contains"),
    ("TERRA MEDICAL", "Medical", "contains"),
    ("OSTEOPATHIC", "Medical", "contains"),
    # Entertainment
    ("NETFLIX", "Entertainment", "contains"),
    ("SPOTIFY", "Entertainment", "contains"),
    ("YOUTUBE", "Entertainment", "contains"),
    ("HBO", "Entertainment", "contains"),
    ("TESLA PREMIUM", "Entertainment", "contains"),
    # Utilities
    ("SINGTEL", "Utilities", "contains"),
    ("MYSINGTELAPP", "Utilities", "contains"),
    ("MYREPUBLIC", "Utilities", "contains"),
    ("SP GROUP", "Utilities", "contains"),
    ("SP GAS", "Utilities", "contains"),
    ("SP DIGITAL PL-UTILITIE", "Utilities", "contains"),
    ("1PASSWORD", "Utilities", "contains"),
    # Subscriptions
    ("ANTHROPIC", "Subscriptions", "contains"),
    ("CLAUDE.AI", "Subscriptions", "contains"),
    ("OPENAI", "Subscriptions", "contains"),
    ("CHATGPT", "Subscriptions", "contains"),
    ("NOTION LABS", "Subscriptions", "contains"),
    ("TRADINGVIEW", "Subscriptions", "contains"),
    ("REMNOTE", "Subscriptions", "contains"),
    ("MICROSOFT 365", "Subscriptions", "contains"),
    ("OURA", "Subscriptions", "contains"),
    ("GOOGLE PLAY", "Subscriptions", "contains"),
    ("TWITTER", "Subscriptions", "contains"),
    ("RUNPOD", "Subscriptions", "contains"),
    ("GOOGLE*GOOGLE ONE", "Subscriptions", "contains"),
    ("GOOGLE ONE", "Subscriptions", "contains"),
    ("JASPER.AI", "Subscriptions", "contains"),
    ("SPENDEE", "Subscriptions", "contains"),
    # Insurance
    ("MANULIFE", "Insurance", "contains"),
    ("AIA", "Insurance", "startswith"),
    ("PRUDENTIAL", "Insurance", "contains"),
    ("GREAT EASTERN", "Insurance", "contains"),
    ("SINGAPORE LIFE", "Insurance", "contains"),
    # Loan/EMI
    ("HP HPR", "Loan/EMI", "contains"),
    # Travel
    ("DUTY FREE", "Travel", "contains"),
    ("HEINEMANN", "Travel", "contains"),
    ("FLYSCOOT", "Travel", "contains"),
    ("AUSTRALIANETA", "Travel", "contains"),
    # Pet
    ("GOODWOOF", "Pet", "contains"),
    ("PET LOVERS CENTRE", "Pet", "contains"),
    # Fitness
    ("VIVE ACTIVE", "Fitness", "contains"),
    ("BFT", "Fitness", "startswith"),
    ("BARRYSBOOTCAMP", "Fitness", "contains"),
    ("BARRY'S", "Fitness", "contains"),
    ("SICC", "Fitness", "contains"),
    ("SINGAPORE ISLAND COUNTRY", "Fitness", "contains"),
    ("SINGAPORE ISLAND COUNT", "Fitness", "contains"),
    ("VIN GOLF", "Fitness", "contains"),
    ("BUKIT TIMAH GOLF", "Fitness", "contains"),
    ("ORCHID COUNTRY CLUB", "Fitness", "contains"),
    ("HIDDEN CASTLE GOLF", "Fitness", "contains"),
    ("UPLAY VENTURES", "Fitness", "contains"),
    ("PING ", "Fitness", "startswith"),
    # Kids
    ("MOTHERS WORK", "Kids", "contains"),
    ("MOTHER WORK", "Kids", "contains"),
    ("PRAMFOX", "Kids", "contains"),
    # Education
    ("BRILLIANT", "Education", "contains"),
    ("WELCH LABS", "Education", "contains"),
    # Business (Moom)
    ("GOOGLE*ADS", "Moom", "contains"),
    ("LOYALTYLION", "Moom", "contains"),
    ("ALIBABA.COM", "Moom", "contains"),
    ("XERO", "Moom", "contains"),
    ("MOOM", "Moom", "contains"),
    ("KLAVIYO", "Moom", "contains"),
    ("SHOPIFY", "Moom", "contains"),
    ("IWG MANAGEMENT", "Moom", "contains"),
    # Home
    ("DYSON", "Home", "contains"),
    ("HOME 360", "Home", "contains"),
    ("SP SONNO", "Home", "contains"),
    # Dining (additional)
    ("FATELICIOUS", "Dining", "contains"),
    ("365 JUICES", "Dining", "contains"),
    ("PEEPAL BY ZED", "Dining", "contains"),
    # Education (additional)
    ("PARCHMENT-UNIV", "Education", "contains"),
    # Other / catch-all for known-but-uncategorized
    ("H KONCEPTS", "Other", "contains"),
    ("VOOVOO", "Other", "contains"),
    ("NEO EMPIRE", "Other", "contains"),
    ("SINGAPORE246", "Other", "startswith"),
    ("SINGAPORE618", "Other", "startswith"),
    ("OTT MB", "Other", "startswith"),
]


# Seed labels that are not a Household type of the same name: (book, type).
# These are what the book and type conversion makes of the old categories of
# the same names: no type, left for the operator to place.
SEED_LABELS = {
    "Moom": ("Moom", None),
    "Other": ("Household", None),
    "Loan/EMI": ("Household", None),
}


class DatabaseNotConverted(Exception):
    """The database is in a shape from before book and type replaced the
    category tree. Nothing was changed."""


def _refuse_unconverted(conn: sqlite3.Connection) -> None:
    """Stop before touching a database that still carries categories, or
    whose accounts have no owner yet.

    Conversions of existing rows go through the conversion runner, behind a
    backup, and never happen here.
    """
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "transactions" not in tables:
        return  # a new database
    tx_cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)")}
    if "categories" in tables or "category_id" in tx_cols or "book" not in tx_cols:
        raise DatabaseNotConverted(
            "this database still carries the category tree. Convert it first, in this "
            "order: python convert_book_type.py <path to database>, then "
            "python retire_categories.py <path to database>"
        )
    rule_cols = {r[1] for r in conn.execute("PRAGMA table_info(merchant_rules)")}
    if "amount_sgd" in tx_cols or "min_amount" in rule_cols or "max_amount" in rule_cols:
        raise DatabaseNotConverted(
            "this database still carries float amounts. Convert it first, in this "
            "order: python convert_minor_units.py <path to database>, then "
            "python retire_float_amounts.py <path to database>"
        )
    if "owner" not in {r[1] for r in conn.execute("PRAGMA table_info(accounts)")}:
        raise DatabaseNotConverted(
            "this database is from before accounts had a kind and an owner. Convert it "
            "first: python convert_account_kinds.py <path to database>"
        )
    if "other_side_id" not in tx_cols:
        raise DatabaseNotConverted(
            "this database is from before a row could name its other side. Convert it "
            "first: python convert_movements.py <path to database>"
        )
    if "printed" not in {r[1] for r in conn.execute("PRAGMA table_info(statements)")}:
        raise DatabaseNotConverted(
            "this database is from before a row was filed under the statement it printed "
            "on. Convert it first: python convert_printed_statements.py <path to database>"
        )


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Initialize the database schema and seed data.

    Safe to call multiple times: seeding skips what is already there.
    Refuses a database that has not been through the book and type conversions.
    """
    conn = get_connection()
    try:
        _refuse_unconverted(conn)
    except DatabaseNotConverted:
        conn.close()
        raise

    # Run schema (CREATE IF NOT EXISTS is safe to re-run)
    schema_sql = SCHEMA_PATH.read_text()
    conn.executescript(schema_sql)

    # The type list, from its one declaration.
    book_type.seed_types(conn)
    conn.commit()
    type_ids = book_type.spending_type_ids(conn)

    # Seed merchant rules — skip if pattern already exists in any form
    # Rules require service_id (service-centric model)
    added = 0
    for pattern, label, match_type in DEFAULT_MERCHANT_RULES:
        book, type_name = SEED_LABELS.get(label, (book_type.DEFAULT_BOOK, label))
        existing = conn.execute(
            "SELECT id FROM merchant_rules WHERE pattern = ?", (pattern,)
        ).fetchone()
        if not existing:
            # Find or create a service for this pattern
            svc_name = pattern.title()  # e.g., "GRAB" → "Grab"
            svc = conn.execute(
                "SELECT id FROM services WHERE UPPER(name) = ?", (svc_name.upper(),)
            ).fetchone()
            if not svc:
                conn.execute(
                    "INSERT INTO services (name, book, type_id) VALUES (?, ?, ?)",
                    (svc_name, book, type_ids[type_name] if type_name else None),
                )
                svc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            else:
                svc_id = svc[0]
            conn.execute(
                "INSERT INTO merchant_rules (pattern, service_id, match_type, confidence) "
                "VALUES (?, ?, ?, 'auto')",
                (pattern, svc_id, match_type),
            )
            added += 1
    conn.commit()
    if added > 0:
        print(f"Added {added} new merchant rules")

    # Backfill rows that have no flow_type yet. This keeps dashboard expense
    # views from treating old transfer/payment rows as spend after restart.
    null_flow_count = conn.execute(
        "SELECT COUNT(*) FROM transactions WHERE flow_type IS NULL"
    ).fetchone()[0]
    if null_flow_count:
        from backfill_flow_type import backfill

        backfill(conn)

    total_types = conn.execute(
        "SELECT COUNT(*) FROM types WHERE kind = ?", (book_type.SPENDING,)
    ).fetchone()[0]
    total_rules = conn.execute("SELECT COUNT(*) FROM merchant_rules").fetchone()[0]
    print(f"Database ready: {total_types} types, {total_rules} merchant rules")

    conn.close()


# --- Rule engine cache ---
# In-memory cache of merchant rules, invalidated on any rule CRUD.
# Single-process Flask app, so module-level state is safe.
_rules_cache: list[dict] | None = None


def invalidate_rules_cache() -> None:
    """Clear the cached rules. Call after any merchant_rules INSERT/UPDATE/DELETE."""
    global _rules_cache
    _rules_cache = None


def _get_rules(conn: sqlite3.Connection) -> list[dict]:
    """Return cached rules list, loading from DB on first call or after invalidation."""
    global _rules_cache
    if _rules_cache is None:
        rows = conn.execute(
            "SELECT mr.pattern, mr.match_type, "
            "       mr.priority, mr.min_amount_minor, mr.max_amount_minor, "
            "       mr.service_id, mr.book_override, mr.type_override_id, "
            "       s.book, s.type_id, s.review_each_time "
            "FROM merchant_rules mr "
            "JOIN services s ON mr.service_id = s.id "
            "ORDER BY mr.priority DESC, LENGTH(mr.pattern) DESC"
        ).fetchall()
        _rules_cache = [dict(r) for r in rows]
    return _rules_cache


def rule_label(rule) -> tuple[str | None, int | None, str]:
    """The (book, type_id, provenance) a rule gives a row.

    A rule may override the merchant's type, its book, or both; whatever it
    does not override is the merchant's default.
    """
    overrides = rule["type_override_id"] is not None or rule["book_override"] is not None
    return (
        rule["book_override"] or rule["book"],
        rule["type_override_id"] or rule["type_id"],
        "rule_override" if overrides else "service_default",
    )


# What match_merchant returns when no rule knows the description.
NO_MATCH = {
    "book": None,
    "type_id": None,
    "service_id": None,
    "cat_source": None,
    "review_each_time": False,
}


def match_merchant(
    description: str,
    conn: sqlite3.Connection,
    amount_minor: int | None = None,
) -> dict:
    """Match a transaction description to a merchant using merchant rules.

    Rules are sorted by priority DESC, then pattern length DESC (most specific first).
    Amount-conditional rules (min_amount_minor / max_amount_minor) only match if
    the transaction's amount, in whole minor units, falls within the range.

    Returns {book, type_id, service_id, cat_source, review_each_time}; every
    value is empty when no rule matches. Book and type are the merchant's
    defaults unless the rule overrides them.
    """
    desc_upper = description.upper()
    rules = _get_rules(conn)

    for rule in rules:
        pattern = rule["pattern"].upper()
        match_type = rule["match_type"]

        # Check pattern match
        matched = False
        if match_type == "exact" and desc_upper == pattern:
            matched = True
        elif match_type == "startswith" and desc_upper.startswith(pattern):
            matched = True
        elif match_type == "contains" and pattern in desc_upper:
            matched = True

        if not matched:
            continue

        # Check amount conditions (if set on the rule)
        if amount_minor is not None:
            if rule["min_amount_minor"] is not None and amount_minor < rule["min_amount_minor"]:
                continue
            if rule["max_amount_minor"] is not None and amount_minor > rule["max_amount_minor"]:
                continue
        else:
            # No amount provided — skip amount-conditional rules
            if rule["min_amount_minor"] is not None or rule["max_amount_minor"] is not None:
                continue

        book, type_id, cat_source = rule_label(rule)
        return {
            "book": book,
            "type_id": type_id,
            "service_id": rule["service_id"],
            "cat_source": cat_source,
            "review_each_time": bool(rule["review_each_time"]),
        }

    return dict(NO_MATCH)


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
