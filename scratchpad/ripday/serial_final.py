# Final robustness: concentration, age threshold scan, config-gate conflict
import json, statistics as st, os
os.chdir(r"C:\Users\jcole\multichain-bot\scratchpad\ripday")
rows = json.load(open("_serial_rows.json"))
n = len(rows)

def econ(sel):
    if not sel: return None
    nets = [r["latch_gross"] - 2.6*r["latch_n"] for r in sel]
    g = [r["latch_gross"] for r in sel]
    pos = sum(1 for x in nets if x > 0)
    top = max(nets)
    return dict(n=len(sel), gross=st.mean(g), net=st.mean(nets), med_net=st.median(nets),
                pos_frac=pos/len(sel), top_share=top/sum(nets) if sum(nets) > 0 else float("nan"))

print("--- age_h threshold scan (entry-time gate; missing age = excluded, as live gate would know age) ---")
ser_all = sum(1 for r in rows if r["serial"])
for thr in [0.5, 1.0, 2.0, 3.0, 6.0]:
    sel = [r for r in rows if r.get("age_h") is not None and r["age_h"] <= thr]
    if not sel: continue
    tp = sum(1 for r in sel if r["serial"])
    e = econ(sel)
    print(f"age<={thr:>4}: n={e['n']:3d} prec={tp/e['n']*100:4.0f}% rec={tp/ser_all*100:3.0f}% net/tok={e['net']:+7.2f} med={e['med_net']:+6.2f} posfrac={e['pos_frac']*100:.0f}% topshare={e['top_share']*100:.0f}%")
# and the excluded side
old = [r for r in rows if r.get("age_h") is not None and r["age_h"] > 6]
e = econ(old); tp = sum(1 for r in old if r["serial"])
print(f"age> 6h : n={e['n']:3d} prec={tp/e['n']*100:4.0f}% net/tok={e['net']:+7.2f} med={e['med_net']:+6.2f} posfrac={e['pos_frac']*100:.0f}%")
miss = [r for r in rows if r.get("age_h") is None]
e = econ(miss); tp = sum(1 for r in miss if r["serial"])
print(f"age miss: n={e['n']:3d} prec={tp/e['n']*100:4.0f}% net/tok={e['net']:+7.2f} med={e['med_net']:+6.2f} posfrac={e['pos_frac']*100:.0f}%")

print("\n--- current bot config conflict (age_h_min=6.0, liq>=25k, mcap in [50k,1M]) ---")
def cfg_pass(r):
    return (r.get("age_h") is not None and r["age_h"] >= 6.0
            and (r.get("liq") or 0) >= 25000
            and r.get("mcap") is not None and 50000 <= r["mcap"] <= 1000000)
cp = [r for r in rows if cfg_pass(r)]
e = econ(cp)
if e:
    tp = sum(1 for r in cp if r["serial"])
    print(f"tokens passing current config age/liq/mcap at first swing: n={e['n']} serial={tp} prec={tp/e['n']*100:.0f}% net/tok={e['net']:+.2f}")
ser_young = sum(1 for r in rows if r["serial"] and r.get("age_h") is not None and r["age_h"] < 6)
print(f"serial swingers with age<6h (excluded by current config): {ser_young}/{ser_all}")
ser_liq = [r for r in rows if r["serial"] and r.get("liq") is not None]
print(f"serial with liq>=25k: {sum(1 for r in ser_liq if r['liq']>=25000)}/{len(ser_liq)}; liq med={st.median([r['liq'] for r in ser_liq]):.0f}")
ser_mc = [r for r in rows if r["serial"] and r.get("mcap") is not None]
print(f"serial with mcap>=50k: {sum(1 for r in ser_mc if r['mcap']>=50000)}/{len(ser_mc)}; mcap med={st.median([r['mcap'] for r in ser_mc]):.0f}")

print("\n--- concentration / cohort detail for headline gates ---")
for name, D in [("age<=1h", lambda r: r.get("age_h") is not None and r["age_h"] <= 1.0),
                ("range60>=12", lambda r: r.get("range_mean_60m") is not None and r["range_mean_60m"] >= 12.0),
                ("young AND range", lambda r: r.get("age_h") is not None and r["age_h"] <= 1.0 and (r.get("range_mean_60m") or 0) >= 12.0),
                ("uncond", lambda r: True)]:
    sel = [r for r in rows if D(r)]
    e = econ(sel)
    fw = sum(1 for r in sel if r["first_win"]) / len(sel)
    legs = st.mean([r["latch_n"] for r in sel])
    print(f"{name:<16} n={e['n']:3d} net/tok={e['net']:+7.2f} med_net={e['med_net']:+6.2f} posfrac={e['pos_frac']*100:3.0f}% topshare={e['top_share']*100:3.0f}% firstwin={fw*100:.0f}% legs/tok={legs:.2f}")

# ex-top-token net (drop best token) for age<=1h
sel = [r for r in rows if r.get("age_h") is not None and r["age_h"] <= 1.0]
nets = sorted([r["latch_gross"] - 2.6*r["latch_n"] for r in sel], reverse=True)
print(f"\nage<=1h net/tok ex-top1: {st.mean(nets[1:]):+.2f}  ex-top3: {st.mean(nets[3:]):+.2f}")
sel2 = [r for r in rows if (r.get('range_mean_60m') or 0) >= 12]
nets2 = sorted([r["latch_gross"] - 2.6*r["latch_n"] for r in sel2], reverse=True)
print(f"range>=12 net/tok ex-top1: {st.mean(nets2[1:]):+.2f}  ex-top3: {st.mean(nets2[3:]):+.2f}")

# participation prior check
print("\n--- participation (bars printing) check ---")
for name, D in [("serial", lambda r: r["serial"]), ("other", lambda r: not r["serial"])]:
    sub = [r for r in rows if D(r) and r.get("bars_rate_full") is not None]
    print(f"{name}: bars_rate_full med={st.median([r['bars_rate_full'] for r in sub]):.3f} (n={len(sub)})")
