"""RH regime-SIZING signal analysis (2026-07-13).

Goal (AxiS): find a REAL-TIME, decision-time market signal that separates the
BAD RH regime day (07-11 — every racer lost) from the GOOD days (07-10, 07-12 —
racers won), so a sizing gate can trade smaller on bad days. Metric = NET-$ /
position (median-% hid the regime loss). Honest low-n: 3 days.

Only uses info available AT/BEFORE entry:
  - market-wide regime stamp on the buy (buy_share_30m, netflow_30m, n_swaps_30m,
    distinct_pools_30m, npph)  [present 07-11/07-12; ABSENT 07-10 — pre-stamp]
  - our own realized-so-far read: WR / mean-net-$ of the first N closed trips of
    the day, and a rolling realized WR (available EVERY day, incl 07-10).
"""
import json
import statistics as st
from collections import defaultdict
from datetime import datetime, timezone

LEDGER = "scratchpad/robinhood_tapes/rh_paper_trades.jsonl"
ENTRY_USD = 25.0


def parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def load_rows():
    rows = []
    with open(LEDGER, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(d.get("ts", ""))[:4] == "1970":
                continue  # synthetic test rows
            rows.append(d)
    return rows


def build_trips(rows):
    """Per (bot_id,pool): open on buy from flat, close on fully==True. Attach the
    opening buy's features + regime stamp + day. Returns trip dicts."""
    by_key = defaultdict(list)
    for d in rows:
        if d.get("ev") not in ("buy", "sell"):
            continue
        t = parse_ts(d.get("ts", ""))
        if t is None:
            continue
        by_key[(d.get("bot_id"), d.get("pool"))].append((t, d))
    trips = []
    for (bot, pool), evs in by_key.items():
        evs.sort(key=lambda x: x[0])
        open_buy = open_t = None
        cur_sells = []
        for t, d in evs:
            if d.get("ev") == "buy":
                if open_buy is None:
                    open_buy, open_t = d, t
            elif d.get("ev") == "sell":
                if open_buy is None:
                    continue
                cur_sells.append((t, d))
                if d.get("fully"):
                    pnl_usd = sum(float(s.get("pnl_usd") or 0.0)
                                  for _, s in cur_sells)
                    reg = open_buy.get("regime") or {}
                    mic = open_buy.get("micro") or {}
                    day = str(open_buy.get("ts", ""))[:10]
                    trips.append({
                        "bot": bot, "pool": pool, "day": day,
                        "entry_ts": open_t,
                        "pnl_usd": pnl_usd,
                        "ret_pct": pnl_usd / ENTRY_USD * 100.0,
                        "hold_s": (t - open_t).total_seconds(),
                        "hour_utc": open_t.hour,
                        "dip_pct": open_buy.get("dip_pct"),
                        "liq": open_buy.get("liq"),
                        "age_h": open_buy.get("age_h"),
                        "band": reg.get("band"),
                        "flow_confirm": mic.get("flow_confirm"),
                        "buy_share_30m": reg.get("buy_share_30m"),
                        "netflow_30m_usd": reg.get("netflow_30m_usd"),
                        "n_swaps_30m": reg.get("n_swaps_30m"),
                        "distinct_pools_30m": reg.get("distinct_pools_30m"),
                        "npph": reg.get("npph"),
                        "has_regime": bool(reg),
                    })
                    open_buy = open_t = None
                    cur_sells = []
    return trips


def scrub(trips):
    """Standing SCRUB: drop ret>0 AND hold<10s (paper stale-price illusion)."""
    keep = [t for t in trips if not (t["ret_pct"] > 0 and t["hold_s"] < 10)]
    return keep, len(trips) - len(keep)


def net_stats(trips):
    if not trips:
        return {"n": 0, "net_usd": 0.0, "net_per_pos": None, "wr": None,
                "med_ret": None}
    pnl = [t["pnl_usd"] for t in trips]
    return {
        "n": len(trips),
        "net_usd": round(sum(pnl), 2),
        "net_per_pos": round(sum(pnl) / len(pnl), 3),
        "wr": round(sum(1 for p in pnl if p > 0) / len(pnl), 3),
        "med_ret": round(st.median([t["ret_pct"] for t in trips]), 2),
    }


def pctl(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    return xs[min(len(xs) - 1, int(p * len(xs)))]


def main():
    rows = load_rows()
    trips = build_trips(rows)
    trips, n_scrub = scrub(trips)
    trips.sort(key=lambda t: t["entry_ts"])
    days = sorted({t["day"] for t in trips})
    print(f"=== {len(trips)} closed trips after scrub (dropped {n_scrub}); "
          f"days={days} ===\n")

    # ------------------------------------------------------------------
    # GROUND TRUTH: per-day net-$/position (the mandated metric)
    # ------------------------------------------------------------------
    print("=" * 74)
    print("GROUND TRUTH — per-day NET-$/position (median-% shown for contrast)")
    print("=" * 74)
    per_day = {}
    for day in days:
        dt = [t for t in trips if t["day"] == day]
        s = net_stats(dt)
        per_day[day] = s
        print(f"  {day}: n={s['n']:>3}  NET=${s['net_usd']:>8}  "
              f"net/pos=${s['net_per_pos']:>7}  WR={s['wr']}  "
              f"medRet%={s['med_ret']}")
    print()
    # per-bot per-day (shows 'every racer lost 07-11')
    print("--- per-bot NET-$/position by day (blank = no trips) ---")
    bots = sorted({t["bot"] for t in trips if t["bot"]})
    hdr = "  {:<18}".format("bot") + "".join(f"{d[5:]:>16}" for d in days)
    print(hdr)
    for b in bots:
        cells = []
        for day in days:
            bt = [t for t in trips if t["bot"] == b and t["day"] == day]
            if bt:
                s = net_stats(bt)
                cells.append(f"{s['net_per_pos']:>7}/{s['n']:<2}wr{int(s['wr']*100):>2}")
            else:
                cells.append(" " * 16)
        print(f"  {b:<18}" + "".join(f"{c:>16}" for c in cells))
    # None-bot (07-10 untagged)
    for day in days:
        bt = [t for t in trips if t["bot"] is None and t["day"] == day]
        if bt:
            s = net_stats(bt)
            print(f"  {'(untagged 0710)':<18}{day[5:]}: net/pos=${s['net_per_pos']} "
                  f"n={s['n']} wr={s['wr']}")
    print()

    # ------------------------------------------------------------------
    # SIGNAL 1 — market-wide regime stamp (07-11 vs 07-12; 07-10 has none)
    # ------------------------------------------------------------------
    print("=" * 74)
    print("SIGNAL 1 — MARKET-WIDE regime stamp per day (decision-time, on buy)")
    print("=" * 74)
    for day in days:
        dt = [t for t in trips if t["day"] == day and t["has_regime"]
              and t["buy_share_30m"] is not None]
        if not dt:
            print(f"  {day}: no regime stamps (pre-stamp era)")
            continue
        bs = [t["buy_share_30m"] for t in dt]
        nf = [t["netflow_30m_usd"] for t in dt]
        nsw = [t["n_swaps_30m"] for t in dt if t["n_swaps_30m"] is not None]
        dp = [t["distinct_pools_30m"] for t in dt
              if t["distinct_pools_30m"] is not None]
        npph = [t["npph"] for t in dt if t["npph"] is not None]
        print(f"  {day}: n={len(dt)}")
        print(f"     buy_share_30m : med={st.median(bs):.4f} "
              f"p10={pctl(bs,.1):.4f} p25={pctl(bs,.25):.4f} "
              f"min={min(bs):.4f} mean={st.mean(bs):.4f}")
        print(f"     netflow_30m$  : med={st.median(nf):,.0f} "
              f"p10={pctl(nf,.1):,.0f} min={min(nf):,.0f}")
        print(f"     n_swaps_30m   : med={st.median(nsw):.0f}  "
              f"distinct_pools: med={st.median(dp):.0f}  "
              f"npph: med={st.median(npph):.1f}")
    print()

    # ------------------------------------------------------------------
    # SIGNAL 2 — average ENTRY DIP DEPTH per day (available EVERY day)
    # ------------------------------------------------------------------
    print("=" * 74)
    print("SIGNAL 2 — entry structure per day (dip_pct, liq) — all days")
    print("=" * 74)
    for day in days:
        dt = [t for t in trips if t["day"] == day and t["dip_pct"] is not None]
        dips = [t["dip_pct"] for t in dt]
        liqs = [t["liq"] for t in dt if t["liq"] is not None]
        print(f"  {day}: n={len(dt)}  dip_pct med={st.median(dips):.2f} "
              f"mean={st.mean(dips):.2f}  |  liq med=${st.median(liqs):,.0f}")
    print()

    # ------------------------------------------------------------------
    # SIGNAL 3 — REAL-TIME 'is today working?' : WR & net-$ of first N
    #            closed trips of the day (self-referential, every day)
    # ------------------------------------------------------------------
    print("=" * 74)
    print("SIGNAL 3 — first-N-closed-trips read (real-time 'is today working?')")
    print("=" * 74)
    for N in (5, 8, 10, 15):
        print(f"  --- first {N} closed trips of each day ---")
        for day in days:
            dt = sorted([t for t in trips if t["day"] == day],
                        key=lambda t: t["entry_ts"])
            first = dt[:N]
            rest = dt[N:]
            if len(first) < N:
                print(f"     {day}: only {len(first)} trips (<{N})")
                continue
            fs, rs = net_stats(first), net_stats(rest)
            print(f"     {day}: first{N} WR={fs['wr']} netPos=${fs['net_per_pos']}"
                  f"  || REST n={rs['n']} netPos=${rs['net_per_pos']} "
                  f"WR={rs['wr']}")
    print()

    # ------------------------------------------------------------------
    # SIGNAL 4 — ROLLING realized WR / expectancy over last K closed trips
    #            (fleet-wide, chronological — the true decision-time dial)
    # ------------------------------------------------------------------
    print("=" * 74)
    print("SIGNAL 4 — rolling realized net/pos over last K closed (fleet chrono)")
    print("=" * 74)
    K = 15
    MIN_N = 8
    from datetime import timedelta
    # STRICTLY CAUSAL dial: at trip t's ENTRY time, use only trips that have
    # already fully CLOSED (entry_ts + hold_s <= t.entry_ts). Take the last K of
    # those by close time, mean their realized pnl_usd. No look-ahead whatsoever.
    for t in trips:
        t["_close_ts"] = t["entry_ts"] + timedelta(seconds=t["hold_s"])
    closed_sorted = sorted(trips, key=lambda t: t["_close_ts"])
    close_ts_list = [t["_close_ts"] for t in closed_sorted]
    import bisect
    roll = []
    for t in trips:
        # index of last trip that closed at or before this entry
        j = bisect.bisect_right(close_ts_list, t["entry_ts"])
        prior = closed_sorted[max(0, j - K):j]
        dial = (sum(x["pnl_usd"] for x in prior) / len(prior)) \
            if len(prior) >= MIN_N else None
        t["_dial"] = dial
        roll.append((t, dial))
    # distribution of the dial by day
    for day in days:
        vals = [d for t, d in roll if t["day"] == day and d is not None]
        if not vals:
            print(f"  {day}: dial warm-up only")
            continue
        print(f"  {day}: rollK{K} net/pos  med=${st.median(vals):.3f} "
              f"p25=${pctl(vals,.25):.3f} p75=${pctl(vals,.75):.3f} "
              f"frac<0={sum(1 for v in vals if v<0)/len(vals):.2f}")
    print()

    # ------------------------------------------------------------------
    # SIZING-GATE SIM: use the dial (last-K realized net/pos) at each entry.
    # size = 1.0 if dial>=0 else 0.3 ; also test buy_share gate on 11/12.
    # ------------------------------------------------------------------
    print("=" * 74)
    print("SIZING-GATE SIM — dial<0 => 0.3x size (else 1.0x). Net-$ effect.")
    print("=" * 74)
    for downsize in (0.3, 0.0):
        tag = f"{downsize}x" if downsize > 0 else "PAUSE"
        base_net = defaultdict(float)
        gate_net = defaultdict(float)
        n_down = defaultdict(int)
        for t, dial in roll:
            base_net[t["day"]] += t["pnl_usd"]
            if dial is not None and dial < 0:
                gate_net[t["day"]] += t["pnl_usd"] * downsize
                n_down[t["day"]] += 1
            else:
                gate_net[t["day"]] += t["pnl_usd"]
        print(f"  [defense={tag} when rollK{K}<0]")
        tot_b = tot_g = 0.0
        for day in days:
            b, g = base_net[day], gate_net[day]
            tot_b += b
            tot_g += g
            print(f"     {day}: base=${b:>8.2f} -> gated=${g:>8.2f}  "
                  f"(saved ${g-b:>7.2f}; {n_down[day]} trips downsized)")
        print(f"     TOTAL: base=${tot_b:.2f} -> gated=${tot_g:.2f}  "
              f"delta=${tot_g-tot_b:+.2f}\n")

    # buy_share market gate (11/12 only, non-young), various floors
    print("  [market buy_share_30m gate on 07-11/12 (has stamp), non-young]")
    stamped = [t for t in trips if t["has_regime"]
               and t["buy_share_30m"] is not None]
    for floor in (0.85, 0.88, 0.90, 0.92):
        base = defaultdict(float)
        gated = defaultdict(float)
        nd = defaultdict(int)
        for t in stamped:
            base[t["day"]] += t["pnl_usd"]
            block = (t["band"] != "young") and (t["buy_share_30m"] < floor)
            gated[t["day"]] += t["pnl_usd"] * (0.3 if block else 1.0)
            nd[t["day"]] += 1 if block else 0
        line = f"     floor={floor}: "
        for day in sorted(base):
            line += f"{day[5:]} base=${base[day]:.2f}->${gated[day]:.2f}({nd[day]}dn)  "
        print(line)
    print()


if __name__ == "__main__":
    main()
