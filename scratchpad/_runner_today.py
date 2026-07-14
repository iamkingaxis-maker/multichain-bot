#!/usr/bin/env python3
"""Fetch trade-log (~100 recent) + 1m bars for today's named cases."""
import asyncio, json, os
from feeds.dexscreener_client import DexScreenerClient

CASES = [
    ("mogdog",   "2mkZkLecSeJjyAPenuRF9LZPpQEUrpRr6LAcNQh59V39", "2026-07-10T16:53"),
    ("SMOLE",    "Ed8TRTpcsvu4Q3pnAEnwbeSwERZr2542PzdNAKCn5b1s", "2026-07-10T17:11"),
    ("Balloon",  "B2dXjWyVj6urw8pPjGQq9yMAbfPsuvZQ8cZEQ6WxLyph", "2026-07-10T16:56"),
    ("Bullscan", "6WVuh3BuoGhU4sXdgx1VyArjrBjaUKnJPL6Hwr8UY5xX", "2026-07-09T17:37"),
]
OUT = "scratchpad/_runner_bars"
os.makedirs(OUT, exist_ok=True)

async def main():
    cli = DexScreenerClient(cache_ttl=5, rate_per_min=60)
    for sym, pair, entry in CASES:
        trades = await cli.fetch_recent_trades(pair, limit=300)
        await asyncio.sleep(2)
        bars = await cli.fetch_1m(pair, limit=999)
        await asyncio.sleep(2)
        tss = [t.get("ts") for t in trades if t.get("ts")]
        print(f"{sym}: trades={len(trades)} span={min(tss) if tss else '?'}..{max(tss) if tss else '?'} "
              f"bars={len(bars)} bspan={bars[0].open_time if bars else 0}..{bars[-1].open_time if bars else 0} entry={entry}")
        json.dump({"sym": sym, "pair": pair, "entry": entry,
                   "trades": trades,
                   "bars": [[b.open_time, b.open, b.high, b.low, b.close, b.volume] for b in bars]},
                  open(os.path.join(OUT, f"today_{sym}.json"), "w"))
asyncio.run(main())
