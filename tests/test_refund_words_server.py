"""Net in server words (fin-surfaces 31, case 12 for the server side).

The old refund words leave app.py's docstrings and comments and mcp_tools.py's descriptions,
and the three spending descriptions say what the figures are. Reads the source the way
tests/test_no_left_accent_borders.py does; no database is opened.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import mcp_tools

ROOT = Path(__file__).resolve().parent.parent
OLD_WORDS = ("not taken off", "never taken off", "not in the share")
NET_WORDS = "net of refunds; a refund counts in the month it arrives; S$ accounts only"


def _prose(path: Path) -> list[tuple[int, str]]:
    """Every comment and docstring in a module, with its line."""
    source = path.read_text(encoding="utf-8")
    found = [
        (tok.start[0], tok.string)
        for tok in tokenize.generate_tokens(io.StringIO(source).readline)
        if tok.type == tokenize.COMMENT
    ]
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                found.append((node.body[0].lineno, doc))
    return found


def _old_words(text: str) -> list[str]:
    low = " ".join(text.lower().split())
    return [w for w in OLD_WORDS if w in low]


def test_app_py_prose_has_none_of_the_old_words():
    found = [(line, w) for line, text in _prose(ROOT / "app.py") for w in _old_words(text)]
    assert found == [], f"old refund words in app.py: {found}"


def test_mcp_tools_py_has_none_of_the_old_words():
    found = [(t.name, w) for t in mcp_tools.TOOLS for w in _old_words(t.description)]
    found += [(line, w) for line, text in _prose(ROOT / "mcp_tools.py") for w in _old_words(text)]
    assert found == [], f"old refund words in mcp_tools.py: {found}"


def _description(name: str) -> str:
    [tool] = [t for t in mcp_tools.TOOLS if t.name == name]
    return " ".join(tool.description.split())


def test_the_three_spending_descriptions_say_net():
    for name in ("spending_cards", "spending_by_type", "spending_by_month"):
        assert NET_WORDS in _description(name), name


def test_spending_cards_says_what_out_and_back_are():
    text = _description("spending_cards")
    assert "_out" in text and "_back" in text
    assert "money out" in text and "refunds" in text


def test_the_stat_cards_docstring_says_net_and_keeps_out_back_as_the_breakdown():
    import app

    doc = " ".join(app.api_dashboard_stat_cards.__doc__.split())
    assert "net of refunds" in doc
    assert "breakdown" in doc and "_out" in doc and "_back" in doc
