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
    ("Insurance", None, 1),
    ("Loan/EMI", None, 1),
    ("Travel", None, 1),
    ("Pet", None, 1),
    ("Home", None, 1),
    ("Personal", None, 1),
    ("Education", None, 1),
    ("Fitness", None, 1),
    ("Kids", None, 1),
    ("Gifts & Donations", None, 1),
    ("Moom", None, 0),  # Business - not personal
    ("Other", None, 1),
    ("Rent", None, 1),
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

    # Migration: add is_anomaly column if missing
    tx_columns = [row[1] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()]
    if "is_anomaly" not in tx_columns:
        conn.execute("ALTER TABLE transactions ADD COLUMN is_anomaly INTEGER DEFAULT 0")
        conn.commit()

    # Migration: add parent_id column if missing (replace old text `parent`)
    cat_columns = [row[1] for row in conn.execute("PRAGMA table_info(categories)").fetchall()]
    if "parent_id" not in cat_columns:
        conn.execute("ALTER TABLE categories ADD COLUMN parent_id INTEGER REFERENCES categories(id)")
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

    # Seed merchant rules — INSERT OR IGNORE based on unique pattern
    added = 0
    for pattern, cat_name, match_type in DEFAULT_MERCHANT_RULES:
        cat_id = conn.execute(
            "SELECT id FROM categories WHERE name = ?", (cat_name,)
        ).fetchone()
        if cat_id:
            result = conn.execute(
                "INSERT OR IGNORE INTO merchant_rules (pattern, category_id, match_type, confidence) "
                "VALUES (?, ?, ?, 'auto')",
                (pattern, cat_id[0], match_type),
            )
            if result.rowcount > 0:
                added += 1
    conn.commit()
    if added > 0:
        print(f"Added {added} new merchant rules")

    total_cats = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    total_rules = conn.execute("SELECT COUNT(*) FROM merchant_rules").fetchone()[0]
    print(f"Database ready: {total_cats} categories, {total_rules} merchant rules")

    conn.close()


def categorize_transaction(description: str, conn: sqlite3.Connection) -> int | None:
    """Match a transaction description to a category using merchant rules.

    Returns category_id or None if no match found.
    """
    desc_upper = description.upper()

    rules = conn.execute(
        "SELECT mr.pattern, mr.match_type, mr.category_id "
        "FROM merchant_rules mr ORDER BY LENGTH(mr.pattern) DESC"
    ).fetchall()

    for rule in rules:
        pattern = rule["pattern"].upper()
        match_type = rule["match_type"]

        if match_type == "exact" and desc_upper == pattern:
            return rule["category_id"]
        elif match_type == "startswith" and desc_upper.startswith(pattern):
            return rule["category_id"]
        elif match_type == "contains" and pattern in desc_upper:
            return rule["category_id"]

    return None


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
