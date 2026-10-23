"""Book and type: the two labels a spending row carries.

Book says whose spending a row is; type says what kind of thing the money
was spent on. One type list serves every book. Both vocabularies are declared
here, once, with a plain description for each member, and a new book's
`types` table is filled from this declaration.

Once a book exists, its `types` table is the home of each type's wording
(fin RUL-002): the operator may add a spending type and fix a type's wording
(covers, not for) in the app, and change its settings (usual book, one-off
by default, Check each row by default; type-relationships 02), each one
entry in the change history. A later change to a declared type's wording
here does not reach a book already seeded, as `seed_types` never
overwrites; such a change goes in as a conversion step. Renaming, merging and removing a type, and every book,
stay code changes. The types code means are LOCKED: their names stay as
declared, but their wording is fixed in the app like any spending type's
(fin RUL-004).

Code never finds a type by the name it shows. Each declared type and income
kind keeps a key, its declared name frozen, in the `type_keys` table, and the
code reads the types it means through the key helpers below
(type-relationships 01). A type added in the app has no key.
"""

from __future__ import annotations

import sqlite3
import unicodedata

# (name, description). A new book is a new value here.
BOOKS = (
    ("Household", "spending of the household itself"),
    ("Moom", "costs of the company Moom, whichever account paid them"),
    ("Kalesh", "costs of the company Kalesh, whichever account paid them"),
)

BOOK_NAMES = tuple(name for name, _ in BOOKS)
# The book a row with none is read as: spending nobody has placed is the
# household's until the operator says otherwise.
DEFAULT_BOOK = "Household"

# Where a row's book, and separately its type, came from (personal-ledger 24,
# ADR-008). Each field carries its own source; a field with no value has none.
# Declared here, once, beside the books; every writer of a source validates
# against this declaration through `checked_source`, and schema.sql's CHECK on
# the two columns holds the same list (a test holds the two together).
# (name, description).
SOURCES = (
    ("merchant", "from the merchant: its default book or type"),
    ("rule", "from a rule: the book or type a rule override sets"),
    ("wording", "from the wording: the PayNow wording fallback"),
    ("checked", "checked: the operator accepted the value fin offered; a later fix still reaches it"),
    ("chosen", "chosen: the operator, or Claude in chat, set the value; nothing automatic changes it"),
    ("derived", "worked out: loan interest worked out from figures (loan_interest.py)"),
)

SOURCE_NAMES = tuple(name for name, _ in SOURCES)


class UnknownSource(ValueError):
    """A writer was handed a book or type source that is not declared."""


def checked_source(value) -> str | None:
    """A source as a writer may store it: one of the declared sources, or None
    for a field that is empty."""
    if value is None:
        return None
    if not isinstance(value, str) or value not in SOURCE_NAMES:
        raise UnknownSource(f"unknown source: {value!r}")
    return value


RUNNING = "running"
ONE_OFF = "one-off"

# (name, parent, default, covers, not for). `default` is the running / one-off
# default a row inherits. `covers` is the plain-language meaning, and is also
# what a judgment model is shown. A parent is listed before its sub-types.
SPENDING_TYPES = (
    ("Software & AI tools", None, RUNNING,
     "software, apps, hosting, databases, domains, cloud storage, developer and AI services, security software",
     "hardware; film, music or news subscriptions"),
    ("Advertising", None, RUNNING,
     "paid adverts and promotion on search, social media or marketplaces",
     "personal social media memberships"),
    ("Stock purchases", None, RUNNING,
     "goods bought wholesale to resell",
     "things bought for own use"),
    ("Payment fees", None, RUNNING,
     "charges from payment processors for taking customer payments",
     "bank account or card fees"),
    ("People", None, RUNNING,
     "payroll, contractors, freelancers, recruitment",
     "accountants, lawyers and other firms"),
    ("Professional services", None, RUNNING,
     "accountants, lawyers, company secretaries, consultants",
     "software subscriptions"),
    ("Office", None, RUNNING,
     "office rent, co-working space, meeting rooms",
     "home rent"),
    ("Equipment", None, ONE_OFF,
     "computers, phones, cameras, machines and other durable gear",
     "software; home furniture. A large asset bought by a company may be a balance-sheet movement (ticket 05)"),
    ("Bank & government fees", None, RUNNING,
     "card annual fees and the GST on them, late charges, interest charged, bank service charges, government levies and fines",
     "insurance premiums; tax; visa fees for a trip"),
    ("Insurance", None, RUNNING,
     "insurance premiums of any kind",
     "medical bills"),
    ("Tax", None, RUNNING,
     "income tax, property tax and other tax payments",
     "GST charged on a fee; government service fees"),
    ("Repairs & maintenance", None, ONE_OFF,
     "fixing or servicing a car, home or appliance; tradespeople",
     "buying new furniture or appliances"),
    ("Dining", None, RUNNING,
     "restaurants, bars, fast food, food delivery, coffee shops, cafes, bakeries and drinks shops",
     "supermarkets"),
    ("Groceries", None, RUNNING,
     "supermarkets, convenience stores, markets, food for the home",
     "restaurants"),
    ("Shopping", None, RUNNING,
     "clothes, shoes, electronics, books, furniture, accessories and general retail, in a shop or online",
     "groceries; goods for children"),
    ("Transport", None, RUNNING,
     "road tax, parking, tolls, vehicle licensing",
     "flights; car repairs"),
    ("Rides", "Transport", RUNNING,
     "taxis and ride-hailing",
     "food delivery by the same company"),
    ("Public Transit", "Transport", RUNNING,
     "buses, trains, metro and transit cards",
     None),
    ("EV Charging", "Transport", RUNNING,
     "electric vehicle charging",
     None),
    ("Fuel", "Transport", RUNNING,
     "petrol and diesel",
     None),
    ("Travel", None, RUNNING,
     "flights, hotel and rental stays, visa fees for a trip, tours and travel agencies",
     "meals while travelling; golf while travelling"),
    ("Entertainment", None, RUNNING,
     "cinema, concerts, events, tickets, games, streaming video and music",
     "sport played oneself"),
    ("Fitness", None, RUNNING,
     "gyms, classes, sports clubs",
     "golf"),
    ("Golf", "Fitness", RUNNING,
     "golf courses, country clubs, driving ranges, green fees, golf lessons",
     "golf clothing bought in an ordinary shop"),
    ("Health & Beauty", None, RUNNING,
     "hair salons, barbers, spas, skincare, personal-care pharmacy",
     "doctors and hospitals"),
    ("Medical", None, RUNNING,
     "doctors, clinics, hospitals, dentists, specialists, tests",
     "health insurance premiums"),
    ("Kids", None, RUNNING,
     "clothes and toys for children, play centres, childcare, baby goods",
     "school and course fees"),
    ("Pet", None, RUNNING,
     "vets, pet food, grooming, supplies",
     None),
    ("Education", None, RUNNING,
     "school fees, courses, tuition, exams",
     "books bought for leisure"),
    ("Home", None, RUNNING,
     "furniture, household goods, domestic help, home services",
     "rent; repairs; utilities"),
    ("Rent", None, RUNNING,
     "rent for a home",
     None),
    ("Utilities", None, RUNNING,
     "electricity, gas, water, internet, mobile phone plans",
     "streaming; software"),
    ("Gifts & Donations", None, RUNNING,
     "charity donations, gifts and flowers for others",
     None),
    ("Subscriptions", None, RUNNING,
     "paid newsletters, news, magazines and other memberships that are not software or entertainment",
     "software; streaming"),
    ("Social Media", None, RUNNING,
     "paid memberships of social networks",
     None),
    ("Other", None, RUNNING,
     "spending the operator has not placed",
     None),
)

# Income keeps a short list of its own kinds. (name, description).
INCOME_KINDS = (
    ("Salary", "pay from an employer"),
    ("Rental", "rent received for a property the household lets out"),
    ("Interest", "interest paid by a bank or on a deposit"),
    ("Gift received", "money given to the household"),
    ("Other", "income the operator has not placed"),
)

SPENDING = "spending"
INCOME = "income"

# The types code means, each by its key (its declared name, frozen; see
# `type_keys` below). Nothing outside this module names a type in quotes: a
# test fails when code does.
#
# Book for a merchant no rule knows is proposed by its type: the type's usual
# book, kept per type in the book and changed in the app (`type_settings`,
# type-relationships 02). These lists are what a start seeds it from: these
# types propose Moom; ASKS_FOR_BOOK types propose nothing and the operator
# must say; every other type proposes Household. A picker's first value may
# instead come from the operator's history or the paying account
# (book_prefill.py).
PROPOSES_MOOM = ("Advertising", "Stock purchases", "Payment fees")
ASKS_FOR_BOOK = ("Software & AI tools",)
# What fin files the loan interest it works out under (loan_interest.py).
LOAN_INTEREST = "Bank & government fees"
# What a gift is filed under: a spending type when given (the review list's
# gift, a let-go gift), an income kind when received.
GIFT_GIVEN = "Gifts & Donations"
GIFT_RECEIVED = "Gift received"
# Other means "not placed yet", and is never asked of Jev (suggest.NOT_ASKED).
NOT_PLACED = "Other"


# The spending types code means, so their names stay as declared: the app
# never renames them, though their wording is fixed like any type's (fin
# RUL-004).
LOCKED = PROPOSES_MOOM + ASKS_FOR_BOOK + (LOAN_INTEREST, GIFT_GIVEN, NOT_PLACED)


# --- a type's settings (type-relationships 02) -------------------------------
#
# The kinds are declared here; their values are data, one `type_settings` row
# per spending type: its usual book, one of USUAL_BOOKS, and whether a new
# merchant of it starts as Check each row. One-off by default is the type's
# own `default_one_off`. A change reaches new merchants only (fin RUL-006 Q3).

# The usual book that proposes none: the operator says whose spending it is.
ASK = "ask"
USUAL_BOOKS = BOOK_NAMES + (ASK,)


def _seeded_usual_book(key: str | None) -> str:
    """The usual book a start seeds a type with, from its key: what the code
    said before the settings were data. A type added in the app (no key)
    reads Household until the operator sets one."""
    if key in ASKS_FOR_BOOK:
        return ASK
    if key in PROPOSES_MOOM:
        return "Moom"
    return DEFAULT_BOOK


def settings_of(usual_book: str | None, check_each_row, key: str | None) -> tuple[str, bool]:
    """A type's (usual book, Check each row by default) from its settings row's
    two columns, None when it has no row, and its key."""
    if usual_book is None:
        return _seeded_usual_book(key), False
    return usual_book, bool(check_each_row)


def proposed(usual_book: str) -> str | None:
    """The book a usual book proposes for a new merchant; None means ask."""
    return None if usual_book == ASK else usual_book


def _settings(conn: sqlite3.Connection, type_id: int) -> tuple[str, bool]:
    found = conn.execute(
        "SELECT s.usual_book, s.check_each_row, k.key FROM types t"
        " LEFT JOIN type_settings s ON s.type_id = t.id LEFT JOIN type_keys k ON k.type_id = t.id"
        " WHERE t.id = ?",
        (type_id,),
    ).fetchone()
    return settings_of(*found) if found else (DEFAULT_BOOK, False)


def proposed_book(conn: sqlite3.Connection, type_id: int) -> str | None:
    """The book a spending type proposes for a merchant no rule knows, from
    its usual book; None means ask."""
    return proposed(_settings(conn, type_id)[0])


def proposed_books(conn: sqlite3.Connection) -> dict[int, str | None]:
    """The book each spending type proposes, by its id; None means ask."""
    return {
        type_id: proposed(settings_of(usual_book, check, key)[0])
        for type_id, usual_book, check, key in conn.execute(
            "SELECT t.id, s.usual_book, s.check_each_row, k.key FROM types t"
            " LEFT JOIN type_settings s ON s.type_id = t.id LEFT JOIN type_keys k ON k.type_id = t.id"
            " WHERE t.kind = ?",
            (SPENDING,),
        )
    }


def checks_each_row(conn: sqlite3.Connection, type_id: int | None) -> bool:
    """Whether a new merchant of this type starts as Check each row."""
    return type_id is not None and _settings(conn, type_id)[1]


def seed_settings(conn: sqlite3.Connection) -> None:
    """Give every declared spending type that has none its settings row, from
    what the code said before (_seeded_usual_book), every row the same. Insert
    if missing, never overwriting: a value the operator set survives every
    start. Run after seed_types, with no change entry open; no commit."""
    for key, type_id in keyed_ids(conn).items():
        conn.execute(
            "INSERT INTO type_settings (type_id, usual_book, check_each_row) SELECT ?, ?, 0"
            " WHERE NOT EXISTS (SELECT 1 FROM type_settings WHERE type_id = ?)",
            (type_id, _seeded_usual_book(key), type_id),
        )


# The same statement as in schema.sql, which stays the source of truth; a test
# holds the two to the same shape. The conversion runs it one statement at a
# time, which a script file cannot do inside the runner's transaction.
TYPES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS types (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL DEFAULT 'spending',
    name TEXT NOT NULL,
    parent_id INTEGER,
    default_one_off INTEGER NOT NULL DEFAULT 0,
    covers TEXT NOT NULL,
    not_for TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (parent_id) REFERENCES types(id),
    UNIQUE (kind, name)
)
"""


# The same statement as in schema.sql, which stays the source of truth (a
# test holds the two to the same shape). seed_types makes the table itself, so
# the conversion step that first seeds a book's types keys them as it does.
TYPE_KEYS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS type_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    type_id INTEGER NOT NULL UNIQUE REFERENCES types(id),
    UNIQUE (kind, key)
)
"""


def display_name(name: str, parent: str | None) -> str:
    """'Parent > Child' for a sub-type, the bare name otherwise."""
    return f"{parent} > {name}" if parent else name


def _declared() -> list[tuple]:
    """Every declared type and income kind as (kind, name, parent, default,
    covers, not for), parents before their sub-types."""
    return [(SPENDING, name, parent, default, covers, not_for)
            for name, parent, default, covers, not_for in SPENDING_TYPES] + [
        (INCOME, name, None, RUNNING, description, None) for name, description in INCOME_KINDS]


def _keyed(conn: sqlite3.Connection) -> dict[tuple[str, str], int]:
    """Each keyed type's id by (kind, key); empty on a book with no key table."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'type_keys'").fetchone() is None:
        return {}
    return {(kind, key): type_id for kind, key, type_id in conn.execute("SELECT kind, key, type_id FROM type_keys")}


def seed_types(conn: sqlite3.Connection) -> None:
    """Add every declared type and income kind the book does not hold yet,
    and give each its key.

    A declared type is found by its key. One with no key yet (every type on a
    book from before keys, or a type newly declared) is found by its declared
    name among the types no key holds, and keyed; only when none has that
    name is it added. So a type renamed in the app is never added again, and
    a type added in the app and a wording fixed there survive every start;
    nothing is overwritten or deleted. One statement at a time and no commit,
    so it can run inside a caller's transaction.
    """
    conn.execute(TYPE_KEYS_TABLE_SQL)
    keyed = _keyed(conn)
    taken = set(keyed.values())
    unkeyed = {
        (kind, name): type_id
        for type_id, kind, name in conn.execute("SELECT id, kind, name FROM types")
        if type_id not in taken
    }
    for kind, name, parent, default, covers, not_for in _declared():
        if (kind, name) in keyed:
            continue
        type_id = unkeyed.get((kind, name))
        if type_id is None:
            type_id = conn.execute(
                "INSERT INTO types (kind, name, parent_id, default_one_off, covers, not_for)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (kind, name, keyed[(kind, parent)] if parent else None,
                 1 if default == ONE_OFF else 0, covers, not_for),
            ).lastrowid
        conn.execute("INSERT INTO type_keys (kind, key, type_id) VALUES (?, ?, ?)", (kind, name, type_id))
        keyed[(kind, name)] = type_id


def types_are_seeded(conn: sqlite3.Connection) -> bool:
    """Whether the book holds every declared type and income kind, by its key
    or, on a book from before keys, by its declared name."""
    keyed = _keyed(conn)
    held = {(row[0], row[1]) for row in conn.execute("SELECT kind, name FROM types")}
    return all((kind, name) in keyed or (kind, name) in held for kind, name, *_ in _declared())


# --- the key helpers: how code finds the types it means ---------------------


def keyed_ids(conn: sqlite3.Connection, kind: str = SPENDING) -> dict[str, int]:
    """Each declared type's id (or income kind's, with kind INCOME) by its key."""
    return {key: type_id for (k, key), type_id in _keyed(conn).items() if k == kind}


def keyed_id(conn: sqlite3.Connection, key: str, kind: str = SPENDING) -> int:
    """The id of the declared type (or income kind) with this key. Every
    declared one has its key from the first start, so a missing key is an
    error, never a type to do without."""
    found = conn.execute("SELECT type_id FROM type_keys WHERE kind = ? AND key = ?", (kind, key)).fetchone()
    if found is None:
        raise LookupError(f"no {kind} type has the key {key!r}")
    return found[0]


def keys_by_id(conn: sqlite3.Connection, kind: str = SPENDING) -> dict[int, str]:
    """Each keyed type's key by its id; a type added in the app is not here."""
    return {type_id: key for key, type_id in keyed_ids(conn, kind).items()}


def spending_type_ids(conn: sqlite3.Connection) -> dict[str, int]:
    """Each spending type's id, keyed by its display name ('Fitness > Golf')."""
    return {
        display_name(row[1], row[2]): row[0]
        for row in conn.execute(
            "SELECT t.id, t.name, p.name FROM types t"
            " LEFT JOIN types p ON p.id = t.parent_id WHERE t.kind = ?",
            (SPENDING,),
        )
    }


def spending_type_names(conn: sqlite3.Connection) -> dict[int, str]:
    """Each spending type's display name ('Fitness > Golf'), keyed by its id."""
    return {type_id: name for name, type_id in spending_type_ids(conn).items()}


# --- the app's two type writes (fin RUL-002) ---------------------------------

NAME_MOST = 40
WORDING_MOST = 300
WORDING_FIELDS = ("covers", "not_for")
ADD_FIELDS = ("name", "parent_id", "one_off", "covers", "not_for", "kind")
CODE_CHANGE = "renaming, merging or removing a type is a code change"

# Every column that points at a type, as schema.sql declares it: a type added
# in the app is taken out again, by Undo, only while none of them names it. A
# test holds this list to schema.sql's foreign keys.
USED_BY = (
    ("type_keys", "type_id"),
    ("transactions", "type_id"),
    ("subscriptions", "type_id"),
    ("services", "type_id"),
    ("merchant_rules", "type_override_id"),
    ("suggestion_answers", "chosen_type_id"),
    ("let_go", "type_id"),
    ("type_settings", "type_id"),
)


class TypeRefused(ValueError):
    """A type write was refused. Nothing was written."""


class NoSuchType(LookupError):
    """No type has that id."""


def _words(value, what: str) -> str | None:
    """Words as stored: trimmed, and None for none."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeRefused(f"{what} must be words")
    return value.strip() or None


def _controls(text: str) -> bool:
    """Whether words hold a control character (a line break, a tab, an escape)
    or a format character: one that shows nothing (a zero-width space) or turns
    the text after it around (a right-to-left override)."""
    return any(unicodedata.category(c) in ("Cc", "Cf") for c in text)


def _wording(value, what: str) -> str | None:
    """A type's wording as stored: any run of spaces and line breaks is one
    space, since it is read on one line and sent to Jev inside his question;
    any other control character is refused."""
    words = _words(value, what)
    if words is None:
        return None
    words = " ".join(words.split())
    if _controls(words):
        raise TypeRefused("the wording cannot hold control characters")
    return words


def _covers(value) -> str:
    covers = _wording(value, "covers")
    if covers is None:
        raise TypeRefused("a type needs words for what it covers")
    if len(covers) > WORDING_MOST:
        raise TypeRefused(f"what a type covers can be at most {WORDING_MOST} characters")
    return covers


def _not_for(value) -> str | None:
    not_for = _wording(value, "not_for")
    if not_for is not None and len(not_for) > WORDING_MOST:
        raise TypeRefused(f"what a type is not for can be at most {WORDING_MOST} characters")
    return not_for


def _spending_named(conn: sqlite3.Connection, name: str) -> str | None:
    """The display name of the spending type with this name, ignoring case,
    wherever it sits in the list; None when there is none."""
    wanted = name.casefold()
    for held, parent in conn.execute(
        "SELECT t.name, p.name FROM types t LEFT JOIN types p ON p.id = t.parent_id WHERE t.kind = ?",
        (SPENDING,),
    ):
        if held.casefold() == wanted:
            return display_name(held, parent)
    return None


def add_spending_type(conn: sqlite3.Connection, data: dict) -> int:
    """Add a spending type the operator named, and return its id. The name is
    unique ignoring case across every spending type (the table holds names
    unique, whatever the parent); the parent, when there is one, is a
    top-level spending type. Refused, writing nothing, with the reason in
    words. No commit: the caller owns it."""
    import suggest  # imported here: it imports this module

    unknown = [k for k in data if k not in ADD_FIELDS]
    if unknown:
        raise TypeRefused(f"unknown field: {unknown[0]}")
    if data.get("kind", SPENDING) != SPENDING:
        raise TypeRefused("only a spending type can be added here")
    name = _words(data.get("name"), "name")
    if name is None:
        raise TypeRefused("a type needs a name")
    if ">" in name:
        raise TypeRefused('a type\'s name cannot hold ">"')
    # Read on every screen and in Jev's question: no markup, one line.
    if "<" in name:
        raise TypeRefused('a type\'s name cannot hold "<"')
    if _controls(name):
        raise TypeRefused("a type's name cannot hold a line break or control character")
    # A run of spaces, of any width, is one space: two names that read alike are one name.
    name = " ".join(name.split())
    if len(name) > NAME_MOST:
        raise TypeRefused(f"a type's name can be at most {NAME_MOST} characters")
    if " ".join(name.replace("_", " ").split()).casefold() == suggest.NONE_OF_THESE.replace("_", " "):
        raise TypeRefused('"None of these" is what Jev answers when no type fits')
    held = _spending_named(conn, name)
    if held is not None:
        raise TypeRefused(f"{held} is already a type")
    parent_id = data.get("parent_id")
    if parent_id is not None:
        top = None
        if isinstance(parent_id, int) and not isinstance(parent_id, bool):
            top = conn.execute(
                "SELECT id FROM types WHERE id = ? AND kind = ? AND parent_id IS NULL",
                (parent_id, SPENDING),
            ).fetchone()
        if top is None:
            raise TypeRefused("a type can go only under a top-level spending type")
    one_off = data.get("one_off", False)
    if not isinstance(one_off, bool):
        raise TypeRefused("one_off must be true or false")
    covers, not_for = _covers(data.get("covers")), _not_for(data.get("not_for"))
    try:
        cur = conn.execute(
            "INSERT INTO types (kind, name, parent_id, default_one_off, covers, not_for)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (SPENDING, name, parent_id, 1 if one_off else 0, covers, not_for),
        )
    except sqlite3.IntegrityError:
        raise TypeRefused(f"{name} is already a type") from None
    return cur.lastrowid


SETTING_FIELDS = ("usual_book", "one_off", "check_each_row")


def change_type(conn: sqlite3.Connection, type_id: int, data: dict) -> dict:
    """Change a spending type: its wording (covers, not_for) and its settings
    (usual_book, one_off, check_each_row), any of them in one request.
    Anything else is a code change and refused, as is an income kind, and
    Other's settings (its meaning is fixed); nothing is written. A LOCKED
    type's wording and settings are changed here too, its name never (fin
    RUL-004). Returns what changed, field by new value, with "wording" for
    the wording. No commit."""
    found = conn.execute(
        "SELECT t.kind, t.covers, t.not_for, t.default_one_off, s.usual_book, s.check_each_row, k.key"
        " FROM types t LEFT JOIN type_settings s ON s.type_id = t.id"
        " LEFT JOIN type_keys k ON k.type_id = t.id WHERE t.id = ?",
        (type_id,),
    ).fetchone()
    if found is None:
        raise NoSuchType(type_id)
    kind, covers, not_for, one_off, usual_book, check, key = found
    if kind != SPENDING:
        raise TypeRefused("income kinds are set in code")
    if any(k not in WORDING_FIELDS + SETTING_FIELDS for k in data):
        raise TypeRefused(CODE_CHANGE)
    if not data:
        raise TypeRefused("say what to change about the type")
    usual_book, check = settings_of(usual_book, check, key)
    was = {"usual_book": usual_book, "one_off": bool(one_off), "check_each_row": check,
           "wording": (covers, not_for)}
    now = dict(was)
    if "usual_book" in data and data["usual_book"] not in USUAL_BOOKS:
        raise TypeRefused(f"the usual book is one of {', '.join(BOOK_NAMES)} or {ASK}")
    for field in ("one_off", "check_each_row"):
        if field in data and not isinstance(data[field], bool):
            raise TypeRefused(f"{field} must be true or false")
    if key == NOT_PLACED and any(k in SETTING_FIELDS for k in data):
        raise TypeRefused(f"{NOT_PLACED} means not placed yet: its settings are fixed")
    now.update({k: data[k] for k in SETTING_FIELDS if k in data})
    now["wording"] = (_covers(data["covers"]) if "covers" in data else covers,
                      _not_for(data["not_for"]) if "not_for" in data else not_for)
    conn.execute(
        "UPDATE types SET covers = ?, not_for = ?, default_one_off = ? WHERE id = ?",
        (*now["wording"], 1 if now["one_off"] else 0, type_id),
    )
    if (now["usual_book"], now["check_each_row"]) != (was["usual_book"], was["check_each_row"]):
        conn.execute(
            "INSERT INTO type_settings (type_id, usual_book, check_each_row) VALUES (?, ?, ?)"
            " ON CONFLICT (type_id) DO UPDATE SET usual_book = excluded.usual_book,"
            " check_each_row = excluded.check_each_row",
            (type_id, now["usual_book"], 1 if now["check_each_row"] else 0),
        )
    return {k: v for k, v in now.items() if v != was[k]}


def refusal_to_undo(conn: sqlite3.Connection, entry_id: int) -> str | None:
    """Why undoing an entry would take out a type something still uses, in
    words; None when it would not. Read before anything is written back."""
    added = [r[0] for r in conn.execute(
        "SELECT DISTINCT row_id FROM change_rows WHERE entry_id = ? AND tbl = 'types' AND op = 'insert'",
        (entry_id,),
    )]
    names = spending_type_names(conn)
    for type_id in added:
        shown = names.get(type_id)
        if shown is None:
            continue  # gone already, or an income kind, which the app never adds
        # A type's settings row is made by a later change, never by its add.
        if conn.execute("SELECT 1 FROM type_settings WHERE type_id = ?", (type_id,)).fetchone():
            return f"{shown}'s settings were changed since; undo that first"
        for table, column in USED_BY:
            # Both names come from USED_BY above, never from a request.
            if conn.execute(f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1", (type_id,)).fetchone():
                return f"a merchant or row still uses {shown}; change those first"
        if conn.execute("SELECT 1 FROM types WHERE parent_id = ? LIMIT 1", (type_id,)).fetchone():
            return f"a type still sits under {shown}; undo that one first"
    return None
