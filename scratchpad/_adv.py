import json, statistics as st
from collections import defaultdict
events=json.load(open('_catch_events.json'))
for e in events:
    e['is_held']= e['peak'] is not None and e['peak']>=8
    e['is_gap'] = e['pnl']<=-15
    e['is_dead']= (e['peak'] is None or e['peak']<3) and e['pnl']<=-10

# 1) THE 15 GAP EVENTS
gaps=[e for e in events if e['is_gap']]
print("=== 15 GAP EVENTS (the entire fat-tail) ===")
print("n_gap=%d distinct_pairs=%d"%(len(gaps),len(set(g['addr'] for g in gaps))))
for g in sorted(gaps,key=lambda x:x['pc_h6']):
    print(" %-6s %s pnl=%6.1f pc_h6=%7.1f pc_h24=%8.1f liq=%8.0f age=%5.1f"%(g['sym'],g['time'][:16],g['pnl'],g['pc_h6'],g['pc_h24'] or -1,g['liq'],g['age'] or -1))

# 2) pc_h6 band gap/held raw COUNTS (not rates)
print("\n=== pc_h6 band RAW COUNTS ===")
for lo,hi in [(None,0),(0,100),(100,300),(300,None)]:
    sub=[e for e in events if e['pc_h6'] is not None and (lo is None or e['pc_h6']>=lo) and (hi is None or e['pc_h6']<hi)]
    print(" [%s,%s) n=%d pairs=%d held=%d dead=%d gap=%d medpnl=%.2f"%(lo,hi,len(sub),len(set(e['addr'] for e in sub)),
        sum(e['is_held'] for e in sub),sum(e['is_dead'] for e in sub),sum(e['is_gap'] for e in sub),st.median([e['pnl'] for e in sub])))

# 3) pc_h6>=300: pair concentration of the winners/positive
print("\n=== pc_h6>=300 cohort per-pair ===")
hi300=[e for e in events if e['pc_h6'] is not None and e['pc_h6']>=300]
bypair=defaultdict(list)
for e in hi300: bypair[e['addr']].append(e)
for addr,g in sorted(bypair.items(),key=lambda kv:-st.median([x['pnl'] for x in kv[1]])):
    print(" %-6s n=%2d medpnl=%6.1f held=%d gap=%d liq=%7.0f"%(g[0]['sym'],len(g),st.median([x['pnl'] for x in g]),sum(x['is_held'] for x in g),sum(x['is_gap'] for x in g),g[0]['liq']))
