import json, gzip, statistics, math
from datetime import datetime
from collections import defaultdict
BOT="deepflush_timebox"
try: d=json.load(open('_df_full.json.gz'))
except: d=json.load(gzip.open('_df_full.json.gz'))
t=d['trades'] if isinstance(d,dict) and 'trades' in d else d
rows=[r for r in t if r.get('bot_id')==BOT]
def ts(r):
    try: return datetime.fromisoformat(r['time'].replace('Z','+00:00')).timestamp()
    except: return None
for r in rows: r['_ts']=ts(r)
rows=[r for r in rows if r['_ts'] is not None]; rows.sort(key=lambda r:r['_ts'])
buys=[r for r in rows if r.get('type')=='buy']; sells=[r for r in rows if r.get('type')=='sell']
sba=defaultdict(list)
for s in sells: sba[s.get('address')].append(s)
for v in sba.values(): v.sort(key=lambda r:r['_ts'])
pairs=[];used=set()
for b in buys:
    cand=[s for s in sba.get(b.get('address'),[]) if s['_ts']>=b['_ts'] and id(s) not in used]
    if not cand: continue
    s=cand[0]; used.add(id(s))
    em=b.get('entry_meta')
    if isinstance(em,str):
        try: em=json.loads(em)
        except: em={}
    if not em: continue
    pp=s.get('pnl_pct')
    if pp is None or abs(pp)>300: continue
    pairs.append((em,pp))
n=len(pairs)

def dist(k):
    w=[em[k] for em,pp in pairs if isinstance(em.get(k),(int,float)) and not isinstance(em.get(k),bool) and pp>0 and math.isfinite(em[k])]
    l=[em[k] for em,pp in pairs if isinstance(em.get(k),(int,float)) and not isinstance(em.get(k),bool) and pp<=0 and math.isfinite(em[k])]
    return sorted(w),sorted(l)

def q(a,p):
    if not a: return None
    i=p*(len(a)-1); lo=int(i);
    if lo>=len(a)-1: return a[-1]
    return a[lo]+(a[lo+1]-a[lo])*(i-lo)

# evaluate a >= or <= threshold: keep-win-frac and win-rate inside gate
def gate(k,thr,direction):
    inside=[(em,pp) for em,pp in pairs if isinstance(em.get(k),(int,float)) and not isinstance(em.get(k),bool) and ((em[k]>=thr) if direction=='>=' else (em[k]<=thr))]
    miss=[(em,pp) for em,pp in pairs if not isinstance(em.get(k),(int,float)) or isinstance(em.get(k),bool)]
    if not inside: return None
    iw=sum(1 for em,pp in inside if pp>0)
    tot_w=sum(1 for em,pp in pairs if pp>0)
    return len(inside), iw, 100*iw/len(inside), 100*iw/tot_w, len(miss)

for k in ['1s_bottom_score','time_since_h6_peak_secs','rolling_ng_proba','pc_h1','cycles_seen_before_buy','shape_90m_mins_since_max','top10_buyer_within_60s_count','1m_close_in_range','hl_delta_pct','token_ema_slope_pct']:
    w,l=dist(k)
    cov=100*(len(w)+len(l))/n
    print(f"\n=== {k}  cov={cov:.0f}%  (w n={len(w)} l n={len(l)})")
    print(f"  WIN  p25/med/p75: {q(w,.25):.4g} / {q(w,.5):.4g} / {q(w,.75):.4g}   range [{w[0]:.4g},{w[-1]:.4g}]")
    print(f"  LOSE p25/med/p75: {q(l,.25):.4g} / {q(l,.5):.4g} / {q(l,.75):.4g}   range [{l[0]:.4g},{l[-1]:.4g}]")
    # pick midpoint threshold
    mid=(statistics.median(w)+statistics.median(l))/2
    direction='>=' if statistics.median(w)>statistics.median(l) else '<='
    g=gate(k,mid,direction)
    if g: print(f"  GATE {direction} {mid:.4g}: inside={g[0]} wins={g[1]} WR_inside={g[2]:.0f}% keepwin={g[3]:.0f}% missing(nodata)={g[4]}")
