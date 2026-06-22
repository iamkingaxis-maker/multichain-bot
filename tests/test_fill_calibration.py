"""TDD for core.fill_calibration — calibrate paper fill slippage/runup from
the REAL live fills in live_swaps.jsonl, per liquidity bucket. Pure + fail-open."""
import os
import json
import pytest

from core.fill_calibration import (
    LIQ_BUCKETS,
    calibrate_from_live_swaps,
    calibrated_slip_pct,
    realistic_slip_with_cap,
    load_calibration,
    _bucket_label,
)


def _rec(side="buy", success=True, liq=None, slip=None, runup=None):
    return {
        "side": side,
        "success": success,
        "liquidity_usd": liq,
        "fill_vs_mid_slippage_pct": slip,
        "reprice_runup_pct": runup,
    }


# ── calibrate_from_live_swaps ──────────────────────────────────────────────
def test_buckets_constant():
    assert LIQ_BUCKETS == [(0, 30000), (30000, 100000), (100000, float("inf"))]


def test_empty_fail_open():
    assert calibrate_from_live_swaps([]) == {}
    assert calibrate_from_live_swaps(None) == {}


def test_garbage_fail_open():
    # records missing fields / wrong types -> skipped, never raises
    out = calibrate_from_live_swaps([{"side": "buy", "success": True},
                                     "notadict", 42, None])
    # no usable numeric fields -> empty
    assert out == {}


def test_only_successful_buys_counted():
    recs = [
        _rec(side="sell", success=True, liq=10000, slip=1.0, runup=0.5),  # not buy
        _rec(side="buy", success=False, liq=10000, slip=9.0, runup=9.0),  # failed
        _rec(side="buy", success=True, liq=10000, slip=2.0, runup=1.0),   # counts
    ]
    out = calibrate_from_live_swaps(recs)
    assert out["thin"]["n"] == 1
    assert out["thin"]["slip_p50"] == 2.0
    assert out["overall"]["n"] == 1


def test_bucketing_and_percentiles():
    # thin bucket: slips 1,2,3,4,5 -> p50=3, p90 nearest-rank=5
    thin = [_rec(liq=10000, slip=s, runup=s / 10.0) for s in (1, 2, 3, 4, 5)]
    # mid bucket: 50000 liq
    mid = [_rec(liq=50000, slip=10.0, runup=1.0),
           _rec(liq=50000, slip=20.0, runup=2.0)]
    # deep bucket: 200000 liq
    deep = [_rec(liq=200000, slip=0.5, runup=0.1)]
    out = calibrate_from_live_swaps(thin + mid + deep)

    assert out["thin"]["n"] == 5
    assert out["thin"]["slip_p50"] == 3.0
    assert out["thin"]["slip_p90"] == 5.0  # nearest-rank
    assert out["thin"]["runup_p50"] == 0.3

    assert out["mid"]["n"] == 2
    assert out["mid"]["slip_p50"] == 15.0

    assert out["deep"]["n"] == 1
    assert out["deep"]["slip_p50"] == 0.5

    # overall across all 8
    assert out["overall"]["n"] == 8


def test_unknown_bucket_for_missing_liq():
    recs = [_rec(liq=None, slip=4.0, runup=1.0),
            _rec(liq="bad", slip=6.0, runup=2.0)]
    out = calibrate_from_live_swaps(recs)
    assert out["unknown"]["n"] == 2
    assert out["unknown"]["slip_p50"] == 5.0
    assert out["overall"]["n"] == 2


def test_skips_records_missing_slip():
    # rec1 has only runup, rec2 has only slip; both count as records (n=2) but
    # slip_p50 is computed over the ONE usable slip.
    recs = [_rec(liq=10000, slip=None, runup=1.0),
            _rec(liq=10000, slip=3.0, runup=None)]
    out = calibrate_from_live_swaps(recs)
    assert out["thin"]["n"] == 2
    assert out["thin"]["slip_p50"] == 3.0  # only one usable slip
    assert out["thin"]["runup_p50"] == 1.0  # only one usable runup

    # a record with NEITHER usable field is dropped entirely
    out2 = calibrate_from_live_swaps([_rec(liq=10000, slip=None, runup=None),
                                      _rec(liq=10000, slip=5.0, runup=2.0)])
    assert out2["thin"]["n"] == 1


# ── _bucket_label ──────────────────────────────────────────────────────────
def test_bucket_label():
    assert _bucket_label(0) == "thin"
    assert _bucket_label(29999) == "thin"
    assert _bucket_label(30000) == "mid"
    assert _bucket_label(99999) == "mid"
    assert _bucket_label(100000) == "deep"
    assert _bucket_label(5_000_000) == "deep"
    assert _bucket_label(None) == "unknown"
    assert _bucket_label("garbage") == "unknown"


# ── calibrated_slip_pct ────────────────────────────────────────────────────
def test_calibrated_uses_bucket_when_enough_n():
    calib = {"thin": {"slip_p50": 3.0, "n": 5},
             "overall": {"slip_p50": 9.0, "n": 10}}
    assert calibrated_slip_pct(calib, 10000, default=1.5, min_n=5) == 3.0


def test_calibrated_falls_back_to_overall_when_bucket_thin():
    calib = {"thin": {"slip_p50": 3.0, "n": 2},
             "overall": {"slip_p50": 9.0, "n": 10}}
    assert calibrated_slip_pct(calib, 10000, default=1.5, min_n=5) == 9.0


def test_calibrated_falls_back_to_default_when_all_thin():
    calib = {"thin": {"slip_p50": 3.0, "n": 1},
             "overall": {"slip_p50": 9.0, "n": 2}}
    assert calibrated_slip_pct(calib, 10000, default=1.5, min_n=5) == 1.5


def test_calibrated_empty_calib_returns_default():
    assert calibrated_slip_pct({}, 10000, default=1.5) == 1.5
    assert calibrated_slip_pct(None, 10000, default=1.5) == 1.5


def test_calibrated_unknown_liq_uses_overall():
    calib = {"unknown": {"slip_p50": 4.0, "n": 1},
             "overall": {"slip_p50": 7.0, "n": 6}}
    assert calibrated_slip_pct(calib, None, default=1.5, min_n=5) == 7.0


# ── realistic_slip_with_cap ────────────────────────────────────────────────
def test_realistic_below_cap_unchanged():
    assert realistic_slip_with_cap(2.0, ultra_cap_pct=4.0, legacy_extra_pct=2.0) == 2.0
    assert realistic_slip_with_cap(4.0, ultra_cap_pct=4.0, legacy_extra_pct=2.0) == 4.0


def test_realistic_above_cap_adds_legacy_extra():
    assert realistic_slip_with_cap(5.0, ultra_cap_pct=4.0, legacy_extra_pct=2.0) == 7.0


def test_realistic_reads_cap_from_env(monkeypatch):
    monkeypatch.setenv("PROBE_ULTRA_SLIPPAGE_BPS", "300")  # 3.0%
    # 3.5 > 3.0 cap -> add legacy extra
    assert realistic_slip_with_cap(3.5, legacy_extra_pct=2.0) == 5.5
    # 2.5 < 3.0 cap -> unchanged
    assert realistic_slip_with_cap(2.5, legacy_extra_pct=2.0) == 2.5


def test_realistic_fail_open_garbage():
    # garbage input -> returns input unchanged (fail-open), never raises
    assert realistic_slip_with_cap(None) is None


# ── load_calibration (cached loader) ───────────────────────────────────────
def test_load_calibration_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import core.fill_calibration as fc
    fc._CACHE.clear()
    assert load_calibration() == {}


def test_load_calibration_reads_and_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    p = tmp_path / "live_swaps.jsonl"
    recs = [_rec(liq=10000, slip=2.0, runup=1.0),
            _rec(liq=10000, slip=4.0, runup=1.0)]
    p.write_text("\n".join(json.dumps(r) for r in recs) + "\n")
    import core.fill_calibration as fc
    fc._CACHE.clear()
    out = load_calibration()
    assert out["thin"]["n"] == 2
    assert out["thin"]["slip_p50"] == 3.0
    # cached: second call returns same object without re-reading
    out2 = load_calibration()
    assert out2 is out
