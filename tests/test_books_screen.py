"""Books the operator creates, the screen side (fin RUL-021; fin-redesign 22, spec
part 2 cases 9 and 13): Lists › Books lists the books and adds one, and an added
book is drawn wherever books are, Home's tiles included.

The seam is the page itself, as in test_types_screen: the real static/app.js under
node against the real Flask app on a throwaway port over a temporary book. Every
name and wording here is invented; "Film" is the spec's own example.
"""

import json
import re

from tests.test_types_screen import NODE, entries, run, server, words  # noqa: F401 (server is a fixture)
from tests.test_types_screen import api  # noqa: F401 (a fixture)

import pytest

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the screen is drawn only in JavaScript")

FILL = """
const fill = values => Object.entries(values).forEach(([id, v]) => { __byId(id).value = v; });
const press = async act => { await ACT[act]({ dataset: {} }); await __settle(); };
"""


def test_the_books_card_lists_every_book_and_offers_add(server):
    html = run(server, "return await viewLists('books');")
    said = words(html)
    assert re.search(r'<a href="#/books/lists/books" aria-current="page">.*?Books</a>', html, re.S)
    assert "Renaming or removing a book is a code change." in said
    names = re.findall(r'<tr><td class="sl-name"><b>([^<]*)</b>', html)
    assert names == ["Household", "Moom", "Kalesh"]
    assert re.search(r'<button class="btn"[^>]*data-act="book-add"[^>]*>.*?Add a book</button>', html, re.S)


def test_adding_a_book_on_the_sheet_lists_it_and_toasts_with_undo(server, api):
    out = run(server, FILL + """
        ACT['book-add']();
        fill({ 'bk-name': 'Film', 'bk-desc': 'costs of the film, whichever account paid them' });
        await press('book-add-save');
        return { toasts: __toasts, html: await viewLists('books') };
    """)
    assert [(words(t["words"]), t["bad"], t["undo"]) for t in out["toasts"]] == [("Added the book Film", False, True)]
    assert re.findall(r'<tr><td class="sl-name"><b>([^<]*)</b>', out["html"])[-1] == "Film"
    assert "added in fin" in words(out["html"])
    [entry] = entries(api)
    assert entry["summary"] == "Added the book Film"


def test_a_refused_book_says_the_servers_words_and_adds_nothing(server, api):
    out = run(server, FILL + """
        ACT['book-add']();
        fill({ 'bk-name': 'moom', 'bk-desc': 'again' });
        await press('book-add-save');
        return { toasts: __toasts };
    """)
    assert [(words(t["words"]), t["bad"], t["undo"]) for t in out["toasts"]] == [("Moom is already a book", True, False)]
    assert entries(api) == []


def test_home_draws_an_added_book_as_a_company_tile(server, api):
    assert api.post("/api/books", json={"name": "Film", "description": "the film"}).status_code == 201
    out = run(server, """
        const [cards, r] = await Promise.all([get('/api/dashboard/stat-cards?ref_month=2026-09&history=3'), refs()]);
        return homeBookTiles(cards, r.books);
    """)
    tiles = json.dumps(out)
    assert "Film, its costs" in tiles
    assert "moves Film" in words(tiles.replace("\\u2019", "'"))
