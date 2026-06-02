# Phase-2 Trajectory De-Risk — Design Spec (2026-06-01, RE-AIMED 2026-06-02)

**NORTH STAR (same as Phase-1): a feature of the PRODUCTION BOT, not fleet tuning.**

> ⚠️ **RE-AIMED 2026-06-02 — the lever is DE-RISK the LOW cohort, NOT scale-in the HIGH
> cohort.** The trajectory all-in round (wilj94wuf) validated the +8-min SHAPE signal on
> the bot's own trades (off-GACHA scorer AUC **0.607, p=0.01**; HIGH−LOW pnl gap **+4.16pp,
> p=0.0004**; leak-free) — BUT, deduped + off-GACHA, the HIGH-score cohort is only
> **break-even (+0.05%) and jackknife sign-flips**, while the LOW-score cohort is **durably
> negative (−4.12%, stable both time-halves)**. So *adding* size to the high cohort is
> value-neutral-to-NEGATIVE (−$2…−$12/day — burns fees on a break-even cohort), whereas
> **de-sizing / early-exiting the LOW cohort is +$7…+$19/day off-GACHA, ALL of it
> loss-avoidance** (erodes to ~$0 past an ~8pp add-leg haircut). This matches the standing
> fleet thesis (edge = loss-avoidance + sizing, NOT winner selection). The signal/mechanism
> below are unchanged; the ENFORCE DECISION flips to the low cohort.

**Premise (validated):** entry-snapshot features can't tell a runner from a pump-and-dump
(held-out AUC 0.52). But the **first-8-min demand-trajectory SHAPE** predicts CONTINUATION
at **held-out-by-token AUC 0.765** universe / **0.607 off-GACHA on bot trades** (SHAPE only:
peak_position, vol_sustain_ratio, minutes_to_peak, higher_low_n — no price-level leak). The
useful, durable end of that signal is the LOW end: it reliably flags the dying cohort.

**Core idea (re-aimed):** at the **~+8-min checkpoint** the production bot scores the live
trajectory; on the **LOW-score cohort it DE-RISKS — holds-small (does not commit full size)
or early-exits** — while the high cohort just runs the normal lifecycle. The value is
avoiding the low cohort's losses, NOT amplifying the (break-even) high cohort.

**Validation on the bot's REAL trades (2026-06-01, gate decision):**
- The +8-min continuation model (universe-trained, AUC 0.765) applied to 652 real bot
  trades / 50 tokens: the top score-quartile (Q4) realized **67% WR / +5.93%** (peak
  +9.93%) vs overall ~54%/+0.5%; bottom-mid (Q2) **36% WR / −3.75%**.
- **GACHA-robustness (the trap that killed score2):** Q4 is only 16% GACHA; **excl-GACHA
  Q4 = 61% WR / +1.01% across 27 distinct tokens** — a durable (modest) cohort edge, NOT
  a single-runner artifact.
- **What does NOT work:** loosening the exit to "let Q4 run" — Q4's give-back is only 4pp
  (the tight exit already captures ~60% of peak), and the let-run mechanism is falsified
  (workflow: real trails give back + stops slip). So KEEP the tight exit; tilt SIZE only.
- **Caveat:** full-curve realized AUC is 0.60 and fold-fragile (0.47–0.77); off-GACHA edge
  is +1.01% (real but small). Hence GENTLE (≤1.5×) + SHADOW-FIRST + more-data before enforce.

---

## The scorer
- A small **continuation model** = the persistence model (`HistGradientBoostingClassifier`
  on the SHAPE features), trained on the universe corpus + (validation) the bot's own
  trades. Outputs P(continuation ≥ +5% beyond the +8-min price).
- **Non-stationarity:** retrain on a trailing window (the nightly analyzer retrains + writes
  the model artifact), per the rolling-scorer lesson. Never a frozen one-shot.
- **Thresholds (RE-AIMED to de-risk):** the action is on the LOW end. `P < low_threshold`
  (~0.6 — the cohort that, deduped/off-GACHA, realizes −4.12% and is jackknife-stable
  negative) → **DE-RISK** (hold-small or early-exit). `P ≥ low_threshold` → run the normal
  lifecycle (do NOT add size — the high cohort is only break-even, so adding burns fees).
  An optional deeper `P ≤ exit_threshold` (~0.3, Q1 26% continue) early-exits the worst.

## Trajectory tracking (no extra fetches)
- The per-bot tick loop (`dip_scanner._tick_all_bots_positions`) already fetches each
  position's price every cycle. **Accumulate the first-8-min price/volume path on the
  position** (`OpenPosition.state_blob["traj"]` = list of (t, close, low, vol)).
- At the **first tick ≥ entry+8min**, compute the SHAPE features from the accumulated path
  (reuse `compute_trajectory_features` logic) → score with the model. Stamp the score +
  features onto the position once.

## Rollout — shadow first (production-bot-scoped)

**Phase 2a — SHADOW (no behavior change) — ALREADY BUILT (commit b6f8db6, deploy 8aa8f589):**
the tick loop accumulates the first-8-min path and stamps the SHAPE features
(`scalein_peak_position`/`_minutes_to_peak`/`_frac_above_entry`/`_higher_low_n`/
`_vol_sustain_ratio`/`_n`) on every sell. Scored OFFLINE (no model in the hot path). The
nightly analyzer scores the stamped shape and measures the **DE-RISK benefit**: the realized
loss of the LOW-score cohort (what early-exit/hold-small would have avoided) and that the
HIGH cohort is at-least-non-negative. Zero behavior change; accruing now.

**Phase 2b — ENFORCE the DE-RISK tilt (after 2a firms, production config only):**
- Config (`BotConfig`): `traj_derisk_enabled: bool=False`, `traj_low_threshold: float=0.6`,
  `traj_derisk_action: str="hold_small"` (or `"early_exit"`), `traj_hold_small_frac: float=0.4`.
- **`hold_small` variant:** enter at `hold_small_frac × size_usd`; at the +8-min checkpoint,
  fill up to full size ONLY if `score ≥ low_threshold` (high cohort) — the LOW cohort stays
  small. (Net effect = de-sizing the durably-negative low cohort; the high "fill-up" adds
  ~$0 but is harmless.)
- **`early_exit` variant:** enter full size; at +8-min, if `score < low_threshold` (esp.
  `≤ exit_threshold`), EXIT the position early (cut the dying cohort before it bleeds to
  slow_bleed/hard_stop) — the loss-avoidance lever directly.
- High cohort: **do NOT add size** (it's break-even; adding burns fees — the falsified
  scale-in lever). TP1/TP2/trail/hard-stop lifecycle otherwise unchanged.
- **RE-GATE before enforce:** ≥100 unique off-GACHA tokens on the forward shadow, LOW-cohort
  negative-EV confirmed forward, HIGH-cohort non-negative by-token jackknife. Until then it
  stays a measurement probe. Enforce is a sizing/exit change → **needs user approval**.

## Files
- `core/per_bot_position_manager.py` — accumulate `state_blob["traj"]`; `add_to_position`
  (weighted-avg entry, capital reserve); the +8-min checkpoint hook.
- `feeds/dip_scanner.py` — in the tick loop, at entry+8min compute shape + score; shadow
  stamp (2a) or add/exit (2b). Entry path opens at `entry_fraction` when enabled.
- `core/bot_config.py` — scale-in fields.
- A scorer module (load the trailing-window model artifact the analyzer writes).
- `tests/` — trajectory accumulation, +8min checkpoint fires once, add weighted-avg math,
  capital reserve correctness, shadow stamps without behavior change.

## Dependencies & sequencing
1. **Bot-trade validation — DONE (2026-06-01):** the signal carries to the bot's realized
   outcome but WEAKLY (full-curve AUC 0.60, fragile) — yet the high-score cohort (Q4) is
   durably better (61% WR/+1.01% off-GACHA, 27 tokens). Verdict: enough to justify Phase 2a
   SHADOW + a GENTLE tilt; NOT enough to enforce a strong (2×) bet. More data needed for 2b.
2. **Phase 2a (shadow)** — ship now (low risk): wire trajectory tracking + the scorer,
   stamp `scalein_score` + realized outcome, let the analyzer measure forward + accumulate
   tokens (50 is thin). This is the next build.
3. **Phase 2b (gentle ≤1.5× tilt, enforce)** — only after 2a's forward data firms the
   off-GACHA edge on more tokens AND approval (paper→live). May stay shadow indefinitely if
   the modest edge doesn't firm — that's an acceptable outcome.

## Honest caveats
- 0.765 is on the universe corpus (continuation proxy) + one ~10-day regime; the bot-trade
  backfill + the forward shadow are required before sizing real money on it.
- Scale-in adds capital mid-trade — the reserve accounting must be exact (test-pinned) so a
  failed add can't strand capital. Sells/exits always take priority over adds.
- Adds happen at +8min, by which the price has moved — the add fills higher than entry on a
  continuing token (that's fine; you're paying for confirmation). Model the add-leg slippage.
