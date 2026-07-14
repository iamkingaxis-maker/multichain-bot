"""BOUNCED-BUT-WE-LOST — step 2: reachability + counterfactual replay + saves.

Replays the family exit stack MINUS the velocity leg on GT minute bars from each
round's actual entry time/price. Pessimistic same-bar resolution: stop legs (bar
low) are evaluated BEFORE TP legs (bar high); the entry bar can stop us but can
NEVER TP us; peak is updated only after both checks. Fills at exact threshold
prices (no favorable slippage); gap-through fills at bar open.

Variant A (family geometry, velocity off):
  pre-TP1 floors: never_runner -6 (peak<3), giveback -6 (peak>=4), MAE floor -7,
  hard stop -12 (gap fills at open); timebox 60min @ peak<3 -> close at bar close.
  TP1 +6 sell 75%; TP2 +12 sell 25%; post-TP1 trail 2pp off peak.
Variant B (wideexit v2 floor geometry, same TP ladder):
  pre-TP1 floors: never_runner -12 (peak<3), giveback -12 (peak>=4), floor -18;
  timebox 60min @ peak<3; TP1 +6/75%, TP2 +12/25%, trail 2pp.
Cap: 360 min or end of bars -> mark at last close (flagged).
"""
import json, os, bisect
from collections import defaultdict

RIP = os.path.dirname(os.path.abspath(__file__))
HORIZON_MIN = 360

def bar_file(pair):
    for d in ("_gt_bars", "_gt_bars_b"):
        p = os.path.join(RIP, d, pair[:12] + ".json")
        if os.path.exists(p):
            return p
    return None

_bar_cache = {}
def load_bars(pair):
    if pair in _bar_cache:
        return _bar_cache[pair]
    f = bar_file(pair)
    bars = json.load(open(f)) if f else []
    bars = sorted(bars, key=lambda b: b[0])
    _bar_cache[pair] = bars
    return bars

def reach_tp1(bars, entry_ts, entry_price, window_s=90*60, tp=6.0):
    """Did price touch entry*(1+tp%) within window after entry? Entry bar excluded
    from TP credit (pessimistic). Returns (covered, reached, max_up_pct, min_dn_pct)."""
    tgt = entry_price * (1 + tp/100.0)
    hi, lo = -1e18, 1e18
    n = 0
    for b in bars:
        ts = b[0]
        if ts <= entry_ts - 60:
            continue
        if ts > entry_ts + window_s:
            break
        if ts <= entry_ts:  # entry bar: no TP credit
            continue
        n += 1
        hi = max(hi, b[2]); lo = min(lo, b[3])
    if n == 0:
        return False, False, None, None
    return True, hi >= tgt, 100*(hi/entry_price-1), 100*(lo/entry_price-1)

def replay(bars, entry_ts, entry_price, variant="A", fill="pess",
           tp1=6.0, tp1_frac=0.75, tp2=12.0, trail_pp=2.0):
    """fill='touch': stop fills at exact threshold (gap -> bar open).
    fill='pess':  stop fills at min(threshold, bar close) (gap -> min(open, close));
    models a poll-latency fill on a crashing candle. Truth lies between."""
    if variant == "A":
        nr_floor, gb_floor, mae_floor, hard = -6.0, -6.0, -7.0, -12.0
    else:
        nr_floor, gb_floor, mae_floor, hard = -12.0, -12.0, -18.0, -18.0
    px = lambda pct: entry_price * (1 + pct/100.0)
    pct = lambda p: 100*(p/entry_price - 1)
    # bars from entry bar onward
    i0 = bisect.bisect_left([b[0] for b in bars], entry_ts - 59)
    seq = bars[i0:]
    if not seq:
        return None
    peak = 0.0
    tp1_hit = False
    remaining = 1.0
    realized = 0.0  # in pct-of-position units (weighted pnl pct)
    legs = []
    end_ts = entry_ts + HORIZON_MIN*60
    last_close = None
    for k, b in enumerate(seq):
        ts, o, h, l, c = b[0], b[1], b[2], b[3], b[4]
        if ts > end_ts:
            break
        last_close = c
        entry_bar = ts <= entry_ts
        o_p, h_p, l_p, c_p = pct(o), pct(h), pct(l), pct(c)
        hold_min = (ts + 60 - entry_ts) / 60.0
        if not tp1_hit:
            # ---- stops first (pessimistic) ----
            # effective floor at current peak state
            if peak < 3.0:
                floor = nr_floor
            elif peak >= 4.0:
                floor = gb_floor
            else:
                floor = mae_floor
            floor = max(floor, hard)  # floor is shallower; hard is gap backstop
            if entry_bar:
                # entry bar: o/h/l include PRE-entry action (we buy mid-flush);
                # only the close is reliably post-entry. Stop-check close only.
                if c_p <= floor:
                    realized += remaining * c_p; legs.append(("entrybar_stop", c_p, hold_min))
                    remaining = 0.0; break
                continue
            if o_p <= floor:  # gap open through floor
                f_ = o_p if fill == "touch" else min(o_p, c_p)
                realized += remaining * f_; legs.append(("gap_floor", f_, hold_min))
                remaining = 0.0; break
            if l_p <= floor:
                f_ = floor if fill == "touch" else min(floor, c_p)
                realized += remaining * f_; legs.append(("floor", f_, hold_min))
                remaining = 0.0; break
            # timebox (never-runner 60min, peak<3): close at this bar close
            if hold_min >= 60.0 and peak < 3.0:
                realized += remaining * c_p; legs.append(("timebox", c_p, hold_min))
                remaining = 0.0; break
            # ---- TP1 (entry bar cannot TP) ----
            if h_p >= tp1:
                realized += tp1_frac * tp1
                remaining -= tp1_frac
                tp1_hit = True
                legs.append(("tp1", tp1, hold_min))
                peak = max(peak, h_p)
                # same-bar TP2 pessimistically NOT granted (stop-before-TP within bar)
                continue
            peak = max(peak, h_p)
            continue
        # ---- post-TP1 on remainder ----
        trail_lvl = peak - trail_pp
        if o_p <= trail_lvl:
            f_ = o_p if fill == "touch" else min(o_p, c_p)
            realized += remaining * f_; legs.append(("trail_gap", f_, hold_min))
            remaining = 0.0; break
        if l_p <= trail_lvl:
            f_ = trail_lvl if fill == "touch" else min(trail_lvl, c_p)
            realized += remaining * f_; legs.append(("trail", f_, hold_min))
            remaining = 0.0; break
        if h_p >= tp2:
            realized += remaining * tp2; legs.append(("tp2", tp2, hold_min))
            remaining = 0.0; break
        peak = max(peak, h_p)
    if remaining > 0:
        # mark at last close (cap or bars exhausted)
        mark = pct(last_close) if last_close is not None else 0.0
        realized += remaining * mark
        legs.append(("mark_cap", mark, None))
    return dict(realized_pct=realized, legs=legs, tp1_hit=tp1_hit,
                capped=any(x[0] == "mark_cap" for x in legs))

def main():
    rounds = json.load(open(os.path.join(RIP, "_bnl_rounds.json")))
    losing = [r for r in rounds if r["realized_pct"] < 0 and r["sells"]]
    out = []
    for r in losing:
        bars = load_bars(r["pair"])
        cov, reached, mx, mn = reach_tp1(bars, r["entry_ts"], r["entry_price"]) if bars else (False, False, None, None)
        rec = dict(bot=r["bot"], pair=r["pair"], token=r["token"], address=r["address"],
                   entry_time=r["entry_time"], entry_ts=r["entry_ts"], entry_price=r["entry_price"],
                   actual_pct=r["realized_pct"], term_class=r["term_class"],
                   covered=cov, tp1_reachable=reached, max_up_90m=mx, min_dn_90m=mn)
        if cov:
            rec["replayA"] = replay(bars, r["entry_ts"], r["entry_price"], "A", "pess")
            rec["replayB"] = replay(bars, r["entry_ts"], r["entry_price"], "B", "pess")
            rec["replayA_touch"] = replay(bars, r["entry_ts"], r["entry_price"], "A", "touch")
            rec["replayB_touch"] = replay(bars, r["entry_ts"], r["entry_price"], "B", "touch")
        # saves check for velocity-bail terminal rounds
        if r["term_class"] == "velocity_bail":
            bail = [s for s in r["sells"] if s["rclass"] == "velocity_bail"][-1]
            bp, bts = bail["exit_price"], bail["ts"]
            lo = 1e18; n = 0
            hi_after = -1e18
            for b in bars:
                if b[0] <= bts:
                    continue
                if b[0] > bts + 90*60:
                    break
                n += 1; lo = min(lo, b[3]); hi_after = max(hi_after, b[2])
            if n and bp:
                rec["bail_price"] = bp
                rec["post_bail_min_pct"] = 100*(lo/bp-1)
                rec["post_bail_max_pct"] = 100*(hi_after/bp-1)
                rec["bail_saved_6"] = (100*(lo/bp-1)) <= -6.0
                rec["bail_cost_6"] = (100*(hi_after/bp-1)) >= 6.0
        out.append(rec)
    json.dump(out, open(os.path.join(RIP, "_bnl_replay.json"), "w"), indent=1)
    ncov = sum(1 for x in out if x["covered"])
    print(f"losing rounds: {len(out)}  covered by bars: {ncov}")
    print(f"tp1 reachable: {sum(1 for x in out if x.get('tp1_reachable'))}")

if __name__ == "__main__":
    main()
