"""Phantom-guard correctness (scripts/scrub_phantom_pnl._is_phantom).
2026-06-02 multi-regime mine bug: exit/entry>3 fired on records with a corrupt
entry_price FIELD whose pnl_pct was legit -> dropped real pre-05-27 wins. Fix:
trust pnl_pct when present; only fall back to the ratio when pnl_pct is missing."""
from scripts.scrub_phantom_pnl import _is_phantom, PHANTOM_PCT, PHANTOM_RATIO


def test_legit_pnl_with_corrupt_entry_field_NOT_phantom():
    # the exact bug: entry field garbage (0.00025) -> exit/entry=44360>3, but pnl is +10.92%
    s = {"type": "sell", "pnl_pct": 10.92, "entry_price": 0.00025, "exit_price": 11.09}
    assert _is_phantom(s) is False


def test_real_win_phantom_by_pnl():
    s = {"type": "sell", "pnl_pct": 300.0, "entry_price": 1.0, "exit_price": 4.0}
    assert _is_phantom(s) is True


def test_sane_pnl_high_ratio_trusts_pnl():
    # pnl sane (+50%) but exit/entry=10>3 -> trust pnl, NOT a phantom (the bug case generalized)
    s = {"type": "sell", "pnl_pct": 50.0, "entry_price": 1.0, "exit_price": 10.0}
    assert _is_phantom(s) is False


def test_ratio_fallback_when_pnl_missing():
    # no pnl_pct -> fall back to the ratio heuristic (still catches a glitch)
    s = {"type": "sell", "pnl_pct": None, "entry_price": 1.0, "exit_price": 5.0}
    assert _is_phantom(s) is True


def test_normal_win_not_phantom():
    s = {"type": "sell", "pnl_pct": 35.0, "entry_price": 1.0, "exit_price": 1.35}
    assert _is_phantom(s) is False


def test_buy_never_phantom():
    assert _is_phantom({"type": "buy", "pnl_pct": 999}) is False
