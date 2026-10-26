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
    flow TEXT,                     -- what the rule says a row is (flow.RULE_FLOWS); NULL = it states no flow and labels only (ADR-009). An existing database gains it through convert_rule_flow.py
    other_side_id INTEGER REFERENCES accounts(id),  -- the account it names as the row's other side, with its flow; NULL = none named
    FOREIGN KEY (service_id) REFERENCES services(id)
);

CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,           -- e.g., "DBS Altitude Visa 1229"
    short_name TEXT NOT NULL,    -- e.g., "DBS-Altitude-5054"
    type TEXT NOT NULL,          -- the account's kind: bank, card, loan, holding, company, person, untracked (declared in account_kind.py)
    last_four TEXT,              -- last 4 digits
    currency TEXT DEFAULT 'SGD', -- three-letter code with a declared minor unit (money.py): SGD, INR
    status TEXT DEFAULT 'active', -- 'active', 'archived'
    created_at TEXT DEFAULT (datetime('now')),
    owner TEXT NOT NULL DEFAULT 'Household',  -- whose sheet it is on: Household or a company (declared in account_kind.py)
    party_id INTEGER REFERENCES services(id)  -- the party (a merchant) a person, company, loan, holding or untracked account belongs to (party.py); NULL = none. An existing database gains it through convert_party_link.py
);

-- Hire-purchase terms on a loan (loan_terms.py): what turns its statement's
-- total of remaining instalments into principal owed when it is imported,
-- and the loan's first figure (the amount financed, owed from `owed_from`).
-- At most one row per loan. A new table, made empty by CREATE IF NOT EXISTS
-- on start, its triggers with it (history.ADDED_ON_START); no row is converted.
CREATE TABLE IF NOT EXISTS loan_terms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL UNIQUE REFERENCES accounts(id),
    financed_minor INTEGER NOT NULL,  -- the amount financed, whole minor units of the loan's currency
    interest_minor INTEGER NOT NULL,  -- the flat interest over the whole term
    flat_rate TEXT,                   -- the flat rate a year in percent as entered ("1.68"); NULL when the interest was entered
    months INTEGER NOT NULL,          -- how many monthly instalments
    first_instalment TEXT NOT NULL,   -- YYYY-MM-DD, the first instalment's due day
    owed_from TEXT NOT NULL,          -- YYYY-MM-DD, the day the amount financed is owed from: the loan's first figure
    method TEXT NOT NULL              -- how the interest is spread over the instalments (declared in loan_terms.py)
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
    period_start TEXT,              -- YYYY-MM-DD, the first day the statement covers, where it is known on its own (statement_period.py): a printed statement's opening day as its parser read it, so it covers period_start to statement_date; a calendar-month record's 1st, so it covers its month. NULL on a printed statement is read from the closing before it (statement_period.start); on any other record, as the 1st of its month. An existing database gains it through convert_statement_periods.py
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
    flow_type TEXT,                -- expense|income|transfer|payment|refund|movement|review (declared in flow.py)
    flow_type_manual INTEGER DEFAULT 0,  -- 1 = user overrode classifier; preserve on recategorize
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    book TEXT,                     -- whose spending: a book's name (the books table); NULL reads as Household
    type_id INTEGER REFERENCES types(id),  -- what kind of spending, or for income its kind; NULL = not placed
    amount_minor INTEGER,          -- the amount in whole minor units of the account's currency: positive = money out, negative = money in
    other_side_id INTEGER REFERENCES accounts(id),  -- the account a movement, transfer or payment names as its other side; NULL = none named. An existing database gains it through convert_movements.py
    book_source TEXT CHECK (book_source IN ('merchant', 'rule', 'wording', 'checked', 'chosen', 'derived')),  -- where the book came from (declared in book_type.py, SOURCES); NULL when the book is empty, but 'chosen' for one left empty by hand (the review list's label); a book with a value and no source reads as from the merchant (label_source). An existing database gains it through convert_label_source.py
    type_source TEXT CHECK (type_source IN ('merchant', 'rule', 'wording', 'checked', 'chosen', 'derived')),  -- where the type came from, as book_source; NULL when the type is empty, but 'chosen' for one left empty by hand
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
    review_each_time INTEGER DEFAULT 0,  -- 1 = a mixed merchant: its rows take the default type and are flagged for a look each time
    role TEXT                           -- the part the merchant plays as a party (declared in party.py); NULL = it says none. An existing database gains it through convert_party_link.py
);

-- The type list: one list for every book, with one level of sub-type, and
-- the short list of income kinds. A new book's is filled from the declaration
-- in book_type.py; once a book exists the table is the home of each type's
-- wording, and the app may add a spending type or fix one's wording (fin
-- RUL-002). Tracked by the change history.
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

-- The stable key of each type and income kind book_type.py declares: its
-- declared name, frozen, so code finds the type it means however the type is
-- named on screen (type-relationships 01). Filled at start by seed_types,
-- which matches declared names on a book that has no key yet; a type added
-- in the app has none. Tracked by the change history. A new table, made by
-- CREATE IF NOT EXISTS on start; no existing row is converted.
CREATE TABLE IF NOT EXISTS type_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,             -- the type's kind: 'spending' or 'income'
    key TEXT NOT NULL,              -- the declared name, frozen
    type_id INTEGER NOT NULL UNIQUE REFERENCES types(id),
    UNIQUE (kind, key)
);

-- The books (fin RUL-021): whose spending a row is. A book is data: Household,
-- Moom and Kalesh are seeded once from book_type.BOOKS, never overwriting, and
-- the operator adds a book on Lists › Books (book_type.add_book). Every reader,
-- chat tool and owner rule reads the list from here, in id order, Household
-- first. A row, merchant, rule, let-go and type setting holds a book by its
-- name; an account's owner is Household or a book. Renaming and removing a
-- book stay code changes. Tracked by the change history. A new table, made by
-- CREATE IF NOT EXISTS on start; no existing row is converted.
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE,  -- shown as written; unique ignoring case
    description TEXT NOT NULL,                 -- plain words: whose costs it holds
    created_at TEXT DEFAULT (datetime('now'))
);

-- A spending type's settings (type-relationships 02): its usual book, the
-- one a new merchant of it is proposed (a book in the books table, or
-- 'ask': the operator says), and whether a new merchant of it starts as Check
-- each row. A change reaches new merchants only. Seeded at start for every
-- declared spending type from what the code said before, never overwriting
-- (book_type.seed_settings); a type added in the app has a row once one is
-- set, and reads Household with every row the same until then. One-off by
-- default stays types.default_one_off. Tracked by the change history. A new
-- table, made by CREATE IF NOT EXISTS on start; no existing row is converted.
CREATE TABLE IF NOT EXISTS type_settings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type_id INTEGER NOT NULL UNIQUE REFERENCES types(id),
    usual_book TEXT NOT NULL,                  -- a book's name, or 'ask' (book_type.ASK)
    check_each_row INTEGER NOT NULL DEFAULT 0  -- 1: a new merchant of this type starts as Check each row
);

-- fin's own filings (type-relationships 03): the type each thing fin files
-- itself goes under, one row per slot book_type.SLOTS declares: loan_interest
-- (the interest loan_interest.py works out) and gift_given (the review list's
-- gift, a let-go gift), each a spending type; gift_received (the review
-- list's gift on money in), an income kind. Seeded at start from what the
-- code said before, never overwriting (book_type.seed_slots); moved in the
-- app. Tracked by the change history. A new table, made by CREATE IF NOT
-- EXISTS on start; no existing row is converted.
CREATE TABLE IF NOT EXISTS type_slots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot TEXT NOT NULL UNIQUE,                     -- book_type.SLOTS
    type_id INTEGER NOT NULL REFERENCES types(id)
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
    result_json TEXT,                 -- while status is 'preview': the preview's facts confirm writes; after: JSON summary (or the error)
    created_at TEXT DEFAULT (datetime('now'))
);

-- What the screens keep between visits (screens.py): the last change the
-- operator looked at in the history. (The "Claude may write" switch is not
-- here: chat_writes.py keeps it beside the book.) Not a tracked table:
-- looking is not a change to the book.
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

-- Expect back (personal-ledger 23): an amount on one row that is not the
-- household's to keep or spend, and the records that settle it
-- (expect_back.py). Amounts are whole minor units, always positive: the
-- direction and the row's own sign say which way the money went. A row's own
-- part (its amount less what is expected and placed on it) is never stored:
-- the view row_parts below is its one home. Every table here is tracked by
-- the change history; an existing database gains them, and their triggers,
-- through convert_change_history.py.

-- An expectation: part or all of one row expected back (money out) or to pay
-- back (money in), in that row's account currency. One per row. An opening
-- figure has no row and names its person account and anchor date instead.
CREATE TABLE IF NOT EXISTS expectation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id INTEGER UNIQUE REFERENCES transactions(id),    -- NULL only for an opening figure
    person_id INTEGER REFERENCES accounts(id),            -- an opening figure's person account
    opening_date TEXT,                                    -- an opening figure's anchor date, YYYY-MM-DD
    direction TEXT NOT NULL CHECK (direction IN ('back', 'pay')),  -- back: on money out; pay: on money in
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),         -- no more than the row's amount (checked at write)
    currency TEXT NOT NULL,                               -- the row's account currency (checked at write)
    note TEXT,
    made_at TEXT NOT NULL DEFAULT (datetime('now')),
    source TEXT NOT NULL DEFAULT 'hand' CHECK (source IN ('hand', 'converted')),
    CHECK ((row_id IS NOT NULL AND person_id IS NULL AND opening_date IS NULL)
        OR (row_id IS NULL AND person_id IS NOT NULL AND opening_date IS NOT NULL))
);

-- A name on an expectation: a person account it is expected from (or owed to).
CREATE TABLE IF NOT EXISTS expectation_name (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expectation_id INTEGER NOT NULL REFERENCES expectation(id),
    person_id INTEGER NOT NULL REFERENCES accounts(id),
    UNIQUE (expectation_id, person_id)
);

-- A set-off: one payment row placed, wholly or partly, against one
-- expectation, or waiting for its row (no expectation yet; a person and a
-- note instead). The payment row has the opposite sign to the expectation's
-- row. amount_minor is in the payment's currency, item_amount_minor in the
-- expectation's; they differ only across currencies, at the rate kept here.
CREATE TABLE IF NOT EXISTS set_off (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    payment_row_id INTEGER NOT NULL REFERENCES transactions(id),
    expectation_id INTEGER REFERENCES expectation(id),    -- NULL while waiting for its row
    person_id INTEGER REFERENCES accounts(id),            -- waiting: who it is from
    note TEXT,
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),
    item_amount_minor INTEGER NOT NULL CHECK (item_amount_minor > 0),
    rate TEXT,                                            -- decimal text, when the currencies differ
    rate_date TEXT,                                       -- the day that rate is of
    made_at TEXT NOT NULL DEFAULT (datetime('now')),
    source TEXT NOT NULL DEFAULT 'hand' CHECK (source IN ('hand', 'suggested', 'refund', 'converted')),
    CHECK ((rate IS NULL) = (rate_date IS NULL)),
    CHECK (rate IS NOT NULL OR item_amount_minor = amount_minor)
);

-- A let-go: an expectation's whole open rest closed by the operator, as
-- spending ('mine', the row's book and type) or a gift, counted on its own day.
CREATE TABLE IF NOT EXISTS let_go (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expectation_id INTEGER NOT NULL UNIQUE REFERENCES expectation(id),
    amount_minor INTEGER NOT NULL CHECK (amount_minor > 0),  -- the whole open rest, in the expectation's currency
    became TEXT NOT NULL CHECK (became IN ('mine', 'gift')),
    book TEXT NOT NULL,
    type_id INTEGER NOT NULL REFERENCES types(id),
    to_whom_id INTEGER REFERENCES accounts(id),           -- a gift's person, when named
    counts_on TEXT NOT NULL,                              -- YYYY-MM-DD, the day it counts
    made_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (became = 'gift' OR to_whom_id IS NULL)
);

-- A refund link: one refund row and the purchase it refunds.
CREATE TABLE IF NOT EXISTS refund_link (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    refund_row_id INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
    purchase_row_id INTEGER NOT NULL REFERENCES transactions(id),
    source TEXT NOT NULL CHECK (source IN ('auto', 'hand')),
    made_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (refund_row_id != purchase_row_id)
);

-- A sender wording: the cleaned words a payment's text carries, learnt for
-- one person.
CREATE TABLE IF NOT EXISTS sender_wording (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wording TEXT NOT NULL UNIQUE,
    person_id INTEGER NOT NULL REFERENCES accounts(id),
    learnt_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- A dismissal: Not a payback on a money-in row ('payback'), or an Unlink of a
-- refund link that the linker must not make again ('refund_link', naming the
-- purchase).
CREATE TABLE IF NOT EXISTS dismissal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id INTEGER NOT NULL REFERENCES transactions(id),
    kind TEXT NOT NULL CHECK (kind IN ('payback', 'refund_link')),
    purchase_row_id INTEGER REFERENCES transactions(id),
    made_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (row_id, kind, purchase_row_id),
    CHECK ((kind = 'refund_link') = (purchase_row_id IS NOT NULL))
);

-- A folio reference (folio_link.py): a row, an account or a party (a
-- merchant) carries at most one read-only link to its folio record, an LP's
-- page ('lp') or a row of folio's ledger ('tx'), by folio's own id. Nothing
-- in folio is read or written. A new table, made empty by CREATE IF NOT
-- EXISTS on start, its triggers with it (history.ADDED_ON_START); no row is
-- converted. The subject is named by id, with no foreign key, so taking a
-- row out (an import undone) is never blocked by its link.
CREATE TABLE IF NOT EXISTS folio_link (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    subject TEXT NOT NULL CHECK (subject IN ('row', 'account', 'party')),  -- declared in folio_link.py
    subject_id INTEGER NOT NULL,      -- transactions.id, accounts.id or services.id
    target TEXT NOT NULL CHECK (target IN ('lp', 'tx')),  -- declared in folio_link.py
    folio_id INTEGER NOT NULL CHECK (folio_id > 0),
    UNIQUE (subject, subject_id)
);

-- These indexes are made on start (db.init_db), after the tables exist. They
-- are written with a space before the column list: retire_categories.py puts
-- back every index written without one, on a database older than these tables.
CREATE INDEX IF NOT EXISTS idx_expectation_name_expectation ON expectation_name (expectation_id);
CREATE INDEX IF NOT EXISTS idx_set_off_payment ON set_off (payment_row_id);
CREATE INDEX IF NOT EXISTS idx_set_off_expectation ON set_off (expectation_id);
CREATE INDEX IF NOT EXISTS idx_refund_link_purchase ON refund_link (purchase_row_id);
-- One Not a payback per row: UNIQUE above lets NULL purchases repeat.
CREATE UNIQUE INDEX IF NOT EXISTS idx_dismissal_one ON dismissal(row_id, kind, IFNULL(purchase_row_id, 0));

-- A row's parts: what is expected on it (back or to pay back), what it places
-- as a payment, and its own part, the rest. Each carries the row's sign, so
-- own_minor = amount_minor - expected_minor - placed_minor in the row's
-- account currency. The one home of the formula: readers take the own part
-- through expect_back.own_sql(), never a copy.
CREATE VIEW IF NOT EXISTS row_parts AS
SELECT row_id, amount_minor, expected_minor, placed_minor,
       amount_minor - expected_minor - placed_minor AS own_minor
FROM (
    SELECT t.id AS row_id,
           t.amount_minor AS amount_minor,
           (CASE WHEN t.amount_minor < 0 THEN -1 ELSE 1 END)
             * COALESCE((SELECT e.amount_minor FROM expectation e WHERE e.row_id = t.id), 0) AS expected_minor,
           (CASE WHEN t.amount_minor < 0 THEN -1 ELSE 1 END)
             * COALESCE((SELECT SUM(so.amount_minor) FROM set_off so WHERE so.payment_row_id = t.id), 0) AS placed_minor
    FROM transactions t
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
        json_object('id', NEW.id, 'statement_id', NEW.statement_id, 'date', NEW.date, 'description', NEW.description, 'amount_foreign', NEW.amount_foreign, 'currency_foreign', NEW.currency_foreign, 'service_id', NEW.service_id, 'is_one_off', NEW.is_one_off, 'flow_type', NEW.flow_type, 'flow_type_manual', NEW.flow_type_manual, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'amount_minor', NEW.amount_minor, 'other_side_id', NEW.other_side_id, 'book_source', NEW.book_source, 'type_source', NEW.type_source));
END;
CREATE TRIGGER IF NOT EXISTS history_transactions_update AFTER UPDATE ON transactions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'statement_id', OLD.statement_id, 'date', OLD.date, 'description', OLD.description, 'amount_foreign', OLD.amount_foreign, 'currency_foreign', OLD.currency_foreign, 'service_id', OLD.service_id, 'is_one_off', OLD.is_one_off, 'flow_type', OLD.flow_type, 'flow_type_manual', OLD.flow_type_manual, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'amount_minor', OLD.amount_minor, 'other_side_id', OLD.other_side_id, 'book_source', OLD.book_source, 'type_source', OLD.type_source) IS NOT json_object('id', NEW.id, 'statement_id', NEW.statement_id, 'date', NEW.date, 'description', NEW.description, 'amount_foreign', NEW.amount_foreign, 'currency_foreign', NEW.currency_foreign, 'service_id', NEW.service_id, 'is_one_off', NEW.is_one_off, 'flow_type', NEW.flow_type, 'flow_type_manual', NEW.flow_type_manual, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'amount_minor', NEW.amount_minor, 'other_side_id', NEW.other_side_id, 'book_source', NEW.book_source, 'type_source', NEW.type_source)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'transactions', NEW.id, 'update',
        json_object('id', OLD.id, 'statement_id', OLD.statement_id, 'date', OLD.date, 'description', OLD.description, 'amount_foreign', OLD.amount_foreign, 'currency_foreign', OLD.currency_foreign, 'service_id', OLD.service_id, 'is_one_off', OLD.is_one_off, 'flow_type', OLD.flow_type, 'flow_type_manual', OLD.flow_type_manual, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'amount_minor', OLD.amount_minor, 'other_side_id', OLD.other_side_id, 'book_source', OLD.book_source, 'type_source', OLD.type_source),
        json_object('id', NEW.id, 'statement_id', NEW.statement_id, 'date', NEW.date, 'description', NEW.description, 'amount_foreign', NEW.amount_foreign, 'currency_foreign', NEW.currency_foreign, 'service_id', NEW.service_id, 'is_one_off', NEW.is_one_off, 'flow_type', NEW.flow_type, 'flow_type_manual', NEW.flow_type_manual, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'amount_minor', NEW.amount_minor, 'other_side_id', NEW.other_side_id, 'book_source', NEW.book_source, 'type_source', NEW.type_source));
END;
CREATE TRIGGER IF NOT EXISTS history_transactions_delete AFTER DELETE ON transactions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'transactions', OLD.id, 'delete',
        json_object('id', OLD.id, 'statement_id', OLD.statement_id, 'date', OLD.date, 'description', OLD.description, 'amount_foreign', OLD.amount_foreign, 'currency_foreign', OLD.currency_foreign, 'service_id', OLD.service_id, 'is_one_off', OLD.is_one_off, 'flow_type', OLD.flow_type, 'flow_type_manual', OLD.flow_type_manual, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'amount_minor', OLD.amount_minor, 'other_side_id', OLD.other_side_id, 'book_source', OLD.book_source, 'type_source', OLD.type_source), NULL);
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
        json_object('id', NEW.id, 'name', NEW.name, 'is_one_off', NEW.is_one_off, 'exclude_from_expense_views', NEW.exclude_from_expense_views, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'review_each_time', NEW.review_each_time, 'role', NEW.role));
END;
CREATE TRIGGER IF NOT EXISTS history_services_update AFTER UPDATE ON services
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'name', OLD.name, 'is_one_off', OLD.is_one_off, 'exclude_from_expense_views', OLD.exclude_from_expense_views, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'review_each_time', OLD.review_each_time, 'role', OLD.role) IS NOT json_object('id', NEW.id, 'name', NEW.name, 'is_one_off', NEW.is_one_off, 'exclude_from_expense_views', NEW.exclude_from_expense_views, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'review_each_time', NEW.review_each_time, 'role', NEW.role)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'services', NEW.id, 'update',
        json_object('id', OLD.id, 'name', OLD.name, 'is_one_off', OLD.is_one_off, 'exclude_from_expense_views', OLD.exclude_from_expense_views, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'review_each_time', OLD.review_each_time, 'role', OLD.role),
        json_object('id', NEW.id, 'name', NEW.name, 'is_one_off', NEW.is_one_off, 'exclude_from_expense_views', NEW.exclude_from_expense_views, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'review_each_time', NEW.review_each_time, 'role', NEW.role));
END;
CREATE TRIGGER IF NOT EXISTS history_services_delete AFTER DELETE ON services
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'services', OLD.id, 'delete',
        json_object('id', OLD.id, 'name', OLD.name, 'is_one_off', OLD.is_one_off, 'exclude_from_expense_views', OLD.exclude_from_expense_views, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'review_each_time', OLD.review_each_time, 'role', OLD.role), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_merchant_rules_insert AFTER INSERT ON merchant_rules
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'merchant_rules', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'pattern', NEW.pattern, 'service_id', NEW.service_id, 'match_type', NEW.match_type, 'confidence', NEW.confidence, 'priority', NEW.priority, 'created_at', NEW.created_at, 'book_override', NEW.book_override, 'type_override_id', NEW.type_override_id, 'min_amount_minor', NEW.min_amount_minor, 'max_amount_minor', NEW.max_amount_minor, 'flow', NEW.flow, 'other_side_id', NEW.other_side_id));
END;
CREATE TRIGGER IF NOT EXISTS history_merchant_rules_update AFTER UPDATE ON merchant_rules
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'pattern', OLD.pattern, 'service_id', OLD.service_id, 'match_type', OLD.match_type, 'confidence', OLD.confidence, 'priority', OLD.priority, 'created_at', OLD.created_at, 'book_override', OLD.book_override, 'type_override_id', OLD.type_override_id, 'min_amount_minor', OLD.min_amount_minor, 'max_amount_minor', OLD.max_amount_minor, 'flow', OLD.flow, 'other_side_id', OLD.other_side_id) IS NOT json_object('id', NEW.id, 'pattern', NEW.pattern, 'service_id', NEW.service_id, 'match_type', NEW.match_type, 'confidence', NEW.confidence, 'priority', NEW.priority, 'created_at', NEW.created_at, 'book_override', NEW.book_override, 'type_override_id', NEW.type_override_id, 'min_amount_minor', NEW.min_amount_minor, 'max_amount_minor', NEW.max_amount_minor, 'flow', NEW.flow, 'other_side_id', NEW.other_side_id)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'merchant_rules', NEW.id, 'update',
        json_object('id', OLD.id, 'pattern', OLD.pattern, 'service_id', OLD.service_id, 'match_type', OLD.match_type, 'confidence', OLD.confidence, 'priority', OLD.priority, 'created_at', OLD.created_at, 'book_override', OLD.book_override, 'type_override_id', OLD.type_override_id, 'min_amount_minor', OLD.min_amount_minor, 'max_amount_minor', OLD.max_amount_minor, 'flow', OLD.flow, 'other_side_id', OLD.other_side_id),
        json_object('id', NEW.id, 'pattern', NEW.pattern, 'service_id', NEW.service_id, 'match_type', NEW.match_type, 'confidence', NEW.confidence, 'priority', NEW.priority, 'created_at', NEW.created_at, 'book_override', NEW.book_override, 'type_override_id', NEW.type_override_id, 'min_amount_minor', NEW.min_amount_minor, 'max_amount_minor', NEW.max_amount_minor, 'flow', NEW.flow, 'other_side_id', NEW.other_side_id));
END;
CREATE TRIGGER IF NOT EXISTS history_merchant_rules_delete AFTER DELETE ON merchant_rules
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'merchant_rules', OLD.id, 'delete',
        json_object('id', OLD.id, 'pattern', OLD.pattern, 'service_id', OLD.service_id, 'match_type', OLD.match_type, 'confidence', OLD.confidence, 'priority', OLD.priority, 'created_at', OLD.created_at, 'book_override', OLD.book_override, 'type_override_id', OLD.type_override_id, 'min_amount_minor', OLD.min_amount_minor, 'max_amount_minor', OLD.max_amount_minor, 'flow', OLD.flow, 'other_side_id', OLD.other_side_id), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_accounts_insert AFTER INSERT ON accounts
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'accounts', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'name', NEW.name, 'short_name', NEW.short_name, 'type', NEW.type, 'last_four', NEW.last_four, 'currency', NEW.currency, 'status', NEW.status, 'created_at', NEW.created_at, 'owner', NEW.owner, 'party_id', NEW.party_id));
END;
CREATE TRIGGER IF NOT EXISTS history_accounts_update AFTER UPDATE ON accounts
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'name', OLD.name, 'short_name', OLD.short_name, 'type', OLD.type, 'last_four', OLD.last_four, 'currency', OLD.currency, 'status', OLD.status, 'created_at', OLD.created_at, 'owner', OLD.owner, 'party_id', OLD.party_id) IS NOT json_object('id', NEW.id, 'name', NEW.name, 'short_name', NEW.short_name, 'type', NEW.type, 'last_four', NEW.last_four, 'currency', NEW.currency, 'status', NEW.status, 'created_at', NEW.created_at, 'owner', NEW.owner, 'party_id', NEW.party_id)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'accounts', NEW.id, 'update',
        json_object('id', OLD.id, 'name', OLD.name, 'short_name', OLD.short_name, 'type', OLD.type, 'last_four', OLD.last_four, 'currency', OLD.currency, 'status', OLD.status, 'created_at', OLD.created_at, 'owner', OLD.owner, 'party_id', OLD.party_id),
        json_object('id', NEW.id, 'name', NEW.name, 'short_name', NEW.short_name, 'type', NEW.type, 'last_four', NEW.last_four, 'currency', NEW.currency, 'status', NEW.status, 'created_at', NEW.created_at, 'owner', NEW.owner, 'party_id', NEW.party_id));
END;
CREATE TRIGGER IF NOT EXISTS history_accounts_delete AFTER DELETE ON accounts
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'accounts', OLD.id, 'delete',
        json_object('id', OLD.id, 'name', OLD.name, 'short_name', OLD.short_name, 'type', OLD.type, 'last_four', OLD.last_four, 'currency', OLD.currency, 'status', OLD.status, 'created_at', OLD.created_at, 'owner', OLD.owner, 'party_id', OLD.party_id), NULL);
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
        json_object('id', NEW.id, 'account_id', NEW.account_id, 'statement_date', NEW.statement_date, 'filename', NEW.filename, 'imported_at', NEW.imported_at, 'printed', NEW.printed, 'period_start', NEW.period_start));
END;
CREATE TRIGGER IF NOT EXISTS history_statements_update AFTER UPDATE ON statements
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'account_id', OLD.account_id, 'statement_date', OLD.statement_date, 'filename', OLD.filename, 'imported_at', OLD.imported_at, 'printed', OLD.printed, 'period_start', OLD.period_start) IS NOT json_object('id', NEW.id, 'account_id', NEW.account_id, 'statement_date', NEW.statement_date, 'filename', NEW.filename, 'imported_at', NEW.imported_at, 'printed', NEW.printed, 'period_start', NEW.period_start)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'statements', NEW.id, 'update',
        json_object('id', OLD.id, 'account_id', OLD.account_id, 'statement_date', OLD.statement_date, 'filename', OLD.filename, 'imported_at', OLD.imported_at, 'printed', OLD.printed, 'period_start', OLD.period_start),
        json_object('id', NEW.id, 'account_id', NEW.account_id, 'statement_date', NEW.statement_date, 'filename', NEW.filename, 'imported_at', NEW.imported_at, 'printed', NEW.printed, 'period_start', NEW.period_start));
END;
CREATE TRIGGER IF NOT EXISTS history_statements_delete AFTER DELETE ON statements
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'statements', OLD.id, 'delete',
        json_object('id', OLD.id, 'account_id', OLD.account_id, 'statement_date', OLD.statement_date, 'filename', OLD.filename, 'imported_at', OLD.imported_at, 'printed', OLD.printed, 'period_start', OLD.period_start), NULL);
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
CREATE TRIGGER IF NOT EXISTS history_expectation_insert AFTER INSERT ON expectation
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'expectation', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'row_id', NEW.row_id, 'person_id', NEW.person_id, 'opening_date', NEW.opening_date, 'direction', NEW.direction, 'amount_minor', NEW.amount_minor, 'currency', NEW.currency, 'note', NEW.note, 'made_at', NEW.made_at, 'source', NEW.source));
END;
CREATE TRIGGER IF NOT EXISTS history_expectation_update AFTER UPDATE ON expectation
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'row_id', OLD.row_id, 'person_id', OLD.person_id, 'opening_date', OLD.opening_date, 'direction', OLD.direction, 'amount_minor', OLD.amount_minor, 'currency', OLD.currency, 'note', OLD.note, 'made_at', OLD.made_at, 'source', OLD.source) IS NOT json_object('id', NEW.id, 'row_id', NEW.row_id, 'person_id', NEW.person_id, 'opening_date', NEW.opening_date, 'direction', NEW.direction, 'amount_minor', NEW.amount_minor, 'currency', NEW.currency, 'note', NEW.note, 'made_at', NEW.made_at, 'source', NEW.source)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'expectation', NEW.id, 'update',
        json_object('id', OLD.id, 'row_id', OLD.row_id, 'person_id', OLD.person_id, 'opening_date', OLD.opening_date, 'direction', OLD.direction, 'amount_minor', OLD.amount_minor, 'currency', OLD.currency, 'note', OLD.note, 'made_at', OLD.made_at, 'source', OLD.source),
        json_object('id', NEW.id, 'row_id', NEW.row_id, 'person_id', NEW.person_id, 'opening_date', NEW.opening_date, 'direction', NEW.direction, 'amount_minor', NEW.amount_minor, 'currency', NEW.currency, 'note', NEW.note, 'made_at', NEW.made_at, 'source', NEW.source));
END;
CREATE TRIGGER IF NOT EXISTS history_expectation_delete AFTER DELETE ON expectation
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'expectation', OLD.id, 'delete',
        json_object('id', OLD.id, 'row_id', OLD.row_id, 'person_id', OLD.person_id, 'opening_date', OLD.opening_date, 'direction', OLD.direction, 'amount_minor', OLD.amount_minor, 'currency', OLD.currency, 'note', OLD.note, 'made_at', OLD.made_at, 'source', OLD.source), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_expectation_name_insert AFTER INSERT ON expectation_name
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'expectation_name', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'expectation_id', NEW.expectation_id, 'person_id', NEW.person_id));
END;
CREATE TRIGGER IF NOT EXISTS history_expectation_name_update AFTER UPDATE ON expectation_name
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'expectation_id', OLD.expectation_id, 'person_id', OLD.person_id) IS NOT json_object('id', NEW.id, 'expectation_id', NEW.expectation_id, 'person_id', NEW.person_id)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'expectation_name', NEW.id, 'update',
        json_object('id', OLD.id, 'expectation_id', OLD.expectation_id, 'person_id', OLD.person_id),
        json_object('id', NEW.id, 'expectation_id', NEW.expectation_id, 'person_id', NEW.person_id));
END;
CREATE TRIGGER IF NOT EXISTS history_expectation_name_delete AFTER DELETE ON expectation_name
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'expectation_name', OLD.id, 'delete',
        json_object('id', OLD.id, 'expectation_id', OLD.expectation_id, 'person_id', OLD.person_id), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_set_off_insert AFTER INSERT ON set_off
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'set_off', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'payment_row_id', NEW.payment_row_id, 'expectation_id', NEW.expectation_id, 'person_id', NEW.person_id, 'note', NEW.note, 'amount_minor', NEW.amount_minor, 'item_amount_minor', NEW.item_amount_minor, 'rate', NEW.rate, 'rate_date', NEW.rate_date, 'made_at', NEW.made_at, 'source', NEW.source));
END;
CREATE TRIGGER IF NOT EXISTS history_set_off_update AFTER UPDATE ON set_off
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'payment_row_id', OLD.payment_row_id, 'expectation_id', OLD.expectation_id, 'person_id', OLD.person_id, 'note', OLD.note, 'amount_minor', OLD.amount_minor, 'item_amount_minor', OLD.item_amount_minor, 'rate', OLD.rate, 'rate_date', OLD.rate_date, 'made_at', OLD.made_at, 'source', OLD.source) IS NOT json_object('id', NEW.id, 'payment_row_id', NEW.payment_row_id, 'expectation_id', NEW.expectation_id, 'person_id', NEW.person_id, 'note', NEW.note, 'amount_minor', NEW.amount_minor, 'item_amount_minor', NEW.item_amount_minor, 'rate', NEW.rate, 'rate_date', NEW.rate_date, 'made_at', NEW.made_at, 'source', NEW.source)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'set_off', NEW.id, 'update',
        json_object('id', OLD.id, 'payment_row_id', OLD.payment_row_id, 'expectation_id', OLD.expectation_id, 'person_id', OLD.person_id, 'note', OLD.note, 'amount_minor', OLD.amount_minor, 'item_amount_minor', OLD.item_amount_minor, 'rate', OLD.rate, 'rate_date', OLD.rate_date, 'made_at', OLD.made_at, 'source', OLD.source),
        json_object('id', NEW.id, 'payment_row_id', NEW.payment_row_id, 'expectation_id', NEW.expectation_id, 'person_id', NEW.person_id, 'note', NEW.note, 'amount_minor', NEW.amount_minor, 'item_amount_minor', NEW.item_amount_minor, 'rate', NEW.rate, 'rate_date', NEW.rate_date, 'made_at', NEW.made_at, 'source', NEW.source));
END;
CREATE TRIGGER IF NOT EXISTS history_set_off_delete AFTER DELETE ON set_off
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'set_off', OLD.id, 'delete',
        json_object('id', OLD.id, 'payment_row_id', OLD.payment_row_id, 'expectation_id', OLD.expectation_id, 'person_id', OLD.person_id, 'note', OLD.note, 'amount_minor', OLD.amount_minor, 'item_amount_minor', OLD.item_amount_minor, 'rate', OLD.rate, 'rate_date', OLD.rate_date, 'made_at', OLD.made_at, 'source', OLD.source), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_let_go_insert AFTER INSERT ON let_go
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'let_go', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'expectation_id', NEW.expectation_id, 'amount_minor', NEW.amount_minor, 'became', NEW.became, 'book', NEW.book, 'type_id', NEW.type_id, 'to_whom_id', NEW.to_whom_id, 'counts_on', NEW.counts_on, 'made_at', NEW.made_at));
END;
CREATE TRIGGER IF NOT EXISTS history_let_go_update AFTER UPDATE ON let_go
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'expectation_id', OLD.expectation_id, 'amount_minor', OLD.amount_minor, 'became', OLD.became, 'book', OLD.book, 'type_id', OLD.type_id, 'to_whom_id', OLD.to_whom_id, 'counts_on', OLD.counts_on, 'made_at', OLD.made_at) IS NOT json_object('id', NEW.id, 'expectation_id', NEW.expectation_id, 'amount_minor', NEW.amount_minor, 'became', NEW.became, 'book', NEW.book, 'type_id', NEW.type_id, 'to_whom_id', NEW.to_whom_id, 'counts_on', NEW.counts_on, 'made_at', NEW.made_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'let_go', NEW.id, 'update',
        json_object('id', OLD.id, 'expectation_id', OLD.expectation_id, 'amount_minor', OLD.amount_minor, 'became', OLD.became, 'book', OLD.book, 'type_id', OLD.type_id, 'to_whom_id', OLD.to_whom_id, 'counts_on', OLD.counts_on, 'made_at', OLD.made_at),
        json_object('id', NEW.id, 'expectation_id', NEW.expectation_id, 'amount_minor', NEW.amount_minor, 'became', NEW.became, 'book', NEW.book, 'type_id', NEW.type_id, 'to_whom_id', NEW.to_whom_id, 'counts_on', NEW.counts_on, 'made_at', NEW.made_at));
END;
CREATE TRIGGER IF NOT EXISTS history_let_go_delete AFTER DELETE ON let_go
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'let_go', OLD.id, 'delete',
        json_object('id', OLD.id, 'expectation_id', OLD.expectation_id, 'amount_minor', OLD.amount_minor, 'became', OLD.became, 'book', OLD.book, 'type_id', OLD.type_id, 'to_whom_id', OLD.to_whom_id, 'counts_on', OLD.counts_on, 'made_at', OLD.made_at), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_refund_link_insert AFTER INSERT ON refund_link
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'refund_link', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'refund_row_id', NEW.refund_row_id, 'purchase_row_id', NEW.purchase_row_id, 'source', NEW.source, 'made_at', NEW.made_at));
END;
CREATE TRIGGER IF NOT EXISTS history_refund_link_update AFTER UPDATE ON refund_link
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'refund_row_id', OLD.refund_row_id, 'purchase_row_id', OLD.purchase_row_id, 'source', OLD.source, 'made_at', OLD.made_at) IS NOT json_object('id', NEW.id, 'refund_row_id', NEW.refund_row_id, 'purchase_row_id', NEW.purchase_row_id, 'source', NEW.source, 'made_at', NEW.made_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'refund_link', NEW.id, 'update',
        json_object('id', OLD.id, 'refund_row_id', OLD.refund_row_id, 'purchase_row_id', OLD.purchase_row_id, 'source', OLD.source, 'made_at', OLD.made_at),
        json_object('id', NEW.id, 'refund_row_id', NEW.refund_row_id, 'purchase_row_id', NEW.purchase_row_id, 'source', NEW.source, 'made_at', NEW.made_at));
END;
CREATE TRIGGER IF NOT EXISTS history_refund_link_delete AFTER DELETE ON refund_link
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'refund_link', OLD.id, 'delete',
        json_object('id', OLD.id, 'refund_row_id', OLD.refund_row_id, 'purchase_row_id', OLD.purchase_row_id, 'source', OLD.source, 'made_at', OLD.made_at), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_sender_wording_insert AFTER INSERT ON sender_wording
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'sender_wording', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'wording', NEW.wording, 'person_id', NEW.person_id, 'learnt_at', NEW.learnt_at));
END;
CREATE TRIGGER IF NOT EXISTS history_sender_wording_update AFTER UPDATE ON sender_wording
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'wording', OLD.wording, 'person_id', OLD.person_id, 'learnt_at', OLD.learnt_at) IS NOT json_object('id', NEW.id, 'wording', NEW.wording, 'person_id', NEW.person_id, 'learnt_at', NEW.learnt_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'sender_wording', NEW.id, 'update',
        json_object('id', OLD.id, 'wording', OLD.wording, 'person_id', OLD.person_id, 'learnt_at', OLD.learnt_at),
        json_object('id', NEW.id, 'wording', NEW.wording, 'person_id', NEW.person_id, 'learnt_at', NEW.learnt_at));
END;
CREATE TRIGGER IF NOT EXISTS history_sender_wording_delete AFTER DELETE ON sender_wording
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'sender_wording', OLD.id, 'delete',
        json_object('id', OLD.id, 'wording', OLD.wording, 'person_id', OLD.person_id, 'learnt_at', OLD.learnt_at), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_dismissal_insert AFTER INSERT ON dismissal
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'dismissal', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'row_id', NEW.row_id, 'kind', NEW.kind, 'purchase_row_id', NEW.purchase_row_id, 'made_at', NEW.made_at));
END;
CREATE TRIGGER IF NOT EXISTS history_dismissal_update AFTER UPDATE ON dismissal
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'row_id', OLD.row_id, 'kind', OLD.kind, 'purchase_row_id', OLD.purchase_row_id, 'made_at', OLD.made_at) IS NOT json_object('id', NEW.id, 'row_id', NEW.row_id, 'kind', NEW.kind, 'purchase_row_id', NEW.purchase_row_id, 'made_at', NEW.made_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'dismissal', NEW.id, 'update',
        json_object('id', OLD.id, 'row_id', OLD.row_id, 'kind', OLD.kind, 'purchase_row_id', OLD.purchase_row_id, 'made_at', OLD.made_at),
        json_object('id', NEW.id, 'row_id', NEW.row_id, 'kind', NEW.kind, 'purchase_row_id', NEW.purchase_row_id, 'made_at', NEW.made_at));
END;
CREATE TRIGGER IF NOT EXISTS history_dismissal_delete AFTER DELETE ON dismissal
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'dismissal', OLD.id, 'delete',
        json_object('id', OLD.id, 'row_id', OLD.row_id, 'kind', OLD.kind, 'purchase_row_id', OLD.purchase_row_id, 'made_at', OLD.made_at), NULL);
END;
-- Types in the app (fin RUL-002). The table predates its triggers, so a
-- book gains them on its next start (history.ADDED_ON_START).
CREATE TRIGGER IF NOT EXISTS history_types_insert AFTER INSERT ON types
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'types', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'kind', NEW.kind, 'name', NEW.name, 'parent_id', NEW.parent_id, 'default_one_off', NEW.default_one_off, 'covers', NEW.covers, 'not_for', NEW.not_for, 'created_at', NEW.created_at));
END;
CREATE TRIGGER IF NOT EXISTS history_types_update AFTER UPDATE ON types
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'kind', OLD.kind, 'name', OLD.name, 'parent_id', OLD.parent_id, 'default_one_off', OLD.default_one_off, 'covers', OLD.covers, 'not_for', OLD.not_for, 'created_at', OLD.created_at) IS NOT json_object('id', NEW.id, 'kind', NEW.kind, 'name', NEW.name, 'parent_id', NEW.parent_id, 'default_one_off', NEW.default_one_off, 'covers', NEW.covers, 'not_for', NEW.not_for, 'created_at', NEW.created_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'types', NEW.id, 'update',
        json_object('id', OLD.id, 'kind', OLD.kind, 'name', OLD.name, 'parent_id', OLD.parent_id, 'default_one_off', OLD.default_one_off, 'covers', OLD.covers, 'not_for', OLD.not_for, 'created_at', OLD.created_at),
        json_object('id', NEW.id, 'kind', NEW.kind, 'name', NEW.name, 'parent_id', NEW.parent_id, 'default_one_off', NEW.default_one_off, 'covers', NEW.covers, 'not_for', NEW.not_for, 'created_at', NEW.created_at));
END;
CREATE TRIGGER IF NOT EXISTS history_types_delete AFTER DELETE ON types
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'types', OLD.id, 'delete',
        json_object('id', OLD.id, 'kind', OLD.kind, 'name', OLD.name, 'parent_id', OLD.parent_id, 'default_one_off', OLD.default_one_off, 'covers', OLD.covers, 'not_for', OLD.not_for, 'created_at', OLD.created_at), NULL);
END;
-- Type keys (type-relationships 01): a new table, so a book gains its
-- triggers on its next start (history.ADDED_ON_START).
CREATE TRIGGER IF NOT EXISTS history_type_keys_insert AFTER INSERT ON type_keys
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'type_keys', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'kind', NEW.kind, 'key', NEW.key, 'type_id', NEW.type_id));
END;
CREATE TRIGGER IF NOT EXISTS history_type_keys_update AFTER UPDATE ON type_keys
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'kind', OLD.kind, 'key', OLD.key, 'type_id', OLD.type_id) IS NOT json_object('id', NEW.id, 'kind', NEW.kind, 'key', NEW.key, 'type_id', NEW.type_id)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'type_keys', NEW.id, 'update',
        json_object('id', OLD.id, 'kind', OLD.kind, 'key', OLD.key, 'type_id', OLD.type_id),
        json_object('id', NEW.id, 'kind', NEW.kind, 'key', NEW.key, 'type_id', NEW.type_id));
END;
CREATE TRIGGER IF NOT EXISTS history_type_keys_delete AFTER DELETE ON type_keys
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'type_keys', OLD.id, 'delete',
        json_object('id', OLD.id, 'kind', OLD.kind, 'key', OLD.key, 'type_id', OLD.type_id), NULL);
END;
-- Type settings (type-relationships 02): a new table, so a book gains its
-- triggers on its next start (history.ADDED_ON_START).
CREATE TRIGGER IF NOT EXISTS history_type_settings_insert AFTER INSERT ON type_settings
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'type_settings', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'type_id', NEW.type_id, 'usual_book', NEW.usual_book, 'check_each_row', NEW.check_each_row));
END;
CREATE TRIGGER IF NOT EXISTS history_type_settings_update AFTER UPDATE ON type_settings
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'type_id', OLD.type_id, 'usual_book', OLD.usual_book, 'check_each_row', OLD.check_each_row) IS NOT json_object('id', NEW.id, 'type_id', NEW.type_id, 'usual_book', NEW.usual_book, 'check_each_row', NEW.check_each_row)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'type_settings', NEW.id, 'update',
        json_object('id', OLD.id, 'type_id', OLD.type_id, 'usual_book', OLD.usual_book, 'check_each_row', OLD.check_each_row),
        json_object('id', NEW.id, 'type_id', NEW.type_id, 'usual_book', NEW.usual_book, 'check_each_row', NEW.check_each_row));
END;
CREATE TRIGGER IF NOT EXISTS history_type_settings_delete AFTER DELETE ON type_settings
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'type_settings', OLD.id, 'delete',
        json_object('id', OLD.id, 'type_id', OLD.type_id, 'usual_book', OLD.usual_book, 'check_each_row', OLD.check_each_row), NULL);
END;
-- fin's own filings (type-relationships 03): a new table, so a book gains its
-- triggers on its next start (history.ADDED_ON_START).
CREATE TRIGGER IF NOT EXISTS history_type_slots_insert AFTER INSERT ON type_slots
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'type_slots', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'slot', NEW.slot, 'type_id', NEW.type_id));
END;
CREATE TRIGGER IF NOT EXISTS history_type_slots_update AFTER UPDATE ON type_slots
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'slot', OLD.slot, 'type_id', OLD.type_id) IS NOT json_object('id', NEW.id, 'slot', NEW.slot, 'type_id', NEW.type_id)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'type_slots', NEW.id, 'update',
        json_object('id', OLD.id, 'slot', OLD.slot, 'type_id', OLD.type_id),
        json_object('id', NEW.id, 'slot', NEW.slot, 'type_id', NEW.type_id));
END;
CREATE TRIGGER IF NOT EXISTS history_type_slots_delete AFTER DELETE ON type_slots
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'type_slots', OLD.id, 'delete',
        json_object('id', OLD.id, 'slot', OLD.slot, 'type_id', OLD.type_id), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_loan_terms_insert AFTER INSERT ON loan_terms
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'loan_terms', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'account_id', NEW.account_id, 'financed_minor', NEW.financed_minor, 'interest_minor', NEW.interest_minor, 'flat_rate', NEW.flat_rate, 'months', NEW.months, 'first_instalment', NEW.first_instalment, 'owed_from', NEW.owed_from, 'method', NEW.method));
END;
CREATE TRIGGER IF NOT EXISTS history_loan_terms_update AFTER UPDATE ON loan_terms
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'account_id', OLD.account_id, 'financed_minor', OLD.financed_minor, 'interest_minor', OLD.interest_minor, 'flat_rate', OLD.flat_rate, 'months', OLD.months, 'first_instalment', OLD.first_instalment, 'owed_from', OLD.owed_from, 'method', OLD.method) IS NOT json_object('id', NEW.id, 'account_id', NEW.account_id, 'financed_minor', NEW.financed_minor, 'interest_minor', NEW.interest_minor, 'flat_rate', NEW.flat_rate, 'months', NEW.months, 'first_instalment', NEW.first_instalment, 'owed_from', NEW.owed_from, 'method', NEW.method)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'loan_terms', NEW.id, 'update',
        json_object('id', OLD.id, 'account_id', OLD.account_id, 'financed_minor', OLD.financed_minor, 'interest_minor', OLD.interest_minor, 'flat_rate', OLD.flat_rate, 'months', OLD.months, 'first_instalment', OLD.first_instalment, 'owed_from', OLD.owed_from, 'method', OLD.method),
        json_object('id', NEW.id, 'account_id', NEW.account_id, 'financed_minor', NEW.financed_minor, 'interest_minor', NEW.interest_minor, 'flat_rate', NEW.flat_rate, 'months', NEW.months, 'first_instalment', NEW.first_instalment, 'owed_from', NEW.owed_from, 'method', NEW.method));
END;
CREATE TRIGGER IF NOT EXISTS history_loan_terms_delete AFTER DELETE ON loan_terms
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'loan_terms', OLD.id, 'delete',
        json_object('id', OLD.id, 'account_id', OLD.account_id, 'financed_minor', OLD.financed_minor, 'interest_minor', OLD.interest_minor, 'flat_rate', OLD.flat_rate, 'months', OLD.months, 'first_instalment', OLD.first_instalment, 'owed_from', OLD.owed_from, 'method', OLD.method), NULL);
END;
CREATE TRIGGER IF NOT EXISTS history_folio_link_insert AFTER INSERT ON folio_link
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'folio_link', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'subject', NEW.subject, 'subject_id', NEW.subject_id, 'target', NEW.target, 'folio_id', NEW.folio_id));
END;
CREATE TRIGGER IF NOT EXISTS history_folio_link_update AFTER UPDATE ON folio_link
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'subject', OLD.subject, 'subject_id', OLD.subject_id, 'target', OLD.target, 'folio_id', OLD.folio_id) IS NOT json_object('id', NEW.id, 'subject', NEW.subject, 'subject_id', NEW.subject_id, 'target', NEW.target, 'folio_id', NEW.folio_id)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'folio_link', NEW.id, 'update',
        json_object('id', OLD.id, 'subject', OLD.subject, 'subject_id', OLD.subject_id, 'target', OLD.target, 'folio_id', OLD.folio_id),
        json_object('id', NEW.id, 'subject', NEW.subject, 'subject_id', NEW.subject_id, 'target', NEW.target, 'folio_id', NEW.folio_id));
END;
CREATE TRIGGER IF NOT EXISTS history_folio_link_delete AFTER DELETE ON folio_link
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'folio_link', OLD.id, 'delete',
        json_object('id', OLD.id, 'subject', OLD.subject, 'subject_id', OLD.subject_id, 'target', OLD.target, 'folio_id', OLD.folio_id), NULL);
END;
-- The books (fin RUL-021): a new table, so a book gains its triggers on its
-- next start (history.ADDED_ON_START).
CREATE TRIGGER IF NOT EXISTS history_books_insert AFTER INSERT ON books
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'books', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'name', NEW.name, 'description', NEW.description, 'created_at', NEW.created_at));
END;
CREATE TRIGGER IF NOT EXISTS history_books_update AFTER UPDATE ON books
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'name', OLD.name, 'description', OLD.description, 'created_at', OLD.created_at) IS NOT json_object('id', NEW.id, 'name', NEW.name, 'description', NEW.description, 'created_at', NEW.created_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'books', NEW.id, 'update',
        json_object('id', OLD.id, 'name', OLD.name, 'description', OLD.description, 'created_at', OLD.created_at),
        json_object('id', NEW.id, 'name', NEW.name, 'description', NEW.description, 'created_at', NEW.created_at));
END;
CREATE TRIGGER IF NOT EXISTS history_books_delete AFTER DELETE ON books
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'books', OLD.id, 'delete',
        json_object('id', OLD.id, 'name', OLD.name, 'description', OLD.description, 'created_at', OLD.created_at), NULL);
END;
-- history:end

-- "Asked first, yes in chat" (fin-surfaces 01 Q3, 02 Changes): a chat change
-- over mcp_tools.MANY_ROWS rows stands only when the call carried the count
-- the operator agreed to in chat; this records that count against its entry.
-- Written by mcp_tools.call, read by the history routes. Not a tracked table.
-- A new table, made empty by CREATE IF NOT EXISTS on start; no existing row
-- is converted or rewritten.
CREATE TABLE IF NOT EXISTS change_asked (
    entry_id INTEGER PRIMARY KEY REFERENCES change_entries(id),
    expected_count INTEGER NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- The conversion steps recorded as run on this book (conversion.py): one row
-- per step that records itself, written by the step inside the runner's
-- transaction, with its writes, so it commits or rolls back with them. A
-- step whose work cannot be read back from the rows alone reads this
-- instead: after expect-back, the operator's own acts (an Unlink, an Undo of
-- the mark, a person figure entered or deleted) can leave rows its plan would
-- convert again, and the book is still converted. fin also records
-- expect-back when it starts on a book with nothing left for that step to
-- convert (a new book, or one whose people have no rows). Not a tracked
-- table: an undo never removes it. A new table, made empty by CREATE IF NOT
-- EXISTS on start; no existing row is converted.
CREATE TABLE IF NOT EXISTS conversion_steps (
    step TEXT PRIMARY KEY,          -- a step's name, as conversion.CHAIN names it
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))  -- UTC
);

-- A seed fin makes once, at start, and never again, so what the operator
-- removes of it stays removed: the default merchant rules, and the PayNow
-- wordings made rules (type-relationships 05; db.py names each seed). Not a tracked table: an
-- Undo never removes it. A new table, made empty by CREATE IF NOT EXISTS on
-- start; no existing row is converted.
CREATE TABLE IF NOT EXISTS seeded_once (
    seed TEXT PRIMARY KEY,          -- a seed's name, as db.py declares it
    seeded_at TEXT NOT NULL DEFAULT (datetime('now'))  -- UTC
);
