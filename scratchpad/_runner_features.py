#!/usr/bin/env python3
"""Runner-signature feature extraction.

For each pair with (maker-level tape + 1m bars):
  1. find the max run (low -> peak) inside tape coverage
  2. label: monster (gain>=40%) / regular (8<=gain<=20%) / skip
  3. decision window D = [t5, t5+10min] where t5 = first cross of low*1.05
     reference window R = [t5-10min, t5)
  4. compute tape features in D (and R for arrival baseline)
Output: scratchpad/_runner_features.json + console table.
"""
import json, glob, os, math
from datetime import datetime, timezone

CACHE = "scratchpad/_runner_bars"
WIN_SECS = 600          # decision window length
REF_SECS = 600          # reference window before t5

def iso2unix(s):
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())

def load_tape(path):
    out = []
    for l in open(path, encoding="utf-8"):
        try: t = json.loads(l)
        except: continue
        try: ts = iso2unix(t["ts"])
        except Exception: continue
        out.append((ts, t.get("kind"), float(t.get("volume_usd") or 0), t.get("maker") or ""))
    out.sort(key=lambda x: x[0])
    return out

def find_runs(bars, t0, t1):
    """Segment price into runs (local low -> peak -> fade). bars ascending.
    A run starts when high >= running_min*1.08; it ends when close retraces
    >=60% of the gain or drops back to low*1.02. Returns list of
    (gain_pct, low_ts, low_px, peak_ts, peak_px, end_ts)."""
    seq = [b for b in bars if t0 <= b[0] <= t1 and b[3] > 0]
    if len(seq) < 5:
        return []
    runs = []
    run_min = None; run_min_ts = None
    in_run = False; peak = None; peak_ts = None
    for b in seq:
        ts, hi, lo, cl = b[0], b[2], b[3], b[4]
        if not in_run:
            if run_min is None or lo < run_min:
                run_min = lo; run_min_ts = ts
            if run_min and hi >= run_min * 1.08:
                in_run = True; peak = hi; peak_ts = ts
        else:
            if hi > peak:
                peak = hi; peak_ts = ts
            gain_abs = peak - run_min
            if cl <= peak - 0.6 * gain_abs or cl <= run_min * 1.02:
                runs.append(((peak / run_min - 1) * 100, run_min_ts, run_min,
                             peak_ts, peak, ts))
                in_run = False; run_min = cl; run_min_ts = ts
                peak = None; peak_ts = None
    if in_run and peak is not None:
        runs.append(((peak / run_min - 1) * 100, run_min_ts, run_min,
                     peak_ts, peak, seq[-1][0]))
    return runs

def crossing(bars, low_ts, low_px, mult):
    for b in bars:
        if b[0] < low_ts: continue
        if b[2] >= low_px * mult:
            return b[0]
    return None

def feats(tape, bars, t5, low_px):
    D = [t for t in tape if t5 <= t[0] < t5 + WIN_SECS]
    R = [t for t in tape if t5 - REF_SECS <= t[0] < t5]
    pre_makers = {t[3] for t in tape if t[0] < t5}
    buysD = [t for t in D if t[1] == "buy"]
    sellsD = [t for t in D if t[1] == "sell"]
    bvol = sum(t[2] for t in buysD); svol = sum(t[2] for t in sellsD)
    out = {"n_D": len(D), "n_R": len(R), "buy_vol_D": round(bvol, 0), "sell_vol_D": round(svol, 0)}
    if len(D) < 10 or bvol <= 0:
        out["thin"] = True
        return out
    out["thin"] = False
    # 1. wallet diversity
    mk = {t[3] for t in buysD if t[3]}
    out["makers_per_1k"] = round(len(mk) / max(bvol / 1000.0, 0.001), 2)
    out["n_buyers_D"] = len(mk)
    newm = {m for m in mk if m not in pre_makers}
    out["new_maker_frac"] = round(len(newm) / max(len(mk), 1), 3)
    # 2. flow persistence: 60s sub-windows
    pos = 0; streak = 0; best_streak = 0; nets = []
    for w in range(WIN_SECS // 60):
        a, b = t5 + w * 60, t5 + (w + 1) * 60
        net = (sum(t[2] for t in buysD if a <= t[0] < b)
               - sum(t[2] for t in sellsD if a <= t[0] < b))
        nets.append(net)
        if net > 0:
            pos += 1; streak += 1; best_streak = max(best_streak, streak)
        else:
            streak = 0
    out["pos_windows_10"] = pos
    out["max_streak"] = best_streak
    out["net_ratio_D"] = round((bvol - svol) / max(bvol + svol, 1), 3)
    # late-vs-early flow (absorption proxy: does flow hold late in window)
    half = WIN_SECS // 2
    be = sum(t[2] for t in buysD if t[0] < t5 + half); se = sum(t[2] for t in sellsD if t[0] < t5 + half)
    bl = bvol - be; sl = svol - se
    out["net_ratio_early"] = round((be - se) / max(be + se, 1), 3)
    out["net_ratio_late"] = round((bl - sl) / max(bl + sl, 1), 3)
    # 3. buy-size distribution
    sizes = sorted(t[2] for t in buysD)
    out["med_buy"] = round(sizes[len(sizes) // 2], 1)
    out["buy_share_ge100"] = round(sum(s for s in sizes if s >= 100) / bvol, 3)
    out["buy_share_ge500"] = round(sum(s for s in sizes if s >= 500) / bvol, 3)
    # venue-normalized: median buy in D vs reference window R
    rsizes = sorted(t[2] for t in R if t[1] == "buy")
    med_R = rsizes[len(rsizes) // 2] if rsizes else None
    out["med_buy_rel"] = round(out["med_buy"] / med_R, 2) if med_R else None
    # 5. buys/min acceleration
    bpm_e = sum(1 for t in buysD if t[0] < t5 + half) / (half / 60)
    bpm_l = sum(1 for t in buysD if t[0] >= t5 + half) / (half / 60)
    out["bpm_early"] = round(bpm_e, 1); out["bpm_late"] = round(bpm_l, 1)
    out["bpm_accel"] = round(bpm_l / max(bpm_e, 0.1), 2)
    # vs reference arrival rate
    bpm_R = sum(1 for t in R if t[1] == "buy") / (REF_SECS / 60)
    out["bpm_vs_ref"] = round((len(buysD) / (WIN_SECS / 60)) / max(bpm_R, 0.1), 2)
    # 6. seller concentration
    from collections import defaultdict
    sc = defaultdict(float)
    for t in sellsD: sc[t[3]] += t[2]
    top3 = sum(sorted(sc.values(), reverse=True)[:3])
    out["seller_top3_share"] = round(top3 / max(svol, 1), 3) if svol > 0 else None
    # coverage: tape volume in D vs bar volume in D
    barv = sum(b[5] for b in bars if t5 <= b[0] < t5 + WIN_SECS)
    out["tape_coverage"] = round((bvol + svol) / max(barv, 1), 2)
    return out

results = []
for f in glob.glob(os.path.join(CACHE, "bars_*.json")):
    d = json.load(open(f))
    net, pair, sym = d["net"], d["pair"], d["sym"]
    bars = d["bars"]
    if not bars: continue
    tape_dir = ("scratchpad/ripday/live_tapes" if net == "solana"
                else "scratchpad/robinhood_tapes")
    tp = None
    for k in (8, 10, 12):
        cand = os.path.join(tape_dir, f"tape_{pair[:k]}.jsonl")
        if os.path.exists(cand):
            tp = cand; break
    if not tp: continue
    tape = load_tape(tp)
    if not tape: continue
    t0, t1 = tape[0][0], tape[-1][0]
    for (gain, low_ts, low_px, peak_ts, peak_px, end_ts) in find_runs(bars, t0, t1):
        label = "monster" if gain >= 40 else ("regular" if 8 <= gain <= 20 else "skip")
        if label == "skip": continue
        t5 = crossing(bars, low_ts, low_px, 1.05)
        if t5 is None: continue
        row = {"net": net, "sym": sym, "pair": pair, "gain": round(gain, 1),
               "label": label, "low_ts": low_ts, "t5": t5, "peak_ts": peak_ts,
               "mins_low_to_peak": round((peak_ts - low_ts) / 60, 1)}
        if t5 - 300 < t0 or t5 + WIN_SECS > t1:
            row["label"] = "skip"; row["skip_why"] = "window outside tape"
        else:
            row.update(feats(tape, bars, t5, low_px))
        results.append(row)

json.dump(results, open("scratchpad/_runner_features.json", "w"), indent=1)
lab = [r for r in results if r["label"] in ("monster", "regular") and not r.get("thin", True)]
print(f"pairs processed: {len(results)}, usable labeled: {len(lab)} "
      f"(monster={sum(1 for r in lab if r['label']=='monster')}, regular={sum(1 for r in lab if r['label']=='regular')})")
for r in sorted(results, key=lambda x: -x.get("gain", 0)):
    print(f"{r['net'][:3]} {str(r['sym'])[:12]:<12} gain={r['gain']:>7.1f} {r['label']:<8} "
          f"t5={datetime.fromtimestamp(r['t5'], tz=timezone.utc).strftime('%m-%d %H:%M') if r.get('t5') else '?':<11} "
          f"nD={r.get('n_D','-')} cov={r.get('tape_coverage','-')} why={r.get('skip_why','')}")
