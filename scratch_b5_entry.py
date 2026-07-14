"""Batch-5 entry-state sampler: for one wallet, sample buys and reconstruct
token state at entry (dip_90m off prior-90m high) + token age + fdv.
Reuses wallet_decode.trade_map + mine_wallet_entries token_pool_ohlc/entry_features.
Usage: python scratch_b5_entry.py <ADDR> [sigs=150] [max_tokens=10]
"""
import sys, os, time, statistics
sys.path.insert(0, "scripts")
sys.path.insert(0, ".")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
import wallet_decode as w
import mine_wallet_entries as m

addr = sys.argv[1]
sigs = int(sys.argv[2]) if len(sys.argv) > 2 else 150
maxtok = int(sys.argv[3]) if len(sys.argv) > 3 else 10

tok = w.trade_map(addr, sigs)
# build per-token first-buy ts + total spent
ents = []
for mint, r in tok.items():
    if not r["buys"]:
        continue
    b0 = min(b[0] for b in r["buys"])
    spent = sum(b[1] for b in r["buys"])
    ents.append((b0, mint, spent))
ents.sort(reverse=True)  # most recent first
print(f"WALLET {addr[:12]} | {len(ents)} bought tokens; sampling up to {maxtok}")

samp = ents[:maxtok]
dips, ages, fdvs = [], [], []
mom, dip = 0, 0
for b0, mint, spent in samp:
    meta = m.token_pool_ohlc(mint)
    if not meta:
        print(f"  {mint[:10]} spent={spent:.2f} -> no OHLC")
        continue
    created, liq, fdv, ohlcv = meta
    f = m.entry_features(b0, created, liq, fdv, ohlcv)
    if not f:
        print(f"  {mint[:10]} spent={spent:.2f} -> no entry feats (buys older than window)")
        continue
    d = f["dip_90m"]; a = f["age_h"]
    dips.append(d)
    if a is not None:
        ages.append(a)
    if fdv:
        fdvs.append(fdv)
    cls = "MOM(strength)" if d > -3 else "DIP"
    if d > -3:
        mom += 1
    else:
        dip += 1
    print(f"  {mint[:10]} spent={spent:.2f}SOL dip90m={d:+.1f}% age={a if a is None else round(a,1)}h "
          f"fdv=${fdv/1e6:.2f}M fwd6h_max={f['fwd_max']:+.1f}% -> {cls}")

if dips:
    print(f"\nENTRY-STATE: n={len(dips)} | dip90m median {statistics.median(dips):+.1f}% "
          f"(p25 {m.pctl(dips,0.25):+.1f} / p75 {m.pctl(dips,0.75):+.1f}) | MOM={mom} DIP={dip}")
if ages:
    print(f"AGE: median {statistics.median(ages):.1f}h (p25 {m.pctl(ages,0.25):.1f} / p75 {m.pctl(ages,0.75):.1f})")
if fdvs:
    print(f"FDV: median ${statistics.median(fdvs)/1e6:.2f}M")
