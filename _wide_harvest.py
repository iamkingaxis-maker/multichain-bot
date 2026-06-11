"""WIDE wallet harvest (2026-06-11, 'we need more wallets now').
Three funnels, one ranked output:
  A. Widened runner-recurrence (peak>=15, 60 runners, hits>=2, broad buy band)
  B. Co-buyer cluster expansion around the PROVEN 6 (wallets that co-bought
     the same tokens our keepers bought recently — strongest prior)
  C. Old-roster slice (ranks beyond the tried top-40 by n_winners)
Scoring (diversity+net) runs with heavy pacing; RPC failures -> retry queue.
"""
import asyncio, gzip, io, json, sys, time, urllib.request
from collections import defaultdict
sys.path.insert(0, "."); sys.path.insert(0, "scripts")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")
BASE = "https://gracious-inspiration-production.up.railway.app"

def _get(url):
    req = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=180) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)

WATCH = set(json.load(open("config/follow_watchlist.json")))
CAND = defaultdict(lambda: {"src": set(), "hits": 0})

# ── A. widened runner recurrence ─────────────────────────────────────────
ev = _get(f"{BASE}/api/universe-recorder?limit=5000")
best = {}
for e in ev:
    pk = e.get("peak_pct")
    if not isinstance(pk, (int, float)) or pk < 15: continue
    tok = e.get("token_address") or e.get("pair_address")
    if not e.get("pair_address"): continue
    if tok not in best or pk > best[tok][1]:
        best[tok] = (e["pair_address"], pk, e.get("symbol"))
runners = sorted(best.items(), key=lambda kv: -kv[1][1])[:60]
print(f"[A] runners: {len(runners)}", file=sys.stderr)

async def harvest_pairs(pairs, label, skip_frac=0.05, take_frac=0.60, lo_usd=20, hi_usd=5000):
    from feeds.dexscreener_client import DexScreenerClient
    cl = DexScreenerClient()
    hits = defaultdict(set)
    for tok, pair, sym in pairs:
        try:
            trades = await cl.fetch_recent_trades(pair, limit=200)
        except Exception:
            continue
        buys = [t for t in trades if t.get("kind") == "buy" and t.get("maker")
                and lo_usd <= float(t.get("volume_usd") or 0) <= hi_usd]
        buys.sort(key=lambda t: t.get("ts", ""))
        lo = int(len(buys) * skip_frac); hi = lo + max(1, int(len(buys) * take_frac))
        for t in buys[lo:hi]:
            hits[str(t["maker"])].add(tok)
        await asyncio.sleep(0.45)
    print(f"[{label}] wallets seen: {len(hits)}", file=sys.stderr)
    return hits

hA = asyncio.run(harvest_pairs([(t, p, s) for t, (p, pk, s) in runners], "A"))
for w, toks in hA.items():
    if len(toks) >= 2 and w not in WATCH:
        CAND[w]["src"].add("runners"); CAND[w]["hits"] += len(toks)

# ── B. co-buyer expansion around the proven 6 ────────────────────────────
# tokens our elites bought on the bad day (saved) + recent fire tokens
try:
    mints = json.load(open("_elite_badday_mints.json"))
except Exception:
    mints = []
# resolve pools for a sample of those mints
import random
random.seed(11)
sample = random.sample(mints, min(35, len(mints)))
pairs_b = []
from curl_cffi import requests as cr
S = cr.Session(impersonate="chrome")
for m in sample:
    try:
        r = S.get(f"https://api.dexscreener.com/latest/dex/tokens/{m}", timeout=12)
        ps = [p for p in (r.json().get("pairs") or []) if p.get("chainId") == "solana"]
        ps.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
        if ps:
            pairs_b.append((m, ps[0]["pairAddress"], (ps[0].get("baseToken") or {}).get("symbol")))
    except Exception:
        pass
    time.sleep(0.4)
print(f"[B] elite-token pools resolved: {len(pairs_b)}", file=sys.stderr)
hB = asyncio.run(harvest_pairs(pairs_b, "B", skip_frac=0.0, take_frac=0.7))
for w, toks in hB.items():
    if len(toks) >= 3 and w not in WATCH:   # co-bought >=3 elite tokens
        CAND[w]["src"].add("elite-cluster"); CAND[w]["hits"] += len(toks)

# ── C. roster slice ranks 40-150 by n_winners ────────────────────────────
try:
    roster = json.load(open("analysis/_prune_mine/discovered_wallets.json"))
    rows = []
    if isinstance(roster, dict):
        for k, v in roster.items():
            nw = (v.get("n_winners") if isinstance(v, dict) else v) or 0
            rows.append((k, int(nw) if isinstance(nw, (int, float)) else 0))
    elif isinstance(roster, list):
        for x in roster:
            if isinstance(x, dict):
                w = x.get("wallet") or x.get("address")
                rows.append((w, int(x.get("n_winners") or 0)))
            elif isinstance(x, (list, tuple)) and len(x) >= 2:
                v = x[1]
                nw = (v.get("n_winners") if isinstance(v, dict) else v) or 0
                rows.append((x[0], int(nw) if isinstance(nw, (int, float)) else 0))
    rows = [r for r in rows if r[0]]
    rows.sort(key=lambda r: -r[1])
    print(f"[C] roster rows: {len(rows)}", file=sys.stderr)
    for w, nw in rows[40:150]:
        if w not in WATCH:
            CAND[w]["src"].add("roster"); CAND[w]["hits"] += nw // 10
except Exception as e:
    print(f"[C] roster unavailable: {e}", file=sys.stderr)

print(f"\nTOTAL candidates pre-score: {len(CAND)}")
ranked = sorted(CAND.items(), key=lambda kv: (-len(kv[1]['src']), -kv[1]['hits']))[:60]
for w, m in ranked[:25]:
    print(f"  {w}  src={','.join(sorted(m['src']))} hits={m['hits']}")

# ── scoring with heavy pacing ────────────────────────────────────────────
import score_wallet_diversity as swd
out, retryq = [], []
for w, m in ranked:
    r = swd.analyze(w, 70)
    time.sleep(0.3)
    if r is None:
        retryq.append(w); continue
    cls = swd.classify(r)
    keep = cls == "SELECTOR" and r.get("net_realized", 0) > 0 and r.get("roundtrips", 0) >= 4
    wr = f"{r['realized_wr']*100:.0f}%" if r.get("realized_wr") is not None else "n/a"
    print(f"  {w[:12]:12s} src={','.join(sorted(m['src'])):22s} ndist={r['n_distinct']:3d} "
          f"rtrips={r['roundtrips']:3d} rWR={wr:>4s} net={r['net_realized']:+7.2f} {cls}"
          + ("  *** KEEPER-GRADE" if keep else ""))
    if keep:
        out.append({"wallet": w, "sources": sorted(m["src"]), **{k: v for k, v in r.items()
                    if isinstance(v, (int, float, str))}})
json.dump({"keepers": out, "retry_rpc": retryq},
          open("_wide_harvest_results.json", "w"), indent=2)
print(f"\nKEEPER-GRADE: {len(out)} | RPC-retry queue: {len(retryq)} -> _wide_harvest_results.json")
