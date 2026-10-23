"""The server's suggested name and wording are the card's (chat-queue-read 01, case 7).

suggestName and suggestPattern live in static/app.js, where the Queue draws
them; name_wording.py holds their Python twins for the cards chat reads. This
test lifts the real functions (and the rules they read) out of the file, runs
them under node over one table of statement wordings, and runs the twins over
the same table: any difference fails, so the two cannot drift apart silently.
Every wording here is invented.
"""

import json
import subprocess

import pytest

import name_wording
from test_suggest_pattern import APP_JS, NODE, OTHER_MERCHANTS, SPECIMENS, _function
from test_suggested_name import CASES, STAR

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH: the JavaScript cannot be run")


def _rules(src: str) -> str:
    start = src.index("const NAME_RULES = {")
    return src[start:src.index("\n};\n", start) + 3]


def in_node(wordings: list) -> list:
    src = APP_JS.read_text(encoding="utf-8")
    code = "\n".join([_function(src, "suggestPattern"), _rules(src), _function(src, "suggestName"),
                      "const lines = JSON.parse(process.argv[1]);",
                      "console.log(JSON.stringify(lines.map(l => [suggestName(l), suggestPattern(l)])));"])
    out = subprocess.run([NODE, "-e", code, json.dumps(wordings)], capture_output=True, text=True,
                         encoding="utf-8", timeout=30, check=True)
    return json.loads(out.stdout)


# The queue-naming spec's cases 1-7 and the star cases, the suggestPattern
# tests' wordings, and the edges: an empty or spaced line, lower case, a
# trailing date, a line break, a no-break space and letters past ASCII.
TABLE = list(dict.fromkeys(
    [w for w, _ in CASES] + [w for w, _ in STAR] + [w for w, _ in SPECIMENS] + OTHER_MERCHANTS + [
        "MARKS & SPENCER 0042 SINGAPORE", "MCDONALD'S 1234 BUGIS", "PAYPAL *NETFLIX 4029357733",
        "NETFLIX.COM SINGAPORE", "STARBUCKS #4471 ORCHARD", "KOPI A-R123 BUGIS", "NTUC FAIRPRICE SINGAPORE",
        "SHOPEE 12345 SG", "GRAB 12/09", "BUS/MRT 278316423", "CAFE 12345 TAMPINES", "TAXI A-1234",
        "GRAB*A-R34567890", "GRAB*A-R23456789", "GRAB* SG", "SQ*A1",
        "INDIGOAIR 312-4567890 MUMBAI", "RHODE SKIN 8812 SINGAPORE", "INDIGOAIR SGD",
        "", "   ", "lantern noodle house 0042 sg", "SAMPLE SHOP 12/09", "SAMPLE SHOP 12/09\n", "SAMPLE\nSHOP 0042",
        "SAMPLE\u00a0SHOP 0042", "CAF\u00c9 \u00c9TOILE 0042 PARIS FR", "WWW.SAMPLE-SHOP.CO.UK", "2C2P*HARBOUR KITE SHOP 99812",
        "SP GROUP SERVICES", "GIRO SAMPLE UTILITIES", "ATM WITHDRAWAL 0042", "GST 2026", "HELP.UBER.COM",
        "OMISE * SAMPLE STORE - TAMPINES 1", "7-ELEVEN BUGIS", "SP 1234 TAMPINES", "A",
    ]))


def test_case_7_the_twins_give_what_the_card_gives_for_every_wording():
    got = [[name_wording.suggest_name(w), name_wording.suggest_pattern(w)] for w in TABLE]

    want = in_node(TABLE)

    assert dict(zip(TABLE, map(tuple, got))) == dict(zip(TABLE, map(tuple, want)))


def test_the_twins_read_no_wording_as_empty():
    assert (name_wording.suggest_name(None), name_wording.suggest_pattern(None)) == ("", "")
