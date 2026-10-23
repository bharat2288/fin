"""Speed pass 03: marking Changes looked keeps the read cache (spec cases 10-11).

"Looked" moves the history mark and nothing else, so the lists already read stay read: the
next screen draws from what the browser holds, and only the Claude count is asked for again.
A write to the book still empties the cache. The whole real app.js runs under node over the
held stand-in fetch of test_one_round_reads, answered one round at a time. Every name and
figure is invented.
"""

import json

from test_one_round_reads import HOME, REFS, node

# Home with two changes by Claude not yet looked at; once "looked" is in, none.
UNSEEN = {"last_looked": 3, "last_looked_at": None, "claude_count": 2, "latest_claude_at": None, "newest": 5}
SEEN = {**UNSEEN, "last_looked": 5, "claude_count": 0}
BOOK = [["/api/changes/looked", SEEN], ["/api/changes/marks", {"rows": {}, "accounts": {}}],
        ["/api/history", {"entries": [], "many_rows": 50, **UNSEEN}], ["/api/chat-writes", {"enabled": True}],
        ["/api/rates", []], ["/api/settings", UNSEEN], *HOME]
# What a screen read before and should not ask for again: the shared lists and the merchants.
LISTS = [needle for needle, _ in REFS] + ["/api/services"]

# Drives the app as the operator does: open a screen by its hash, every read answered in
# rounds until none is held. The server's mark moves when "looked" is answered, so a read of
# the count answered after it says none. The toast has a box to land in.
DRIVE = """
const SETTINGS = ANSWERS.find(([n]) => n === '/api/settings')[1];
const heldFetch = fetch;
globalThis.fetch = (url, opts) => {
    const p = heldFetch(url, opts);
    return url.includes('/api/changes/looked') ? p.then(res => { Object.assign(SETTINGS, %s); return res; }) : p;
};
const realQuery = document.querySelector;
document.querySelector = s => s === '#toasts' ? el() : realQuery(s);
document.createElement = () => ({ ...el(), remove() {} });
async function settle(p) { await flush(); while (held.length) await release(); await p; await flush(); }
async function open(hash) { location.hash = hash; await settle(render()); }
const since = () => asked.splice(0);
const drawn = () => document.querySelector('#view').innerHTML;
""" % json.dumps(SEEN)


def _run(body):
    return node(DRIVE + body + "\nprocess.exit(0);", BOOK)


def _relisted(urls):
    return [u for u in urls if any(u.startswith(n) for n in LISTS)]


def test_case_10_opening_changes_marks_looked_and_the_next_screen_draws_from_cache():
    got = _run("""
        await open('#/books/lists/types');
        await open('#/');
        const before = drawn();
        since();
        await open('#/changes');
        const changes = since();
        await open('#/books/lists/types');
        const types = since();
        await open('#/');
        out({ before, changes, types, home: since(), html: drawn() });
    """)
    assert "2 changes by Claude</b>" in got["before"]
    looked = got["changes"].index("POST /api/changes/looked")
    # The Claude count is asked for again once "looked" is sent ...
    assert "/api/settings" in got["changes"][looked:], got["changes"]
    # ... and nothing else read before is.
    assert _relisted(got["changes"][looked:]) == [], got["changes"]
    assert _relisted(got["types"]) == [], got["types"]
    assert _relisted(got["home"]) == [], got["home"]
    assert "by Claude</b>" not in got["html"]
    assert "Nothing new from Claude" in got["html"]


def test_case_10_looks_right_on_home_clears_the_count_and_keeps_the_lists():
    got = _run("""
        await open('#/');
        const before = drawn();
        since();
        await settle(ACT['looks-right']({ dataset: { upto: '5' } }));
        out({ before, again: since(), html: drawn() });
    """)
    assert "2 changes by Claude</b>" in got["before"]
    assert "POST /api/changes/looked" in got["again"], got["again"]
    assert _relisted(got["again"]) == [], got["again"]
    assert "by Claude</b>" not in got["html"]
    assert "Nothing new from Claude" in got["html"]


def test_case_11_a_book_write_still_empties_the_cache():
    got = _run("""
        await open('#/books/lists/types');
        since();
        await settle(send('PUT', '/api/services/9', { name: 'Amazon', book: 'Moom', type_id: 3 }));
        await open('#/books/lists/types');
        out({ types: since() });
    """)
    relisted = _relisted(got["types"])
    for needle, _ in REFS:
        assert any(u.startswith(needle) for u in relisted), (needle, got["types"])
