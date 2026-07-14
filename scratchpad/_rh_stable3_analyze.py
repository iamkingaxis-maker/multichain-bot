"""RH stable-3 tail-cap quantification (2026-07-13).

Reconstructs closed RH paper trips (scorecard load_rh_trips join), attaches
per-pool rug stamps + entry facts, and measures what CAPPING THE LEFT TAIL does
to the top-WR racers:  hard downside stop (floor trip ret at -X)  +  concentration
rug gate ENFORCED pre-buy (drop pools flagged top1>=9 OR top10>=30)  +
fast_liq_bail (staged drains; 0 in ledger -> reported, no effect).

Metrics per racer:  token-level ex-top-2 median, trip-WR, token-WR (green%),
DISPERSION (std of per-token median returns), % catastrophic tokens (<-20%).
OOS = odd/even split by DISTINCT TOKEN (no single-token leak across halves).
"""
import json
import os
import statistics
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "robinhood_tapes", "rh_paper_trades.jsonl")
ENTRY_USD = 25.0
RH_CONTROL = "rh_young_v1"

# rug gate v2 thresholds (core/rh_rug_signals)
RUG_TOP1 = 9.0
RUG_TOP10 = 30.0


def _num(x):
    try:
        if x is None or isinstance(x, bool):
            return None
        return float(x)
    except (TypeError, ValueError):
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
                continue
            rows.append(d)
    return rows


def build():
    rows = load_rows()
    # pool -> symbol (from buy or sell rows)
    pool_sym = {}
    # pool -> rug stamp (top1/top10) -- take the first stamp with numbers
    pool_rug = {}
    for d in rows:
        p = d.get("pool")
        if d.get("sym") and p and p not in pool_sym:
            pool_sym[p] = d.get("sym")
        if d.get("ev") == "rug_signals" and p:
            t1 = _num(d.get("bs_top1_pct"))
            t1 = t1 if t1 is not None else _num(d.get("top1_pct"))
            t10 = _num(d.get("bs_top10_pct"))
            t10 = t10 if t10 is not None else _num(d.get("top10_pct"))
            if p not in pool_rug and (t1 is not None or t10 is not None):
                pool_rug[p] = {"top1": t1, "top10": t10}
    # buy facts per (bot,pool): the LATEST buy dip/liq before each trip is hard to
    # match precisely; we keep the median dip per (bot,pool) for cohorting only.
    buys_by_key = defaultdict(list)
    for d in rows:
        if d.get("ev") == "buy":
            bot = d.get("bot_id") or RH_CONTROL
            buys_by_key[(bot, d.get("pool"))].append(d)

    sells_by_key = defaultdict(list)
    for d in rows:
        if d.get("ev") == "sell":
            bot = d.get("bot_id") or RH_CONTROL
            sells_by_key[(bot, d.get("pool"))].append(d)

    trips = []
    for (bot, pool), sells in sells_by_key.items():
        sells.sort(key=lambda x: x.get("ts", ""))
        cur = []
        for s in sells:
            cur.append(s)
            if s.get("fully"):
                pnl_usd = sum(_num(x.get("pnl_usd")) or 0.0 for x in cur)
                ret = pnl_usd / ENTRY_USD * 100.0
                kinds = [x.get("kind") for x in cur]
                trips.append({
                    "bot": bot, "pool": pool, "sym": pool_sym.get(pool, pool[:8]),
                    "ret": ret, "sell_time": cur[-1].get("ts", ""),
                    "last_kind": kinds[-1], "kinds": kinds,
                })
                cur = []
    return trips, pool_rug, pool_sym


def rug_flagged(pool, pool_rug):
    r = pool_rug.get(pool)
    if not r:
        return False
    t1, t10 = r.get("top1"), r.get("top10")
    return (t1 is not None and t1 >= RUG_TOP1) or (t10 is not None and t10 >= RUG_TOP10)


def per_token_medians(trips):
    by = defaultdict(list)
    for t in trips:
        by[t["pool"]].append(t["ret"])
    return {p: statistics.median(v) for p, v in by.items() if v}


def metrics(trips):
    """token-level ex-top-2, trip-WR, token-WR, dispersion, %catastrophic."""
    if not trips:
        return None
    pt = per_token_medians(trips)
    n_tok = len(pt)
    meds = sorted(pt.values())
    kept = meds[:-2] if n_tok > 2 else meds
    ex2 = statistics.median(kept) if kept else statistics.median(meds)
    tok_green = sum(1 for m in pt.values() if m > 0)
    rets = [t["ret"] for t in trips]
    trip_green = sum(1 for r in rets if r > 0)
    # dispersion = std of per-token median returns
    disp = statistics.pstdev(list(pt.values())) if n_tok > 1 else 0.0
    cat = sum(1 for m in pt.values() if m < -20.0)
    return {
        "nTrip": len(trips), "nTok": n_tok,
        "ex2": round(ex2, 2),
        "tripWR": round(100.0 * trip_green / len(trips), 1),
        "tokGreen": round(100.0 * tok_green / n_tok, 1),
        "retMed": round(statistics.median(rets), 2),
        "tokMed": round(statistics.median(list(pt.values())), 2),
        "disp": round(disp, 2),
        "catTok": cat,
        "catRate": round(100.0 * cat / n_tok, 1),
    }


def apply_capping(trips, pool_rug, floor_pct=None, rug_gate=False):
    """Return a copy of trips with the tail-cap applied.
    rug_gate: drop trips on pools flagged by the concentration gate (pre-buy).
    floor_pct: floor each remaining trip's ret at this value (hard downside stop).
    """
    out = []
    for t in trips:
        if rug_gate and rug_flagged(t["pool"], pool_rug):
            continue
        ret = t["ret"]
        if floor_pct is not None and ret < floor_pct:
            ret = floor_pct
        nt = dict(t)
        nt["ret"] = ret
        out.append(nt)
    return out


def oos_by_token(trips):
    """Split trips into two halves by DISTINCT TOKEN (ordered by first sell_time),
    so no token appears in both halves (avoids the single-token leak)."""
    first_seen = {}
    for t in sorted(trips, key=lambda x: x["sell_time"]):
        first_seen.setdefault(t["pool"], t["sell_time"])
    toks = sorted(first_seen, key=lambda p: first_seen[p])
    odd_toks = set(toks[0::2])
    even_toks = set(toks[1::2])
    odd = [t for t in trips if t["pool"] in odd_toks]
    even = [t for t in trips if t["pool"] in even_toks]
    return odd, even


def fmt(m):
    if not m:
        return "  (no trips)"
    return (f"nTrip={m['nTrip']:>3} nTok={m['nTok']:>2}  ex2={m['ex2']:>7}  "
            f"tripWR={m['tripWR']:>5}%  tokGrn={m['tokGreen']:>5}%  "
            f"retMed={m['retMed']:>6}  tokMed={m['tokMed']:>6}  "
            f"disp={m['disp']:>6}  cat={m['catTok']}({m['catRate']}%)")


if __name__ == "__main__":
    trips, pool_rug, pool_sym = build()
    print(f"total closed trips={len(trips)}  distinct pools={len({t['pool'] for t in trips})}")
    print(f"rug-stamped pools={len(pool_rug)}  rug-FLAGGED pools="
          f"{sum(1 for p in pool_rug if rug_flagged(p, pool_rug))}")
    flagged = [(pool_sym.get(p, p[:8]), pool_rug[p]) for p in pool_rug if rug_flagged(p, pool_rug)]
    print("  FLAGGED:", flagged)

    # catastrophic tokens across the whole fleet
    print("\n=== catastrophic tokens (any racer, token-median < -20) ===")
    allpt = defaultdict(list)
    for t in trips:
        allpt[(t["sym"], t["pool"])].append(t["ret"])
    catrows = sorted(((statistics.median(v), sym, len(v), rug_flagged(p, pool_rug))
                      for (sym, p), v in allpt.items() if statistics.median(v) < -20),
                     key=lambda x: x[0])
    for med, sym, n, fl in catrows:
        print(f"  {sym:<14} tokMed={med:>8.1f}  nTrip={n}  rug_gate_flag={fl}")

    RACERS = ["rh_demand_heavy", "rh_deep_only", "rh_aged_deep", "rh_young_v1",
              "rh_aged_hold", "rh_bites2", "rh_moonbag", "rh_wide_ladder"]
    by_bot = defaultdict(list)
    for t in trips:
        by_bot[t["bot"]].append(t)

    print("\n" + "=" * 130)
    print("TAIL-CAP QUANTIFICATION  (baseline -> +rug_gate -> +floor@-20 -> +floor@-15 -> ALL[rug+floor-20])")
    print("=" * 130)
    for bot in RACERS:
        bt = by_bot.get(bot, [])
        if not bt:
            continue
        print(f"\n### {bot}  (n={len(bt)} trips)")
        base = metrics(bt)
        rug = metrics(apply_capping(bt, pool_rug, rug_gate=True))
        f20 = metrics(apply_capping(bt, pool_rug, floor_pct=-20.0))
        f15 = metrics(apply_capping(bt, pool_rug, floor_pct=-15.0))
        allc = metrics(apply_capping(bt, pool_rug, floor_pct=-20.0, rug_gate=True))
        allc15 = metrics(apply_capping(bt, pool_rug, floor_pct=-15.0, rug_gate=True))
        print("  baseline      :", fmt(base))
        print("  +rug_gate     :", fmt(rug))
        print("  +floor@-20    :", fmt(f20))
        print("  +floor@-15    :", fmt(f15))
        print("  ALL rug+f-20  :", fmt(allc))
        print("  ALL rug+f-15  :", fmt(allc15))
        # OOS split on the ALL-capped set (floor -20 + rug gate)
        capped = apply_capping(bt, pool_rug, floor_pct=-20.0, rug_gate=True)
        odd, even = oos_by_token(capped)
        mo, me = metrics(odd), metrics(even)
        print("  OOS(ALL-cap) ODD :", fmt(mo) if mo else " n/a")
        print("  OOS(ALL-cap) EVEN:", fmt(me) if me else " n/a")
