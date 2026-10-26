"""An account's currency changed on its sheet (Lists › Accounts): the film's
company, Mahavir, moves to rupees. A figure typed in the old currency stops
it, in the server's words, and nothing changes.

The seam is the page itself, as in test_types_screen. Every name and figure
is invented.
"""

import pytest

from tests.test_types_screen import NODE, run, server, words  # noqa: F401 (server is a fixture)
from tests.test_types_screen import api  # noqa: F401 (a fixture)

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the screen is drawn only in JavaScript")

SAVE_AS = """
// Only the fields a company's sheet draws are there; the harness makes any it is asked for.
const q = document.querySelector;
document.querySelector = sel => (['#ac-hp', '#ac-dep', '#ac-shared', '#ac-interest-book'].includes(sel) ? null : q(sel));
const pick = async (id, cur) => {
    await ACT['account-edit']({ dataset: { id: String(id) } });
    // The harness keeps no field's drawn value: fill what the sheet holds.
    __byId('ac-name').value = 'Sample Mahavir';
    __byId('ac-last4').value = '';
    __byId('ac-cur').value = cur;
    await ACT['account-save']({ dataset: { id: String(id) } });
    await __settle();
    return { toasts: __toasts };
};
"""


def company(api) -> int:
    resp = api.post("/api/accounts", json={"name": "Sample Mahavir", "type": "company"})
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()["id"]


def currency(api, acct: int) -> str:
    return {a["id"]: a for a in api.get("/api/accounts").get_json()}[acct]["currency"]


def test_the_sheet_changes_an_accounts_currency(server, api):
    acct = company(api)

    out = run(server, SAVE_AS + f"return await pick({acct}, 'INR');")

    assert [words(t["words"]) for t in out["toasts"]] == ["Account saved"]
    assert currency(api, acct) == "INR"


def test_a_figure_typed_in_the_old_currency_stops_the_change_in_the_servers_words(server, api):
    acct = company(api)
    assert api.post("/api/anchors", json={"account_id": acct, "amount": "1000.00",
                                          "date": "2026-07-31"}).status_code == 200

    out = run(server, SAVE_AS + f"return await pick({acct}, 'INR');")

    [toast] = out["toasts"]
    assert toast["bad"] and "Delete its figures first" in words(toast["words"])
    assert currency(api, acct) == "SGD"


def test_a_rename_on_the_sheet_sends_no_currency(server, api):
    acct = company(api)

    out = run(server, SAVE_AS.replace("'Sample Mahavir'", "'Sample Mahavir Films'") + f"return await pick({acct}, 'SGD');")

    assert [words(t["words"]) for t in out["toasts"]] == ["Account saved"]
    entry = api.get("/api/history").get_json()["entries"][0]
    assert entry["summary"] == "Changed an account"
    assert currency(api, acct) == "SGD"


def test_a_loans_terms_are_typed_figures_too(api):
    """S$ 100,000.00 financed would be read as Rs 100,000.00: the terms are
    sent again in the new currency with it, or the change is refused."""
    resp = api.post("/api/accounts", json={"name": "Sample Car Loan", "type": "loan", "terms": {
        "financed": "100000.00", "months": 60, "flat_rate": "2.5", "first_instalment": "2026-02-01"}})
    assert resp.status_code == 200, resp.get_json()
    loan = resp.get_json()["id"]

    refused = api.put(f"/api/accounts/{loan}", json={"currency": "INR"})

    assert refused.status_code == 400
    assert "terms" in refused.get_json()["error"]
    assert currency(api, loan) == "SGD"
