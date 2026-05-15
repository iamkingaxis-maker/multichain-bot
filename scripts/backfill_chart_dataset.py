"""Backfill historical chart dataset from closed trades.

Pulls all closed trades from /api/trades, fetches pre-entry candle
data, renders to 3-channel image, writes .npy + .json label files
to .cnn_dataset/v1/.

Pattern label: from entry_meta.chart_pattern_5m (chart_reader output
captured at trade time).
Outcome label: 1 if total pnl > 0, 0 otherwise.

Usage: python scripts/backfill_chart_dataset.py
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from feeds.chart_image_renderer import render_chart_image
from feeds.candle_utils import Candle

OUT_DIR = Path(".cnn_dataset/v1")
API_URL = "https://gracious-inspiration-production.up.railway.app/api/trades?limit=2000"


def fetch_trades() -> list:
    """Pull all trades from production API."""
    r = urllib.request.urlopen(API_URL, timeout=30)
    data = json.loads(r.read())
    return data if isinstance(data, list) else data.get("trades", [])


def pair_buys_with_sells(trades: list) -> list:
    """Pair each buy with its subsequent sells; compute total pnl per buy."""
    sells_by_addr = defaultdict(list)
    for t in trades:
        if t.get("type") == "sell":
            sells_by_addr[t.get("address", "")].append(t)
    for a in sells_by_addr:
        sells_by_addr[a].sort(key=lambda x: x.get("time", ""))

    paired = []
    for t in trades:
        if t.get("type") != "buy":
            continue
        addr = t.get("address", "")
        ts = t.get("time", "")
        rs = [s for s in sells_by_addr.get(addr, []) if s.get("time", "") > ts]
        if not rs:
            continue
        total_pnl = sum((s.get("pnl") or 0) for s in rs)
        paired.append({
            "addr": addr,
            "time": ts,
            "token": t.get("token"),
            "pair": t.get("pair_address"),
            "pnl": total_pnl,
            "entry_meta": t.get("entry_meta") or {},
        })
    return paired


async def fetch_candles_at_entry(pair_addr: str, entry_ts_iso: str):
    """Fetch (candles_1m, candles_5m, candles_15m) just before entry_ts.

    Uses the existing assemble_chart_data — same source the bot used at
    entry time. Returns None on any error.
    """
    try:
        from feeds.chart_data import assemble_chart_data
        from feeds.gt_client import GeckoTerminalClient
        from feeds.dexscreener_client import DexScreenerClient
        gt = GeckoTerminalClient()
        ds = DexScreenerClient()
        cd = await assemble_chart_data(gt, pair_addr, dexs_client=ds)
        if not cd:
            return None, None, None
        return (cd.candles_1m or [], cd.candles_5m or [], cd.candles_15m or [])
    except Exception as e:
        print(f"  candle fetch err: {e}")
        return None, None, None


def label_for_trade(trade: dict) -> dict:
    """Build the label JSON for one trade."""
    em = trade.get("entry_meta") or {}
    pattern = em.get("chart_pattern_5m") or "none"
    return {
        "addr": trade["addr"],
        "ts": trade["time"],
        "token": trade["token"],
        "pattern_label": pattern,
        "outcome_label": 1 if trade["pnl"] > 0 else 0,
        "outcome_pnl_pct": float(em.get("outcome_pnl_pct") or 0),
        "context": {
            "triggers_fired": em.get("triggers_fired") or [],
            "hour_ct": em.get("hour_ct"),
            "mcap_usd": em.get("entry_market_cap_usd"),
        },
    }


async def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    trades = fetch_trades()
    paired = pair_buys_with_sells(trades)
    print(f"Found {len(paired)} closed trades")

    success = 0
    fail = 0
    for i, t in enumerate(paired):
        out_npy = OUT_DIR / f"{t['addr']}_{t['time'].replace(':', '-')}.npy"
        out_json = OUT_DIR / f"{t['addr']}_{t['time'].replace(':', '-')}.json"
        if out_npy.exists() and out_json.exists():
            success += 1
            continue
        if not t.get("pair"):
            fail += 1
            continue
        c1, c5, c15 = await fetch_candles_at_entry(t["pair"], t["time"])
        if not c1 or not c5 or not c15:
            fail += 1
            print(f"[{i+1}/{len(paired)}] {t['token']}: no candles available")
            continue
        img = render_chart_image(c1, c5, c15)
        if img is None:
            fail += 1
            print(f"[{i+1}/{len(paired)}] {t['token']}: render failed")
            continue
        np.save(out_npy, img)
        with open(out_json, "w") as f:
            json.dump(label_for_trade(t), f, indent=2)
        success += 1
        print(f"[{i+1}/{len(paired)}] {t['token']}: ok (pattern={label_for_trade(t)['pattern_label']}, win={label_for_trade(t)['outcome_label']})")
        await asyncio.sleep(1.0)  # GT rate-limit pacing

    print(f"\nDone: {success} saved, {fail} failed")


if __name__ == "__main__":
    asyncio.run(main())
