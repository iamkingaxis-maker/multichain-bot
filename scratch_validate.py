import json
import numpy as np
from collections import defaultdict
rows=json.load(open('scratch_rows.json'))

def block_mask(r, f, t, direction):
    v=r['em'].get(f)
    if v is None: return None
    if direction=='ge': return v>=t
    return v<t

def evaluate(f, t, direction, label):
    have=[r for r in rows if r['em'].get(f) is not None]
    blocked=[r for r in have if block_mask(r,f,t,direction)]
    inside=[r for r in have if not block_mask(r,f,t,direction)]
    def ngpct(rs): return 100*sum(1 for r in rs if r['lab']=='NG')/len(rs) if rs else 0
    ng_bl=sum(1 for r in blocked if r['lab']=='NG'); b_bl=sum(1 for r in blocked if r['lab']=='B')
    wk = b_bl/ng_bl if ng_bl>0 else 999
    print("="*70)
    print(f"{label}: {f} {direction} {t:.4g}  | have={len(have)}")
    print(f"  INSIDE n={len(inside)} NG%={ngpct(inside):.1f}   BLOCKED n={len(blocked)} NG%={ngpct(blocked):.1f}  winner-kill={wk:.2f} (B_bl={b_bl}, NG_bl={ng_bl})")
    # distinct tokens in blocked
    bl_tok=set(r['token'] for r in blocked)
    print(f"  blocked distinct tokens={len(bl_tok)}")
    # token concentration of blocked NG
    tc=defaultdict(int)
    for r in blocked:
        if r['lab']=='NG': tc[r['sym']]+=1
    top=sorted(tc.items(),key=lambda x:-x[1])[:6]
    print(f"  top blocked-NG tokens: {top}")
    # LEAVE-ONE-OUT by token: recompute gap dropping each token, report worst
    toks=set(r['token'] for r in have)
    worst_gap=None; worst_tok=None
    for drop in toks:
        sub=[r for r in have if r['token']!=drop]
        bl=[r for r in sub if block_mask(r,drop and f,t,direction)]
        bl=[r for r in sub if block_mask(r,f,t,direction)]
        ins=[r for r in sub if not block_mask(r,f,t,direction)]
        if len(bl)<15 or len(ins)<15: continue
        g=ngpct(bl)-ngpct(ins)
        if worst_gap is None or g<worst_gap:
            worst_gap=g; worst_tok=drop
    print(f"  LOO worst gap (drop one token) = {worst_gap:.1f}pp (dropping {worst_tok})")
    # TIME halves
    have_sorted=sorted(have,key=lambda r:r['time'])
    mid=len(have_sorted)//2
    for half,name in [(have_sorted[:mid],'H1'),(have_sorted[mid:],'H2')]:
        bl=[r for r in half if block_mask(r,f,t,direction)]
        ins=[r for r in half if not block_mask(r,f,t,direction)]
        g=ngpct(bl)-ngpct(ins) if bl and ins else 0
        print(f"  TIME {name}: blocked n={len(bl)} NG%={ngpct(bl):.1f}  inside n={len(ins)} NG%={ngpct(ins):.1f}  gap={g:.1f}")

evaluate('macro30_pct',-4.83,'ge','C1 macro30')
evaluate('trend_ma50_dist_pct',-5.463,'ge','C2 ma50_dist')
evaluate('trend_30m_slope_pct_per_min',-0.1816,'ge','C3 trend30_slope')
evaluate('pct_in_1h_range',0.198,'ge','C4 pct_in_1h_range')
evaluate('time_since_local_low_s',1200,'ge','C5 time_since_local_low')
evaluate('rsi_15m',61.25,'ge','C6 rsi_15m')
