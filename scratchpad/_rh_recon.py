#!/usr/bin/env python3
"""Reconstruct per-line timestamps from RH paper-lane tape and compute the
retrace-microstructure windowed features at each BUY, then test winner/loser
separation per-session and pooled with regime discipline.

Anchoring: the `[fh] N.Nmin: ... taped=K` lines give (elapsed_sec, cum_fhtape).
We interpolate each fh-tape line's ts from its own cumulative index against the
(cum_at_anchor, elapsed_sec) anchor points (self-consistent even under tail
drift). BUY/SELL ref_ts = ts of the immediately preceding fh-tape line ("now").
Per-token trade streams feed the EXACT pure functions in
core/retrace_microstructure.py.
"""
import re, sys, os, json, math
from bisect import bisect_right

REPO = r"C:\Users\jcole\multichain-bot"
sys.path.insert(0, REPO)
from core.retrace_microstructure import (
    sell_distribution_flag, net_flow_persistence)

TAPE_DIR = os.path.join(REPO, "scratchpad", "robinhood_tapes")

# regime map (mtime-derived): 07-10 bad, 07-11 bad, 07-12 good
REGIME = {1:"07-10",2:"07-10",3:"07-10",4:"07-10",5:"07-10",6:"07-10",
          7:"07-11",8:"07-11",9:"07-11",10:"07-12"}

RE_TAPE = re.compile(r"\[fh-tape\]\s+(.*?)\s+(buy|sell)\s+\$\s*([\d.,]+)\s+lag=(-?[\d.]+)s( est)?\s*$")
RE_ANCHOR = re.compile(r"\[fh\] ([0-9.]+)min:.*taped=(\d+)")
RE_BUY = re.compile(r"\[rh-paper\] BUY\s+(?:LIVE\s+)?(.*?)\s+\$[\d.]+\s+dip=(-?[\d.]+)%")
RE_SELL = re.compile(r"\[rh-paper\] SELL\s+(?:LIVE\s+)?(.*?)\s+(\S+)\s+([\d.]+)%\s+pnl=([+-][\d.]+)%")


def _bot_sym(blob):
    """Split the middle blob into (bot_id, sym). Old (07-10) single-bot format
    has no bot column; new format prefixes an rh_* bot id. Sym may be multiword."""
    blob = blob.strip()
    parts = blob.split()
    if len(parts) >= 2 and parts[0].startswith("rh_"):
        return parts[0], " ".join(parts[1:])
    return "single", blob


def parse_session(path):
    tape = []          # (cum_idx, token, kind, vol)
    anchors = []       # (cum_at_anchor, elapsed_sec)
    buys = []          # (prec_cum, bot, sym, dip, order)
    sells = []         # (prec_cum, bot, sym, kind, frac, pnl, order)
    cum = 0
    order = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            order += 1
            if line.startswith("[fh-tape]"):
                m = RE_TAPE.match(line.rstrip("\n"))
                if m:
                    cum += 1
                    tok, kind, vol, lag, est = m.groups()
                    v = float(vol.replace(",", "")) if vol else 0.0
                    tape.append((cum, tok, kind, v))
                else:
                    cum += 1  # count anyway to stay aligned with taped counter
                continue
            m = RE_ANCHOR.search(line)
            if m:
                anchors.append((cum, float(m.group(1)) * 60.0))
                continue
            if "[rh-paper] BUY" in line:
                m = RE_BUY.search(line)
                if m:
                    bot, sym = _bot_sym(m.group(1))
                    buys.append((cum, bot, sym, float(m.group(2)), order))
                continue
            if "[rh-paper] SELL" in line:
                m = RE_SELL.search(line)
                if m:
                    bot, sym = _bot_sym(m.group(1))
                    sells.append((cum, bot, sym, m.group(2),
                                  float(m.group(3)), float(m.group(4)), order))
    return tape, anchors, buys, sells


def make_interp(anchors):
    """cum index -> elapsed seconds, piecewise-linear on anchor points."""
    xs = [a[0] for a in anchors]
    ys = [a[1] for a in anchors]
    def ts(cum):
        if not xs:
            return float(cum)          # degenerate: use index as pseudo-seconds
        if cum <= xs[0]:
            # extrapolate backward using first segment slope
            if len(xs) >= 2 and xs[1] > xs[0]:
                slope = (ys[1]-ys[0])/(xs[1]-xs[0])
            else:
                slope = 1.0
            return ys[0] + (cum - xs[0]) * slope
        if cum >= xs[-1]:
            if len(xs) >= 2 and xs[-1] > xs[-2]:
                slope = (ys[-1]-ys[-2])/(xs[-1]-xs[-2])
            else:
                slope = 1.0
            return ys[-1] + (cum - xs[-1]) * slope
        i = bisect_right(xs, cum) - 1
        x0, x1, y0, y1 = xs[i], xs[i+1], ys[i], ys[i+1]
        if x1 == x0:
            return y0
        return y0 + (cum - x0) * (y1 - y0) / (x1 - x0)
    return ts


def build(path):
    tape, anchors, buys, sells = parse_session(path)
    ts_of = make_interp(anchors)
    # per-token trade streams with interpolated ts
    streams = {}
    for (cum, tok, kind, vol) in tape:
        streams.setdefault(tok, []).append(
            {"kind": kind, "volume_usd": vol, "ts": ts_of(cum), "_cum": cum})
    for tok in streams:
        streams[tok].sort(key=lambda r: r["ts"])
    return tape, anchors, buys, sells, ts_of, streams


def event_features(path):
    """Return list of entry-events with reconstructed features + outcome."""
    tape, anchors, buys, sells, ts_of, streams = build(path)

    # group BUYs into events by (sym, prec_cum): same _paper_buy batch
    ev_map = {}   # (sym, prec_cum) -> {ref_ts, bots:set, dip}
    for (cum, bot, sym, dip, order) in buys:
        key = (sym, cum)
        e = ev_map.setdefault(key, {"sym": sym, "prec_cum": cum,
                                    "ref_ts": ts_of(cum), "bots": set(),
                                    "dip": dip, "order": order})
        e["bots"].add(bot)

    events = sorted(ev_map.values(), key=lambda e: (e["sym"], e["ref_ts"]))

    # per-sym sorted event ref_ts list for "next event" bounding
    by_sym = {}
    for e in events:
        by_sym.setdefault(e["sym"], []).append(e["ref_ts"])
    for s in by_sym:
        by_sym[s].sort()

    # outcome: best sell pnl for that sym between this event and next same-sym event
    #          (event-level RAN label). Also position net-$ via frac accounting.
    sells_by_sym = {}
    for (cum, bot, sym, kind, frac, pnl, order) in sells:
        sells_by_sym.setdefault(sym, []).append(
            {"ts": ts_of(cum), "bot": bot, "kind": kind,
             "frac": frac/100.0, "pnl": pnl, "order": order})
    for s in sells_by_sym:
        sells_by_sym[s].sort(key=lambda r: r["order"])

    out = []
    for e in events:
        sym = e["sym"]; ref = e["ref_ts"]
        ref_list = by_sym[sym]
        idx = ref_list.index(ref)
        nxt = ref_list[idx+1] if idx+1 < len(ref_list) else float("inf")
        # sells for this event window (ts in [ref, nxt))
        evs = [r for r in sells_by_sym.get(sym, [])
               if ref - 1e-6 <= r["ts"] < nxt]
        best_pnl = max((r["pnl"] for r in evs), default=None)
        # position net-$: accumulate per-bot frac-weighted pnl within window
        posn = {}
        for r in evs:
            p = posn.setdefault(r["bot"], {"wpnl": 0.0, "frac": 0.0})
            p["wpnl"] += r["frac"] * r["pnl"]
            p["frac"] += r["frac"]
        # realized net-$ per position on $25 size (realized fraction only)
        pos_net = [25.0 * p["wpnl"]/100.0 for p in posn.values() if p["frac"] > 0]
        best_pos_pnl = max((p["wpnl"] for p in posn.values()), default=None)

        # FEATURES at ref on this token's reconstructed stream
        trades = streams.get(sym, [])
        sd = sell_distribution_flag(trades, ref)
        nf = net_flow_persistence(trades, ref)

        out.append({
            "sym": sym, "ref_ts": ref, "dip": e["dip"],
            "n_bots": len(e["bots"]),
            "n_sells": len(evs),
            "best_pnl": best_pnl,
            "best_pos_pnl": best_pos_pnl,
            "pos_net_usd": pos_net,
            "ran": (best_pnl is not None and best_pnl >= 6.0),
            "has_outcome": best_pnl is not None,
            "sell_rate_60": sd.get("sell_rate_60"),
            "sell_traj": sd.get("sell_traj"),
            "n_trades_60": sd.get("n_trades_60"),
            "avoid_block": sd.get("block"),
            "cum_nf_60": nf.get("cum_nf_60"),
            "pos_subwins": nf.get("pos_subwins"),
            "flow_confirm": nf.get("confirm"),
        })
    return out


if __name__ == "__main__":
    allrows = []
    for i in range(1, 11):
        p = os.path.join(TAPE_DIR, f"paper_lane_session{i}.log")
        if not os.path.exists(p):
            continue
        rows = event_features(p)
        for r in rows:
            r["session"] = i
            r["regime"] = REGIME[i]
        allrows.extend(rows)
        n_out = sum(1 for r in rows if r["has_outcome"])
        n_ran = sum(1 for r in rows if r["ran"])
        print(f"session{i} [{REGIME[i]}]: events={len(rows)} with_outcome={n_out} ran={n_ran}")
    with open(os.path.join(TAPE_DIR, "_recon_events.json"), "w") as f:
        json.dump(allrows, f)
    print(f"TOTAL events={len(allrows)} saved to _recon_events.json")
