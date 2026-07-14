import json, statistics as st
from collections import defaultdict
events=json.load(open('_catch_events.json'))
for e in events:
    e['is_held']= e['peak'] is not None and e['peak']>=8
    e['is_gap'] = e['pnl']<=-15
    e['is_dead']= (e['peak'] is None or e['peak']<3) and e['pnl']<=-10

def stats(sub):
    n=len(sub)
    if not n: return "empty"
    return "n=%3d pr=%3d held=%.3f dead=%.3f gap=%.3f medpnl=%6.2f"%(n,len(set(e['addr'] for e in sub)),
        sum(e['is_held'] for e in sub)/n,sum(e['is_dead'] for e in sub)/n,sum(e['is_gap'] for e in sub)/n,st.median([e['pnl'] for e in sub]))

# OUT-OF-SAMPLE: split by date midpoint
dates=sorted(e['time'][:10] for e in events)
mid=dates[len(dates)//2]
print("date range",dates[0],"->",dates[-1],"split at",mid)
early=[e for e in events if e['time'][:10]<mid]
late =[e for e in events if e['time'][:10]>=mid]
print("EARLY n=%d  LATE n=%d"%(len(early),len(late)))
for name,ev in [("EARLY",early),("LATE",late)]:
  print("\n--- %s ---"%name)
  print(" liq<30k   ",stats([e for e in ev if e['liq']<30000]))
  print(" liq 30-80k",stats([e for e in ev if 30000<=e['liq']<80000]))
  print(" pc_h6<0   ",stats([e for e in ev if e['pc_h6']<0]))
  print(" pc_h6 100-300 (POISON)",stats([e for e in ev if 100<=e['pc_h6']<300]))
  print(" pc_h6>=300 (MOONER)    ",stats([e for e in ev if e['pc_h6']>=300]))

# LIVE-ONLY slice check (claim: live-only held collapses 1% vs 15% below/above floor)
# events don't carry live flag; recompute from rows
rows=json.load(open('_catch_rows.json'))
rows=[r for r in rows if not (r['pnl'] is not None and r['pnl']>0 and r['hold'] is not None and r['hold']<10)]
rows=[r for r in rows if r['pnl'] is not None and r['liq'] is not None and r['pc_h6'] is not None]
live=[r for r in rows if r['live']]
print("\n=== LIVE-ONLY rows n=%d pairs=%d ==="%(len(live),len(set(r['addr'] for r in live))))
def rstat(sub):
    n=len(sub)
    if not n: return "empty"
    return "n=%d pr=%d held(peak>=8)=%.3f medpnl=%.2f"%(n,len(set(r['addr'] for r in sub)),sum(1 for r in sub if r['peak'] and r['peak']>=8)/n,st.median([r['pnl'] for r in sub]))
print(" live liq<30k ",rstat([r for r in live if r['liq']<30000]))
print(" live liq>=30k",rstat([r for r in live if r['liq']>=30000]))
