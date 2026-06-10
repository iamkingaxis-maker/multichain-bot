"""Beat-the-regime-shift study (2026-06-10, Fable 5).

Question: what POLICY best converts the known day-level regime persistence
(WR autocorr 0.31, red stretches in runs) into saved dollars, walk-forward,
on OUR candidate universe? Candidates:

  P0 baseline        — always 1.0x (what we do now)
  P1 yesterday-WR    — size 0.5x if yesterday's fleet WR < 55%, 1.5x if > 65%
  P2 first-quarter   — after the first 25% of the CT day's closes, size the
                       REST of the day 0.5x/1.0x/1.5x by that quarter's WR
  P3 rolling-20      — intraday: rolling 20-close WR < 45% -> 0.5x until > 55%
  P4 yesterday-breadth — median regime_h1_neg_pct of yesterday's entries
                       > 30 -> 0.5x (memecoin downside breadth keying)

Sizing applies to LOSSES AND WINS alike (multiplying each close's pnl) —
walk-forward only (no same-day hindsight beyond the policy's own trigger).
Universe: candidate set + smart_follow (the go-live population).
"""
import json, sys, collections, statistics
from datetime import datetime, timedelta
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

tr = json.load(open("_trades_cache.json"))
CAND_PREFIX = ("pond_", "pool_a_candidate", "pool_c_", "momentum_shadow", "young_probe")
buy_strat = {}
buy_meta = {}
for t in tr:
    if t.get("type") == "buy":
        k = (t.get("pair_address") or t.get("address") or "").lower()
        if t.get("strategy"):
            buy_strat[k] = t["strategy"]
        if t.get("entry_meta"):
            buy_meta.setdefault(t.get("time", "")[:10], []).append(t["entry_meta"])

def ct_day(ts):
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return (dt - timedelta(hours=5)).strftime("%Y-%m-%d")
    except Exception:
        return None

closes = []   # (time, ct_day, pnl)
for t in tr:
    if t.get("type") != "sell" or "cancelled" in (t.get("reason") or "").lower():
        continue
    bot = t.get("bot_id") or ""
    k = (t.get("pair_address") or t.get("address") or "").lower()
    if not (bot.startswith(CAND_PREFIX) or buy_strat.get(k) == "smart_follow"):
        continue
    d = ct_day(t.get("time"))
    if d:
        closes.append((t.get("time"), d, float(t.get("pnl") or 0)))
closes.sort()
days = sorted({d for _, d, _ in closes})
by_day = collections.defaultdict(list)
for ts, d, p in closes:
    by_day[d].append((ts, p))
print(f"closes={len(closes)} days={len(days)} ({days[0]}..{days[-1]})")

# day-level fleet WR (ALL sells, for the yesterday-WR signal — broader, stabler)
fleet_day_wr = {}
fleet_sells = collections.defaultdict(list)
for t in tr:
    if t.get("type") != "sell" or "cancelled" in (t.get("reason") or "").lower():
        continue
    d = ct_day(t.get("time"))
    if d:
        fleet_sells[d].append(float(t.get("pnl") or 0))
for d, ps in fleet_sells.items():
    if len(ps) >= 20:
        fleet_day_wr[d] = sum(1 for p in ps if p > 0) / len(ps)

# yesterday breadth: median regime_h1_neg_pct across that day's entries
day_breadth = {}
for d, metas in buy_meta.items():
    vals = [m.get("regime_h1_neg_pct") for m in metas
            if isinstance(m.get("regime_h1_neg_pct"), (int, float))]
    if len(vals) >= 10:
        day_breadth[d] = statistics.median(vals)

def prev_day(d):
    i = days.index(d)
    return days[i - 1] if i > 0 else None

def run_policy(name, mult_fn):
    """mult_fn(day, idx_in_day, closes_so_far_today) -> multiplier for close idx."""
    total = 0.0
    daily = {}
    for d in days:
        rows = by_day[d]
        s = 0.0
        for i, (ts, p) in enumerate(rows):
            s += p * mult_fn(d, i, rows[:i])
        daily[d] = s
        total += s
    worst = min(daily.values()); best = max(daily.values())
    met = sum(1 for v in daily.values() if v >= 100)
    return name, total, worst, best, met, daily

policies = []

policies.append(run_policy("P0 baseline 1.0x", lambda d, i, prior: 1.0))

def p1(d, i, prior):
    pd = prev_day(d)
    wr = fleet_day_wr.get(pd) if pd else None
    if wr is None: return 1.0
    return 0.5 if wr < 0.55 else (1.5 if wr > 0.65 else 1.0)
policies.append(run_policy("P1 yesterday-WR dial", p1))

def p2(d, i, prior):
    n_day = len(by_day[d])
    q = max(5, n_day // 4)
    if i < q: return 1.0   # first quarter trades at 1x (measurement window)
    qw = sum(1 for _, p in by_day[d][:q] if p > 0) / q
    return 0.5 if qw < 0.5 else (1.5 if qw > 0.65 else 1.0)
policies.append(run_policy("P2 first-quarter dial", p2))

_roll_state = {}
def p3(d, i, prior):
    # rolling 20-close WR across days (continuous), throttle on cold streak
    hist = _roll_state.setdefault("hist", [])
    # rebuild deterministically: hist = last 20 candidate closes before this one
    # (cheap approach: maintain as we go — run_policy iterates in order)
    if i == 0 and d == days[0]:
        hist.clear()
    if len(hist) >= 20:
        wr = sum(hist[-20:]) / 20
        mult = 0.5 if wr < 0.45 else (1.5 if wr > 0.65 else 1.0)
    else:
        mult = 1.0
    # record this close's outcome AFTER deciding
    hist.append(1 if by_day[d][i][1] > 0 else 0)
    return mult
policies.append(run_policy("P3 rolling-20 throttle", p3))

def p4(d, i, prior):
    pd = prev_day(d)
    b = day_breadth.get(pd) if pd else None
    if b is None: return 1.0
    return 0.5 if b > 30 else 1.0
policies.append(run_policy("P4 yesterday-breadth", p4))

def p5(d, i, prior):
    return min(p1(d, i, prior), p2(d, i, prior))
policies.append(run_policy("P5 min(P1,P2) combo", p5))

print(f"\n{'policy':26s}{'total $':>10s}{'worst day':>11s}{'best day':>10s}{'days>=100':>10s}")
base_daily = policies[0][5]
for name, total, worst, best, met, daily in policies:
    print(f"  {name:24s}{total:+10.0f}{worst:+11.0f}{best:+10.0f}{met:10d}")

# per-day diff table for the best non-baseline policy
print("\nper-day P&L: baseline vs P2 vs P5 (last 12 days):")
p2_daily = policies[2][5]; p5_daily = policies[5][5]
for d in days[-12:]:
    print(f"  {d}  base {base_daily[d]:+8.0f}   P2 {p2_daily[d]:+8.0f}   P5 {p5_daily[d]:+8.0f}")
