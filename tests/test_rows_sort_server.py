"""Walk-fixes 05, the server half: the rows read sorts by a column (spec cases 11 and 12).

An account page's Money in and Money out, and the Type column wherever the rows are paged
(the merchant page, an account page), are sorted by the server. Text sorts A to Z ignoring
case, an empty cell comes last whichever way the column runs, and rows that tie on the column
fall in one fixed order, so a page boundary never repeats or skips a row. Driven through the
Flask test client against the temporary book; every name and figure is invented.
"""

from urllib.parse import urlencode

from test_book_and_type_switch import row, statement  # noqa: F401 (statement is a fixture)


def listed(client, **args) -> list[dict]:
    query = urlencode({"per_page": 500, **args})
    return client.get(f"/api/transactions?{query}").get_json()["transactions"]


def amounts(client, sort: str, sort_dir: str) -> list[int]:
    return [t["amount_minor"] for t in listed(client, sort=sort, sort_dir=sort_dir)]


def some_in_and_out(conn, statement) -> None:
    for i, amount in enumerate([30.0, -5.0, 10.0, -50.0, 20.0]):
        row(conn, statement, f"SAMPLE SHOP {i}", amount)


def test_money_out_both_ways_leaves_the_money_in_rows_last(client, conn, statement):
    some_in_and_out(conn, statement)

    assert amounts(client, "amount_out", "asc")[:3] == [1000, 2000, 3000]
    assert amounts(client, "amount_out", "desc")[:3] == [3000, 2000, 1000]
    assert all(a < 0 for a in amounts(client, "amount_out", "asc")[3:] + amounts(client, "amount_out", "desc")[3:])


def test_money_in_both_ways_leaves_the_money_out_rows_last(client, conn, statement):
    some_in_and_out(conn, statement)

    assert amounts(client, "amount_in", "desc")[:2] == [-5000, -500]   # largest in first
    assert amounts(client, "amount_in", "asc")[:2] == [-500, -5000]
    assert all(a > 0 for a in amounts(client, "amount_in", "asc")[2:] + amounts(client, "amount_in", "desc")[2:])


def test_a_row_with_no_type_comes_last_both_ways(client, conn, statement):
    row(conn, statement, "SAMPLE NOTHING", 12.0)
    row(conn, statement, "SAMPLE AIRWAYS", 400.0, type_name="Travel")
    row(conn, statement, "SAMPLE CAFE", 8.0, type_name="Dining")

    up = [t["type"] for t in listed(client, sort="type", sort_dir="asc")]
    down = [t["type"] for t in listed(client, sort="type", sort_dir="desc")]

    assert up == ["Dining", "Travel", None]
    assert down == ["Travel", "Dining", None]


def test_text_sorts_a_to_z_ignoring_case(client, conn, statement):
    for words in ["cherry tart", "BANANA BREAD", "apple pie"]:
        row(conn, statement, words, 5.0)

    got = [t["description"] for t in listed(client, sort="description", sort_dir="asc")]

    assert got == ["apple pie", "BANANA BREAD", "cherry tart"]


def test_rows_that_tie_page_in_one_fixed_order(client, conn, statement):
    ids = [row(conn, statement, f"SAMPLE CAFE {i}", 8.0, type_name="Dining") for i in range(5)]

    pages = [t["id"] for page in (1, 2, 3)
             for t in listed(client, sort="type", sort_dir="asc", per_page=2, page=page)]

    assert pages == sorted(ids, reverse=True)
