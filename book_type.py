"""Book and type: the two labels a spending row carries.

Book says whose spending a row is; type says what kind of thing the money
was spent on. One type list serves every book. Both vocabularies are declared
here, once, with a plain description for each member, and a new book's
`types` table is filled from this declaration.

Once a book exists, its `types` table is the home of each type's wording
(fin RUL-002): the operator may add a spending type and fix a type's wording
(covers, not for) in the app, each one entry in the change history. A later
change to a declared type's wording here does not reach a book already
seeded, as `seed_types` never overwrites; such a change goes in as a
conversion step, as a rename does. Renaming, merging and removing a type,
and every book, stay code changes. The types code reads by name are LOCKED:
the app does not fix their wording either.
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

# Book for a merchant no rule knows is proposed by its type. These types
# propose Moom; ASKS_FOR_BOOK types propose nothing and the operator must say;
# every other type proposes Household. A picker's first value may instead come
# from the operator's history or the paying account (book_prefill.py).
PROPOSES_MOOM = ("Advertising", "Stock purchases", "Payment fees")
ASKS_FOR_BOOK = ("Software & AI tools",)


# The types code reads by name, so their meaning is the code's: the app
# neither renames them nor fixes their wording. The book-proposing lists above,
# loan interest's type (loan_interest.TYPE_NAME), a gift given
# (review.GIFT_GIVEN_TYPE, letgo_api.GIFTS), and Other, which means "not
# placed yet" and is never asked of Jev (suggest.NOT_ASKED). A test fails
# when code reads a type by a name not in this list.
LOCKED = PROPOSES_MOOM + ASKS_FOR_BOOK + ("Bank & government fees", "Gifts & Donations", "Other")


def proposed_book(type_name: str | None) -> str | None:
    """The book proposed for a type (its display name); None means ask."""
    if type_name in ASKS_FOR_BOOK:
        return None
    if type_name in PROPOSES_MOOM:
        return "Moom"
    return DEFAULT_BOOK

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


def display_name(name: str, parent: str | None) -> str:
    """'Parent > Child' for a sub-type, the bare name otherwise."""
    return f"{parent} > {name}" if parent else name


def seed_types(conn: sqlite3.Connection) -> None:
    """Add every declared type and income kind the table does not hold yet.

    Safe to run again: a name already present is left as it is, wording and
    all, so a type added in the app and a wording fixed there survive every
    start; nothing is overwritten or deleted. One statement at a time and no
    commit, so it can run inside a caller's transaction.
    """
    held = {
        (row[0], row[1]): row[2]
        for row in conn.execute("SELECT kind, name, id FROM types")
    }
    for name, parent, default, covers, not_for in SPENDING_TYPES:
        if (SPENDING, name) in held:
            continue
        cur = conn.execute(
            "INSERT INTO types (kind, name, parent_id, default_one_off, covers, not_for)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (SPENDING, name, held[(SPENDING, parent)] if parent else None,
             1 if default == ONE_OFF else 0, covers, not_for),
        )
        held[(SPENDING, name)] = cur.lastrowid
    for name, description in INCOME_KINDS:
        if (INCOME, name) in held:
            continue
        conn.execute(
            "INSERT INTO types (kind, name, covers) VALUES (?, ?, ?)",
            (INCOME, name, description),
        )


def types_are_seeded(conn: sqlite3.Connection) -> bool:
    """Whether the table holds every declared type and income kind."""
    held = {(row[0], row[1]) for row in conn.execute("SELECT kind, name FROM types")}
    declared = {(SPENDING, t[0]) for t in SPENDING_TYPES} | {(INCOME, k[0]) for k in INCOME_KINDS}
    return declared <= held


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
    ("transactions", "type_id"),
    ("subscriptions", "type_id"),
    ("services", "type_id"),
    ("merchant_rules", "type_override_id"),
    ("suggestion_answers", "chosen_type_id"),
    ("let_go", "type_id"),
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
    """Whether words hold a control character (a line break, a tab, an escape)."""
    return any(unicodedata.category(c) == "Cc" for c in text)


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


def fix_wording(conn: sqlite3.Connection, type_id: int, data: dict) -> None:
    """Fix a spending type's wording: what it covers, what it is not for, or
    both. Anything else in the request is a code change and refused, as is a
    locked type and an income kind; nothing is written. No commit."""
    found = conn.execute(
        "SELECT t.kind, t.name, p.name AS parent, t.covers, t.not_for FROM types t"
        " LEFT JOIN types p ON p.id = t.parent_id WHERE t.id = ?",
        (type_id,),
    ).fetchone()
    if found is None:
        raise NoSuchType(type_id)
    kind, name, parent, covers, not_for = found
    if kind != SPENDING:
        raise TypeRefused("income kinds are set in code")
    if any(k not in WORDING_FIELDS for k in data):
        raise TypeRefused(CODE_CHANGE)
    shown = display_name(name, parent)
    if shown in LOCKED:
        raise TypeRefused(f"{shown} is set in code: fin reads it by name")
    if not data:
        raise TypeRefused("say what the type covers or what it is not for")
    if "covers" in data:
        covers = _covers(data["covers"])
    if "not_for" in data:
        not_for = _not_for(data["not_for"])
    conn.execute("UPDATE types SET covers = ?, not_for = ? WHERE id = ?", (covers, not_for, type_id))


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
        for table, column in USED_BY:
            # Both names come from USED_BY above, never from a request.
            if conn.execute(f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1", (type_id,)).fetchone():
                return f"a merchant or row still uses {shown}; change those first"
        if conn.execute("SELECT 1 FROM types WHERE parent_id = ? LIMIT 1", (type_id,)).fetchone():
            return f"a type still sits under {shown}; undo that one first"
    return None
