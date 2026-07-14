"""Full realized per-token outcome dump for a wallet (on-chain SOL flow only, no GT).
Categorizes: closed-win / closed-loss / closed-RUG(<=-85%) / OPEN(held bag).
Usage: python scratch_b1z_realized.py <WALLET> [sigs]
"""
import sys, os, json, statistics, datetime
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
import wallet_decode as wd

addr = sys.argv[1]
sigs = int(sys.argv[2]) if len(sys.argv) > 2 else 300
tok = wd.trade_map(addr, sigs)
rows = []
for m, r in tok.items():
    if not r["buys"]:
        continue
    spent = r["spent"]; recv = r["recv"]
    b0 = min(b[0] for b in r["buys"])
    closed = bool(r["sells"])
    ret = (recv / spent - 1) * 100 if spent and closed else None
    rows.append({
        "mint": m, "spent_sol": round(spent, 4), "recv_sol": round(recv, 4),
        "nbuys": len(r["buys"]), "nsells": len(r["sells"]),
        "ret_pct": round(ret, 1) if ret is not None else None,
        "closed": closed, "first_buy": b0,
    })
rows.sort(key=lambda x: x["first_buy"])
fmt = lambda ts: datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%m-%d %H:%M")
print(f"WALLET {addr}  | {len(rows)} distinct traded tokens (sigs={sigs})")
print(f"{'token':12} {'first_buy':12} {'spent':>8} {'recv':>8} {'nb':>3} {'ns':>3} {'ret%':>8}  cat")
tot_spent = tot_recv = 0.0
cats = {"win":0,"loss":0,"rug":0,"open":0}
open_spent = 0.0
realized_sol = 0.0
for r in rows:
    tot_spent += r["spent_sol"]
    if r["closed"]:
        tot_recv += r["recv_sol"]
        realized_sol += (r["recv_sol"] - r["spent_sol"])
        if r["ret_pct"] is not None and r["ret_pct"] <= -85:
            cat = "RUG"; cats["rug"] += 1
        elif r["ret_pct"] is not None and r["ret_pct"] > 0:
            cat = "win"; cats["win"] += 1
        else:
            cat = "loss"; cats["loss"] += 1
    else:
        cat = "OPEN"; cats["open"] += 1; open_spent += r["spent_sol"]
    print(f"{r['mint'][:12]:12} {fmt(r['first_buy']):12} {r['spent_sol']:8.3f} {r['recv_sol']:8.3f} {r['nbuys']:3} {r['nsells']:3} {str(r['ret_pct']):>8}  {cat}")
print()
print(f"CATEGORIES: win={cats['win']} loss={cats['loss']} RUG(<=-85%)={cats['rug']} OPEN(unsold bag)={cats['open']}")
n_closed = cats['win']+cats['loss']+cats['rug']
if n_closed:
    print(f"  closed WR = {cats['win']}/{n_closed} = {cats['win']/n_closed:.0%}")
    print(f"  closed RUG rate = {cats['rug']}/{n_closed} = {cats['rug']/n_closed:.0%}")
print(f"TOTAL spent={tot_spent:.2f} SOL | recv(closed)={tot_recv:.2f} SOL | realized P&L(closed)={realized_sol:+.2f} SOL")
print(f"OPEN bag cost basis still at risk = {open_spent:.2f} SOL across {cats['open']} tokens")
closed_rets = [r["ret_pct"] for r in rows if r["closed"] and r["ret_pct"] is not None]
if closed_rets:
    cr = sorted(closed_rets)
    print(f"closed ret%: min {cr[0]:+.0f} | p25 {cr[len(cr)//4]:+.0f} | med {statistics.median(cr):+.0f} | p75 {cr[3*len(cr)//4]:+.0f} | max {cr[-1]:+.0f}")
    wins = [r for r in closed_rets if r > 0]
    print(f"  sum of winning rets {sum(wins):+.0f}pp across {len(wins)} | top3 winners {sorted(wins)[-3:]}")
