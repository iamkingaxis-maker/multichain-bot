import json, statistics as st
d=json.load(open('_full_trades.json'))
buys=[r for r in d if r.get('type')=='buy']
# token-level: dedup by address keeping first occurrence per token
def med(x): return st.median(x) if x else float('nan')
def mean(x): return sum(x)/len(x) if x else float('nan')
# gather metrics
cp=[]; bsl=[]; freshgap=[]; rng=[]; bottom=[]
seen=set()
rows=[]
for r in buys:
    a=r.get('address')
    em=r.get('entry_meta') or {}
    emp=r.get('entry_mid_price'); ep=r.get('entry_price')
    cpv=em.get('1s_close_pos_60s'); bslv=em.get('1s_bars_since_low_60s')
    rngv=em.get('1s_range_pct_60s'); bsv=em.get('1s_bottom_score')
    rows.append((a,cpv,bslv,rngv,bsv,emp,ep))
# token-level dedup (first per token)
tok={}
for a,cpv,bslv,rngv,bsv,emp,ep in rows:
    if a in tok: continue
    tok[a]=(cpv,bslv,rngv,bsv,emp,ep)
print("distinct tokens:",len(tok),"raw buys:",len(buys))
cp=[t[0] for t in tok.values() if isinstance(t[0],(int,float))]
bsl=[t[1] for t in tok.values() if isinstance(t[1],(int,float))]
rng=[t[2] for t in tok.values() if isinstance(t[2],(int,float))]
bottom=[t[3] for t in tok.values() if isinstance(t[3],(int,float))]
for a,(cpv,bslv,rngv,bsv,emp,ep) in tok.items():
    if isinstance(emp,(int,float)) and isinstance(ep,(int,float)) and emp>0:
        freshgap.append((ep/emp-1)*100)
print(f"close_pos_60s n={len(cp)} median={med(cp):.3f} mean={mean(cp):.3f}  near-low(<=0.2): {sum(1 for x in cp if x<=0.2)}/{len(cp)}  mid-or-top(>=0.5): {sum(1 for x in cp if x>=0.5)}/{len(cp)}")
print(f"bars_since_low_60s n={len(bsl)} median={med(bsl):.1f} mean={mean(bsl):.1f}")
print(f"range_pct_60s n={len(rng)} median={med(rng):.2f} mean={mean(rng):.2f}")
print(f"bottom_score n={len(bottom)} median={med(bottom):.3f} mean={mean(bottom):.3f}")
print(f"fresh-fill gap (entry_price vs entry_mid_price) n={len(freshgap)} median={med(freshgap):+.2f}% mean={mean(freshgap):+.2f}%  pos:{sum(1 for x in freshgap if x>0)}/{len(freshgap)}")
# entry vs 60s low estimate: close_pos * range gives % above low. above_low% ~ close_pos_60s * range_pct_60s
abovelow=[]
for cpv,bslv,rngv,bsv,emp,ep in tok.values():
    if isinstance(cpv,(int,float)) and isinstance(rngv,(int,float)):
        abovelow.append(cpv*rngv)
print(f"est %% above 60s-low at DECISION (close_pos*range) n={len(abovelow)} median={med(abovelow):.2f}% mean={mean(abovelow):.2f}%")
