"""TDD for Tier-2 GAP A — real priority-fee extraction + paper fee calibration.

Part 1: parse_ultra_order surfaces the REAL prioritizationFeeLamports.
Part 2: core.fill_calibration.calibrated_fee_usd learns the median real per-tx fee
        from live_swaps; core.paper_fidelity.paper_fee_usd consults it ONLY when the
        gate (PAPER_FEE_CALIBRATION_MODE) is shadow/enforce — default off byte-identical.
Pure + fail-open throughout.
"""
import json
import os

import core.trader as T
from core.fill_calibration import calibrated_fee_usd, load_fee_calibration, BASE_FEE_LAMPORTS
import core.paper_fidelity as PF
from core.paper_fidelity import paper_fee_usd, effective_fill


# ── Part 1: parse_ultra_order surfaces priority_fee_lamports ─────────────────
def test_parse_order_surfaces_priority_fee():
    r = T.parse_ultra_order({
        "transaction": "BASE64TX", "requestId": "rid-1",
        "outAmount": "12345", "inAmount": "20000000", "router": "metis",
        "prioritizationFeeLamports": 175000,
    })
    assert r["ok"] is True
    assert r["priority_fee_lamports"] == 175000


def test_parse_order_priority_fee_none_safe_when_absent():
    r = T.parse_ultra_order({"transaction": "tx", "requestId": "r"})
    assert r["ok"] is True
    # key always present, None when the response omits it
    assert "priority_fee_lamports" in r
    assert r["priority_fee_lamports"] is None


def test_parse_order_bad_response_no_crash():
    # unusable response -> ok False, never raises
    assert T.parse_ultra_order(None)["ok"] is False
    assert T.parse_ultra_order({"error": "no route"})["ok"] is False


# ── Part 2a: calibrated_fee_usd ──────────────────────────────────────────────
SOL = 150.0  # $/SOL for the worked examples


def _rec(lam=175000, success=True):
    return {"side": "buy", "success": success, "priority_fee_lamports": lam}


def test_calibrated_fee_thin_sample_returns_default():
    # < min_n (default 10) qualifying records -> caller's default, no change
    recs = [_rec() for _ in range(9)]
    assert calibrated_fee_usd(recs, default=0.17, sol_price_usd=SOL) == 0.17


def test_calibrated_fee_sufficient_sample_returns_median():
    recs = [_rec(175000) for _ in range(10)]
    fee = calibrated_fee_usd(recs, default=0.17, sol_price_usd=SOL)
    # (175000 + 5000) / 1e9 * 150 = 0.027
    expected = (175000 + BASE_FEE_LAMPORTS) / 1e9 * SOL
    assert abs(fee - expected) < 1e-6
    assert abs(fee - 0.027) < 1e-6
    # the fidelity correction: calibrated << the 0.17 placeholder
    assert fee < 0.17


def test_calibrated_fee_median_of_spread():
    lams = [100000, 150000, 175000, 200000, 250000,
            175000, 175000, 180000, 160000, 190000, 175000]  # n=11, median=175000
    recs = [_rec(l) for l in lams]
    fee = calibrated_fee_usd(recs, default=0.17, sol_price_usd=SOL)
    expected = (175000 + BASE_FEE_LAMPORTS) / 1e9 * SOL
    assert abs(fee - expected) < 1e-6


def test_calibrated_fee_skips_failed_and_garbage():
    good = [_rec(175000) for _ in range(10)]
    bad = [
        _rec(175000, success=False),          # failed swap -> skip
        {"side": "buy", "success": True},     # missing field -> skip
        {"success": True, "priority_fee_lamports": "garbage"},  # non-numeric -> skip
        {"success": True, "priority_fee_lamports": -5},         # negative -> skip
        {"success": True, "priority_fee_lamports": None},       # None -> skip
        "not-a-dict",
    ]
    fee = calibrated_fee_usd(good + bad, default=0.17, sol_price_usd=SOL)
    expected = (175000 + BASE_FEE_LAMPORTS) / 1e9 * SOL
    assert abs(fee - expected) < 1e-6


def test_calibrated_fee_no_sol_price_fails_open():
    recs = [_rec() for _ in range(10)]
    assert calibrated_fee_usd(recs, default=0.17, sol_price_usd=None) == 0.17
    assert calibrated_fee_usd(recs, default=0.17, sol_price_usd=0) == 0.17


def test_calibrated_fee_empty_fails_open():
    assert calibrated_fee_usd([], default=0.17, sol_price_usd=SOL) == 0.17
    assert calibrated_fee_usd(None, default=0.17, sol_price_usd=SOL) == 0.17


# ── Part 2b: paper_fee_usd gate ──────────────────────────────────────────────
def _write_live_swaps(tmp_path, lam=175000, n=12):
    p = tmp_path / "live_swaps.jsonl"
    with open(p, "w") as f:
        for _ in range(n):
            f.write(json.dumps({"side": "buy", "success": True,
                                 "priority_fee_lamports": lam}) + "\n")
    return tmp_path


def _reset_fee_cache():
    import core.fill_calibration as FC
    FC._FEE_REC_CACHE.clear()


def test_paper_fee_mode_off_is_unchanged(monkeypatch):
    monkeypatch.delenv("PAPER_FEE_CALIBRATION_MODE", raising=False)
    monkeypatch.delenv("PAPER_FEE_USD_PER_TX", raising=False)
    assert paper_fee_usd() == 0.17  # byte-identical to historical default


def test_paper_fee_off_ignores_calibration(monkeypatch, tmp_path):
    # even with rich live data, mode=off books the placeholder EXACTLY
    _write_live_swaps(tmp_path)
    _reset_fee_cache()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOL_PRICE_USD", str(SOL))
    monkeypatch.setenv("PAPER_FEE_CALIBRATION_MODE", "off")
    assert paper_fee_usd() == 0.17


def test_paper_fee_enforce_books_calibrated(monkeypatch, tmp_path):
    _write_live_swaps(tmp_path, lam=175000, n=12)
    _reset_fee_cache()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOL_PRICE_USD", str(SOL))
    monkeypatch.setenv("PAPER_FEE_CALIBRATION_MODE", "enforce")
    fee = paper_fee_usd()
    expected = (175000 + BASE_FEE_LAMPORTS) / 1e9 * SOL  # ~0.027
    assert abs(fee - expected) < 1e-6
    assert fee < 0.17


def test_paper_fee_enforce_thin_sample_fails_open(monkeypatch, tmp_path):
    # < min_n live records -> placeholder (no change until the sample accrues)
    _write_live_swaps(tmp_path, n=3)
    _reset_fee_cache()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOL_PRICE_USD", str(SOL))
    monkeypatch.setenv("PAPER_FEE_CALIBRATION_MODE", "enforce")
    assert paper_fee_usd() == 0.17


def test_paper_fee_shadow_books_placeholder(monkeypatch, tmp_path):
    # shadow logs the delta but STILL books the placeholder (no behavior change)
    _write_live_swaps(tmp_path, n=12)
    _reset_fee_cache()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOL_PRICE_USD", str(SOL))
    monkeypatch.setenv("PAPER_FEE_CALIBRATION_MODE", "shadow")
    assert paper_fee_usd() == 0.17


def test_paper_fee_enforce_explicit_sol_price_arg(monkeypatch, tmp_path):
    _write_live_swaps(tmp_path, n=12)
    _reset_fee_cache()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SOL_PRICE_USD", raising=False)
    monkeypatch.setenv("PAPER_FEE_CALIBRATION_MODE", "enforce")
    fee = paper_fee_usd(sol_price_usd=SOL)
    expected = (175000 + BASE_FEE_LAMPORTS) / 1e9 * SOL
    assert abs(fee - expected) < 1e-6


def test_paper_fee_enforce_no_sol_price_fails_open(monkeypatch, tmp_path):
    _write_live_swaps(tmp_path, n=12)
    _reset_fee_cache()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("SOL_PRICE_USD", raising=False)
    monkeypatch.setenv("PAPER_FEE_CALIBRATION_MODE", "enforce")
    assert paper_fee_usd() == 0.17  # no SOL price -> placeholder


# ── Part 2c: the $5 round-trip fidelity correction ──────────────────────────
def test_five_dollar_roundtrip_fee_drop(monkeypatch, tmp_path):
    """At $5 size the booked per-side fee drops ~0.17 -> ~0.027, ~5.7pp round-trip."""
    _write_live_swaps(tmp_path, lam=175000, n=12)
    _reset_fee_cache()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOL_PRICE_USD", str(SOL))

    monkeypatch.setenv("PAPER_FEE_CALIBRATION_MODE", "off")
    fee_before = paper_fee_usd()
    monkeypatch.setenv("PAPER_FEE_CALIBRATION_MODE", "enforce")
    fee_after = paper_fee_usd()

    assert abs(fee_before - 0.17) < 1e-9
    assert abs(fee_after - 0.027) < 1e-6

    size = 5.0
    # per-side fee drag fraction
    frac_before = fee_before / size
    frac_after = fee_after / size
    # round-trip (buy + sell) overstatement removed, in pp
    rt_pp_before = frac_before * 2 * 100
    rt_pp_after = frac_after * 2 * 100
    assert abs(rt_pp_before - 6.8) < 0.1     # 0.17/5*2 = 6.8pp
    assert abs(rt_pp_after - 1.08) < 0.05    # 0.027/5*2 = 1.08pp
    assert (rt_pp_before - rt_pp_after) > 5.0  # ~5.7pp correction

    # and it actually moves the booked fill price at $5
    buy_before = effective_fill(1.0, "buy", 0.0, fee_before, size)
    buy_after = effective_fill(1.0, "buy", 0.0, fee_after, size)
    assert buy_after < buy_before  # cheaper modeled buy under enforce


def test_hundred_dollar_roundtrip_negligible(monkeypatch, tmp_path):
    """At $100 size the fee correction is negligible (sub-0.3pp round-trip)."""
    _write_live_swaps(tmp_path, lam=175000, n=12)
    _reset_fee_cache()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SOL_PRICE_USD", str(SOL))
    monkeypatch.setenv("PAPER_FEE_CALIBRATION_MODE", "enforce")
    fee_after = paper_fee_usd()
    size = 100.0
    rt_pp_before = 0.17 / size * 2 * 100   # 0.34pp
    rt_pp_after = fee_after / size * 2 * 100  # ~0.054pp
    assert rt_pp_before < 0.4
    assert rt_pp_after < 0.1
