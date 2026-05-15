"""Broader-universe chart dataset miner.

Pulls 1000+ tokens from various sources (trending, dexscreener boosts,
dexscreener search, token profiles), fetches their candle history, slides a
60-min window across the history, renders chart images at each window step,
and labels each with synthetic forward outcome.

Output: .cnn_dataset/v2_broad/<token_addr>/<ts_iso>.npy + .json

Usage:
    python scripts/mine_chart_universe.py --tokens 1000 --window-step-min 30
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import requests

from feeds.candle_utils import Candle
from feeds.chart_data import assemble_chart_data
from feeds.chart_image_renderer import render_chart_image
from feeds.dexscreener_client import DexScreenerClient
from feeds.gecko_ohlcv import GeckoTerminalClient
from models.chart_cnn import PATTERN_CLASSES

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

# ── CLI ────────────────────────────────────────────────────────────────────

def parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tokens", type=int, default=500,
                    help="Max tokens to mine (default 500)")
    ap.add_argument("--window-step-min", type=int, default=30,
                    help="Minutes between window snapshots within one token")
    ap.add_argument("--max-windows-per-token", type=int, default=24,
                    help="Cap snapshots per token (24 = 12h of history at 30min steps)")
    ap.add_argument("--forward-min", type=int, default=30,
                    help="Forward outcome window in minutes")
    ap.add_argument("--rate-limit-s", type=float, default=1.5,
                    help="Sleep between GT calls (per token)")
    ap.add_argument("--out-dir", default=".cnn_dataset/v2_broad")
    return ap.parse_args()


# ── Token universe collection ──────────────────────────────────────────────

_DS_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
_SEARCH_TERMS = ["sol", "bonk", "wif", "cat", "dog", "meme", "pepe", "ai", "baby", "pump"]


def _safe_get(url: str, timeout: int = 10) -> dict | list | None:
    try:
        r = requests.get(url, headers=_DS_HEADERS, timeout=timeout)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def _fetch_gt_trending_sync(pages: int = 5) -> list[dict]:
    """Pull GT trending Solana pools synchronously. Returns raw GT pool dicts."""
    out: list[dict] = []
    for page in range(1, pages + 1):
        try:
            url = (
                f"https://api.geckoterminal.com/api/v2/networks/solana/trending_pools"
                f"?page={page}&include=base_token"
            )
            r = requests.get(url, headers=_DS_HEADERS, timeout=12)
            if r.status_code != 200:
                break
            data = r.json()
            items = data.get("data") or []
            included = {i["id"]: i for i in (data.get("included") or [])}
            for item in items:
                try:
                    attrs = item.get("attributes") or {}
                    rels = item.get("relationships") or {}
                    base_ref = (rels.get("base_token") or {}).get("data") or {}
                    base_id = base_ref.get("id") or ""
                    base_info = included.get(base_id) or {}
                    bi_attrs = base_info.get("attributes") or {}
                    base_addr = bi_attrs.get("address") or (
                        base_id.split("_", 1)[1] if "_" in base_id else ""
                    )
                    base_sym = bi_attrs.get("symbol") or attrs.get("name", "?").split(" /")[0]
                    pair_addr = attrs.get("address") or ""
                    if base_addr and pair_addr and not base_addr.startswith("0x"):
                        out.append({
                            "addr": base_addr,
                            "pair": pair_addr,
                            "symbol": base_sym,
                        })
                except Exception:
                    continue
            if len(items) < 20:
                break  # last page
        except Exception as e:
            print(f"  GT trending page {page} err: {e}")
            break
        time.sleep(2.0)  # respect 25 req/min
    return out


def _fetch_ds_boosts_sync() -> list[dict]:
    """DexScreener token-boosts/top — returns address-only list."""
    data = _safe_get("https://api.dexscreener.com/token-boosts/top/v1")
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("pairs", [])
    addrs = [
        it.get("tokenAddress") or it.get("address")
        for it in (items or [])
        if it.get("chainId") == "solana" and (it.get("tokenAddress") or it.get("address"))
    ]
    return [{"addr": a} for a in addrs if a]


def _fetch_ds_profiles_sync() -> list[dict]:
    """DexScreener token-profiles/latest — returns address-only list."""
    data = _safe_get("https://api.dexscreener.com/token-profiles/latest/v1")
    if not data:
        return []
    items = data if isinstance(data, list) else data.get("pairs", [])
    addrs = [
        it.get("tokenAddress") or it.get("address")
        for it in (items or [])
        if it.get("chainId") == "solana" and (it.get("tokenAddress") or it.get("address"))
    ]
    return [{"addr": a} for a in addrs if a]


def _fetch_ds_search_sync() -> list[dict]:
    """DexScreener keyword search — returns address-only list."""
    addrs: set[str] = set()
    for kw in _SEARCH_TERMS:
        data = _safe_get(
            f"https://api.dexscreener.com/latest/dex/search?q={kw}&chainId=solana",
            timeout=8,
        )
        for p in (data or {}).get("pairs", []) or []:
            if p.get("chainId") != "solana":
                continue
            ta = (p.get("baseToken") or {}).get("address", "")
            if ta:
                addrs.add(ta)
        time.sleep(0.4)
    return [{"addr": a} for a in addrs]


def _enrich_addrs_with_pairs(addrs: list[str]) -> list[dict]:
    """Batch-enrich token addresses with pair addresses via DS /tokens."""
    enriched: list[dict] = []
    addrs = list(dict.fromkeys(a for a in addrs if a))  # dedupe
    for i in range(0, len(addrs), 30):
        batch = addrs[i:i + 30]
        url = f"https://api.dexscreener.com/latest/dex/tokens/{','.join(batch)}"
        data = _safe_get(url)
        if not data:
            time.sleep(0.5)
            continue
        # Pick highest-liquidity pair per base address
        best: dict[str, dict] = {}
        for p in data.get("pairs") or []:
            if p.get("chainId") != "solana":
                continue
            ta = (p.get("baseToken") or {}).get("address", "")
            if not ta:
                continue
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            cur = best.get(ta)
            if cur is None or liq > float((cur.get("liquidity") or {}).get("usd") or 0):
                best[ta] = p
        for p in best.values():
            base = p.get("baseToken") or {}
            addr = base.get("address", "")
            pair = p.get("pairAddress", "")
            sym = base.get("symbol") or base.get("name") or "?"
            if addr and pair:
                enriched.append({"addr": addr, "pair": pair, "symbol": sym})
        time.sleep(0.3)
    return enriched


def gather_token_universe(max_tokens: int) -> list[dict]:
    """Returns list of {addr, pair, symbol} dicts, deduplicated by token addr."""
    universe: dict[str, dict] = {}  # addr.lower() -> token dict

    # Source 1: GT trending — returns fully-enriched dicts directly
    print("  Fetching GT trending pools...")
    gt_pools = _fetch_gt_trending_sync(pages=5)
    for t in gt_pools:
        key = t["addr"].lower()
        if key not in universe and t.get("pair"):
            universe[key] = t
    print(f"  GT trending: {len(gt_pools)} -> {len(universe)} unique so far")

    # Sources 2-4: DS address sources -> need enrichment with pair addresses
    print("  Fetching DS boosts...")
    boost_addrs_raw = _fetch_ds_boosts_sync()
    boost_addrs = [t["addr"] for t in boost_addrs_raw if t["addr"].lower() not in universe]
    print(f"  DS boosts: {len(boost_addrs)} new addrs to enrich")

    print("  Fetching DS profiles...")
    prof_addrs_raw = _fetch_ds_profiles_sync()
    prof_addrs = [t["addr"] for t in prof_addrs_raw if t["addr"].lower() not in universe]
    print(f"  DS profiles: {len(prof_addrs)} new addrs to enrich")

    print("  Fetching DS search...")
    search_addrs_raw = _fetch_ds_search_sync()
    search_addrs = [t["addr"] for t in search_addrs_raw if t["addr"].lower() not in universe]
    print(f"  DS search: {len(search_addrs)} new addrs to enrich")

    all_new_addrs = list(dict.fromkeys(boost_addrs + prof_addrs + search_addrs))
    if all_new_addrs:
        print(f"  Enriching {len(all_new_addrs)} new DS addrs...")
        enriched = _enrich_addrs_with_pairs(all_new_addrs)
        for t in enriched:
            key = t["addr"].lower()
            if key not in universe:
                universe[key] = t
        print(f"  After DS enrichment: {len(universe)} unique tokens total")

    result = list(universe.values())[:max_tokens]
    print(f"Token universe: {len(result)} tokens (capped at {max_tokens})")
    return result


# ── Synthetic pattern labeler ──────────────────────────────────────────────

def _synthetic_pattern_label(c1: list[Candle], c5: list[Candle],
                              c15: list[Candle], cur_price: float) -> str:
    """Apply heuristic versions of our triggers to label this chart moment.

    Returns the first matching trigger name in PATTERN_CLASSES priority order,
    or 'default' if none match. These are CHART SIGNATURE approximations —
    they produce labels that are visually correlated with each pattern even if
    they don't perfectly match the production trigger predicates.
    """
    if not c1 or not c5 or not c15 or cur_price <= 0:
        return "default"

    last30 = c1[-30:] if len(c1) >= 30 else c1
    if not last30:
        return "default"

    # 1m short-window helpers
    last_5 = last30[-5:]
    consec_red = sum(1 for b in last_5 if b.close < b.open)
    last_close_pct = (
        ((last30[-1].close - last30[-2].close) / last30[-2].close * 100)
        if len(last30) >= 2 and last30[-2].close > 0
        else 0.0
    )

    # Volume ratio: recent 5 vs prior 10
    recent_vols = [b.volume for b in last30[-5:]]
    older_vols = [b.volume for b in last30[-15:-5]] if len(last30) >= 15 else []
    avg_recent = sum(recent_vols) / len(recent_vols) if recent_vols else 0.0
    avg_older = sum(older_vols) / len(older_vols) if older_vols else 1e-9
    vol_spike = avg_recent / max(avg_older, 1e-9)

    # 5m window helpers
    last_5m_5 = c5[-5:] if len(c5) >= 5 else c5
    if not last_5m_5:
        return "default"
    pc_5m_5 = (
        ((last_5m_5[-1].close - last_5m_5[0].open) / last_5m_5[0].open * 100)
        if last_5m_5[0].open > 0
        else 0.0
    )
    last_5m = c5[-1]
    prev_5m = c5[-2] if len(c5) >= 2 else last_5m

    # 15m peak-below calculation
    last_15m_10 = c15[-10:] if len(c15) >= 10 else c15
    peak_15m = max((b.high for b in last_15m_10), default=cur_price)
    pct_below_peak = ((peak_15m / cur_price) - 1) * 100 if cur_price > 0 else 0.0

    # ── Priority-ordered heuristics ────────────────────────────────────────
    # Order matches PATTERN_CLASSES priority: more distinctive/rare first.

    # 1s_capit_reversal: 3+ consecutive reds then strong green reversal
    if consec_red >= 3 and last_close_pct > 1.0 and vol_spike > 0.6:
        return "1s_capit_reversal"

    # sweep_rejection: 5m candle made a new low vs prior 5m, but closed
    # bullishly near its high (pinbar / hammer)
    if (last_5m.low < prev_5m.low
            and last_5m.close > last_5m.open
            and (last_5m.high - last_5m.low) > 0):
        rng = last_5m.high - last_5m.low
        close_pos = (last_5m.close - last_5m.low) / rng
        if close_pos > 0.65:
            return "sweep_rejection"

    # extreme_sweep_1m: very long lower wick on last 1m bar (wick >= 3× body)
    last_1m = last30[-1]
    body_1m = abs(last_1m.close - last_1m.open)
    lower_wick_1m = min(last_1m.open, last_1m.close) - last_1m.low
    if lower_wick_1m > 0 and body_1m > 0 and lower_wick_1m >= 3 * body_1m:
        return "extreme_sweep_1m"

    # demand_bottom_compound: big-wick sweep + immediate follow-through green
    if (lower_wick_1m > body_1m * 2
            and last_close_pct > 0.5
            and len(last30) >= 2
            and last30[-2].close < last30[-2].open):
        return "demand_bottom_compound"

    # patient_bottom: sideways compression below recent peak (5-25% below peak)
    if 5.0 <= pct_below_peak < 25.0:
        ranges_5m = [b.high - b.low for b in last_5m_5]
        avg_range_5m = sum(ranges_5m) / len(ranges_5m) if ranges_5m else 0.0
        if avg_range_5m / max(cur_price, 1e-9) < 0.015:
            return "patient_bottom"

    # clean_break: 5m bar closes strongly green above prior 5m range high
    prior_5m_high = max(b.high for b in c5[-6:-1]) if len(c5) >= 6 else 0.0
    if pc_5m_5 > 1.5 and last_5m.close > prior_5m_high:
        return "clean_break"

    # pullback_in_uptrend: 15m TF trending up, recent 5m TF pulling back
    if len(c15) >= 5:
        pc_15m_5 = (
            ((c15[-1].close - c15[-5].open) / c15[-5].open * 100)
            if c15[-5].open > 0
            else 0.0
        )
        if pc_15m_5 > 5.0 and pc_5m_5 < -1.0:
            return "pullback_in_uptrend"

    # controlled_greens_5m: 4+ of last 5 5m candles are green (steady uptrend)
    if len(c5) >= 5:
        recent_5m_green = sum(1 for b in c5[-5:] if b.close >= b.open)
        if recent_5m_green >= 4:
            return "controlled_greens_5m"

    # net_flow_5m_demand: recent 5m volume dominant on buy side (vol spike + green)
    if vol_spike > 1.5 and last_close_pct > 0.3 and last_5m.close >= last_5m.open:
        return "net_flow_5m_demand"

    return "default"


# ── Per-token mining ───────────────────────────────────────────────────────

async def mine_token(
    token: dict,
    args: argparse.Namespace,
    out_dir: Path,
    gt: GeckoTerminalClient,
    ds: DexScreenerClient,
) -> int:
    """Mine all valid time-windows from one token. Returns count of windows emitted."""
    pair = token.get("pair", "")
    addr = token.get("addr", "")
    if not pair or not addr:
        return 0

    try:
        cd = await assemble_chart_data(
            gt,
            pair,
            dexs_client=ds,
            limit_1m=400,
            limit_5m=400,
            limit_15m=400,
        )
    except Exception as e:
        logger.debug(f"[mine_token] candle fetch err for {addr[:12]}: {e}")
        return 0

    if not cd:
        return 0
    c1_all = cd.candles_1m or []
    c5_all = cd.candles_5m or []
    c15_all = cd.candles_15m or []

    # Need enough bars on all TFs to render
    if len(c1_all) < 60 or len(c5_all) < 60 or len(c15_all) < 30:
        return 0

    # Determine the valid sliding window range.
    # earliest_ts: after we have 60 1m bars of context
    # latest_ts: must leave forward_min 1m bars ahead for outcome
    earliest_ts = c1_all[60].open_time
    latest_ts = c1_all[-args.forward_min].open_time if len(c1_all) > args.forward_min else 0
    if latest_ts <= earliest_ts:
        return 0

    step_s = args.window_step_min * 60
    out_token_dir = out_dir / addr.lower()
    out_token_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    cur_ts = earliest_ts
    while cur_ts < latest_ts and count < args.max_windows_per_token:
        # Slice each TF at the current timestamp
        c1 = [c for c in c1_all if c.open_time <= cur_ts][-60:]
        c5 = [c for c in c5_all if c.open_time <= cur_ts][-60:]
        c15 = [c for c in c15_all if c.open_time <= cur_ts][-60:]

        if len(c1) < 30 or len(c5) < 30 or len(c15) < 30:
            cur_ts += step_s
            continue

        cur_price = c1[-1].close
        if cur_price <= 0:
            cur_ts += step_s
            continue

        # Render chart image
        img = render_chart_image(c1, c5, c15)
        if img is None:
            cur_ts += step_s
            continue

        # Forward outcome: max % gain over the next forward_min 1m bars
        forward_window_end = cur_ts + args.forward_min * 60
        forward_candles = [
            c for c in c1_all if cur_ts < c.open_time <= forward_window_end
        ]
        if not forward_candles:
            cur_ts += step_s
            continue
        forward_max = max(c.high for c in forward_candles)
        forward_max_gain_pct = ((forward_max / cur_price) - 1.0) * 100.0

        # Outcome label: 1 if token can gain >= 5% in the forward window
        outcome_label = 1 if forward_max_gain_pct >= 5.0 else 0

        # Synthetic pattern label
        pattern_label = _synthetic_pattern_label(c1, c5, c15, cur_price)

        # Build output paths
        ts_iso = datetime.fromtimestamp(cur_ts, tz=timezone.utc).isoformat()
        safe_ts = ts_iso.replace(":", "-")
        npy_path = out_token_dir / f"{safe_ts}.npy"
        json_path = out_token_dir / f"{safe_ts}.json"

        if not npy_path.exists():
            np.save(str(npy_path), img)
            with open(json_path, "w") as f:
                json.dump(
                    {
                        "addr": addr,
                        "ts": ts_iso,
                        "token": token.get("symbol", "?"),
                        "pattern_label": pattern_label,
                        "outcome_label": outcome_label,
                        "outcome_pnl_pct": round(forward_max_gain_pct, 4),
                        "context": {
                            "cur_price": cur_price,
                            "forward_min": args.forward_min,
                        },
                    },
                    f,
                )
            count += 1

        cur_ts += step_s

    return count


# ── Main entry point ───────────────────────────────────────────────────────

async def main():
    args = parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 60)
    print(f"mine_chart_universe.py  tokens={args.tokens}  "
          f"step={args.window_step_min}min  forward={args.forward_min}min  "
          f"max_win={args.max_windows_per_token}")
    print(f"Output -> {out_dir.resolve()}")
    print("=" * 60)

    print("\nGathering token universe...")
    universe = gather_token_universe(args.tokens)
    print(f"\nMining {len(universe)} tokens...\n")

    # Single GT + DS client shared across all tokens to benefit from caching
    gt = GeckoTerminalClient()
    ds = DexScreenerClient()

    total_windows = 0
    pattern_counts: dict[str, int] = defaultdict(int)
    outcome_counts: dict[int, int] = defaultdict(int)

    for i, token in enumerate(universe):
        try:
            count = await mine_token(token, args, out_dir, gt, ds)
            total_windows += count
            status = f"{count:3d} windows"
            try:
                print(
                    f"[{i+1:>4}/{len(universe)}] {token.get('symbol', '?'):<14} "
                    f"{token.get('addr', '')[:10]}  {status}  (total={total_windows})"
                )
            except UnicodeEncodeError:
                print(f"[{i+1:>4}/{len(universe)}] <non-ascii-symbol> {token.get('addr', '')[:10]}  {status}  (total={total_windows})")
        except Exception as e:
            try:
                print(f"[{i+1:>4}/{len(universe)}] {token.get('symbol', '?'):<14} ERR: {e}")
            except UnicodeEncodeError:
                print(f"[{i+1:>4}/{len(universe)}] <non-ascii-symbol> ERR: <unprintable>")

        await asyncio.sleep(args.rate_limit_s)

    # Build summary stats from written JSONs
    print("\nBuilding summary stats...")
    pattern_counts.clear()
    outcome_counts.clear()
    for json_file in out_dir.rglob("*.json"):
        try:
            with open(json_file) as f:
                meta = json.load(f)
            pattern_counts[meta.get("pattern_label", "?")] += 1
            outcome_counts[meta.get("outcome_label", -1)] += 1
        except Exception:
            pass

    total_files = sum(outcome_counts.values())

    print("\n" + "=" * 60)
    print(f"DONE: {total_windows} windows emitted from {len(universe)} tokens")
    print(f"Total .json files on disk: {total_files}")
    print("\nPattern label distribution:")
    for cls in PATTERN_CLASSES:
        cnt = pattern_counts.get(cls, 0)
        pct = cnt / max(total_files, 1) * 100
        print(f"  {cls:<30} {cnt:>6}  ({pct:.1f}%)")
    other = {k: v for k, v in pattern_counts.items() if k not in PATTERN_CLASSES}
    for k, v in sorted(other.items(), key=lambda x: -x[1]):
        print(f"  {k:<30} {v:>6}")
    print("\nOutcome label distribution:")
    for label, cnt in sorted(outcome_counts.items()):
        pct = cnt / max(total_files, 1) * 100
        print(f"  outcome_label={label}  {cnt:>6}  ({pct:.1f}%)")

    # Spot-check: load one .npy and verify shape
    npy_files = list(out_dir.rglob("*.npy"))
    if npy_files:
        sample_path = npy_files[0]
        arr = np.load(str(sample_path))
        print(f"\nSample .npy check: {sample_path.name}  shape={arr.shape}  dtype={arr.dtype}")
        if arr.shape == (3, 64, 64):
            print("  Shape (3, 64, 64) OK")
        else:
            print(f"  WARNING: unexpected shape {arr.shape}")
    else:
        print("\nNo .npy files written — check token availability and candle data.")


if __name__ == "__main__":
    asyncio.run(main())
