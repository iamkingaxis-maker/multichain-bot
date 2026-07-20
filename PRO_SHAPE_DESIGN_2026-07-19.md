# PROFESSIONAL-SHAPE PANEL — rh_pro_agedflush (2026-07-19)

(3 Fable designers x 3 adversarial judges; synthesis below)

All verification runs are done — the synthesized config reproduces cleanly. Final answer below.

# THE PROFESSIONAL-SHAPE BOT: `rh_pro_agedflush` (paper seat, RH)

## Synthesis decisions
**Base = D3 (AUTHSTACK-PRO, highest score 7/7/7.5, only design with cap-luck resample control).** Grafts and rejections, each verified against the 63,972-candidate replay just now:

- **REMOVED D3's -40 depth floor** (Judge 3: noise-fit). Verified: sub--40 flushes under the full gate stack are +$0.10 to +$0.33/e, wr ~48-51%, **zero dead exposure** — weakly positive, and the rug gate (top10>=50/top1>=20, a verified filter) already handles rug-shaped distribution. Admitted at **base size only**.
- **Conviction tier restricted to the monotone band**: 1.5x at -40 <= dip <= -30 only (bands: -30..-25 +$0.76/e, -35..-30 +$0.55, -40..-35 +$1.18; below -40 the gradient breaks, so no tier there). This fixes the flaw my un-banded test exposed: a naive `dip<=-30` tier would put 1.5x on the weakest cohort.
- **REJECTED D2's ns30==0 graft**: verified +$0.03/e for -22% n, worse worst-day; fails OPEN on tape gaps; shape of a newly mined predicate (mining is closed). demand-net (b30>s30) keeps the verified knife-skip substance.
- **REJECTED phoenix arm** (wr 49%, synthetic stop-markers, ledger cross-check shown contaminated by Judge 3) and **D1's payness sensor** (live lane structurally cannot reproduce the tape-wide collector instrument — the exact "dead pipe" failure mode). Phoenix stays a fleet-bot hypothesis, not this seat's.
- **REJECTED 3x sizing everywhere** (n=11 evidence). Max tier 1.5x.

## 1) Final config

```python
LaneBot(
  bot_id="rh_pro_agedflush", chain="rh", paper=True,
  entry_mode="dip",
  dip_trigger_pct=-25.0,            # no max-depth floor (floor removed; rug gate covers)
  min_pool_age_h=24.0, max_pool_age_h=None,
  demand_min_buy_usd=50.0, min_buys_30s=1,
  demand_net_required=True,         # 30s buys$ > sells$ (knife-skip sell-dominated leg, hardened)
  knife_skip=True,                  # config-drift insurance ($0 in replay, subsumed)
  min_session_vol_usd=16_000, min_liq_usd=30_000,
  rug_gate="enforce",               # top10>=50 / top1>=20 (verified holder-concentration filter)
  allowed_hours_utc=None,           # 24/7 — hour gating rejected as unstable in both D2/D3
  max_buys_per_day=15, reentry_cooldown_s=86_400,   # one bite per pool per day
  max_concurrent=3, max_rt_cost_pct=6.0,
  entry_usd=25.0,
  # exits: triple-validated aged_sl1 ladder, unchanged
  sl1_pct=-6.0, sl1_sell_fraction=0.75,
  tp1_pct=6.0, tp1_sell=0.50, tp2_pct=16.0, tp2_sell=0.30,
  trail_pp=10.0, pre_tp1_trail_arm=5.0, pre_tp1_trail_gap=2.0,
  hard_stop_pct=-15.0, time_box_s=None, phoenix_entry=False,
)
```

New gate code (~14 lines) in `scripts/rh_paper_lane.py`:

```python
def pro_gate(sig, cfg):                                  # runs after lane's existing gates
    reason = None
    if sig.pool_age_h < cfg.min_pool_age_h:  reason = "age<24h"
    elif sig.dip_pct > cfg.dip_trigger_pct:  reason = "shallow"
    elif sig.buys_30s_usd < cfg.demand_min_buy_usd or sig.n_buys_30s < 1: reason = "no_demand"
    elif sig.buys_30s_usd <= sig.sells_30s_usd: reason = "net_sell"      # demand-net
    elif sig.session_vol_usd < cfg.min_session_vol_usd:  reason = "thin_session"
    log_gate_decision(sig.pool, sig.dip_pct, reason)     # log EVERY fail -> population-drift audit
    if reason: return None
    size = 37.50 if -40.0 <= sig.dip_pct <= -30.0 else 25.00   # conviction band, 1.5x
    return size
```

The gate-decision log is load-bearing, not telemetry: it resolves the judges' "live min_liq/session-vol semantics untested" flaw by making live-vs-replay population drift measurable in day one instead of discovered at grading.

## 2) Pre-registered grading bar and kill criteria

**Grading bar (standing, no promotion talk before ALL):** n>=30 entries, >=5 distinct UTC days, >=20 distinct tokens, drop-top-2-trades still positive, all in dead-token-corrected fidelity dollars (daily zeroing vs `_dead_tokens.json` process), benchmarked against tape median per `feedback_market_context_every_check`. Headline planning number is the **draw-distribution p10 (+$113/16d replay), never the first-come headline (+$202)**.

**Kill criteria (any one, pre-registered):**
- Any dead/unsellable-token position booked as a win that survives fidelity correction — instrument failure, halt immediately.
- Fidelity net <= -$40 cumulative in week one, OR 4+ consecutive red days.
- Win rate < 40% at n>=25.
- Entries/day sustained >20 (gate leak) or <2 (population mismatch vs replay's 8.2/day) for 3+ days after the first 24h warm-up — means the live instrument is not the backtested one; fix or kill, do not grade.
- Session-vol/liq gates blocking >50% of otherwise-passing signals — semantics mismatch; recalibrate, restart grading clock.
- SL1 shadow-track: if the SL1-vs-parent premium widens past -0.3pp/trade forward, revisit SL1 first (pre-registered lever; kept now per the triple-validated loss-conversion framework despite ~-$0.05/e in-cell cost).

## 3) Honest expected performance (fidelity dollars)

Replay of the exact final stack: FC15 n=131, 98 pools, +$202 total, $1.54/e, wr 66%, dt2 +$172, 13/16 days green, worst day -$4.8, 8.2 entries/day; 300-draw resample mean +$148, p10 +$113, 100% positive; population floor (no budget, n=660) **+$0.63/e**, dt2-robust, **$0.00 dead-positive dollars**; dense-3-day share only 36% of dollars (better than any original design).

Forward expectation after the judges' discounts (in-sample threshold decay, single regime era, 4.8% failed-tx, dead-booking softness): **+$0.25 to +$0.90 per entry at 5-10 entries/day = roughly +$2 to +$8/day; two-week burn-in +$25 to +$110.** Downside on a regime flip: SL1 caps typical loss at ~-$2.6/trade (hard stop ~-$5.6 at tier size), worst replay day -$4.8; a genuinely hostile week should land ~-$20 to -$40, which is exactly what the kill line catches. Breakeven-to-slightly-red forward is a real possibility and would be a legitimate answer, not a failure of process.

**Explicitly accepted flaws:** single-era evidence (79% of candidates from 3 days — forward paper IS the test, nothing pre-fixes this); age>=24h band chosen in-window (gradient independently reproduced by a judge, $0.12 vs $0.49/e — likely signal, boundary fit); dead-booking softer than -90 framing (mitigated by daily fidelity zeroing, and this cell's dead exposure is 2/660 with $0 positive); UTC-midnight budget reset (tested as variance).

## 4) What kills it in week one

The single most likely killer: **the live gate population diverges from the replay population** — session-vol undercounting on late-watched pools plus the untestable min_liq=30k makes the seat fire a different (thinner, or worse, different-shaped) stream than the one graded. That is why the gate-decision log ships in the same commit as the gate. Second: the market re-enters the 07-13..18 sick-window structure that NO replay day overlaps — if aged deep dips stop bouncing (the SOL demand-vacuum failure mode arriving on RH), the 4-red-day / -$40 lines trigger and the seat stands down with ~1.5% of the wallet spent on the answer. Either death produces the next iteration's spec, not a doom verdict.

## Original designs (headline results)
[
 {
  "name": "rh_bounce_pro \u2014 Flush-Turn Bounce Seat (RH, paper)",
  "result": "n=196 entries over 23 traded days (7.0/day): +$409.23 total = $2.09/entry ($1.62 per $25-notional-unit), win rate 58%, DROP-TOP-2 still +$324.66, days green 19/23, worst day -$11.82, worst single trade -$9.59, 135 distinct pools, median hold 14 min (p90 93 min). Split-half: first 14 days +$42.11 ($1.36/e, n=31, 6/9 days green \u2014 thin tape coverage), last 14 days +$367.12 ($2.23/e, n=165, 13/14 days green). Arm split: flush $2.85/e (wr 63%, dt2 +$289), phoenix $0.55/e (wr 49%, dt2 +$21, 13/17 days green). Uncapped gate floor (no concurrency thinning) is independently positive: +$1,543 on n=2,475 ($0.62/e, 17/22 green, dt2 +$1,508). Baseline all-candidates same ladder: -$0.27/e. Zero res=dead entries in the final selection."
 },
 {
  "name": "RH SNIPER \u2014 regime-first conviction seat (rh_sniper_regimefirst)",
  "result": "Flat $25: n=174 entries, 114 pools, 17 trading days armed / 10 stood down; total +$170.5 = +$0.98/entry, win rate 58%, drop-top-2 trades +$134, drop-top-2 pools +$120, days green 14/17, worst day -$13.2; chrono halves +$1.08/+$0.87 per entry, parity +$1.02/+$0.94; ~+$10/armed-day, ~+$0.88-0.93/entry after 4.8% failed-tx haircut. Rug share 2.9%; res mix: 162 closed / 7 t4h / 5 dead(-90% booked). Tiered sizing (secondary, in-sample tier boundaries): +$379 total (+$2.18/entry-avg, drop-top-2 +$268, same 14/17 green, worst day -$19.9). Regime gate ablation: without pay gate +$0.63/entry; without knife-skip +$0.44/entry \u2014 both components carry weight independently."
 },
 {
  "name": "RH AUTHSTACK-PRO (rh_pro_authstack) \u2014 one concentrated aged-deep-dip seat on RH",
  "result": "FINAL (tiered sizing, budget 15, first-come): n=118 over 11 days, +$157.5 total, +$1.335/entry, win rate 65.3%, drop-top-2-trades still +$127.3, 10 days green / 1 red (worst day -$4.80), 10.7 entries/day, 118 unique pools >=... 400 unique tokens in the parent cell. Selection-luck-free: 300 random 15/day draws mean +$112.4, p10 +$66.6, p90 +$156.5, 100% of draws positive. Flat-$25 version: +$117.5, +$0.995/entry, dt2 +$97.3. Population (no budget, n=835, 400 tokens): +$0.466/entry, wr 0.533, odd-days +$0.681 / even-days +$0.380, dead-token exposure 3/835 (0.36%). Apply the measured 4.8% failed-tx haircut: ~+$150 first-come / ~+$107 draw-mean expectation. Meets n>=30, >=5 days, >=20 tokens, drop-top-2, fidelity-dollars bars IN REPLAY \u2014 still needs the forward paper burn-in before any promotion talk."
 }
]