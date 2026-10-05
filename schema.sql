-- fin: personal finance tracker
-- Schema v1

-- A row, a merchant and a rule are labelled by book (whose spending) and type
-- (what kind), declared in book_type.py. The category tree they replaced is
-- retired; an existing database loses it through retire_categories.py.

-- An amount is a whole number of minor units of its account's currency
-- (cents, for an SGD account); positive is money out. No float amount is
-- stored for a row or a rule threshold. An existing database gains and fills
-- the integers through convert_minor_units.py and loses the floats through
-- retire_float_amounts.py.

CREATE TABLE IF NOT EXISTS merchant_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL,          -- merchant name pattern (case-insensitive match)
    service_id INTEGER NOT NULL,   -- FK to services (book and type come from the merchant)
    match_type TEXT DEFAULT 'contains',  -- 'contains', 'startswith', 'exact'
    confidence TEXT DEFAULT 'confirmed', -- 'auto', 'confirmed' (user-verified)
    priority INTEGER DEFAULT 0,    -- higher priority wins (for overlapping patterns)
    created_at TEXT DEFAULT (datetime('now')),
    book_override TEXT,            -- book the rule sets in place of the merchant's; NULL = the merchant's
    type_override_id INTEGER REFERENCES types(id),  -- type the rule sets in place of the merchant's
    min_amount_minor INTEGER,      -- if set, rule only matches when the row's amount_minor >= this
    max_amount_minor INTEGER,      -- if set, rule only matches when the row's amount_minor <= this
    FOREIGN KEY (service_id) REFERENCES services(id)
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,           -- e.g., "DBS Altitude Visa 1229"
    short_name TEXT NOT NULL,    -- e.g., "DBS-Altitude-5054"
    type TEXT NOT NULL,          -- the account's kind: bank, card, loan, holding, company, person (declared in account_kind.py)
    last_four TEXT,              -- last 4 digits
    currency TEXT DEFAULT 'SGD', -- three-letter code with a declared minor unit (money.py): SGD, INR
    status TEXT DEFAULT 'active', -- 'active', 'archived'
    created_at TEXT DEFAULT (datetime('now')),
    owner TEXT NOT NULL DEFAULT 'Household'  -- whose sheet it is on: Household, or a company (declared in account_kind.py)
);

-- An anchor: one account's balance on one date, from a statement or supplied
-- by the operator. One per account and date. An existing database gains this
-- table, and the owner column above, through convert_account_kinds.py.
CREATE TABLE IF NOT EXISTS anchors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    date TEXT NOT NULL,             -- YYYY-MM-DD
    amount INTEGER NOT NULL,        -- whole minor units of the account's currency; cash positive, anything owed negative
    source TEXT NOT NULL,           -- 'statement' or 'supplied' (declared in anchors.py)
    note TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    UNIQUE (account_id, date)
);

-- A saved rate: what one unit of a currency was worth in SGD on a date
-- (rates.py). One per pair and date; fetched once and read from here after,
-- or entered by the operator. A new table, made empty by CREATE IF NOT EXISTS
-- on start; no existing row is converted.
CREATE TABLE IF NOT EXISTS rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,             -- 'INR/SGD': SGD for one rupee
    date TEXT NOT NULL,             -- YYYY-MM-DD, the day the rate is used for
    rate TEXT NOT NULL,             -- decimal text, never a float
    source TEXT NOT NULL,           -- where it came from, and the day it is of when that differs
    fetched_at TEXT NOT NULL,       -- when it was fetched or entered (UTC)
    UNIQUE (pair, date)
);

CREATE TABLE IF NOT EXISTS statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    statement_date TEXT NOT NULL,   -- YYYY-MM-DD
    filename TEXT,                  -- original PDF filename
    imported_at TEXT DEFAULT (datetime('now')),
    printed INTEGER NOT NULL DEFAULT 0,  -- 1 = one statement as printed: statement_date is its closing day and it holds the rows it printed; 0 = a calendar-month record (statement_date the 1st) of rows from a source that states no balance. An existing database gains it through convert_printed_statements.py
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    UNIQUE(account_id, statement_date)
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id INTEGER NOT NULL,
    date TEXT NOT NULL,             -- YYYY-MM-DD
    description TEXT NOT NULL,      -- raw merchant description from statement
    amount_foreign REAL,           -- original amount if foreign currency
    currency_foreign TEXT,         -- three-letter code, e.g., 'USD', 'AUD', 'INR'
    service_id INTEGER,            -- FK to services table (merchant identity)
    is_one_off INTEGER DEFAULT 0,  -- 1 = one-time/exceptional expense (toggle in table)
    cat_source TEXT DEFAULT 'auto',  -- where book and type came from: auto|service_default|rule_override|fallback|manual|derived (loan interest worked out from figures: loan_interest.py)
    flow_type TEXT,                -- expense|income|transfer|payment|refund|movement|review (declared in flow.py)
    flow_type_manual INTEGER DEFAULT 0,  -- 1 = user overrode classifier; preserve on recategorize
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    book TEXT,                     -- whose spending: Household, Moom, Kalesh (declared in book_type.py); NULL reads as Household
    type_id INTEGER REFERENCES types(id),  -- what kind of spending, or for income its kind; NULL = not placed
    amount_minor INTEGER,          -- the amount in whole minor units of the account's currency: positive = money out, negative = money in
    other_side_id INTEGER REFERENCES accounts(id),  -- the account a movement, transfer or payment names as its other side; NULL = none named. An existing database gains it through convert_movements.py
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

-- What the screens keep between visits (screens.py): the "Claude may write"
-- switch and the last change the operator looked at in the history. Not a
-- tracked table: flipping the switch or looking is not a change to the book.
-- A new table, made empty by CREATE IF NOT EXISTS on start; no existing row
-- is converted.
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,           -- declared in screens.py
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A statement refused at upload because its rows do not tie to its own
-- balances (screens.py). None of its rows is written; this keeps the tie line
-- so the queue and the account page can say so until a file that ties is
-- imported for the same account and day. No file name is kept. Not a
-- tracked table. A new table, made empty by CREATE IF NOT EXISTS on start;
-- no existing row is converted.
CREATE TABLE IF NOT EXISTS refused_statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_name TEXT NOT NULL,     -- as the statement names it, card numbers masked
    account_id INTEGER REFERENCES accounts(id),  -- the account it would land on; NULL = none yet
    statement_date TEXT NOT NULL,   -- YYYY-MM-DD, its closing day
    currency TEXT NOT NULL DEFAULT 'SGD',
    opening_minor INTEGER NOT NULL,
    rows_minor INTEGER NOT NULL,    -- the change its rows make to the balance (tie.figures)
    closing_minor INTEGER NOT NULL,
    difference_minor INTEGER NOT NULL,
    row_count INTEGER NOT NULL DEFAULT 0,
    refused_at TEXT NOT NULL DEFAULT (datetime('now')),
    set_aside_at TEXT,              -- "Known, leave it": off Home and the queue's top; still refused
    UNIQUE (account_name, statement_date)
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);
CREATE INDEX IF NOT EXISTS idx_transactions_statement ON transactions(statement_id);
CREATE INDEX IF NOT EXISTS idx_merchant_rules_pattern ON merchant_rules(pattern);
CREATE INDEX IF NOT EXISTS idx_subscriptions_status ON subscriptions(status);

-- history:begin
-- The change history: one entry per app action or chat tool call that
-- changed the book, with every row it changed before and after (history.py).
-- Filled by the triggers below, which record only while an entry is open, so
-- seeding and the conversion steps are never recorded. An existing database
-- gains this block through convert_change_history.py. A column added to a
-- tracked table must be added to its three triggers (a test checks).
CREATE TABLE IF NOT EXISTS change_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL DEFAULT (datetime('now')),  -- UTC
    via TEXT NOT NULL,                -- 'app' or 'chat' (declared in history.py)
    actor TEXT NOT NULL,              -- 'fin' for the app; the chat client's name
    summary TEXT NOT NULL DEFAULT '',
    open INTEGER NOT NULL DEFAULT 1,  -- 1 while the action runs; the triggers record into the open entry
    undoes INTEGER REFERENCES change_entries(id),     -- this entry undid that one
    undone_by INTEGER REFERENCES change_entries(id)   -- that entry undid this one
);
CREATE TABLE IF NOT EXISTS change_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES change_entries(id),
    tbl TEXT NOT NULL,                -- a tracked table (history.TRACKED)
    row_id INTEGER NOT NULL,
    op TEXT NOT NULL,                 -- 'insert', 'update' or 'delete'
    before TEXT,                      -- JSON of the row before; NULL for an insert
    after TEXT                        -- JSON of the row after; NULL for a delete
);
CREATE INDEX IF NOT EXISTS idx_change_rows_row ON change_rows(tbl, row_id);
CREATE INDEX IF NOT EXISTS idx_change_rows_entry ON change_rows(entry_id, id);
CREATE TRIGGER IF NOT EXISTS history_transactions_insert AFTER INSERT ON transactions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'transactions', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'statement_id', NEW.statement_id, 'date', NEW.date, 'description', NEW.description, 'amount_foreign', NEW.amount_foreign, 'currency_foreign', NEW.currency_foreign, 'service_id', NEW.service_id, 'is_one_off', NEW.is_one_off, 'cat_source', NEW.cat_source, 'flow_type', NEW.flow_type, 'flow_type_manual', NEW.flow_type_manual, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'amount_minor', NEW.amount_minor, 'other_side_id', NEW.other_side_id));
END;
CREATE TRIGGER IF NOT EXISTS history_transactions_update AFTER UPDATE ON transactions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'statement_id', OLD.statement_id, 'date', OLD.date, 'description', OLD.description, 'amount_foreign', OLD.amount_foreign, 'currency_foreign', OLD.currency_foreign, 'service_id', OLD.service_id, 'is_one_off', OLD.is_one_off, 'cat_source', OLD.cat_source, 'flow_type', OLD.flow_type, 'flow_type_manual', OLD.flow_type_manual, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'amount_minor', OLD.amount_minor, 'other_side_id', OLD.other_side_id) IS NOT json_object('id', NEW.id, 'statement_id', NEW.statement_id, 'date', NEW.date, 'description', NEW.description, 'amount_foreign', NEW.amount_foreign, 'currency_foreign', NEW.currency_foreign, 'service_id', NEW.service_id, 'is_one_off', NEW.is_one_off, 'cat_source', NEW.cat_source, 'flow_type', NEW.flow_type, 'flow_type_manual', NEW.flow_type_manual, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'amount_minor', NEW.amount_minor, 'other_side_id', NEW.other_side_id)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'transactions', NEW.id, 'update',
        json_object('id', OLD.id, 'statement_id', OLD.statement_id, 'date', OLD.date, 'description', OLD.description, 'amount_foreign', OLD.amount_foreign, 'currency_foreign', OLD.currency_foreign, 'service_id', OLD.service_id, 'is_one_off', OLD.is_one_off, 'cat_source', OLD.cat_source, 'flow_type', OLD.flow_type, 'flow_type_manual', OLD.flow_type_manual, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'amount_minor', OLD.amount_minor, 'other_side_id', OLD.other_side_id),
        json_object('id', NEW.id, 'statement_id', NEW.statement_id, 'date', NEW.date, 'description', NEW.description, 'amount_foreign', NEW.amount_foreign, 'currency_foreign', NEW.currency_foreign, 'service_id', NEW.service_id, 'is_one_off', NEW.is_one_off, 'cat_source', NEW.cat_source, 'flow_type', NEW.flow_type, 'flow_type_manual', NEW.flow_type_manual, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'amount_minor', NEW.amount_minor, 'other_side_id', NEW.other_side_id));
END;
CREATE TRIGGER IF NOT EXISTS history_transactions_delete AFTER DELETE ON transactions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'transactions', OLD.id, 'delete',
        json_object('id', OLD.id, 'statement_id', OLD.statement_id, 'date', OLD.date, 'description', OLD.description, 'amount_foreign', OLD.amount_foreign, 'currency_foreign', OLD.currency_foreign, 'service_id', OLD.service_id, 'is_one_off', OLD.is_one_off, 'cat_source', OLD.cat_source, 'flow_type', OLD.flow_type, 'flow_type_manual', OLD.flow_type_manual, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'amount_minor', OLD.amount_minor, 'other_side_id', OLD.other_side_id), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_anchors_insert AFTER INSERT ON anchors
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'anchors', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'account_id', NEW.account_id, 'date', NEW.date, 'amount', NEW.amount, 'source', NEW.source, 'note', NEW.note, 'created_at', NEW.created_at));
END;
CREATE TRIGGER IF NOT EXISTS history_anchors_update AFTER UPDATE ON anchors
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'account_id', OLD.account_id, 'date', OLD.date, 'amount', OLD.amount, 'source', OLD.source, 'note', OLD.note, 'created_at', OLD.created_at) IS NOT json_object('id', NEW.id, 'account_id', NEW.account_id, 'date', NEW.date, 'amount', NEW.amount, 'source', NEW.source, 'note', NEW.note, 'created_at', NEW.created_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'anchors', NEW.id, 'update',
        json_object('id', OLD.id, 'account_id', OLD.account_id, 'date', OLD.date, 'amount', OLD.amount, 'source', OLD.source, 'note', OLD.note, 'created_at', OLD.created_at),
        json_object('id', NEW.id, 'account_id', NEW.account_id, 'date', NEW.date, 'amount', NEW.amount, 'source', NEW.source, 'note', NEW.note, 'created_at', NEW.created_at));
END;
CREATE TRIGGER IF NOT EXISTS history_anchors_delete AFTER DELETE ON anchors
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'anchors', OLD.id, 'delete',
        json_object('id', OLD.id, 'account_id', OLD.account_id, 'date', OLD.date, 'amount', OLD.amount, 'source', OLD.source, 'note', OLD.note, 'created_at', OLD.created_at), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_services_insert AFTER INSERT ON services
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'services', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'name', NEW.name, 'is_one_off', NEW.is_one_off, 'exclude_from_expense_views', NEW.exclude_from_expense_views, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'review_each_time', NEW.review_each_time));
END;
CREATE TRIGGER IF NOT EXISTS history_services_update AFTER UPDATE ON services
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'name', OLD.name, 'is_one_off', OLD.is_one_off, 'exclude_from_expense_views', OLD.exclude_from_expense_views, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'review_each_time', OLD.review_each_time) IS NOT json_object('id', NEW.id, 'name', NEW.name, 'is_one_off', NEW.is_one_off, 'exclude_from_expense_views', NEW.exclude_from_expense_views, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'review_each_time', NEW.review_each_time)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'services', NEW.id, 'update',
        json_object('id', OLD.id, 'name', OLD.name, 'is_one_off', OLD.is_one_off, 'exclude_from_expense_views', OLD.exclude_from_expense_views, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'review_each_time', OLD.review_each_time),
        json_object('id', NEW.id, 'name', NEW.name, 'is_one_off', NEW.is_one_off, 'exclude_from_expense_views', NEW.exclude_from_expense_views, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'review_each_time', NEW.review_each_time));
END;
CREATE TRIGGER IF NOT EXISTS history_services_delete AFTER DELETE ON services
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'services', OLD.id, 'delete',
        json_object('id', OLD.id, 'name', OLD.name, 'is_one_off', OLD.is_one_off, 'exclude_from_expense_views', OLD.exclude_from_expense_views, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'review_each_time', OLD.review_each_time), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_merchant_rules_insert AFTER INSERT ON merchant_rules
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'merchant_rules', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'pattern', NEW.pattern, 'service_id', NEW.service_id, 'match_type', NEW.match_type, 'confidence', NEW.confidence, 'priority', NEW.priority, 'created_at', NEW.created_at, 'book_override', NEW.book_override, 'type_override_id', NEW.type_override_id, 'min_amount_minor', NEW.min_amount_minor, 'max_amount_minor', NEW.max_amount_minor));
END;
CREATE TRIGGER IF NOT EXISTS history_merchant_rules_update AFTER UPDATE ON merchant_rules
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'pattern', OLD.pattern, 'service_id', OLD.service_id, 'match_type', OLD.match_type, 'confidence', OLD.confidence, 'priority', OLD.priority, 'created_at', OLD.created_at, 'book_override', OLD.book_override, 'type_override_id', OLD.type_override_id, 'min_amount_minor', OLD.min_amount_minor, 'max_amount_minor', OLD.max_amount_minor) IS NOT json_object('id', NEW.id, 'pattern', NEW.pattern, 'service_id', NEW.service_id, 'match_type', NEW.match_type, 'confidence', NEW.confidence, 'priority', NEW.priority, 'created_at', NEW.created_at, 'book_override', NEW.book_override, 'type_override_id', NEW.type_override_id, 'min_amount_minor', NEW.min_amount_minor, 'max_amount_minor', NEW.max_amount_minor)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'merchant_rules', NEW.id, 'update',
        json_object('id', OLD.id, 'pattern', OLD.pattern, 'service_id', OLD.service_id, 'match_type', OLD.match_type, 'confidence', OLD.confidence, 'priority', OLD.priority, 'created_at', OLD.created_at, 'book_override', OLD.book_override, 'type_override_id', OLD.type_override_id, 'min_amount_minor', OLD.min_amount_minor, 'max_amount_minor', OLD.max_amount_minor),
        json_object('id', NEW.id, 'pattern', NEW.pattern, 'service_id', NEW.service_id, 'match_type', NEW.match_type, 'confidence', NEW.confidence, 'priority', NEW.priority, 'created_at', NEW.created_at, 'book_override', NEW.book_override, 'type_override_id', NEW.type_override_id, 'min_amount_minor', NEW.min_amount_minor, 'max_amount_minor', NEW.max_amount_minor));
END;
CREATE TRIGGER IF NOT EXISTS history_merchant_rules_delete AFTER DELETE ON merchant_rules
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'merchant_rules', OLD.id, 'delete',
        json_object('id', OLD.id, 'pattern', OLD.pattern, 'service_id', OLD.service_id, 'match_type', OLD.match_type, 'confidence', OLD.confidence, 'priority', OLD.priority, 'created_at', OLD.created_at, 'book_override', OLD.book_override, 'type_override_id', OLD.type_override_id, 'min_amount_minor', OLD.min_amount_minor, 'max_amount_minor', OLD.max_amount_minor), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_accounts_insert AFTER INSERT ON accounts
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'accounts', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'name', NEW.name, 'short_name', NEW.short_name, 'type', NEW.type, 'last_four', NEW.last_four, 'currency', NEW.currency, 'status', NEW.status, 'created_at', NEW.created_at, 'owner', NEW.owner));
END;
CREATE TRIGGER IF NOT EXISTS history_accounts_update AFTER UPDATE ON accounts
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'name', OLD.name, 'short_name', OLD.short_name, 'type', OLD.type, 'last_four', OLD.last_four, 'currency', OLD.currency, 'status', OLD.status, 'created_at', OLD.created_at, 'owner', OLD.owner) IS NOT json_object('id', NEW.id, 'name', NEW.name, 'short_name', NEW.short_name, 'type', NEW.type, 'last_four', NEW.last_four, 'currency', NEW.currency, 'status', NEW.status, 'created_at', NEW.created_at, 'owner', NEW.owner)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'accounts', NEW.id, 'update',
        json_object('id', OLD.id, 'name', OLD.name, 'short_name', OLD.short_name, 'type', OLD.type, 'last_four', OLD.last_four, 'currency', OLD.currency, 'status', OLD.status, 'created_at', OLD.created_at, 'owner', OLD.owner),
        json_object('id', NEW.id, 'name', NEW.name, 'short_name', NEW.short_name, 'type', NEW.type, 'last_four', NEW.last_four, 'currency', NEW.currency, 'status', NEW.status, 'created_at', NEW.created_at, 'owner', NEW.owner));
END;
CREATE TRIGGER IF NOT EXISTS history_accounts_delete AFTER DELETE ON accounts
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'accounts', OLD.id, 'delete',
        json_object('id', OLD.id, 'name', OLD.name, 'short_name', OLD.short_name, 'type', OLD.type, 'last_four', OLD.last_four, 'currency', OLD.currency, 'status', OLD.status, 'created_at', OLD.created_at, 'owner', OLD.owner), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_rates_insert AFTER INSERT ON rates
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'rates', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'pair', NEW.pair, 'date', NEW.date, 'rate', NEW.rate, 'source', NEW.source, 'fetched_at', NEW.fetched_at));
END;
CREATE TRIGGER IF NOT EXISTS history_rates_update AFTER UPDATE ON rates
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'pair', OLD.pair, 'date', OLD.date, 'rate', OLD.rate, 'source', OLD.source, 'fetched_at', OLD.fetched_at) IS NOT json_object('id', NEW.id, 'pair', NEW.pair, 'date', NEW.date, 'rate', NEW.rate, 'source', NEW.source, 'fetched_at', NEW.fetched_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'rates', NEW.id, 'update',
        json_object('id', OLD.id, 'pair', OLD.pair, 'date', OLD.date, 'rate', OLD.rate, 'source', OLD.source, 'fetched_at', OLD.fetched_at),
        json_object('id', NEW.id, 'pair', NEW.pair, 'date', NEW.date, 'rate', NEW.rate, 'source', NEW.source, 'fetched_at', NEW.fetched_at));
END;
CREATE TRIGGER IF NOT EXISTS history_rates_delete AFTER DELETE ON rates
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'rates', OLD.id, 'delete',
        json_object('id', OLD.id, 'pair', OLD.pair, 'date', OLD.date, 'rate', OLD.rate, 'source', OLD.source, 'fetched_at', OLD.fetched_at), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_statements_insert AFTER INSERT ON statements
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'statements', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'account_id', NEW.account_id, 'statement_date', NEW.statement_date, 'filename', NEW.filename, 'imported_at', NEW.imported_at, 'printed', NEW.printed));
END;
CREATE TRIGGER IF NOT EXISTS history_statements_update AFTER UPDATE ON statements
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'account_id', OLD.account_id, 'statement_date', OLD.statement_date, 'filename', OLD.filename, 'imported_at', OLD.imported_at, 'printed', OLD.printed) IS NOT json_object('id', NEW.id, 'account_id', NEW.account_id, 'statement_date', NEW.statement_date, 'filename', NEW.filename, 'imported_at', NEW.imported_at, 'printed', NEW.printed)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'statements', NEW.id, 'update',
        json_object('id', OLD.id, 'account_id', OLD.account_id, 'statement_date', OLD.statement_date, 'filename', OLD.filename, 'imported_at', OLD.imported_at, 'printed', OLD.printed),
        json_object('id', NEW.id, 'account_id', NEW.account_id, 'statement_date', NEW.statement_date, 'filename', NEW.filename, 'imported_at', NEW.imported_at, 'printed', NEW.printed));
END;
CREATE TRIGGER IF NOT EXISTS history_statements_delete AFTER DELETE ON statements
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'statements', OLD.id, 'delete',
        json_object('id', OLD.id, 'account_id', OLD.account_id, 'statement_date', OLD.statement_date, 'filename', OLD.filename, 'imported_at', OLD.imported_at, 'printed', OLD.printed), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_subscriptions_insert AFTER INSERT ON subscriptions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'subscriptions', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'service_id', NEW.service_id, 'amount', NEW.amount, 'currency', NEW.currency, 'frequency', NEW.frequency, 'periods', NEW.periods, 'account_id', NEW.account_id, 'last_paid', NEW.last_paid, 'renewal_date', NEW.renewal_date, 'status', NEW.status, 'link', NEW.link, 'notes', NEW.notes, 'match_pattern', NEW.match_pattern, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id));
END;
CREATE TRIGGER IF NOT EXISTS history_subscriptions_update AFTER UPDATE ON subscriptions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'service_id', OLD.service_id, 'amount', OLD.amount, 'currency', OLD.currency, 'frequency', OLD.frequency, 'periods', OLD.periods, 'account_id', OLD.account_id, 'last_paid', OLD.last_paid, 'renewal_date', OLD.renewal_date, 'status', OLD.status, 'link', OLD.link, 'notes', OLD.notes, 'match_pattern', OLD.match_pattern, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id) IS NOT json_object('id', NEW.id, 'service_id', NEW.service_id, 'amount', NEW.amount, 'currency', NEW.currency, 'frequency', NEW.frequency, 'periods', NEW.periods, 'account_id', NEW.account_id, 'last_paid', NEW.last_paid, 'renewal_date', NEW.renewal_date, 'status', NEW.status, 'link', NEW.link, 'notes', NEW.notes, 'match_pattern', NEW.match_pattern, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'subscriptions', NEW.id, 'update',
        json_object('id', OLD.id, 'service_id', OLD.service_id, 'amount', OLD.amount, 'currency', OLD.currency, 'frequency', OLD.frequency, 'periods', OLD.periods, 'account_id', OLD.account_id, 'last_paid', OLD.last_paid, 'renewal_date', OLD.renewal_date, 'status', OLD.status, 'link', OLD.link, 'notes', OLD.notes, 'match_pattern', OLD.match_pattern, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id),
        json_object('id', NEW.id, 'service_id', NEW.service_id, 'amount', NEW.amount, 'currency', NEW.currency, 'frequency', NEW.frequency, 'periods', NEW.periods, 'account_id', NEW.account_id, 'last_paid', NEW.last_paid, 'renewal_date', NEW.renewal_date, 'status', NEW.status, 'link', NEW.link, 'notes', NEW.notes, 'match_pattern', NEW.match_pattern, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id));
END;
CREATE TRIGGER IF NOT EXISTS history_subscriptions_delete AFTER DELETE ON subscriptions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'subscriptions', OLD.id, 'delete',
        json_object('id', OLD.id, 'service_id', OLD.service_id, 'amount', OLD.amount, 'currency', OLD.currency, 'frequency', OLD.frequency, 'periods', OLD.periods, 'account_id', OLD.account_id, 'last_paid', OLD.last_paid, 'renewal_date', OLD.renewal_date, 'status', OLD.status, 'link', OLD.link, 'notes', OLD.notes, 'match_pattern', OLD.match_pattern, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id), NULL);
END;
-- history:end
