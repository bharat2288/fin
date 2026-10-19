"""The expect-back blueprint (personal-ledger 23). app.py registers it with
one line and merges its SUMMARIES into the change history's words with
another; each route module below owns one lane's routes and registers none
until that lane builds it. No module here imports app.py.

The rules every route shares (the own part, eligibility, the guarded writes,
the machine) are expect_back.py's.
"""

from flask import Blueprint

import expectation_api
import expectation_page_api
import letgo_api
import payback_queue_api
import refund_link_api
import setoff_api
import waiting_api

bp = Blueprint("expect", __name__)

MODULES = (
    expectation_api,
    expectation_page_api,
    setoff_api,
    waiting_api,
    letgo_api,
    refund_link_api,
    payback_queue_api,
)

# What each writing route did, in words, keyed as request.endpoint reads it.
SUMMARIES: dict[str, str] = {}

for _module in MODULES:
    _module.register(bp)
    SUMMARIES.update({f"{bp.name}.{name}": words for name, words in _module.SUMMARIES.items()})
