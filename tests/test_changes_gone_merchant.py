"""Queue-naming 05: Changes names a merchant that is gone (spec E, case 20).

A teach that made its merchant, then undone: the merchant is no longer in fin,
but both change entries still hold its row (the save's after, the undo's
before). An opened change's row line reads the merchant's name from there,
marked as since removed, instead of "a merchant no longer listed". Where no
row of the change holds the name, the old words stay.

The teach and the Undo are crossed with the Flask test client; the opened
changes are drawn by the real `static/app.js` under node against the real
Flask app on a throwaway port over the temporary book
(tests/test_types_screen.py's harness). Every wording and name is invented.
"""

import json
import re

from test_book_and_type_switch import statement, type_id  # noqa: F401 (statement is a fixture)
from test_preview_merchant_change import add_row, entries
from test_types_screen import run, server, words  # noqa: F401 (server is a fixture)


def merchant_line(html):
    """The row line's merchant field: what it was, what it is."""
    m = re.search(r'<td class="c-field">merchant</td><td class="c-was">(.*?)</td>\s*<td class="c-now">(.*?)</td>', html, re.S)
    assert m, html
    return words(m.group(1)), words(m.group(2))


def opened(server, entry_id):
    return run(server, f"return await changeRowsHTML(await get('/api/history/{entry_id}', {{ fresh: true }}));")


def test_case_20_an_undone_merchant_is_named_in_both_entries_row_lines(server, client, conn, statement):  # noqa: F811
    tx = add_row(conn, statement, "HERMES.COM SAMPLE ORDER", 42000, service_id=None, book=None, type_name=None)
    resp = client.post("/api/transactions/resolve", json={
        "tx_id": tx, "service_name": "Hermes.Com", "type_id": type_id(conn, "Shopping"), "book": "Household",
        "apply_scope": "service_default", "pattern": "HERMES.COM", "match_type": "contains"})
    assert resp.status_code == 200, resp.get_json()
    [save] = entries(client)
    assert client.post(f"/api/history/{save['id']}/undo").status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM services WHERE name = 'Hermes.Com'").fetchone()[0] == 0

    undo = next(e for e in entries(client) if e.get("undoes") == save["id"])
    assert merchant_line(opened(server, save["id"])) == ("no merchant", "Hermes.Com, since removed")
    assert merchant_line(opened(server, undo["id"])) == ("Hermes.Com, since removed", "no merchant")


def test_a_gone_merchant_no_row_of_the_change_names_keeps_the_old_words(server):
    entry = {"id": 1, "changes": [{"table": "transactions", "op": "update", "row_id": 1,
                                   "before": {"description": "SAMPLE ROW", "date": "2026-09-10", "service_id": None},
                                   "after": {"description": "SAMPLE ROW", "date": "2026-09-10", "service_id": 999}}]}
    html = run(server, f"return await changeRowsHTML({json.dumps(entry)});")
    assert merchant_line(html) == ("no merchant", "a merchant no longer listed")
