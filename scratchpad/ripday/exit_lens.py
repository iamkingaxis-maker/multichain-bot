# exit_lens.py -- EXIT + SIZE mechanism decode for rip-day winner wallets (local files only)
import json, glob, os, math
from datetime import datetime, timezone

RD = os.path.dirname(os.path.abspath(__file__))

def iso2ep(s):
    return datetime.fromisoformat(s).timestamp()

# ---- load tapes into per-wallet-token trade lists for target wallets ----
TIER_A = [  # strict winners: >=3 distinct profitable tokens, covered net > 0
    "kEFiAX3jo5NmemysQov342TZ9mGh6yp92GDRjhA8XDf",
    "J1sfMsbxGNXDPMUPXyGs5D6oCEe7fSYgdPMRyVzZuZUW",
]
TIER_B = [  # strong 2-token winners (net>+$100 or 0 losers)
    "DJocqRPK2uKWvmR5WnWcd7m8fDw6az1L54R4UuH3GrGN",
    "7JCe3GHwkEr3feHgtLXnmuJ1yB3A7coSeyynxTBgdG8k",
    "DF8tRgFkt1JSuqqtVmG2maiEY92mfFWBHNpMeRBK4fEo",
    "4MB2yiq54PHkJ11YPoZGYgVzew9zFRRms41PAFoXaevg",
    "CAP9q6SmwGufYWTKupGA5uGzkjqirkRRuz6YEGhumjyi",
    "8P1msjLVVaZdwtHke9Ly9GSkQzDtwEjAr9UDyTiJBJuP",
]
TIER_C = [  # contrast: big-tail wallets w/ >=3 pos tokens but NET NEGATIVE (what kills them)
    "AgmLJBMDCqWynYnQiPCuj9ewsNNsBJXyzoUhD9LJzN51",
    "FYX5JQ2kP7TD8gWb9WP1tjmwWWUAzi8edEZTr5Z8F1ck",
    "8zkgFGVZrDLieViwqiXFCydSX6WL5hsxmUu55yBdsNsZ",
    "2tgUbS9UMoQD6GkDZBiqKYCURnGrSb6ocYwRABrSJUvY",
]
TARGETS = set(TIER_A + TIER_B + TIER_C)

trades = {}  # (wallet, token) -> list of (ep, kind, usd)
tok_sym = {}
for f in glob.glob(os.path.join(RD, "tape_*.jsonl")):
    for line in open(f, encoding="utf-8"):
        t = json.loads(line)
        if t["maker"] not in TARGETS:
            continue
        k = (t["maker"], t["token"])
        trades.setdefault(k, []).append((iso2ep(t["ts"]), t["kind"], t["volume_usd"]))
        tok_sym[t["token"]] = t["sym"]
for k in trades:
    trades[k].sort()

# ---- load OHLC ----
ohlc = {}  # mint -> list of bars [ep,o,h,l,c,v]
for f in glob.glob(os.path.join(RD, "ohlc_*.json")):
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    if d.get("bars"):
        ohlc[d["token"]] = d["bars"]

def px_at(mint, ep):
    bars = ohlc.get(mint)
    if not bars:
        return None
    best = None
    for b in bars:
        if b[0] <= ep:
            best = b
        else:
            break
    if best is None:
        return None
    if ep - best[0] > 180:  # bar too far away
        return None
    return best[4]  # close

def peak_after(mint, ep, horizon_s=6 * 3600):
    bars = ohlc.get(mint)
    if not bars:
        return None, None, None
    hi, hi_ts, cover = None, None, 0
    for b in bars:
        if b[0] < ep:
            continue
        if b[0] > ep + horizon_s:
            break
        cover = b[0] - ep
        if hi is None or b[2] > hi:
            hi, hi_ts = b[2], b[0]
    return hi, hi_ts, cover / 60 if cover else 0

def sim_our_exit(mint, entry_ep, entry_px, tp1_pct, tp1_frac, trail_pct, floor_pct, horizon_s=6 * 3600):
    """Simulate our exit on minute bars. Returns (roi_pct, hold_min, tag) or None."""
    bars = [b for b in ohlc.get(mint, []) if entry_ep <= b[0] <= entry_ep + horizon_s]
    if not bars:
        return None
    rem = 1.0
    realized = 0.0
    tp_px = entry_px * (1 + tp1_pct / 100)
    floor_px = entry_px * (1 + floor_pct / 100)
    peak = entry_px
    tp_done = False
    for b in bars:
        ep, o, h, l, c, v = b[:6]
        # floor first (pessimistic: low before high)
        if l <= floor_px and rem > 0:
            realized += rem * (floor_px / entry_px - 1)
            return realized * 100, (ep - entry_ep) / 60, "floor"
        if not tp_done and h >= tp_px:
            realized += tp1_frac * (tp1_pct / 100)
            rem -= tp1_frac
            tp_done = True
            peak = max(peak, tp_px)
        peak = max(peak, h)
        trail_px = peak * (1 - trail_pct / 100)
        if tp_done and l <= trail_px and rem > 0:
            realized += rem * (trail_px / entry_px - 1)
            return realized * 100, (ep - entry_ep) / 60, "trail"
    # end of data: mark remainder at last close
    realized += rem * (bars[-1][4] / entry_px - 1)
    return realized * 100, (bars[-1][0] - entry_ep) / 60, "eod"

def fmt(x, w=8, p=1):
    if x is None:
        return " " * (w - 2) + "--"
    return f"{x:{w}.{p}f}"

# ---- per-position decode ----
rows = []
for (w, tok), tl in sorted(trades.items()):
    buys = [t for t in tl if t[1] == "buy"]
    sells = [t for t in tl if t[1] == "sell"]
    if not buys:
        continue
    first_buy = buys[0][0]
    covered_sells = [s for s in sells if s[0] >= first_buy]
    buy_usd = sum(b[2] for b in buys)
    sell_usd = sum(s[2] for s in covered_sells)
    if buy_usd < 20:
        continue
    # re-entry: a buy that happens after at least one covered sell
    n_reentry = sum(1 for b in buys if covered_sells and b[0] > covered_sells[0][0])
    # hold times
    if covered_sells:
        h_first = (covered_sells[0][0] - first_buy) / 60
        h_last = (covered_sells[-1][0] - first_buy) / 60
    else:
        h_first = h_last = None
    # price-based decode
    e_px = px_at(tok, first_buy)
    pk, pk_ts, cover_min = peak_after(tok, first_buy)
    peak_pct = (pk / e_px - 1) * 100 if (pk and e_px) else None
    # their sell px vs entry, and vs peak; sold before or after peak
    sell_px_w = 0.0
    sell_w = 0.0
    n_before_peak = 0
    for s in covered_sells:
        sp = px_at(tok, s[0])
        if sp and e_px:
            sell_px_w += sp * s[2]
            sell_w += s[2]
        if pk_ts and s[0] <= pk_ts:
            n_before_peak += 1
    their_exit_vs_entry = (sell_px_w / sell_w / e_px - 1) * 100 if (sell_w and e_px) else None
    roi_cash = (sell_usd / buy_usd - 1) * 100 if buy_usd else None
    frac_of_peak = None
    if their_exit_vs_entry is not None and peak_pct and peak_pct > 0:
        frac_of_peak = their_exit_vs_entry / peak_pct
    # our exits simulated at THEIR entry
    simA = sim_our_exit(tok, first_buy, e_px, 6, 0.75, 10, -12) if e_px else None
    simB = sim_our_exit(tok, first_buy, e_px, 13, 0.30, 10, -12) if e_px else None
    rows.append(dict(w=w, tok=tok, sym=tok_sym.get(tok, "?"), n_buys=len(buys),
                     n_sells=len(covered_sells), buy_usd=buy_usd, sell_usd=sell_usd,
                     roi_cash=roi_cash, h_first=h_first, h_last=h_last,
                     n_reentry=n_reentry, e_px=e_px, peak_pct=peak_pct,
                     cover_min=cover_min, their_px_roi=their_exit_vs_entry,
                     frac_of_peak=frac_of_peak, n_before_peak=n_before_peak,
                     simA=simA, simB=simB, open_bag=(len(covered_sells) == 0)))

json.dump(rows, open(os.path.join(RD, "exit_lens_rows.json"), "w"), indent=1, default=str)

def tier_of(w):
    if w in TIER_A: return "A"
    if w in TIER_B: return "B"
    return "C"

print("=== PER-POSITION DECODE (buys>=$20, tape-covered) ===")
print(f"{'T':1} {'wallet':>12} {'sym':>10} {'nB':>3} {'nS':>3} {'buy$':>7} {'sell$':>8} "
      f"{'cashROI%':>8} {'hold1':>6} {'holdL':>6} {'reE':>3} {'peak%':>7} {'cov':>4} "
      f"{'pxROI%':>7} {'fPeak':>6} {'simA%':>7} {'simB%':>7}")
for r in sorted(rows, key=lambda r: (tier_of(r["w"]), r["w"], -(r["roi_cash"] or -999))):
    sa = fmt(r["simA"][0], 7) if r["simA"] else "     --"
    sb = fmt(r["simB"][0], 7) if r["simB"] else "     --"
    sym = "".join(ch if ord(ch) < 128 else "?" for ch in r["sym"])[:10]
    print(f"{tier_of(r['w'])} {r['w'][:12]:>12} {sym:>10} {r['n_buys']:3d} {r['n_sells']:3d} "
          f"{r['buy_usd']:7.0f} {r['sell_usd']:8.0f} {fmt(r['roi_cash'],8)} "
          f"{fmt(r['h_first'],6,0)} {fmt(r['h_last'],6,0)} {r['n_reentry']:3d} "
          f"{fmt(r['peak_pct'],7)} {fmt(r['cover_min'],4,0)} {fmt(r['their_px_roi'],7)} "
          f"{fmt(r['frac_of_peak'],6,2)} {sa} {sb}")

# ---- wallet-level sizing summary ----
print()
print("=== SIZING PER WALLET (buy_usd per token) ===")
import statistics as st
by_w = {}
for r in rows:
    by_w.setdefault(r["w"], []).append(r)
for w in TIER_A + TIER_B + TIER_C:
    rs = by_w.get(w, [])
    if not rs:
        continue
    sizes = [r["buy_usd"] for r in rs]
    wins = [r for r in rs if r["roi_cash"] is not None and r["roi_cash"] > 2 and not r["open_bag"]]
    losses = [r for r in rs if r["roi_cash"] is not None and r["roi_cash"] <= 2 and not r["open_bag"]]
    win_sz = st.median([r["buy_usd"] for r in wins]) if wins else None
    los_sz = st.median([r["buy_usd"] for r in losses]) if losses else None
    print(f"{tier_of(w)} {w[:12]:>12} n={len(rs):2d} size med={st.median(sizes):7.0f} "
          f"min={min(sizes):6.0f} max={max(sizes):7.0f} cv={st.pstdev(sizes)/st.mean(sizes):4.2f} "
          f"| winMedSz={fmt(win_sz,7,0)} losMedSz={fmt(los_sz,7,0)} "
          f"| partial(nS>=2)={sum(1 for r in rs if r['n_sells']>=2)}/{sum(1 for r in rs if r['n_sells']>=1)} "
          f"reentry={sum(1 for r in rs if r['n_reentry']>0)}")

# ---- aggregate: their exit vs our sims on the SAME entries ----
print()
print("=== AGGREGATE: THEIR REALIZED vs OUR EXITS ON THEIR ENTRIES (closed, px-covered) ===")
for tier, ws in [("A", TIER_A), ("B", TIER_B), ("A+B", TIER_A + TIER_B), ("C", TIER_C)]:
    sel = [r for r in rows if r["w"] in ws and not r["open_bag"] and r["their_px_roi"] is not None
           and r["simA"] and r["cover_min"] and r["cover_min"] >= 60]
    if not sel:
        print(f"tier {tier}: n=0")
        continue
    n = len(sel)
    them = st.median([r["roi_cash"] for r in sel])
    them_m = st.mean([r["roi_cash"] for r in sel])
    sa = st.median([r["simA"][0] for r in sel]); sa_m = st.mean([r["simA"][0] for r in sel])
    sb = st.median([r["simB"][0] for r in sel]); sb_m = st.mean([r["simB"][0] for r in sel])
    pk = st.median([r["peak_pct"] for r in sel if r["peak_pct"] is not None])
    hold = st.median([r["h_last"] for r in sel if r["h_last"] is not None])
    print(f"tier {tier}: n={n} | THEM cash med={them:6.1f}% mean={them_m:6.1f}% "
          f"| simA(tp6/75,tr10,fl-12) med={sa:6.1f}% mean={sa_m:6.1f}% "
          f"| simB(tp13/30) med={sb:6.1f}% mean={sb_m:6.1f}% | med peak={pk:6.1f}% med holdLast={hold:5.0f}m")

# hold-time and partial stats for A+B closed winners
print()
print("=== EXIT STYLE (tier A+B, closed positions) ===")
sel = [r for r in rows if r["w"] in TIER_A + TIER_B and not r["open_bag"]]
w_pos = [r for r in sel if r["roi_cash"] is not None and r["roi_cash"] > 2]
w_neg = [r for r in sel if r["roi_cash"] is not None and r["roi_cash"] <= 2]
for lbl, grp in [("winners(roi>2%)", w_pos), ("rest", w_neg)]:
    if not grp:
        continue
    print(f"{lbl}: n={len(grp)} medHoldFirstSell={st.median([g['h_first'] for g in grp if g['h_first'] is not None]):5.0f}m "
          f"medHoldLastSell={st.median([g['h_last'] for g in grp if g['h_last'] is not None]):5.0f}m "
          f"partialRate={sum(1 for g in grp if g['n_sells']>=2)}/{len(grp)} "
          f"medNSells={st.median([g['n_sells'] for g in grp]):3.1f} "
          f"soldBeforePeak={sum(g['n_before_peak'] for g in grp)}/{sum(g['n_sells'] for g in grp)} sells "
          f"medFracPeak={fmt(st.median([g['frac_of_peak'] for g in grp if g['frac_of_peak'] is not None]) if any(g['frac_of_peak'] is not None for g in grp) else None,6,2)}")
