# Phase-2 Scale-In — Design Spec (2026-06-01)

**NORTH STAR (same as Phase-1): a feature of the PRODUCTION BOT, not fleet tuning.**

**Premise (validated this session):** entry-snapshot features can't tell a runner from
a pump-and-dump (held-out AUC 0.52). But the **first-8-min demand-trajectory SHAPE**
predicts CONTINUATION at **held-out-by-token AUC 0.765** (folds 0.74–0.79; monotonic
Q1 26% → Q4 80% continuation-rate; features: peak_position, vol_sustain_ratio,
minutes_to_peak, higher_low_n — SHAPE only, no price-level leak). So: stop betting full
size at t=0 when the answer isn't knowable — **enter small, let the token prove demand
is persisting, then size in.**

**Core idea:** production bot enters at a FRACTION of full size; at the **~+8-min
checkpoint** it scores the live trajectory and **adds the rest only to the high-score
cohort, holds-small on the low-score cohort.** This is a SIZE TILT, NOT an exit change.

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
- **Thresholds (from the validated quartiles):** add-size if P ≥ ~0.6 (Q3/Q4, 64–80%
  continue); hold-small if mid; consider early-exit if P ≤ ~0.3 (Q1, 26%).

## Trajectory tracking (no extra fetches)
- The per-bot tick loop (`dip_scanner._tick_all_bots_positions`) already fetches each
  position's price every cycle. **Accumulate the first-8-min price/volume path on the
  position** (`OpenPosition.state_blob["traj"]` = list of (t, close, low, vol)).
- At the **first tick ≥ entry+8min**, compute the SHAPE features from the accumulated path
  (reuse `compute_trajectory_features` logic) → score with the model. Stamp the score +
  features onto the position once.

## Rollout — shadow first (production-bot-scoped)

**Phase 2a — SHADOW (no sizing change):** enter full size as today, but track the +8-min
trajectory, score it, and **stamp `scalein_score` + the shape + the eventual realized
outcome into the sell record.** The nightly analyzer measures: does `scalein_score`
predict the bot's REALIZED win on actual filtered/sized trades (confirm the 0.765 holds
forward + on real trades, not just universe continuation). Zero behavior change.

**Phase 2b — ENFORCE (after 2a confirms, on the production config only):**
- Config (`BotConfig`): `scalein_enabled: bool=False`, `scalein_entry_fraction: float=0.4`,
  `scalein_add_threshold: float=0.6`, `scalein_exit_threshold: Optional[float]=None`.
- **Entry:** reserve + open at `entry_fraction × size_usd` (rest held in reserve).
- **+8-min checkpoint:** if `score ≥ add_threshold` → **add** the remaining `(1−fraction)`
  (reserve capital, increase the position; weighted-avg the entry price + slippage on the
  add leg). If `score ≤ exit_threshold` (when set) → exit the small position early. Else
  hold the small position through the normal TP/trail/stop.
- TP1/TP2/trail/hard-stop lifecycle unchanged — scale-in is a layer *before* TP1.

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
