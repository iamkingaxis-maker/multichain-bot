"""BOUNCED-BUT-WE-LOST cohort builder — step 1: rounds + bar coverage.

Builds rounds (buy->flat, per bot per pair) since 07-01 from the three family
bot trade dumps, applies the SCRUB RULE, classifies exit reasons, and checks
minute-bar coverage for each round's entry window. ANALYSIS ONLY.
"""
import json, os, re, sys
from datetime import datetime, timezone
from collections import defaultdict

RIP = os.path.dirname(os.path.abspath(__file__))
BOTS = ["badday_flush", "badday_allday", "badday_young_absorb"]
SINCE = "2026-07-01"

def ts(s):
    return datetime.fromisoformat(s).timestamp()

def reason_class(reason, kind):
    r = (reason or "").lower()
    if "velocity-bail" in r: return "velocity_bail"
    if "mae-floor" in r: return "mae_floor"
    if kind == "TP1" or kind == "TP2": return "tp"
    if kind == "POST_TP1_TRAIL": return "trail"
    if kind == "BREAKEVEN_LOCK": return "breakeven_lock"
    if kind == "NEVER_RUNNER": return "timestop"
    if "hard" in r and "stop" in r: return "hard_stop"
    return "other:" + (kind or r[:20])

def load_rounds():
    rounds = []
    for b in BOTS:
        d = json.load(open(os.path.join(RIP, f"_bnl_trades_{b}.json")))
        d = [t for t in d if t.get("time")]
        d.sort(key=lambda t: t["time"])
        # group per pair
        by_pair = defaultdict(list)
        for t in d:
            by_pair[t.get("pair_address") or t.get("address")].append(t)
        for pair, trades in by_pair.items():
            cur = None
            for t in trades:
                if t["type"] == "buy":
                    if cur is not None:
                        # unexpected second buy while open -> treat as scale-in (record)
                        cur["extra_buys"] += 1
                        continue
                    cur = dict(bot=b, pair=pair, token=t["token"], address=t["address"],
                               entry_time=t["time"], entry_ts=ts(t["time"]),
                               entry_price=t["entry_price"], amount_usd=t.get("amount_usd", 100.0),
                               sells=[], scrubbed=0, extra_buys=0)
                elif t["type"] == "sell":
                    if cur is None:
                        continue  # sell closing a pre-window position
                    # SCRUB RULE
                    if (t.get("pnl_pct") or 0) > 0 and (t.get("hold_secs") or 999) < 10:
                        cur["scrubbed"] += 1
                        if t.get("fully_closed"):
                            cur = None  # drop whole-round if scrubbed close? keep sells so far
                        continue
                    cur["sells"].append(dict(
                        time=t["time"], ts=ts(t["time"]), exit_price=t.get("exit_price"),
                        pnl=t.get("pnl"), pnl_pct=t.get("pnl_pct"),
                        hold_secs=t.get("hold_secs"), reason=t.get("reason"),
                        kind=t.get("kind"), frac=t.get("sell_fraction"),
                        peak_pnl_pct=t.get("peak_pnl_pct"),
                        rclass=reason_class(t.get("reason"), t.get("kind")),
                        fully_closed=bool(t.get("fully_closed"))))
                    if t.get("fully_closed"):
                        rounds.append(cur)
                        cur = None
            # still-open position at dump end: skip (not a completed round)
    # window filter: entry since 07-01
    rounds = [r for r in rounds if r["entry_time"] >= SINCE]
    for r in rounds:
        r["realized_pnl_usd"] = sum(s["pnl"] or 0 for s in r["sells"])
        r["realized_pct"] = 100.0 * r["realized_pnl_usd"] / r["amount_usd"]
        # terminal exit = the fully_closed sell; dominant class = largest |pnl| sell class
        term = [s for s in r["sells"] if s["fully_closed"]]
        r["term_class"] = term[-1]["rclass"] if term else (r["sells"][-1]["rclass"] if r["sells"] else "none")
        r["classes"] = sorted({s["rclass"] for s in r["sells"]})
    return rounds

def bar_file(pair):
    for d in ("_gt_bars", "_gt_bars_b"):
        p = os.path.join(RIP, d, pair[:12] + ".json")
        if os.path.exists(p):
            return p
    return None

def main():
    rounds = load_rounds()
    losing = [r for r in rounds if r["realized_pct"] < 0 and r["sells"]]
    print(f"rounds since {SINCE}: {len(rounds)}  losing: {len(losing)}  winning/flat: {len(rounds)-len(losing)}")
    from collections import Counter
    print("terminal class (losing):", Counter(r["term_class"] for r in losing).most_common())
    print("terminal class (all):", Counter(r["term_class"] for r in rounds).most_common())
    # bar coverage for losing rounds
    need, have, cover = set(), set(), {}
    for r in losing:
        need.add(r["pair"])
    for p in need:
        f = bar_file(p)
        if f:
            have.add(p)
    print(f"losing-round pairs: {len(need)}  with bar file: {len(have)}")
    # check bar time coverage vs entry windows
    ok, partial, missing = 0, 0, 0
    miss_pairs = []
    for r in losing:
        f = bar_file(r["pair"])
        if not f:
            missing += 1; miss_pairs.append(r["pair"]); r["bars"] = "none"; continue
        bars = json.load(open(f))
        if not bars:
            missing += 1; miss_pairs.append(r["pair"]); r["bars"] = "empty"; continue
        t0, t1 = bars[0][0], bars[-1][0]
        if t0 <= r["entry_ts"] and t1 >= r["entry_ts"] + 90*60:
            ok += 1; r["bars"] = "ok"
        else:
            partial += 1; r["bars"] = f"partial({t0}..{t1})"
    print(f"entry+90m fully covered: {ok}  partial: {partial}  missing file: {missing}")
    json.dump(rounds, open(os.path.join(RIP, "_bnl_rounds.json"), "w"), indent=1)
    json.dump(sorted(set(miss_pairs)), open(os.path.join(RIP, "_bnl_missing_pairs.json"), "w"))
    # also dump partial pairs with needed window
    partials = {}
    for r in losing:
        if str(r.get("bars","")).startswith("partial") or r.get("bars") in ("none","empty"):
            p = partials.setdefault(r["pair"], [1e18, 0])
            p[0] = min(p[0], r["entry_ts"]); p[1] = max(p[1], r["entry_ts"] + 6*3600)
    json.dump(partials, open(os.path.join(RIP, "_bnl_refetch_pairs.json"), "w"), indent=1)
    print(f"pairs needing (re)fetch: {len(partials)}")

if __name__ == "__main__":
    main()
