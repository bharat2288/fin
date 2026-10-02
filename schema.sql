-- fin: personal finance tracker
-- Schema v1

-- A row, a merchant and a rule are labelled by book (whose spending) and type
-- (what kind), declared in book_type.py. The category tree they replaced is
-- retired; an existing database loses it through retire_categories.py.

CREATE TABLE IF NOT EXISTS merchant_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,          -- merchant name pattern (case-insensitive match)
    service_id INTEGER NOT NULL,   -- FK to services (book and type come from the merchant)
    match_type TEXT DEFAULT 'contains',  -- 'contains', 'startswith', 'exact'
    confidence TEXT DEFAULT 'confirmed', -- 'auto', 'confirmed' (user-verified)
    priority INTEGER DEFAULT 0,    -- higher priority wins (for overlapping patterns)
    min_amount REAL,               -- if set, rule only matches when amount >= this
    max_amount REAL,               -- if set, rule only matches when amount <= this
    created_at TEXT DEFAULT (datetime('now')),
    book_override TEXT,            -- book the rule sets in place of the merchant's; NULL = the merchant's
    type_override_id INTEGER REFERENCES types(id),  -- type the rule sets in place of the merchant's
    FOREIGN KEY (service_id) REFERENCES services(id)
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,           -- e.g., "DBS Altitude Visa 1229"
    short_name TEXT NOT NULL,    -- e.g., "DBS-Altitude-5054"
    type TEXT NOT NULL,          -- 'credit_card', 'bank', 'debit'
    last_four TEXT,              -- last 4 digits
    currency TEXT DEFAULT 'SGD',
    status TEXT DEFAULT 'active', -- 'active', 'archived'
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    statement_date TEXT NOT NULL,   -- YYYY-MM-DD
    filename TEXT,                  -- original PDF filename
    imported_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    UNIQUE(account_id, statement_date)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id INTEGER NOT NULL,
    date TEXT NOT NULL,             -- YYYY-MM-DD
    description TEXT NOT NULL,      -- raw merchant description from statement
    amount_sgd REAL NOT NULL,       -- positive = expense, negative = credit/payment
    amount_foreign REAL,           -- original amount if foreign currency
    currency_foreign TEXT,         -- e.g., 'USD', 'AUD', 'INR'
    service_id INTEGER,            -- FK to services table (merchant identity)
    is_one_off INTEGER DEFAULT 0,  -- 1 = one-time/exceptional expense (toggle in table)
    cat_source TEXT DEFAULT 'auto',  -- where book and type came from: auto|service_default|rule_override|fallback|manual
    flow_type TEXT,                -- expense|income|transfer|payment|refund (ADR v2)
    flow_type_manual INTEGER DEFAULT 0,  -- 1 = user overrode classifier; preserve on recategorize
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    book TEXT,                     -- whose spending: Household, Moom, Kalesh (declared in book_type.py); NULL reads as Household
    type_id INTEGER REFERENCES types(id),  -- what kind of spending; NULL = not placed, or not spending
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (service_id) REFERENCES services(id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER,             -- FK to services table (source of truth for name, book and type)
    amount REAL NOT NULL,           -- billed amount per cycle
    currency TEXT DEFAULT 'SGD',    -- 'SGD' or 'USD'
    frequency TEXT NOT NULL,        -- 'monthly', 'yearly', 'quarterly'
    periods INTEGER DEFAULT 1,      -- number of periods per billing
    account_id INTEGER,            -- FK to accounts table
    last_paid TEXT,                 -- YYYY-MM-DD
    renewal_date TEXT,             -- YYYY-MM-DD
    status TEXT DEFAULT 'active',   -- 'active', 'deactivated', 'paused'
    link TEXT,                     -- URL to manage subscription
    notes TEXT,
    match_pattern TEXT,            -- pattern to match against transaction descriptions
    created_at TEXT DEFAULT (datetime('now')),
    book TEXT,                     -- not read or written: a subscription takes book and type from its merchant.
    type_id INTEGER REFERENCES types(id),  -- Both hold the label the conversion gave an existing subscription.
    FOREIGN KEY (service_id) REFERENCES services(id),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,          -- e.g., "Netflix", "SP Gas BGV", "Grab"
    is_one_off INTEGER DEFAULT 0,       -- 1 = one-off service (not recurring)
    exclude_from_expense_views INTEGER DEFAULT 0,  -- 1 = keep in ledger but hide from dashboard/expense tables
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    book TEXT,                          -- default book for this merchant's rows
    type_id INTEGER REFERENCES types(id),  -- default type for this merchant's rows
    review_each_time INTEGER DEFAULT 0  -- 1 = a mixed merchant: its rows take the default type and are flagged for a look each time
);

-- The type list: one list for every book, with one level of sub-type, and
-- the short list of income kinds. Filled from the declaration in book_type.py.
-- An existing database gains it, and the book and type columns, through
-- convert_book_type.py.
CREATE TABLE IF NOT EXISTS types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'spending',  -- 'spending' = a type, 'income' = an income kind
    name TEXT NOT NULL,
    parent_id INTEGER,              -- NULL = top-level type; FK = sub-type
    default_one_off INTEGER NOT NULL DEFAULT 0,  -- 0 = running by default, 1 = one-off by default
    covers TEXT NOT NULL,           -- plain description: what the type is for
    not_for TEXT,                   -- what it is not for
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (parent_id) REFERENCES types(id),
    UNIQUE (kind, name)
);

-- Answers from the outside judgment model to "which type is this merchant?",
-- one per cleaned merchant string, model and version (suggest.py). A
-- suggestion only: nothing here labels a row. A new table, made empty by
-- CREATE IF NOT EXISTS on start; no existing row is converted.
CREATE TABLE IF NOT EXISTS suggestion_answers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    merchant TEXT NOT NULL,           -- the cleaned merchant string that was sent
    model_id TEXT NOT NULL,           -- the model id asked for
    returned_model_id TEXT NOT NULL,  -- the id that answered; only the pinned one is ever shown
    cleaning_version TEXT NOT NULL,   -- version of the cleaning that made the string
    question_version TEXT NOT NULL,   -- version of the question text
    pick TEXT NOT NULL,               -- the option picked: a type's display name, or none_of_these
    probabilities TEXT NOT NULL,      -- JSON: the probability of every option
    confidence REAL,
    input_tokens INTEGER,
    cost_usd TEXT,                    -- decimal text, US dollars
    latency_ms INTEGER,
    asked_at TEXT DEFAULT (datetime('now')),
    chosen_type_id INTEGER REFERENCES types(id),  -- afterwards: the type the operator chose
    suggestion_visible INTEGER,       -- afterwards: 1 = the suggestion was on screen when they chose
    chosen_at TEXT,
    UNIQUE (merchant, model_id, returned_model_id, cleaning_version, question_version)
);

CREATE TABLE IF NOT EXISTS batch_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filenames TEXT NOT NULL,          -- JSON array of filenames
    accounts TEXT NOT NULL,           -- JSON array of detected account names
    status TEXT DEFAULT 'preview',    -- 'preview', 'committed', 'failed'
    total_lines INTEGER DEFAULT 0,
    categorized_lines INTEGER DEFAULT 0,
    result_json TEXT,                 -- JSON summary of what was committed
    created_at TEXT DEFAULT (datetime('now'))
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_statement ON transactions(statement_id);
CREATE INDEX IF NOT EXISTS idx_merchant_rules_pattern ON merchant_rules(pattern);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
