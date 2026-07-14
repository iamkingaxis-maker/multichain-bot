"""Probe io.dexscreener trade-log reach on 4 rip runners (oldest ts in the ~100 returned)."""
import asyncio, json, sys
from datetime import datetime, timezone
sys.path.insert(0, ".")
from feeds.dexscreener_client import DexScreenerClient

PAIRS = [
    ("Guardians_0701_17", "64uYz2gdsTVZ7hC6gfPGWDBXKdCJubtEv463rrnXmSg3"),
    ("TJR2_0701_14", "8imenxcQAGZapaWz2BaWMrjH9Geio7mQnJz4ugdfCmhw"),
    ("MMGA_0629_20", None),  # resolve below from rip_runners
    ("FRAG_0629_22", None),
]
rip = json.load(open("scratchpad/ripday/rip_runners.json"))
for tok, r in rip.items():
    if r["sym"] == "MMGA":
        PAIRS[2] = ("MMGA_0629_20", r["pair"])
    if r["sym"] == "FRAG":
        PAIRS[3] = ("FRAG_0629_22", r["pair"])

async def main():
    cl = DexScreenerClient()
    for name, pair in PAIRS:
        if not pair:
            print(name, "no pair"); continue
        try:
            tr = await cl.fetch_recent_trades(pair, limit=300)
        except Exception as e:
            print(name, "ERR", e); continue
        if not tr:
            print(f"{name:20s} 0 trades returned")
        else:
            tss = sorted(t.get("ts") or "" for t in tr)
            mk = sum(1 for t in tr if t.get("maker"))
            print(f"{name:20s} n={len(tr)} makers={mk} oldest={tss[0][:19]} newest={tss[-1][:19]}")
            print("   sample:", {k: v for k, v in tr[0].items()})
        await asyncio.sleep(2.0)

asyncio.run(main())
