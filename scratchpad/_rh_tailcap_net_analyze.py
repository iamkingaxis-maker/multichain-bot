"""RH tail-cap NET-$/position optimization (2026-07-13).

Models loss-cut configs on the accumulated RH paper ledger and reports
net-$/position AND net-$ PER DAY (07-10/11/12 = regime robustness):
  - hard-stop level S in {-8,-10,-12,-15,-20}
  - fast-derisk (partial exit to 25%) at T in {none,3,5,10} min
For the robust edges (rh_demand_heavy, rh_deep_only) AND the whole fleet.

MODEL (honest, leg-aware; provenance in _rh_tailcap_net_0713.md):
  * Trips = scorecard load_rh_trips join (sells per (bot,pool), split at fully).
  * Partial TP legs fire at POSITIVE prices BEFORE any drawdown -> protected.
  * Terminal LOSS leg (HARD_STOP / PRE_STOP_BAIL) is a GRADUAL bleed (observed
    stop slip is small: median HARD_STOP fill -17.9 vs -15 trigger = -2.9pp), so
    a tighter stop S exits earlier ~ at S. IDEALIZED headline floors the terminal
    fill at S; a SLIP-AWARE lower bound (keeps the observed overshoot) is reported
    as sensitivity.
  * Terminal LP_DRAIN leg is a SINGLE-BLOCK pull (CASHCATWIF -100 @ 109min): a
    price STOP cannot catch it (fill already ~-84 before the quote). Only DERISK
    (cut exposure to 25% before the late pull) reduces it.
  * DERISK@T: if hold-to-terminal h>T and exposure into the terminal leg q>0.25,
    the freed (q-0.25) is banked at p_T = f*min(1,T/h) (linear-decline proxy,
    CONSERVATIVE for flat-then-cliff rugs where p_T->0), and 0.25 takes the
    terminal fill. Also clips slow WINNERS held past T (measured = derisk
    winner-kill).
  * WINNER-KILL from a tighter STOP: measurable only where a currently-winning
    trip has an OBSERVED excursion below S (deepest non-terminal exit is
    POST_TP1_TRAIL -7.0, so 0 measurable at S>=-8). The UNOBSERVABLE pre-TP1
    knife-through is flagged (ledger has no intra-trip price path).
"""
import json
import os
import statistics
from collections import defaultdict
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "robinhood_tapes", "rh_paper_trades.jsonl")
CTRL = "rh_young_v1"
DEFAULT_E = 25.0
CAT_BOUND = -22.0     # terminal fills worse than this = single-block/huge-slip
SLIP_MAX = 8.0        # |overshoot| beyond this on a HARD_STOP = treat as gap


def _num(x):
    try:
        if x is None or isinstance(x, bool):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def pt(s):
    try:
        return datetime.fromisoformat(s)
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
                continue
            rows.append(d)
    return rows


def build_trips():
    rows = load_rows()
    pool_sym = {}
    for d in rows:
        if d.get("sym") and d.get("pool") and d["pool"] not in pool_sym:
            pool_sym[d["pool"]] = d["sym"]
    buys = defaultdict(list)
    for d in rows:
        if d.get("ev") == "buy":
            buys[(d.get("bot_id") or CTRL, d.get("pool"))].append(d)
    for k in buys:
        buys[k].sort(key=lambda x: x.get("ts", ""))
    sells = defaultdict(list)
    for d in rows:
        if d.get("ev") == "sell":
            sells[(d.get("bot_id") or CTRL, d.get("pool"))].append(d)
    trips = []
    for (bot, pool), ss in sells.items():
        ss.sort(key=lambda x: x.get("ts", ""))
        cur = []
        for s in ss:
            cur.append(s)
            if s.get("fully"):
                first_ts = cur[0].get("ts", "")
                last_ts = cur[-1].get("ts", "")
                # entry = latest buy <= first sell ts
                bs = [b for b in buys[(bot, pool)] if b.get("ts", "") <= first_ts]
                E = _num(bs[-1].get("usd")) if bs else None
                E = E or DEFAULT_E
                ent = pt(bs[-1]["ts"]) if bs else None
                lt = pt(last_ts)
                hold_m = (lt - ent).total_seconds() / 60.0 if (ent and lt) else None
                legs = [{
                    "kind": x.get("kind"),
                    "frac": _num(x.get("frac")) or 0.0,
                    "pnl_pct": _num(x.get("pnl_pct")),
                    "pnl_usd": _num(x.get("pnl_usd")) or 0.0,
                    "ts": x.get("ts", ""),
                } for x in cur]
                pnl = sum(l["pnl_usd"] for l in legs)
                trips.append({
                    "bot": bot, "pool": pool, "sym": pool_sym.get(pool, pool[:8]),
                    "E": E, "pnl_usd": pnl, "ret": pnl / E * 100.0,
                    "day": last_ts[:10], "legs": legs, "hold_m": hold_m,
                    "last_kind": legs[-1]["kind"], "last_fill": legs[-1]["pnl_pct"],
                })
                cur = []
    return trips


def resim(trip, stop_S=-15.0, derisk_T=None, mf=0.25, slip_aware=False):
    """Return resimulated pnl_usd for a trip under stop_S and optional derisk_T (min).
    stop_S: hard-stop level (e.g. -10). Applied to the terminal loss leg only.
    derisk_T: minutes; None = no derisk. mf = max exposure fraction post-derisk.
    slip_aware: keep observed stop overshoot (conservative lower bound)."""
    legs = trip["legs"]
    E = trip["E"]
    h = trip["hold_m"]
    # all legs except terminal are pre-drawdown banks (TP ladder) -> protected
    banked = sum(l["pnl_usd"] for l in legs[:-1])
    term = legs[-1]
    q = term["frac"]                 # fraction held into the terminal leg
    f = term["pnl_pct"]
    if f is None:
        return trip["pnl_usd"]
    kind = term["kind"]
    # ---- exposure into the terminal fill after derisk ----
    exposed = q
    freed = 0.0
    p_T = 0.0
    if derisk_T is not None and h is not None and h > derisk_T and q > mf:
        exposed = mf
        freed = q - mf
        frac_reached = min(1.0, derisk_T / h) if h > 0 else 1.0
        p_T = f * frac_reached            # linear-decline proxy (conservative)
    # ---- terminal fill under the stop ----
    fill = f
    if f < 0:  # a loss leg
        if kind == "LP_DRAIN":
            fill = f                      # single-block: stop cannot catch
        elif f < stop_S:                  # deeper than the stop -> stop triggers
            if slip_aware:
                overshoot = f - (-15.0)    # observed slip past the -15 trigger
                if abs(overshoot) > SLIP_MAX or f < CAT_BOUND:
                    fill = f               # gap/single-block: unsavable by stop
                else:
                    fill = max(f, stop_S + overshoot)
            else:
                fill = stop_S              # idealized: exit at the stop
        else:
            fill = f                       # shallower than stop -> unchanged (bail)
    term_pnl = freed * E * p_T / 100.0 + exposed * E * fill / 100.0
    return banked + term_pnl


def net_by_day(trips, **kw):
    d = defaultdict(float)
    for t in trips:
        d[t["day"]] += resim(t, **kw)
    return d


def summarize(trips, label, **kw):
    days = ["2026-07-10", "2026-07-11", "2026-07-12"]
    nbd = net_by_day(trips, **kw)
    tot = sum(nbd.values())
    n = len(trips)
    perpos = tot / n if n else 0.0
    return {
        "label": label, "n": n, "total": tot, "perpos": perpos,
        "d10": nbd.get(days[0], 0.0), "d11": nbd.get(days[1], 0.0),
        "d12": nbd.get(days[2], 0.0),
    }


def fmt(s):
    return (f"  {s['label']:<26} n={s['n']:>3}  net=${s['total']:>8.2f}  "
            f"/pos=${s['perpos']:>6.2f}  |  07-10 ${s['d10']:>7.2f}  "
            f"07-11 ${s['d11']:>7.2f}  07-12 ${s['d12']:>7.2f}")


if __name__ == "__main__":
    trips = build_trips()
    print(f"total trips={len(trips)}  days={sorted(set(t['day'] for t in trips))}")

    STOPS = [-8.0, -10.0, -12.0, -15.0, -20.0]
    DERISKS = [None, 3.0, 5.0, 10.0]

    def block(name, subset):
        print("\n" + "=" * 118)
        print(f"### {name}   (n={len(subset)})")
        print("=" * 118)
        base = summarize(subset, "BASELINE (stop -15, no derisk)", stop_S=-15.0)
        print(fmt(base))
        print("  -- hard-stop level sweep (idealized floor; no derisk) --")
        for S in STOPS:
            print(fmt(summarize(subset, f"stop {S:g}", stop_S=S)))
        print("  -- hard-stop level sweep (SLIP-AWARE lower bound; no derisk) --")
        for S in STOPS:
            print(fmt(summarize(subset, f"stop {S:g} slip", stop_S=S, slip_aware=True)))
        print("  -- derisk timing sweep (at stop -15) --")
        for T in DERISKS:
            lab = "no derisk" if T is None else f"derisk {T:g}min"
            print(fmt(summarize(subset, lab, stop_S=-15.0, derisk_T=T)))
        print("  -- BEST combos (stop x derisk) --")
        for S in [-10.0, -12.0, -15.0]:
            for T in [None, 5.0]:
                lab = f"stop {S:g}" + ("" if T is None else f" + derisk {T:g}m")
                print(fmt(summarize(subset, lab, stop_S=S, derisk_T=T)))

    for bot in ["rh_demand_heavy", "rh_deep_only"]:
        block(bot, [t for t in trips if t["bot"] == bot])
    block("FLEET (all racers)", trips)
    block("FLEET ex-control-only-day (07-11/12 only)",
          [t for t in trips if t["day"] != "2026-07-10"])
