"""
Trade-log derived features for dip_scanner entry_meta.

Inputs: the `recent_trades` list already pulled by dip_scanner via
DexScreener (or GT fallback). Each trade dict has keys: kind ('buy'/
'sell'), volume_usd, ts. Note: maker_address is NOT currently in the
DexScreener trade dict (we deliberately stripped it to keep the parse
cheap), so per-wallet metrics need a maker-address-aware client.

This module computes:

  Order-size distribution (memecoin-specific):
    median_buy_size_usd, p90_buy_size_usd, mean_buy_size_usd
    n_large_buys_500_30m   (count of buys ≥ $500)
    n_large_buys_2000_30m  (count of buys ≥ $2000)
    large_buyer_volume_pct (volume share of buys ≥ $500)

    Few large buys = whale conviction; many small buys = retail FOMO.
    On $1M-$100M FDV memecoins, $2k+ single buys are notable.

  Order-flow asymmetry:
    buy_sell_volume_imbalance       buy_usd / (buy_usd + sell_usd)
    largest_buy_to_largest_sell     max(buy) / max(sell), capped 10x
    n_consecutive_buys_at_end       buys in a row at the most-recent end

These complement (don't duplicate) the velocity_verdict module which
counts trades but ignores size distribution.
"""
from __future__ import annotations

from typing import Any, Dict, List


def _empty() -> Dict[str, Any]:
    return {
        # Order-size distribution
        # 2026-07-02 FIX (missing-data-read-as-zero bug-class sweep): the size/
        # ratio keys are None (UNKNOWN), not 0/0.5, in the empty/no-buys case.
        # An empty trade log here is overwhelmingly a FETCH FAILURE (same class
        # as the 07-01 maker-key fix below), and a fabricated 0 defeated the
        # None-safe fail-open gates downstream: full_thesis_cohort_eval read
        # median=0.0 as "buyer$0<34.3 -> BLOCK" (FULL_THESIS_COHORT_MODE=enforce
        # darked entries on a data gap), filter_premium_required read p90=0.0 as
        # premium-known-and-failing -> BLOCK, and the trigger-state gates read
        # buy_sell_volume_imbalance=0.5 as a real neutral flow measurement.
        # None -> every consumer's isinstance/is-not-None guard fails open.
        # Counts/flags (n_large_*, n_consecutive, whale_*) keep their historical
        # defaults: no blocking-direction consumer exists for them.
        "median_buy_size_usd": None,
        "p90_buy_size_usd": None,
        "mean_buy_size_usd": None,
        "n_large_buys_500_30m": 0,
        "n_large_buys_2000_30m": 0,
        "large_buyer_volume_pct": None,
        # Buy/sell asymmetry
        "buy_sell_volume_imbalance": None,
        "largest_buy_to_largest_sell": None,
        "n_consecutive_buys_at_end": 0,
        # Wash-detection / buyer-uniqueness (requires maker address — only
        # populated when DexScreener trade-log was used; GT fallback strips
        # maker so these stay at defaults.)
        # 2026-07-01 FIX (4-agent diagnosis P5): these five maker-derived keys
        # are None (UNKNOWN), not 0, in the empty/no-buys case. An empty trade
        # log here is overwhelmingly a FETCH FAILURE (io.dexscreener timeout /
        # slug-miss / circuit-open -> [] -> this path) or a tiny all-sell window
        # at the flush moment — NOT proof of zero buyers. Returning 0 made the
        # fleet-wide rug gate (unique_buyers_n==0) and entry demand-floors
        # fail-CLOSED on a data gap (~20% of surviving signal tokens blocked).
        # None -> gates skip (fail-open); structural rug shields (lp_single_
        # sided, liquidity, age, rug_bundle-when-known) still apply.
        "unique_buyers_n": None,
        "unique_buyer_ratio": None,
        "top5_buyer_volume_pct": None,
        "wash_suspected": None,
        # Buyer-profile signals
        "n_recurring_buyers_3plus": None,
        "whale_buy_present_2k": False,
        # None on missing tape (read-as-zero bug-class rule, 2026-07-03): a
        # fabricated $0 max-print would let a future absorption print-gate
        # block on failed fetches. Boolean whale_buy_present_2k stays False
        # (absence-of-evidence is the correct trigger semantic there).
        "whale_max_buy_usd": None,
    }


def analyze(recent_trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not recent_trades:
        return _empty()

    buys = [t for t in recent_trades if t.get("kind") == "buy"]
    sells = [t for t in recent_trades if t.get("kind") == "sell"]
    buys_v = [float(t.get("volume_usd") or 0) for t in buys]
    sells_v = [float(t.get("volume_usd") or 0) for t in sells]

    if not buys_v:
        return _empty()

    # Order-size distribution on buys
    s_sorted = sorted(buys_v)
    n = len(s_sorted)
    median = s_sorted[n // 2]
    p90 = s_sorted[max(0, int(n * 0.9) - 1)]
    mean = sum(s_sorted) / n
    n_large_500 = sum(1 for v in buys_v if v >= 500.0)
    n_large_2000 = sum(1 for v in buys_v if v >= 2000.0)
    large_vol = sum(v for v in buys_v if v >= 500.0)
    total_buy_vol = sum(buys_v)
    large_pct = (large_vol / total_buy_vol) if total_buy_vol > 0 else 0.0

    # Buy/sell asymmetry
    total_vol = total_buy_vol + sum(sells_v)
    bs_imb = (total_buy_vol / total_vol) if total_vol > 0 else 0.5
    max_buy = max(buys_v) if buys_v else 0.0
    max_sell = max(sells_v) if sells_v else 0.0
    big_ratio = (max_buy / max_sell) if max_sell > 0 else (10.0 if max_buy > 0 else 0.0)
    big_ratio = min(big_ratio, 10.0)  # cap at 10x

    # Consecutive buys at the end (most-recent-first sort assumed)
    n_consec = 0
    for t in recent_trades:
        if t.get("kind") == "buy":
            n_consec += 1
        else:
            break

    # Buyer uniqueness / wash detection (from maker addresses, when present)
    unique_n = 0
    unique_ratio = 0.0
    top5_pct = 0.0
    wash = False
    n_recur = 0
    whale_present = False
    whale_max = max_buy
    makers_with_v = [
        (str(t.get("maker", "")), float(t.get("volume_usd") or 0))
        for t in buys if t.get("maker")
    ]
    if makers_with_v:
        per_maker_vol: Dict[str, float] = {}
        per_maker_count: Dict[str, int] = {}
        for m, v in makers_with_v:
            per_maker_vol[m] = per_maker_vol.get(m, 0) + v
            per_maker_count[m] = per_maker_count.get(m, 0) + 1
        unique_n = len(per_maker_vol)
        unique_ratio = unique_n / len(makers_with_v)
        # Top-5 buyer concentration by volume
        sorted_vols = sorted(per_maker_vol.values(), reverse=True)
        top5_vol = sum(sorted_vols[:5])
        total_makers_vol = sum(per_maker_vol.values())
        top5_pct = (top5_vol / total_makers_vol) if total_makers_vol > 0 else 0.0
        # Wash heuristic: ≥10 buys, ≤4 unique wallets, top-3 hold >70% of vol.
        # On low-cap memecoins this is the smoking-gun signature.
        if (
            len(makers_with_v) >= 10
            and unique_n <= 4
            and (sum(sorted_vols[:3]) / total_makers_vol if total_makers_vol > 0 else 0.0) >= 0.70
        ):
            wash = True
        n_recur = sum(1 for c in per_maker_count.values() if c >= 3)
        whale_present = max_buy >= 2000.0

    # 2026-07-01 FIX: maker-derived buyer features are UNKNOWN (None), not 0,
    # when the trade log carried buys but NO maker field (the GT-fallback path
    # strips maker; io.dexscreener slug-miss / circuit-open -> GT). Returning 0
    # made the rug gate (unique_buyers_n==0) + entry demand-floors fail-CLOSED on
    # a data GAP, darkening ~95% of the fleet when DexScreener trade-log coverage
    # dropped. None makes those gates fail-OPEN as documented (block only on a
    # REAL measured 0). Genuine screening is unchanged when maker data IS present;
    # the other rug shields (rug_bundle, lp_single_sided, liquidity, age) still
    # apply to unknown-maker tokens. (buys exist here — we passed the buys_v guard.)
    _maker_known = bool(makers_with_v)
    return {
        "median_buy_size_usd": round(median, 2),
        "p90_buy_size_usd": round(p90, 2),
        "mean_buy_size_usd": round(mean, 2),
        "n_large_buys_500_30m": n_large_500,
        "n_large_buys_2000_30m": n_large_2000,
        "large_buyer_volume_pct": round(large_pct, 3),
        "buy_sell_volume_imbalance": round(bs_imb, 3),
        "largest_buy_to_largest_sell": round(big_ratio, 2),
        "n_consecutive_buys_at_end": n_consec,
        "unique_buyers_n": unique_n if _maker_known else None,
        "unique_buyer_ratio": round(unique_ratio, 3) if _maker_known else None,
        "top5_buyer_volume_pct": round(top5_pct, 3) if _maker_known else None,
        "wash_suspected": wash if _maker_known else None,
        "n_recurring_buyers_3plus": n_recur if _maker_known else None,
        "whale_buy_present_2k": whale_present,
        "whale_max_buy_usd": round(whale_max, 2),
    }
