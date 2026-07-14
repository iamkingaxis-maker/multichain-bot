"""Stage 4: wallet decode at full-population scale on rh_history hist_* tapes.
Outputs: rh_history/decode_results.json + console report sections.
"""
import sys, json, os, glob, gzip, time, collections, bisect, statistics
from datetime import datetime, timezone

OUT = r"C:\Users\jcole\multichain-bot\scratchpad\rh_history"
REG = {}
for line in open(os.path.join(OUT, "pools_registry.jsonl"), encoding="utf-8"):
    d = json.loads(line)
    REG[d["pool"]] = d

def pt(ts):
    return datetime.fromisoformat(ts).timestamp()

# ---- load hist tapes + v0 recorder tapes (pools not backfilled) ----
TAPES = r"C:\Users\jcole\multichain-bot\scratchpad\robinhood_tapes"
t0 = time.time()
trades_by_pool = {}
pool_source = {}
n_rows = n_nomaker = 0
n_hist_pools = n_rec_pools = 0
hist_pool12 = set()

def load_file(fp):
    rows = []
    for ln in open(fp, encoding="utf-8"):
        try:
            d = json.loads(ln)
        except Exception:
            continue
        if d.get("kind") not in ("buy", "sell") or not d.get("pair"):
            continue
        d["t"] = pt(d["ts"])
        rows.append(d)
    return rows

for fp in glob.glob(os.path.join(OUT, "hist_*.jsonl")):
    hist_pool12.add(os.path.basename(fp)[5:-6])
    rows = load_file(fp)
    n_rows += len(rows)
    n_nomaker += sum(1 for d in rows if not d.get("maker"))
    if rows:
        rows.sort(key=lambda r: (r["t"], r.get("block", 0)))
        trades_by_pool[rows[0]["pair"]] = rows
        pool_source[rows[0]["pair"]] = "hist"
        n_hist_pools += 1
for fp in glob.glob(os.path.join(TAPES, "tape_*.jsonl")):
    p12 = os.path.basename(fp)[5:-6]
    if p12 in hist_pool12:
        continue  # full-history version exists; avoid double counting
    rows = load_file(fp)
    if rows:
        pair = rows[0]["pair"]
        if pair in trades_by_pool:  # paranoia: pair-level dedupe too
            continue
        rows.sort(key=lambda r: r["t"])
        n_rows += len(rows)
        n_nomaker += sum(1 for d in rows if not d.get("maker"))
        trades_by_pool[pair] = rows
        pool_source[pair] = "recorder"
        n_rec_pools += 1
print(f"[load] {len(trades_by_pool)} pools ({n_hist_pools} full-history backfill "
      f"+ {n_rec_pools} 07-10 recorder-only), {n_rows} rows ({n_nomaker} maker-less) "
      f"in {time.time()-t0:.0f}s", flush=True)
all_ts = [r["t"] for rows in trades_by_pool.values() for r in (rows[0], rows[-1])]
print(f"[load] span {datetime.fromtimestamp(min(all_ts), tz=timezone.utc):%Y-%m-%d %H:%M} -> "
      f"{datetime.fromtimestamp(max(all_ts), tz=timezone.utc):%Y-%m-%d %H:%M} UTC", flush=True)

# per-pool arrays for lookback queries
pool_arr = {}
for p, rows in trades_by_pool.items():
    ts = [r["t"] for r in rows]
    sv = [r["volume_usd"] if r["kind"] == "buy" else -r["volume_usd"] for r in rows]
    cum = []
    c = 0.0
    for v in sv:
        c += v; cum.append(c)
    px = [r.get("px") or 0.0 for r in rows]
    pool_arr[p] = (ts, cum, px)

def net_inflow_before(pool, t_entry, window=120.0):
    ts, cum, _ = pool_arr[pool]
    j = bisect.bisect_left(ts, t_entry)      # strictly before entry
    i = bisect.bisect_left(ts, t_entry - window)
    if j <= 0:
        return None  # nothing before entry
    hi = cum[j - 1]
    lo = cum[i - 1] if i > 0 else 0.0
    return hi - lo

def px_vs_high(pool, t_entry, entry_px, window=600.0):
    """entry px / max px over prior `window` secs. None if no prior px."""
    ts, _, px = pool_arr[pool]
    j = bisect.bisect_left(ts, t_entry)
    i = bisect.bisect_left(ts, t_entry - window)
    prior = [px[k] for k in range(i, j) if px[k] > 0]
    if not prior or not entry_px:
        return None
    return entry_px / max(prior)

# ---- (maker,pool) ledgers ----
MP = {}
maker_pools = collections.defaultdict(set)
for p, rows in trades_by_pool.items():
    for d in rows:
        m = d["maker"]
        if not m:
            continue
        k = (m, p)
        r = MP.get(k)
        if r is None:
            r = MP[k] = {"b": 0.0, "s": 0.0, "nb": 0, "ns": 0, "fb": None,
                         "ls": None, "fs": None, "bs": [], "entries": []}
            maker_pools[m].add(p)
        if d["kind"] == "buy":
            r["b"] += d["volume_usd"]; r["nb"] += 1
            r["bs"].append(d["volume_usd"])
            if r["fb"] is None:
                r["fb"] = d["t"]
            r["entries"].append((d["t"], d.get("px") or 0.0, d["volume_usd"]))
        else:
            r["s"] += d["volume_usd"]; r["ns"] += 1
            if r["fs"] is None:
                r["fs"] = d["t"]
            r["ls"] = d["t"]

def classify(r):
    if r["ns"] == 0: return "open"
    if r["b"] == 0: return "sell_only"
    if r["s"] >= 0.7 * r["b"]: return "closed"
    return "partial"

def day_of(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%Y-%m-%d")

for k, r in MP.items():
    r["net"] = r["s"] - r["b"]
    r["cls"] = classify(r)
print(f"[ledger] {len(MP)} (maker,pool) ledgers, {len(maker_pools)} makers", flush=True)

# ---- winners (union-counted, day-robust) ----
maker_realized = collections.defaultdict(list)  # maker -> [(pool, net, cls, day, ledger)]
for (m, p), r in MP.items():
    if r["cls"] in ("closed", "sell_only"):
        maker_realized[m].append((p, r["net"], r["cls"], day_of(r["ls"]), r))

winners, losers = {}, {}
for m, lst in maker_realized.items():
    pos_pools = [x for x in lst if x[1] > 1.0]
    neg_pools = [x for x in lst if x[1] < -1.0]
    tot = sum(x[1] for x in lst)
    day_net = collections.defaultdict(float)
    for p, net, cls, day, r in lst:
        day_net[day] += net
    pos_days = sum(1 for v in day_net.values() if v > 0)
    has_sellonly = any(x[2] == "sell_only" for x in lst)
    mixed = has_sellonly and any(x[2] == "closed" for x in lst)
    cls3 = ("pure_sell_only" if has_sellonly and not mixed
            else "mixed" if mixed else "pure_ontape")
    rec = {"n_pos": len(pos_pools), "n_neg": len(neg_pools), "tot": tot,
           "pos_days": pos_days, "cls": cls3, "n_realized": len(lst),
           "n_pools_all": len(maker_pools[m])}
    if len(pos_pools) >= 3 and tot > 0:
        winners[m] = rec
    elif len(neg_pools) >= 3 and tot < 0:
        losers[m] = rec

W_day = {m: w for m, w in winners.items() if w["pos_days"] >= 2}
by_cls = collections.Counter(w["cls"] for w in W_day.values())
by_cls_all = collections.Counter(w["cls"] for w in winners.values())
print(f"\n[winners] repeat winners (net>+$1 in >=3 pools): {len(winners)} "
      f"| day-robust (net-positive on >=2 UTC days): {len(W_day)}", flush=True)
for c in ("pure_sell_only", "mixed", "pure_ontape"):
    tot = sum(w["tot"] for m, w in W_day.items() if w["cls"] == c)
    tot_a = sum(w["tot"] for m, w in winners.items() if w["cls"] == c)
    print(f"  {c:15s}: day-robust n={by_cls.get(c,0):5d} (${tot:+,.0f}) | "
          f"all n={by_cls_all.get(c,0):5d} (${tot_a:+,.0f})", flush=True)

audited = {m: w for m, w in W_day.items() if w["cls"] == "pure_ontape"}
audited_all = {m: w for m, w in winners.items() if w["cls"] == "pure_ontape"}
print(f"[winners] AUDITED (pure on-tape, day-robust): n={len(audited)} "
      f"(v0 had 24; single-day audited={len(audited_all)})", flush=True)

# ---- audited winner profile vs repeat pure-ontape losers ----
def profile(makers, label):
    ent_strength, ent_dipratio, holds, sizes, ages, per_maker_dip = [], [], [], [], [], []
    for m in makers:
        m_dips = []
        for p, net, cls, day, r in maker_realized[m]:
            if cls != "closed":
                continue
            creation = REG.get(p, {}).get("ts")
            for (te, pxe, sz) in r["entries"]:
                nf = net_inflow_before(p, te)
                if nf is not None:
                    ent_strength.append(1 if nf > 0 else 0)
                dr = px_vs_high(p, te, pxe)
                if dr is not None:
                    ent_dipratio.append(dr)
                    m_dips.append(dr)
                if creation:
                    ages.append((te - creation) / 60.0)
            if r["fb"] and r["ls"]:
                holds.append((r["ls"] - r["fb"]) / 60.0)
            sizes.extend(r["bs"])
        if m_dips:
            per_maker_dip.append(sum(1 for d in m_dips if d < 0.97) / len(m_dips))
    def med(x): return statistics.median(x) if x else float("nan")
    print(f"\n[{label}] n_makers={len(makers)} entries_classified={len(ent_strength)}", flush=True)
    if ent_strength:
        print(f"  strength entries (120s netflow>0): {sum(ent_strength)}/{len(ent_strength)} "
              f"({100*sum(ent_strength)/len(ent_strength):.0f}%)", flush=True)
    if ent_dipratio:
        n_dip = sum(1 for d in ent_dipratio if d < 0.97)
        print(f"  dip entries (px < 97% of 10min high): {n_dip}/{len(ent_dipratio)} "
              f"({100*n_dip/len(ent_dipratio):.0f}%) | median px/high={med(ent_dipratio):.3f}", flush=True)
    print(f"  median hold={med(holds):.1f}m | median buy=${med(sizes):.0f} | "
          f"median entry pool-age={med(ages):.1f}m", flush=True)
    return {"n": len(makers), "strength_pct": (100*sum(ent_strength)/len(ent_strength)) if ent_strength else None,
            "dip_pct": (100*sum(1 for d in ent_dipratio if d < 0.97)/len(ent_dipratio)) if ent_dipratio else None,
            "med_hold_m": med(holds), "med_buy": med(sizes), "med_age_m": med(ages),
            "per_maker_dipshare": per_maker_dip}

prof_w = profile(list(audited), "audited winners")
loser_ontape = [m for m, l in losers.items() if l["cls"] == "pure_ontape"]
prof_l = profile(loser_ontape, "repeat pure-ontape losers")

# dip-buyer winners: audited winners whose entries are MOSTLY dips
dipw = [x for x in prof_w["per_maker_dipshare"] if x >= 0.5]
print(f"\n[dip] audited winners with >=50% dip entries: {len(dipw)}/{len(prof_w['per_maker_dipshare'])}", flush=True)

# top audited winners table
top = sorted(audited.items(), key=lambda kv: -kv[1]["tot"])[:15]
print("\n[top audited day-robust winners]", flush=True)
for m, w in top:
    print(f"  {m[:14]} net=${w['tot']:+,.0f} pos_pools={w['n_pos']}/{w['n_pools_all']} "
          f"pos_days={w['pos_days']}", flush=True)

# ---- rug decode ----
collapsed = []
for p, rows in trades_by_pool.items():
    tot_vol = sum(r["volume_usd"] for r in rows)
    if tot_vol < 500 or len(rows) < 30:
        continue
    ts, _, px = pool_arr[p]
    peak = 0.0; t_coll = None; peak_t = None
    for i in range(len(px)):
        if px[i] <= 0: continue
        if px[i] > peak:
            peak = px[i]; peak_t = ts[i]
        elif peak > 0 and px[i] < 0.3 * peak and t_coll is None and i > 5:
            t_coll = ts[i]
    last_px = next((px[i] for i in range(len(px)-1, -1, -1) if px[i] > 0), 0)
    if t_coll and peak > 0 and last_px < 0.1 * peak:
        collapsed.append((p, t_coll, tot_vol))
print(f"\n[rug] collapsed pools (px -90% from peak, crossed -70%): {len(collapsed)}", flush=True)

pre_sellers = collections.defaultdict(list)  # maker -> [(pool, sell_usd, net, day)]
for p, t_coll, tv in collapsed:
    for d in trades_by_pool[p]:
        if d["kind"] == "sell" and t_coll - 1800 <= d["t"] < t_coll and d["maker"]:
            m = d["maker"]
            r = MP.get((m, p))
            if r and r["net"] > 0 and r["s"] >= 100:
                pre_sellers[m].append((p, d["volume_usd"], r["net"], day_of(d["t"])))
repeat_rug = {}
for m, lst in pre_sellers.items():
    pools = {x[0] for x in lst}
    days = {x[3] for x in lst}
    if len(pools) >= 2 and len(days) >= 2:
        repeat_rug[m] = {"n_rugs": len(pools), "days": sorted(days),
                         "net_from_rugs": round(sum({x[0]: x[2] for x in lst}.values()), 0),
                         "overall_net": round(sum(r["net"] for (mm, p), r in MP.items() if mm == m), 0)}
rr = sorted(repeat_rug.items(), key=lambda kv: -kv[1]["net_from_rugs"])
print(f"[rug] repeat pre-collapse net-positive sellers (>=2 rugs, >=2 days): {len(repeat_rug)}", flush=True)
for m, r in rr[:12]:
    print(f"  {m[:14]} rugs={r['n_rugs']} rug_net=${r['net_from_rugs']:+,.0f} "
          f"overall=${r['overall_net']:+,.0f} days={len(r['days'])}", flush=True)

# ---- sell-only resolution check ----
sell_only_makers = {m for m, lst in maker_realized.items() if any(x[2] == "sell_only" for x in lst)}
resolved = 0
unresolved_usd = resolved_usd = 0.0
for m in sell_only_makers:
    has_buy_anywhere = any(MP[(m, p)]["nb"] > 0 for p in maker_pools[m])
    so_usd = sum(r["s"] for (mm, p), r in MP.items() if mm == m and r["cls"] == "sell_only")
    if has_buy_anywhere:
        resolved += 1; resolved_usd += so_usd
    else:
        unresolved_usd += so_usd
print(f"\n[sell_only] makers with any sell_only ledger: {len(sell_only_makers)} | "
      f"with buys elsewhere on captured pools: {resolved} "
      f"({100*resolved/max(1,len(sell_only_makers)):.0f}%)", flush=True)
print(f"[sell_only] extraction $ by never-buyers: ${unresolved_usd:,.0f} vs "
      f"buyers-elsewhere: ${resolved_usd:,.0f}", flush=True)

json.dump({
    "n_pools": len(trades_by_pool), "n_hist_pools": n_hist_pools,
    "n_recorder_pools": n_rec_pools,
    "n_rows": n_rows, "n_nomaker": n_nomaker,
    "n_makers": len(maker_pools), "n_ledgers": len(MP),
    "winners_all": len(winners), "winners_dayrobust": len(W_day),
    "winners_by_class_dayrobust": dict(by_cls), "winners_by_class_all": dict(by_cls_all),
    "audited_dayrobust_n": len(audited), "audited_all_n": len(audited_all),
    "profile_winners": {k: v for k, v in prof_w.items() if k != "per_maker_dipshare"},
    "profile_losers": {k: v for k, v in prof_l.items() if k != "per_maker_dipshare"},
    "dip_majority_winners": len(dipw),
    "top_audited": [(m, round(w["tot"], 0), w["n_pos"], w["pos_days"]) for m, w in top],
    "collapsed_pools": len(collapsed),
    "repeat_rug_actors": {m: r for m, r in rr},
    "sell_only": {"n": len(sell_only_makers), "resolved": resolved,
                  "unresolved_usd": round(unresolved_usd, 0),
                  "resolved_usd": round(resolved_usd, 0)},
}, open(os.path.join(OUT, "decode_results.json"), "w"), indent=1)
print("\n[decode] wrote decode_results.json", flush=True)
