"""Peel-geometry grid replay — 2026-07-06.

Extends scratchpad/_tp_replay2.py HYBRID method (actual fills kept through TP1;
only the post-TP1 remainder is simulated on GT minute bars) with:
  slice in {0.30, 0.50, 0.75} sold at TP1 (conditional: only TP1 fill < +12)
  runner giveback trail in {5, 8, 12} pp from running peak, hard floor -12
  optional runner max-hold in {none, 3h, 5h} measured from the TP1 fill ts
Wick fills (TP1 fill >= +12) behave exactly as today (realized) in ALL variants.

HONEST FRICTION on the simulated runner leg (post exit-booking-fidelity 19bcb0f):
  decision(bar) price * (1 - slip - ultra_fee) then - flat tx fee.
  slip = 0.703% (live-calibrated overall p50, 149 sell legs),
  ultra platform fee = 0.5% (<24h tokens), tx fee = $0.17 on $100 positions
  => fee = 0.17pp position-level per sim leg.
Applied uniformly to every variant INCLUDING the shipped 0.75/5pp baseline cell,
so cross-variant comparisons are apples-to-apples. The actual TP1 leg keeps its
actual booked fill (it happened) in all variants.

Same conservatism as v2 runner sim: bars strictly after TP1 ts; trail fills at
min(trigger, bar open); running peak updates end-of-bar.
"""
import json
from datetime import datetime

SLIP = 0.703 / 100.0     # calibrated sell slip p50 (overall), per leg
ULTRA = 0.5 / 100.0      # ultra platform fee <24h, per leg
FEE_PP = 0.17            # $0.17 tx fee on $100 position, position-level pp
WICK = 12.0              # conditional threshold: fill >= WICK keeps ladder
FLOOR = -12.0

pos = json.load(open("scratchpad/_tp_positions.json"))
bars_by_pid = {}
for line in open("scratchpad/_tp_bars.jsonl"):
    d = json.loads(line)
    if d.get("bars"):
        bars_by_pid[d["pid"]] = d["bars"]


def frict(pnl_pct):
    """Apply slip+ultra to a runner fill pnl (%); tx fee charged separately."""
    return ((1.0 + pnl_pct / 100.0) * (1.0 - SLIP - ULTRA) - 1.0) * 100.0


def runner(bars, ep, start_ts, peak0, gb, maxhold_s):
    """Trail sim from start_ts. Returns (raw_fill_pnl, how, cov_hours).
    how: 'trail' | 'maxhold' | 'open' (bars exhausted, still open) | None (no bars)."""
    P = lambda x: (x / ep - 1.0) * 100.0
    peak, last_c, seen = peak0, None, False
    last_ts = None
    for ts, o, h, l, c, vol in bars:
        if ts <= start_ts:
            continue
        if maxhold_s is not None and ts > start_ts + maxhold_s:
            # time-stop at deadline: exit at last known close (or this bar open
            # if the deadline falls before any bar was seen)
            if last_c is None:
                return P(o), "maxhold", (ts - start_ts) / 3600.0
            return last_c, "maxhold", (last_ts - start_ts) / 3600.0
        seen = True
        o, h, l, c = P(o), P(h), P(l), P(c)
        last_c, last_ts = c, ts
        trail = max(peak - gb, FLOOR)
        if l <= trail:
            return min(trail, o), "trail", (ts - start_ts) / 3600.0
        peak = max(peak, h)
    if last_c is None:
        return None, None, 0.0
    return last_c, "open", (last_ts - start_ts) / 3600.0


SLICES = [0.30, 0.50, 0.75]
GBS = [5.0, 8.0, 12.0]
HOLDS = [(None, "none"), (3 * 3600, "3h"), (5 * 3600, "5h")]

variants = [(s, g, h, hl) for s in SLICES for g in GBS for h, hl in HOLDS]

rows = []
for p in pos:
    pid = f"{p['bot']}|{p['addr']}|{p['entry_time']}"
    bars = bars_by_pid.get(pid)
    ep = p["entry_price"]
    tp1s = [s for s in p["sells"] if s["reason"].startswith("TP1")]
    row = {"pid": pid, "bot": p["bot"], "token": p["token"], "addr": p["addr"],
           "winner": p["winner"], "entry_time": p["entry_time"],
           "realized": p["realized_pp"], "tp1": bool(tp1s), "has_bars": bool(bars)}
    if not tp1s:
        row["mode"] = "identical"           # pre-TP1 exits untouched by any variant
    elif not bars:
        row["mode"] = "nobars"              # Fatcoin: excluded from ALL columns
    else:
        s1 = tp1s[0]
        fill1 = s1["pnl"]
        row["tp1_fill"] = fill1
        if fill1 >= WICK:
            row["mode"] = "wick"            # conditional: keep ladder = realized
        else:
            row["mode"] = "eligible"
            t1 = datetime.fromisoformat(s1["time"]).timestamp()
            peak0 = max(s1["peak"] or 0.0, fill1)
            row["runner"] = {}
            for gb in GBS:
                for hs, hl in HOLDS:
                    rf, how, cov = runner(bars, ep, t1, peak0, gb, hs)
                    if rf is None:
                        # no bars after TP1: neutral (runner exits at TP1 fill, raw)
                        rf, how = fill1, "nobars_after"
                    row["runner"][f"{gb:.0f}|{hl}"] = {
                        "raw": rf, "net": frict(rf), "how": how, "cov_h": cov}
    rows.append(row)

# corpus accounting on the common comparable set (exclude nobars from everything)
comp = [r for r in rows if r["mode"] != "nobars"]
elig = [r for r in comp if r["mode"] == "eligible"]
realized_total = sum(r["realized"] for r in comp)
n_tokens_all = len({r["addr"] for r in comp})
n_tokens_elig = len({r["addr"] for r in elig})

print(f"corpus: {len(comp)} positions comparable ({len(rows)-len(comp)} nobars-excluded), "
      f"{n_tokens_all} distinct tokens; realized total = {realized_total:+.1f}pp")
print(f"TP1-fired: {sum(1 for r in comp if r['tp1'])}; eligible (fill<+12): "
      f"{len(elig)} positions / {n_tokens_elig} distinct tokens; "
      f"wick (>= +12, unchanged): {sum(1 for r in comp if r['mode']=='wick')}")

# runner coverage from TP1
covs = []
for r in elig:
    rr = r["runner"]["5|none"]
    covs.append(rr["cov_h"])
covs.sort()
print(f"runner bar-coverage from TP1 (h): min {covs[0]:.2f} p10 {covs[int(.1*len(covs))]:.2f} "
      f"med {covs[len(covs)//2]:.2f} max {covs[-1]:.2f}; "
      f">=3h: {sum(1 for c in covs if c>=3)} / >=5h: {sum(1 for c in covs if c>=5)} of {len(covs)}")

results = {}
for sl, gb, hs, hl in variants:
    key = f"s{sl:.2f}/gb{gb:.0f}/{hl}"
    tot = 0.0
    deltas, run_rows = [], []
    n_open = n_maxhold = n_trail = 0
    tok_delta = {}
    for r in comp:
        if r["mode"] != "eligible":
            tot += r["realized"]
            continue
        fill1 = r["tp1_fill"]
        rr = r["runner"][f"{gb:.0f}|{hl}"]
        rf_net = rr["net"]
        # variant pp: slice @ actual TP1 fill + (1-slice) @ net runner + leg fee
        vpp = sl * fill1 + (1.0 - sl) * rf_net - FEE_PP
        tot += vpp
        d = vpp - r["realized"]
        deltas.append(d)
        tok = r["token"] or r["addr"][:6]
        tok_delta[tok] = tok_delta.get(tok, 0.0) + d
        # runner-half outcome: net runner fill vs the TP1 fill it declined to take
        run_rows.append((tok, rf_net, rf_net - fill1, rr["how"]))
        if rr["how"] == "open":
            n_open += 1
        elif rr["how"] == "maxhold":
            n_maxhold += 1
        elif rr["how"] == "trail":
            n_trail += 1
    dwin = sum(1 for d in deltas if d > 0.01)
    dloss = sum(1 for d in deltas if d < -0.01)
    rwin = sum(1 for _, _, rd, _ in run_rows if rd > 0.01)
    rloss = sum(1 for _, _, rd, _ in run_rows if rd < -0.01)
    run_sorted = sorted(run_rows, key=lambda x: x[2])
    tok_sorted = sorted(tok_delta.items(), key=lambda x: x[1])
    results[key] = {
        "total": tot, "delta": tot - realized_total,
        "n_elig": len(deltas), "dwin": dwin, "dloss": dloss,
        "rwin": rwin, "rloss": rloss,
        "n_trail": n_trail, "n_maxhold": n_maxhold, "n_open": n_open,
        "worst_tok": tok_sorted[0], "best_tok": tok_sorted[-1],
        "bot5": run_sorted[:5], "top5": run_sorted[-5:][::-1],
        "tok_delta": tok_sorted,
    }

print(f"\n{'variant':<20} {'total':>9} {'dRealzd':>8} {'runWR':>11} {'dWR':>9} "
      f"{'trail/hold/open':>16} {'worst-token':>22} {'best-token':>22}")
for key, v in results.items():
    print(f"{key:<20} {v['total']:+9.1f} {v['delta']:+8.1f} "
          f"{v['rwin']:>3}-{v['rloss']:<3}({v['rwin']/(v['rwin']+v['rloss']):.0%}) "
          f"{v['dwin']:>3}-{v['dloss']:<3} "
          f"{v['n_trail']:>4}/{v['n_maxhold']}/{v['n_open']:<4} "
          f"{v['worst_tok'][0][:10]:>13} {v['worst_tok'][1]:+6.1f} "
          f"{v['best_tok'][0][:10]:>14} {v['best_tok'][1]:+6.1f}")

json.dump({k: {kk: vv for kk, vv in v.items() if kk != "tok_delta"}
           for k, v in results.items()},
          open("scratchpad/_peel_geom_grid_0706.json", "w"), indent=1, default=str)

# fat-tail detail for the headline cells
print("\n===== fat-tail detail (runner net-fill minus TP1-fill, pp on the runner leg) =====")
for key in ["s0.75/gb5/none", "s0.50/gb5/none", "s0.30/gb5/none",
            "s0.30/gb8/none", "s0.30/gb12/none", "s0.50/gb8/none",
            "s0.30/gb8/3h", "s0.30/gb12/5h", "s0.50/gb12/none"]:
    v = results[key]
    t5 = ", ".join(f"{t[:8]} {rd:+.1f}({how[0]})" for t, _, rd, how in v["top5"])
    b5 = ", ".join(f"{t[:8]} {rd:+.1f}({how[0]})" for t, _, rd, how in v["bot5"])
    print(f"\n{key}: top5 [{t5}]")
    print(f"{'':>{len(key)}}  bot5 [{b5}]")

# half-split (tune-vs-holdout style) for headline cells
print("\n===== half split (entry date) =====")
for key in ["s0.75/gb5/none", "s0.50/gb5/none", "s0.30/gb8/none", "s0.30/gb12/none"]:
    sl, gb, hl = key.split("/")
    sl = float(sl[1:]); gb = float(gb[2:])
    for lbl, lo, hi in [("07-01/02", "2026-07-01", "2026-07-03"),
                        ("07-03+", "2026-07-03", "2026-07-99")]:
        dsum, n = 0.0, 0
        for r in elig:
            if not (lo <= r["entry_time"] < hi):
                continue
            rr = r["runner"][f"{gb:.0f}|{hl}"]
            vpp = sl * r["tp1_fill"] + (1 - sl) * rr["net"] - FEE_PP
            dsum += vpp - r["realized"]; n += 1
        print(f"{key} {lbl}: delta {dsum:+7.1f}pp (n={n})")
