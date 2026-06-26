"""Root-cause test for the live full-sell "Insufficient funds" bug (2026-06-26).

The probe's Ultra sell path sized the FINAL/full leg at exactly 100% of the
on-chain balance (`atomic = bal_atomic`). Selling the literal 100% makes the
router abort at the cleanup/settle step -> error_text "Insufficient funds"
(LAZARUS 00:58Z, SWCH 04:15Z live-swap log). The legacy `trader.sell()` path
already learned this: it clamps full sells to 99.9% to leave dust headroom
(core/trader.py:2665). `sell_atomic_units` ports that proven haircut to the
probe path so BOTH the Ultra call and its legacy fallback (which reuse the same
`atomic`) get headroom.

Convention: partial legs (frac < 0.999) naturally leave a remainder, so they
need NO extra haircut; only the final leg does.
"""
from core.probe_instrument import sell_atomic_units as sau


def test_full_sell_leaves_dust_headroom():
    """sold_frac=1.0 of a full position -> 99.9%, NOT 100% (the bug)."""
    assert sau(1_000_000, sold_frac=1.0, remaining_fraction=1.0) == 999_000


def test_full_sell_strictly_below_balance():
    """The regression guard: a full sell must request STRICTLY less than held."""
    bal = 25_927_100_578
    out = sau(bal, sold_frac=1.0, remaining_fraction=1.0)
    assert out < bal
    assert out == int(bal * 0.999)


def test_final_leg_detected_via_remaining_fraction():
    """Selling the last half of a half-remaining position is a FINAL leg
    (frac_of_bal = 0.5/0.5 = 1.0 >= 0.999) -> headroom applies."""
    assert sau(1_000_000, sold_frac=0.5, remaining_fraction=0.5) == 999_000


def test_partial_leg_no_extra_haircut():
    """A genuine partial leg (frac_of_bal=0.5) sells its share, leaving the rest
    as natural headroom -> no 0.999 multiplier."""
    assert sau(1_000_000, sold_frac=0.5, remaining_fraction=1.0) == 500_000


def test_never_exceeds_balance():
    """Even with absurd inputs, never request more than the wallet holds."""
    assert sau(1_000, sold_frac=2.0, remaining_fraction=1.0) <= 1_000


def test_zero_remaining_treated_as_final():
    """remaining_fraction=0 (degenerate) -> treat as final leg, not a div-by-zero."""
    assert sau(1_000_000, sold_frac=1.0, remaining_fraction=0.0) == 999_000


def test_dust_headroom_configurable():
    """The headroom fraction is overridable (e.g. thinner/thicker dust)."""
    assert sau(1_000_000, sold_frac=1.0, remaining_fraction=1.0,
               dust_headroom_frac=0.99) == 990_000


def test_zero_balance_returns_zero():
    """No balance -> nothing to sell (caller handles the ghost-close)."""
    assert sau(0, sold_frac=1.0, remaining_fraction=1.0) == 0
