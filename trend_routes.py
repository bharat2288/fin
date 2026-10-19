"""The trend pages' blueprint. app.py registers it with one line; each route
module below owns one lane's routes and registers none until that lane builds
it (fin-surfaces 22). No module here imports app.py."""

from flask import Blueprint

import account_balances_api
import trend_api
import trend_types_api

bp = Blueprint("trend", __name__)

for _module in (trend_api, trend_types_api, account_balances_api):
    _module.register(bp)
