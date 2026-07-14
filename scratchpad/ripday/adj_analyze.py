# Revival adjudication -- analysis. H1 (real edge, lumpy monetization) vs H2 (mark mirage).
import json, os, glob, bisect, statistics as st, collections
from datetime import datetime, timezone

RIP = r"C:\Users\jcole\multichain-bot\scratchpad\ripday"
WIN_END = datetime(2026, 7, 4, 4, 26, tzinfo=timezone.utc).timestamp()
NOW = datetime.now(timezone.utc).timestamp()

def iso2ep(s): return datetime.fromisoformat(s).timestamp()

eps = json.load(open(os.path.join(RIP, "_revival_eps_0703.json")))
SYM = {}
for e in eps:
    s = (e.get("sym") or "?").encode("ascii", "replace").decode()
    SYM.setdefault(e["pair"], s)
REV_PAIRS = sorted(SYM.keys())
WINNERS = sorted({e["w"] for e in eps})
print("eps=%d tokens=%d wallets=%d" % (len(eps), len(REV_PAIRS), len(WINNERS)))

# ---- current marks (DexScreener) ----
ds = json.load(open(os.path.join(RIP, "_adj_ds_now.json")))
ds_now = {}
for p in ds["pairs"]:
    ds_now[p["pairAddress"]] = dict(
        px=float(p["priceUsd"]), liq=(p.get("liquidity") or {}).get("usd"),
        vol24=(p.get("volume") or {}).get("h24"), pc24=(p.get("priceChange") or {}).get("h24"),
        sym=p["baseToken"]["symbol"])
print("ds fetched_at:", ds["fetched_at"])

# ---- GT hourly bars (sort asc) ----
gt = {}
for f in glob.glob(os.path.join(RIP, "_adj_gt_hour", "*.json")):
    bl = json.load(open(f))
    bl.sort(key=lambda b: b[0])
    gt[os.path.basename(f)[:-5]] = bl
def bars_for(pair): return gt.get(pair[:12])
def px_at(pair, ts):
    bl = bars_for(pair)
    if not bl: return None
    xs = [b[0] for b in bl]
    i = bisect.bisect_right(xs, ts) - 1
    if i < 0: return None
    return bl[i][4]

# =========================================================
# TEST 1 -- BAG MARKS NOW (mark-to-market, labeled as MARKS)
# =========================================================
print("\n===== TEST 1: BAG MARKS NOW (marks, not realized) =====")
rows = []
for e in eps:
    p = e["pair"]; nowp = ds_now.get(p)
    if not nowp or not e.get("buy_vwap"): continue
    fs = min(max(e.get("frac_sold") or 0.0, 0.0), 1.0)
    mark_ratio_now = nowp["px"] / e["buy_vwap"]
    unreal_now = (1 - fs) * e["buy_usd"] * mark_ratio_now
    net_now = e["realized"] + unreal_now
    rem_val_meas = e["net"] - e["realized"]
    mark_px_meas = (rem_val_meas / ((1 - fs) * e["buy_usd"]) * e["buy_vwap"]) if (fs < 1 and e["buy_usd"]) else None
    rows.append(dict(pair=p, w=e["w"], buy_usd=e["buy_usd"], ret_meas=e["ret"],
                     net_meas=e["net"], realized=e["realized"], fs=fs,
                     ret_now=100 * net_now / e["buy_usd"], net_now=net_now,
                     px_vs_entry_now=100 * (mark_ratio_now - 1),
                     mark_px_meas=mark_px_meas, px_now=nowp["px"], buy_vwap=e["buy_vwap"]))
tot_buy = sum(r["buy_usd"] for r in rows)
tot_net_meas = sum(r["net_meas"] for r in rows)
tot_net_now = sum(r["net_now"] for r in rows)
tot_realized = sum(r["realized"] for r in rows)
print("episodes valued n=%d, buy $%.0f" % (len(rows), tot_buy))
print("aggregate ret: at-measurement %+.1f%% (MARKS) -> NOW %+.1f%% (MARKS) | window matched realized %+.1f%%"
      % (100 * tot_net_meas / tot_buy, 100 * tot_net_now / tot_buy, 100 * tot_realized / tot_buy))
print("per-ep median ret: meas %+.1f%% -> now %+.1f%% | med px-now-vs-entry %+.1f%% | eps green now: %d/%d"
      % (st.median([r["ret_meas"] for r in rows]), st.median([r["ret_now"] for r in rows]),
         st.median([r["px_vs_entry_now"] for r in rows]),
         sum(1 for r in rows if r["net_now"] > 0), len(rows)))

print("\nper-token (med across eps):")
tok_summary = {}
for p in REV_PAIRS:
    rr = [r for r in rows if r["pair"] == p]
    if not rr:
        print(" %-10s NO ds/eps" % SYM[p]); continue
    nowp = ds_now[p]
    mm = [r["mark_px_meas"] for r in rr if r["mark_px_meas"]]
    px_vs_meas = 100 * (nowp["px"] / st.median(mm) - 1) if mm else None
    tok_summary[p] = dict(
        sym=SYM[p], n=len(rr),
        px_vs_entry=st.median([r["px_vs_entry_now"] for r in rr]),
        ret_meas=st.median([r["ret_meas"] for r in rr]),
        ret_now=st.median([r["ret_now"] for r in rr]),
        px_vs_meas=px_vs_meas, liq=nowp["liq"], vol24=nowp["vol24"])
    t = tok_summary[p]
    print(" %-10s n=%2d | px-vs-entry %+7.1f%% | ret %+6.1f -> %+6.1f | vs meas-mark %s | liq $%.0fk vol24 $%.0fk"
          % (t["sym"], t["n"], t["px_vs_entry"], t["ret_meas"], t["ret_now"],
             ("%+.1f%%" % t["px_vs_meas"]) if t["px_vs_meas"] is not None else "n/a",
             (t["liq"] or 0) / 1e3, (t["vol24"] or 0) / 1e3))
tl = list(tok_summary.values())
print("TOKEN-LEVEL med: px-vs-entry %+.1f%%, ret_now %+.1f%%, tokens green-vs-entry %d/%d, liq med $%.0fk"
      % (st.median([t["px_vs_entry"] for t in tl]), st.median([t["ret_now"] for t in tl]),
         sum(1 for t in tl if t["px_vs_entry"] > 0), len(tl),
         st.median([t["liq"] or 0 for t in tl]) / 1e3))

# =========================================================
# TEST 2 -- DID THEY BANK? post-window winner-wallet activity
# =========================================================
print("\n===== TEST 2: POST-WINDOW BANKING (matched realized, union entries; px from hourly bars = approx) =====")
def load_trades(pair):
    seen = set(); out = []
    srcs = [os.path.join(RIP, "live_tapes", "tape_%s.jsonl" % pair[:8]),
            os.path.join(RIP, "tape_%s.jsonl" % pair[:8])]
    for s in srcs:
        if not os.path.exists(s): continue
        for line in open(s, encoding="ascii", errors="replace"):
            try: t = json.loads(line)
            except Exception: continue
            if t.get("pair") and t["pair"] != pair: continue
            k = (t["ts"], t.get("maker", ""), t["volume_usd"], t["kind"])
            if k in seen: continue
            seen.add(k); out.append(t)
    for line in open(os.path.join(RIP, "_adj_fresh_trades.jsonl"), encoding="ascii"):
        t = json.loads(line)
        if t["pair"] != pair: continue
        k = (t["ts"], t.get("maker", ""), t["volume_usd"], t["kind"])
        if k in seen: continue
        seen.add(k); out.append(t)
    for t in out: t["ep"] = iso2ep(t["ts"])
    out.sort(key=lambda t: t["ep"])
    return out

WSET = set(WINNERS)
led = json.load(open(os.path.join(RIP, "ledger3_wallets.json")))
basis = {}
for e in eps:
    if not e.get("buy_vwap"): continue
    fs = min(max(e.get("frac_sold") or 0.0, 0.0), 1.0)
    tok_b = e["buy_usd"] / e["buy_vwap"]
    b = basis.setdefault((e["w"], e["pair"]), dict(tok_rem=0.0, buy_usd=0.0, tok_b=0.0))
    b["tok_rem"] += tok_b * (1 - fs); b["buy_usd"] += e["buy_usd"]; b["tok_b"] += tok_b
extra = 0
for w in WINNERS:
    for e in led.get(w, []):
        if e["pair"] in SYM and e.get("day") == "2026-07-04" and iso2ep(e["first_buy"]) < WIN_END:
            vw = None
            if e.get("buy_usd") and e.get("buy_ts"):
                pxs = [px_at(e["pair"], iso2ep(ts)) for ts in e["buy_ts"]]
                if all(pxs):
                    toks = sum(u / p_ for u, p_ in zip(e["buy_usd_list"], pxs))
                    vw = e["buy_usd"] / toks if toks else None
            if vw:
                fs = min(max(e.get("frac_sold") or 0.0, 0.0), 1.0)
                tok_b = e["buy_usd"] / vw
                b = basis.setdefault((w, e["pair"]), dict(tok_rem=0.0, buy_usd=0.0, tok_b=0.0))
                b["tok_rem"] += tok_b * (1 - fs); b["buy_usd"] += e["buy_usd"]; b["tok_b"] += tok_b
                extra += 1
print("basis wallet-tokens: %d (+%d from 07-04 pre-close ledger eps)" % (len(basis), extra))

post = collections.defaultdict(lambda: dict(sell_usd=0.0, sell_tok=0.0, buy_usd=0.0, buy_tok=0.0,
                                            n_sell=0, n_buy=0, first=None, last=None))
tape_cov = {}
for p in REV_PAIRS:
    tr = load_trades(p)
    win_tr = [t for t in tr if t["ep"] > WIN_END]
    ts_sorted = [WIN_END] + [t["ep"] for t in win_tr] + [NOW]
    gaps = [bb - a for a, bb in zip(ts_sorted, ts_sorted[1:])]
    tape_cov[p] = dict(n=len(win_tr), max_gap_h=max(gaps) / 3600 if gaps else 23.5)
    for t in win_tr:
        if t.get("maker") not in WSET: continue
        px = px_at(p, t["ep"])
        if not px: continue
        d = post[(t["maker"], p)]
        tok = t["volume_usd"] / px
        if t["kind"] == "sell":
            d["sell_usd"] += t["volume_usd"]; d["sell_tok"] += tok; d["n_sell"] += 1
        else:
            d["buy_usd"] += t["volume_usd"]; d["buy_tok"] += tok; d["n_buy"] += 1
        d["first"] = d["first"] or t["ts"]; d["last"] = t["ts"]
print("tape coverage post-window (max gap hours):")
for p in REV_PAIRS:
    print("  %-10s n=%5d max_gap=%.1fh" % (SYM[p], tape_cov[p]["n"], tape_cov[p]["max_gap_h"]))

bank_rows = []
for (w, p), d in post.items():
    b = basis.get((w, p))
    tot_cost = (b["buy_usd"] if b else 0.0) + d["buy_usd"]
    tot_tok = (b["tok_b"] if b else 0.0) + d["buy_tok"]
    vwap_u = tot_cost / tot_tok if tot_tok else None
    tok_avail = (b["tok_rem"] if b else 0.0) + d["buy_tok"]
    sell_matched_tok = min(d["sell_tok"], tok_avail) if tok_avail else 0.0
    sell_vwap = d["sell_usd"] / d["sell_tok"] if d["sell_tok"] else None
    realized_pw = (sell_matched_tok * (sell_vwap - vwap_u)) if (sell_vwap and vwap_u) else 0.0
    bank_rows.append(dict(w=w, pair=p, sym=SYM[p], sell_usd=d["sell_usd"], buy_usd=d["buy_usd"],
                          n_sell=d["n_sell"], n_buy=d["n_buy"], sell_vwap=sell_vwap, vwap_u=vwap_u,
                          sell_vs_entry=100 * (sell_vwap / vwap_u - 1) if (sell_vwap and vwap_u) else None,
                          realized_pw=realized_pw, had_bag=bool(b and b["tok_rem"] > 0)))
act_w = {r["w"] for r in bank_rows}
sellers = [r for r in bank_rows if r["n_sell"] > 0]
print("\npost-window activity: %d wallet-token pairs active (%d wallets of %d); sellers=%d"
      % (len(bank_rows), len(act_w), len(WINNERS), len(sellers)))
if sellers:
    tot_r = sum(r["realized_pw"] for r in sellers)
    tot_s = sum(r["sell_usd"] for r in sellers)
    sv = [r["sell_vs_entry"] for r in sellers if r["sell_vs_entry"] is not None]
    print("POST-WINDOW MATCHED REALIZED (deciding number): $%+.0f on $%.0f sold (%+.1f%% of sold USD)"
          % (tot_r, tot_s, 100 * tot_r / tot_s if tot_s else 0))
    if sv:
        print("sell VWAP vs union entry VWAP: med %+.1f%%, above-entry sellers %d/%d"
              % (st.median(sv), sum(1 for x in sv if x > 0), len(sv)))
    bytok = collections.defaultdict(lambda: [0.0, 0.0, 0])
    for r in sellers:
        bytok[r["sym"]][0] += r["realized_pw"]; bytok[r["sym"]][1] += r["sell_usd"]; bytok[r["sym"]][2] += 1
    for s, v in sorted(bytok.items()):
        print("  %-10s realized $%+8.0f | %d sellers | $%.0f sold" % (s, v[0], v[2], v[1]))
bag_wp = {k for k, b in basis.items() if b["tok_b"] and b["tok_rem"] * (b["buy_usd"] / b["tok_b"]) > 5}
silent = [k for k in bag_wp if k not in post]
print("bag holders (>$5 cost remaining): %d wallet-tokens; SILENT since window: %d (%.0f%%)"
      % (len(bag_wp), len(silent), 100 * len(silent) / len(bag_wp) if bag_wp else 0))
json.dump(bank_rows, open(os.path.join(RIP, "_adj_bank_rows.json"), "w"), indent=1)

# =========================================================
# TEST 3 -- POND FORWARD PATH (bars-predicate matches 07-03/07-04 -> NOW)
# =========================================================
print("\n===== TEST 3: POND FORWARD to NOW (per-token dedup on first match) =====")
grid = json.load(open(os.path.join(RIP, "_revival_grid.json")))
def day(t): return datetime.fromtimestamp(t, tz=timezone.utc).strftime("%m-%d")
first_match = {}
for p, rr in grid.items():
    for t, m, f in sorted(rr):
        if m and day(t) in ("07-03", "07-04"):
            first_match.setdefault(p, t); break
fwd_rows = []
for p, t0 in sorted(first_match.items(), key=lambda kv: kv[1]):
    bl = bars_for(p)
    if not bl:
        print(" %-10s NO bars" % p[:8]); continue
    px0 = px_at(p, t0)
    if not px0:
        print(" %-10s no signal px" % p[:8]); continue
    fw = [b for b in bl if b[0] > t0]
    if not fw: continue
    mx = max(b[4] for b in fw); mn = min(b[4] for b in fw)
    px_now = ds_now.get(p, {}).get("px") or fw[-1][4]
    hrs = (NOW - t0) / 3600
    sym = SYM.get(p) or ds_now.get(p, {}).get("sym") or p[:6]
    fwd_rows.append(dict(pair=p, sym=sym, day=day(t0), hrs=hrs,
                         now=100 * (px_now / px0 - 1), maxc=100 * (mx / px0 - 1),
                         minc=100 * (mn / px0 - 1)))
    print(" %-10s %s +%4.0fh | now %+7.1f%% | max-close %+7.1f%% | min-close %+6.1f%%"
          % (sym, day(t0), hrs, fwd_rows[-1]["now"], fwd_rows[-1]["maxc"], fwd_rows[-1]["minc"]))
for dsel in ("07-03", "07-04"):
    sel = [r for r in fwd_rows if r["day"] == dsel]
    if sel:
        print("%s matches n=%d: med now %+.1f%%, med max-close %+.1f%%, med min %+.1f%%, green-now %d/%d, hit+15-close %d/%d"
              % (dsel, len(sel), st.median([r["now"] for r in sel]), st.median([r["maxc"] for r in sel]),
                 st.median([r["minc"] for r in sel]), sum(1 for r in sel if r["now"] > 0), len(sel),
                 sum(1 for r in sel if r["maxc"] >= 15), len(sel)))
json.dump(dict(tok=tok_summary, fwd=fwd_rows, cov={SYM[p]: tape_cov[p] for p in REV_PAIRS}),
          open(os.path.join(RIP, "_adj_results.json"), "w"), indent=1, default=str)
print("\nsaved _adj_results.json")
