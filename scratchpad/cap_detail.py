import json, statistics as st
from collections import defaultdict, Counter
rows = [json.loads(l) for l in open('scratchpad/_cap_sells.jsonl', encoding='utf-8')]
YOUNG = {'badday_young_absorb', 'badday_young_rt_paper', 'badday_young_rt',
         'badday_young_pump_dip_ab', 'badday_young_moonbag_ab',
         'badday_young_adaptsize_ab', 'badday_young_vsnap_ab'}
def rlabel(r): return r['reason'].split(' ')[0].split(':')[0]
def med(xs):
    xs=[x for x in xs if x is not None]; return st.median(xs) if xs else float('nan')
# reconstruct positions (same as cap_analysis)
pos=defaultdict(list)
for r in rows:
    if r['bot_id'] not in YOUNG: continue
    pos[(r['bot_id'],r['address'],round(r.get('peak_pnl_pct') or 0.0,4))].append(r)
positions=[]
for key,legs in pos.items():
    legs=sorted(legs,key=lambda x:x['time']); peak=key[2]; reasons=[rlabel(l) for l in legs]
    if len(legs)==1: fr=[1.0]
    else:
        if any(rl=='TP1' for rl in reasons):
            rest=len(legs)-1; fr=[0.75 if rl=='TP1' else 0.25/rest for rl in reasons]
        else: fr=[1.0/len(legs)]*len(legs)
    blended=sum(f*l['pnl_pct'] for f,l in zip(fr,legs))
    positions.append(dict(bot=key[0],peak=peak,blended=blended,reasons=reasons,legs=legs))

print('=== [6,12) small-winner positions: what happens to the 25% remainder? ===')
sm=[p for p in positions if 6<=p['peak']<12]
print('n=%d'%len(sm))
print(Counter(tuple(p['reasons']) for p in sm).most_common())
# show the remainder-leg pnl for those with TP1 + a remainder leg
for p in sm[:12]:
    legpnl=[(rlabel(l),round(l['pnl_pct'],1)) for l in p['legs']]
    print('  peak=%.1f blended=%+.1f legs=%s'%(p['peak'],p['blended'],legpnl))

print('\n=== monster [30+] runners: leg breakdown ===')
mon=[p for p in positions if p['peak']>=30]
for p in sorted(mon,key=lambda x:-x['peak'])[:12]:
    legpnl=[(rlabel(l),round(l['pnl_pct'],1)) for l in p['legs']]
    print('  peak=%.1f blended=%+.1f legs=%s'%(p['peak'],p['blended'],legpnl))

print('\n=== trail_reprice_shadow: reason of rows where populated ===')
tr=[r for r in rows if r['bot_id'] in YOUNG and r.get('trail_reprice_shadow_pnl') is not None]
print('n=%d reason mix:'%len(tr), Counter(rlabel(r) for r in tr).most_common())
print('by peak bucket, live vs reprice delta:')
for lo,hi in [(0,6),(6,12),(12,18),(18,30),(30,1e9)]:
    b=[r for r in tr if lo<=(r.get('peak_pnl_pct') or 0)<hi]
    if b:
        print('  peak[%g,%g) n=%d live_med=%.1f reprice_med=%.1f delta_med=%+.1f delta_mean=%+.1f'
              %(lo,hi,len(b),med([r['pnl_pct'] for r in b]),med([r['trail_reprice_shadow_pnl'] for r in b]),
                med([r['trail_reprice_shadow_pnl']-r['pnl_pct'] for r in b]),
                st.mean([r['trail_reprice_shadow_pnl']-r['pnl_pct'] for r in b])))
print('  trail_reprice_shadow_peak sample:', [round(r.get('trail_reprice_shadow_peak') or 0,1) for r in tr[:8]])
