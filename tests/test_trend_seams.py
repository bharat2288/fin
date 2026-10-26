"""The server seams the trend pages build on (fin-surfaces 22): scope.py holds
the scope SQL, the trend blueprint carries only the trend routes, and
/api/transactions can list hidden merchants on request. Every figure here is
invented."""

import ast
import importlib
from pathlib import Path

import app as fin_app
import book_type
import db
import scope

ROOT = Path(__file__).resolve().parent.parent
LANE_MODULES = ("trend_api", "trend_types_api", "account_balances_api")


def test_app_reads_the_scope_names_from_scope():
    assert fin_app.book_expr is scope.book_expr
    assert fin_app._HOUSEHOLD_ACCOUNT is scope._HOUSEHOLD_ACCOUNT
    assert fin_app._TYPE_JOINS is scope._TYPE_JOINS
    assert fin_app.get_db is db.get_db


def test_book_expr_defaults_an_unplaced_row():
    assert scope.book_expr("x") == f"COALESCE(x.book, '{book_type.DEFAULT_BOOK}')"
    assert scope.book_expr() == scope.book_expr("t")


def test_get_db_closes_its_connection(temp_db):
    with db.get_db() as conn:
        conn.execute("SELECT 1")
    try:
        conn.execute("SELECT 1")
        closed = False
    except Exception:
        closed = True
    assert closed


def test_trend_blueprint_is_registered_and_serves_only_the_trend_routes():
    """The blueprint carries R1-R3 (spec 15, Server routes) as their lanes land,
    and nothing else."""
    import re
    import trend_routes
    assert fin_app.app.blueprints[trend_routes.bp.name] is trend_routes.bp
    served = [r for r in fin_app.app.url_map.iter_rules()
              if r.endpoint.startswith(trend_routes.bp.name + ".")]
    allowed = re.compile(r"^/api/trend$|^/api/trend/types$|^/api/accounts/<[^>]+>/balances$")
    assert all(allowed.match(r.rule) and r.methods - {"HEAD", "OPTIONS"} == {"GET"}
               for r in served), [r.rule for r in served]


def test_route_modules_exist_and_none_imports_app():
    for name in LANE_MODULES + ("trend_routes", "scope"):
        importlib.import_module(name)
        tree = ast.parse((ROOT / f"{name}.py").read_text(encoding="utf-8"))
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        assert "app" not in imported, name


def _seed(conn):
    conn.execute("INSERT INTO accounts (id, name, short_name, type, currency, owner) VALUES (900, 'Test acct', 'Test', 'card', 'SGD', 'Household')")
    conn.execute("INSERT INTO statements (id, account_id, statement_date) VALUES (900, 900, '2026-01-31')")
    for sid, name, hidden in ((900, "SHOWN CO", 0), (901, "HIDDEN CO", 1)):
        conn.execute("INSERT INTO services (id, name, exclude_from_expense_views) VALUES (?, ?, ?)",
                     (sid, name, hidden))
        conn.execute(
            "INSERT INTO transactions (statement_id, date, description, amount_minor, service_id, flow_type)"
            " VALUES (900, '2026-01-10', ?, -1000, ?, 'expense')", (name, sid))
    conn.commit()


def _descriptions(client, query=""):
    data = client.get("/api/transactions" + query).get_json()
    rows = data.get("transactions", data) if isinstance(data, dict) else data
    return {r["description"] for r in rows}


def test_include_hidden_lists_hidden_merchants_only_on_request(conn, client):
    _seed(conn)
    assert _descriptions(client) == {"SHOWN CO"}
    assert _descriptions(client, "?include_hidden=false") == {"SHOWN CO"}
    assert _descriptions(client, "?include_hidden=true") == {"SHOWN CO", "HIDDEN CO"}
