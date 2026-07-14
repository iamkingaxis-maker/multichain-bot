# -*- coding: utf-8 -*-
import json, sys, io
sys.stdout=io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import numpy as np
from collections import defaultdict
rows=json.load(open('scratch_rows.json'))
TOTAL_NG=sum(1 for r in rows if r['lab']=='NG')
TOTAL_B=sum(1 for r in rows if r['lab']=='B')

def bm(r,f,t,direction):
    v=r['em'].get(f)
    if v is None: return None
    return v>=t if direction=='ge' else v<t

def ev(f,t,direction,label):
    have=[r for r in rows if r['em'].get(f) is not None]
    blocked=[r for r in have if bm(r,f,t,direction)]
    inside=[r for r in have if not bm(r,f,t,direction)]
    def ngp(rs): return 100*sum(1 for r in rs if r['lab']=='NG')/len(rs) if rs else 0
    ng_bl=sum(1 for r in blocked if r['lab']=='NG'); b_bl=sum(1 for r in blocked if r['lab']=='B')
    wk=b_bl/ng_bl if ng_bl>0 else 999
    toks=set(r['token'] for r in have)
    worst=None
    for drop in toks:
        sub=[r for r in have if r['token']!=drop]
        bl=[r for r in sub if bm(r,f,t,direction)]; ins=[r for r in sub if not bm(r,f,t,direction)]
        if len(bl)<15 or len(ins)<15: continue
        g=ngp(bl)-ngp(ins)
        if worst is None or g<worst: worst=g
    hs=sorted(have,key=lambda r:r['time']); mid=len(hs)//2
    th=[]
    for half in (hs[:mid],hs[mid:]):
        bl=[r for r in half if bm(r,f,t,direction)]; ins=[r for r in half if not bm(r,f,t,direction)]
        th.append((len(bl),ngp(bl),ngp(ins)))
    print("%s: %s %s %.4g | INSIDE n=%d NG%%=%.1f | BLOCKED n=%d NG%%=%.1f wk=%.2f (B_bl=%d NG_bl=%d) | distinct_bl_tok=%d | LOO_worst_gap=%.1f | H1(n=%d bl%.0f in%.0f) H2(n=%d bl%.0f in%.0f)"%(
        label,f,direction,t,len(inside),ngp(inside),len(blocked),ngp(blocked),wk,b_bl,ng_bl,
        len(set(r['token'] for r in blocked)),worst if worst is not None else -99,
        th[0][0],th[0][1],th[0][2],th[1][0],th[1][1],th[1][2]))

print("base: NG=%d B=%d total=%d NG%%=%.1f"%(TOTAL_NG,TOTAL_B,len(rows),100*TOTAL_NG/len(rows)))
ev('macro30_pct',-4.83,'ge','C1_macro30')
ev('shape_30m_chg_pct',-4.83,'ge','C1b_shape30chg')
ev('trend_ma50_dist_pct',-5.463,'ge','C2_ma50dist')
ev('trend_30m_slope_pct_per_min',-0.1816,'ge','C3_trend30slope')
ev('pct_in_1h_range',0.198,'ge','C4_pct_in_1h')
ev('time_since_local_low_s',1200,'ge','C5_time_since_low')
ev('rsi_15m',61.25,'ge','C6_rsi15m')
ev('pc_m5',0.27,'ge','C7_pc_m5')
ev('1s_bars_120s',2,'lt','C8_1s_bars120')
