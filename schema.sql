-- fin: personal finance tracker
-- Schema v1

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    parent_id INTEGER,             -- NULL = top-level parent; FK = subcategory
    is_personal INTEGER DEFAULT 1,  -- 0 = business (Moom), 1 = personal
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (parent_id) REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS merchant_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,          -- merchant name pattern (case-insensitive match)
    category_id INTEGER NOT NULL,
    match_type TEXT DEFAULT 'contains',  -- 'contains', 'startswith', 'exact'
    confidence TEXT DEFAULT 'confirmed', -- 'auto', 'confirmed' (user-verified)
    priority INTEGER DEFAULT 0,    -- higher priority wins (for overlapping patterns)
    min_amount REAL,               -- if set, rule only matches when amount >= this
    max_amount REAL,               -- if set, rule only matches when amount <= this
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (category_id) REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,           -- e.g., "DBS Altitude Visa 1229"
    short_name TEXT NOT NULL,    -- e.g., "DBS-Altitude-5054"
    type TEXT NOT NULL,          -- 'credit_card', 'bank', 'debit'
    last_four TEXT,              -- last 4 digits
    currency TEXT DEFAULT 'SGD',
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
    category_id INTEGER,
    is_payment INTEGER DEFAULT 0,  -- 1 = payment/credit, not an expense
    is_transfer INTEGER DEFAULT 0, -- 1 = internal transfer (bank statements)
    is_anomaly INTEGER DEFAULT 0,  -- 1 = one-time/exceptional expense (toggle in views)
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (category_id) REFERENCES categories(id)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service TEXT NOT NULL,
    category_id INTEGER,
    amount_sgd REAL NOT NULL,       -- current billed amount in SGD
    amount_usd REAL,
    frequency TEXT NOT NULL,        -- 'monthly', 'yearly', 'quarterly'
    periods INTEGER DEFAULT 1,      -- number of periods per billing
    card TEXT,                      -- legacy text field (deprecated)
    account_id INTEGER,            -- FK to accounts table (single source of truth)
    last_paid TEXT,                 -- YYYY-MM-DD
    renewal_date TEXT,             -- YYYY-MM-DD
    status TEXT DEFAULT 'active',   -- 'active', 'deactivated', 'paused'
    link TEXT,                     -- URL to manage subscription
    notes TEXT,
    match_pattern TEXT,            -- pattern to match against transaction descriptions
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (category_id) REFERENCES categories(id),
    FOREIGN KEY (account_id) REFERENCES accounts(id)
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,          -- e.g., "Netflix", "SP Gas BGV", "Grab"
    category_id INTEGER,                -- default category for this service
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (category_id) REFERENCES categories(id)
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
CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category_id);
CREATE INDEX IF NOT EXISTS idx_transactions_statement ON transactions(statement_id);
CREATE INDEX IF NOT EXISTS idx_merchant_rules_pattern ON merchant_rules(pattern);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);
CREATE INDEX IF NOT EXISTS idx_services_category ON services(category_id);
