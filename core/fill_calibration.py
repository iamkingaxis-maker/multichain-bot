"""core.fill_calibration — calibrate the PAPER fill's slippage + run-up from the
REAL live fills recorded in DATA_DIR/live_swaps.jsonl, per liquidity bucket.

Instead of paper paying a FIXED ~1.5% slippage assumption, this learns what
live ACTUALLY paid (per thin/mid/deep liquidity bucket) so paper books the real
cost. DEFAULT-SAFE: when the live sample is thin (live is paused, very few
fills) every helper degrades gracefully to the caller-supplied placeholder, so
there is NO behavior change until real fills accrue.

PURE + FAIL-OPEN: ``calibrate_from_live_swaps`` / ``calibrated_slip_pct`` /
``realistic_slip_with_cap`` never raise — they wrap aggregation, skip malformed
records, and fall open to the default. Address-keyed upstream; no money path,
no live path. ``load_calibration`` does a blocking file read (callers are
off-loop / infrequent) and caches by file mtime.
"""
from __future__ import annotations

import os
import statistics
from typing import Any

# thin / mid / deep liquidity buckets (USD).
LIQ_BUCKETS = [(0, 30000), (30000, 100000), (100000, float("inf"))]
_BUCKET_LABELS = ["thin", "mid", "deep"]


def _as_float(v: Any):
    """Return float(v) or None if not a finite number (bool excluded).

    Same NaN/inf/bool-safe coercion as core.top_bots._as_float."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return None
    return f


def _bucket_label(liq_usd: Any) -> str:
    """Map a liquidity (USD) to a bucket label. None/garbage -> 'unknown'."""
    f = _as_float(liq_usd)
    if f is None:
        return "unknown"
    for (lo, hi), label in zip(LIQ_BUCKETS, _BUCKET_LABELS):
        if lo <= f < hi:
            return label
    # f below 0 or otherwise unmatched -> unknown (defensive; ranges cover >=0)
    return "unknown"


def _pctile_nearest_rank(vals, q: float):
    """Nearest-rank percentile of a numeric list. None on empty."""
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return s[i]


def _bucket_stats(slips: list, runups: list, n_records: int) -> dict:
    """Compute the per-bucket stat block. Fail-open on empty lists."""
    return {
        "slip_p50": (round(statistics.median(slips), 6) if slips else None),
        "slip_p90": (round(_pctile_nearest_rank(slips, 0.90), 6) if slips else None),
        "runup_p50": (round(statistics.median(runups), 6) if runups else None),
        "n": n_records,
    }


def calibrate_from_live_swaps(records: list, side: str = "buy") -> dict:
    """Build the per-bucket slippage/run-up calibration from live-swap records.

    PURE + FAIL-OPEN. Considers only SUCCESSFUL records matching ``side`` (default
    'buy' — the historical contract, so the buy path is byte-identical; pass
    side='sell' for the exit-tail calibration, or use the
    ``calibrate_exit_from_live_swaps`` wrapper). Buckets each by ``liquidity_usd``
    (None/missing -> 'unknown'). Per bucket computes slip_p50 (median
    fill_vs_mid_slippage_pct), slip_p90 (90th pct, nearest-rank — the TAIL that
    matters for the exit floor), runup_p50 (median reprice_runup_pct; ~empty for
    sells), and n. Also an 'overall' bucket across all qualifying records.

    Records missing the needed numeric fields are skipped. A bucket appears only
    if at least one qualifying record landed in it. Empty/garbage -> {}.
    Never raises.
    """
    try:
        records = records or []
        # label -> {"slips": [...], "runups": [...], "n": int}
        acc: dict = {}
        overall_slips: list = []
        overall_runups: list = []
        overall_n = 0

        for r in records:
            try:
                if not isinstance(r, dict):
                    continue
                if r.get("side") != side:
                    continue
                if not r.get("success"):
                    continue
                slip = _as_float(r.get("fill_vs_mid_slippage_pct"))
                runup = _as_float(r.get("reprice_runup_pct"))
                # Need at least one usable numeric field to learn anything.
                if slip is None and runup is None:
                    continue
                label = _bucket_label(r.get("liquidity_usd"))
                b = acc.setdefault(label, {"slips": [], "runups": [], "n": 0})
                if slip is not None:
                    b["slips"].append(slip)
                    overall_slips.append(slip)
                if runup is not None:
                    b["runups"].append(runup)
                    overall_runups.append(runup)
                b["n"] += 1
                overall_n += 1
            except Exception:
                continue

        out: dict = {}
        for label, b in acc.items():
            out[label] = _bucket_stats(b["slips"], b["runups"], b["n"])
        if overall_n:
            out["overall"] = _bucket_stats(overall_slips, overall_runups, overall_n)
        return out
    except Exception:
        return {}


def calibrate_exit_from_live_swaps(records: list) -> dict:
    """Per-liquidity-bucket EXIT (sell) slippage calibration — Part 2 of the
    liquidity-conditional exit-tail design.

    Thin wrapper over ``calibrate_from_live_swaps(records, side='sell')``:
    aggregates SUCCESSFUL sell records the same way buys are, surfacing per
    thin/mid/deep/overall bucket the slip_p50, slip_p90 (the TAIL — the
    gap-through risk a human reads to set LIQ_EXIT_FLOOR_USD), and n. The buy
    path is unchanged. PURE + FAIL-OPEN: empty/garbage/no-sells -> {}, never
    raises. NOT read by the live gate at decision time (measurement only)."""
    return calibrate_from_live_swaps(records, side="sell")


def calibrated_slip_pct(calib: dict, liq_usd: Any, default: float,
                        min_n: int = 5) -> float:
    """Pick the calibrated slip (%) for a token's liquidity, fail-open to default.

    If the matching bucket has n >= min_n, return its slip_p50; else if
    'overall' has n >= min_n, return overall slip_p50; else return ``default``.
    Any error / missing calib -> ``default``."""
    try:
        if not calib:
            return default
        label = _bucket_label(liq_usd)
        b = calib.get(label)
        if isinstance(b, dict):
            n = b.get("n") or 0
            p50 = b.get("slip_p50")
            if n >= min_n and p50 is not None:
                return float(p50)
        ov = calib.get("overall")
        if isinstance(ov, dict):
            n = ov.get("n") or 0
            p50 = ov.get("slip_p50")
            if n >= min_n and p50 is not None:
                return float(p50)
        return default
    except Exception:
        return default


# ── per-tx fee calibration (priority fee) ──────────────────────────────────
# Solana charges a fixed 5000-lamport base signature fee per tx ON TOP OF the
# priority fee. The real per-tx cost ≈ (priority_fee_lamports + 5000) / 1e9 * SOL$.
BASE_FEE_LAMPORTS = 5000


def calibrated_fee_usd(records: list, default: float, sol_price_usd: Any,
                       min_n: int = 10) -> float:
    """Median realized per-tx fee (USD) from live records, fail-open to default.

    Mirrors ``calibrated_slip_pct``: learns what live ACTUALLY paid instead of the
    fixed placeholder. Per SUCCESSFUL record, fee_usd =
    (priority_fee_lamports + BASE_FEE_LAMPORTS) / 1e9 * sol_price_usd; returns the
    median across qualifying records.

    DEFAULT-SAFE: returns ``default`` until at least ``min_n`` qualifying records
    exist (thin sample -> no change), or if ``sol_price_usd`` is missing/<=0.
    PURE + FAIL-OPEN: skips records missing/garbage priority_fee_lamports; any
    error -> ``default``; never raises."""
    try:
        sp = _as_float(sol_price_usd)
        if sp is None or sp <= 0:
            return default
        fees: list = []
        for r in records or []:
            try:
                if not isinstance(r, dict):
                    continue
                if not r.get("success"):
                    continue
                lam = _as_float(r.get("priority_fee_lamports"))
                if lam is None or lam < 0:
                    continue
                fees.append((lam + BASE_FEE_LAMPORTS) / 1e9 * sp)
            except Exception:
                continue
        if len(fees) < min_n:
            return default
        return round(statistics.median(fees), 6)
    except Exception:
        return default


# module-level cache for the live-swap records the fee calibration reads, keyed
# by file mtime (same off-loop pattern as ``load_calibration``).
_FEE_REC_CACHE: dict = {}


def _load_live_swap_records_cached() -> list:
    """Read DATA_DIR/live_swaps.jsonl records, cached by file mtime. Fail-open
    to [] (missing file / any error). Blocking read — callers are off-loop /
    infrequent and the live path is gated default-off."""
    try:
        path = _live_swaps_path()
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None
        if _FEE_REC_CACHE.get("mtime") == mtime and "recs" in _FEE_REC_CACHE:
            return _FEE_REC_CACHE["recs"]
        if mtime is None:
            _FEE_REC_CACHE["mtime"] = None
            _FEE_REC_CACHE["recs"] = []
            return _FEE_REC_CACHE["recs"]
        from core.live_swap_log import read_live_swaps
        recs = read_live_swaps(path)
        _FEE_REC_CACHE["mtime"] = mtime
        _FEE_REC_CACHE["recs"] = recs
        return recs
    except Exception:
        return []


def load_fee_calibration(default: float, sol_price_usd: Any,
                         min_n: int = 10) -> float:
    """Cached convenience wrapper: read live_swaps (mtime-cached) and return the
    calibrated per-tx fee (USD), fail-open to ``default``. Used by the gated
    paper-fee path; default-off byte-identical (never called when mode=off)."""
    try:
        recs = _load_live_swap_records_cached()
        return calibrated_fee_usd(recs, default, sol_price_usd, min_n=min_n)
    except Exception:
        return default


def _ultra_cap_pct_from_env(fallback: float = 4.0) -> float:
    """Ultra slippage cap (%) from PROBE_ULTRA_SLIPPAGE_BPS / 100. Fail-open."""
    try:
        bps = os.environ.get("PROBE_ULTRA_SLIPPAGE_BPS")
        if bps is not None and str(bps).strip() != "":
            return float(bps) / 100.0
    except Exception:
        pass
    return fallback


def realistic_slip_with_cap(slip_pct: float, ultra_cap_pct: float | None = None,
                            legacy_extra_pct: float = 2.0) -> float:
    """Model the live reality that the Ultra swap REVERTS above its cap.

    Above the cap the real fill is the worse legacy/sandwich outcome, so we add
    ``legacy_extra_pct``; at/below the cap the slip is unchanged. ``ultra_cap_pct``
    defaults to PROBE_ULTRA_SLIPPAGE_BPS/100 (else 4.0). Pure + fail-open: garbage
    input is returned unchanged (never raises)."""
    try:
        cap = ultra_cap_pct if ultra_cap_pct is not None else _ultra_cap_pct_from_env()
        s = float(slip_pct)
        if s > float(cap):
            return s + float(legacy_extra_pct)
        return s
    except Exception:
        return slip_pct


# ── cached loader (off-loop-safe) ──────────────────────────────────────────
# module-level cache keyed by file mtime so we only re-read when the file
# changes. {"mtime": float|None, "calib": dict}
_CACHE: dict = {}


def _live_swaps_path() -> str:
    from core.live_swap_log import LOG_BASENAME
    return os.path.join(os.environ.get("DATA_DIR", "/data"), LOG_BASENAME)


def load_calibration() -> dict:
    """Read DATA_DIR/live_swaps.jsonl, calibrate, cache by file mtime.

    Re-reads only when the file's mtime changes. FAIL-OPEN -> {} (so
    calibrated_slip_pct then returns the caller's default). Blocking file read is
    fine — callers are off-loop or infrequent. Never raises."""
    try:
        path = _live_swaps_path()
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None  # missing file
        if _CACHE.get("mtime") == mtime and "calib" in _CACHE:
            return _CACHE["calib"]
        if mtime is None:
            _CACHE["mtime"] = None
            _CACHE["calib"] = {}
            return _CACHE["calib"]
        from core.live_swap_log import read_live_swaps
        recs = read_live_swaps(path)
        calib = calibrate_from_live_swaps(recs)
        _CACHE["mtime"] = mtime
        _CACHE["calib"] = calib
        return calib
    except Exception:
        return {}


# SELL-side twin cache (exit-booking fidelity, 2026-07-06). Separate from _CACHE
# so the buy path stays byte-identical.
_EXIT_CACHE: dict = {}


def load_exit_calibration() -> dict:
    """SELL-side twin of ``load_calibration``: read DATA_DIR/live_swaps.jsonl,
    calibrate from the REAL live SELL legs (calibrate_exit_from_live_swaps),
    cache by file mtime. Sell slip is recorded ADVERSE-POSITIVE
    (core.probe_instrument.fill_slippage_pct: (mid-fill)/mid*100), so the p50
    feeds effective_fill's sell drag directly. FAIL-OPEN -> {} (caller's
    calibrated_slip_pct then returns its default). Never raises."""
    try:
        path = _live_swaps_path()
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = None  # missing file
        if _EXIT_CACHE.get("mtime") == mtime and "calib" in _EXIT_CACHE:
            return _EXIT_CACHE["calib"]
        if mtime is None:
            _EXIT_CACHE["mtime"] = None
            _EXIT_CACHE["calib"] = {}
            return _EXIT_CACHE["calib"]
        from core.live_swap_log import read_live_swaps
        recs = read_live_swaps(path)
        calib = calibrate_exit_from_live_swaps(recs)
        _EXIT_CACHE["mtime"] = mtime
        _EXIT_CACHE["calib"] = calib
        return calib
    except Exception:
        return {}
