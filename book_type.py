"""Book and type: the two labels a spending row carries.

Book says whose spending a row is; type says what kind of thing the money
was spent on. One type list serves every book. Both vocabularies are declared
here, once, with a plain description for each member; the `types` table is
filled from this declaration and nothing else.
"""

from __future__ import annotations

import sqlite3

# (name, description). A new book is a new value here.
BOOKS = (
    ("Household", "spending of the household itself"),
    ("Moom", "costs of the company Moom, whichever account paid them"),
    ("Kalesh", "costs of the company Kalesh, whichever account paid them"),
)

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

    Safe to run again: a name already present is left as it is. One statement
    at a time and no commit, so it can run inside a caller's transaction.
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
