"""IN-BOT hourly regime-pattern miner (2026-06-14, AxiS: "autonomously find entry
patterns every hour, on the dashboard").

DETERMINISTIC — no LLM, no Anthropic API, zero credits. Runs in-process on the scan
loop (~hourly), reads the fleet's OWN closed trades from the shared TradeStore, and
computes which entry features SEPARATE winners from losers RIGHT NOW + the current
regime. Writes _hourly_patterns_latest.json (+ a rolling _hourly_patterns.jsonl) for
the dashboard /api/regime-patterns tab. This is the always-on, credit-free replacement
for the (API-credit-needing) scheduled remote agent — entry patterns rotate with the
regime ([[reference_regime_entry_hunt_2026_06_14]]), so the fleet always has a fresh read.
"""
from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_DATA_DIR = os.environ.get("DATA_DIR", ".")
_OUT_LATEST = os.path.join(_DATA_DIR, "_hourly_patterns_latest.json")
_OUT_LOG = os.path.join(_DATA_DIR, "_hourly_patterns.jsonl")

MINE_INTERVAL_SECS = float(os.environ.get("REGIME_MINER_INTERVAL_SECS", "3600"))  # hourly
WINDOW_SECS = float(os.environ.get("REGIME_MINER_WINDOW_SECS", str(3 * 3600)))    # last 3h
MIN_SIDE_N = 3  # need >=3 winners AND >=3 losers to report a feature separator

_last_run = 0.0

# Entry-state features to test for winner-vs-loser separation (regime-entry hunt set).
_FEATURES = [
    "shape_90m_drawdown_from_max_pct",  # dip off 90m high (the dip-vs-momentum axis)
    "pc_h1", "pc_h24", "entry_age_hours", "mcap", "fdv", "liquidity_usd",
    "bs_m5", "net_flow_60s", "vol_m5", "1m_volume_spike",
]


def _median(vals):
    s = sorted(vals)
    n = len(s)
    if n == 0:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def closed_with_meta(trades, since_iso):
    """Join each entry_meta-bearing BUY to its SELL(s) -> [{em, pnl, token, time, bot_id}].
    Mirrors scripts/entry_meta_miner.py's buy->sell windowing (next-buy bounds the sells)."""
    buys = [t for t in trades if t.get("type") == "buy" and (t.get("entry_meta") or {})]
    sells = [t for t in trades if t.get("type") == "sell"]
    sell_idx = defaultdict(list)
    for s in sells:
        sell_idx[(s.get("address"), s.get("pair_address"))].append(s)
    buys_by_key = defaultdict(list)
    for b in buys:
        buys_by_key[(b.get("address"), b.get("pair_address"))].append(b.get("time") or "")
    for k in buys_by_key:
        buys_by_key[k].sort()
    out = []
    for b in buys:
        bt = b.get("time") or ""
        if bt < since_iso:
            continue
        addr, pair = b.get("address"), b.get("pair_address")
        next_bt = "9999"
        for c in buys_by_key.get((addr, pair), []):
            if c > bt:
                next_bt = c
                break
        rel = [s for s in sell_idx.get((addr, pair), [])
               if bt < (s.get("time") or "") < next_bt]
        if not rel:
            continue
        pnl = sum(float(s.get("pnl") or 0) for s in rel)
        out.append({"em": b.get("entry_meta") or {}, "pnl": pnl,
                    "token": b.get("token"), "time": bt, "bot_id": b.get("bot_id")})
    return out


def classify_regime(closed):
    """Regime from the WINNERS' median entry dip-depth: momentum-up (enter near highs),
    mid-dip, or deep-dip (enter deep). Unknown if too few winners."""
    dds = [c["em"].get("shape_90m_drawdown_from_max_pct") for c in closed if c["pnl"] > 0]
    dds = [d for d in dds if isinstance(d, (int, float))]
    md = _median(dds)
    if md is None:
        return "unknown"
    if md > -8:
        return "momentum-up"
    if md <= -20:
        return "deep-dip"
    return "mid-dip"


def feature_separators(closed):
    """Per feature: winner median vs loser median (+ n each). The features where winners
    clearly differ from losers ARE the current entry gates."""
    wins = [c for c in closed if c["pnl"] > 0]
    losers = [c for c in closed if c["pnl"] <= 0]
    sep = {}
    for f in _FEATURES:
        wv = [c["em"].get(f) for c in wins if isinstance(c["em"].get(f), (int, float))]
        lv = [c["em"].get(f) for c in losers if isinstance(c["em"].get(f), (int, float))]
        if len(wv) >= MIN_SIDE_N and len(lv) >= MIN_SIDE_N:
            mw, ml = _median(wv), _median(lv)
            denom = abs(mw) + abs(ml) + 1e-9
            sep[f] = {"win_med": round(mw, 3), "loss_med": round(ml, 3),
                      "n_win": len(wv), "n_loss": len(lv),
                      "rel_gap": round((mw - ml) / denom, 3)}  # signed, scale-normalized
    return sep


def build_snapshot(trades, now_dt=None):
    """Pure: trades -> the hourly pattern snapshot dict (no I/O; unit-testable)."""
    now_dt = now_dt or datetime.now(timezone.utc)
    since_iso = (now_dt - timedelta(seconds=WINDOW_SECS)).isoformat()
    closed = closed_with_meta(trades, since_iso)
    n = len(closed)
    wins = sum(1 for c in closed if c["pnl"] > 0)
    sep = feature_separators(closed)
    # rank the separators by |rel_gap| (the strongest winner-vs-loser splits = the gates)
    top = sorted(sep.items(), key=lambda kv: -abs(kv[1]["rel_gap"]))[:6]
    return {
        "ts": now_dt.isoformat(),
        "window_h": round(WINDOW_SECS / 3600, 1),
        "n_closed": n,
        "wins": wins,
        "wr": round(wins / n, 3) if n else None,
        "regime": classify_regime(closed),
        "top_separators": [{"feature": k, **v} for k, v in top],
        "all_separators": sep,
    }


def run(scanner, now=None):
    """Hourly hook from the scan loop. Never raises."""
    global _last_run
    try:
        now = now or time.time()
        if now - _last_run < MINE_INTERVAL_SECS:
            return
        _last_run = now
        store = getattr(scanner, "trade_store", None)
        if store is None:
            return
        snap = build_snapshot(store.load_trades())
        tmp = _OUT_LATEST + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(snap, fh, indent=1)
        os.replace(tmp, _OUT_LATEST)
        try:
            with open(_OUT_LOG, "a") as fh:
                fh.write(json.dumps({k: snap[k] for k in
                                     ("ts", "regime", "n_closed", "wr", "top_separators")}) + "\n")
        except Exception:
            pass
        logger.info("[RegimeMiner] %d closes (WR %s) regime=%s top=%s",
                    snap["n_closed"], snap["wr"], snap["regime"],
                    [s["feature"] for s in snap["top_separators"]])
    except Exception as e:
        logger.debug("[RegimeMiner] error: %s", e)
