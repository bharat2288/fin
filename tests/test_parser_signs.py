"""Balance-delta direction detection for bank-statement parsers.

Regression coverage for the 2026-06-11 sign-repair sweep: DBS/UOB bank PDF
parsers used to mark every ambiguous line as a withdrawal, mis-signing
deposits (Independent Reserve remittances, interest credits, incoming PayNow).
"""

from flow import OWN_ALIAS_SEED, ClassifierContext, classify_flow
from parse_dbs import _direction_from_balance


def _ctx():
    return ClassifierContext(
        own_aliases=OWN_ALIAS_SEED,
        owned_bank_refs=("438-59169-9", "072-560530-0"),
    )


def test_kalesh_dashed_ib_ref_is_transfer():
    facts = {
        "description": "TRF FT251229MB20925746 072-560530-0:IB | 072-560530-0:IB",
        "amount_minor": 6000000,
    }
    assert classify_flow(facts, _ctx()) == "transfer"


def test_independent_reserve_remittance_is_transfer():
    facts = {
        "description": "Advice Remittance Transfer of Funds 0016RF5856080 INDEPENDENT RESERVE",
        "amount_minor": -50000000,
    }
    assert classify_flow(facts, _ctx()) == "transfer"


# Balances and amounts are whole cents, as the parsers read them.


def test_deposit_detected_when_balance_rises():
    withdrawal, deposit = _direction_from_balance(52749749, 104547699, [51797950])
    assert withdrawal is None
    assert deposit == 51797950


def test_withdrawal_detected_when_balance_falls():
    withdrawal, deposit = _direction_from_balance(100000, 75000, [25000])
    assert withdrawal == 25000
    assert deposit is None


def test_no_anchor_returns_none_pair():
    assert _direction_from_balance(None, 75000, [25000]) == (None, None)


def test_unreconciled_delta_returns_none_pair():
    # Delta (100) doesn't match any printed amount — caller must fall back
    assert _direction_from_balance(100000, 110000, [4200]) == (None, None)


def test_delta_matches_any_candidate_not_just_first():
    # Description-embedded decimals appear before the true amount
    withdrawal, deposit = _direction_from_balance(10000, 6179, [750000, 3821])
    assert withdrawal == 3821
    assert deposit is None


def test_one_cent_tolerance():
    withdrawal, deposit = _direction_from_balance(1000, 4820, [3821])
    assert withdrawal is None
    assert deposit == 3820


def test_two_cents_out_is_not_matched():
    assert _direction_from_balance(1000, 4820, [3822]) == (None, None)
    assert _direction_from_balance(1000, 4820, [3818]) == (None, None)


def test_the_movement_is_exact_where_floats_would_not_be():
    # 0.30 - 0.10 is 0.19999999999999998 as floats; in cents it is 20.
    assert _direction_from_balance(30, 10, [20]) == (20, None)
