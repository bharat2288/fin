"""Ticket 35, case 48: Back returns to the same place (scroll and open sections).

The route-memory code in static/app.js's hashchange handler is lifted out of the real file and
run under node with a fake window, history, document and render, as test_client_seams.py does.
The Chromium walk of case 48 (By type open, 1,240 px, Dining, Back) is seam 2 and stays a walk.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = (Path(__file__).resolve().parent.parent / "static" / "app.js").read_text(encoding="utf-8")
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not on PATH")


def _block():
    m = re.search(r"^// ROUTE MEMORY begin$.*?^// ROUTE MEMORY end$", APP_JS, re.M | re.S)
    assert m, "route memory block not found in static/app.js"
    return m.group(0)


def _run(script):
    """A fake browser: one scrollable page with details sections, a history stack, a router."""
    code = r"""
const listeners = {};
let scrollY = 0, scrolls = [];
const sections = [{open:false},{open:false},{open:false}];
globalThis.window = { addEventListener: (n, f) => { listeners[n] = f; }, get scrollY() { return scrollY; },
  scrollTo: (x, y) => { scrollY = y; scrolls.push(y); } };
globalThis.document = { querySelectorAll: () => sections, addEventListener() {} };
globalThis.location = { hash: '#/books/spending' };
let histState = null; globalThis.history = { get state() { return histState; }, replaceState: s => { histState = s; } };
globalThis.closeSheet = () => {};
let renders = 0; globalThis.render = async () => { renders++; sections.forEach(s => { s.open = false; }); };
""" + _block() + "\n" + script
    out = subprocess.run([NODE, "-e", code], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


@needs_node
def test_back_restores_scroll_and_open_sections():
    got = _run(r"""
(async () => {
  histState = {finBack: true};                 // the Spending entry, tagged when it was reached
  sections[0].open = true; sections[2].open = true; scrollY = 1240;
  // forward: the operator opens Dining (a fresh entry, no state)
  location.hash = '#/books/merchant/9'; histState = null;
  await listeners.hashchange({ oldURL: 'http://x/#/books/spending', newURL: 'http://x/#/books/merchant/9' });
  const onDining = scrollY;
  // Back: the browser lands on the Spending entry, which carries its state
  location.hash = '#/books/spending'; histState = {finBack: true};
  await listeners.hashchange({ oldURL: 'http://x/#/books/merchant/9', newURL: 'http://x/#/books/spending' });
  console.log(JSON.stringify([onDining, scrollY, sections.map(s => s.open)]));
})();
""")
    assert got == [0, 1240, [True, False, True]]


@needs_node
def test_a_fresh_visit_to_a_place_seen_before_still_starts_at_the_top():
    got = _run(r"""
(async () => {
  scrollY = 900;
  location.hash = '#/books/merchant/9'; histState = null;
  await listeners.hashchange({ oldURL: 'http://x/#/books/spending', newURL: 'http://x/#/books/merchant/9' });
  scrollY = 300;
  location.hash = '#/books/spending'; histState = null;      // a link click, not Back
  await listeners.hashchange({ oldURL: 'http://x/#/books/merchant/9', newURL: 'http://x/#/books/spending' });
  console.log(JSON.stringify([scrollY, histState]));
})();
""")
    assert got == [0, {"finBack": True}]


@needs_node
def test_back_to_a_place_never_left_goes_to_the_top():
    got = _run(r"""
(async () => {
  scrollY = 500; location.hash = '#/'; histState = {finBack: true};
  await listeners.hashchange({ oldURL: 'http://x/#/books/spending', newURL: 'http://x/#/' });
  console.log(JSON.stringify(scrollY));
})();
""")
    assert got == 0
