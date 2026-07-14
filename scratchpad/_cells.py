import json, statistics as st, collections
from datetime import datetime, timedelta
rows=json.load(open('_pos_rows.json'))
def ct_hour(ts):
    dt=datetime.fromisoformat(ts.replace('Z','+00:00'))
    ct=dt-timedelta(hours=5)  # CDT July
    return ct.hour
for r in rows:
    r['cthr']=ct_hour(r['buy_time'])

def stat(sub,label):
    if not sub: 
        print(f'{label:32s} n=0'); return
    p=[r['rpnl'] for r in sub]
    dt=len(set(r['address'] for r in sub))
    ev=sum(p)/len(p)
    wr=100*sum(1 for x in p if x>0)/len(p)
    print(f'{label:32s} n={len(sub):4d} dt={dt:3d} EV={ev:7.2f} med={st.median(p):7.2f} wr={wr:4.1f}')

print('=== HOUR OF DAY (CT) ===')
for h in range(24):
    stat([r for r in rows if r['cthr']==h],f'CT {h:02d}:00')
print()
print('=== HOUR BLOCKS (CT) ===')
blocks={'03-08 sleep':range(3,8),'08-13 dead':range(8,13),'13-22 prime':range(13,22),'22-03 late':list(range(22,24))+list(range(0,3))}
for name,hrs in blocks.items():
    stat([r for r in rows if r['cthr'] in hrs],name)
