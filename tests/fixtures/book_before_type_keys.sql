BEGIN TRANSACTION;
CREATE TABLE accounts (
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
INSERT INTO "accounts" VALUES(1,'Test Card 1111','Test-Card-1111','card','1111','SGD','active','2026-10-23 15:46:43','Household');
CREATE TABLE anchors (
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
CREATE TABLE app_settings (
    key TEXT PRIMARY KEY,           -- declared in screens.py
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE batch_imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filenames TEXT NOT NULL,          -- JSON array of filenames
    accounts TEXT NOT NULL,           -- JSON array of detected account names
    status TEXT DEFAULT 'preview',    -- 'preview', 'committed', 'failed'
    total_lines INTEGER DEFAULT 0,
    categorized_lines INTEGER DEFAULT 0,
    result_json TEXT,                 -- while status is 'preview': the preview's facts confirm writes; after: JSON summary (or the error)
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE change_asked (
    entry_id INTEGER PRIMARY KEY REFERENCES change_entries(id),
    expected_count INTEGER NOT NULL,
    at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE change_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL DEFAULT (datetime('now')),  -- UTC
    via TEXT NOT NULL,                -- 'app' or 'chat' (declared in history.py)
    actor TEXT NOT NULL,              -- 'fin' for the app; the chat client's name
    summary TEXT NOT NULL DEFAULT '',
    open INTEGER NOT NULL DEFAULT 1,  -- 1 while the action runs; the triggers record into the open entry
    undoes INTEGER REFERENCES change_entries(id),     -- this entry undid that one
    undone_by INTEGER REFERENCES change_entries(id)   -- that entry undid this one
);
INSERT INTO "change_entries" VALUES(1,'2026-10-23 15:46:43','app','fin','Added the type Kids > Childcare',0,NULL,NULL);
CREATE TABLE change_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id INTEGER NOT NULL REFERENCES change_entries(id),
    tbl TEXT NOT NULL,                -- a tracked table (history.TRACKED)
    row_id INTEGER NOT NULL,
    op TEXT NOT NULL,                 -- 'insert', 'update' or 'delete'
    before TEXT,                      -- JSON of the row before; NULL for an insert
    after TEXT                        -- JSON of the row after; NULL for a delete
);
INSERT INTO "change_rows" VALUES(1,1,'types',42,'insert',NULL,'{"id":42,"kind":"spending","name":"Childcare","parent_id":27,"default_one_off":0,"covers":"nannies, after-school care","not_for":null,"created_at":"2026-10-23 15:46:43"}');
CREATE TABLE conversion_steps (
    step TEXT PRIMARY KEY,          -- a step's name, as conversion.CHAIN names it
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))  -- UTC
);
INSERT INTO "conversion_steps" VALUES('expect-back','2026-10-23 15:46:43');
CREATE TABLE dismissal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_id INTEGER NOT NULL REFERENCES transactions(id),
    kind TEXT NOT NULL CHECK (kind IN ('payback', 'refund_link')),
    purchase_row_id INTEGER REFERENCES transactions(id),
    made_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (row_id, kind, purchase_row_id),
    CHECK ((kind = 'refund_link') = (purchase_row_id IS NOT NULL))
);
CREATE TABLE expectation (
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
CREATE TABLE expectation_name (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expectation_id INTEGER NOT NULL REFERENCES expectation(id),
    person_id INTEGER NOT NULL REFERENCES accounts(id),
    UNIQUE (expectation_id, person_id)
);
CREATE TABLE let_go (
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
CREATE TABLE merchant_rules (
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
INSERT INTO "merchant_rules" VALUES(1,'TANGLIN MARKET',1,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(2,'LITTLE FARMS',2,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(3,'CS FRESH',3,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(4,'FAIRPRICE',4,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(5,'COLD STORAGE',5,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(6,'COLES',6,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(7,'ALDI',7,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(8,'MARKS & SPENCER',8,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(9,'M & S',9,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(10,'FOOD PANDA',10,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(11,'FP*FOOD PANDA',11,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(12,'REDMART',12,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(13,'SHENG SIONG',13,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(14,'GIANT',14,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(15,'SCOOP WHOLEFOODS',15,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(16,'PARAGON MARKET',16,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(17,'DIN TAI FUNG',17,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(18,'CULINA',18,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(19,'SUBWAY',19,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(20,'NET*SUBWAY',20,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(21,'YA KUN',21,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(22,'TOAST BOX',22,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(23,'MCDONALD',23,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(24,'STARBUCKS',24,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(25,'CAFFE BEVIAMO',25,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(26,'GOURMET SARAWAK',26,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(27,'TORI-Q',27,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(28,'NESPRESSO',28,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(29,'BACHA COFFEE',29,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(30,'KOPI',30,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(31,'VIET TASTE',31,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(32,'TWO MEN BAGEL',32,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(33,'CHICHA SAN CHEN',33,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(34,'OLD CHANG KEE',34,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(35,'PROJECT ACAI',35,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(36,'MR COCONUT',36,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(37,'ALLPRESS ESPRESSO',37,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(38,'IMPERIAL TREASURE',38,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(39,'JOLLIBEAN',39,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(40,'7-ELEVEN',40,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(41,'CHEERS',41,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(42,'SUPERBIG',42,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(43,'GRANDHYATT',43,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(44,'HYDRATE',44,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(45,'COCOBELLA',45,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(46,'ASSEMBLY GROUND',46,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(47,'GELATIAMO',47,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(48,'DEARBORN',48,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(49,'FATTENED CALF',49,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(50,'BUS/MRT',50,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(51,'GRAB*',51,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(52,'GRAB ',52,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(53,'UBER',53,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(54,'COMFORT',54,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(55,'GOJEK',55,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(56,'TESLA MOTORS',56,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(57,'KIGO CHARGING',57,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(58,'CHARGEPLUS',58,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(59,'SP DIGITAL PL-EV',59,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(60,'SHELL ',60,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(61,'SPC ',61,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(62,'PARKING.SG',62,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(63,'NETS FLASHPAY',63,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(64,'NETS AUTO TOP UP',64,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(65,'TAKASHIMAYA',65,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(66,'ZARA',66,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(67,'SHOPEE',67,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(68,'LAZADA',68,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(69,'2C2*LAZADA',69,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(70,'AMAZON',70,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(71,'KINOKUNIYA',71,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(72,'WH SMITH',72,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(73,'WHS ',73,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(74,'TARGET',74,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(75,'H M HENNES',75,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(76,'LONGCHAMP',76,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(77,'ONITSUKA',77,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(78,'DAISO',78,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(79,'KINDLE SVCS',79,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(80,'GUARDIAN',80,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(81,'WATSONS',81,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(82,'AESOP',82,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(83,'M SPA',83,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(84,'MECCA',84,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(85,'MOUNT ALVERNIA',85,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(86,'SINGHEALTH',86,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(87,'MOUNT E ORCHARD',87,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(88,'KINDER CLINIC',88,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(89,'ALPHA WOMEN',89,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(90,'PATRICIA YUEN DERMATOL',90,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(91,'TERRA MEDICAL',91,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(92,'OSTEOPATHIC',92,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(93,'NETFLIX',93,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(94,'SPOTIFY',94,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(95,'YOUTUBE',95,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(96,'HBO',96,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(97,'TESLA PREMIUM',97,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(98,'SINGTEL',98,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(99,'MYSINGTELAPP',99,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(100,'MYREPUBLIC',100,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(101,'SP GROUP',101,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(102,'SP GAS',102,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(103,'SP DIGITAL PL-UTILITIE',103,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(104,'1PASSWORD',104,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(105,'ANTHROPIC',105,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(106,'CLAUDE.AI',106,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(107,'OPENAI',107,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(108,'CHATGPT',108,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(109,'NOTION LABS',109,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(110,'TRADINGVIEW',110,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(111,'REMNOTE',111,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(112,'MICROSOFT 365',112,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(113,'OURA',113,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(114,'GOOGLE PLAY',114,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(115,'TWITTER',115,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(116,'RUNPOD',116,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(117,'GOOGLE*GOOGLE ONE',117,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(118,'GOOGLE ONE',118,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(119,'JASPER.AI',119,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(120,'SPENDEE',120,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(121,'MANULIFE',121,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(122,'AIA',122,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(123,'PRUDENTIAL',123,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(124,'GREAT EASTERN',124,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(125,'SINGAPORE LIFE',125,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(126,'HP HPR',126,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(127,'DUTY FREE',127,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(128,'HEINEMANN',128,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(129,'FLYSCOOT',129,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(130,'AUSTRALIANETA',130,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(131,'GOODWOOF',131,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(132,'PET LOVERS CENTRE',132,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(133,'VIVE ACTIVE',133,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(134,'BFT',134,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(135,'BARRYSBOOTCAMP',135,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(136,'BARRY''S',136,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(137,'SICC',137,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(138,'SINGAPORE ISLAND COUNTRY',138,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(139,'SINGAPORE ISLAND COUNT',139,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(140,'VIN GOLF',140,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(141,'BUKIT TIMAH GOLF',141,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(142,'ORCHID COUNTRY CLUB',142,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(143,'HIDDEN CASTLE GOLF',143,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(144,'UPLAY VENTURES',144,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(145,'PING ',145,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(146,'MOTHERS WORK',146,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(147,'MOTHER WORK',147,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(148,'PRAMFOX',148,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(149,'BRILLIANT',149,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(150,'WELCH LABS',150,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(151,'GOOGLE*ADS',151,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(152,'LOYALTYLION',152,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(153,'ALIBABA.COM',153,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(154,'XERO',154,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(155,'MOOM',155,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(156,'KLAVIYO',156,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(157,'SHOPIFY',157,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(158,'IWG MANAGEMENT',158,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(159,'DYSON',159,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(160,'HOME 360',160,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(161,'SP SONNO',161,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(162,'FATELICIOUS',162,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(163,'365 JUICES',163,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(164,'PEEPAL BY ZED',164,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(165,'PARCHMENT-UNIV',165,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(166,'H KONCEPTS',166,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(167,'VOOVOO',167,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(168,'NEO EMPIRE',168,'contains','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(169,'SINGAPORE246',169,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(170,'SINGAPORE618',170,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
INSERT INTO "merchant_rules" VALUES(171,'OTT MB',171,'startswith','auto',0,'2026-10-23 15:46:43',NULL,NULL,NULL,NULL);
CREATE TABLE rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pair TEXT NOT NULL,             -- 'INR/SGD': SGD for one rupee
    date TEXT NOT NULL,             -- YYYY-MM-DD, the day the rate is used for
    rate TEXT NOT NULL,             -- decimal text, never a float
    source TEXT NOT NULL,           -- where it came from, and the day it is of when that differs
    fetched_at TEXT NOT NULL,       -- when it was fetched or entered (UTC)
    UNIQUE (pair, date)
);
CREATE TABLE refund_link (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    refund_row_id INTEGER NOT NULL UNIQUE REFERENCES transactions(id),
    purchase_row_id INTEGER NOT NULL REFERENCES transactions(id),
    source TEXT NOT NULL CHECK (source IN ('auto', 'hand')),
    made_at TEXT NOT NULL DEFAULT (datetime('now')),
    CHECK (refund_row_id != purchase_row_id)
);
CREATE TABLE refused_statements (
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
CREATE TABLE sender_wording (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wording TEXT NOT NULL UNIQUE,
    person_id INTEGER NOT NULL REFERENCES accounts(id),
    learnt_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE services (
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
INSERT INTO "services" VALUES(1,'Tanglin Market',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(2,'Little Farms',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(3,'Cs Fresh',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(4,'Fairprice',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(5,'Cold Storage',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(6,'Coles',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(7,'Aldi',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(8,'Marks & Spencer',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(9,'M & S',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(10,'Food Panda',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(11,'Fp*Food Panda',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(12,'Redmart',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(13,'Sheng Siong',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(14,'Giant',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(15,'Scoop Wholefoods',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(16,'Paragon Market',0,0,NULL,'2026-10-23 15:46:43','Household',14,0);
INSERT INTO "services" VALUES(17,'Din Tai Fung',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(18,'Culina',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(19,'Subway',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(20,'Net*Subway',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(21,'Ya Kun',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(22,'Toast Box',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(23,'Mcdonald',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(24,'Starbucks',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(25,'Caffe Beviamo',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(26,'Gourmet Sarawak',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(27,'Tori-Q',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(28,'Nespresso',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(29,'Bacha Coffee',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(30,'Kopi',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(31,'Viet Taste',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(32,'Two Men Bagel',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(33,'Chicha San Chen',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(34,'Old Chang Kee',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(35,'Project Acai',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(36,'Mr Coconut',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(37,'Allpress Espresso',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(38,'Imperial Treasure',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(39,'Jollibean',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(40,'7-Eleven',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(41,'Cheers',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(42,'Superbig',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(43,'Grandhyatt',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(44,'Hydrate',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(45,'Cocobella',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(46,'Assembly Ground',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(47,'Gelatiamo',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(48,'Dearborn',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(49,'Fattened Calf',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(50,'Bus/Mrt',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(51,'Grab*',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(52,'Grab ',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(53,'Uber',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(54,'Comfort',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(55,'Gojek',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(56,'Tesla Motors',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(57,'Kigo Charging',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(58,'Chargeplus',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(59,'Sp Digital Pl-Ev',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(60,'Shell ',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(61,'Spc ',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(62,'Parking.Sg',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(63,'Nets Flashpay',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(64,'Nets Auto Top Up',0,0,NULL,'2026-10-23 15:46:43','Household',16,0);
INSERT INTO "services" VALUES(65,'Takashimaya',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(66,'Zara',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(67,'Shopee',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(68,'Lazada',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(69,'2C2*Lazada',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(70,'Amazon',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(71,'Kinokuniya',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(72,'Wh Smith',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(73,'Whs ',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(74,'Target',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(75,'H M Hennes',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(76,'Longchamp',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(77,'Onitsuka',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(78,'Daiso',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(79,'Kindle Svcs',0,0,NULL,'2026-10-23 15:46:43','Household',15,0);
INSERT INTO "services" VALUES(80,'Guardian',0,0,NULL,'2026-10-23 15:46:43','Household',25,0);
INSERT INTO "services" VALUES(81,'Watsons',0,0,NULL,'2026-10-23 15:46:43','Household',25,0);
INSERT INTO "services" VALUES(82,'Aesop',0,0,NULL,'2026-10-23 15:46:43','Household',25,0);
INSERT INTO "services" VALUES(83,'M Spa',0,0,NULL,'2026-10-23 15:46:43','Household',25,0);
INSERT INTO "services" VALUES(84,'Mecca',0,0,NULL,'2026-10-23 15:46:43','Household',25,0);
INSERT INTO "services" VALUES(85,'Mount Alvernia',0,0,NULL,'2026-10-23 15:46:43','Household',26,0);
INSERT INTO "services" VALUES(86,'Singhealth',0,0,NULL,'2026-10-23 15:46:43','Household',26,0);
INSERT INTO "services" VALUES(87,'Mount E Orchard',0,0,NULL,'2026-10-23 15:46:43','Household',26,0);
INSERT INTO "services" VALUES(88,'Kinder Clinic',0,0,NULL,'2026-10-23 15:46:43','Household',26,0);
INSERT INTO "services" VALUES(89,'Alpha Women',0,0,NULL,'2026-10-23 15:46:43','Household',26,0);
INSERT INTO "services" VALUES(90,'Patricia Yuen Dermatol',0,0,NULL,'2026-10-23 15:46:43','Household',26,0);
INSERT INTO "services" VALUES(91,'Terra Medical',0,0,NULL,'2026-10-23 15:46:43','Household',26,0);
INSERT INTO "services" VALUES(92,'Osteopathic',0,0,NULL,'2026-10-23 15:46:43','Household',26,0);
INSERT INTO "services" VALUES(93,'Netflix',0,0,NULL,'2026-10-23 15:46:43','Household',22,0);
INSERT INTO "services" VALUES(94,'Spotify',0,0,NULL,'2026-10-23 15:46:43','Household',22,0);
INSERT INTO "services" VALUES(95,'Youtube',0,0,NULL,'2026-10-23 15:46:43','Household',22,0);
INSERT INTO "services" VALUES(96,'Hbo',0,0,NULL,'2026-10-23 15:46:43','Household',22,0);
INSERT INTO "services" VALUES(97,'Tesla Premium',0,0,NULL,'2026-10-23 15:46:43','Household',22,0);
INSERT INTO "services" VALUES(98,'Singtel',0,0,NULL,'2026-10-23 15:46:43','Household',32,0);
INSERT INTO "services" VALUES(99,'Mysingtelapp',0,0,NULL,'2026-10-23 15:46:43','Household',32,0);
INSERT INTO "services" VALUES(100,'Myrepublic',0,0,NULL,'2026-10-23 15:46:43','Household',32,0);
INSERT INTO "services" VALUES(101,'Sp Group',0,0,NULL,'2026-10-23 15:46:43','Household',32,0);
INSERT INTO "services" VALUES(102,'Sp Gas',0,0,NULL,'2026-10-23 15:46:43','Household',32,0);
INSERT INTO "services" VALUES(103,'Sp Digital Pl-Utilitie',0,0,NULL,'2026-10-23 15:46:43','Household',32,0);
INSERT INTO "services" VALUES(104,'1Password',0,0,NULL,'2026-10-23 15:46:43','Household',32,0);
INSERT INTO "services" VALUES(105,'Anthropic',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(106,'Claude.Ai',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(107,'Openai',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(108,'Chatgpt',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(109,'Notion Labs',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(110,'Tradingview',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(111,'Remnote',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(112,'Microsoft 365',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(113,'Oura',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(114,'Google Play',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(115,'Twitter',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(116,'Runpod',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(117,'Google*Google One',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(118,'Google One',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(119,'Jasper.Ai',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(120,'Spendee',0,0,NULL,'2026-10-23 15:46:43','Household',34,0);
INSERT INTO "services" VALUES(121,'Manulife',0,0,NULL,'2026-10-23 15:46:43','Household',10,0);
INSERT INTO "services" VALUES(122,'Aia',0,0,NULL,'2026-10-23 15:46:43','Household',10,0);
INSERT INTO "services" VALUES(123,'Prudential',0,0,NULL,'2026-10-23 15:46:43','Household',10,0);
INSERT INTO "services" VALUES(124,'Great Eastern',0,0,NULL,'2026-10-23 15:46:43','Household',10,0);
INSERT INTO "services" VALUES(125,'Singapore Life',0,0,NULL,'2026-10-23 15:46:43','Household',10,0);
INSERT INTO "services" VALUES(126,'Hp Hpr',0,0,NULL,'2026-10-23 15:46:43','Household',NULL,0);
INSERT INTO "services" VALUES(127,'Duty Free',0,0,NULL,'2026-10-23 15:46:43','Household',21,0);
INSERT INTO "services" VALUES(128,'Heinemann',0,0,NULL,'2026-10-23 15:46:43','Household',21,0);
INSERT INTO "services" VALUES(129,'Flyscoot',0,0,NULL,'2026-10-23 15:46:43','Household',21,0);
INSERT INTO "services" VALUES(130,'Australianeta',0,0,NULL,'2026-10-23 15:46:43','Household',21,0);
INSERT INTO "services" VALUES(131,'Goodwoof',0,0,NULL,'2026-10-23 15:46:43','Household',28,0);
INSERT INTO "services" VALUES(132,'Pet Lovers Centre',0,0,NULL,'2026-10-23 15:46:43','Household',28,0);
INSERT INTO "services" VALUES(133,'Vive Active',0,0,NULL,'2026-10-23 15:46:43','Household',23,0);
INSERT INTO "services" VALUES(134,'Bft',0,0,NULL,'2026-10-23 15:46:43','Household',23,0);
INSERT INTO "services" VALUES(135,'Barrysbootcamp',0,0,NULL,'2026-10-23 15:46:43','Household',23,0);
INSERT INTO "services" VALUES(136,'Barry''S',0,0,NULL,'2026-10-23 15:46:43','Household',23,0);
INSERT INTO "services" VALUES(137,'Sicc',0,0,NULL,'2026-10-23 15:46:43','Household',23,0);
INSERT INTO "services" VALUES(138,'Singapore Island Country',0,0,NULL,'2026-10-23 15:46:43','Household',23,0);
INSERT INTO "services" VALUES(139,'Singapore Island Count',0,0,NULL,'2026-10-23 15:46:43','Household',23,0);
INSERT INTO "services" VALUES(140,'Vin Golf',0,0,NULL,'2026-10-23 15:46:43','Household',23,0);
INSERT INTO "services" VALUES(141,'Bukit Timah Golf',0,0,NULL,'2026-10-23 15:46:43','Household',23,0);
INSERT INTO "services" VALUES(142,'Orchid Country Club',0,0,NULL,'2026-10-23 15:46:43','Household',23,0);
INSERT INTO "services" VALUES(143,'Hidden Castle Golf',0,0,NULL,'2026-10-23 15:46:43','Household',23,0);
INSERT INTO "services" VALUES(144,'Uplay Ventures',0,0,NULL,'2026-10-23 15:46:43','Household',23,0);
INSERT INTO "services" VALUES(145,'Ping ',0,0,NULL,'2026-10-23 15:46:43','Household',23,0);
INSERT INTO "services" VALUES(146,'Mothers Work',0,0,NULL,'2026-10-23 15:46:43','Household',27,0);
INSERT INTO "services" VALUES(147,'Mother Work',0,0,NULL,'2026-10-23 15:46:43','Household',27,0);
INSERT INTO "services" VALUES(148,'Pramfox',0,0,NULL,'2026-10-23 15:46:43','Household',27,0);
INSERT INTO "services" VALUES(149,'Brilliant',0,0,NULL,'2026-10-23 15:46:43','Household',29,0);
INSERT INTO "services" VALUES(150,'Welch Labs',0,0,NULL,'2026-10-23 15:46:43','Household',29,0);
INSERT INTO "services" VALUES(151,'Google*Ads',0,0,NULL,'2026-10-23 15:46:43','Moom',NULL,0);
INSERT INTO "services" VALUES(152,'Loyaltylion',0,0,NULL,'2026-10-23 15:46:43','Moom',NULL,0);
INSERT INTO "services" VALUES(153,'Alibaba.Com',0,0,NULL,'2026-10-23 15:46:43','Moom',NULL,0);
INSERT INTO "services" VALUES(154,'Xero',0,0,NULL,'2026-10-23 15:46:43','Moom',NULL,0);
INSERT INTO "services" VALUES(155,'Moom',0,0,NULL,'2026-10-23 15:46:43','Moom',NULL,0);
INSERT INTO "services" VALUES(156,'Klaviyo',0,0,NULL,'2026-10-23 15:46:43','Moom',NULL,0);
INSERT INTO "services" VALUES(157,'Shopify',0,0,NULL,'2026-10-23 15:46:43','Moom',NULL,0);
INSERT INTO "services" VALUES(158,'Iwg Management',0,0,NULL,'2026-10-23 15:46:43','Moom',NULL,0);
INSERT INTO "services" VALUES(159,'Dyson',0,0,NULL,'2026-10-23 15:46:43','Household',30,0);
INSERT INTO "services" VALUES(160,'Home 360',0,0,NULL,'2026-10-23 15:46:43','Household',30,0);
INSERT INTO "services" VALUES(161,'Sp Sonno',0,0,NULL,'2026-10-23 15:46:43','Household',30,0);
INSERT INTO "services" VALUES(162,'Fatelicious',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(163,'365 Juices',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(164,'Peepal By Zed',0,0,NULL,'2026-10-23 15:46:43','Household',13,0);
INSERT INTO "services" VALUES(165,'Parchment-Univ',0,0,NULL,'2026-10-23 15:46:43','Household',29,0);
INSERT INTO "services" VALUES(166,'H Koncepts',0,0,NULL,'2026-10-23 15:46:43','Household',NULL,0);
INSERT INTO "services" VALUES(167,'Voovoo',0,0,NULL,'2026-10-23 15:46:43','Household',NULL,0);
INSERT INTO "services" VALUES(168,'Neo Empire',0,0,NULL,'2026-10-23 15:46:43','Household',NULL,0);
INSERT INTO "services" VALUES(169,'Singapore246',0,0,NULL,'2026-10-23 15:46:43','Household',NULL,0);
INSERT INTO "services" VALUES(170,'Singapore618',0,0,NULL,'2026-10-23 15:46:43','Household',NULL,0);
INSERT INTO "services" VALUES(171,'Ott Mb',0,0,NULL,'2026-10-23 15:46:43','Household',NULL,0);
CREATE TABLE set_off (
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
CREATE TABLE statements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL,
    statement_date TEXT NOT NULL,   -- YYYY-MM-DD
    filename TEXT,                  -- original PDF filename
    imported_at TEXT DEFAULT (datetime('now')),
    printed INTEGER NOT NULL DEFAULT 0,  -- 1 = one statement as printed: statement_date is its closing day and it holds the rows it printed; 0 = a calendar-month record (statement_date the 1st) of rows from a source that states no balance. An existing database gains it through convert_printed_statements.py
    FOREIGN KEY (account_id) REFERENCES accounts(id),
    UNIQUE(account_id, statement_date)
);
INSERT INTO "statements" VALUES(1,1,'2026-09-30',NULL,'2026-10-23 15:46:43',1);
CREATE TABLE subscriptions (
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
CREATE TABLE suggestion_answers (
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
CREATE TABLE transactions (
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
    book TEXT,                     -- whose spending: Household, Moom, Kalesh (declared in book_type.py); NULL reads as Household
    type_id INTEGER REFERENCES types(id),  -- what kind of spending, or for income its kind; NULL = not placed
    amount_minor INTEGER,          -- the amount in whole minor units of the account's currency: positive = money out, negative = money in
    other_side_id INTEGER REFERENCES accounts(id),  -- the account a movement, transfer or payment names as its other side; NULL = none named. An existing database gains it through convert_movements.py
    book_source TEXT CHECK (book_source IN ('merchant', 'rule', 'wording', 'checked', 'chosen', 'derived')),  -- where the book came from (declared in book_type.py, SOURCES); NULL when the book is empty, but 'chosen' for one left empty by hand (the review list's label); a book with a value and no source reads as from the merchant (label_source). An existing database gains it through convert_label_source.py
    type_source TEXT CHECK (type_source IN ('merchant', 'rule', 'wording', 'checked', 'chosen', 'derived')),  -- where the type came from, as book_source; NULL when the type is empty, but 'chosen' for one left empty by hand
    FOREIGN KEY (statement_id) REFERENCES statements(id),
    FOREIGN KEY (service_id) REFERENCES services(id)
);
INSERT INTO "transactions" VALUES(1,1,'2026-09-03','DIN TAI FUNG PARAGON',NULL,NULL,17,0,'expense',0,NULL,'2026-10-23 15:46:43','Household',13,4520,NULL,'merchant','merchant');
INSERT INTO "transactions" VALUES(2,1,'2026-09-09','ANNUAL FEE',NULL,NULL,NULL,0,'expense',0,NULL,'2026-10-23 15:46:43','Household',9,19620,NULL,'chosen','chosen');
INSERT INTO "transactions" VALUES(3,1,'2026-09-15','LITTLE ACORNS NANNIES',NULL,NULL,NULL,0,'expense',0,NULL,'2026-10-23 15:46:43','Household',42,30000,NULL,'chosen','chosen');
INSERT INTO "transactions" VALUES(4,1,'2026-09-20','SOMEWHERE NEW',NULL,NULL,NULL,0,'expense',0,NULL,'2026-10-23 15:46:43',NULL,NULL,1250,NULL,NULL,NULL);
CREATE TABLE types (
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
INSERT INTO "types" VALUES(1,'spending','Software & AI tools',NULL,0,'software, apps, hosting, databases, domains, cloud storage, developer and AI services, security software','hardware; film, music or news subscriptions','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(2,'spending','Advertising',NULL,0,'paid adverts and promotion on search, social media or marketplaces','personal social media memberships','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(3,'spending','Stock purchases',NULL,0,'goods bought wholesale to resell','things bought for own use','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(4,'spending','Payment fees',NULL,0,'charges from payment processors for taking customer payments','bank account or card fees','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(5,'spending','People',NULL,0,'payroll, contractors, freelancers, recruitment','accountants, lawyers and other firms','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(6,'spending','Professional services',NULL,0,'accountants, lawyers, company secretaries, consultants','software subscriptions','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(7,'spending','Office',NULL,0,'office rent, co-working space, meeting rooms','home rent','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(8,'spending','Equipment',NULL,1,'computers, phones, cameras, machines and other durable gear','software; home furniture. A large asset bought by a company may be a balance-sheet movement (ticket 05)','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(9,'spending','Bank & government fees',NULL,0,'card annual fees and the GST on them, late charges, interest charged, bank service charges, government levies and fines','insurance premiums; tax; visa fees for a trip','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(10,'spending','Insurance',NULL,0,'insurance premiums of any kind','medical bills','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(11,'spending','Tax',NULL,0,'income tax, property tax and other tax payments','GST charged on a fee; government service fees','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(12,'spending','Repairs & maintenance',NULL,1,'fixing or servicing a car, home or appliance; tradespeople','buying new furniture or appliances','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(13,'spending','Dining',NULL,0,'restaurants, bars, fast food, food delivery, coffee shops, cafes, bakeries and drinks shops','supermarkets','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(14,'spending','Groceries',NULL,0,'supermarkets, convenience stores, markets, food for the home','restaurants','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(15,'spending','Shopping',NULL,0,'clothes, shoes, electronics, books, furniture, accessories and general retail, in a shop or online','groceries; goods for children','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(16,'spending','Transport',NULL,0,'road tax, parking, tolls, vehicle licensing','flights; car repairs','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(17,'spending','Rides',16,0,'taxis and ride-hailing','food delivery by the same company','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(18,'spending','Public Transit',16,0,'buses, trains, metro and transit cards',NULL,'2026-10-23 15:46:43');
INSERT INTO "types" VALUES(19,'spending','EV Charging',16,0,'electric vehicle charging',NULL,'2026-10-23 15:46:43');
INSERT INTO "types" VALUES(20,'spending','Fuel',16,0,'petrol and diesel',NULL,'2026-10-23 15:46:43');
INSERT INTO "types" VALUES(21,'spending','Travel',NULL,0,'flights, hotel and rental stays, visa fees for a trip, tours and travel agencies','meals while travelling; golf while travelling','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(22,'spending','Entertainment',NULL,0,'cinema, concerts, events, tickets, games, streaming video and music','sport played oneself','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(23,'spending','Fitness',NULL,0,'gyms, classes, sports clubs','golf','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(24,'spending','Golf',23,0,'golf courses, country clubs, driving ranges, green fees, golf lessons','golf clothing bought in an ordinary shop','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(25,'spending','Health & Beauty',NULL,0,'hair salons, barbers, spas, skincare, personal-care pharmacy','doctors and hospitals','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(26,'spending','Medical',NULL,0,'doctors, clinics, hospitals, dentists, specialists, tests','health insurance premiums','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(27,'spending','Kids',NULL,0,'clothes and toys for children, play centres, childcare, baby goods','school and course fees','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(28,'spending','Pet',NULL,0,'vets, pet food, grooming, supplies',NULL,'2026-10-23 15:46:43');
INSERT INTO "types" VALUES(29,'spending','Education',NULL,0,'school fees, courses, tuition, exams','books bought for leisure','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(30,'spending','Home',NULL,0,'furniture, household goods, domestic help, home services','rent; repairs; utilities','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(31,'spending','Rent',NULL,0,'rent for a home',NULL,'2026-10-23 15:46:43');
INSERT INTO "types" VALUES(32,'spending','Utilities',NULL,0,'electricity, gas, water, internet, mobile phone plans','streaming; software','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(33,'spending','Gifts & Donations',NULL,0,'charity donations, gifts and flowers for others',NULL,'2026-10-23 15:46:43');
INSERT INTO "types" VALUES(34,'spending','Subscriptions',NULL,0,'paid newsletters, news, magazines and other memberships that are not software or entertainment','software; streaming','2026-10-23 15:46:43');
INSERT INTO "types" VALUES(35,'spending','Social Media',NULL,0,'paid memberships of social networks',NULL,'2026-10-23 15:46:43');
INSERT INTO "types" VALUES(36,'spending','Other',NULL,0,'spending the operator has not placed',NULL,'2026-10-23 15:46:43');
INSERT INTO "types" VALUES(37,'income','Salary',NULL,0,'pay from an employer',NULL,'2026-10-23 15:46:43');
INSERT INTO "types" VALUES(38,'income','Rental',NULL,0,'rent received for a property the household lets out',NULL,'2026-10-23 15:46:43');
INSERT INTO "types" VALUES(39,'income','Interest',NULL,0,'interest paid by a bank or on a deposit',NULL,'2026-10-23 15:46:43');
INSERT INTO "types" VALUES(40,'income','Gift received',NULL,0,'money given to the household',NULL,'2026-10-23 15:46:43');
INSERT INTO "types" VALUES(41,'income','Other',NULL,0,'income the operator has not placed',NULL,'2026-10-23 15:46:43');
INSERT INTO "types" VALUES(42,'spending','Childcare',27,0,'nannies, after-school care',NULL,'2026-10-23 15:46:43');
CREATE INDEX idx_expectation_name_expectation ON expectation_name (expectation_id);
CREATE INDEX idx_set_off_payment ON set_off (payment_row_id);
CREATE INDEX idx_set_off_expectation ON set_off (expectation_id);
CREATE INDEX idx_refund_link_purchase ON refund_link (purchase_row_id);
CREATE UNIQUE INDEX idx_dismissal_one ON dismissal(row_id, kind, IFNULL(purchase_row_id, 0));
CREATE VIEW row_parts AS
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
CREATE INDEX idx_transactions_date ON transactions(date);
CREATE INDEX idx_transactions_statement ON transactions(statement_id);
CREATE INDEX idx_merchant_rules_pattern ON merchant_rules(pattern);
CREATE INDEX idx_subscriptions_status ON subscriptions(status);
CREATE INDEX idx_change_rows_row ON change_rows(tbl, row_id);
CREATE INDEX idx_change_rows_entry ON change_rows(entry_id, id);
CREATE TRIGGER history_transactions_insert AFTER INSERT ON transactions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'transactions', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'statement_id', NEW.statement_id, 'date', NEW.date, 'description', NEW.description, 'amount_foreign', NEW.amount_foreign, 'currency_foreign', NEW.currency_foreign, 'service_id', NEW.service_id, 'is_one_off', NEW.is_one_off, 'flow_type', NEW.flow_type, 'flow_type_manual', NEW.flow_type_manual, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'amount_minor', NEW.amount_minor, 'other_side_id', NEW.other_side_id, 'book_source', NEW.book_source, 'type_source', NEW.type_source));
END;
CREATE TRIGGER history_transactions_update AFTER UPDATE ON transactions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'statement_id', OLD.statement_id, 'date', OLD.date, 'description', OLD.description, 'amount_foreign', OLD.amount_foreign, 'currency_foreign', OLD.currency_foreign, 'service_id', OLD.service_id, 'is_one_off', OLD.is_one_off, 'flow_type', OLD.flow_type, 'flow_type_manual', OLD.flow_type_manual, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'amount_minor', OLD.amount_minor, 'other_side_id', OLD.other_side_id, 'book_source', OLD.book_source, 'type_source', OLD.type_source) IS NOT json_object('id', NEW.id, 'statement_id', NEW.statement_id, 'date', NEW.date, 'description', NEW.description, 'amount_foreign', NEW.amount_foreign, 'currency_foreign', NEW.currency_foreign, 'service_id', NEW.service_id, 'is_one_off', NEW.is_one_off, 'flow_type', NEW.flow_type, 'flow_type_manual', NEW.flow_type_manual, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'amount_minor', NEW.amount_minor, 'other_side_id', NEW.other_side_id, 'book_source', NEW.book_source, 'type_source', NEW.type_source)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'transactions', NEW.id, 'update',
        json_object('id', OLD.id, 'statement_id', OLD.statement_id, 'date', OLD.date, 'description', OLD.description, 'amount_foreign', OLD.amount_foreign, 'currency_foreign', OLD.currency_foreign, 'service_id', OLD.service_id, 'is_one_off', OLD.is_one_off, 'flow_type', OLD.flow_type, 'flow_type_manual', OLD.flow_type_manual, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'amount_minor', OLD.amount_minor, 'other_side_id', OLD.other_side_id, 'book_source', OLD.book_source, 'type_source', OLD.type_source),
        json_object('id', NEW.id, 'statement_id', NEW.statement_id, 'date', NEW.date, 'description', NEW.description, 'amount_foreign', NEW.amount_foreign, 'currency_foreign', NEW.currency_foreign, 'service_id', NEW.service_id, 'is_one_off', NEW.is_one_off, 'flow_type', NEW.flow_type, 'flow_type_manual', NEW.flow_type_manual, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'amount_minor', NEW.amount_minor, 'other_side_id', NEW.other_side_id, 'book_source', NEW.book_source, 'type_source', NEW.type_source));
END;
CREATE TRIGGER history_transactions_delete AFTER DELETE ON transactions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'transactions', OLD.id, 'delete',
        json_object('id', OLD.id, 'statement_id', OLD.statement_id, 'date', OLD.date, 'description', OLD.description, 'amount_foreign', OLD.amount_foreign, 'currency_foreign', OLD.currency_foreign, 'service_id', OLD.service_id, 'is_one_off', OLD.is_one_off, 'flow_type', OLD.flow_type, 'flow_type_manual', OLD.flow_type_manual, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'amount_minor', OLD.amount_minor, 'other_side_id', OLD.other_side_id, 'book_source', OLD.book_source, 'type_source', OLD.type_source), NULL);
END;
CREATE TRIGGER history_anchors_insert AFTER INSERT ON anchors
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'anchors', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'account_id', NEW.account_id, 'date', NEW.date, 'amount', NEW.amount, 'source', NEW.source, 'note', NEW.note, 'created_at', NEW.created_at));
END;
CREATE TRIGGER history_anchors_update AFTER UPDATE ON anchors
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'account_id', OLD.account_id, 'date', OLD.date, 'amount', OLD.amount, 'source', OLD.source, 'note', OLD.note, 'created_at', OLD.created_at) IS NOT json_object('id', NEW.id, 'account_id', NEW.account_id, 'date', NEW.date, 'amount', NEW.amount, 'source', NEW.source, 'note', NEW.note, 'created_at', NEW.created_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'anchors', NEW.id, 'update',
        json_object('id', OLD.id, 'account_id', OLD.account_id, 'date', OLD.date, 'amount', OLD.amount, 'source', OLD.source, 'note', OLD.note, 'created_at', OLD.created_at),
        json_object('id', NEW.id, 'account_id', NEW.account_id, 'date', NEW.date, 'amount', NEW.amount, 'source', NEW.source, 'note', NEW.note, 'created_at', NEW.created_at));
END;
CREATE TRIGGER history_anchors_delete AFTER DELETE ON anchors
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'anchors', OLD.id, 'delete',
        json_object('id', OLD.id, 'account_id', OLD.account_id, 'date', OLD.date, 'amount', OLD.amount, 'source', OLD.source, 'note', OLD.note, 'created_at', OLD.created_at), NULL);
END;
CREATE TRIGGER history_services_insert AFTER INSERT ON services
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'services', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'name', NEW.name, 'is_one_off', NEW.is_one_off, 'exclude_from_expense_views', NEW.exclude_from_expense_views, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'review_each_time', NEW.review_each_time));
END;
CREATE TRIGGER history_services_update AFTER UPDATE ON services
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'name', OLD.name, 'is_one_off', OLD.is_one_off, 'exclude_from_expense_views', OLD.exclude_from_expense_views, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'review_each_time', OLD.review_each_time) IS NOT json_object('id', NEW.id, 'name', NEW.name, 'is_one_off', NEW.is_one_off, 'exclude_from_expense_views', NEW.exclude_from_expense_views, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'review_each_time', NEW.review_each_time)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'services', NEW.id, 'update',
        json_object('id', OLD.id, 'name', OLD.name, 'is_one_off', OLD.is_one_off, 'exclude_from_expense_views', OLD.exclude_from_expense_views, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'review_each_time', OLD.review_each_time),
        json_object('id', NEW.id, 'name', NEW.name, 'is_one_off', NEW.is_one_off, 'exclude_from_expense_views', NEW.exclude_from_expense_views, 'notes', NEW.notes, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id, 'review_each_time', NEW.review_each_time));
END;
CREATE TRIGGER history_services_delete AFTER DELETE ON services
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'services', OLD.id, 'delete',
        json_object('id', OLD.id, 'name', OLD.name, 'is_one_off', OLD.is_one_off, 'exclude_from_expense_views', OLD.exclude_from_expense_views, 'notes', OLD.notes, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id, 'review_each_time', OLD.review_each_time), NULL);
END;
CREATE TRIGGER history_merchant_rules_insert AFTER INSERT ON merchant_rules
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'merchant_rules', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'pattern', NEW.pattern, 'service_id', NEW.service_id, 'match_type', NEW.match_type, 'confidence', NEW.confidence, 'priority', NEW.priority, 'created_at', NEW.created_at, 'book_override', NEW.book_override, 'type_override_id', NEW.type_override_id, 'min_amount_minor', NEW.min_amount_minor, 'max_amount_minor', NEW.max_amount_minor));
END;
CREATE TRIGGER history_merchant_rules_update AFTER UPDATE ON merchant_rules
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'pattern', OLD.pattern, 'service_id', OLD.service_id, 'match_type', OLD.match_type, 'confidence', OLD.confidence, 'priority', OLD.priority, 'created_at', OLD.created_at, 'book_override', OLD.book_override, 'type_override_id', OLD.type_override_id, 'min_amount_minor', OLD.min_amount_minor, 'max_amount_minor', OLD.max_amount_minor) IS NOT json_object('id', NEW.id, 'pattern', NEW.pattern, 'service_id', NEW.service_id, 'match_type', NEW.match_type, 'confidence', NEW.confidence, 'priority', NEW.priority, 'created_at', NEW.created_at, 'book_override', NEW.book_override, 'type_override_id', NEW.type_override_id, 'min_amount_minor', NEW.min_amount_minor, 'max_amount_minor', NEW.max_amount_minor)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'merchant_rules', NEW.id, 'update',
        json_object('id', OLD.id, 'pattern', OLD.pattern, 'service_id', OLD.service_id, 'match_type', OLD.match_type, 'confidence', OLD.confidence, 'priority', OLD.priority, 'created_at', OLD.created_at, 'book_override', OLD.book_override, 'type_override_id', OLD.type_override_id, 'min_amount_minor', OLD.min_amount_minor, 'max_amount_minor', OLD.max_amount_minor),
        json_object('id', NEW.id, 'pattern', NEW.pattern, 'service_id', NEW.service_id, 'match_type', NEW.match_type, 'confidence', NEW.confidence, 'priority', NEW.priority, 'created_at', NEW.created_at, 'book_override', NEW.book_override, 'type_override_id', NEW.type_override_id, 'min_amount_minor', NEW.min_amount_minor, 'max_amount_minor', NEW.max_amount_minor));
END;
CREATE TRIGGER history_merchant_rules_delete AFTER DELETE ON merchant_rules
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'merchant_rules', OLD.id, 'delete',
        json_object('id', OLD.id, 'pattern', OLD.pattern, 'service_id', OLD.service_id, 'match_type', OLD.match_type, 'confidence', OLD.confidence, 'priority', OLD.priority, 'created_at', OLD.created_at, 'book_override', OLD.book_override, 'type_override_id', OLD.type_override_id, 'min_amount_minor', OLD.min_amount_minor, 'max_amount_minor', OLD.max_amount_minor), NULL);
END;
CREATE TRIGGER history_accounts_insert AFTER INSERT ON accounts
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'accounts', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'name', NEW.name, 'short_name', NEW.short_name, 'type', NEW.type, 'last_four', NEW.last_four, 'currency', NEW.currency, 'status', NEW.status, 'created_at', NEW.created_at, 'owner', NEW.owner));
END;
CREATE TRIGGER history_accounts_update AFTER UPDATE ON accounts
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'name', OLD.name, 'short_name', OLD.short_name, 'type', OLD.type, 'last_four', OLD.last_four, 'currency', OLD.currency, 'status', OLD.status, 'created_at', OLD.created_at, 'owner', OLD.owner) IS NOT json_object('id', NEW.id, 'name', NEW.name, 'short_name', NEW.short_name, 'type', NEW.type, 'last_four', NEW.last_four, 'currency', NEW.currency, 'status', NEW.status, 'created_at', NEW.created_at, 'owner', NEW.owner)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'accounts', NEW.id, 'update',
        json_object('id', OLD.id, 'name', OLD.name, 'short_name', OLD.short_name, 'type', OLD.type, 'last_four', OLD.last_four, 'currency', OLD.currency, 'status', OLD.status, 'created_at', OLD.created_at, 'owner', OLD.owner),
        json_object('id', NEW.id, 'name', NEW.name, 'short_name', NEW.short_name, 'type', NEW.type, 'last_four', NEW.last_four, 'currency', NEW.currency, 'status', NEW.status, 'created_at', NEW.created_at, 'owner', NEW.owner));
END;
CREATE TRIGGER history_accounts_delete AFTER DELETE ON accounts
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'accounts', OLD.id, 'delete',
        json_object('id', OLD.id, 'name', OLD.name, 'short_name', OLD.short_name, 'type', OLD.type, 'last_four', OLD.last_four, 'currency', OLD.currency, 'status', OLD.status, 'created_at', OLD.created_at, 'owner', OLD.owner), NULL);
END;
CREATE TRIGGER history_rates_insert AFTER INSERT ON rates
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'rates', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'pair', NEW.pair, 'date', NEW.date, 'rate', NEW.rate, 'source', NEW.source, 'fetched_at', NEW.fetched_at));
END;
CREATE TRIGGER history_rates_update AFTER UPDATE ON rates
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'pair', OLD.pair, 'date', OLD.date, 'rate', OLD.rate, 'source', OLD.source, 'fetched_at', OLD.fetched_at) IS NOT json_object('id', NEW.id, 'pair', NEW.pair, 'date', NEW.date, 'rate', NEW.rate, 'source', NEW.source, 'fetched_at', NEW.fetched_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'rates', NEW.id, 'update',
        json_object('id', OLD.id, 'pair', OLD.pair, 'date', OLD.date, 'rate', OLD.rate, 'source', OLD.source, 'fetched_at', OLD.fetched_at),
        json_object('id', NEW.id, 'pair', NEW.pair, 'date', NEW.date, 'rate', NEW.rate, 'source', NEW.source, 'fetched_at', NEW.fetched_at));
END;
CREATE TRIGGER history_rates_delete AFTER DELETE ON rates
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'rates', OLD.id, 'delete',
        json_object('id', OLD.id, 'pair', OLD.pair, 'date', OLD.date, 'rate', OLD.rate, 'source', OLD.source, 'fetched_at', OLD.fetched_at), NULL);
END;
CREATE TRIGGER history_statements_insert AFTER INSERT ON statements
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'statements', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'account_id', NEW.account_id, 'statement_date', NEW.statement_date, 'filename', NEW.filename, 'imported_at', NEW.imported_at, 'printed', NEW.printed));
END;
CREATE TRIGGER history_statements_update AFTER UPDATE ON statements
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'account_id', OLD.account_id, 'statement_date', OLD.statement_date, 'filename', OLD.filename, 'imported_at', OLD.imported_at, 'printed', OLD.printed) IS NOT json_object('id', NEW.id, 'account_id', NEW.account_id, 'statement_date', NEW.statement_date, 'filename', NEW.filename, 'imported_at', NEW.imported_at, 'printed', NEW.printed)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'statements', NEW.id, 'update',
        json_object('id', OLD.id, 'account_id', OLD.account_id, 'statement_date', OLD.statement_date, 'filename', OLD.filename, 'imported_at', OLD.imported_at, 'printed', OLD.printed),
        json_object('id', NEW.id, 'account_id', NEW.account_id, 'statement_date', NEW.statement_date, 'filename', NEW.filename, 'imported_at', NEW.imported_at, 'printed', NEW.printed));
END;
CREATE TRIGGER history_statements_delete AFTER DELETE ON statements
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'statements', OLD.id, 'delete',
        json_object('id', OLD.id, 'account_id', OLD.account_id, 'statement_date', OLD.statement_date, 'filename', OLD.filename, 'imported_at', OLD.imported_at, 'printed', OLD.printed), NULL);
END;
CREATE TRIGGER history_subscriptions_insert AFTER INSERT ON subscriptions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'subscriptions', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'service_id', NEW.service_id, 'amount', NEW.amount, 'currency', NEW.currency, 'frequency', NEW.frequency, 'periods', NEW.periods, 'account_id', NEW.account_id, 'last_paid', NEW.last_paid, 'renewal_date', NEW.renewal_date, 'status', NEW.status, 'link', NEW.link, 'notes', NEW.notes, 'match_pattern', NEW.match_pattern, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id));
END;
CREATE TRIGGER history_subscriptions_update AFTER UPDATE ON subscriptions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'service_id', OLD.service_id, 'amount', OLD.amount, 'currency', OLD.currency, 'frequency', OLD.frequency, 'periods', OLD.periods, 'account_id', OLD.account_id, 'last_paid', OLD.last_paid, 'renewal_date', OLD.renewal_date, 'status', OLD.status, 'link', OLD.link, 'notes', OLD.notes, 'match_pattern', OLD.match_pattern, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id) IS NOT json_object('id', NEW.id, 'service_id', NEW.service_id, 'amount', NEW.amount, 'currency', NEW.currency, 'frequency', NEW.frequency, 'periods', NEW.periods, 'account_id', NEW.account_id, 'last_paid', NEW.last_paid, 'renewal_date', NEW.renewal_date, 'status', NEW.status, 'link', NEW.link, 'notes', NEW.notes, 'match_pattern', NEW.match_pattern, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'subscriptions', NEW.id, 'update',
        json_object('id', OLD.id, 'service_id', OLD.service_id, 'amount', OLD.amount, 'currency', OLD.currency, 'frequency', OLD.frequency, 'periods', OLD.periods, 'account_id', OLD.account_id, 'last_paid', OLD.last_paid, 'renewal_date', OLD.renewal_date, 'status', OLD.status, 'link', OLD.link, 'notes', OLD.notes, 'match_pattern', OLD.match_pattern, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id),
        json_object('id', NEW.id, 'service_id', NEW.service_id, 'amount', NEW.amount, 'currency', NEW.currency, 'frequency', NEW.frequency, 'periods', NEW.periods, 'account_id', NEW.account_id, 'last_paid', NEW.last_paid, 'renewal_date', NEW.renewal_date, 'status', NEW.status, 'link', NEW.link, 'notes', NEW.notes, 'match_pattern', NEW.match_pattern, 'created_at', NEW.created_at, 'book', NEW.book, 'type_id', NEW.type_id));
END;
CREATE TRIGGER history_subscriptions_delete AFTER DELETE ON subscriptions
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'subscriptions', OLD.id, 'delete',
        json_object('id', OLD.id, 'service_id', OLD.service_id, 'amount', OLD.amount, 'currency', OLD.currency, 'frequency', OLD.frequency, 'periods', OLD.periods, 'account_id', OLD.account_id, 'last_paid', OLD.last_paid, 'renewal_date', OLD.renewal_date, 'status', OLD.status, 'link', OLD.link, 'notes', OLD.notes, 'match_pattern', OLD.match_pattern, 'created_at', OLD.created_at, 'book', OLD.book, 'type_id', OLD.type_id), NULL);
END;
CREATE TRIGGER history_expectation_insert AFTER INSERT ON expectation
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'expectation', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'row_id', NEW.row_id, 'person_id', NEW.person_id, 'opening_date', NEW.opening_date, 'direction', NEW.direction, 'amount_minor', NEW.amount_minor, 'currency', NEW.currency, 'note', NEW.note, 'made_at', NEW.made_at, 'source', NEW.source));
END;
CREATE TRIGGER history_expectation_update AFTER UPDATE ON expectation
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'row_id', OLD.row_id, 'person_id', OLD.person_id, 'opening_date', OLD.opening_date, 'direction', OLD.direction, 'amount_minor', OLD.amount_minor, 'currency', OLD.currency, 'note', OLD.note, 'made_at', OLD.made_at, 'source', OLD.source) IS NOT json_object('id', NEW.id, 'row_id', NEW.row_id, 'person_id', NEW.person_id, 'opening_date', NEW.opening_date, 'direction', NEW.direction, 'amount_minor', NEW.amount_minor, 'currency', NEW.currency, 'note', NEW.note, 'made_at', NEW.made_at, 'source', NEW.source)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'expectation', NEW.id, 'update',
        json_object('id', OLD.id, 'row_id', OLD.row_id, 'person_id', OLD.person_id, 'opening_date', OLD.opening_date, 'direction', OLD.direction, 'amount_minor', OLD.amount_minor, 'currency', OLD.currency, 'note', OLD.note, 'made_at', OLD.made_at, 'source', OLD.source),
        json_object('id', NEW.id, 'row_id', NEW.row_id, 'person_id', NEW.person_id, 'opening_date', NEW.opening_date, 'direction', NEW.direction, 'amount_minor', NEW.amount_minor, 'currency', NEW.currency, 'note', NEW.note, 'made_at', NEW.made_at, 'source', NEW.source));
END;
CREATE TRIGGER history_expectation_delete AFTER DELETE ON expectation
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'expectation', OLD.id, 'delete',
        json_object('id', OLD.id, 'row_id', OLD.row_id, 'person_id', OLD.person_id, 'opening_date', OLD.opening_date, 'direction', OLD.direction, 'amount_minor', OLD.amount_minor, 'currency', OLD.currency, 'note', OLD.note, 'made_at', OLD.made_at, 'source', OLD.source), NULL);
END;
CREATE TRIGGER history_expectation_name_insert AFTER INSERT ON expectation_name
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'expectation_name', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'expectation_id', NEW.expectation_id, 'person_id', NEW.person_id));
END;
CREATE TRIGGER history_expectation_name_update AFTER UPDATE ON expectation_name
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'expectation_id', OLD.expectation_id, 'person_id', OLD.person_id) IS NOT json_object('id', NEW.id, 'expectation_id', NEW.expectation_id, 'person_id', NEW.person_id)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'expectation_name', NEW.id, 'update',
        json_object('id', OLD.id, 'expectation_id', OLD.expectation_id, 'person_id', OLD.person_id),
        json_object('id', NEW.id, 'expectation_id', NEW.expectation_id, 'person_id', NEW.person_id));
END;
CREATE TRIGGER history_expectation_name_delete AFTER DELETE ON expectation_name
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'expectation_name', OLD.id, 'delete',
        json_object('id', OLD.id, 'expectation_id', OLD.expectation_id, 'person_id', OLD.person_id), NULL);
END;
CREATE TRIGGER history_set_off_insert AFTER INSERT ON set_off
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'set_off', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'payment_row_id', NEW.payment_row_id, 'expectation_id', NEW.expectation_id, 'person_id', NEW.person_id, 'note', NEW.note, 'amount_minor', NEW.amount_minor, 'item_amount_minor', NEW.item_amount_minor, 'rate', NEW.rate, 'rate_date', NEW.rate_date, 'made_at', NEW.made_at, 'source', NEW.source));
END;
CREATE TRIGGER history_set_off_update AFTER UPDATE ON set_off
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'payment_row_id', OLD.payment_row_id, 'expectation_id', OLD.expectation_id, 'person_id', OLD.person_id, 'note', OLD.note, 'amount_minor', OLD.amount_minor, 'item_amount_minor', OLD.item_amount_minor, 'rate', OLD.rate, 'rate_date', OLD.rate_date, 'made_at', OLD.made_at, 'source', OLD.source) IS NOT json_object('id', NEW.id, 'payment_row_id', NEW.payment_row_id, 'expectation_id', NEW.expectation_id, 'person_id', NEW.person_id, 'note', NEW.note, 'amount_minor', NEW.amount_minor, 'item_amount_minor', NEW.item_amount_minor, 'rate', NEW.rate, 'rate_date', NEW.rate_date, 'made_at', NEW.made_at, 'source', NEW.source)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'set_off', NEW.id, 'update',
        json_object('id', OLD.id, 'payment_row_id', OLD.payment_row_id, 'expectation_id', OLD.expectation_id, 'person_id', OLD.person_id, 'note', OLD.note, 'amount_minor', OLD.amount_minor, 'item_amount_minor', OLD.item_amount_minor, 'rate', OLD.rate, 'rate_date', OLD.rate_date, 'made_at', OLD.made_at, 'source', OLD.source),
        json_object('id', NEW.id, 'payment_row_id', NEW.payment_row_id, 'expectation_id', NEW.expectation_id, 'person_id', NEW.person_id, 'note', NEW.note, 'amount_minor', NEW.amount_minor, 'item_amount_minor', NEW.item_amount_minor, 'rate', NEW.rate, 'rate_date', NEW.rate_date, 'made_at', NEW.made_at, 'source', NEW.source));
END;
CREATE TRIGGER history_set_off_delete AFTER DELETE ON set_off
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'set_off', OLD.id, 'delete',
        json_object('id', OLD.id, 'payment_row_id', OLD.payment_row_id, 'expectation_id', OLD.expectation_id, 'person_id', OLD.person_id, 'note', OLD.note, 'amount_minor', OLD.amount_minor, 'item_amount_minor', OLD.item_amount_minor, 'rate', OLD.rate, 'rate_date', OLD.rate_date, 'made_at', OLD.made_at, 'source', OLD.source), NULL);
END;
CREATE TRIGGER history_let_go_insert AFTER INSERT ON let_go
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'let_go', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'expectation_id', NEW.expectation_id, 'amount_minor', NEW.amount_minor, 'became', NEW.became, 'book', NEW.book, 'type_id', NEW.type_id, 'to_whom_id', NEW.to_whom_id, 'counts_on', NEW.counts_on, 'made_at', NEW.made_at));
END;
CREATE TRIGGER history_let_go_update AFTER UPDATE ON let_go
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'expectation_id', OLD.expectation_id, 'amount_minor', OLD.amount_minor, 'became', OLD.became, 'book', OLD.book, 'type_id', OLD.type_id, 'to_whom_id', OLD.to_whom_id, 'counts_on', OLD.counts_on, 'made_at', OLD.made_at) IS NOT json_object('id', NEW.id, 'expectation_id', NEW.expectation_id, 'amount_minor', NEW.amount_minor, 'became', NEW.became, 'book', NEW.book, 'type_id', NEW.type_id, 'to_whom_id', NEW.to_whom_id, 'counts_on', NEW.counts_on, 'made_at', NEW.made_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'let_go', NEW.id, 'update',
        json_object('id', OLD.id, 'expectation_id', OLD.expectation_id, 'amount_minor', OLD.amount_minor, 'became', OLD.became, 'book', OLD.book, 'type_id', OLD.type_id, 'to_whom_id', OLD.to_whom_id, 'counts_on', OLD.counts_on, 'made_at', OLD.made_at),
        json_object('id', NEW.id, 'expectation_id', NEW.expectation_id, 'amount_minor', NEW.amount_minor, 'became', NEW.became, 'book', NEW.book, 'type_id', NEW.type_id, 'to_whom_id', NEW.to_whom_id, 'counts_on', NEW.counts_on, 'made_at', NEW.made_at));
END;
CREATE TRIGGER history_let_go_delete AFTER DELETE ON let_go
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'let_go', OLD.id, 'delete',
        json_object('id', OLD.id, 'expectation_id', OLD.expectation_id, 'amount_minor', OLD.amount_minor, 'became', OLD.became, 'book', OLD.book, 'type_id', OLD.type_id, 'to_whom_id', OLD.to_whom_id, 'counts_on', OLD.counts_on, 'made_at', OLD.made_at), NULL);
END;
CREATE TRIGGER history_refund_link_insert AFTER INSERT ON refund_link
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'refund_link', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'refund_row_id', NEW.refund_row_id, 'purchase_row_id', NEW.purchase_row_id, 'source', NEW.source, 'made_at', NEW.made_at));
END;
CREATE TRIGGER history_refund_link_update AFTER UPDATE ON refund_link
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'refund_row_id', OLD.refund_row_id, 'purchase_row_id', OLD.purchase_row_id, 'source', OLD.source, 'made_at', OLD.made_at) IS NOT json_object('id', NEW.id, 'refund_row_id', NEW.refund_row_id, 'purchase_row_id', NEW.purchase_row_id, 'source', NEW.source, 'made_at', NEW.made_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'refund_link', NEW.id, 'update',
        json_object('id', OLD.id, 'refund_row_id', OLD.refund_row_id, 'purchase_row_id', OLD.purchase_row_id, 'source', OLD.source, 'made_at', OLD.made_at),
        json_object('id', NEW.id, 'refund_row_id', NEW.refund_row_id, 'purchase_row_id', NEW.purchase_row_id, 'source', NEW.source, 'made_at', NEW.made_at));
END;
CREATE TRIGGER history_refund_link_delete AFTER DELETE ON refund_link
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'refund_link', OLD.id, 'delete',
        json_object('id', OLD.id, 'refund_row_id', OLD.refund_row_id, 'purchase_row_id', OLD.purchase_row_id, 'source', OLD.source, 'made_at', OLD.made_at), NULL);
END;
CREATE TRIGGER history_sender_wording_insert AFTER INSERT ON sender_wording
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'sender_wording', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'wording', NEW.wording, 'person_id', NEW.person_id, 'learnt_at', NEW.learnt_at));
END;
CREATE TRIGGER history_sender_wording_update AFTER UPDATE ON sender_wording
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'wording', OLD.wording, 'person_id', OLD.person_id, 'learnt_at', OLD.learnt_at) IS NOT json_object('id', NEW.id, 'wording', NEW.wording, 'person_id', NEW.person_id, 'learnt_at', NEW.learnt_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'sender_wording', NEW.id, 'update',
        json_object('id', OLD.id, 'wording', OLD.wording, 'person_id', OLD.person_id, 'learnt_at', OLD.learnt_at),
        json_object('id', NEW.id, 'wording', NEW.wording, 'person_id', NEW.person_id, 'learnt_at', NEW.learnt_at));
END;
CREATE TRIGGER history_sender_wording_delete AFTER DELETE ON sender_wording
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'sender_wording', OLD.id, 'delete',
        json_object('id', OLD.id, 'wording', OLD.wording, 'person_id', OLD.person_id, 'learnt_at', OLD.learnt_at), NULL);
END;
CREATE TRIGGER history_dismissal_insert AFTER INSERT ON dismissal
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'dismissal', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'row_id', NEW.row_id, 'kind', NEW.kind, 'purchase_row_id', NEW.purchase_row_id, 'made_at', NEW.made_at));
END;
CREATE TRIGGER history_dismissal_update AFTER UPDATE ON dismissal
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'row_id', OLD.row_id, 'kind', OLD.kind, 'purchase_row_id', OLD.purchase_row_id, 'made_at', OLD.made_at) IS NOT json_object('id', NEW.id, 'row_id', NEW.row_id, 'kind', NEW.kind, 'purchase_row_id', NEW.purchase_row_id, 'made_at', NEW.made_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'dismissal', NEW.id, 'update',
        json_object('id', OLD.id, 'row_id', OLD.row_id, 'kind', OLD.kind, 'purchase_row_id', OLD.purchase_row_id, 'made_at', OLD.made_at),
        json_object('id', NEW.id, 'row_id', NEW.row_id, 'kind', NEW.kind, 'purchase_row_id', NEW.purchase_row_id, 'made_at', NEW.made_at));
END;
CREATE TRIGGER history_dismissal_delete AFTER DELETE ON dismissal
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'dismissal', OLD.id, 'delete',
        json_object('id', OLD.id, 'row_id', OLD.row_id, 'kind', OLD.kind, 'purchase_row_id', OLD.purchase_row_id, 'made_at', OLD.made_at), NULL);
END;
CREATE TRIGGER history_types_insert AFTER INSERT ON types
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'types', NEW.id, 'insert', NULL,
        json_object('id', NEW.id, 'kind', NEW.kind, 'name', NEW.name, 'parent_id', NEW.parent_id, 'default_one_off', NEW.default_one_off, 'covers', NEW.covers, 'not_for', NEW.not_for, 'created_at', NEW.created_at));
END;
CREATE TRIGGER history_types_update AFTER UPDATE ON types
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
    AND json_object('id', OLD.id, 'kind', OLD.kind, 'name', OLD.name, 'parent_id', OLD.parent_id, 'default_one_off', OLD.default_one_off, 'covers', OLD.covers, 'not_for', OLD.not_for, 'created_at', OLD.created_at) IS NOT json_object('id', NEW.id, 'kind', NEW.kind, 'name', NEW.name, 'parent_id', NEW.parent_id, 'default_one_off', NEW.default_one_off, 'covers', NEW.covers, 'not_for', NEW.not_for, 'created_at', NEW.created_at)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'types', NEW.id, 'update',
        json_object('id', OLD.id, 'kind', OLD.kind, 'name', OLD.name, 'parent_id', OLD.parent_id, 'default_one_off', OLD.default_one_off, 'covers', OLD.covers, 'not_for', OLD.not_for, 'created_at', OLD.created_at),
        json_object('id', NEW.id, 'kind', NEW.kind, 'name', NEW.name, 'parent_id', NEW.parent_id, 'default_one_off', NEW.default_one_off, 'covers', NEW.covers, 'not_for', NEW.not_for, 'created_at', NEW.created_at));
END;
CREATE TRIGGER history_types_delete AFTER DELETE ON types
WHEN EXISTS (SELECT 1 FROM change_entries WHERE open = 1)
BEGIN
    INSERT INTO change_rows (entry_id, tbl, row_id, op, before, after)
    VALUES ((SELECT MAX(id) FROM change_entries WHERE open = 1), 'types', OLD.id, 'delete',
        json_object('id', OLD.id, 'kind', OLD.kind, 'name', OLD.name, 'parent_id', OLD.parent_id, 'default_one_off', OLD.default_one_off, 'covers', OLD.covers, 'not_for', OLD.not_for, 'created_at', OLD.created_at), NULL);
END;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('change_rows',1);
INSERT INTO "sqlite_sequence" VALUES('types',42);
INSERT INTO "sqlite_sequence" VALUES('services',171);
INSERT INTO "sqlite_sequence" VALUES('merchant_rules',171);
INSERT INTO "sqlite_sequence" VALUES('change_entries',1);
INSERT INTO "sqlite_sequence" VALUES('accounts',1);
INSERT INTO "sqlite_sequence" VALUES('statements',1);
INSERT INTO "sqlite_sequence" VALUES('transactions',4);
COMMIT;
