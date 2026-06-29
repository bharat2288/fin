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
        "amount_sgd": 60000.0,
    }
    assert classify_flow(facts, _ctx()) == "transfer"


def test_independent_reserve_remittance_is_transfer():
    facts = {
        "description": "Advice Remittance Transfer of Funds 0016RF5856080 INDEPENDENT RESERVE",
        "amount_sgd": -500000.0,
    }
    assert classify_flow(facts, _ctx()) == "transfer"


def test_deposit_detected_when_balance_rises():
    withdrawal, deposit = _direction_from_balance(527497.49, 1045476.99, [517979.50])
    assert withdrawal is None
    assert deposit == 517979.50


def test_withdrawal_detected_when_balance_falls():
    withdrawal, deposit = _direction_from_balance(1000.00, 750.00, [250.00])
    assert withdrawal == 250.00
    assert deposit is None


def test_no_anchor_returns_none_pair():
    assert _direction_from_balance(None, 750.00, [250.00]) == (None, None)


def test_unreconciled_delta_returns_none_pair():
    # Delta (100) doesn't match any printed amount — caller must fall back
    assert _direction_from_balance(1000.00, 1100.00, [42.00]) == (None, None)


def test_delta_matches_any_candidate_not_just_first():
    # Description-embedded decimals appear before the true amount
    withdrawal, deposit = _direction_from_balance(100.00, 61.79, [7500.00, 38.21])
    assert withdrawal == 38.21
    assert deposit is None


def test_one_cent_tolerance():
    withdrawal, deposit = _direction_from_balance(10.00, 48.20, [38.21])
    assert deposit == 38.20
