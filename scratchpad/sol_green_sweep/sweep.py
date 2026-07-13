"""2-axis entry-meta sweep for GREEN ex-top-2 cohorts, ranked by volume-share.
GREEN = ex-top2 token-median>0 AND >=50% tokens green. OOS=green in >=3/4 halves.
n>=15 distinct tokens or UNDERPOWERED. p90 reported (winner-preserving)."""
import json, statistics as st, itertools
from collections import defaultdict, Counter

T = [t for t in json.load(open('scratchpad/sol_selection/_trips.json')) if t.get('ret') is not None]
legc = Counter(t['token'] for t in T)
TOP2 = set(k for k, _ in legc.most_common(2))
N_ALL = len(T)

def g(t, k): return t.get(k)

def tokmed(trips):
    by = defaultdict(list)
    for t in trips:
        if t['token'] in TOP2: continue
        by[t['address']].append(t['ret'])
    per = [st.median(v) for v in by.values()]
    if not per: return (None, 0, None)
    green_frac = 100 * sum(1 for x in per if x > 0) / len(per)
    return (st.median(per), len(per), green_frac)

def p90(trips):
    r = sorted(t['ret'] for t in trips)
    return r[int(len(r) * 0.9)] if r else None

def splits():
    s = sorted(T, key=lambda t: t['sell_time'] or t['time'] or '')
    mid = len(s) // 2
    ch1 = set(id(t) for t in s[:mid]); ch2 = set(id(t) for t in s[mid:])
    def day(t):
        try: return int((t['time'] or '2026-01-01')[8:10])
        except: return 1
    odd = set(id(t) for t in T if day(t) % 2 == 1)
    even = set(id(t) for t in T if day(t) % 2 == 0)
    # 4 halves = chrono x parity crossings kept simple as the 4 named halves
    return {'CH1': ch1, 'CH2': ch2, 'ODD': odd, 'EVEN': even}

SPL = splits()

# ---- Axis band predicates: (name -> predicate) ----
# Each axis is a dict of band-label -> lambda(t)->bool. None-valued rows fail (fail-closed on missing).
def band(k, op, thr):
    if op == 'ge': return lambda t: (g(t, k) is not None) and g(t, k) >= thr
    if op == 'le': return lambda t: (g(t, k) is not None) and g(t, k) <= thr
    if op == 'lt': return lambda t: (g(t, k) is not None) and g(t, k) < thr
    if op == 'gt': return lambda t: (g(t, k) is not None) and g(t, k) > thr

def rng(k, lo, hi): return lambda t: (g(t, k) is not None) and lo <= g(t, k) < hi

AXES = {
  'dip': {
     'deep(h1<=-45)': band('pc_h1','le',-45),
     'vdeep(h1<=-55)': band('pc_h1','le',-55),
     'mid(-45..-30)': rng('pc_h1',-45,-30),
     'shallow(h1>=-30)': band('pc_h1','ge',-30),
  },
  'liq': {
     'liq>=30k': band('liq','ge',30000),
     'liq>=35k': band('liq','ge',35000),
     'liq>=45k': band('liq','ge',45000),
     'liq<30k': band('liq','lt',30000),
  },
  'ubuy': {
     'ubuy>=45': band('unique_buyers_n','ge',45),
     'ubuy>=50': band('unique_buyers_n','ge',50),
     'ubuy<40': band('unique_buyers_n','lt',40),
  },
  'nf15': {
     'nf15>=150': band('net_flow_15s','ge',150),
     'nf15>=300': band('net_flow_15s','ge',300),
     'nf15<62': band('net_flow_15s','lt',62),
  },
  'nf60': {
     'nf60>=0': band('net_flow_60s','ge',0),
     'nf60>=150': band('net_flow_60s','ge',150),
     'nf60<0': band('net_flow_60s','lt',0),
  },
  'bsh1': {
     'bsh1<=1.2': band('bs_h1','le',1.2),
     'bsh1>=1.35': band('bs_h1','ge',1.35),
     'bsh1>=1.6': band('bs_h1','ge',1.6),
  },
  'age': {
     'age<=2h': band('lifecycle_age_h','le',2),
     'age2-10h': rng('lifecycle_age_h',2,10),
     'age>=10h': band('lifecycle_age_h','ge',10),
     'age>=4h': band('lifecycle_age_h','ge',4),
  },
  'mbuy': {
     'mbuy<=28': band('mean_buy_usd','le',28),
     'mbuy<=43': band('mean_buy_usd','le',43),
     'mbuy>=60': band('mean_buy_usd','ge',60),
  },
  'evol': {
     'evol>=1M': band('entry_vol_h24','ge',1000000),
     'evol>=1.5M': band('entry_vol_h24','ge',1500000),
     'evol<634k': band('entry_vol_h24','lt',634000),
  },
  'mtf': {
     'mtf<=-1': band('chart_mtf_score','le',-1),
     'mtf==0': lambda t: g(t,'chart_mtf_score')==0,
     'mtf>=1': band('chart_mtf_score','ge',1),
  },
  'top10': {
     'top10<=30': band('top10_holder_pct','le',30),
     'top10<=40': band('top10_holder_pct','le',40),
  },
  'bp60': {
     'bp60>=0.62': band('buy_pressure_60s','ge',0.62),
     'bp60<0.52': band('buy_pressure_60s','lt',0.52),
  },
}

def cell_stats(pred):
    sub = [t for t in T if pred(t)]
    if not sub: return None
    m, ntok, gf = tokmed(sub)
    if ntok == 0: return None
    halves = []
    for name in ['CH1','CH2','ODD','EVEN']:
        hs = SPL[name]
        hv = [t for t in sub if id(t) in hs]
        hm, hn, hgf = tokmed(hv)
        halves.append((name, hm, hn))
    ng = sum(1 for _, hm, _ in halves if hm is not None and hm > 0)
    return dict(n=len(sub), vol=len(sub)/N_ALL, tokmed=m, ntok=ntok, gf=gf,
                p90=p90(sub), halves=halves, ngreen_halves=ng, pred=pred)

def is_green(s):
    return s and s['tokmed'] is not None and s['tokmed'] > 0 and s['gf'] >= 50 and s['ntok'] >= 15

# ---- enumerate all 2-axis combos ----
axis_names = list(AXES.keys())
results = []
for a, b in itertools.combinations(axis_names, 2):
    for la, pa in AXES[a].items():
        for lb, pb in AXES[b].items():
            pred = (lambda pa, pb: (lambda t: pa(t) and pb(t)))(pa, pb)
            s = cell_stats(pred)
            if s is None: continue
            s['label'] = f"{la} & {lb}"
            s['axes'] = (a, b)
            results.append(s)

green = [s for s in results if is_green(s)]
green.sort(key=lambda s: -s['vol'])

def fmt(x): return f"{x:+.1f}" if isinstance(x,(int,float)) else "  -"

print(f"BASELINE ex2 tokmed=-5.8  N_ALL={N_ALL}")
print(f"\nTotal 2-axis cells evaluated: {len(results)}  |  GREEN (tokmed>0 & >=50% grn & n>=15): {len(green)}\n")
print(f"{'cell':<34}{'vol%':>6}{'ex2med':>8}{'grn%':>6}{'ntok':>6}{'p90':>7}  4-half(green/pow)")
print("-"*104)
for s in green[:40]:
    powered = s['ntok'] >= 15
    hstr = " ".join(f"{nm}{fmt(hm) if hm is not None else 'na':>6}({hn})" for nm,hm,hn in s['halves'])
    print(f"{s['label']:<34}{s['vol']*100:>5.1f}%{fmt(s['tokmed']):>8}{s['gf']:>5.0f}%{s['ntok']:>6}{fmt(s['p90']):>7}  {s['ngreen_halves']}/4")

# also print deep+liq reference explicitly
ref_pred = lambda t: (g(t,'pc_h1') is not None and g(t,'pc_h1')<=-45) and (g(t,'liq') is not None and g(t,'liq')>=30000)
rs = cell_stats(ref_pred)
print(f"\nREF deep+liq>=30k: vol={rs['vol']*100:.1f}% ex2med={fmt(rs['tokmed'])} grn={rs['gf']:.0f}% ntok={rs['ntok']} p90={fmt(rs['p90'])} halves={rs['ngreen_halves']}/4")

# Save top green for downstream
import pickle
out = [{'label':s['label'],'axes':s['axes'],'vol':s['vol'],'tokmed':s['tokmed'],'gf':s['gf'],
        'ntok':s['ntok'],'p90':s['p90'],'ngreen_halves':s['ngreen_halves'],'n':s['n'],
        'halves':[(nm,hm,hn) for nm,hm,hn in s['halves']]} for s in green]
json.dump(out, open('scratchpad/sol_green_sweep/_green_cells.json','w'))
