# Combined labeled set: ledger rug_signals stamps (at-entry) + retro features
# (_rh_rug_port.md RQ3, at-entry reconstruction). Label = worst realized trip ret
# from the ledger; retro survivors keep their retro label.
# Each row: sym, label(RUG/LOSS/WIN), top1, top10, shoulder, float, pool, sh_t10, nhold, src
R=[
 # --- ledger-stamped, label from realized worst trip ---
 ('CASHCATWIF','RUG',10.61,50.56,13.09,37.98,11.46,0.259,182,'ledger'),
 ('seedcoin','LOSS',2.1,17.41,10.44,78.14,4.45,0.6,1234,'ledger'),
 ('manhood','WIN',4.68,23.5,11.21,64.96,11.54,0.477,522,'ledger'),
 ('BOW','WIN',3.19,19.4,7.85,73.44,7.16,0.405,2208,'ledger'),
 ('UTILITY','WIN',2.0,14.87,9.72,62.56,22.57,0.654,582,'ledger'),
 ('uhood','WIN',2.94,20.23,12.35,73.94,5.83,0.61,1395,'ledger'),
 ('NASDOG','WIN',3.27,21.43,12.25,69.32,9.25,0.572,950,'ledger'),
 ('Artcoin','WIN',2.56,18.58,12.08,66.36,15.06,0.65,1015,'ledger'),
 ('BROKEBEAR','WIN',7.77,22.7,9.68,64.17,13.13,0.426,775,'ledger'),
 ('Pointless','WIN',2.83,17.79,10.15,76.25,5.96,0.571,1556,'ledger'),
 ('HOODBOT','WIN',1.97,17.7,11.91,71.89,10.41,0.673,730,'ledger'),
 ('DATABEAR','WIN',1.84,12.99,7.28,81.61,5.4,0.56,3423,'ledger'),
 ('Hedge','WIN',2.37,20.04,13.26,68.57,11.39,0.662,1041,'ledger'),
 ('BABYCASHCAT','WIN',2.1,13.65,8.91,75.54,10.81,0.653,1564,'ledger'),
 ('POOCH','WIN',2.85,22.82,13.5,62.9,14.28,0.592,497,'ledger'),
 ('FOX','WIN',1.81,16.89,14.03,75.86,7.25,0.831,1215,'ledger'),
 ('WALLET','WIN',2.75,14.01,9.4,82.46,3.53,0.671,2468,'ledger'),
 ('SUIT','WIN',2.22,17.83,10.87,77.72,4.45,0.61,1491,'ledger'),
 ('spinor','WIN',5.93,22.46,9.97,65.67,11.87,0.444,710,'ledger'),
 ('1c','WIN',1.55,11.35,7.53,76.52,12.13,0.663,697,'ledger'),
 # --- retro-only (unstamped in ledger), from _rh_rug_port.md RQ3 ---
 ('CASHCATGAME','RUG',11.9,22.7,6.9,61.5,15.8,0.30,718,'retro'),
 ('Halp','RUG',1.6,12.1,8.5,63.4,24.5,0.71,177,'retro'),
 ('MONSIEUR','LOSS',2.0,16.4,10.3,74.7,8.8,0.63,835,'retro'),
 ('KUNA','LOSS',2.0,17.1,10.9,64.1,18.8,0.64,452,'retro'),
 ('TREAT','LOSS',2.0,15.9,12.0,66.6,17.5,0.76,449,'retro'),
 ('Ape','WIN',4.4,21.9,13.2,63.2,14.9,0.60,276,'retro'),
 ('RANGER','WIN',5.0,18.3,9.3,74.9,6.8,0.51,965,'retro'),
 ('hehe','WIN',1.9,12.9,6.8,20.5,66.6,0.53,147,'retro'),
 ('BILLY','WIN',5.5,21.3,11.3,25.5,53.2,0.53,86,'retro'),
]
cols=['sym','label','top1','top10','shoulder','float','pool','sh_t10','nhold','src']
def col(name): return cols.index(name)
rugs=[r for r in R if r[1]=='RUG']
wins=[r for r in R if r[1]=='WIN']
loss=[r for r in R if r[1]=='LOSS']
print('SET: %d RUG, %d LOSS, %d WIN (combined ledger+retro)'%(len(rugs),len(loss),len(wins)))
print('RUGS:', [r[0] for r in rugs])
print('WINS:', [r[0] for r in wins])
print()

def evalpred(name, fn):
    cr=sum(1 for r in rugs if fn(r)); crL=[r[0] for r in rugs if fn(r)]
    wk=sum(1 for r in wins if fn(r)); wkL=[r[0] for r in wins if fn(r)]
    lc=sum(1 for r in loss if fn(r))
    print('%-38s catch %d/%d %-28s kill %d/%d %-14s loss-hit %d/%d'%(
        name,cr,len(rugs),str(crL),wk,len(wins),str(wkL),lc,len(loss)))

i=col
print('=== single-feature concentration gates ===')
for t in (8,9,10,12):
    evalpred('top1 >= %d'%t, lambda r,t=t: r[i('top1')]>=t)
for t in (28,30,35,40):
    evalpred('top10 >= %d'%t, lambda r,t=t: r[i('top10')]>=t)
print()
print('=== union concentration gate (dump-class tell) ===')
for t1,t10 in ((9,30),(10,30),(8,28),(10,35)):
    evalpred('top1>=%d OR top10>=%d'%(t1,t10), lambda r,a=t1,b=t10: r[i('top1')]>=a or r[i('top10')]>=b)
print()
print('=== falsified/weak (for the record) ===')
evalpred('nhold < 250 (retro anti-signal)', lambda r: r[i('nhold')]<250)
evalpred('sh_t10 >= 0.6 (fat shoulder)', lambda r: r[i('sh_t10')]>=0.6)
evalpred('float >= 60', lambda r: r[i('float')]>=60)
evalpred('pool < 25', lambda r: r[i('pool')]<25)
