"""Database initialization and helpers for fin."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "fin.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"

# Default expense categories — (name, parent_name_or_None, is_personal)
# parent_name is resolved to parent_id at seed time
DEFAULT_CATEGORIES = [
    ("Groceries", None, 1),
    ("Dining", None, 1),
    ("Transport", None, 1),
    ("Shopping", None, 1),
    ("Health & Beauty", None, 1),
    ("Medical", None, 1),
    ("Entertainment", None, 1),
    ("Utilities", None, 1),
    ("Subscriptions", None, 1),
    ("Loan/EMI", None, 1),
    ("Travel", None, 1),
    ("Pet", None, 1),
    ("Home", None, 1),
    ("Personal", None, 1),
    ("Credits", None, 1),
    ("Transfers", None, 1),
    ("Education", None, 1),
    ("Fitness", None, 1),
    ("Kids", None, 1),
    ("Gifts & Donations", None, 1),
    ("Moom", None, 0),  # Business - Moom
    ("Kalesh", None, 0),  # Business - Kalesh
    ("Other", None, 1),
    ("Rent", None, 1),
    ("Admin", None, 1),
    # Subcategories
    ("Salary", "Credits", 1),
    ("Interest", "Credits", 1),
    ("Misc Incoming", "Credits", 1),
    ("Misc Transfer", "Transfers", 1),
    ("Accounting", "Kalesh", 0),
    ("Fees", "Kalesh", 0),
    ("Tax", "Admin", 1),
    ("Insurance", "Admin", 1),
    ("Bank Fees", "Admin", 1),
    ("Government", "Admin", 1),
]

# Initial merchant → category rules based on known data
# (pattern, category_name, match_type)
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


def get_connection() -> sqlite3.Connection:
    """Get a database connection with row factory."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Initialize the database schema and seed data.

    Safe to call multiple times — uses INSERT OR IGNORE for idempotent seeding.
    Adds new categories and merchant rules without duplicating existing ones.
    """
    conn = get_connection()

    # Run schema (CREATE IF NOT EXISTS is safe to re-run)
    schema_sql = SCHEMA_PATH.read_text()
    conn.executescript(schema_sql)

    # Lightweight migrations for additive columns on long-lived local DBs.
    service_cols = {row["name"] for row in conn.execute("PRAGMA table_info(services)").fetchall()}
    if "exclude_from_expense_views" not in service_cols:
        conn.execute(
            "ALTER TABLE services ADD COLUMN exclude_from_expense_views INTEGER DEFAULT 0"
        )
        conn.commit()

    rule_cols = {row["name"] for row in conn.execute("PRAGMA table_info(merchant_rules)").fetchall()}
    if "category_override_id" not in rule_cols:
        conn.execute(
            "ALTER TABLE merchant_rules ADD COLUMN category_override_id INTEGER"
        )
        conn.commit()

    tx_cols = {row["name"] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()}
    if "service_id" not in tx_cols:
        conn.execute(
            "ALTER TABLE transactions ADD COLUMN service_id INTEGER"
        )
        conn.commit()
    if "flow_type" not in tx_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN flow_type TEXT")
        conn.commit()
    if "flow_type_manual" not in tx_cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN flow_type_manual INTEGER DEFAULT 0")
        conn.commit()
    # Drop legacy flags once flow_type exists (ADR v2 grep gate complete)
    if "is_payment" in tx_cols:
        conn.execute("ALTER TABLE transactions DROP COLUMN is_payment")
        conn.commit()
    if "is_transfer" in tx_cols:
        conn.execute("ALTER TABLE transactions DROP COLUMN is_transfer")
        conn.commit()

    # Seed categories — INSERT OR IGNORE so new categories get added
    # First pass: insert all top-level (parent=None)
    for name, parent_name, is_personal in DEFAULT_CATEGORIES:
        if parent_name is None:
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, parent_id, is_personal) VALUES (?, NULL, ?)",
                (name, is_personal),
            )
    conn.commit()
    # Second pass: insert subcategories (parent_name != None)
    for name, parent_name, is_personal in DEFAULT_CATEGORIES:
        if parent_name is not None:
            parent_row = conn.execute(
                "SELECT id FROM categories WHERE name = ?", (parent_name,)
            ).fetchone()
            parent_id = parent_row[0] if parent_row else None
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, parent_id, is_personal) VALUES (?, ?, ?)",
                (name, parent_id, is_personal),
            )
    conn.commit()

    # Seed merchant rules — skip if pattern already exists in any form
    # Rules now require service_id (service-centric model)
    added = 0
    for pattern, cat_name, match_type in DEFAULT_MERCHANT_RULES:
        cat_row = conn.execute(
            "SELECT id FROM categories WHERE name = ?", (cat_name,)
        ).fetchone()
        if cat_row:
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
                    conn.execute("INSERT INTO services (name, category_id) VALUES (?, ?)",
                                 (svc_name, cat_row[0]))
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

    total_cats = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    total_rules = conn.execute("SELECT COUNT(*) FROM merchant_rules").fetchone()[0]
    print(f"Database ready: {total_cats} categories, {total_rules} merchant rules")

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
            "       mr.priority, mr.min_amount, mr.max_amount, "
            "       mr.service_id, mr.category_override_id, s.category_id "
            "FROM merchant_rules mr "
            "JOIN services s ON mr.service_id = s.id "
            "ORDER BY mr.priority DESC, LENGTH(mr.pattern) DESC"
        ).fetchall()
        _rules_cache = [dict(r) for r in rows]
    return _rules_cache


def categorize_transaction(
    description: str,
    conn: sqlite3.Connection,
    amount: float | None = None,
) -> tuple[int | None, int | None, str | None]:
    """Match a transaction description to a category using merchant rules.

    Rules are sorted by priority DESC, then pattern length DESC (most specific first).
    Amount-conditional rules (min_amount / max_amount) only match if the transaction
    amount falls within the specified range.

    Returns (category_id, service_id, cat_source) tuple. Either or both may be None.
    Category is resolved from the service (service.category_id).
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
        if amount is not None:
            if rule["min_amount"] is not None and amount < rule["min_amount"]:
                continue
            if rule["max_amount"] is not None and amount > rule["max_amount"]:
                continue
        else:
            # No amount provided — skip amount-conditional rules
            if rule["min_amount"] is not None or rule["max_amount"] is not None:
                continue

        # Category derived from service (single source of truth)
        service_id = rule["service_id"]
        category_id = rule["category_override_id"] or rule["category_id"]
        cat_source = "rule_override" if rule["category_override_id"] else "service_default"
        return (category_id, service_id, cat_source)

    return (None, None, None)


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
