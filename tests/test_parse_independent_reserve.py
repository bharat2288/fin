"""The Independent Reserve account activity export.

The export is one CSV of everything the exchange account did, newest first:
a "sep=," line, then Settled, Date, the transaction's ids, Type, Status,
Currency, Credit, Debit, Balance (that currency's running balance), Comment
and the blockchain id. Times are the exchange's own (Sydney).

fin reads from it the household's moves of Singapore dollars in and out of
the exchange: the SGD deposits and withdrawals, dated in Singapore. A trade
sells one currency for another inside the exchange, with its brokerage and
GST, and moves no money in or out of the household, so it is no move; a
withdrawal's fee and its GST are the exchange's charges, inside it. fin keeps
Independent Reserve as a holding (RUL-012), and a holding takes no statement
rows: so the upload says what the file holds and imports nothing from it.

Every id, figure and comment here is invented.
"""

import io

import pytest

import parse_independent_reserve as ir

HEADER = (
    "Settled,Date,TransactionGuid,TradeGuid,OrderGuid,Type,Status,Currency,"
    "Credit,Debit,Balance,Comment,BlockchainTransaction"
)


def line(when, kind, currency, credit="", debit="", balance="0", status="Confirmed",
         trade="", comment="", guid="00000000-0000-0000-0000-000000000000"):
    return ",".join([when, when, guid, trade, "", kind, status, currency, credit, debit, balance, comment, ""])


# Newest first, as the export is. A sale of USDT for SGD with its brokerage
# and GST; an SGD withdrawal with its fee and the fee's GST; a USDT deposit;
# an SGD deposit at 01:30 Sydney time, which is the day before in Singapore;
# and a USD withdrawal the exchange rejected.
ROWS = [
    line("12 Mar 2026 10:00:00 +11:00", "Withdrawal Fee", "SGD", debit="1.50000000", balance="0.00000000"),
    line("12 Mar 2026 10:00:00 +11:00", "GST", "SGD", debit="0.15000000", balance="1.50000000"),
    line("12 Mar 2026 10:00:00 +11:00", "Withdrawal", "SGD", debit="3000.00000000", balance="1.65000000",
         comment="sample note"),
    line("10 Mar 2026 09:00:00 +11:00", "GST", "SGD", debit="0.32123456", balance="3001.65000000", trade="t1"),
    line("10 Mar 2026 09:00:00 +11:00", "Brokerage", "SGD", debit="3.21234567", balance="3001.97123456", trade="t1"),
    line("10 Mar 2026 09:00:00 +11:00", "Trade", "SGD", credit="2005.18358023", balance="3005.18358023", trade="t1"),
    line("10 Mar 2026 09:00:00 +11:00", "Trade", "USDT", debit="1500.000000", balance="0.000000", trade="t1"),
    line("09 Mar 2026 08:00:00 +11:00", "Deposit", "USDT", credit="1500.000000", balance="1500.000000"),
    line("02 Mar 2026 01:30:00 +11:00", "Deposit", "SGD", credit="1000.00000000", balance="1000.00000000"),
    line("01 Feb 2026 12:00:00 +11:00", "Withdrawal", "USD", debit="50.00000000", balance="0.00000000",
         status="Rejected"),
]


def export(rows=ROWS) -> str:
    return "\r\n".join(["sep=,", HEADER, *rows]) + "\r\n"


@pytest.fixture
def path(tmp_path):
    def write(text=None):
        target = tmp_path / "2026-01_to_2026-03.csv"
        target.write_text(export() if text is None else text, encoding="utf-8")
        return str(target)
    return write


def test_the_sgd_deposits_and_withdrawals_are_the_moves_dated_in_singapore(path):
    activity = ir.read_activity(path())

    assert [(tx.date, tx.description, tx.amount_minor) for tx in activity.moves] == [
        ("2026-03-01", "Independent Reserve SGD deposit", -100000),
        ("2026-03-12", "Independent Reserve SGD withdrawal sample note", 300000),
    ]
    assert {tx.card_info for tx in activity.moves} == {"Independent Reserve"}


def test_trades_fees_other_currencies_and_rejected_lines_are_no_moves(path):
    activity = ir.read_activity(path())

    assert activity.left_out == {
        "SGD Trade": 1, "SGD Brokerage": 1, "SGD GST": 2, "SGD Withdrawal Fee": 1,
        "in another currency": 2, "not confirmed": 1,
    }
    assert (activity.first_date, activity.last_date) == ("2026-02-01", "2026-03-12")


def test_a_move_finer_than_a_cent_is_refused(path):
    rows = [line("02 Mar 2026 12:00:00 +11:00", "Deposit", "SGD", credit="1000.00500000", balance="1000.00500000")]

    with pytest.raises(ValueError, match="cent"):
        ir.read_activity(path(export(rows)))


def test_the_registry_knows_the_export_and_the_upload_imports_nothing_from_it(path, client):
    with open(path(), "rb") as handle:
        body = handle.read()

    resp = client.post(
        "/api/import/upload",
        data={"files": (io.BytesIO(body), "2026-01_to_2026-03.csv")},
        content_type="multipart/form-data",
    )

    preview = resp.get_json()
    assert preview["groups"] == [] and preview.get("figures") == []
    [error] = preview["errors"]
    assert error["error"] == (
        "Independent Reserve account activity, 1 Feb 2026 to 12 Mar 2026: 1 SGD deposit and "
        "1 SGD withdrawal read. Independent Reserve is a holding, and a holding takes no "
        "statement rows: its deposits and withdrawals are the bank rows that name it. "
        "Nothing was imported."
    )


def test_a_dbs_csv_is_not_taken_for_the_export(tmp_path):
    dbs = tmp_path / "dbs.csv"
    dbs.write_text("Account Details For:,Sample Account 000-0-000001\r\n", encoding="utf-8")

    assert not ir.detect_independent_reserve_csv(str(dbs))
