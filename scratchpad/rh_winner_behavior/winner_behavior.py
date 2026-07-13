"""RH winner-wallet BEHAVIOR decode: EXIT / re-entry / breadth on the 91 audited
day-robust pure-ontape winners. Reuses hist_decode.py loader + winner selection,
then reconstructs TRIPS (buys + sells, units-based) to measure exit discipline.
Read-only; writes only under scratchpad/rh_winner_behavior/.
"""
import json, os, glob, time, collections, bisect, statistics as st
from datetime import datetime, timezone

OUT = r"C:\Users\jcole\multichain-bot\scratchpad\rh_history"
TAPES = r"C:\Users\jcole\multichain-bot\scratchpad\robinhood_tapes"
DEST = r"C:\Users\jcole\multichain-bot\scratchpad\rh_winner_behavior"

REG = {}
for line in open(os.path.join(OUT, "pools_registry.jsonl"), encoding="utf-8"):
    d = json.loads(line); REG[d["pool"]] = d

def pt(ts): return datetime.fromisoformat(ts).timestamp()
def day_of(t): return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")
def med(x): return st.median(x) if x else float("nan")
def pctl(x, q):
    if not x: return float("nan")
    xs = sorted(x); i = min(len(xs)-1, int(q*len(xs)))
    return xs[i]

# ---- load tapes (mirror hist_decode) ----
t0 = time.time()
trades_by_pool = {}
hist_pool12 = set()

def load_file(fp):
    rows = []
    for ln in open(fp, encoding="utf-8"):
        try: d = json.loads(ln)
        except Exception: continue
        if d.get("kind") not in ("buy", "sell") or not d.get("pair"): continue
        d["t"] = pt(d["ts"]); rows.append(d)
    return rows

for fp in glob.glob(os.path.join(OUT, "hist_*.jsonl")):
    hist_pool12.add(os.path.basename(fp)[5:-6])
    rows = load_file(fp)
    if rows:
        rows.sort(key=lambda r: (r["t"], r.get("block", 0)))
        trades_by_pool[rows[0]["pair"]] = rows
for fp in glob.glob(os.path.join(TAPES, "tape_*.jsonl")):
    p12 = os.path.basename(fp)[5:-6]
    if p12 in hist_pool12: continue
    rows = load_file(fp)
    if rows:
        pair = rows[0]["pair"]
        if pair in trades_by_pool: continue
        rows.sort(key=lambda r: r["t"]); trades_by_pool[pair] = rows
print(f"[load] {len(trades_by_pool)} pools in {time.time()-t0:.0f}s", flush=True)

# per-pool price array for recent-high / slope lookups
pool_arr = {}
for p, rows in trades_by_pool.items():
    ts = [r["t"] for r in rows]; px = [r.get("px") or 0.0 for r in rows]
    pool_arr[p] = (ts, px)

def recent_high(pool, t, window=600.0):
    ts, px = pool_arr[pool]
    j = bisect.bisect_left(ts, t); i = bisect.bisect_left(ts, t-window)
    prior = [px[k] for k in range(i, j) if px[k] > 0]
    return max(prior) if prior else None

def slope_into(pool, t, window=120.0):
    """price now vs price `window`s earlier (approx). >1 = rising into this ts."""
    ts, px = pool_arr[pool]
    j = bisect.bisect_left(ts, t)
    cur = None
    for k in range(j-1, -1, -1):
        if px[k] > 0: cur = px[k]; break
    i = bisect.bisect_left(ts, t-window)
    prev = None
    for k in range(i, -1, -1):
        if k < len(px) and px[k] > 0: prev = px[k]; break
    if cur and prev and prev > 0: return cur/prev
    return None

# ---- (maker,pool) ledgers with FULL buy+sell events ----
MP = {}
maker_pools = collections.defaultdict(set)
for p, rows in trades_by_pool.items():
    for d in rows:
        m = d["maker"]
        if not m: continue
        k = (m, p)
        r = MP.get(k)
        if r is None:
            r = MP[k] = {"b":0.0,"s":0.0,"nb":0,"ns":0,"ls":None,"ev":[]}
            maker_pools[m].add(p)
        px = d.get("px") or 0.0
        r["ev"].append((d["t"], d["kind"], d["volume_usd"], px))
        if d["kind"] == "buy": r["b"] += d["volume_usd"]; r["nb"] += 1
        else: r["s"] += d["volume_usd"]; r["ns"] += 1; r["ls"] = d["t"]

def classify(r):
    if r["ns"] == 0: return "open"
    if r["b"] == 0: return "sell_only"
    if r["s"] >= 0.7*r["b"]: return "closed"
    return "partial"
for r in MP.values():
    r["net"] = r["s"]-r["b"]; r["cls"] = classify(r)

# ---- reselect audited day-robust pure-ontape winners (mirror hist_decode) ----
maker_realized = collections.defaultdict(list)
for (m, p), r in MP.items():
    if r["cls"] in ("closed", "sell_only"):
        maker_realized[m].append((p, r["net"], r["cls"], day_of(r["ls"]), r))

winners = {}
for m, lst in maker_realized.items():
    pos_pools = [x for x in lst if x[1] > 1.0]
    tot = sum(x[1] for x in lst)
    day_net = collections.defaultdict(float)
    for p, net, cls, day, r in lst: day_net[day] += net
    pos_days = sum(1 for v in day_net.values() if v > 0)
    has_sellonly = any(x[2] == "sell_only" for x in lst)
    mixed = has_sellonly and any(x[2] == "closed" for x in lst)
    cls3 = ("pure_sell_only" if has_sellonly and not mixed
            else "mixed" if mixed else "pure_ontape")
    if len(pos_pools) >= 3 and tot > 0:
        winners[m] = {"tot":tot,"pos_days":pos_days,"cls":cls3}
audited = [m for m,w in winners.items() if w["pos_days"]>=2 and w["cls"]=="pure_ontape"]
print(f"[winners] audited day-robust pure-ontape: {len(audited)}", flush=True)

# ---- TRIP reconstruction (units-based) for audited winners ----
EPS = 0.10   # position considered flat when <=10% of trip peak units
def trips_for(events):
    """events sorted (t,kind,vusd,px). Returns list of trip dicts."""
    trips = []; cur = None; units = 0.0; peak_u = 0.0
    for (t, kind, v, px) in events:
        if px <= 0: continue
        u = v/px
        if kind == "buy":
            if cur is None:
                cur = {"t0":t,"nb":0,"ns":0,"cost":0.0,"proc":0.0,"buys":[],
                       "sells":[],"peak_px":px,"tlast":t,"pool_peak_at_exit":None}
                units = 0.0; peak_u = 0.0
            cur["nb"] += 1; cur["cost"] += v; cur["buys"].append((t,px,v))
            units += u; peak_u = max(peak_u, units); cur["tlast"] = t
            cur["peak_px"] = max(cur["peak_px"], px)
        else:  # sell
            if cur is None: continue  # sell with no open trip (untracked basis) -> skip
            cur["ns"] += 1; cur["proc"] += v; cur["sells"].append((t,px,v))
            units -= u; cur["tlast"] = t
            cur["peak_px"] = max(cur["peak_px"], px)
            if peak_u > 0 and units <= EPS*peak_u:  # trip closed
                cur["t1"] = t; trips.append(cur); cur = None; units = 0.0; peak_u = 0.0
    if cur is not None and cur["ns"] > 0:  # trailing partial close
        cur["t1"] = cur["tlast"]; cur["partial"] = True; trips.append(cur)
    return trips

# collect trip-level metrics for closed trips
hold_m, n_sells_per_trip, scaleout_frac_flags = [], [], []
exit_mult = []          # trip proceeds/cost (realized multiple, closed trips)
exit_px_over_entry = [] # avg sell px / avg buy px
sell_into_strength = [] # per SELL event: rising slope into it?
sell_vs_triphigh = []   # sell px / trip peak px (how close to top)
first_bank_frac = []    # fraction of position (proceeds share) banked on FIRST sell
reentry_pools = 0; reentry_makers = set()
mfe = []                # max favorable excursion: trip peak_px / avg entry px
peaked_under6 = 0; peaked_over6 = 0; peaked_over12 = 0
trip_realized_by_token = collections.defaultdict(list)  # (maker,pool)->[realized$]
reentry_depths = []     # re-entry buy px / prior sell px (deep <1, chase >1)
reentry_net = []; firsttrip_net = []
re_deep_net = []; re_chase_net = []   # depth-split re-entry realized
firsttrip_realized_sum = 0.0; reentry_realized_sum = 0.0
per_maker_trips = collections.defaultdict(int)
tbox_hits = 0; total_closed = 0

for m in audited:
    for p in maker_pools[m]:
        r = MP[(m,p)]
        ev = sorted(r["ev"])
        tr = trips_for(ev)
        per_maker_trips[m] += len(tr)
        prior_last_sell_px = None
        for idx, trip in enumerate(tr):
            closed = not trip.get("partial", False)
            hm = (trip["t1"]-trip["t0"])/60.0
            rnet = trip["proc"]-trip["cost"]
            if idx == 0:
                firsttrip_net.append(rnet)
                if not trip.get("partial"): firsttrip_realized_sum += rnet
            else:
                reentry_pools += 1; reentry_makers.add(m)
                reentry_net.append(rnet)
                if not trip.get("partial"): reentry_realized_sum += rnet
                # depth of re-entry: first buy px of this trip / last sell px of prior trip
                fb_px = trip["buys"][0][1]
                if prior_last_sell_px:
                    d = fb_px/prior_last_sell_px
                    reentry_depths.append(d)
                    (re_deep_net if d < 1.0 else re_chase_net).append(rnet)
            if trip["sells"]:
                prior_last_sell_px = trip["sells"][-1][1]
            if closed:
                total_closed += 1
                hold_m.append(hm)
                n_sells_per_trip.append(trip["ns"])
                scaleout_frac_flags.append(1 if trip["ns"] >= 2 else 0)
                if trip["cost"] > 0:
                    exit_mult.append(trip["proc"]/trip["cost"])
                    trip_realized_by_token[(m,p)].append(trip["proc"]-trip["cost"])
                avg_buy = trip["cost"]/sum(v/px for (_,px,v) in trip["buys"])
                mf = trip["peak_px"]/avg_buy; mfe.append(mf)
                if mf >= 1.12: peaked_over12 += 1; peaked_over6 += 1
                elif mf >= 1.06: peaked_over6 += 1
                else: peaked_under6 += 1
                sold_u = sum(v/px for (_,px,v) in trip["sells"])
                if sold_u > 0:
                    avg_sell = trip["proc"]/sold_u
                    exit_px_over_entry.append(avg_sell/avg_buy)
                if hm <= 22 and hm >= 15: tbox_hits += 1
                # first-sell bank fraction
                if trip["proc"] > 0:
                    first_bank_frac.append(trip["sells"][0][2]/trip["proc"])
                # per-sell strength + vs trip high
                for (ts_, px_, v_) in trip["sells"]:
                    sl = slope_into(p, ts_)
                    if sl is not None: sell_into_strength.append(1 if sl > 1.0 else 0)
                    if trip["peak_px"] > 0: sell_vs_triphigh.append(px_/trip["peak_px"])

# ---- BREADTH: distinct pools with a buy per maker per UTC day ----
maker_day_pools = collections.defaultdict(set)
maker_day_buys = collections.defaultdict(int)
for m in audited:
    for p in maker_pools[m]:
        for (t,kind,v,px) in MP[(m,p)]["ev"]:
            if kind == "buy":
                dk = (m, day_of(t)); maker_day_pools[dk].add(p); maker_day_buys[dk] += 1
breadth_pools = [len(v) for v in maker_day_pools.values()]
breadth_buys = [maker_day_buys[k] for k in maker_day_pools]

# ---- honest cohort: ex-top-2 token-median realized per trip (winner OWN = upper bound) ----
tok_med = []
for (m,p), lst in trip_realized_by_token.items():
    tok_med.append(sum(lst))  # per-token realized (sum of its closed trips)
tok_sorted = sorted(tok_med, reverse=True)
ex2 = tok_sorted[2:] if len(tok_sorted) > 2 else []

res = {
 "n_audited": len(audited),
 "n_closed_trips": total_closed,
 "n_reentry_trips": reentry_pools,
 "pct_makers_reenter": round(100*len(reentry_makers)/max(1,len(audited)),1),
 "EXIT": {
   "hold_m_p25_p50_p75": [round(pctl(hold_m,.25),2),round(med(hold_m),2),round(pctl(hold_m,.75),2)],
   "hold_m_p90": round(pctl(hold_m,.90),2),
   "n_sells_per_trip_p50_p75_p90": [med(n_sells_per_trip),pctl(n_sells_per_trip,.75),pctl(n_sells_per_trip,.90)],
   "scaleout_frac_trips_ge2_sells": round(sum(scaleout_frac_flags)/max(1,len(scaleout_frac_flags)),3),
   "exit_mult_p25_p50_p75_p90": [round(pctl(exit_mult,.25),3),round(med(exit_mult),3),round(pctl(exit_mult,.75),3),round(pctl(exit_mult,.90),3)],
   "exit_px_over_entry_p50_p75_p90": [round(med(exit_px_over_entry),3),round(pctl(exit_px_over_entry,.75),3),round(pctl(exit_px_over_entry,.90),3)],
   "sell_into_strength_pct": round(100*sum(sell_into_strength)/max(1,len(sell_into_strength)),1),
   "sell_vs_triphigh_p25_p50_p75": [round(pctl(sell_vs_triphigh,.25),3),round(med(sell_vs_triphigh),3),round(pctl(sell_vs_triphigh,.75),3)],
   "first_sell_bank_frac_p50": round(med(first_bank_frac),3),
   "tbox_15_22m_hits": tbox_hits, "tbox_share": round(tbox_hits/max(1,total_closed),3),
   "mfe_peak_over_entry_p25_p50_p75_p90": [round(pctl(mfe,.25),3),round(med(mfe),3),round(pctl(mfe,.75),3),round(pctl(mfe,.90),3)],
   "trip_peak_under_plus6_pct": round(100*peaked_under6/max(1,total_closed),1),
   "trip_peak_over_plus6_pct": round(100*peaked_over6/max(1,total_closed),1),
   "trip_peak_over_plus12_pct": round(100*peaked_over12/max(1,total_closed),1),
 },
 "REENTRY": {
   "reentry_depth_px_over_priorsell_p25_p50_p75": [round(pctl(reentry_depths,.25),3),round(med(reentry_depths),3),round(pctl(reentry_depths,.75),3)],
   "reentry_net_median": round(med(reentry_net),2), "reentry_net_mean": round(st.mean(reentry_net),2) if reentry_net else None,
   "firsttrip_net_median": round(med(firsttrip_net),2),
   "n_reentry": len(reentry_net),
   "deep_reentry_below_prior_sell": {"n":len(re_deep_net),"net_median":round(med(re_deep_net),2),"net_mean":round(st.mean(re_deep_net),2) if re_deep_net else None,"net_sum":round(sum(re_deep_net),0)},
   "chase_reentry_above_prior_sell": {"n":len(re_chase_net),"net_median":round(med(re_chase_net),2),"net_mean":round(st.mean(re_chase_net),2) if re_chase_net else None,"net_sum":round(sum(re_chase_net),0)},
   "realized_sum_firsttrip": round(firsttrip_realized_sum,0),
   "realized_sum_reentry": round(reentry_realized_sum,0),
   "reentry_share_of_realized": round(reentry_realized_sum/max(1e-9,firsttrip_realized_sum+reentry_realized_sum),3),
 },
 "BREADTH": {
   "pools_per_maker_day_p50_p75_p90_max": [med(breadth_pools),pctl(breadth_pools,.75),pctl(breadth_pools,.90),max(breadth_pools) if breadth_pools else 0],
   "buys_per_maker_day_p50_p75_p90": [med(breadth_buys),pctl(breadth_buys,.75),pctl(breadth_buys,.90)],
   "n_maker_days": len(breadth_pools),
 },
 "HONEST_COHORT_upperbound": {
   "n_tokens": len(tok_med),
   "token_realized_median": round(med(tok_med),2),
   "token_realized_median_ex_top2": round(med(ex2),2) if ex2 else None,
   "token_realized_sum": round(sum(tok_med),0),
   "top2_share_of_sum": round(sum(tok_sorted[:2])/max(1,sum(tok_med)),3),
 },
 "trips_per_maker_p50_p90": [med(list(per_maker_trips.values())),pctl(list(per_maker_trips.values()),.90)],
}
json.dump(res, open(os.path.join(DEST,"behavior_results.json"),"w"), indent=1)
print(json.dumps(res, indent=1), flush=True)
