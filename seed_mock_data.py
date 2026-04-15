"""Generate mock data for fin demo database.

Creates a realistic-looking fin.db with 6 months of fictional transactions,
accounts, subscriptions, and import history. All data is entirely made up —
no real financial information.

Usage:
    python seed_mock_data.py

Safety: refuses to run if fin.db already exists.
"""

import random
import sys
from datetime import date, timedelta

from db import DB_PATH, get_connection, init_db

# ---------------------------------------------------------------------------
# Mock accounts — fictional card numbers, real bank names
# ---------------------------------------------------------------------------
MOCK_ACCOUNTS = [
    ("DBS Visa Platinum 4521", "DBS-Visa-4521", "credit_card", "4521", "SGD"),
    ("DBS Savings Account 8834", "DBS-Savings-8834", "bank", "8834", "SGD"),
    ("Citi Rewards Card 7293", "Citi-Rewards-7293", "credit_card", "7293", "SGD"),
    ("UOB One Card 3156", "UOB-One-3156", "credit_card", "3156", "SGD"),
    ("UOB Savings Account 6602", "UOB-Savings-6602", "bank", "6602", "SGD"),
]

# ---------------------------------------------------------------------------
# Merchant pool — (description_template, min_amount, max_amount)
# Descriptions are crafted to match DEFAULT_MERCHANT_RULES patterns.
# ---------------------------------------------------------------------------
MERCHANTS = {
    "Groceries": [
        ("FAIRPRICE FINEST SOMERSET", 15, 120),
        ("FAIRPRICE XTRA JURONG", 20, 90),
        ("COLD STORAGE GREAT WORLD", 20, 150),
        ("SHENG SIONG HDB MART BLK 123", 10, 60),
        ("GIANT HYPERMARKET TAMPINES", 15, 80),
        ("REDMART ONLINE ORDER", 30, 120),
        ("MARKS & SPENCER FOOD HALL", 25, 80),
        ("FOOD PANDA GROCERIES", 15, 60),
    ],
    "Dining": [
        ("STARBUCKS RAFFLES PLACE", 5, 15),
        ("STARBUCKS MARINA BAY", 6, 14),
        ("MCDONALD'S ORCHARD RD", 6, 18),
        ("SUBWAY TANJONG PAGAR", 8, 15),
        ("DIN TAI FUNG PARAGON", 25, 80),
        ("YA KUN KAYA TOAST CBD", 4, 10),
        ("TOAST BOX MARINA SQ", 5, 12),
        ("NESPRESSO BOUTIQUE ION", 15, 60),
        ("7-ELEVEN BUGIS", 3, 12),
        ("OLD CHANG KEE ION", 4, 10),
        ("BACHA COFFEE TAKASHIMAYA", 8, 25),
    ],
    "Transport": [
        ("GRAB*A-R12345678", 8, 35),
        ("GRAB*A-R23456789", 10, 40),
        ("GRAB*A-R34567890", 6, 25),
        ("BUS/MRT 278316423", 1.5, 3),
        ("BUS/MRT 384729156", 1.0, 2.5),
        ("GOJEK RIDE SG", 6, 25),
        ("COMFORT DELGRO TAXI", 10, 35),
        ("SHELL BUKIT TIMAH", 40, 100),
        ("PARKING.SG REF 45821", 2, 8),
    ],
    "Shopping": [
        ("SHOPEE SINGAPORE", 10, 200),
        ("LAZADA MARKETPLACE", 15, 150),
        ("AMAZON.SG ORDER", 10, 300),
        ("TAKASHIMAYA DEPT STORE", 20, 200),
        ("ZARA ORCHARD GATEWAY", 30, 150),
        ("DAISO JAPAN PLAZA SING", 5, 20),
        ("KINOKUNIYA BOOKSTORE", 10, 50),
    ],
    "Entertainment": [
        ("NETFLIX.COM", 16.98, 22.98),
        ("SPOTIFY PREMIUM", 9.90, 14.90),
        ("YOUTUBE PREMIUM", 11.98, 17.98),
        ("HBO GO ASIA", 13.98, 19.98),
    ],
    "Utilities": [
        ("SINGTEL MOBILE BILL", 40, 80),
        ("MYREPUBLIC FIBRE", 37.45, 47.45),
        ("SP GROUP UTILITIES", 80, 200),
        ("SP GAS PTE LTD", 15, 45),
        ("1PASSWORD.COM ANNUAL", 5, 8),
    ],
    "Subscriptions": [
        ("ANTHROPIC CLAUDE PRO", 27.50, 27.50),
        ("OPENAI CHATGPT PLUS", 27.50, 27.50),
        ("NOTION LABS INC", 13.50, 13.50),
        ("MICROSOFT 365 PERSONAL", 9.90, 9.90),
        ("GOOGLE ONE STORAGE", 3.98, 3.98),
    ],
    "Health": [
        ("GUARDIAN PHARMACY TAMP", 8, 45),
        ("GUARDIAN HEALTH MARINA", 10, 35),
        ("WATSONS PERSONAL CARE", 10, 50),
    ],
    "Home": [
        ("DYSON SINGAPORE ION", 80, 400),
    ],
    "Travel": [
        ("DUTY FREE CHANGI T3", 30, 200),
        ("FLYSCOOT BOOKING SG", 150, 500),
    ],
    "Insurance": [
        ("MANULIFE PREMIUM LIFE", 180, 280),
        ("AIA INSURANCE PAYMENT", 120, 220),
        ("PRUDENTIAL ASSURANCE CO", 150, 250),
    ],
    "Business": [
        ("GOOGLE*ADS ADVERTISING", 50, 300),
        ("XERO CLOUD ACCOUNTING", 35, 35),
        ("SHOPIFY MONTHLY PLAN", 40, 40),
        ("KLAVIYO EMAIL PLATFORM", 25, 100),
    ],
}

# How many transactions per month per category (weight-based distribution).
# Higher weight = more transactions from that category each month.
CATEGORY_WEIGHTS = {
    "Groceries": 18,
    "Dining": 22,
    "Transport": 20,
    "Shopping": 6,
    "Entertainment": 4,
    "Utilities": 4,
    "Subscriptions": 5,
    "Health": 3,
    "Home": 1,
    "Travel": 1,
    "Insurance": 2,
    "Business": 6,
}

# ---------------------------------------------------------------------------
# Mock subscriptions — obvious, universal services at real-world prices
# ---------------------------------------------------------------------------
MOCK_SUBSCRIPTIONS = [
    # (service_pattern, amount, currency, frequency, match_pattern)
    ("Spotify", 9.99, "USD", "monthly", "SPOTIFY"),
    ("Netflix", 22.98, "SGD", "monthly", "NETFLIX"),
    ("Claude Pro", 20.00, "USD", "monthly", "ANTHROPIC"),
    ("ChatGPT Plus", 20.00, "USD", "monthly", "CHATGPT"),
    ("YouTube Premium", 11.98, "SGD", "monthly", "YOUTUBE"),
    ("Singtel Mobile", 58.00, "SGD", "monthly", "SINGTEL"),
    ("MyRepublic Fibre", 37.45, "SGD", "monthly", "MYREPUBLIC"),
    ("Google One", 2.99, "USD", "monthly", "GOOGLE ONE"),
    ("Microsoft 365", 9.90, "SGD", "monthly", "MICROSOFT 365"),
    ("Notion", 10.00, "USD", "monthly", "NOTION"),
]


def create_accounts(conn):
    """Insert fictional bank accounts."""
    for name, short_name, acct_type, last_four, currency in MOCK_ACCOUNTS:
        conn.execute(
            "INSERT INTO accounts (name, short_name, type, last_four, currency, status) "
            "VALUES (?, ?, ?, ?, ?, 'active')",
            (name, short_name, acct_type, last_four, currency),
        )
    conn.commit()


def create_statements(conn, account_ids: dict[str, int]):
    """Create per-month statement records for 6 months (Oct 2025 – Mar 2026)."""
    months = [
        "2025-10-01", "2025-11-01", "2025-12-01",
        "2026-01-01", "2026-02-01", "2026-03-01",
    ]
    for short_name, acct_id in account_ids.items():
        if short_name == "DBS-Biz-Bank":
            # Business account: only recent 3 months
            for month in months[3:]:
                conn.execute(
                    "INSERT INTO statements (account_id, statement_date, filename) "
                    "VALUES (?, ?, ?)",
                    (acct_id, month, f"{short_name}_{month[:7]}.csv"),
                )
        else:
            for month in months:
                conn.execute(
                    "INSERT INTO statements (account_id, statement_date, filename) "
                    "VALUES (?, ?, ?)",
                    (acct_id, month, f"{short_name}_{month[:7]}.csv"),
                )
    conn.commit()


def get_statement_id(conn, account_id: int, tx_date: str) -> int:
    """Find the statement for this account + month, or create one."""
    month_start = tx_date[:7] + "-01"
    row = conn.execute(
        "SELECT id FROM statements WHERE account_id = ? AND statement_date = ?",
        (account_id, month_start),
    ).fetchone()
    if row:
        return row["id"]
    # Create on the fly if missing
    conn.execute(
        "INSERT INTO statements (account_id, statement_date) VALUES (?, ?)",
        (account_id, month_start),
    )
    conn.commit()
    return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def random_date_in_month(year: int, month: int) -> date:
    """Return a random date within the given month, biased toward weekdays."""
    if month == 12:
        days_in_month = 31
    else:
        next_month = date(year, month + 1, 1)
        days_in_month = (next_month - timedelta(days=1)).day
    day = random.randint(1, days_in_month)
    d = date(year, month, day)
    # 80% chance of weekday — shift weekends to adjacent Friday/Monday
    if d.weekday() >= 5 and random.random() < 0.8:
        if d.weekday() == 5:  # Saturday → Friday
            d -= timedelta(days=1)
        else:  # Sunday → Monday
            d += timedelta(days=1)
        # Clamp to valid range
        if d.month != month:
            d = date(year, month, days_in_month)
    return d


def create_transactions(conn, categories: dict, services: dict, account_ids: dict):
    """Generate ~600-900 mock transactions across 6 months."""
    # Build lookup: pattern → (service_id, category_id) from merchant rules
    rules = conn.execute(
        "SELECT mr.pattern, mr.match_type, mr.service_id, s.category_id "
        "FROM merchant_rules mr JOIN services s ON mr.service_id = s.id"
    ).fetchall()

    # Category name → id lookup
    cat_name_to_id = categories

    # Personal account IDs (exclude business)
    personal_accounts = [
        aid for sn, aid in account_ids.items()
        if sn != "DBS-Biz-Bank"
    ]
    biz_account = account_ids.get("DBS-Biz-Bank")

    # Months to generate
    months = [
        (2025, 10), (2025, 11), (2025, 12),
        (2026, 1), (2026, 2), (2026, 3),
    ]

    total_inserted = 0
    random.seed(42)  # Reproducible mock data

    for year, month in months:
        # Vary transaction count per month (100-150)
        target_count = random.randint(100, 140)
        month_txns = 0

        # Build weighted pool of (category, merchant_desc, min_amt, max_amt)
        pool = []
        for cat, weight in CATEGORY_WEIGHTS.items():
            merchants = MERCHANTS.get(cat, [])
            if not merchants:
                continue
            for _ in range(weight):
                merchant = random.choice(merchants)
                pool.append((cat, *merchant))

        random.shuffle(pool)

        for i in range(target_count):
            if i >= len(pool):
                # Wrap around
                entry = pool[i % len(pool)]
            else:
                entry = pool[i]

            cat_name, description, min_amt, max_amt = entry

            # Generate amount with realistic distribution (slightly right-skewed)
            amount = round(random.uniform(min_amt, max_amt), 2)
            # 30% chance of being near the lower end
            if random.random() < 0.3:
                amount = round(random.uniform(min_amt, min_amt + (max_amt - min_amt) * 0.3), 2)

            # Determine category_id and service_id via the categorization engine
            cat_id = cat_name_to_id.get(cat_name)
            # For subcategories (Insurance → under Admin)
            if not cat_id:
                cat_id = cat_name_to_id.get("Other")

            # Find matching service via rules
            service_id = None
            desc_upper = description.upper()
            for rule in rules:
                pat = rule["pattern"].upper()
                mt = rule["match_type"]
                if mt == "contains" and pat in desc_upper:
                    service_id = rule["service_id"]
                    cat_id = rule["category_id"]
                    break
                elif mt == "startswith" and desc_upper.startswith(pat):
                    service_id = rule["service_id"]
                    cat_id = rule["category_id"]
                    break

            # Pick account — business expenses go to business account
            is_biz = cat_name == "Business"
            if is_biz and biz_account:
                acct_id = biz_account
            else:
                acct_id = random.choice(personal_accounts)

            tx_date = random_date_in_month(year, month)
            stmt_id = get_statement_id(conn, acct_id, tx_date.isoformat())

            # Determine cat_source — mostly auto, ~5% manual
            cat_source = "manual" if random.random() < 0.05 else "auto"

            # One-off flag — ~2% of transactions
            is_one_off = 1 if random.random() < 0.02 else 0

            conn.execute(
                "INSERT INTO transactions "
                "(statement_id, date, description, amount_sgd, category_id, "
                "service_id, is_one_off, cat_source, flow_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'expense')",
                (
                    stmt_id,
                    tx_date.isoformat(),
                    description,
                    amount,
                    cat_id,
                    service_id,
                    is_one_off,
                    cat_source,
                ),
            )
            month_txns += 1

        # Add 3-8 uncategorized transactions per month
        uncat_count = random.randint(3, 8)
        uncat_descs = [
            "PAYMENT TO 91234567", "PAYNOW TRANSFER REF123",
            "NETS PURCHASE 482910", "POS DEBIT 8820134",
            "BILL PAYMENT REF 44210", "FAST PAYMENT 2738291",
            "GIRO DEDUCTION AUTO", "FUND TRANSFER 119283",
        ]
        for _ in range(uncat_count):
            desc = random.choice(uncat_descs)
            amount = round(random.uniform(5, 200), 2)
            tx_date = random_date_in_month(year, month)
            acct_id = random.choice(personal_accounts)
            stmt_id = get_statement_id(conn, acct_id, tx_date.isoformat())
            conn.execute(
                "INSERT INTO transactions "
                "(statement_id, date, description, amount_sgd, category_id, "
                "service_id, is_one_off, cat_source, flow_type) "
                "VALUES (?, ?, ?, ?, NULL, NULL, 0, 'auto', 'expense')",
                (stmt_id, tx_date.isoformat(), desc, amount),
            )
            month_txns += 1

        total_inserted += month_txns
        print(f"  {year}-{month:02d}: {month_txns} transactions")

    conn.commit()
    print(f"  Total: {total_inserted} transactions")


def create_subscriptions(conn, services: dict, categories: dict, account_ids: dict):
    """Create 10 mock subscriptions with real-world prices."""
    # Personal credit card accounts for subscription billing
    cc_accounts = [
        aid for sn, aid in account_ids.items()
        if "Visa" in sn or "Rewards" in sn or "One" in sn
    ]

    for svc_pattern, amount, currency, frequency, match_pattern in MOCK_SUBSCRIPTIONS:
        # Find or create the service
        svc = conn.execute(
            "SELECT id, category_id FROM services WHERE UPPER(name) LIKE ?",
            (f"%{svc_pattern.upper()}%",),
        ).fetchone()

        if svc:
            svc_id = svc["id"]
            cat_id = svc["category_id"]
        else:
            # Find category from the merchant rule
            cat_id = None
            for cat_name in ["Subscriptions", "Entertainment", "Utilities"]:
                if cat_name in categories:
                    cat_id = categories[cat_name]
                    break
            conn.execute(
                "INSERT INTO services (name, category_id) VALUES (?, ?)",
                (svc_pattern, cat_id),
            )
            svc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Set renewal date to near-future (within 30 days) for demo effect
        days_offset = random.randint(1, 30)
        renewal = (date(2026, 3, 16) + timedelta(days=days_offset)).isoformat()

        # Last paid: recent past
        last_paid = (date(2026, 3, 16) - timedelta(days=random.randint(1, 28))).isoformat()

        acct_id = random.choice(cc_accounts)

        conn.execute(
            "INSERT INTO subscriptions "
            "(service_id, category_id, amount, currency, frequency, periods, "
            "account_id, last_paid, renewal_date, status, match_pattern) "
            "VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, 'active', ?)",
            (svc_id, cat_id, amount, currency, frequency, acct_id,
             last_paid, renewal, match_pattern),
        )

    conn.commit()


def create_batch_imports(conn):
    """Create 2 mock batch import records for import history."""
    conn.execute(
        "INSERT INTO batch_imports (filenames, accounts, status, total_lines, "
        "categorized_lines, created_at) VALUES (?, ?, 'committed', 156, 148, ?)",
        (
            '["DBS-Visa-4521_2025-10.csv", "Citi-Rewards-7293_2025-10.csv"]',
            '["DBS Visa Platinum 4521", "Citi Rewards Card 7293"]',
            "2025-11-02 10:30:00",
        ),
    )
    conn.execute(
        "INSERT INTO batch_imports (filenames, accounts, status, total_lines, "
        "categorized_lines, created_at) VALUES (?, ?, 'committed', 203, 195, ?)",
        (
            '["DBS-Visa-4521_2025-12.csv", "UOB-One-3156_2025-12.csv", "DBS-Savings-8834_2025-12.csv"]',
            '["DBS Visa Platinum 4521", "UOB One Card 3156", "DBS Savings Account 8834"]',
            "2026-01-03 09:15:00",
        ),
    )
    conn.commit()


def print_summary(conn):
    """Print what was created."""
    counts = {}
    for table in ["categories", "accounts", "services", "merchant_rules",
                   "transactions", "subscriptions", "statements", "batch_imports"]:
        row = conn.execute(f"SELECT COUNT(*) as n FROM {table}").fetchone()
        counts[table] = row["n"]

    # Date range
    date_range = conn.execute(
        "SELECT MIN(date) as earliest, MAX(date) as latest FROM transactions"
    ).fetchone()

    # Uncategorized count
    uncat = conn.execute(
        "SELECT COUNT(*) as n FROM transactions WHERE category_id IS NULL"
    ).fetchone()["n"]

    print(f"\n{'='*50}")
    print(f"  fin mock database created successfully!")
    print(f"{'='*50}")
    print(f"  Categories:    {counts['categories']}")
    print(f"  Accounts:      {counts['accounts']}")
    print(f"  Services:      {counts['services']}")
    print(f"  Rules:         {counts['merchant_rules']}")
    print(f"  Transactions:  {counts['transactions']} ({date_range['earliest']} to {date_range['latest']})")
    print(f"    Uncategorized: {uncat}")
    print(f"  Subscriptions: {counts['subscriptions']}")
    print(f"  Statements:    {counts['statements']}")
    print(f"  Imports:       {counts['batch_imports']}")
    print(f"{'='*50}")
    print(f"\n  Run the app:  python app.py")
    print(f"  Open:         http://localhost:8450\n")


def _adapt_categories_for_demo(conn):
    """Rename Moom → Business and remove personal-specific categories.

    The live codebase seeds all categories via init_db(). For the mock/demo
    database we want a cleaner, universal set (12 top-level + subcats).
    This runs AFTER init_db() and modifies only the freshly-created mock DB.
    """
    # Rename "Moom" → "Business"
    conn.execute("UPDATE categories SET name = 'Business' WHERE name = 'Moom'")

    # Remove categories that are too personal for a generic demo
    remove = [
        "Health & Beauty", "Medical", "Fitness", "Pet", "Kids",
        "Education", "Personal", "Loan/EMI", "Rent", "Gifts & Donations",
        "Government",
    ]
    for cat_name in remove:
        # Move any rules/services/transactions referencing this category
        # to "Other" before deleting
        other_id = conn.execute(
            "SELECT id FROM categories WHERE name = 'Other'"
        ).fetchone()
        if other_id:
            cat_row = conn.execute(
                "SELECT id FROM categories WHERE name = ?", (cat_name,)
            ).fetchone()
            if cat_row:
                conn.execute(
                    "UPDATE services SET category_id = ? WHERE category_id = ?",
                    (other_id[0], cat_row[0]),
                )
                conn.execute(
                    "UPDATE transactions SET category_id = ? WHERE category_id = ?",
                    (other_id[0], cat_row[0]),
                )
                conn.execute("DELETE FROM categories WHERE id = ?", (cat_row[0],))

    # Rename "Kalesh" business account if it exists
    conn.execute(
        "UPDATE accounts SET name = 'DBS Business Account', short_name = 'DBS-Biz-Bank' "
        "WHERE short_name = 'DBS-Kalesh-Bank'"
    )

    conn.commit()
    final_count = conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
    print(f"Adapted categories for demo: {final_count} categories")


def main():
    # Safety check — never overwrite an existing database
    if DB_PATH.exists():
        print(f"Error: {DB_PATH} already exists.")
        print("Delete it first if you want to re-seed:")
        print(f"  rm {DB_PATH}")
        sys.exit(1)

    print("Creating mock database for fin...\n")

    # Step 1: Initialize schema + seed categories + default merchant rules
    init_db()

    conn = get_connection()

    # Step 1b: Adapt categories for open-source (rename Moom → Business,
    # remove personal-specific categories). This keeps the live codebase
    # untouched while the mock DB uses clean, universal categories.
    _adapt_categories_for_demo(conn)

    # Step 2: Create fictional accounts
    print("Creating accounts...")
    create_accounts(conn)

    # Build lookups
    categories = {
        r["name"]: r["id"]
        for r in conn.execute("SELECT id, name FROM categories").fetchall()
    }
    services = {
        r["name"]: {"id": r["id"], "category_id": r["category_id"]}
        for r in conn.execute("SELECT id, name, category_id FROM services").fetchall()
    }
    account_ids = {
        r["short_name"]: r["id"]
        for r in conn.execute("SELECT id, short_name FROM accounts").fetchall()
    }

    # Step 3: Create statement records
    print("Creating statements...")
    create_statements(conn, account_ids)

    # Step 4: Generate mock transactions
    print("Generating transactions...")
    create_transactions(conn, categories, services, account_ids)

    # Step 5: Create subscriptions
    print("Creating subscriptions...")
    create_subscriptions(conn, services, categories, account_ids)

    # Step 6: Create import history
    print("Creating import history...")
    create_batch_imports(conn)

    conn.close()

    # Summary
    conn = get_connection()
    print_summary(conn)
    conn.close()


if __name__ == "__main__":
    main()
