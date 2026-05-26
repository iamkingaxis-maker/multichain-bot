"""Bayesian win-rate + drawdown/streak analysis for the multi-bot fleet.

Two researcher-grade tools (read-only; zero fleet impact), per "claude ideas 3":

1. BAYESIAN WIN-RATE — treats each bot's WR as a Beta posterior, not a point
   estimate. Reports the 90% credible interval and P(bot_WR > baseline_WR) so we
   know *when* n is enough to call a champion-bracket winner (vs. reading n=2-16
   noise, the trap we kept hitting). Uniform Beta(1,1) prior; posterior
   Beta(wins+1, losses+1).

2. DRAWDOWN / STREAK — given each bot's WR, Monte-Carlos the distribution of the
   longest losing streak it *should* see, so an observed drawdown can be called
   "normal variance" vs "the strategy broke." Answers the question we asked twice
   today about the -$309 afternoon.

Phantom TROLL glitch sells (pnl_pct < -40, the pre-fix bad ticks) are EXCLUDED so
WR reflects true skill. Cutoff = the SP5 reset.

Usage: python scripts/bayesian_drawdown_analysis.py
"""

import time
import numpy as np
from scipy.stats import beta as beta_dist

BASE = "https://gracious-inspiration-production.up.railway.app"
RESET = "2026-05-25T21:25:00"
PHANTOM_PCT = -40.0   # sells below this are the pre-fix price-glitch artifacts
RNG = np.random.default_rng(42)


def _get(url):
    from curl_cffi import requests as r
    for _ in range(6):
        try:
            return r.get(url, impersonate="chrome", timeout=45).json()
        except Exception:
            time.sleep(2)
    return None


def pull_bot_pnls():
    """Return {bot_id: [pnl, ...]} ordered by time, phantom-excluded, post-reset."""
    bots = _get(f"{BASE}/api/bots")
    out = {}
    for b in bots:
        bid = b["bot_id"]
        tr = _get(f"{BASE}/api/bots/{bid}/trades?limit=200")
        if not tr:
            out[bid] = []
            continue
        sells = [s for s in tr if s.get("type") == "sell"
                 and s.get("time", "") >= RESET
                 and (s.get("pnl_pct") or 0) >= PHANTOM_PCT]
        sells.sort(key=lambda s: s.get("time", ""))
        out[bid] = [float(s.get("pnl") or 0.0) for s in sells]
    return out


def beta_ci(wins, n, lo=0.05, hi=0.95):
    a, b = wins + 1, (n - wins) + 1
    return beta_dist.ppf(lo, a, b), beta_dist.ppf(hi, a, b), a / (a + b)


def prob_better(wins_a, n_a, wins_b, n_b, draws=100_000):
    """P(WR_a > WR_b) under independent Beta posteriors."""
    sa = RNG.beta(wins_a + 1, (n_a - wins_a) + 1, draws)
    sb = RNG.beta(wins_b + 1, (n_b - wins_b) + 1, draws)
    return float((sa > sb).mean())


def max_losing_streak(pnls):
    cur = best = 0
    for p in pnls:
        cur = cur + 1 if p < 0 else 0
        best = max(best, cur)
    return best


def max_drawdown(pnls):
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(np.concatenate([[0.0], cum]))[1:]
    return float((cum - peak).min()) if len(cum) else 0.0


def expected_streak_pctile(wr, n, sims=10_000):
    """95th-pct longest losing streak across `sims` simulations of n trades."""
    if n == 0:
        return 0.0
    losses = RNG.random((sims, n)) > wr   # True = loss
    streaks = np.zeros(sims, dtype=int)
    cur = np.zeros(sims, dtype=int)
    for j in range(n):
        col = losses[:, j]
        cur = (cur + 1) * col
        streaks = np.maximum(streaks, cur)
    return float(np.percentile(streaks, 95))


def main():
    pnls = pull_bot_pnls()
    base = pnls.get("baseline_v1", [])
    base_w, base_n = sum(1 for p in base if p > 0), len(base)
    base_wr = base_w / base_n if base_n else 0.0
    print(f"baseline_v1: n={base_n} WR={base_wr:.0%}\n")

    rows = []
    for bid, ps in pnls.items():
        n = len(ps)
        if n == 0:
            continue
        w = sum(1 for p in ps if p > 0)
        lo, hi, mean = beta_ci(w, n)
        pb = prob_better(w, n, base_w, base_n) if base_n else 0.5
        wins = [p for p in ps if p > 0]
        loss = [p for p in ps if p < 0]
        aw = np.mean(wins) if wins else 0.0
        al = np.mean(loss) if loss else 0.0
        rr = (aw / abs(al)) if al else float("inf")
        obs_streak = max_losing_streak(ps)
        exp_streak95 = expected_streak_pctile(mean, n)
        dd = max_drawdown(ps)
        rows.append(dict(bid=bid, n=n, w=w, wr=mean, lo=lo, hi=hi, pb=pb,
                         rr=rr, total=sum(ps), obs_streak=obs_streak,
                         exp95=exp_streak95, dd=dd))

    # ── Bayesian: who has separated from baseline ──
    print("=" * 96)
    print("BAYESIAN WIN-RATE — 90% credible interval + P(WR > baseline)")
    print("=" * 96)
    sig = sorted([r for r in rows if r["n"] >= 8], key=lambda r: -r["pb"])
    print(f"{'bot':<26}{'n':>4}{'WR':>6}{'90% CI':>16}{'P(>base)':>10}{'R:R':>6}  verdict")
    for r in sig[:14] + sig[-6:]:
        ci = f"[{r['lo']:.0%},{r['hi']:.0%}]"
        if r["pb"] >= 0.90:
            v = "LIKELY > baseline"
        elif r["pb"] <= 0.10:
            v = "LIKELY < baseline"
        else:
            v = "inconclusive"
        rr = f"{r['rr']:.1f}" if r["rr"] != float("inf") else "inf"
        print(f"{r['bid']:<26}{r['n']:>4}{r['wr']:>6.0%}{ci:>16}{r['pb']:>10.2f}{rr:>6}  {v}")

    # ── Data sufficiency ──
    enough = [r for r in rows if (r["hi"] - r["lo"]) <= 0.25]
    print(f"\nDATA SUFFICIENCY: {len(enough)}/{len(rows)} bots have a 90% CI width "
          f"<= 25pp (i.e. enough trades to start judging). Median n = "
          f"{int(np.median([r['n'] for r in rows]))}.")

    # ── Drawdown / streak reality check ──
    print("\n" + "=" * 96)
    print("DRAWDOWN / STREAK — observed vs 95th-pct expected (is a drawdown real or variance?)")
    print("=" * 96)
    print(f"{'bot':<26}{'n':>4}{'WR':>6}{'obs_streak':>11}{'exp95':>7}{'maxDD$':>9}  flag")
    worst = sorted([r for r in rows if r["n"] >= 8], key=lambda r: r["total"])[:10]
    for r in worst:
        flag = "ANOMALOUS (>exp)" if r["obs_streak"] > r["exp95"] else "normal variance"
        print(f"{r['bid']:<26}{r['n']:>4}{r['wr']:>6.0%}{r['obs_streak']:>11}"
              f"{r['exp95']:>7.1f}{r['dd']:>9.2f}  {flag}")


if __name__ == "__main__":
    # sanity self-checks on the pure math (mislabelled stats would mislead decisions)
    lo, hi, m = beta_ci(14, 16)
    assert 0.60 < lo < 0.75 and 0.90 < hi < 0.98 and 0.80 < m < 0.86, (lo, hi, m)
    assert max_losing_streak([1, -1, -1, -1, 1, -1]) == 3
    assert abs(max_drawdown([10, -5, -5, 8]) - (-10.0)) < 1e-9
    main()
