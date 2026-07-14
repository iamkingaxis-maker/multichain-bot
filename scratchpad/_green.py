import json, statistics as st
rows=json.load(open('_pos_rows.json'))
def stat(sub,label):
    if not sub: print(f'{label:40s} n=0'); return None
    p=[r['rpnl'] for r in sub]
    dt=len(set(r['address'] for r in sub))
    ev=sum(p)/len(p); wr=100*sum(1 for x in p if x>0)/len(p)
    print(f'{label:40s} n={len(sub):4d} dt={dt:3d} EV={ev:7.2f} med={st.median(p):7.2f} wr={wr:4.1f}')
    return (len(sub),dt,ev,sum(p))

print('=== pc_h6 buckets (token 6h change at entry) ===')
buckets=[('pc_h6>0 (pump-retrace)',lambda r:r['pc_h6']>0),
         ('-10<pc_h6<=0',lambda r:-10<r['pc_h6']<=0),
         ('-25<pc_h6<=-10',lambda r:-25<r['pc_h6']<=-10),
         ('pc_h6<=-25 (deep flush)',lambda r:r['pc_h6']<=-25)]
for name,f in buckets: stat([r for r in rows if r['pc_h6'] is not None and f(r)],name)

print()
print('=== GREEN-DAY GATE: pc_h6 x sol_pc_h6 ===')
print('-- sol_pc_h6 < 1.5 (calm/down SOL) --')
for name,f in buckets: stat([r for r in rows if r['pc_h6'] is not None and r['sol_pc_h6'] is not None and f(r) and r['sol_pc_h6']<1.5],name+' & solLOW')
print('-- sol_pc_h6 >= 1.5 (SOL green-day) --')
for name,f in buckets: stat([r for r in rows if r['pc_h6'] is not None and r['sol_pc_h6'] is not None and f(r) and r['sol_pc_h6']>=1.5],name+' & solHIGH')

print()
print('=== The proposed +EV cell vs rest ===')
cell=lambda r: r['pc_h6']<=-25 and r['sol_pc_h6']<1.5
stat([r for r in rows if cell(r)],'GATE-PASS: deepflush & solLOW')
stat([r for r in rows if not cell(r)],'GATE-BLOCK: everything else')
