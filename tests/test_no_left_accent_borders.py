"""Ruling 2026-10-06 (luno): no left accent border on a card, or on anything else fin draws.

A notice, a banner and a toast say their tone with a whole border in the tone's colour and an
icon; a change by Claude says so with its teal tag; a blocking item has a whole ink border.
This reads the stylesheet and the page's script, so a rule that brings a side stripe back fails
here: a border on the left or inline-start side, an inset box-shadow drawn as a stripe down one
edge, or a left border written inline from app.js.
"""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"


def _declared(text: str, pattern: str) -> list[tuple[int, str]]:
    return [(text.count("\n", 0, m.start()) + 1, m.group(0)) for m in re.finditer(pattern, text)]


def test_the_stylesheet_declares_no_left_border():
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    found = _declared(css, r"border-(?:left|inline-start)(?:-color|-width|-style)?\s*:[^;]*;")
    assert found == [], f"left-border declarations in static/styles.css: {found}"


def test_the_stylesheet_draws_no_inset_stripe():
    # `box-shadow: inset 3px 0 0 …` is a left stripe by another name.
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    found = _declared(css, r"inset\s+-?\d+(?:\.\d+)?px\s+0(?:px)?\s+0(?:px)?\b[^;]*;")
    assert found == [], f"inset stripes in static/styles.css: {found}"


def test_the_page_writes_no_left_border_inline():
    for name in ("app.js", "index.html"):
        text = (STATIC / name).read_text(encoding="utf-8")
        found = _declared(text, r"border-?(?:left|inline-start)|borderLeft|borderInlineStart")
        assert found == [], f"left borders written in static/{name}: {found}"
