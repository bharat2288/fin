"""Generate mock data for fin demo database.

Creates a realistic-looking fin.db with 6 months of fictional transactions,
accounts, subscriptions, and import history. All data is entirely made up —
no real financial information.

Usage:
    python seed_mock_data.py

Safety: refuses to run if fin.db already exists.
"""

import calendar
import random
import sys
from datetime import date, timedelta

import book_type
from db import current_db_path, get_connection, init_db, match_merchant

# The six months the book covers end here. The repo's demo book keeps this
# end so its output stays the same; the public sample (demo_serve.py) ends
# at the last whole month instead, so its screens read as current.
DEFAULT_END_MONTH = (2026, 3)
# Set by build() for the steps below.
MONTHS: list[tuple[int, int]] = []


def six_months_ending(end: tuple[int, int]) -> list[tuple[int, int]]:
    """The six (year, month) pairs ending at `end`, oldest first."""
    year, month = end
    index = year * 12 + (month - 1)
    return [(i // 12, i % 12 + 1) for i in range(index - 5, index + 1)]


def _month_start(year_month: tuple[int, int]) -> str:
    return f"{year_month[0]:04d}-{year_month[1]:02d}-01"

# ---------------------------------------------------------------------------
# Mock accounts — fictional card numbers, real bank names
# ---------------------------------------------------------------------------
MOCK_ACCOUNTS = [
    ("DBS Visa Platinum 4521", "DBS-Visa-4521", "card", "4521", "SGD"),
    ("DBS Savings Account 8834", "DBS-Savings-8834", "bank", "8834", "SGD"),
    ("Citi Rewards Card 7293", "Citi-Rewards-7293", "card", "7293", "SGD"),
    ("UOB One Card 3156", "UOB-One-3156", "card", "3156", "SGD"),
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

# How many transactions per month per merchant group (weight-based distribution).
# Higher weight = more transactions from that group each month.
GROUP_WEIGHTS = {
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
    """Create per-month statement records for the six months."""
    months = [_month_start(m) for m in MONTHS]
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


def create_transactions(conn, account_ids: dict):
    """Generate ~600-900 mock transactions across 6 months."""
    # Type name → id, for a merchant no rule knows
    type_ids = book_type.spending_type_ids(conn)

    # Personal account IDs (exclude business)
    personal_accounts = [
        aid for sn, aid in account_ids.items()
        if sn != "DBS-Biz-Bank"
    ]
    biz_account = account_ids.get("DBS-Biz-Bank")

    # Months to generate
    months = MONTHS

    total_inserted = 0
    random.seed(42)  # Reproducible mock data

    for year, month in months:
        # Vary transaction count per month (100-150)
        target_count = random.randint(100, 140)
        month_txns = 0

        # Build weighted pool of (group, merchant_desc, min_amt, max_amt)
        pool = []
        for group, weight in GROUP_WEIGHTS.items():
            merchants = MERCHANTS.get(group, [])
            if not merchants:
                continue
            for _ in range(weight):
                merchant = random.choice(merchants)
                pool.append((group, *merchant))

        random.shuffle(pool)

        for i in range(target_count):
            if i >= len(pool):
                # Wrap around
                entry = pool[i % len(pool)]
            else:
                entry = pool[i]

            group, description, min_amt, max_amt = entry

            # Generate amount with realistic distribution (slightly right-skewed),
            # in whole cents
            amount = round(random.uniform(min_amt, max_amt) * 100)
            # 30% chance of being near the lower end
            if random.random() < 0.3:
                amount = round(random.uniform(min_amt, min_amt + (max_amt - min_amt) * 0.3) * 100)

            # Book, type and service via the rule engine; for a merchant no
            # rule knows, the group's name where it is a type
            found = match_merchant(description, conn, amount_minor=amount)
            service_id = found["service_id"]
            book = found["book"]
            type_id = found["type_id"]
            if service_id is None:
                book = book_type.DEFAULT_BOOK
                type_id = type_ids.get(group)

            # Pick account — business expenses go to business account
            is_biz = group == "Business"
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
                "(statement_id, date, description, amount_minor, book, type_id, "
                "service_id, is_one_off, cat_source, flow_type) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'expense')",
                (
                    stmt_id,
                    tx_date.isoformat(),
                    description,
                    amount,
                    book,
                    type_id,
                    service_id,
                    is_one_off,
                    cat_source,
                ),
            )
            month_txns += 1

        # Add 3-8 transactions with no type per month
        uncat_count = random.randint(3, 8)
        uncat_descs = [
            "PAYMENT TO 91234567", "PAYNOW TRANSFER REF123",
            "NETS PURCHASE 482910", "POS DEBIT 8820134",
            "BILL PAYMENT REF 44210", "FAST PAYMENT 2738291",
            "GIRO DEDUCTION AUTO", "FUND TRANSFER 119283",
        ]
        for _ in range(uncat_count):
            desc = random.choice(uncat_descs)
            amount = round(random.uniform(5, 200) * 100)  # whole cents
            tx_date = random_date_in_month(year, month)
            acct_id = random.choice(personal_accounts)
            stmt_id = get_statement_id(conn, acct_id, tx_date.isoformat())
            conn.execute(
                "INSERT INTO transactions "
                "(statement_id, date, description, amount_minor, type_id, "
                "service_id, is_one_off, cat_source, flow_type) "
                "VALUES (?, ?, ?, ?, NULL, NULL, 0, 'auto', 'expense')",
                (stmt_id, tx_date.isoformat(), desc, amount),
            )
            month_txns += 1

        total_inserted += month_txns
        print(f"  {year}-{month:02d}: {month_txns} transactions")

    conn.commit()
    print(f"  Total: {total_inserted} transactions")


def create_subscriptions(conn, account_ids: dict):
    """Create 10 mock subscriptions with real-world prices. A subscription
    takes its book and type from its service."""
    # Personal credit card accounts for subscription billing
    cc_accounts = [
        aid for sn, aid in account_ids.items()
        if "Visa" in sn or "Rewards" in sn or "One" in sn
    ]

    for svc_pattern, amount, currency, frequency, match_pattern in MOCK_SUBSCRIPTIONS:
        # Find or create the service
        svc = conn.execute(
            "SELECT id FROM services WHERE UPPER(name) LIKE ?",
            (f"%{svc_pattern.upper()}%",),
        ).fetchone()

        if svc:
            svc_id = svc["id"]
        else:
            conn.execute(
                "INSERT INTO services (name, book, type_id) VALUES (?, ?, ?)",
                (svc_pattern, book_type.DEFAULT_BOOK,
                 book_type.spending_type_ids(conn)["Subscriptions"]),
            )
            svc_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Set renewal date to near-future (within 30 days) for demo effect
        days_offset = random.randint(1, 30)
        renewal = (date(*MONTHS[-1], 16) + timedelta(days=days_offset)).isoformat()

        # Last paid: recent past
        last_paid = (date(*MONTHS[-1], 16) - timedelta(days=random.randint(1, 28))).isoformat()

        acct_id = random.choice(cc_accounts)

        conn.execute(
            "INSERT INTO subscriptions "
            "(service_id, amount, currency, frequency, periods, "
            "account_id, last_paid, renewal_date, status, match_pattern) "
            "VALUES (?, ?, ?, ?, 1, ?, ?, ?, 'active', ?)",
            (svc_id, amount, currency, frequency, acct_id,
             last_paid, renewal, match_pattern),
        )

    conn.commit()


# Each account's balance the day before the first month, in minor units:
# cash positive, owed negative.
OPENING_BALANCES = {
    "DBS-Savings-8834": 6_240_000,
    "UOB-Savings-6602": 3_815_000,
    "DBS-Visa-4521": -184_250,
    "Citi-Rewards-7293": -96_830,
    "UOB-One-3156": -142_010,
}


def create_anchors(conn, account_ids: dict[str, int]):
    """A balance for each account the day before the first month and at the
    end of every month, each the one before less that month's rows, so the
    books tie and Home has a net worth to show. Entered as the operator's
    own figures (supplied): the mock statements state no balance."""
    import anchors

    for short_name, acct_id in account_ids.items():
        balance = OPENING_BALANCES.get(short_name)
        if balance is None:
            continue
        first = date(*MONTHS[0], 1)
        anchors.record(conn, acct_id, (first - timedelta(days=1)).isoformat(), balance, anchors.SUPPLIED, "opening balance")
        for year, month in MONTHS:
            last = date(year, month, calendar.monthrange(year, month)[1])
            moved = conn.execute(
                "SELECT COALESCE(SUM(t.amount_minor), 0) FROM transactions t JOIN statements s ON s.id = t.statement_id "
                "WHERE s.account_id = ? AND t.date BETWEEN ? AND ?",
                (acct_id, f"{year:04d}-{month:02d}-01", last.isoformat()),
            ).fetchone()[0]
            balance -= moved  # positive amount_minor = money out
            anchors.record(conn, acct_id, last.isoformat(), balance, anchors.SUPPLIED, "month-end balance")
    conn.commit()


def create_batch_imports(conn):
    """Create 2 mock batch import records for import history."""
    first, third = MONTHS[0], MONTHS[2]
    first_name, third_name = _month_start(first)[:7], _month_start(third)[:7]
    first_after, third_after = _month_start(MONTHS[1]), _month_start(MONTHS[3])
    conn.execute(
        "INSERT INTO batch_imports (filenames, accounts, status, total_lines, "
        "categorized_lines, created_at) VALUES (?, ?, 'committed', 156, 148, ?)",
        (
            f'["DBS-Visa-4521_{first_name}.csv", "Citi-Rewards-7293_{first_name}.csv"]',
            '["DBS Visa Platinum 4521", "Citi Rewards Card 7293"]',
            f"{first_after[:8]}02 10:30:00",
        ),
    )
    conn.execute(
        "INSERT INTO batch_imports (filenames, accounts, status, total_lines, "
        "categorized_lines, created_at) VALUES (?, ?, 'committed', 203, 195, ?)",
        (
            f'["DBS-Visa-4521_{third_name}.csv", "UOB-One-3156_{third_name}.csv", "DBS-Savings-8834_{third_name}.csv"]',
            '["DBS Visa Platinum 4521", "UOB One Card 3156", "DBS Savings Account 8834"]',
            f"{third_after[:8]}03 09:15:00",
        ),
    )
    conn.commit()


def print_summary(conn):
    """Print what was created."""
    counts = {}
    for table in ["accounts", "services", "merchant_rules",
                   "transactions", "subscriptions", "statements", "batch_imports"]:
        row = conn.execute(f"SELECT COUNT(*) as n FROM {table}").fetchone()
        counts[table] = row["n"]

    # Date range
    date_range = conn.execute(
        "SELECT MIN(date) as earliest, MAX(date) as latest FROM transactions"
    ).fetchone()

    # Rows with no type
    untyped = conn.execute(
        "SELECT COUNT(*) as n FROM transactions WHERE type_id IS NULL"
    ).fetchone()["n"]

    print(f"\n{'='*50}")
    print(f"  fin mock database created successfully!")
    print(f"{'='*50}")
    print(f"  Types:         {len(book_type.SPENDING_TYPES)}")
    print(f"  Accounts:      {counts['accounts']}")
    print(f"  Services:      {counts['services']}")
    print(f"  Rules:         {counts['merchant_rules']}")
    print(f"  Transactions:  {counts['transactions']} ({date_range['earliest']} to {date_range['latest']})")
    print(f"    No type:       {untyped}")
    print(f"  Subscriptions: {counts['subscriptions']}")
    print(f"  Statements:    {counts['statements']}")
    print(f"  Imports:       {counts['batch_imports']}")
    print(f"{'='*50}")
    print(f"\n  Run the app:  python app.py")
    print(f"  Open:         http://localhost:8450\n")


def main():
    # Safety check — never overwrite an existing database
    book = current_db_path()
    if book.exists():
        print(f"Error: {book} already exists.")
        print("Delete it first if you want to re-seed:")
        print(f"  rm {book}")
        sys.exit(1)

    build()


def build(end: tuple[int, int] = DEFAULT_END_MONTH, *, quiet: bool = False) -> None:
    """Write the mock book into the current book (db.current_db_path()),
    covering the six months that end at `end`. The caller checks the book
    is new."""
    MONTHS[:] = six_months_ending(end)
    if quiet:
        import contextlib
        import io

        with contextlib.redirect_stdout(io.StringIO()):
            _build()
    else:
        _build()


def _build() -> None:
    print("Creating mock database for fin...\n")

    # Step 1: Initialize schema + seed the type list + default merchant rules
    init_db()

    conn = get_connection()

    # Step 2: Create fictional accounts
    print("Creating accounts...")
    create_accounts(conn)

    # Build lookups
    account_ids = {
        r["short_name"]: r["id"]
        for r in conn.execute("SELECT id, short_name FROM accounts").fetchall()
    }

    # Step 3: Create statement records
    print("Creating statements...")
    create_statements(conn, account_ids)

    # Step 4: Generate mock transactions
    print("Generating transactions...")
    create_transactions(conn, account_ids)

    # Step 5: Create subscriptions
    print("Creating subscriptions...")
    create_subscriptions(conn, account_ids)

    # Step 6: Month-end balances
    print("Creating balances...")
    create_anchors(conn, account_ids)

    # Step 7: Create import history
    print("Creating import history...")
    create_batch_imports(conn)

    conn.close()

    # Summary
    conn = get_connection()
    print_summary(conn)
    conn.close()


if __name__ == "__main__":
    main()
