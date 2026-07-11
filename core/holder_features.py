"""Compute on-chain holder-concentration features from a full rugcheck report.

Single source of truth for the buy-time holder features (top-10 / top-1 wallet
concentration, dev holdings, LP imbalance). Faithful extraction of the logic that
lived inline in core/trader.py:1338-1429 so the FLEET buy path (dip_scanner
_execute_bot_buy) can stamp the same features into recorded trades — which is the
instrumentation that lets the never-green scorer eventually train on holder data.

Pure function: rc_full (rugcheck `/report` dict) -> {feature: value}. Returns {}
on any malformed input (fail-soft; never raises).
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

_LP_TAGS = {"lp", "liquidity", "liquiditypool", "pool", "amm", "bonding curve"}


def compute_holder_features(rc_full: dict) -> dict:
    out: dict = {}
    if not isinstance(rc_full, dict):
        return out
    # ---- top-holder concentration (insiders + LP excluded) -----------------
    try:
        th = rc_full.get("topHolders") or []
        if isinstance(th, list) and th:
            real = [
                h for h in th
                if isinstance(h, dict)
                and h.get("insider", False) is not True
                and (h.get("tag", "") or "").lower().strip() not in _LP_TAGS
            ]
            # topHolders `pct` is already a percent (e.g. 12.5).
            top10 = sum(float(h.get("pct", 0) or 0) for h in real[:10])
            out["top10_holder_pct"] = round(top10, 2)
            if real:
                top1 = float(real[0].get("pct", 0) or 0)
                out["top1_holder_pct"] = round(top1, 2)
                if top10 > 0:
                    out["top1_share_of_top10"] = round(top1 / top10, 3)
            # insider count among the reported top holders (own signal).
            out["topholder_insider_n"] = sum(
                1 for h in th if isinstance(h, dict) and h.get("insider", False) is True
            )
            # HOODLANA-class instrumentation (2026-07-11 forensics). HOODLANA's
            # -98% was a HIDDEN-SUPPLY DUMP: ~70% of supply sat in wallets ranked
            # 11+ (each small enough to evade the top10 check) and was dumped into
            # the pool, draining the SOL side — while lp_locked_pct read a clean
            # 100 the whole time. These stamps make that shape visible at entry
            # so the labeled-cohort grade can set thresholds on real outcomes.
            out["shoulder_11_20_pct"] = round(
                sum(float(h.get("pct", 0) or 0) for h in real[10:20]), 2)
            out["pool_topholder_pct"] = round(sum(
                float(h.get("pct", 0) or 0) for h in th
                if isinstance(h, dict)
                and (h.get("tag", "") or "").lower().strip() in _LP_TAGS
            ), 2)
            out["topholder_insider_pct"] = round(sum(
                float(h.get("pct", 0) or 0) for h in th
                if isinstance(h, dict) and h.get("insider", False) is True
            ), 2)
        _tot_h = rc_full.get("totalHolders")
        if isinstance(_tot_h, (int, float)) and not isinstance(_tot_h, bool):
            out["total_holders"] = int(_tot_h)
    except Exception as e:
        logger.debug(f"[holder_features] topHolders parse failed: {e}")
    # ---- dev / creator holdings --------------------------------------------
    try:
        creator = (rc_full.get("creator_address") or "").lower()
        if creator:
            dev_pct = None
            full_holders = rc_full.get("holders") or []
            if isinstance(full_holders, list):
                for h in full_holders:
                    if not isinstance(h, dict):
                        continue
                    addr = (h.get("account") or h.get("address") or "").lower()
                    if addr == creator:
                        dev_pct = float(h.get("percent", 0) or 0) * 100  # `percent` is 0..1
                        break
            th = rc_full.get("topHolders") or []
            if dev_pct is None and isinstance(th, list):
                for h in th:
                    if not isinstance(h, dict):
                        continue
                    addr = (h.get("address") or h.get("account") or "").lower()
                    if addr == creator:
                        dev_pct = float(h.get("pct", 0) or 0)  # `pct` is 0..100
                        break
            if dev_pct is not None:
                out["dev_holder_pct"] = round(dev_pct, 2)
    except Exception as e:
        logger.debug(f"[holder_features] creator parse failed: {e}")
    # ---- LP imbalance (dominant pool by combined USD depth) ----------------
    try:
        markets = rc_full.get("markets") or []
        if isinstance(markets, list) and markets:
            best = None; best_depth = -1.0; best_market = None
            for m in markets:
                if not isinstance(m, dict):
                    continue
                lp = m.get("lp") or {}
                if not isinstance(lp, dict):
                    continue
                b = float(lp.get("baseUSD") or 0); q = float(lp.get("quoteUSD") or 0)
                if b + q > best_depth:
                    best_depth = b + q; best = (b, q); best_market = m
            if best and best_depth > 0:
                b, q = best
                ratio = max(b, q) / max(min(b, q), 0.01)
                out["lp_imbalance_ratio"] = round(ratio, 3)
                out["lp_single_sided"] = bool(ratio > 5.0)
                out["lp_dominant_depth_usd"] = round(best_depth, 2)
            # LP LOCK / BURN on the dominant pool — the fleet-wide rug-gate signal
            # (mirrors trader.buy:1335-1400, which only the legacy path got). mintLP ==
            # system address => LP BURNED, functionally MORE secure than locked (tokens
            # can never be redeemed) => 100. Else the pool's lpLockedPct (top-level fallback).
            if best_market is not None:
                _bm_lp = best_market.get("lp") or {}
                if (best_market.get("mintLP") or "") == "11111111111111111111111111111111":
                    out["lp_locked_pct"] = 100.0
                    out["lp_burned"] = True
                else:
                    _lpl = _bm_lp.get("lpLockedPct")
                    if _lpl is None:
                        _lpl = rc_full.get("lpLockedPct")  # top-level aggregate fallback
                    if _lpl is not None:
                        out["lp_locked_pct"] = round(float(_lpl or 0), 2)
                        out["lp_burned"] = False
    except Exception as e:
        logger.debug(f"[holder_features] markets parse failed: {e}")
    # Rugcheck risk score (0..100, higher = riskier) — top-level rug-gate signal.
    try:
        _sc = rc_full.get("score_normalised")
        if _sc is None:
            _sc = rc_full.get("score")
        if _sc is not None:
            out["rugcheck_score"] = round(float(_sc), 2)
    except Exception:
        pass
    return out
