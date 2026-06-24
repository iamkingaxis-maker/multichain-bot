"""Mine win-signature for deepflush_timebox: join buys->next sell per (bot,address),
split winner/loser, rank numeric entry_meta separators."""
import json, gzip, statistics, math
from datetime import datetime

BOT = "deepflush_timebox"
try: d = json.load(open('_df_full.json.gz'))
except: d = json.load(gzip.open('_df_full.json.gz'))
t = d['trades'] if isinstance(d, dict) and 'trades' in d else d

rows = [r for r in t if r.get('bot_id') == BOT]

def ts(r):
    try: return datetime.fromisoformat(r['time'].replace('Z','+00:00')).timestamp()
    except: return None

for r in rows: r['_ts'] = ts(r)
rows = [r for r in rows if r['_ts'] is not None]
rows.sort(key=lambda r: r['_ts'])

# JOIN: each buy -> temporally-next sell for same address
buys = [r for r in rows if r.get('type') == 'buy']
sells = [r for r in rows if r.get('type') == 'sell']
from collections import defaultdict
sells_by_addr = defaultdict(list)
for s in sells: sells_by_addr[s.get('address')].append(s)
for v in sells_by_addr.values(): v.sort(key=lambda r: r['_ts'])

pairs = []
used = set()
for b in buys:
    addr = b.get('address')
    cand = [s for s in sells_by_addr.get(addr, []) if s['_ts'] >= b['_ts'] and id(s) not in used]
    if not cand: continue
    s = cand[0]
    used.add(id(s))
    em = b.get('entry_meta')
    if isinstance(em, str):
        try: em = json.loads(em)
        except: em = {}
    if not em: continue
    pp = s.get('pnl_pct')
    if pp is None or abs(pp) > 300: continue
    pairs.append((b, s, em, pp))

print(f"buys={len(buys)} sells={len(sells)} joined_pairs(valid)={len(pairs)}")
wins = [p for p in pairs if p[3] > 0]
losers = [p for p in pairs if p[3] <= 0]
print(f"winners={len(wins)} losers={len(losers)} WR={100*len(wins)/len(pairs):.1f}%")
print(f"win pnl_pct median={statistics.median([p[3] for p in wins]):.2f}" if wins else "no wins")
print(f"loser pnl_pct median={statistics.median([p[3] for p in losers]):.2f}" if losers else "no losers")

n = len(pairs)
# numeric feature collection
def num(v):
    if isinstance(v, bool): return float(v)
    if isinstance(v, (int, float)): return float(v)
    return None

feats = defaultdict(lambda: {'w': [], 'l': [], 'cov': 0})
for b, s, em, pp in pairs:
    seen = set()
    for k, v in em.items():
        x = num(v)
        if x is None: continue
        if not math.isfinite(x): continue
        seen.add(k)
        (feats[k]['w'] if pp > 0 else feats[k]['l']).append(x)
    for k in seen: feats[k]['cov'] += 1

results = []
for k, dd in feats.items():
    w, l = dd['w'], dd['l']
    if len(w) < 3 or len(l) < 3: continue
    wm, lm = statistics.median(w), statistics.median(l)
    allv = w + l
    sd = statistics.pstdev(allv) if len(allv) > 1 else 0
    sep = abs(wm - lm) / sd if sd > 0 else 0
    cov = 100.0 * dd['cov'] / n
    # distinct values (filter near-constant boolean-ish that are all same)
    nd = len(set(allv))
    results.append((k, wm, lm, sep, cov, nd, len(w), len(l)))

results.sort(key=lambda r: -r[3])
HOLDER = ('holder', 'top10_holder', 'top1_holder', 'top5_buyer', 'topholder', 'unique_buyer')
print("\nfeature | wmed | lmed | sep | cov% | ndistinct")
for k, wm, lm, sep, cov, nd, nw, nl in results[:60]:
    if nd < 3: continue  # skip near-constant
    print(f"{k} | {wm:.4g} | {lm:.4g} | {sep:.3f} | {cov:.0f} | nd={nd}")
