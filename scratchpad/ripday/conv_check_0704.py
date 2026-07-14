# Winner co-buy convergence per day: % of winner buys with >=2 (>=3) other same-day
# winner wallets buying the same pair within +/-15m. Partly circular (winners share tokens);
# used only as a day-over-day rotation signal.
import json, os, glob, bisect
from datetime import datetime

RIP = os.path.dirname(os.path.abspath(__file__))
led = json.load(open(os.path.join(RIP, "ledger3_wallets.json")))
wbd = json.load(open(os.path.join(RIP, "winners_by_day.json")))

for day in ["2026-07-01", "2026-07-02", "2026-07-03"]:
    winners = set(wbd[day]["winners"])
    # winner buys per pair
    per_pair = {}
    for w in winners:
        for e in led.get(w, []):
            if e["day"] != day or e["buy_usd"] < 20 or e.get("no_px"):
                continue
            for bts in e["buy_ts"]:
                per_pair.setdefault(e["pair"], []).append((datetime.fromisoformat(bts).timestamp(), w))
    for p in per_pair:
        per_pair[p].sort()
    n = c2 = c3 = 0
    for p, lst in per_pair.items():
        for ep, w in lst:
            others = {ww for ee, ww in lst if ww != w and abs(ee - ep) <= 900}
            n += 1
            if len(others) >= 2: c2 += 1
            if len(others) >= 3: c3 += 1
    if n:
        print("%s: winner buys=%d  >=2 co-winners +/-15m: %.0f%%  >=3: %.0f%%  (winners=%d)" % (
            day, n, 100 * c2 / n, 100 * c3 / n, len(winners)))
