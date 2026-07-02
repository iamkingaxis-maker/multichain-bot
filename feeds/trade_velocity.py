"""
Trade velocity / burst detection.

Memecoin-specific. On low-cap tokens, the leading indicator of a real
pump (vs a fake bounce on dead volume) is a BURST of trades clustered
in time. 30 buys in 60 seconds = real demand. 30 buys spread over an
hour = noise.

Input is the recent_trades list already pulled by dip_scanner via
gt_client.fetch_recent_trades. Each trade has 'kind' ('buy'/'sell'),
'volume_usd', and 'ts' (ISO timestamp).

Outputs:
  buys_per_min_recent          buys/min over last 60s
  sells_per_min_recent         sells/min over last 60s
  buy_burst_30s_count          # of buys in last 30 seconds
  sell_burst_30s_count         # of sells in last 30 seconds
  max_burst_size               largest count of trades in any 30s window
  buy_pressure_60s             buys / (buys + sells) in last 60s
  trade_density_30s_vs_5m      ratio of trade rate (last 30s) to (last 5m)
                               > 2.0 = clear acceleration
  velocity_verdict             SURGE_BUY / SURGE_SELL / NORMAL / QUIET
"""
from __future__ import annotations

from typing import List, Dict, Any
from datetime import datetime, timezone


def _parse_ts(s: str) -> float:
    """Parse ISO ts to epoch seconds. Tolerant of 'Z' suffix."""
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def analyze(recent_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Compute trade velocity / burst features from the last ~30 trades.

    recent_trades is the dip_scanner's `recent_trades` list (sorted
    most-recent-first per gt_client.fetch_recent_trades).
    """
    blank = {
        "buys_per_min_recent": 0.0,
        "sells_per_min_recent": 0.0,
        "buy_burst_30s_count": 0,
        "sell_burst_30s_count": 0,
        "max_burst_size_30s": 0,
        # 2026-07-02 FIX (missing-data-read-as-zero bug-class sweep): an empty
        # trade list here is overwhelmingly a FETCH FAILURE, and the fabricated
        # neutral 0.5 was consumed by the trigger-state gates (informed_cluster
        # <=0.40 / swing_structure_rsi >=0.57) as a REAL flow measurement —
        # dropping triggers in BOTH directions on a data gap. None -> the
        # gates' isinstance guards fail open ("na"). The other blank keys keep
        # their historical defaults deliberately: the corpse gates now guard on
        # real tape at their call sites, filter_high_activity_fomo blocks only
        # on HIGH bpm (0 = pass), and filter_fake_bounce's calm-tape carve-out
        # RESCUES (allows) on spm=0 — flipping those to None would make missing
        # data MORE blocking, the opposite of this bug class's fix direction.
        "buy_pressure_60s": None,
        "trade_density_30s_vs_5m": 0.0,
        "velocity_verdict": "QUIET",
    }
    if not recent_trades:
        return blank

    # Parse timestamps
    enriched = []
    for t in recent_trades:
        ts = _parse_ts(t.get("ts", ""))
        if ts > 0:
            enriched.append((ts, t.get("kind", ""), float(t.get("volume_usd", 0) or 0)))
    if not enriched:
        return blank
    enriched.sort()  # oldest first

    now = datetime.now(timezone.utc).timestamp()

    # Windows
    last_30s = [(ts, k, v) for ts, k, v in enriched if now - ts <= 30]
    last_60s = [(ts, k, v) for ts, k, v in enriched if now - ts <= 60]
    last_5m = [(ts, k, v) for ts, k, v in enriched if now - ts <= 300]

    buys_60 = sum(1 for _, k, _ in last_60s if k == "buy")
    sells_60 = sum(1 for _, k, _ in last_60s if k == "sell")
    buys_30 = sum(1 for _, k, _ in last_30s if k == "buy")
    sells_30 = sum(1 for _, k, _ in last_30s if k == "sell")

    buys_per_min = buys_60  # already per 60s
    sells_per_min = sells_60

    # Buy pressure
    total_60 = buys_60 + sells_60
    bp_60 = buys_60 / total_60 if total_60 > 0 else 0.5

    # Density: trades/sec in last 30s vs last 5m
    rate_30s = len(last_30s) / 30.0
    rate_5m = len(last_5m) / 300.0 if last_5m else 0.0
    density = (rate_30s / rate_5m) if rate_5m > 0 else 0.0

    # Sliding-window max burst over last 5 minutes
    max_burst = 0
    if last_5m:
        for i, (ts_i, _, _) in enumerate(last_5m):
            window_count = sum(1 for ts_j, _, _ in last_5m if 0 <= ts_j - ts_i <= 30)
            if window_count > max_burst:
                max_burst = window_count

    # Verdict
    verdict = "NORMAL"
    if buys_30 >= 6 and bp_60 >= 0.65 and density >= 1.5:
        verdict = "SURGE_BUY"
    elif sells_30 >= 6 and bp_60 <= 0.35 and density >= 1.5:
        verdict = "SURGE_SELL"
    elif rate_30s == 0 and rate_5m < 0.05:  # < 1 trade in 20 sec sustained
        verdict = "QUIET"

    return {
        "buys_per_min_recent": round(buys_per_min, 2),
        "sells_per_min_recent": round(sells_per_min, 2),
        "buy_burst_30s_count": buys_30,
        "sell_burst_30s_count": sells_30,
        "max_burst_size_30s": max_burst,
        "buy_pressure_60s": round(bp_60, 3),
        "trade_density_30s_vs_5m": round(density, 3),
        "velocity_verdict": verdict,
    }
