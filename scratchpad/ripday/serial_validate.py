# Split validation + confound checks + economics for top discriminators
import json, statistics as st, os
os.chdir(r"C:\Users\jcole\multichain-bot\scratchpad\ripday")
rows = json.load(open("_serial_rows.json"))
n = len(rows)

# tape coverage check
cov = sum(1 for r in rows if (r.get("tape_n") or 0) > 0)
print(f"tape coverage at first-swing window: {cov}/{n} tokens have ANY trades -> tape features unusable\n")

# age distribution
ages = sorted(r["age_h"] for r in rows if r.get("age_h") is not None)
print(f"age_h at first swing: n={len(ages)} p10={ages[int(.1*len(ages))]:.2f} p25={ages[len(ages)//4]:.2f} "
      f"med={st.median(ages):.2f} p75={ages[3*len(ages)//4]:.2f} p90={ages[int(.9*len(ages))]:.2f} max={max(ages):.1f}")
neg = [a for a in ages if a < 0]
print(f"negative ages (first swing before pool_created_at): {len(neg)}\n")

# correlation of range_mean_60m with age (confound check)
import math
def spearman(xs, ys):
    def rank(v):
        s = sorted(range(len(v)), key=lambda i: v[i]); rk = [0]*len(v)
        for i, j in enumerate(s): rk[j] = i
        return rk
    rx, ry = rank(xs), rank(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a-mx)*(b-my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a-mx)**2 for a in rx) * sum((b-my)**2 for b in ry))
    return num/den if den else 0

both = [r for r in rows if r.get("age_h") is not None and r.get("range_mean_60m") is not None]
print(f"spearman(age_h, range_mean_60m) = {spearman([r['age_h'] for r in both],[r['range_mean_60m'] for r in both]):.2f} (n={len(both)})")
bothp = [r for r in rows if r.get("pre_history_min") is not None and r.get("age_h") is not None]
print(f"spearman(age_h, pre_history_min) = {spearman([r['age_h'] for r in bothp],[r['pre_history_min'] for r in bothp]):.2f} (n={len(bothp)})")

# does range add within young/old strata?
for name, cond in [("age<=1h", lambda r: r.get("age_h") is not None and r["age_h"] <= 1),
                   ("age>1h", lambda r: r.get("age_h") is not None and r["age_h"] > 1),
                   ("age missing", lambda r: r.get("age_h") is None)]:
    sub = [r for r in rows if cond(r)]
    if not sub: continue
    ser = sum(1 for r in sub if r["serial"])
    hi = [r for r in sub if (r.get("range_mean_60m") or 0) >= 12]
    lo = [r for r in sub if r.get("range_mean_60m") is not None and r["range_mean_60m"] < 12]
    print(f"{name:>12}: n={len(sub)} serial={ser/len(sub)*100:.0f}% | range>=12: {sum(1 for r in hi if r['serial'])}/{len(hi)} | range<12: {sum(1 for r in lo if r['serial'])}/{len(lo)}")

# ---- candidate discriminators to validate ----
def D_young(r): return r.get("age_h") is not None and r["age_h"] <= 1.0
def D_range(r): return r.get("range_mean_60m") is not None and r["range_mean_60m"] >= 12.0
def D_retstd(r): return r.get("ret_std_60m") is not None and r["ret_std_60m"] >= 10.0
def D_young_or_range(r): return D_young(r) or D_range(r)
def D_young_and_range(r): return D_young(r) and D_range(r)
def D_bounce(r): return r.get("first_bounce10") is not None and r["first_bounce10"] <= 5  # continuation gate (post-first-swing)

CANDS = [("age_h<=1.0", D_young), ("range60>=12", D_range), ("retstd60>=10", D_retstd),
         ("young OR range", D_young_or_range), ("young AND range", D_young_and_range)]

def stats(sub, D):
    sel = [r for r in sub if D(r)]
    ser_all = sum(1 for r in sub if r["serial"])
    tp = sum(1 for r in sel if r["serial"])
    prec = tp/len(sel) if sel else float("nan")
    rec = tp/ser_all if ser_all else float("nan")
    g = st.mean([r["latch_gross"] for r in sel]) if sel else float("nan")
    net = st.mean([r["latch_gross"] - 2.6*r["latch_n"] for r in sel]) if sel else float("nan")
    return len(sel), prec, rec, g, net

print("\n--- full set + splits (time halves by t0; alternating tokens by pair sort) ---")
rows_t = sorted(rows, key=lambda r: r["t0"])
tmed = rows_t[len(rows_t)//2]["t0"]
h1 = [r for r in rows if r["t0"] < tmed]; h2 = [r for r in rows if r["t0"] >= tmed]
rows_p = sorted(rows, key=lambda r: r["pair"])
a = rows_p[0::2]; b = rows_p[1::2]
print(f"{'gate':<16}{'split':<8}{'n_sel':>5}{'prec':>7}{'rec':>6}{'gross/tok':>10}{'net/tok':>9}")
for name, D in CANDS:
    for sname, sub in [("ALL", rows), ("T1", h1), ("T2", h2), ("tokA", a), ("tokB", b)]:
        k, prec, rec, g, net = stats(sub, D)
        bs = sum(1 for r in sub if r['serial'])/len(sub)
        print(f"{name:<16}{sname:<8}{k:>5}{prec*100:>6.0f}%{rec*100:>5.0f}%{g:>+10.2f}{net:>+9.2f}   (base {bs*100:.0f}%, n={len(sub)})")
    print()

# unconditioned economics per split for reference
for sname, sub in [("ALL", rows), ("T1", h1), ("T2", h2), ("tokA", a), ("tokB", b)]:
    g = st.mean([r["latch_gross"] for r in sub]); net = st.mean([r["latch_gross"] - 2.6*r["latch_n"] for r in sub])
    print(f"uncond {sname}: n={len(sub)} gross/tok={g:+.2f} net/tok={net:+.2f}")

# ---- continuation gate economics: take first swing on all, continue only if fast bounce ----
print("\n--- continuation gate (post-first-swing): keep latch only if first_bounce10<=5min ---")
def latch_pnls(r):
    out = []
    for pnl, how in r["swings"]:
        out.append(pnl)
        if pnl <= 0: break
    return out
base_total = 0; gate_total = 0; base_legs = 0; gate_legs = 0
for r in rows:
    legs = latch_pnls(r)
    base_total += sum(legs); base_legs += len(legs)
    if D_bounce(r):
        gate_total += sum(legs); gate_legs += len(legs)
    else:
        gate_total += legs[0]; gate_legs += 1
print(f"uncond latch: {base_total/n:+.2f} gross/tok, legs/tok {base_legs/n:.2f}, net {(base_total-2.6*base_legs)/n:+.2f}")
print(f"bounce-gated: {gate_total/n:+.2f} gross/tok, legs/tok {gate_legs/n:.2f}, net {(gate_total-2.6*gate_legs)/n:+.2f}")

# what if entry gate = young OR range AND then latch normally: also show tokens/day-ish volume proxy
sel = [r for r in rows if D_young_or_range(r)]
print(f"\nentry-gated (young OR range): {len(sel)}/{n} tokens latched")
