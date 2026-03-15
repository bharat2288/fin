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
    ("Education", None, 1),
    ("Fitness", None, 1),
    ("Kids", None, 1),
    ("Gifts & Donations", None, 1),
    ("Moom", None, 0),  # Business - not personal
    ("Other", None, 1),
    ("Rent", None, 1),
    ("Admin", None, 1),
    # Subcategories
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

    # Migration: rename is_anomaly → is_one_off on transactions
    tx_columns = [row[1] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()]
    if "is_anomaly" in tx_columns and "is_one_off" not in tx_columns:
        conn.execute("ALTER TABLE transactions RENAME COLUMN is_anomaly TO is_one_off")
        conn.commit()
        print("Renamed transactions.is_anomaly -> is_one_off")
    elif "is_one_off" not in tx_columns and "is_anomaly" not in tx_columns:
        conn.execute("ALTER TABLE transactions ADD COLUMN is_one_off INTEGER DEFAULT 0")
        conn.commit()

    # Migration: add parent_id column if missing (replace old text `parent`)
    cat_columns = [row[1] for row in conn.execute("PRAGMA table_info(categories)").fetchall()]
    if "parent_id" not in cat_columns:
        conn.execute("ALTER TABLE categories ADD COLUMN parent_id INTEGER REFERENCES categories(id)")
        conn.commit()

    # Migration: add priority, min_amount, max_amount to merchant_rules
    rule_columns = [row[1] for row in conn.execute("PRAGMA table_info(merchant_rules)").fetchall()]
    if "priority" not in rule_columns:
        conn.execute("ALTER TABLE merchant_rules ADD COLUMN priority INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE merchant_rules ADD COLUMN min_amount REAL")
        conn.execute("ALTER TABLE merchant_rules ADD COLUMN max_amount REAL")
        conn.commit()

    # Migration: add account_id FK to subscriptions (replaces card text)
    sub_columns = [row[1] for row in conn.execute("PRAGMA table_info(subscriptions)").fetchall()]
    if "account_id" not in sub_columns:
        conn.execute("ALTER TABLE subscriptions ADD COLUMN account_id INTEGER REFERENCES accounts(id)")
        # Populate from existing card text → account short_name match
        conn.execute("""
            UPDATE subscriptions SET account_id = (
                SELECT a.id FROM accounts a WHERE a.short_name = subscriptions.card
            ) WHERE card IS NOT NULL AND card != ''
        """)
        conn.commit()
        migrated = conn.execute(
            "SELECT COUNT(*) FROM subscriptions WHERE account_id IS NOT NULL"
        ).fetchone()[0]
        print(f"Migrated {migrated} subscription card values to account_id FK")

    # Migration: add match_pattern to subscriptions
    sub_columns = [row[1] for row in conn.execute("PRAGMA table_info(subscriptions)").fetchall()]
    if "match_pattern" not in sub_columns:
        conn.execute("ALTER TABLE subscriptions ADD COLUMN match_pattern TEXT")
        # Pre-populate from service name (uppercased)
        conn.execute("UPDATE subscriptions SET match_pattern = UPPER(service) WHERE match_pattern IS NULL")
        conn.commit()

    # Migration: remove UNIQUE constraint on pattern (allow duplicate patterns
    # with different amount ranges / priorities for dual-purpose merchants)
    # Check if pattern column still has UNIQUE constraint
    index_info = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='merchant_rules'"
    ).fetchall()
    create_sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='merchant_rules'"
    ).fetchone()
    if create_sql and "UNIQUE" in (create_sql[0] or ""):
        # Recreate table without UNIQUE on pattern
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS merchant_rules_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT NOT NULL,
                category_id INTEGER NOT NULL,
                match_type TEXT DEFAULT 'contains',
                confidence TEXT DEFAULT 'confirmed',
                priority INTEGER DEFAULT 0,
                min_amount REAL,
                max_amount REAL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (category_id) REFERENCES categories(id)
            );
            INSERT INTO merchant_rules_new (id, pattern, category_id, match_type, confidence, priority, min_amount, max_amount, created_at)
                SELECT id, pattern, category_id, match_type, confidence,
                       COALESCE(priority, 0), min_amount, max_amount, created_at
                FROM merchant_rules;
            DROP TABLE merchant_rules;
            ALTER TABLE merchant_rules_new RENAME TO merchant_rules;
            CREATE INDEX IF NOT EXISTS idx_merchant_rules_pattern ON merchant_rules(pattern);
        """)

    # Migration: add service_id FK to merchant_rules, subscriptions, transactions
    rule_columns = [row[1] for row in conn.execute("PRAGMA table_info(merchant_rules)").fetchall()]
    sub_columns = [row[1] for row in conn.execute("PRAGMA table_info(subscriptions)").fetchall()]
    tx_columns = [row[1] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()]

    if "service_id" not in rule_columns:
        conn.execute("ALTER TABLE merchant_rules ADD COLUMN service_id INTEGER REFERENCES services(id)")
        conn.commit()
    if "service_id" not in sub_columns:
        conn.execute("ALTER TABLE subscriptions ADD COLUMN service_id INTEGER REFERENCES services(id)")
        conn.commit()
    if "service_id" not in tx_columns:
        conn.execute("ALTER TABLE transactions ADD COLUMN service_id INTEGER REFERENCES services(id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_service ON transactions(service_id)")
        conn.commit()

    # Migration: auto-create services from subscriptions + merchant rules
    svc_count = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
    if svc_count == 0:
        # Phase 1: create services from existing subscriptions
        subs = conn.execute(
            "SELECT id, service, category_id, match_pattern FROM subscriptions"
        ).fetchall()
        for sub in subs:
            svc_name = sub["service"]
            try:
                conn.execute(
                    "INSERT INTO services (name, category_id) VALUES (?, ?)",
                    (svc_name, sub["category_id"]),
                )
                svc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute(
                    "UPDATE subscriptions SET service_id = ? WHERE id = ?",
                    (svc_id, sub["id"]),
                )
                # Link matching merchant rules to this service via match_pattern
                pat = (sub["match_pattern"] or "").strip()
                if pat:
                    conn.execute(
                        "UPDATE merchant_rules SET service_id = ? WHERE UPPER(pattern) = ? AND service_id IS NULL",
                        (svc_id, pat.upper()),
                    )
            except sqlite3.IntegrityError:
                # Duplicate name — find existing and link
                existing = conn.execute(
                    "SELECT id FROM services WHERE name = ?", (svc_name,)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE subscriptions SET service_id = ? WHERE id = ?",
                        (existing["id"], sub["id"]),
                    )
        conn.commit()

        # Phase 2: create services from unlinked merchant rules
        unlinked_rules = conn.execute(
            "SELECT id, pattern, category_id FROM merchant_rules WHERE service_id IS NULL"
        ).fetchall()
        for rule in unlinked_rules:
            svc_name = rule["pattern"].title()
            existing = conn.execute(
                "SELECT id FROM services WHERE name = ?", (svc_name,)
            ).fetchone()
            if existing:
                conn.execute(
                    "UPDATE merchant_rules SET service_id = ? WHERE id = ?",
                    (existing["id"], rule["id"]),
                )
            else:
                try:
                    conn.execute(
                        "INSERT INTO services (name, category_id) VALUES (?, ?)",
                        (svc_name, rule["category_id"]),
                    )
                    svc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                    conn.execute(
                        "UPDATE merchant_rules SET service_id = ? WHERE id = ?",
                        (svc_id, rule["id"]),
                    )
                except sqlite3.IntegrityError:
                    pass  # skip if name collision
        conn.commit()

        # Phase 3: backfill transactions.service_id from linked merchant rules
        conn.execute("""
            UPDATE transactions SET service_id = (
                SELECT mr.service_id FROM merchant_rules mr
                WHERE mr.service_id IS NOT NULL
                  AND (
                    (mr.match_type = 'contains' AND UPPER(transactions.description) LIKE '%' || UPPER(mr.pattern) || '%')
                    OR (mr.match_type = 'startswith' AND UPPER(transactions.description) LIKE UPPER(mr.pattern) || '%')
                    OR (mr.match_type = 'exact' AND UPPER(transactions.description) = UPPER(mr.pattern))
                  )
                ORDER BY mr.priority DESC, LENGTH(mr.pattern) DESC
                LIMIT 1
            ) WHERE service_id IS NULL
        """)
        conn.commit()

        total_svcs = conn.execute("SELECT COUNT(*) FROM services").fetchone()[0]
        linked_subs = conn.execute("SELECT COUNT(*) FROM subscriptions WHERE service_id IS NOT NULL").fetchone()[0]
        linked_rules = conn.execute("SELECT COUNT(*) FROM merchant_rules WHERE service_id IS NOT NULL").fetchone()[0]
        linked_txs = conn.execute("SELECT COUNT(*) FROM transactions WHERE service_id IS NOT NULL").fetchone()[0]
        print(f"Services migration: {total_svcs} services created, "
              f"{linked_subs} subs linked, {linked_rules} rules linked, {linked_txs} txs linked")

    # Migration: add is_one_off to services (anomaly flag)
    svc_columns = [row[1] for row in conn.execute("PRAGMA table_info(services)").fetchall()]
    if "is_one_off" not in svc_columns:
        conn.execute("ALTER TABLE services ADD COLUMN is_one_off INTEGER DEFAULT 0")
        conn.commit()
        print("Added is_one_off column to services")

    # Migration: add cat_source to transactions (auto/manual)
    tx_columns = [row[1] for row in conn.execute("PRAGMA table_info(transactions)").fetchall()]
    if "cat_source" not in tx_columns:
        conn.execute("ALTER TABLE transactions ADD COLUMN cat_source TEXT DEFAULT 'auto'")
        conn.commit()
        print("Added cat_source column to transactions")

    # Migration: add status column to accounts (active/archived)
    acct_columns = [row[1] for row in conn.execute("PRAGMA table_info(accounts)").fetchall()]
    if "status" not in acct_columns:
        conn.execute("ALTER TABLE accounts ADD COLUMN status TEXT DEFAULT 'active'")
        # Archive inactive accounts
        conn.execute("UPDATE accounts SET status = 'archived' WHERE short_name IN ('DBS-Altitude-5054', 'DBS-Debit-6088')")
        conn.commit()
        print("Added status column to accounts (archived: DBS-Altitude-5054, DBS-Debit-6088)")

    # Seed: Kalesh bank account (business)
    kalesh_bank = conn.execute(
        "SELECT id FROM accounts WHERE short_name = 'DBS-Kalesh-Bank'"
    ).fetchone()
    if not kalesh_bank:
        conn.execute(
            "INSERT INTO accounts (name, short_name, type, last_four, currency, status) "
            "VALUES ('DBS Kalesh Bank Account', 'DBS-Kalesh-Bank', 'bank', NULL, 'SGD', 'active')"
        )
        conn.commit()
        print("Added Kalesh bank account")

    # Migration: billing model refactor — add amount + currency to subscriptions
    sub_columns = [row[1] for row in conn.execute("PRAGMA table_info(subscriptions)").fetchall()]
    if "currency" not in sub_columns:
        conn.execute("ALTER TABLE subscriptions ADD COLUMN amount REAL")
        conn.execute("ALTER TABLE subscriptions ADD COLUMN currency TEXT DEFAULT 'SGD'")
        conn.commit()
        # Populate: if amount_usd > 0, this is USD-billed; otherwise SGD-billed
        conn.execute("""
            UPDATE subscriptions SET
                amount = CASE
                    WHEN amount_usd IS NOT NULL AND amount_usd > 0 THEN amount_usd
                    ELSE amount_sgd
                END,
                currency = CASE
                    WHEN amount_usd IS NOT NULL AND amount_usd > 0 THEN 'USD'
                    ELSE 'SGD'
                END
        """)
        conn.commit()
        # (Diagnostic for FX ratios removed — legacy columns being dropped)
        migrated = conn.execute("SELECT COUNT(*) FROM subscriptions WHERE amount IS NOT NULL").fetchone()[0]
        print(f"Billing model migration: {migrated} subscriptions migrated to amount/currency")

    # Migration: drop legacy subscription columns (service text, amount_sgd, amount_usd, card)
    # These have been replaced by service_id FK, amount+currency, and account_id FK
    if "amount_sgd" in sub_columns:
        for col in ["amount_sgd", "amount_usd", "card", "service"]:
            if col in sub_columns:
                conn.execute(f"ALTER TABLE subscriptions DROP COLUMN {col}")
        conn.commit()
        print("Legacy subscription columns dropped: service, amount_sgd, amount_usd, card")

    # Migration: drop category_id from merchant_rules (service-centric model)
    # Rules now map pattern → service_id only. Category derived from service.
    rule_columns = [row[1] for row in conn.execute("PRAGMA table_info(merchant_rules)").fetchall()]
    if "category_id" in rule_columns:
        # Backfill any orphaned rules (service_id IS NULL) before dropping
        orphans = conn.execute(
            "SELECT id, category_id FROM merchant_rules WHERE service_id IS NULL"
        ).fetchall()
        for orphan in orphans:
            # Find or create a service with matching category
            svc = conn.execute(
                "SELECT id FROM services WHERE category_id = ? LIMIT 1",
                (orphan["category_id"],)
            ).fetchone()
            if svc:
                conn.execute("UPDATE merchant_rules SET service_id = ? WHERE id = ?",
                             (svc["id"], orphan["id"]))
            else:
                # Create a generic service for this category
                cat = conn.execute("SELECT name FROM categories WHERE id = ?",
                                   (orphan["category_id"],)).fetchone()
                svc_name = f"Unknown ({cat['name']})" if cat else "Unknown"
                conn.execute("INSERT INTO services (name, category_id) VALUES (?, ?)",
                             (svc_name, orphan["category_id"]))
                new_svc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                conn.execute("UPDATE merchant_rules SET service_id = ? WHERE id = ?",
                             (new_svc_id, orphan["id"]))
        if orphans:
            conn.commit()
            print(f"Backfilled {len(orphans)} orphaned rules with service_id")

        # Recreate table without category_id (can't ALTER DROP with FK constraint)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS merchant_rules_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern TEXT NOT NULL,
                service_id INTEGER NOT NULL,
                match_type TEXT DEFAULT 'contains',
                confidence TEXT DEFAULT 'confirmed',
                priority INTEGER DEFAULT 0,
                min_amount REAL,
                max_amount REAL,
                created_at TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (service_id) REFERENCES services(id)
            );
            INSERT INTO merchant_rules_new (id, pattern, service_id, match_type, confidence, priority, min_amount, max_amount, created_at)
                SELECT id, pattern, service_id, match_type, confidence,
                       COALESCE(priority, 0), min_amount, max_amount, created_at
                FROM merchant_rules;
            DROP TABLE merchant_rules;
            ALTER TABLE merchant_rules_new RENAME TO merchant_rules;
            CREATE INDEX IF NOT EXISTS idx_merchant_rules_pattern ON merchant_rules(pattern);
        """)
        print("Dropped category_id from merchant_rules (service-centric model)")

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

    # Migration: move top-level Insurance under Admin (if Admin exists and Insurance
    # is still top-level)
    admin_row = conn.execute("SELECT id FROM categories WHERE name = 'Admin'").fetchone()
    if admin_row:
        old_insurance = conn.execute(
            "SELECT id FROM categories WHERE name = 'Insurance' AND parent_id IS NULL"
        ).fetchone()
        if old_insurance:
            # Check if an "Insurance" under Admin already exists (from DEFAULT_CATEGORIES seed)
            admin_insurance = conn.execute(
                "SELECT id FROM categories WHERE name = 'Insurance' AND parent_id = ?",
                (admin_row[0],)
            ).fetchone()
            if admin_insurance:
                # Move all references from old Insurance to the new one under Admin
                # Update services (not rules — rules no longer have category_id)
                conn.execute(
                    "UPDATE services SET category_id = ? WHERE category_id = ?",
                    (admin_insurance[0], old_insurance[0]),
                )
                conn.execute(
                    "UPDATE transactions SET category_id = ? WHERE category_id = ?",
                    (admin_insurance[0], old_insurance[0]),
                )
                conn.execute("DELETE FROM categories WHERE id = ?", (old_insurance[0],))
            else:
                # Just move the existing Insurance under Admin
                conn.execute(
                    "UPDATE categories SET parent_id = ? WHERE id = ?",
                    (admin_row[0], old_insurance[0]),
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


def migrate_split_multimonth_statements() -> None:
    """Split multi-month statements into per-month records.

    Finds statements whose transactions span multiple months, creates
    per-month statement records (YYYY-MM-01), re-links transactions to
    the correct month's statement, and deletes the original if empty.
    Idempotent — safe to run multiple times.
    """
    conn = get_connection()

    # Find statements with transactions spanning multiple months
    multi = conn.execute("""
        SELECT s.id, s.account_id, s.filename,
               COUNT(DISTINCT SUBSTR(t.date, 1, 7)) as month_count
        FROM statements s
        JOIN transactions t ON t.statement_id = s.id
        GROUP BY s.id
        HAVING month_count > 1
    """).fetchall()

    if not multi:
        conn.close()
        return

    total_moved = 0
    stmts_created = 0
    stmts_deleted = 0

    for stmt in multi:
        stmt_id = stmt["id"]
        account_id = stmt["account_id"]
        filename = stmt["filename"] or ""

        # Get distinct months for this statement's transactions
        months = conn.execute(
            "SELECT DISTINCT SUBSTR(date, 1, 7) as ym FROM transactions WHERE statement_id = ?",
            (stmt_id,),
        ).fetchall()

        for row in months:
            ym = row["ym"]
            target_date = f"{ym}-01"

            # Get or create the per-month statement
            existing = conn.execute(
                "SELECT id FROM statements WHERE account_id = ? AND statement_date = ?",
                (account_id, target_date),
            ).fetchone()

            if existing:
                target_stmt_id = existing["id"]
                # Don't re-link if the source IS the target (shouldn't happen but safety)
                if target_stmt_id == stmt_id:
                    continue
            else:
                conn.execute(
                    "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, ?)",
                    (account_id, target_date, filename),
                )
                target_stmt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                stmts_created += 1

            # Re-link transactions from old statement to per-month statement
            cur = conn.execute(
                "UPDATE transactions SET statement_id = ? "
                "WHERE statement_id = ? AND SUBSTR(date, 1, 7) = ?",
                (target_stmt_id, stmt_id, ym),
            )
            total_moved += cur.rowcount

        # Delete the original multi-month statement if it has no remaining transactions
        remaining = conn.execute(
            "SELECT COUNT(*) FROM transactions WHERE statement_id = ?", (stmt_id,)
        ).fetchone()[0]
        if remaining == 0:
            conn.execute("DELETE FROM statements WHERE id = ?", (stmt_id,))
            stmts_deleted += 1

    conn.commit()
    conn.close()
    if total_moved > 0:
        print(
            f"Statement split migration: {total_moved} txns moved, "
            f"{stmts_created} statements created, {stmts_deleted} old statements deleted"
        )


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
            "       mr.service_id, s.category_id "
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
) -> tuple[int | None, int | None]:
    """Match a transaction description to a category using merchant rules.

    Rules are sorted by priority DESC, then pattern length DESC (most specific first).
    Amount-conditional rules (min_amount / max_amount) only match if the transaction
    amount falls within the specified range.

    Returns (category_id, service_id) tuple. Either or both may be None.
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
        category_id = rule["category_id"]
        return (category_id, service_id)

    return (None, None)


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")
