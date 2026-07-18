#!/usr/bin/env python3
"""flip_simulator.py — THE SHADOW FLIPPER (2026-07-18, AxiS: "simulate when we
would choose to flip on the live bot and how it would do if it was on once you
would have flipped it. same for turning it off vs if you didnt turn it off").

Replays the recorded 15-min regime snapshots (/api/regime/history) through the
flipper's Phase-1 mechanics and prices every decision:
  * ARM  when the route says TRADE (effective state, hysteresis already baked
    into the snapshots) — seat goes to the route's PRIMARY bot;
  * DISARM when the route says STAND_DOWN;
  * cadence cap: max 1 seat change per 4h (plan section 4).
For every span it books the routed bot's PAPER sells in that window as the
simulated seat P&L (paper = $25 positions; RH slippage measured ~0 at $25, so
paper $ is an honest live proxy):
  * ARMED spans -> "what live WOULD have made" (the flip-ON verdict);
  * OFF spans   -> the SAME bot's P&L we did NOT take (the flip-OFF verdict:
    money the disarm saved us if negative, cost us if positive).
Output: flip log + per-span $ + totals, saved to scratchpad/_flip_sim.json.
Honest limits: recommend-only replay (no execution friction beyond paper's
fidelity model, no wallet-size caps); route bots must have ledger data; spans
attribute sells by close-time (positions opened pre-flip count if closed in
the span — symmetric on both sides, so deltas stay fair).
"""
from __future__ import annotations
import json
import sys
import urllib.request
from datetime import datetime

BASE = "https://gracious-inspiration-production.up.railway.app"
WINDOW_CAP_S = 4 * 3600.0


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "rh-bot"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def main():
    hist = _get(f"{BASE}/api/regime/history?n=2000").get("snapshots") or []
    snaps = []
    for h in hist:
        t = _ts(h.get("ts"))
        if t is None:
            continue
        route = (h.get("route") or {})
        rh = route.get("rh") or {}
        snaps.append({"t": t, "ts": h.get("ts"),
                      "action": rh.get("action"),
                      "bots": rh.get("bots") or []})
    snaps.sort(key=lambda x: x["t"])
    if len(snaps) < 4:
        print(f"only {len(snaps)} snapshots — not enough to simulate yet")
        return
    print(f"snapshots: {len(snaps)}  span "
          f"{snaps[0]['ts'][:16]} -> {snaps[-1]['ts'][:16]}")

    # ── replay the seat state machine (RH seat; SOL is STAND_DOWN throughout)
    spans = []            # {state, bot, t0, t1}
    seat = None           # None = OFF, else bot_id
    last_flip = 0.0
    span_start = snaps[0]["t"]
    for s in snaps:
        want = s["bots"][0] if (s["action"] == "TRADE" and s["bots"]) else None
        can_flip = (last_flip == 0.0
                    or (s["t"] - last_flip) >= WINDOW_CAP_S)
        if want != seat and can_flip:
            spans.append({"bot": seat, "t0": span_start, "t1": s["t"]})
            seat = want
            last_flip = s["t"]
            span_start = s["t"]
    spans.append({"bot": seat, "t0": span_start, "t1": snaps[-1]["t"]})
    spans = [x for x in spans if x["t1"] > x["t0"]]

    # ── price each span from the routed bot's ledger sells ─────────────────
    ledgers = {}

    def bot_sells(bot):
        if bot not in ledgers:
            try:
                rows = _get(f"{BASE}/api/rh-paper?bot={bot}&raw=1").get("rows") or []
            except Exception:
                rows = []
            ledgers[bot] = [(r.get("ts"), float(r.get("pnl_usd") or 0))
                            for r in rows if r.get("ev") == "sell"
                            and r.get("pnl_usd") is not None]
        return ledgers[bot]

    # the counterfactual bot for OFF spans = the route's primary at that time;
    # approximation: use the NEXT armed bot, else the most-recent armed bot
    armed_bots = [x["bot"] for x in spans if x["bot"]]
    # OFF spans must ALWAYS be priced against the bot we WOULD have run —
    # if nothing ever armed in the recorded window, fall back to the route's
    # current primary so "what did standing down cost" is never $0-by-default.
    default_bot = armed_bots[0] if armed_bots else "rh_slcut_agedhold"

    on_usd = off_usd = 0.0
    flips = []
    prev_bot = None
    for sp in spans:
        cf_bot = sp["bot"] or prev_bot or default_bot
        usd = 0.0
        if cf_bot:
            usd = sum(p for ts, p in bot_sells(cf_bot)
                      if (_ts(ts) or 0) > sp["t0"] and (_ts(ts) or 0) <= sp["t1"])
        hrs = (sp["t1"] - sp["t0"]) / 3600
        state = "ARMED" if sp["bot"] else "OFF"
        if sp["bot"]:
            on_usd += usd
        else:
            off_usd += usd
        flips.append({"state": state, "bot": cf_bot,
                      "hours": round(hrs, 1), "usd": round(usd, 2),
                      "t0": datetime.fromtimestamp(sp["t0"], tz=__import__("datetime").timezone.utc).isoformat()[:16]})
        if sp["bot"]:
            prev_bot = sp["bot"]

    print(f"\n{'state':6} {'bot':22} {'hours':>6} {'usd':>9}  start")
    for f_ in flips:
        print(f"{f_['state']:6} {str(f_['bot'])[:22]:22} {f_['hours']:>6} "
              f"{f_['usd']:>+9.2f}  {f_['t0']}")
    print(f"\nARMED spans (what live WOULD have made):      ${on_usd:+.2f}")
    print(f"OFF spans (what standing down avoided/cost):  ${off_usd:+.2f}")
    print(f"  -> negative OFF = the disarms SAVED money; positive = they cost")
    print(f"FLIPPER EDGE (armed - off): ${on_usd - off_usd:+.2f} — the value "
          f"of flipping vs never flipping")
    with open("scratchpad/_flip_sim.json", "w", encoding="utf-8") as f:
        json.dump({"snapshots": len(snaps), "flips": flips,
                   "armed_usd": round(on_usd, 2),
                   "off_usd": round(off_usd, 2)}, f, indent=1)
    print("-> scratchpad/_flip_sim.json")


if __name__ == "__main__":
    main()
