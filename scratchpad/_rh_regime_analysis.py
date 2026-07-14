"""RH regime-gate + OOS demand-signal analysis (2026-07-13).

Reconstructs closed trips per (bot,pool) from the RH paper ledger, attaches the
OPENING buy's entry-time features (dip_pct, liq, micro.flow_confirm, age band,
and the feed-wide regime stamp: buy_share_30m / netflow_30m_usd / n_swaps /
distinct_pools / disc), applies the standing SCRUB rule, then:
  (1) quantifies a LOOSE crash-only regime gate (block rate + blocked-vs-kept
      outcomes), age-band aware (looser for young/small);
  (2) tests whether any entry-time demand/depth feature separates winners from
      bleeders AND survives odd/even-trip OOS.

ex-top-2 token-median grading; honest low-n framing.
"""
import json
import statistics as st
from collections import defaultdict
from datetime import datetime

LEDGER = "scratchpad/robinhood_tapes/rh_paper_trades.jsonl"
ENTRY_USD = 25.0


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
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
                continue          # synthetic test rows
            rows.append(d)
    return rows


def build_trips(rows):
    """Per (bot,pool): walk events in ts order; a trip opens on a buy from flat,
    accumulates sells until fully==True, closes. Attaches the OPENING buy's
    features. Returns list of trip dicts."""
    by_key = defaultdict(list)
    for d in rows:
        ev = d.get("ev")
        if ev not in ("buy", "sell"):
            continue
        key = (d.get("bot_id"), d.get("pool"))
        t = parse_ts(d.get("ts", ""))
        if t is None:
            continue
        by_key[key].append((t, d))
    trips = []
    for (bot, pool), evs in by_key.items():
        evs.sort(key=lambda x: x[0])
        open_buy = None
        open_t = None
        cur_sells = []
        for t, d in evs:
            if d.get("ev") == "buy":
                if open_buy is None:
                    open_buy = d
                    open_t = t
                # re-buys while open just add size; keep first entry features
            elif d.get("ev") == "sell":
                if open_buy is None:
                    continue      # sell with no tracked open (untracked basis)
                cur_sells.append((t, d))
                if d.get("fully"):
                    pnl_usd = sum(float(s.get("pnl_usd") or 0.0)
                                  for _, s in cur_sells)
                    reg = open_buy.get("regime") or {}
                    mic = open_buy.get("micro") or {}
                    trips.append({
                        "bot": bot, "token": pool,
                        "ret": pnl_usd / ENTRY_USD * 100.0,
                        "hold_s": t - open_t,
                        "entry_ts": open_buy.get("ts"),
                        "dip_pct": open_buy.get("dip_pct"),
                        "liq": open_buy.get("liq"),
                        "age_h": open_buy.get("age_h"),
                        "flow_confirm": mic.get("flow_confirm"),
                        "buy_share_30m": reg.get("buy_share_30m"),
                        "netflow_30m_usd": reg.get("netflow_30m_usd"),
                        "n_swaps_30m": reg.get("n_swaps_30m"),
                        "distinct_pools_30m": reg.get("distinct_pools_30m"),
                        "disc": reg.get("disc"),
                        "band": reg.get("band"),
                        "hour_utc": reg.get("hour_utc"),
                        "has_regime": bool(reg),
                    })
                    open_buy = None
                    open_t = None
                    cur_sells = []
    return trips


def scrub(trips):
    """Standing SCRUB: drop ret>0 AND hold<10s (paper stale-price illusion)."""
    keep = [t for t in trips
            if not (t["ret"] > 0 and t["hold_s"] is not None
                    and t["hold_s"] < 10)]
    return keep, len(trips) - len(keep)


def ex_top2_tokmed(trips):
    """ex-top-2 token-median of returns. Token = pool; take each token's median
    return, drop the top 2 token-medians, return median of the rest."""
    by_tok = defaultdict(list)
    for t in trips:
        by_tok[t["token"]].append(t["ret"])
    tok_meds = sorted((st.median(v) for v in by_tok.values()))
    n_tok = len(tok_meds)
    ex2 = tok_meds[:-2] if n_tok > 2 else tok_meds
    return {
        "n_trips": len(trips),
        "n_tokens": n_tok,
        "raw_median": round(st.median([t["ret"] for t in trips]), 2)
        if trips else None,
        "raw_mean": round(st.mean([t["ret"] for t in trips]), 2)
        if trips else None,
        "tokmed_ex2": round(st.median(ex2), 2) if ex2 else None,
        "win_rate": round(sum(1 for t in trips if t["ret"] > 0) / len(trips), 3)
        if trips else None,
    }


def fmt(m):
    return (f"n={m['n_trips']:>3} tok={m['n_tokens']:>2} "
            f"rawMed={m['raw_median']} rawMean={m['raw_mean']} "
            f"tokmedEx2={m['tokmed_ex2']} wr={m['win_rate']}")


def main():
    rows = load_rows()
    trips = build_trips(rows)
    trips, n_scrub = scrub(trips)
    print(f"=== BUILD: {len(trips)} closed trips after scrub "
          f"(dropped {n_scrub} spike-illusion) ===")
    reg_trips = [t for t in trips if t["has_regime"]
                 and t["buy_share_30m"] is not None]
    print(f"trips with regime stamp: {len(reg_trips)}\n")

    print("--- overall (all trips) ---")
    print(fmt(ex_top2_tokmed(trips)))
    print("--- overall (regime-stamped subset) ---")
    print(fmt(ex_top2_tokmed(reg_trips)))
    print()

    # ==================================================================
    #  PART 1 — LOOSE CRASH-ONLY REGIME GATE
    # ==================================================================
    print("=" * 66)
    print("PART 1 — LOOSE CRASH-ONLY REGIME GATE (market-wide demand)")
    print("=" * 66)
    # buy_share_30m distribution
    bs = sorted(t["buy_share_30m"] for t in reg_trips)
    nf = sorted(t["netflow_30m_usd"] for t in reg_trips)
    print(f"buy_share_30m distribution (n={len(bs)}): "
          f"min={bs[0]:.3f} p10={bs[int(.1*len(bs))]:.3f} "
          f"p25={bs[int(.25*len(bs))]:.3f} med={st.median(bs):.3f} "
          f"p75={bs[int(.75*len(bs))]:.3f} max={bs[-1]:.3f}")
    print(f"netflow_30m_usd distribution: min={nf[0]:.0f} "
          f"p10={nf[int(.1*len(nf))]:.0f} p25={nf[int(.25*len(nf))]:.0f} "
          f"med={st.median(nf):.0f} max={nf[-1]:.0f}")
    print("NOTE: netflow_30m is >0 in ALL stamped trips (buy_share med 0.89) — "
          "the tape captured NO market-wide crash window. buy_share is the only\n"
          "gradable crash axis, and its LOW tail (p10) is the loose-gate probe.\n")

    # Loose gate candidates: block only the bottom crash tail of buy_share.
    for thr in [0.80, 0.82, 0.84, 0.86]:
        blocked = [t for t in reg_trips if t["buy_share_30m"] < thr]
        kept = [t for t in reg_trips if t["buy_share_30m"] >= thr]
        if not blocked:
            continue
        bm = ex_top2_tokmed(blocked)
        km = ex_top2_tokmed(kept)
        print(f"[buy_share < {thr}] BLOCK {len(blocked)}/{len(reg_trips)} "
              f"({100*len(blocked)/len(reg_trips):.0f}%)  "
              f"blocked_rawMed={bm['raw_median']} blocked_rawMean={bm['raw_mean']}"
              f" | kept_rawMed={km['raw_median']} kept_rawMean={km['raw_mean']}")
    print()
    # Age-band awareness: is the crash signal weaker for YOUNG (AxiS thesis)?
    print("--- age-band split: does buy_share separate outcomes per band? ---")
    for band in ("young", "mid", "aged"):
        bt = [t for t in reg_trips if t["band"] == band]
        if len(bt) < 8:
            print(f"  {band}: n={len(bt)} (too thin)")
            continue
        med_bs = st.median([t["buy_share_30m"] for t in bt])
        lo = [t for t in bt if t["buy_share_30m"] < med_bs]
        hi = [t for t in bt if t["buy_share_30m"] >= med_bs]
        lom, him = ex_top2_tokmed(lo), ex_top2_tokmed(hi)
        print(f"  {band}: n={len(bt)}  LOW-share rawMean={lom['raw_mean']} "
              f"(n={lom['n_trips']}) | HIGH-share rawMean={him['raw_mean']} "
              f"(n={him['n_trips']})  spread={round((him['raw_mean'] or 0)-(lom['raw_mean'] or 0),2)}")
    print()

    # ==================================================================
    #  PART 2 — OOS DEMAND/DEPTH SIGNAL SEPARATION (odd/even trips)
    # ==================================================================
    print("=" * 66)
    print("PART 2 — DEMAND/DEPTH SIGNAL vs OUTCOME, ODD/EVEN OOS")
    print("=" * 66)
    # order trips chronologically for a stable odd/even split
    reg_sorted = sorted(reg_trips, key=lambda t: t["entry_ts"] or "")
    for i, t in enumerate(reg_sorted):
        t["_idx"] = i
    even = [t for t in reg_sorted if t["_idx"] % 2 == 0]
    odd = [t for t in reg_sorted if t["_idx"] % 2 == 1]
    print(f"split: even n={len(even)}  odd n={len(odd)}\n")

    def test_feature(name, getter, hi_is_favor=True, thr_fn=None):
        """For each split, threshold at that split's own median; report
        favored-cohort minus disfavored rawMean (the 'lift'). A signal SURVIVES
        only if lift has the SAME sign in BOTH splits."""
        print(f"[{name}]  (favor = {'HIGH' if hi_is_favor else 'LOW'})")
        lifts = []
        for label, split in (("even", even), ("odd", odd)):
            vals = [(getter(t), t) for t in split if getter(t) is not None]
            if len(vals) < 10:
                print(f"   {label}: n={len(vals)} too thin")
                lifts.append(None)
                continue
            med = st.median([v for v, _ in vals])
            hi = [t for v, t in vals if v >= med]
            lo = [t for v, t in vals if v < med]
            favor, dis = (hi, lo) if hi_is_favor else (lo, hi)
            fm, dm = ex_top2_tokmed(favor), ex_top2_tokmed(dis)
            lift_mean = round((fm["raw_mean"] or 0) - (dm["raw_mean"] or 0), 2)
            lift_tok = None
            if fm["tokmed_ex2"] is not None and dm["tokmed_ex2"] is not None:
                lift_tok = round(fm["tokmed_ex2"] - dm["tokmed_ex2"], 2)
            lifts.append(lift_mean)
            print(f"   {label}: thr@med={med:.4g}  favor rawMean={fm['raw_mean']}"
                  f"(n{fm['n_trips']}) dis rawMean={dm['raw_mean']}(n{dm['n_trips']})"
                  f"  LIFT_mean={lift_mean}  LIFT_tokEx2={lift_tok}")
        if all(l is not None for l in lifts):
            same = (lifts[0] > 0) == (lifts[1] > 0) and abs(lifts[0]) > 0 \
                   and abs(lifts[1]) > 0
            verdict = "SURVIVES (same sign both splits)" if same \
                else "FAILS OOS (sign flips / null)"
            print(f"   => {verdict}\n")
        else:
            print("   => INSUFFICIENT (a split too thin)\n")

    # continuous demand/depth features
    test_feature("buy_share_30m (market demand)", lambda t: t["buy_share_30m"], True)
    test_feature("netflow_30m_usd (market demand)", lambda t: t["netflow_30m_usd"], True)
    test_feature("n_swaps_30m (market activity)", lambda t: t["n_swaps_30m"], True)
    test_feature("dip_pct (deeper=lower; favor SHALLOW/HIGH)", lambda t: t["dip_pct"], True)
    test_feature("dip_pct (favor DEEP/LOW)", lambda t: t["dip_pct"], False)
    test_feature("liq (favor HIGH)", lambda t: t["liq"], True)

    # binary flow_confirm
    print("[micro.flow_confirm (per-token demand turn, binary)]")
    for label, split in (("even", even), ("odd", odd)):
        tv = [t for t in split if t["flow_confirm"] is not None]
        yes = [t for t in tv if t["flow_confirm"] is True]
        no = [t for t in tv if t["flow_confirm"] is False]
        ym, nm = ex_top2_tokmed(yes), ex_top2_tokmed(no)
        lift = round((ym["raw_mean"] or 0) - (nm["raw_mean"] or 0), 2) \
            if yes and no else None
        print(f"   {label}: confirm=T rawMean={ym['raw_mean']}(n{ym['n_trips']}) "
              f"confirm=F rawMean={nm['raw_mean']}(n{nm['n_trips']}) LIFT={lift}")
    print()


if __name__ == "__main__":
    main()
