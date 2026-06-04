"""Per-wallet order-flow concentration features (2026-06-03).

THE UNTESTED AXIS. Every prior 'seller concentration' test on this fleet used a
coarse DOLLAR-share proxy (rt_max_sell_usd / rt_sells_usd) that CANNOT tell "one
real whale dumping" from "a thin order book" -- it conflates them, which is exactly
why it failed the within-token test (Wilcoxon p=0.51) in the 2026-06-03 3-thread
hunt (reference_pump_vs_bleed_3thread). The maker WALLET is preserved by the raw
io.dexscreener decoder (feeds/dexscreener_trades_format.parse_trades -> 'maker') but
is dropped downstream at feeds/trade_log_features.py:6-8, so true per-wallet
structure was never computed.

This module computes the real thing from a list of swaps: per-wallet Herfindahl
(HHI) of sell and buy volume, top-wallet share, unique-wallet count, and a
single-whale-dominance flag. Hypothesis: a token being crushed by ONE concentrated
seller (high seller HHI / high top1 seller share with few unique sellers) bleeds
out, whereas distributed selling into broad buying continues. NEAN (net_flow -0.46,
straight to stop) is the motivating case.

Pure functions (no IO) so they are unit-testable. Input = list of swap dicts with
keys: kind ('buy'/'sell'), volume_usd (float), maker (wallet str).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def _num(v) -> Optional[float]:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def wallet_concentration(swaps: List[Dict[str, Any]], side: str) -> Dict[str, Any]:
    """Per-wallet $-volume concentration for one side ('buy' or 'sell').

    Returns hhi (Herfindahl = sum of squared wallet shares, in (0,1]; 1.0 = a single
    wallet did all the volume, ->0 = many equal wallets), top1_share, top3_share,
    n_wallets, n_swaps, total_usd. Empty side -> hhi/shares None (fail-open)."""
    by_wallet: Dict[str, float] = {}
    n_swaps = 0
    for s in swaps:
        if s.get("kind") != side:
            continue
        v = _num(s.get("volume_usd"))
        w = s.get("maker") or ""
        if v is None or v <= 0 or not w:
            continue
        by_wallet[w] = by_wallet.get(w, 0.0) + v
        n_swaps += 1
    total = sum(by_wallet.values())
    if total <= 0 or not by_wallet:
        return {"hhi": None, "top1_share": None, "top3_share": None,
                "n_wallets": 0, "n_swaps": n_swaps, "total_usd": round(total, 2)}
    shares = sorted((v / total for v in by_wallet.values()), reverse=True)
    hhi = sum(x * x for x in shares)
    return {
        "hhi": round(hhi, 4),
        "top1_share": round(shares[0], 4),
        "top3_share": round(sum(shares[:3]), 4),
        "n_wallets": len(by_wallet),
        "n_swaps": n_swaps,
        "total_usd": round(total, 2),
    }


def wallet_flow_features(swaps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Full per-wallet flow feature vector from a swap list. Combines sell-side and
    buy-side concentration plus derived contrasts. All keys present (None when a side
    is empty) so downstream extraction is uniform."""
    sell = wallet_concentration(swaps, "sell")
    buy = wallet_concentration(swaps, "buy")

    def _d(a, b):
        return round(a - b, 4) if (a is not None and b is not None) else None

    sh, bh = sell["hhi"], buy["hhi"]
    # single-whale-seller: one wallet owns most sell $ AND few unique sellers
    swd = (sell["top1_share"] is not None and sell["top1_share"] >= 0.5
           and sell["n_wallets"] <= 5)
    return {
        # sell side (the dump-toxicity axis)
        "seller_hhi": sh,
        "seller_top1_share": sell["top1_share"],
        "seller_top3_share": sell["top3_share"],
        "n_sellers": sell["n_wallets"],
        "sell_usd": sell["total_usd"],
        # buy side (demand breadth)
        "buyer_hhi": bh,
        "buyer_top1_share": buy["top1_share"],
        "n_buyers": buy["n_wallets"],
        "buy_usd": buy["total_usd"],
        # derived contrasts
        "hhi_sell_minus_buy": _d(sh, bh),            # >0 = selling more concentrated than buying
        "single_whale_seller": bool(swd),            # one dominant seller, thin seller set
        "seller_buyer_wallet_ratio": (round(sell["n_wallets"] / buy["n_wallets"], 3)
                                      if buy["n_wallets"] else None),
        "n_swaps": sell["n_swaps"] + buy["n_swaps"],
    }
