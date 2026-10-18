"""The rule a save suggests from a statement line stops at a reference number.

suggestPattern lives in static/app.js and nowhere else, so the test lifts it
(with titleCase, which turns it into the merchant name) out of the real file
and runs it under node on each specimen.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP_JS = Path(__file__).resolve().parent.parent / "static" / "app.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH: suggestPattern runs only in JavaScript")

# Statement line -> the pattern fin suggests; None: unchanged from the line.
SPECIMENS = [
    ("INDIGOAIR 312-4567890 MUMBAI", "INDIGOAIR"),
    ("NTUC FAIRPRICE 0123 SINGAPORE", "NTUC FAIRPRICE"),
    ("SQ *KOPI CORNER", None),
    ("GRAB*A-R12345678", None),
    ("7-ELEVEN BUGIS", None),
    ("SP 1234 TAMPINES", "SP 1234"),
    ("PAYNOW TO K TAN", ""),
]


def _function(src, name):
    # A one-line function ends on its own line; a longer one at the first "}" in column 0.
    m = re.search(rf"^function {name}\(.*\}}$", src, re.M) or re.search(rf"^function {name}\(.*?^\}}$", src, re.M | re.S)
    assert m, f"{name} not found in static/app.js"
    return m.group(0)


def _run(lines):
    src = APP_JS.read_text(encoding="utf-8")
    code = "\n".join([_function(src, "suggestPattern"), _function(src, "titleCase"),
        "const lines = JSON.parse(process.argv[1]);",
        "console.log(JSON.stringify(lines.map(l => { const p = suggestPattern(l); return [p, titleCase(p)]; })));"])
    out = subprocess.run([NODE, "-e", code, json.dumps(lines)], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def test_specimens():
    got = _run([line for line, _ in SPECIMENS])
    for (line, want), (pattern, _) in zip(SPECIMENS, got):
        assert pattern == (line if want is None else want), line


def test_the_name_stops_too():
    [(pattern, name)] = _run(["INDIGOAIR 312-4567890 MUMBAI"])
    assert (pattern, name) == ("INDIGOAIR", "Indigoair")


def test_other_references_and_long_codes():
    got = [p for p, _ in _run(["STARBUCKS #4471 ORCHARD", "KOPI A-R123 BUGIS", "NTUC FAIRPRICE SINGAPORE",
                               "SHOPEE 12345 SG", "GRAB 12/09"])]
    # "#4471" and "A-R123" are references; a long word with no digit still
    # stops as before, keeping two words; a short first word keeps two.
    assert got == ["STARBUCKS", "KOPI", "NTUC FAIRPRICE", "SHOPEE", "GRAB"]
