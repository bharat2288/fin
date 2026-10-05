"""The spread on a conversion between the rupee account and an SGD account is
currency change in the month check, not spending and not unexplained
(ruling 5). Through the HTTP interface; every name, figure and rate is
invented. Cents on the SGD account, paise on the rupee account."""

from test_month_check import BANK, RUPEE, bring_in, check, household, make_account, set_rate

RATE = "0.0125"              # one rupee in SGD, at both month-ends: no revaluation
SGD_PAID = 100_000           # S$ 1,000.00 out of the bank, at the bank's rate
RUPEES_IN = -8_320_000       # Rs 83,200.00 in: S$ 1,040.00 at the month-end rate
SPREAD = 104_000 - SGD_PAID  # S$ 40.00


def build(client, *, rupee_names_bank: bool = True) -> None:
    make_account(client, BANK, "bank", last_four="0002")
    rupee = make_account(client, RUPEE, "bank", currency="INR")
    bank = next(a["id"] for a in client.get("/api/accounts").get_json() if a["name"] == BANK)
    set_rate(client, "2026-07-31", RATE)
    set_rate(client, "2026-08-31", RATE)
    bring_in(client, BANK, 5_010_000, [
        ("2026-07-10", "SAMPLE BAKERY JUL", 10_000, "expense", household("Groceries")),
    ], closing_date="2026-07-31")
    bring_in(client, RUPEE, 10_050_000, [
        ("2026-07-14", "UPI-SAMPLE GROCER JUL", 50_000, "expense", household("Groceries")),
    ], closing_date="2026-07-31", currency="INR")
    bring_in(client, BANK, 5_000_000, [
        ("2026-08-12", "OUTWARD TELEGRAPHIC TRANSFER REF 550012", SGD_PAID, "transfer", {"names": rupee}),
    ])
    bring_in(client, RUPEE, 10_000_000, [
        ("2026-08-14", "INWARD REMITTANCE SAMPLE", RUPEES_IN, "transfer",
         {"names": bank} if rupee_names_bank else {}),
    ], currency="INR")


def test_the_spread_on_a_conversion_is_currency_change_and_nothing_is_unexplained(client):
    build(client)

    shown = check(client)

    assert shown["currency_change_minor"] == SPREAD == 4_000
    assert shown["unexplained_minor"] == 0
    (line,) = shown["currency_change_lines"]
    assert (line["name"], line["revaluation_minor"], line["spread_minor"]) == (RUPEE, 0, 4_000)
    # Neither leg is income or spending.
    assert (shown["income_minor"], shown["spending_minor"]) == (0, 0)


def test_a_conversion_named_on_one_side_only_leaves_its_spread_unexplained(client):
    build(client, rupee_names_bank=False)

    shown = check(client)

    assert shown["currency_change_minor"] == 0
    assert shown["unexplained_minor"] == SPREAD
