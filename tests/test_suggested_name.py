"""Queue-naming 01: a clean suggested name for a merchant (spec A, cases 1-8, and 13).

fin's own tidy-up of a statement wording, the name a teach saves unless the
operator gives another. The seam is the page: the real `static/app.js` runs under
node in the vm of tests/test_types_screen.py, against the real Flask app on a
throwaway port over a temporary book, so the merchants it matches are the ones the
server lists and the This was… sheet is the one the operator opens. Every wording,
merchant and figure here is invented.
"""

import json
import subprocess

import pytest

from test_book_and_type_switch import merchant, statement  # noqa: F401 (statement is a fixture)
from test_preview_merchant_change import add_row
from test_types_screen import APP_JS_PATH, HARNESS, NODE, server  # noqa: F401 (server is a fixture)

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the name is made only in JavaScript")

# The same vm, with clearTimeout (the rule line's live count debounces with it) and
# a page observer that never fires (a count back before its line waits on it).
TIMERS = HARNESS.replace("setTimeout: (fn, ms)", "clearTimeout, MutationObserver: class { observe() {} disconnect() {} },"
                                                  " setTimeout: (fn, ms)")
assert TIMERS != HARNESS


def run(base, body):
    done = subprocess.run([NODE, "-e", TIMERS, str(APP_JS_PATH), base, body],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


# Statement wording -> the name fin suggests (spec A, cases 1-7).
CASES = [
    ("SP RHODE SKIN RHODESKIN.COM CA", "Rhode Skin"),       # 1: processor prefix, web address, country code
    ("EZYPAYSGD*VAURA PILATE", "Vaura Pilate"),             # 2: processor prefix up to "*"
    ("MOTHERCARE - PARAGON", "Mothercare"),                 # 3: the branch after " - "
    ("INDIGOAIR SGD", "Indigoair"),                         # 4: trailing currency code
    ("Etsy.com*Multiple Shop Du IRL 25OCT 4628-0000", "Etsy"),  # 5: a web address before "*"
    ("BOTTEGA VENETA", "Bottega Veneta"),                   # 6: nothing to drop
    ("PAYNOW TO K TAN", ""),                                # 7: no name to read: a PayNow
    ("FAST PAYMENT TO SAMPLE PTE LTD", ""),                 # 7: a transfer
    ("PAYMENT TO 91234567", ""),                            # 7: bank advice and fee lines (suggest.py's)
    ("BILL PAYMENT REF 44210", ""),
    ("SERVICE FEE 2026", ""),
]


def test_cases_1_to_7_each_wording_gives_the_spec_name(server):
    got = run(server, f"return {json.dumps([w for w, _ in CASES])}.map(suggestName);")
    assert dict(zip([w for w, _ in CASES], got)) == dict(CASES)


def test_the_name_keeps_ampersands_and_apostrophes_and_stops_at_a_reference(server):
    got = run(server, "return ['MARKS & SPENCER 0042 SINGAPORE', \"MCDONALD'S 1234 BUGIS\","
                      " 'PAYPAL *NETFLIX 4029357733', 'GRAB*A-R12345678', 'NETFLIX.COM SINGAPORE'].map(suggestName);")
    assert got == ["Marks & Spencer", "Mcdonald's", "Netflix", "Grab", "Netflix"]


# The star cuts a name short only after a processor (case 2) or a web address
# (case 5). Any other "WORD*REST" keeps both sides, so the name does not shrink
# to a word a held merchant may also carry ("GOOGLE*ADS" is not "Google"); SP
# Group is a merchant, not SP's processor prefix (review F2).
STAR = [
    ("FP*FOOD PANDA", "Fp Food Panda"),
    ("GOOGLE*ADS", "Google Ads"),
    ("GOOGLE*GOOGLE ONE", "Google Google One"),
    ("UBER *TRIP HELP.UBER.COM", "Uber Trip"),
    ("SP GROUP SERVICES", "Sp Group Services"),
    ("GRAB*A-R12345678", "Grab"),
]


def test_the_star_cuts_only_after_a_processor_or_a_web_address(server):
    got = run(server, f"return {json.dumps([w for w, _ in STAR])}.map(suggestName);")
    assert dict(zip([w for w, _ in STAR], got)) == dict(STAR)


@pytest.fixture
def waiting(conn, statement):  # noqa: F811
    """A Rhode Skin row with no merchant or type, waiting in the Queue."""
    return add_row(conn, statement, "SP RHODE SKIN RHODESKIN.COM CA", 53234, service_id=None, book=None,
                   type_name=None, day="2026-10-02")


# What the sure card names (sureName) and what This was…'s Merchant field holds
# once Spending is chosen, for the waiting row.
BOTH = r"""
const q = await loadQueue();
const row = q.lanes.out.find(i => i.row.id === %d).row;
const card = sureName(row, await servicesList());
await openWhatWas(row, [row]);
await __settle();
await ACT['ww-pick']({ dataset: { choice: 'spending' } });
await __settle();
const field = /id="rs-merchant"[^>]*value="([^"]*)"/.exec(__byId('ww-ask').innerHTML);
return [card, field && field[1], __byId('ww-scope-words').textContent];
"""


def test_case_8_a_suggestion_matching_a_merchant_ignoring_case_names_that_merchant(server, conn, waiting):
    merchant(conn, "RHODE skin", "Household", "Health & Beauty")
    card, _, _ = run(server, BOTH % waiting)
    assert card == "RHODE skin"


def test_case_13_this_was_defaults_to_the_same_suggested_name(server, conn, waiting):
    card, field, _ = run(server, BOTH % waiting)
    assert (card, field) == ("Rhode Skin", "Rhode Skin")


def test_case_13_this_was_names_the_merchant_fin_holds_as_the_card_does(server, conn, waiting):
    merchant(conn, "RHODE skin", "Household", "Health & Beauty")
    card, field, _ = run(server, BOTH % waiting)
    assert (card, field) == ("RHODE skin", "RHODE skin")


def test_case_7_this_was_starts_with_no_name_for_a_paynow(server, conn, statement):  # noqa: F811
    tx = add_row(conn, statement, "PAYNOW TO K TAN", 5000, service_id=None, book=None, type_name=None,
                 day="2026-10-03")
    _, field, scope = run(server, BOTH % tx)
    assert field == ""
    assert scope == "Label every row from this merchant the same way"
