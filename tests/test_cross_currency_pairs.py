"""Cross-currency pair offers (fin-redesign 24; spec part 2, cases 10 and 13;
fin RUL-022).

An S$ row out and a row in another currency in, on household bank accounts,
a day apart at most, whose rate is within 3% of that day's ECB rate, are
offered as a pair. Confirming stores the real rate (the amounts', or an RFX
rate in the wording, "SGD500.0@74.52") for both rows, and from then on the
foreign leg is valued at it; a waiting leg becomes a transfer naming the
other's account. Not a pair is remembered. Each is one change in Changes,
and undoing a confirmed pair clears its rate.

Driven through the HTTP interface (the pair routes, /api/transactions,
Changes, Undo) and a start (db.init_db). Every name and figure is invented;
the amounts are case 10's.
"""

import pytest

import db

ECB = {  # INR/SGD, near case 10's days: 73.48, 73.80, 74.07, 74.35 rupees a dollar
    "2026-04-16": "0.013609",
    "2026-08-04": "0.013550",
    "2026-08-17": "0.013500",
    "2026-08-24": "0.013450",
}


def make_account(conn, name, currency="SGD", kind="bank"):
    acct = conn.execute(
        "INSERT INTO accounts (name, short_name, type, currency) VALUES (?, ?, ?, ?)",
        (name, name.replace(" ", "-"), kind, currency),
    ).lastrowid
    conn.commit()
    return acct


def make_row(conn, account, day, description, amount_minor, flow_type="review", manual=0, other_side=None):
    held = conn.execute("SELECT id FROM statements WHERE account_id = ? AND statement_date = ?",
                        (account, day)).fetchone()
    stmt = held[0] if held else conn.execute(
        "INSERT INTO statements (account_id, statement_date, filename) VALUES (?, ?, ?)",
        (account, day, f"{account}-{day}.csv"),
    ).lastrowid
    row = conn.execute(
        "INSERT INTO transactions (statement_id, date, description, amount_minor, flow_type,"
        " flow_type_manual, other_side_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (stmt, day, description, amount_minor, flow_type, manual, other_side),
    ).lastrowid
    conn.commit()
    return row


def save_rates(conn, rates=ECB):
    for day, rate in rates.items():
        conn.execute("INSERT INTO rates (pair, date, rate, source, fetched_at) VALUES ('INR/SGD', ?, ?, 'test ECB', '2026-10-26T00:00:00Z')",
                     (day, rate))
    conn.commit()


@pytest.fixture
def book(conn):
    """Case 10: four S$ transfers out of a DBS account and their rupee legs."""
    dbs = make_account(conn, "Sample DBS")
    hdfc = make_account(conn, "Sample HDFC 3528", "INR")
    shared = make_account(conn, "Sample HDFC 6324", "INR")
    # 6324 is shared (fin-redesign 20): #2124's rupee leg is hers, not ours.
    conn.execute("INSERT INTO shared_accounts (account_id) VALUES (?)", (shared,))
    save_rates(conn)
    legs = {
        "2094": (make_row(conn, dbs, "2026-04-16", "REMITTANCE TO SAMPLE HDFC", 15_000_001),
                 make_row(conn, hdfc, "2026-04-16", "INWARD REMITTANCE SAMPLE", -1_092_418_700)),
        "2116": (make_row(conn, dbs, "2026-08-04", "REMITTANCE TO SAMPLE HDFC", 3_500_000),
                 make_row(conn, hdfc, "2026-08-04", "INWARD REMITTANCE SAMPLE", -257_580_200)),
        "2117": (make_row(conn, dbs, "2026-08-17", "REMITTANCE TO SAMPLE HDFC", 4_059_600),
                 make_row(conn, hdfc, "2026-08-17", "INWARD REMITTANCE SAMPLE", -300_000_000)),
        "2124": (make_row(conn, dbs, "2026-08-24", "REMITTANCE TO SAMPLE PARTNER", 3_358_500),
                 make_row(conn, shared, "2026-08-24", "INWARD REMITTANCE SAMPLE", -250_000_000,
                          flow_type="not_ours")),
    }
    return {"dbs": dbs, "hdfc": hdfc, "shared": shared, "legs": legs}


def offers(client):
    resp = client.get("/api/fx-pairs/offers")
    assert resp.status_code == 200
    return resp.get_json()["offers"]


def offered(client):
    return {(o["out"]["id"], o["in"]["id"]) for o in offers(client)}


def confirm(client, out_id, in_id):
    return client.post("/api/fx-pairs", json={"out_id": out_id, "in_id": in_id})


def decline(client, out_id, in_id):
    return client.post("/api/fx-pairs/decline", json={"out_id": out_id, "in_id": in_id})


def listed(client, row_id):
    rows = client.get(f"/api/transactions?tx_id={row_id}&every=true").get_json()["transactions"]
    return next(r for r in rows if r["id"] == row_id)


def entries(client):
    return client.get("/api/history").get_json()["entries"]


def stored(conn):
    return [tuple(r) for r in conn.execute("SELECT * FROM transactions ORDER BY id")]


# --- what is offered -----------------------------------------------------------


def test_case_10s_four_transfers_are_offered_with_their_rupee_legs(client, book):
    assert offered(client) == set(book["legs"].values())


def test_an_offer_says_both_legs_the_rate_and_how_far_it_is_from_the_ecb(client, book):
    out_id, in_id = book["legs"]["2116"]
    offer = next(o for o in offers(client) if o["out"]["id"] == out_id)
    assert offer["in"]["id"] == in_id
    assert offer["out"]["account"] == "Sample DBS" and offer["in"]["account"] == "Sample HDFC 3528"
    assert offer["out"]["amount_minor"] == 3_500_000 and offer["in"]["amount_minor"] == -257_580_200
    assert offer["currency"] == "INR"
    assert offer["per_sgd"] == "73.59"          # rupees a dollar, as the amounts say
    assert offer["ecb_per_sgd"] == "73.80"
    assert offer["source"] == "amounts"
    assert offer["off_percent"] == "0.3"


def test_a_pair_outside_three_percent_is_not_offered(client, conn, book):
    dbs, hdfc = book["dbs"], book["hdfc"]
    out_id = make_row(conn, dbs, "2026-08-04", "REMITTANCE TO SAMPLE HDFC", 1_000_000)
    in_id = make_row(conn, hdfc, "2026-08-04", "INWARD REMITTANCE SAMPLE", -80_000_000)  # 80.00 a dollar, 8% off
    assert (out_id, in_id) not in offered(client)
    assert all(o["out"]["id"] != out_id for o in offers(client))


def test_the_next_day_is_offered_and_two_days_apart_is_not(client, conn, book):
    dbs, hdfc = book["dbs"], book["hdfc"]
    out_a = make_row(conn, dbs, "2026-08-10", "REMITTANCE A", 1_000_000)
    in_a = make_row(conn, hdfc, "2026-08-11", "INWARD A", -73_500_000)
    out_b = make_row(conn, dbs, "2026-08-20", "REMITTANCE B", 2_000_000)
    in_b = make_row(conn, hdfc, "2026-08-22", "INWARD B", -147_000_000)
    pairs = offered(client)
    assert (out_a, in_a) in pairs
    assert all(o != out_b and i != in_b for o, i in pairs)


def test_a_day_with_no_saved_rate_offers_nothing_never_guessed(client, conn, book):
    dbs, hdfc = book["dbs"], book["hdfc"]
    out_id = make_row(conn, dbs, "2026-06-10", "REMITTANCE JUNE", 1_000_000)
    in_id = make_row(conn, hdfc, "2026-06-10", "INWARD JUNE", -73_500_000)
    pairs = offered(client)
    assert all(o != out_id and i != in_id for o, i in pairs)


def test_spending_and_money_between_two_sgd_accounts_are_not_offered(client, conn, book):
    dbs, hdfc = book["dbs"], book["hdfc"]
    other = make_account(conn, "Sample UOB")
    out_id = make_row(conn, dbs, "2026-08-12", "SAMPLE SHOP", 1_000_000, flow_type="expense")
    in_id = make_row(conn, hdfc, "2026-08-12", "INWARD SAMPLE", -73_500_000)
    sgd_in = make_row(conn, other, "2026-08-12", "FROM SAMPLE DBS", -1_000_000)
    pairs = offered(client)
    assert all(o != out_id and i not in (in_id, sgd_in) for o, i in pairs)


def test_one_rupee_leg_is_offered_once_to_its_nearest_rate(client, conn, book):
    dbs, hdfc = book["dbs"], book["hdfc"]
    near = make_row(conn, dbs, "2026-08-13", "REMITTANCE NEAR", 1_000_000)     # 73.80 a dollar
    far = make_row(conn, dbs, "2026-08-13", "REMITTANCE FAR", 1_010_000)       # 73.07 a dollar
    in_id = make_row(conn, hdfc, "2026-08-13", "INWARD ONE", -73_800_000)
    pairs = offered(client)
    assert (near, in_id) in pairs
    assert all(o != far for o, _ in pairs)


def test_an_rfx_rate_in_the_wording_is_the_offer_s_rate(client, conn, book):
    dbs, hdfc = book["dbs"], book["hdfc"]
    out_id = make_row(conn, dbs, "2026-08-14", "REMITTANCE RFX", 3_500_000)
    in_id = make_row(conn, hdfc, "2026-08-14", "INWARD REMIT RFX SGD35000.0@73.20 SAMPLE", -256_000_000)
    offer = next(o for o in offers(client) if o["out"]["id"] == out_id)
    assert offer["in"]["id"] == in_id
    assert offer["source"] == "wording"
    assert offer["per_sgd"] == "73.20"


# --- confirming, declining, undoing ----------------------------------------------


def test_a_confirmed_pair_s_rupee_leg_is_valued_at_the_real_rate(client, book):
    out_id, in_id = book["legs"]["2094"]
    assert listed(client, in_id)["sgd_minor"] == -14_866_726      # at the ECB's 0.013609
    resp = confirm(client, out_id, in_id)
    assert resp.status_code == 200, resp.get_json()
    assert listed(client, in_id)["sgd_minor"] == -15_000_001      # the S$ that left, to the cent
    assert listed(client, out_id)["sgd_minor"] == 15_000_001


def test_confirming_stores_the_rate_on_both_rows_and_pairs_waiting_legs(client, book):
    out_id, in_id = book["legs"]["2117"]
    confirm(client, out_id, in_id)
    out_row, in_row = listed(client, out_id), listed(client, in_id)
    for row in (out_row, in_row):
        assert row["real_rate"] == {"per_sgd": "73.90", "currency": "INR", "source": "amounts",
                                    "pair_row_id": in_id if row is out_row else out_id}
        assert row["flow_type"] == "transfer"
    assert out_row["other_side_id"] == book["hdfc"] and in_row["other_side_id"] == book["dbs"]
    assert (out_id, in_id) not in offered(client)


def test_a_leg_labelled_by_hand_keeps_its_label(client, conn, book):
    owed = make_account(conn, "Owed to Sample Partner", kind="loan")
    out_id, in_id = book["legs"]["2124"]
    conn.execute("UPDATE transactions SET flow_type = 'movement', flow_type_manual = 1, other_side_id = ?"
                 " WHERE id = ?", (owed, out_id))
    conn.commit()
    assert (out_id, in_id) in offered(client)
    assert confirm(client, out_id, in_id).status_code == 200
    out_row = listed(client, out_id)
    assert out_row["flow_type"] == "movement" and out_row["other_side_id"] == owed
    assert out_row["real_rate"]["per_sgd"] == "74.44"
    assert listed(client, in_id)["sgd_minor"] == -3_358_500
    assert listed(client, in_id)["flow_type"] == "not_ours"     # hers on the shared account, still


def test_an_rfx_rate_confirmed_values_the_leg_at_the_wording_s_rate(client, conn, book):
    dbs, hdfc = book["dbs"], book["hdfc"]
    out_id = make_row(conn, dbs, "2026-08-14", "REMITTANCE RFX", 3_500_000)
    in_id = make_row(conn, hdfc, "2026-08-14", "INWARD REMIT RFX SGD35000.0@73.20 SAMPLE", -256_000_000)
    assert confirm(client, out_id, in_id).status_code == 200
    assert listed(client, in_id)["real_rate"]["source"] == "wording"
    assert listed(client, in_id)["sgd_minor"] == -3_497_268     # ₹25,60,000 / 73.20


def test_confirming_is_one_change_and_undoing_it_clears_both_rates(client, book):
    out_id, in_id = book["legs"]["2116"]
    confirm(client, out_id, in_id)
    entry = entries(client)[0]
    assert entry["summary"] == "Paired S$ 35,000.00 out of Sample DBS with ₹ 25,75,802.00 into Sample HDFC 3528 at ₹ 73.59 a dollar"
    assert client.post(f"/api/history/{entry['id']}/undo").status_code == 200
    for row_id in (out_id, in_id):
        row = listed(client, row_id)
        assert row["real_rate"] is None and row["flow_type"] == "review" and row["other_side_id"] is None
    assert listed(client, in_id)["sgd_minor"] == -3_490_212      # the ECB's again
    assert (out_id, in_id) in offered(client)


def test_not_a_pair_is_not_offered_again_and_undoing_it_offers_it_again(client, book):
    out_id, in_id = book["legs"]["2116"]
    assert decline(client, out_id, in_id).status_code == 200
    assert (out_id, in_id) not in offered(client)
    assert listed(client, in_id)["real_rate"] is None
    entry = entries(client)[0]
    assert entry["summary"] == "Not a pair: S$ 35,000.00 out of Sample DBS and ₹ 25,75,802.00 into Sample HDFC 3528"
    assert client.post(f"/api/history/{entry['id']}/undo").status_code == 200
    assert (out_id, in_id) in offered(client)


def test_a_pair_no_longer_offered_is_refused_and_nothing_is_written(client, conn, book):
    out_id, in_id = book["legs"]["2116"]
    decline(client, out_id, in_id)
    before = stored(conn)
    resp = confirm(client, out_id, in_id)
    assert resp.status_code == 409
    assert "Nothing was changed" in resp.get_json()["error"]
    assert stored(conn) == before
    assert listed(client, in_id)["real_rate"] is None


def test_two_offers_cannot_share_a_leg_once_one_is_confirmed(client, conn, book):
    out_id, in_id = book["legs"]["2117"]
    confirm(client, out_id, in_id)
    assert confirm(client, out_id, in_id).status_code == 409
    other_in = make_row(conn, book["hdfc"], "2026-08-17", "INWARD SECOND", -300_000_000)
    assert all(o != out_id for o, _ in offered(client))
    assert confirm(client, out_id, other_in).status_code == 409


@pytest.mark.parametrize("body", [{}, {"out_id": "1", "in_id": 2}, {"out_id": 1}, None])
def test_a_body_without_both_row_ids_is_refused(client, book, body):
    resp = client.post("/api/fx-pairs", json=body)
    assert resp.status_code == 400


def test_a_start_changes_no_stored_row(conn, book):
    before = stored(conn)
    db.init_db()
    assert stored(conn) == before
    assert conn.execute("SELECT COUNT(*) FROM fx_pair").fetchone()[0] == 0


def test_a_row_pairing_already_matched_in_sgd_is_not_offered(client, conn, book):
    uob = make_account(conn, "Sample UOB")
    out_id = make_row(conn, book["dbs"], "2026-08-12", "TO SAMPLE UOB", 1_000_000,
                      flow_type="transfer", other_side=uob)
    in_id = make_row(conn, book["hdfc"], "2026-08-12", "INWARD UNRELATED", -73_800_000)
    assert all(o != out_id and i != in_id for o, i in offered(client))


def test_a_leg_carrying_an_expectation_is_not_offered(client, conn, book):
    out_id, in_id = book["legs"]["2116"]
    # As the reviewer found it: an expectation on the S$ leg, written direct.
    conn.execute("INSERT INTO expectation (row_id, direction, amount_minor, currency) VALUES (?, 'back', 100000, 'SGD')",
                 (out_id,))
    conn.commit()
    assert all(o != out_id for o, _ in offered(client))
