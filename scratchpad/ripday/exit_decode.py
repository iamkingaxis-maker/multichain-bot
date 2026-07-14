# WINNER EXIT-BEHAVIOR DECODE — realized-core winners (14 wallets) from winners_current.json
# Q1 exit shape / Q2 where they sell + counterfactual under OUR ladder / Q3 +4..+9 band /
# Q4 re-entry / Q5 loss exits. Union-of-entries, pre-window-inventory capped, per (wallet, token).
import json, glob, os, bisect, statistics as st, collections
from datetime import datetime

RIP = os.path.dirname(os.path.abspath(__file__))
W_START = "2026-07-01T00:00:00+00:00"


def iso2ep(s):
    return datetime.fromisoformat(s).timestamp()


win = json.load(open(os.path.join(RIP, "winners_current.json")))
winners_all = set(win["winners"].keys())
core = {w for w, s in win["winners"].items() if s["realized"] > 0}
print("winner wallets:", len(winners_all), "| realized core:", len(core))

# ---------- tapes (dedup identical to build_ledger2) ----------
seen = set()
trades_by_pair = {}
pair_tok = {}
for f in glob.glob(os.path.join(RIP, "live_tapes", "tape_*.jsonl")) + glob.glob(os.path.join(RIP, "tape_*.jsonl")):
    for line in open(f, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            t = json.loads(line)
        except Exception:
            continue
        if t["ts"] < W_START:
            continue
        if t["maker"] not in winners_all:
            continue
        key = (t["pair"], t["ts"], t["maker"], t["kind"], round(t["volume_usd"], 4))
        if key in seen:
            continue
        seen.add(key)
        trades_by_pair.setdefault(t["pair"], []).append(t)
        if t["pair"] not in pair_tok:
            pair_tok[t["pair"]] = (t.get("token", ""), t.get("sym", ""))
try:
    idx = json.load(open(os.path.join(RIP, "tape_index.json")))
    for p, v in idx.items():
        if p in pair_tok and not pair_tok[p][0]:
            pair_tok[p] = (v.get("token", ""), pair_tok[p][1] or v.get("sym", ""))
except Exception:
    pass
for p in trades_by_pair:
    trades_by_pair[p].sort(key=lambda t: t["ts"])
print("winner trades:", len(seen), "on pairs:", len(trades_by_pair))

# ---------- bars ----------
bars_by_pair = {}
for f in glob.glob(os.path.join(RIP, "ohlc2_*.json")):
    try:
        d = json.load(open(f))
        if d.get("pair") and d.get("bars"):
            bars_by_pair.setdefault(d["pair"], []).extend(d["bars"])
    except Exception:
        pass
p12 = {}
for p in set(list(trades_by_pair) + list(bars_by_pair)):
    p12.setdefault(p[:12], p)
for dd in ("_gt_bars", "_gt_bars_b"):
    for f in glob.glob(os.path.join(RIP, dd, "*.json")):
        stem = os.path.basename(f).split(".")[0]
        p = p12.get(stem)
        if not p:
            continue
        try:
            b = json.load(open(f))
            if isinstance(b, list):
                bars_by_pair.setdefault(p, []).extend(b)
            elif isinstance(b, dict) and b.get("bars"):
                bars_by_pair.setdefault(p, []).extend(b["bars"])
        except Exception:
            pass
for p in bars_by_pair:
    u = {int(b[0]): b for b in bars_by_pair[p]}
    bars_by_pair[p] = sorted(u.values(), key=lambda b: b[0])
bar_ts = {p: [b[0] for b in bars_by_pair[p]] for p in bars_by_pair}
print("pairs w/ trades that have bars:", sum(1 for p in trades_by_pair if bars_by_pair.get(p)), "/", len(trades_by_pair))


def px_at(p, ep, max_gap=3600):
    ts = bar_ts.get(p)
    if not ts:
        return None
    i = bisect.bisect_right(ts, ep) - 1
    if i < 0 or ep - ts[i] > max_gap:
        return None
    return bars_by_pair[p][i][4]


def bar_idx(p, ep):
    ts = bar_ts.get(p)
    if not ts:
        return None
    i = bisect.bisect_right(ts, ep) - 1
    return i if i >= 0 else None


# ---------- per (wallet, token) episodes with avg-cost accounting ----------
# token-level dedup: merge pairs of same token per wallet? check collisions first
tok_pairs = collections.defaultdict(set)
for p, (tok, sym) in pair_tok.items():
    if p in trades_by_pair:
        tok_pairs[tok or p].add(p)
multi_pair_toks = {t: ps for t, ps in tok_pairs.items() if len(ps) > 1}
print("tokens spanning >1 pair among winner-traded pairs:", len(multi_pair_toks))

episodes = []  # one dict per (wallet, pair) episode
for p, trades in trades_by_pair.items():
    by_w = collections.defaultdict(list)
    for t in trades:
        by_w[t["maker"]].append(t)
    for w, evs in by_w.items():
        evs.sort(key=lambda t: t["ts"])
        pos_qty = 0.0
        cost = 0.0            # cost basis USD of open position
        buy_usd_total = 0.0
        realized_usd = 0.0
        first_buy = None
        first_buy_px = None
        sells = []            # covered sell records
        buys = []             # buy records
        n_px_miss = 0
        n_sells_precov = 0    # sells before first in-window buy / uncovered part
        peak_px = 0.0         # running max high since first buy (bar-based, updated lazily)
        peak_bar_i = None
        for t in evs:
            ep = iso2ep(t["ts"])
            px = px_at(p, ep)
            if px is None or px <= 0:
                n_px_miss += 1
                continue
            qty = t["volume_usd"] / px
            if t["kind"] == "buy":
                if first_buy is None:
                    first_buy, first_buy_px = ep, px
                    peak_px = px
                    peak_bar_i = bar_idx(p, ep)
                buys.append({"ep": ep, "px": px, "usd": t["volume_usd"], "qty": qty,
                             "pos_before": pos_qty})
                pos_qty += qty
                cost += t["volume_usd"]
                buy_usd_total += t["volume_usd"]
            else:
                if first_buy is None or pos_qty <= 0:
                    n_sells_precov += 1
                    continue
                sqty = min(qty, pos_qty)
                covered_frac_of_print = sqty / qty
                avg_cost_px = cost / pos_qty if pos_qty > 0 else px
                # update running peak (bar highs from peak_bar_i..sell bar)
                bi = bar_idx(p, ep)
                if bi is not None and peak_bar_i is not None and bi >= peak_bar_i:
                    for b in bars_by_pair[p][peak_bar_i:bi + 1]:
                        if b[2] > peak_px:
                            peak_px = b[2]
                    peak_bar_i = bi
                frac = sqty / pos_qty
                pnl = sqty * (px - avg_cost_px)
                realized_usd += pnl
                px5 = px_at(p, ep - 300)
                mom5 = 100 * (px / px5 - 1) if px5 else None
                # local 30m peak (bar highs in [ep-1800, ep])
                vp30 = None
                if bi is not None:
                    j30 = bisect.bisect_left(bar_ts[p], ep - 1800)
                    w30 = bars_by_pair[p][j30:bi + 1]
                    if w30:
                        h30 = max(b[2] for b in w30)
                        if h30 > 0:
                            vp30 = 100 * (px / h30 - 1)
                sells.append({
                    "mom5": mom5, "vs_peak30": vp30,
                    "ep": ep, "px": px, "usd": t["volume_usd"] * covered_frac_of_print,
                    "qty": sqty, "frac_of_pos": frac,
                    "ret_vwap": 100 * (px / avg_cost_px - 1),
                    "ret_first": 100 * (px / first_buy_px - 1),
                    "vs_peak": 100 * (px / peak_px - 1) if peak_px > 0 else None,
                    "mins_since_first_buy": (ep - first_buy) / 60,
                })
                cost -= sqty * avg_cost_px
                pos_qty -= sqty
                if pos_qty < 1e-12:
                    pos_qty, cost = 0.0, 0.0
        if first_buy is None or buy_usd_total < 20.0:
            continue
        lastpx = bars_by_pair[p][-1][4] if bars_by_pair.get(p) else None
        unreal = (pos_qty * lastpx - cost) if (lastpx and pos_qty > 0) else 0.0
        episodes.append({
            "w": w, "pair": p, "tok": pair_tok.get(p, ("", ""))[0], "sym": pair_tok.get(p, ("", ""))[1],
            "core": w in core,
            "buys": buys, "sells": sells, "n_px_miss": n_px_miss, "n_sells_precov": n_sells_precov,
            "buy_usd": buy_usd_total, "realized_usd": realized_usd, "unreal_usd": unreal,
            "first_buy": first_buy, "first_buy_px": first_buy_px,
            "leftover_qty": pos_qty, "leftover_cost": cost,
        })

core_eps = [e for e in episodes if e["core"]]
print("episodes (buy>=20, in-window): all-winner=%d, core=%d" % (len(episodes), len(core_eps)))
print("core wallets w/ episodes:", len({e['w'] for e in core_eps}))

# derived per-episode fields ---------------------------------------------------
for e in episodes:
    p = e["pair"]
    bl = bars_by_pair.get(p, [])
    i0 = bar_idx(p, e["first_buy"])
    e["bars_ok"] = bool(bl) and i0 is not None
    # post-entry peak paths vs first-buy px
    if e["bars_ok"]:
        fb = e["first_buy_px"]
        # 120m window peak (comparable to our giveback-band framing)
        k120 = bisect.bisect_right(bar_ts[p], e["first_buy"] + 7200)
        w120 = bl[i0:k120]
        e["peak120_pct"] = 100 * (max(b[2] for b in w120) / fb - 1) if w120 else None
        e["trough120_pct"] = 100 * (min(b[3] for b in w120) / fb - 1) if w120 else None
        # full-horizon peak (entry -> end of bars)
        wfull = bl[i0:]
        e["peakfull_pct"] = 100 * (max(b[2] for b in wfull) / fb - 1) if wfull else None
    else:
        e["peak120_pct"] = e["trough120_pct"] = e["peakfull_pct"] = None
    # realized pct on covered buy USD (only if meaningfully closed)
    covered_cost = e["buy_usd"] - e["leftover_cost"]
    e["covered_cost"] = covered_cost
    e["realized_pct"] = 100 * e["realized_usd"] / covered_cost if covered_cost > 1 else None
    e["closed_frac"] = covered_cost / e["buy_usd"] if e["buy_usd"] > 0 else 0
    e["is_closed"] = e["closed_frac"] >= 0.8   # >=80% of buy USD exited
    # fwd after each sell
    for s in e["sells"]:
        bi = bar_idx(p, s["ep"])
        if bi is None:
            s["fwd_max60"] = s["fwd_min60"] = None
            continue
        k = bisect.bisect_right(bar_ts[p], s["ep"] + 3600)
        fwd = bl[bi + 1:k]
        s["fwd_max60"] = 100 * (max(b[2] for b in fwd) / s["px"] - 1) if fwd else None
        s["fwd_min60"] = 100 * (min(b[3] for b in fwd) / s["px"] - 1) if fwd else None

# ---------- OUR LADDER counterfactual ----------
# TP1 +6% sell 75% / TP2 +12% sell rest / trail 2pp on runner after TP1 / stop -12 / 240m timestop.
# Worst-case intrabar ordering (low checked before high). Entry = winner first-buy bar close.
def our_ladder(p, entry_ep, entry_px, horizon_s=4 * 3600):
    bl = bars_by_pair.get(p)
    if not bl:
        return None
    i0 = bar_idx(p, entry_ep)
    if i0 is None:
        return None
    k = bisect.bisect_right(bar_ts[p], entry_ep + horizon_s)
    walk = bl[i0 + 1:k]
    if not walk:
        return None
    remaining, realized, peak, tp1 = 1.0, 0.0, 0.0, False
    reason = "timestop"
    for b in walk:
        lo = 100 * (b[3] / entry_px - 1)
        hi = 100 * (b[2] / entry_px - 1)
        cl = 100 * (b[4] / entry_px - 1)
        if not tp1:
            if lo <= -12:
                realized += remaining * (-12)
                remaining = 0.0
                reason = "stop-12"
                break
            if hi >= 6:
                realized += 0.75 * 6
                remaining = 0.25
                tp1 = True
                peak = max(peak, min(hi, 6))  # conservative: peak from TP1 fill fwd
                # same-bar TP2 (only if hi reaches 12)
                if hi >= 12:
                    realized += remaining * 12
                    remaining = 0.0
                    reason = "tp2"
                    break
        else:
            trail_lvl = peak - 2
            if lo <= max(trail_lvl, -12):
                fill = max(trail_lvl, -12)
                realized += remaining * fill
                remaining = 0.0
                reason = "trail" if trail_lvl > -12 else "stop-12"
                break
            if hi >= 12:
                realized += remaining * 12
                remaining = 0.0
                reason = "tp2"
                break
        peak = max(peak, hi)
    if remaining > 0:
        realized += remaining * cl
    return {"cf_pct": realized, "reason": reason}


for e in episodes:
    cf = our_ladder(e["pair"], e["first_buy"], e["first_buy_px"])
    e["cf"] = cf


def q(vals, f):
    vals = sorted(vals)
    return vals[min(len(vals) - 1, int(f * len(vals)))] if vals else None


def dist(name, vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        print("%s: n=0" % name)
        return
    print("%s n=%d p10=%.1f p25=%.1f med=%.1f p75=%.1f p90=%.1f mean=%.1f" % (
        name, len(vals), q(vals, .1), q(vals, .25), st.median(vals), q(vals, .75), q(vals, .9), st.mean(vals)))


SEP = "=" * 70

# ============ Q1 EXIT SHAPE ============
print("\n" + SEP + "\nQ1 EXIT SHAPE (core realized winners)\n" + SEP)
for e in episodes:  # dust-filtered sell view for shape stats (P&L keeps all)
    e["sells_ndust"] = [s for s in e["sells"] if s["usd"] >= 2.0]
sold = [e for e in core_eps if e["sells_ndust"]]
print("core episodes: %d | with >=1 covered sell: %d | open bags (0 sells): %d" % (
    len(core_eps), len(sold), sum(1 for e in core_eps if not e["sells"])))
dist("n_sells/episode", [len(e["sells_ndust"]) for e in sold])
single_full = sum(1 for e in sold if len(e["sells_ndust"]) == 1 and e["sells_ndust"][0]["frac_of_pos"] >= 0.95)
scale_out = sum(1 for e in sold if len(e["sells_ndust"]) >= 2)
one_partial = sum(1 for e in sold if len(e["sells_ndust"]) == 1 and e["sells_ndust"][0]["frac_of_pos"] < 0.95)
print("single full-clip exits: %d | single partial: %d | scale-out (>=2 sells): %d" % (single_full, one_partial, scale_out))
dist("first-sell frac_of_pos", [e["sells_ndust"][0]["frac_of_pos"] * 100 for e in sold])
dist("all-sell frac_of_pos", [s["frac_of_pos"] * 100 for e in sold for s in e["sells_ndust"]])
# spacing between consecutive sells
gaps_t, gaps_px = [], []
for e in sold:
    for a, b in zip(e["sells_ndust"], e["sells_ndust"][1:]):
        gaps_t.append((b["ep"] - a["ep"]) / 60)
        if a["px"] > 0:
            gaps_px.append(100 * (b["px"] / a["px"] - 1))
dist("gap between sells (min)", gaps_t)
dist("px change between consecutive sells (%)", gaps_px)

# per-wallet exit-style table
print("\nper-wallet exit style (core):")
print("%-10s %4s %5s %6s %8s %9s %10s %9s %8s" % ("wallet", "eps", "sold", "scale%", "medsells", "medret1st", "medretvwap", "real$sum", "wr(closed)"))
for w in sorted(core, key=lambda x: -sum(e["realized_usd"] for e in core_eps if e["w"] == x)):
    my = [e for e in core_eps if e["w"] == w]
    ms = [e for e in my if e["sells_ndust"]]
    if not my:
        continue
    allret = [s["ret_vwap"] for e in ms for s in e["sells_ndust"]]
    cl = [e for e in my if e["is_closed"] and e["realized_pct"] is not None]
    print("%-10s %4d %5d %5.0f%% %8s %9s %10s %9.0f %7s" % (
        w[:10], len(my), len(ms),
        100 * sum(1 for e in ms if len(e["sells_ndust"]) >= 2) / len(ms) if ms else 0,
        ("%.1f" % st.median([len(e["sells_ndust"]) for e in ms])) if ms else "-",
        ("%.1f" % st.median([e["sells_ndust"][0]["ret_first"] for e in ms])) if ms else "-",
        ("%.1f" % st.median(allret)) if allret else "-",
        sum(e["realized_usd"] for e in my),
        ("%d/%d" % (sum(1 for e in cl if e["realized_pct"] > 0), len(cl))) if cl else "-"))

# ============ Q2 WHERE THEY SELL ============
print("\n" + SEP + "\nQ2 WHERE THEY SELL (core; per covered sell, usd-weighted noted)\n" + SEP)
all_sells = [s for e in sold for s in e["sells_ndust"]]
dist("sell ret vs entry VWAP (%)", [s["ret_vwap"] for s in all_sells])
dist("sell ret vs first entry (%)", [s["ret_first"] for s in all_sells])
dist("sell px vs running peak since entry (%)", [s["vs_peak"] for s in all_sells])
dist("mom5 into sell (%; + = selling into strength)", [s.get("mom5") for s in all_sells])
dist("sell px vs local 30m peak (%)", [s.get("vs_peak30") for s in all_sells])
vp = [s for s in all_sells if s.get("vs_peak30") is not None]
if vp:
    print("sells within 3%% of LOCAL 30m peak: %d/%d (%.0f%%)" % (
        sum(1 for s in vp if s["vs_peak30"] >= -3), len(vp),
        100 * sum(1 for s in vp if s["vs_peak30"] >= -3) / len(vp)))
mom_pos = [s for s in all_sells if s.get("mom5") is not None]
if mom_pos:
    print("sells with RISING 5m tape (mom5>+1): %d/%d (%.0f%%) | falling (<-1): %d (%.0f%%)" % (
        sum(1 for s in mom_pos if s["mom5"] > 1), len(mom_pos),
        100 * sum(1 for s in mom_pos if s["mom5"] > 1) / len(mom_pos),
        sum(1 for s in mom_pos if s["mom5"] < -1),
        100 * sum(1 for s in mom_pos if s["mom5"] < -1) / len(mom_pos)))
dist("fwd_max60 after sell (%)", [s["fwd_max60"] for s in all_sells])
dist("fwd_min60 after sell (%)", [s["fwd_min60"] for s in all_sells])
# bucket sells vs our TP levels (ret vs vwap)
buck = collections.Counter()
usd_buck = collections.Counter()
for s in all_sells:
    r = s["ret_vwap"]
    b = ("<-12" if r < -12 else "-12..0" if r < 0 else "0..+6" if r < 6 else "+6..+12" if r < 12 else ">=+12")
    buck[b] += 1
    usd_buck[b] += s["usd"]
tot_usd = sum(usd_buck.values())
print("\nsell buckets vs entry VWAP (count | usd share):")
for b in ["<-12", "-12..0", "0..+6", "+6..+12", ">=+12"]:
    print("  %-8s %3d  %5.1f%%" % (b, buck[b], 100 * usd_buck[b] / tot_usd if tot_usd else 0))
# top-tick vs retrace: sell within 2% of running peak = into strength/top; below = retrace sell
near_peak = sum(1 for s in all_sells if s["vs_peak"] is not None and s["vs_peak"] >= -2)
retr = sum(1 for s in all_sells if s["vs_peak"] is not None and s["vs_peak"] < -10)
nn = sum(1 for s in all_sells if s["vs_peak"] is not None)
print("sells within 2%% of running peak: %d/%d (%.0f%%) | >10%% below peak (retrace/capitulation): %d (%.0f%%)" % (
    near_peak, nn, 100 * near_peak / nn, retr, 100 * retr / nn))

# episode realized multiple distribution (closed episodes)
closed = [e for e in core_eps if e["is_closed"] and e["realized_pct"] is not None]
print("\nclosed core episodes (>=80%% of buy USD exited): n=%d" % len(closed))
dist("episode realized %% (closed)", [e["realized_pct"] for e in closed])

# ============ Q2c COUNTERFACTUAL: OUR LADDER on their episodes ============
print("\n" + SEP + "\nQ2c OUR LADDER (+6/75%, +12/25%, trail2pp, stop-12, 240m timestop) on their entries\n" + SEP)
cf_rows = [e for e in core_eps if e["cf"] and e["is_closed"] and e["realized_pct"] is not None]
print("closed core episodes with bar coverage for sim: n=%d" % len(cf_rows))
dist("THEIR realized %", [e["realized_pct"] for e in cf_rows])
dist("OUR ladder cf %", [e["cf"]["cf_pct"] for e in cf_rows])
dist("delta (ours - theirs) pp", [e["cf"]["cf_pct"] - e["realized_pct"] for e in cf_rows])
wins_ours = sum(1 for e in cf_rows if e["cf"]["cf_pct"] > e["realized_pct"])
print("episodes where OUR ladder beats them: %d/%d" % (wins_ours, len(cf_rows)))
print("their total realized on these (usd): %.0f | our-ladder total (same buy usd, single entry at first buy): %.0f" % (
    sum(e["realized_usd"] for e in cf_rows),
    sum(e["cf"]["cf_pct"] / 100 * e["covered_cost"] for e in cf_rows)))
rc = collections.Counter(e["cf"]["reason"] for e in cf_rows)
print("cf exit reasons:", dict(rc))
# also on ALL core episodes (incl. open) for robustness
cf_all = [e for e in core_eps if e["cf"]]
dist("OUR ladder cf % (all core eps)", [e["cf"]["cf_pct"] for e in cf_all])

# ============ Q3 THE +4..+9 BAND ============
print("\n" + SEP + "\nQ3 +4..+9 PEAK BAND (peak120 after their first buy in [+4,+9))\n" + SEP)
def band_report(eps, label):
    rows = [e for e in eps if e["peak120_pct"] is not None]
    band = [e for e in rows if 4 <= e["peak120_pct"] < 9]
    print("%s: eps w/ peak data=%d, in band=%d" % (label, len(rows), len(band)))
    if not band:
        return
    for e in band:
        first_sell = e["sells"][0] if e["sells"] else None
        print("  %-10s w=%s.. peak120=%+5.1f trough120=%+6.1f n_sells=%d 1st-sell@%+5.1f%%(vwap) t=%s realized%%=%s closed=%s unreal=%.0f" % (
            (e["sym"] or "?")[:10], e["w"][:6], e["peak120_pct"], e["trough120_pct"], len(e["sells"]),
            first_sell["ret_vwap"] if first_sell else float("nan"),
            ("%.0fm" % first_sell["mins_since_first_buy"]) if first_sell else "-",
            ("%.1f" % e["realized_pct"]) if e["realized_pct"] is not None else "-", e["is_closed"], e["unreal_usd"]))
    cl = [e for e in band if e["is_closed"] and e["realized_pct"] is not None]
    if cl:
        dist("  band realized %% (closed)", [e["realized_pct"] for e in cl])
        cf = [e for e in cl if e["cf"]]
        if cf:
            dist("  band OUR-ladder cf %%", [e["cf"]["cf_pct"] for e in cf])
band_report(core_eps, "CORE")
band_report([e for e in episodes if not e["core"]], "OTHER 35 WINNERS (corroboration)")

# ============ Q4 RE-ENTRY ============
print("\n" + SEP + "\nQ4 RE-ENTRY after a profitable sell (core)\n" + SEP)
re_rows = []
for e in core_eps:
    evs = sorted([("b", b) for b in e["buys"]] + [("s", s) for s in e["sells"]], key=lambda x: x[1]["ep"])
    last_prof_sell = None
    for kind, ev in evs:
        if kind == "s":
            if ev["ret_vwap"] > 0:
                last_prof_sell = ev
        else:
            if last_prof_sell is not None and ev["ep"] > last_prof_sell["ep"]:
                re_rows.append({
                    "w": e["w"], "sym": e["sym"], "pair": e["pair"],
                    "gap_min": (ev["ep"] - last_prof_sell["ep"]) / 60,
                    "disc_pct": 100 * (ev["px"] / last_prof_sell["px"] - 1),
                    "reentry_ep": ev["ep"], "reentry_px": ev["px"],
                })
                last_prof_sell = None  # count first re-buy after each profitable sell
n_ep_reentry = len({(r["w"], r["pair"]) for r in re_rows})
print("re-entry events (first buy after a profitable sell): %d, in %d episodes / %d sold episodes" % (
    len(re_rows), n_ep_reentry, len(sold)))
dist("re-entry gap (min)", [r["gap_min"] for r in re_rows])
dist("re-entry px vs prior exit px (%)", [r["disc_pct"] for r in re_rows])
# round-2 outcome: forward path from re-entry px (what the re-bought leg saw)
r2max, r2min = [], []
for r in re_rows:
    p = r["pair"]
    bi = bar_idx(p, r["reentry_ep"])
    if bi is None:
        continue
    k = bisect.bisect_right(bar_ts[p], r["reentry_ep"] + 3600)
    fwd = bars_by_pair[p][bi + 1:k]
    if fwd:
        r2max.append(100 * (max(b[2] for b in fwd) / r["reentry_px"] - 1))
        r2min.append(100 * (min(b[3] for b in fwd) / r["reentry_px"] - 1))
dist("round-2 fwd_max60 (%)", r2max)
dist("round-2 fwd_min60 (%)", r2min)

# ============ Q5 LOSS EXITS ============
print("\n" + SEP + "\nQ5 LOSS EXITS (core closed episodes with realized<0)\n" + SEP)
losers = [e for e in core_eps if e["is_closed"] and e["realized_pct"] is not None and e["realized_pct"] < 0]
print("closed losing episodes: %d / %d closed" % (len(losers), len(closed)))
dist("loser realized %", [e["realized_pct"] for e in losers])
dist("loser LAST-sell ret vs vwap (%)", [e["sells"][-1]["ret_vwap"] for e in losers if e["sells"]])
dist("loser worst-sell ret vs vwap (%)", [min(s["ret_vwap"] for s in e["sells"]) for e in losers if e["sells"]])
dist("loser hold to last sell (min)", [e["sells"][-1]["mins_since_first_buy"] for e in losers if e["sells"]])
lb = collections.Counter()
for e in losers:
    r = e["realized_pct"]
    lb["0..-8"] += r > -8
    lb["-8..-16"] += -16 < r <= -8
    lb["-16..-30"] += -30 < r <= -16
    lb["<-30"] += r <= -30
print("loss depth buckets:", dict(lb))
# bag-holds: open/mostly-open episodes currently red
bags = [e for e in core_eps if not e["is_closed"]]
red_bags = [e for e in bags if e["unreal_usd"] + e["realized_usd"] < 0]
print("non-closed core episodes: %d, of which net-red (mark-to-last, UNREALIZED): %d" % (len(bags), len(red_bags)))

# ============ A: USD-WEIGHTED COUNTERFACTUAL + where the money actually is ============
print("\n" + SEP + "\nA: MONEY-WEIGHTED VIEW (all core episodes with covered sells)\n" + SEP)
tot_realized = sum(e["realized_usd"] for e in core_eps)
tot_unreal = sum(e["unreal_usd"] for e in core_eps)
print("core reconstruction: sum realized=$%.0f, sum unreal=$%.0f (winners_current realized sum ~ $1628; delta = accounting method + capped pre-window inventory)" % (tot_realized, tot_unreal))
big = sorted([e for e in core_eps if e["sells"]], key=lambda e: -abs(e["realized_usd"]))[:12]
print("\ntop episodes by |realized usd|:")
print("%-10s %-8s %8s %8s %6s %6s %7s %8s %8s" % ("sym", "wallet", "real$", "buy$", "closed", "nsell", "real%", "cf%", "cfreason"))
for e in big:
    print("%-10s %-8s %8.0f %8.0f %6s %6d %7s %8s %8s" % (
        (e["sym"] or "?")[:10], e["w"][:8], e["realized_usd"], e["buy_usd"], "Y" if e["is_closed"] else "n",
        len(e["sells"]), ("%.1f" % e["realized_pct"]) if e["realized_pct"] is not None else "-",
        ("%.1f" % e["cf"]["cf_pct"]) if e["cf"] else "-", e["cf"]["reason"] if e["cf"] else "-"))
rows_w = [e for e in core_eps if e["sells"] and e["cf"] and e["covered_cost"] > 1]
their_w = sum(e["realized_usd"] for e in rows_w) / sum(e["covered_cost"] for e in rows_w) * 100
ours_w = sum(e["cf"]["cf_pct"] / 100 * e["covered_cost"] for e in rows_w) / sum(e["covered_cost"] for e in rows_w) * 100
print("\nUSD-weighted (covered cost) over %d sold core episodes: THEIRS %+.2f%% vs OUR LADDER %+.2f%%" % (len(rows_w), their_w, ours_w))
# same over all 49 winners for n
rows_aw = [e for e in episodes if e["sells"] and e["cf"] and e["covered_cost"] > 1]
their_aw = sum(e["realized_usd"] for e in rows_aw) / sum(e["covered_cost"] for e in rows_aw) * 100
ours_aw = sum(e["cf"]["cf_pct"] / 100 * e["covered_cost"] for e in rows_aw) / sum(e["covered_cost"] for e in rows_aw) * 100
print("USD-weighted over %d sold ALL-WINNER episodes: THEIRS %+.2f%% vs OUR LADDER %+.2f%%" % (len(rows_aw), their_aw, ours_aw))

# cross-tab: our stop-12 exits vs their outcome on the same episode
print("\ncross-tab (closed core eps): our cf reason vs their realized")
xt = collections.defaultdict(list)
for e in cf_rows:
    xt[e["cf"]["reason"]].append(e["realized_pct"])
for r, vals in sorted(xt.items()):
    print("  cf=%-8s n=%2d their realized med %+6.1f%% mean %+6.1f%% | they were green on %d/%d" % (
        r, len(vals), st.median(vals), st.mean(vals), sum(1 for v in vals if v > 0), len(vals)))
grn = [e for e in cf_rows if e["realized_pct"] > 0]
if grn:
    stopped = sum(1 for e in grn if e["cf"]["reason"] == "stop-12")
    print("  of THEIR green episodes (n=%d), our ladder stopped out -12 on %d (%.0f%%)" % (
        len(grn), stopped, 100 * stopped / len(grn)))

# ============ B: LADDER GRID on winner entries ============
print("\n" + SEP + "\nB: LADDER PARAMETER GRID simulated on winner first-buys (worst-case intrabar)\n" + SEP)
def ladder(p, entry_ep, entry_px, tp1, tp1_frac, tp2, trail, stop, be_arm=None, scratch=None, horizon_s=4 * 3600):
    # be_arm = (arm_at_pct, floor_pct): once prior-bar peak >= arm_at, pre-TP1 stop rises to floor
    # scratch = (mins, thresh_pct): after mins, if TP1 never hit and close < thresh -> exit all at close
    bl = bars_by_pair.get(p)
    if not bl:
        return None
    i0 = bar_idx(p, entry_ep)
    if i0 is None:
        return None
    k = bisect.bisect_right(bar_ts[p], entry_ep + horizon_s)
    walk = bl[i0 + 1:k]
    if not walk:
        return None
    remaining, realized, peak, hit1 = 1.0, 0.0, 0.0, False
    cl = 0.0
    for b in walk:
        lo = 100 * (b[3] / entry_px - 1)
        hi = 100 * (b[2] / entry_px - 1)
        cl = 100 * (b[4] / entry_px - 1)
        if not hit1:
            stop_eff = stop
            if be_arm and peak >= be_arm[0]:
                stop_eff = max(stop, be_arm[1])
            if lo <= stop_eff:
                return realized + remaining * stop_eff
            if scratch and b[0] - entry_ep >= scratch[0] * 60 and cl < scratch[1] and hi < tp1:
                return realized + remaining * cl
            if hi >= tp1:
                realized += tp1_frac * tp1
                remaining = 1.0 - tp1_frac
                hit1 = True
                peak = max(peak, tp1)
                if hi >= tp2:
                    return realized + remaining * tp2
        else:
            lvl = max(peak - trail, stop)
            if lo <= lvl:
                return realized + remaining * lvl
            if hi >= tp2:
                return realized + remaining * tp2
        peak = max(peak, hi)
    return realized + remaining * cl

grid = []
for tp1 in (4, 6, 9, 13):
    for tp1_frac in (0.5, 0.75):
        for tp2 in (12, 20, 30):
            if tp2 <= tp1:
                continue
            for trail in (2, 4, 6):
                for stop in (-8, -12, -18):
                    grid.append((tp1, tp1_frac, tp2, trail, stop))
def eval_grid(eps, label):
    print("\n%s (n=%d episodes w/ bars):" % (label, len(eps)))
    res = []
    for g in grid:
        vals = []
        for e in eps:
            r = ladder(e["pair"], e["first_buy"], e["first_buy_px"], *g)
            if r is not None:
                vals.append(r)
        if len(vals) >= 20:
            res.append((st.mean(vals), st.median(vals), sum(1 for v in vals if v > 0) / len(vals), g, len(vals)))
    res.sort(key=lambda x: -x[0])
    print("%-30s %7s %7s %6s" % ("tp1/frac/tp2/trail/stop", "mean%", "med%", "win%"))
    for m, md, wr, g, n in res[:8]:
        print("tp1=%2d f=%.2f tp2=%2d tr=%d st=%3d  %7.2f %7.2f %5.0f%% n=%d" % (g[0], g[1], g[2], g[3], g[4], m, md, 100 * wr, n))
    # current config for reference
    for m, md, wr, g, n in res:
        if g == (6, 0.75, 12, 2, -12):
            print("CURRENT tp1=6 f=0.75 tp2=12 tr=2 st=-12: mean %.2f med %.2f win %.0f%% n=%d (rank %d/%d)" % (
                m, md, 100 * wr, n, [x[3] for x in res].index(g) + 1, len(res)))
    return res
res_core = eval_grid([e for e in core_eps if e["bars_ok"]], "CORE 14-wallet episodes")
res_all = eval_grid([e for e in episodes if e["bars_ok"]], "ALL 49-winner episodes (corroboration)")
json.dump({"core": [{"g": g, "mean": m, "med": md, "win": wr, "n": n} for m, md, wr, g, n in res_core],
           "all": [{"g": g, "mean": m, "med": md, "win": wr, "n": n} for m, md, wr, g, n in res_all]},
          open(os.path.join(RIP, "exit_grid_results.json"), "w"), indent=1)
# focused comparisons
def show(res, g):
    for m, md, wr, gg, n in res:
        if gg == g:
            print("  %-28s mean %+6.2f med %+6.2f win %3.0f%% n=%d" % (str(g), m, md, 100 * wr, n))
print("\nfocused configs (ALL-winner set):")
for g in [(6, 0.75, 12, 2, -12), (13, 0.5, 30, 2, -12), (13, 0.5, 30, 2, -18), (13, 0.3, 30, 2, -12),
          (9, 0.5, 20, 2, -12), (4, 0.5, 12, 2, -12), (6, 0.5, 20, 4, -12), (13, 0.5, 20, 4, -12)]:
    if g not in [x[3] for x in res_all] and g[1] == 0.3:
        # add tp1_frac=0.3 run on the fly
        vals = [r for e in [e for e in episodes if e["bars_ok"]]
                if (r := ladder(e["pair"], e["first_buy"], e["first_buy_px"], *g)) is not None]
        if vals:
            print("  %-28s mean %+6.2f med %+6.2f win %3.0f%% n=%d" % (str(g), st.mean(vals), st.median(vals),
                  100 * sum(1 for v in vals if v > 0) / len(vals), len(vals)))
        continue
    show(res_all, g)
print("focused configs (CORE set):")
for g in [(6, 0.75, 12, 2, -12), (13, 0.5, 30, 2, -12), (13, 0.5, 30, 2, -18), (9, 0.5, 20, 2, -12)]:
    show(res_core, g)

# BE-arm variants
print("\nBREAKEVEN-ARM variants (arm floor once peak tags arm_at, pre-TP1):")
def run_cfg(eps, tp1, f, tp2, tr, stp, be):
    vals = [r for e in eps if (r := ladder(e["pair"], e["first_buy"], e["first_buy_px"], tp1, f, tp2, tr, stp, be)) is not None]
    if not vals:
        return None
    return (st.mean(vals), st.median(vals), sum(1 for v in vals if v > 0) / len(vals), len(vals))
eps_all = [e for e in episodes if e["bars_ok"]]
eps_core2 = [e for e in core_eps if e["bars_ok"]]
for label, eps in (("ALL", eps_all), ("CORE", eps_core2)):
    for cfg in [
        (6, 0.75, 12, 2, -12, None),
        (6, 0.75, 12, 2, -12, (4, 0)),
        (13, 0.5, 30, 2, -12, None),
        (13, 0.5, 30, 2, -12, (4, 0)),
        (13, 0.5, 30, 2, -12, (4, -2)),
        (13, 0.5, 30, 2, -12, (6, 0)),
        (13, 0.5, 30, 4, -12, (4, 0)),
        (13, 0.3, 30, 2, -12, (4, 0)),
    ]:
        r = run_cfg(eps, *cfg)
        if r:
            print("  %s tp1=%-2d f=%.2f tp2=%-2d tr=%d st=%d be=%-8s mean %+6.2f med %+6.2f win %3.0f%% n=%d" % (
                label, cfg[0], cfg[1], cfg[2], cfg[3], cfg[4], str(cfg[5]), r[0], r[1], 100 * r[2], r[3]))
# FAILED-BOUNCE SCRATCH variants (their actual loss behavior: time-conditioned scratch)
print("\nFAILED-BOUNCE SCRATCH variants (after T min, if TP1 unhit and close<thresh, exit at close):")
def run_cfg2(eps, tp1, f, tp2, tr, stp, be, scr):
    vals = [r for e in eps if (r := ladder(e["pair"], e["first_buy"], e["first_buy_px"], tp1, f, tp2, tr, stp, be, scr)) is not None]
    if not vals:
        return None
    return (st.mean(vals), st.median(vals), sum(1 for v in vals if v > 0) / len(vals), len(vals))
for label, eps in (("ALL", eps_all), ("CORE", eps_core2)):
    for cfg in [
        (6, 0.75, 12, 2, -12, None, None),
        (6, 0.75, 12, 2, -12, None, (30, 1)),
        (13, 0.5, 30, 2, -12, None, (30, 1)),
        (13, 0.5, 30, 2, -18, None, None),
        (13, 0.5, 30, 2, -18, None, (20, 1)),
        (13, 0.5, 30, 2, -18, None, (30, 1)),
        (13, 0.5, 30, 2, -18, None, (45, 1)),
        (13, 0.5, 30, 2, -18, None, (30, -3)),
        (13, 0.5, 30, 2, -25, None, (30, 1)),
    ]:
        r = run_cfg2(eps, *cfg)
        if r:
            print("  %s tp1=%-2d f=%.2f tp2=%-2d tr=%d st=%-3d scr=%-9s mean %+6.2f med %+6.2f win %3.0f%% n=%d" % (
                label, cfg[0], cfg[1], cfg[2], cfg[3], cfg[4], str(cfg[6]), r[0], r[1], 100 * r[2], r[3]))

# band-specific check for the leading BE-arm config
print("\n+4..+9-band episodes under candidate ladders (peak120 in [4,9), ALL winners):")
band_eps = [e for e in eps_all if e["peak120_pct"] is not None and 4 <= e["peak120_pct"] < 9]
for cfg in [(6, 0.75, 12, 2, -12, None), (13, 0.5, 30, 2, -12, None), (13, 0.5, 30, 2, -12, (4, 0))]:
    r = run_cfg(band_eps, *cfg)
    if r:
        print("  tp1=%-2d f=%.2f tp2=%-2d tr=%d st=%d be=%-8s mean %+6.2f med %+6.2f win %3.0f%% n=%d" % (
            cfg[0], cfg[1], cfg[2], cfg[3], cfg[4], str(cfg[5]), r[0], r[1], 100 * r[2], r[3]))

# ============ C: BAND SPLIT 4-6 vs 6-9 ============
print("\n" + SEP + "\nC: band split (peak120)\n" + SEP)
for lo, hi in ((4, 6), (6, 9), (9, 15)):
    rows = [e for e in episodes if e["peak120_pct"] is not None and lo <= e["peak120_pct"] < hi]
    cl = [e for e in rows if e["is_closed"] and e["realized_pct"] is not None]
    fs = [e["sells"][0]["ret_vwap"] for e in rows if e["sells"]]
    cf = [e["cf"]["cf_pct"] for e in rows if e["cf"]]
    print("peak120 [%d,%d): n=%d (core %d) | closed real%%: %s (n=%d) | first-sell@vwap med %s | our-cf med %s" % (
        lo, hi, len(rows), sum(1 for e in rows if e["core"]),
        ("%.1f" % st.median([e["realized_pct"] for e in cl])) if cl else "-", len(cl),
        ("%.1f" % st.median(fs)) if fs else "-",
        ("%.1f" % st.median(cf)) if cf else "-"))

# ============ D: round-2 realized ============
print("\n" + SEP + "\nD: ROUND-2 REALIZED (sells after re-entry vs re-entry px, core)\n" + SEP)
r2_real = []
for r in re_rows:
    e = next(x for x in core_eps if x["w"] == r["w"] and x["pair"] == r["pair"])
    post = [s for s in e["sells"] if s["ep"] > r["reentry_ep"]]
    if not post:
        r2_real.append(None)
        continue
    tq = sum(s["qty"] for s in post)
    ret = sum(s["qty"] * (s["px"] / r["reentry_px"] - 1) for s in post) / tq * 100 if tq > 0 else None
    r2_real.append(ret)
dist("round-2 realized on later sells vs re-entry px (%)", [x for x in r2_real if x is not None])
print("re-entries with no later covered sell (open round-2): %d/%d" % (sum(1 for x in r2_real if x is None), len(r2_real)))

# honesty counters
print("\n--- honesty ---")
print("px-missing events skipped (core eps):", sum(e["n_px_miss"] for e in core_eps))
print("pre-window/uncovered sells excluded (core eps):", sum(e["n_sells_precov"] for e in core_eps))
print("episodes w/o bar coverage:", sum(1 for e in core_eps if not e["bars_ok"]))

json.dump([{k: v for k, v in e.items() if k not in ("buys",)} for e in episodes],
          open(os.path.join(RIP, "exit_decode_rows.json"), "w"), default=str)
print("saved exit_decode_rows.json")
