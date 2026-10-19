"""Case 12 (fin-surfaces 15, ticket 48): the old refund words leave, in one check.

"not taken off", "never taken off" and "not in the share" said refunds sat
beside spending. Spending is net of refunds everywhere now, so the words
appear nowhere a reader meets them:

- the page: static/app.js, and every other script and the shell the page
  loads (the trend pages draw their own words), read whole;
- app.py: its docstrings and comments, and its strings, which are what
  reaches the screen from the server;
- mcp_tools.py: every tool's description and the descriptions in its
  input schemas, as Claude reads them.

Reads source and the tool list the way tests/test_no_left_accent_borders.py
does; no database is opened.
"""

from __future__ import annotations

import ast
import io
import tokenize
from pathlib import Path

import mcp_tools

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
OLD_WORDS = ("not taken off", "never taken off", "not in the share")
VENDORED = {"chart.min.js"}  # Chart.js, not fin's words


def old_words(text: str) -> list[str]:
    """The old phrases in a text, case and line breaks aside."""
    flat = " ".join(text.lower().split())
    return [w for w in OLD_WORDS if w in flat]


def python_words(source: str) -> list[tuple[int, str]]:
    """(line, text) for every comment, docstring and string in a module."""
    found = [(tok.start[0], tok.string)
             for tok in tokenize.generate_tokens(io.StringIO(source).readline)
             if tok.type in (tokenize.COMMENT, tokenize.STRING)]
    # Docstrings again through ast, whole: a string split over lines reads as one.
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                found.append((node.body[0].lineno, doc))
    return found


def schema_descriptions(schema) -> list[str]:
    """Every "description" inside a JSON schema, however deep."""
    if isinstance(schema, dict):
        own = [schema["description"]] if isinstance(schema.get("description"), str) else []
        return own + [d for value in schema.values() for d in schema_descriptions(value)]
    if isinstance(schema, list):
        return [d for value in schema for d in schema_descriptions(value)]
    return []


def test_the_check_finds_each_phrase_where_it_looks():
    """The scanners are not blind: each phrase is caught in each kind of place."""
    for phrase in OLD_WORDS:
        spaced = phrase.replace(" ", "\n    ").upper()
        assert old_words(f"refunds,\n {spaced}.") == [phrase]
        module = f'"""Doc: {phrase}."""\n# {phrase}\ndef f():\n    """{spaced}"""\n    return "{phrase}"\n'
        # module docstring, comment, a docstring broken over lines, a string on screen
        on_screen = module.count("\n")  # the return, on the last line
        assert {line for line, t in python_words(module) if old_words(t) == [phrase]} == {1, 2, 4, on_screen}
        assert schema_descriptions({"properties": {"x": {"description": phrase}}}) == [phrase]


def test_the_page_says_none_of_the_old_words():
    pages = sorted(p for p in STATIC.iterdir()
                   if p.suffix in (".js", ".html") and p.name not in VENDORED)
    assert STATIC / "app.js" in pages
    found = [(p.name, text.count("\n", 0, text.lower().find(w)) + 1, w)
             for p in pages for text in [p.read_text(encoding="utf-8")] for w in old_words(text)]
    assert found == [], f"old refund words on the page: {found}"


def test_app_py_says_none_of_the_old_words_in_its_docstrings_comments_or_strings():
    source = (ROOT / "app.py").read_text(encoding="utf-8")
    found = [(line, w) for line, text in python_words(source) for w in old_words(text)]
    assert found == [], f"old refund words in app.py: {found}"


def test_mcp_tools_descriptions_say_none_of_the_old_words():
    assert mcp_tools.TOOLS
    found = [(t.name, w) for t in mcp_tools.TOOLS
             for text in [t.description, *schema_descriptions(t.input_schema)] for w in old_words(text)]
    source = (ROOT / "mcp_tools.py").read_text(encoding="utf-8")
    found += [(line, w) for line, text in python_words(source) for w in old_words(text)]
    assert found == [], f"old refund words in mcp_tools.py: {found}"
