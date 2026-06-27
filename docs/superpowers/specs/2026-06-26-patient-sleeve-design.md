# Patient Sleeve — Design Spec (2026-06-26)

**Status:** design, approved by AxiS (winner-selection-gated entries + paper sleeve bot).
**Origin:** the winner-vs-us comparison ([[reference_winner_comparison_2026_06_26]]). The only
finding that points to doing something structurally *different* from our fast-scalp identity:
the 10 decoded consistent winners catch the fat tail by **holding the right entries for hours**
with wide stops. The workflow proved holding longer **destroys** P&L on our *average* entry
(median trade never moves) — so a patient sleeve is only viable if it holds the **+tail-worthy
subset**. This sleeve = our offense signal (winner-selection) + winner-style exits.

## Goal
Measure, in PAPER as a clean A/B, whether holding winner-selection-qualified entries with
winner-like exits captures more tail (higher realized **mean**) than our ~5.6-min time-box —
*before* any live capital. Judge on mean + tail-capture, not median (fat-tail: median stays
negative regardless).

## Non-goals
- Not live (paper only this phase; live probe is a separate later decision).
- Not conviction sizing (memory: conviction trigger-count sizing caused drawdown). Fixed size.
- Not a per-token cap or downsizing (operator-rejected levers).
- Not changing any existing bot — the sleeve is additive and the time-box bots are the control arm.

## Architecture
A new **paper** bot config `patient_sleeve` that rides the existing scanner's entry detection
(so it sees the same candidates as the badday fleet), but:
1. **Entry-gates** on the winner-selection signal — fires only when
   `winner_demand_selected(median_buy_size_usd) is True` (>=34.3: deep capitulation met by real
   buyer size). The time-box bots take the same entries → paired A/B on identical tokens.
2. **Exits** like a winner, not a scalper: long max-hold, wide ~-22% stop, runner-trail, no fast
   bails. Keeps rug guards (winners eat -22%, never -96% rugs).

```
scanner detects dip candidate
  -> badday_* bots: existing entry stack -> TIME-BOX exits (CONTROL arm)
  -> patient_sleeve: same candidate, ONLY if winner_demand_selected -> PATIENT exits (TEST arm)
        (non-badday bot_id => auto-skips the badday-scoped -7 IN_FLIGHT_FLOOR + entry stack)
```

## Components

### 1. New config `config/bots/patient_sleeve.json`
Paper, enabled. Key `BotConfig` fields (real field names from `core/bot_config.py`):
- `bot_id: "patient_sleeve"` — deliberately NOT `badday_*`, so it auto-skips the badday-scoped
  `IN_FLIGHT_FLOOR` (-7), `not_dipping`, `structure_edge`, `winner_demand_size`-shadow scoping.
- `base_position_usd`: match the badday bots (e.g. 75–100) for a comparable A/B.
- `max_concurrent_positions`: **wide** (e.g. 20) — the Little's-Law bill: ~91-min holds need
  ~16–50× the 3-slot budget or they starve entries. Free in paper; flagged as real capital live.
- `hard_stop_pct: -22.0` — the validated winner floor (vs our -6/-15). The whole downside thesis.
- `time_stop_minutes: 240` (4h) — patient, vs the ~5.6-min effective time-box. (None = unbounded
  is an alternative; 240 bounds paper bag accumulation. Decide in plan.)
- `trail_pp`: keep the runner-trail to ride winners (existing post-TP1 trail mechanic).
- `tp1_pct`: set HIGH or partial — to catch the tail we must not cap winners at +5%. Plan decides
  between (a) no TP1 + pure trail, or (b) small partial TP1 (e.g. 25% at +15%) then ride. Lean (b).
- Fast-exit knobs OFF (these are the scalp mechanics we're testing AGAINST):
  `fast_bail_pnl_pct: null`, `giveback_floor_*: null`, `pre_stop_bail_pnl_pct` very low,
  `slow_bleed_*` disabled/loose, `flat_exit_minutes: null`, `stall_exit_minutes: null`.
- Rug guards ON: do NOT set `antirug_floor_exempt`; leave `rug_bundle` fleet default.
- `daily_loss_limit_usd`, `max_token_buys_per_day`: keep sane paper guards.

### 2. Winner-selection ENTRY gate (small addition, `feeds/dip_scanner.py` ~1907–1935)
The signal is already computed there (`winner_demand_selected(_ar_meta.get("median_buy_size_usd"))`)
but only *recorded* (shadow). Add: when `bot_id == "patient_sleeve"` (or a new BotConfig flag
`winner_select_entry: bool = False`), **block the buy** unless `_wsz_sel` is True. Fail-OPEN only
if `median_buy_size_usd` is missing? No — fail-CLOSED for this bot (no signal => not a qualified
+tail entry => skip), since the whole point is to hold only qualified entries. Use the existing
per-cycle dedup pattern. Prefer the `winner_select_entry` flag over hardcoding bot_id (cleaner,
testable, reusable).

### 3. No new exit code
Reuses existing `PerBotPositionManager.tick()` exits driven by the config fields above
(hard_stop_pct, time_stop_minutes, trail). The breakeven-lock gate (just shipped) is independent
and stays shadow; the sleeve does NOT use it (it's testing the *opposite* — hold through dips).

## Data flow / measurement (the A/B)
Both arms log to the same trades ledger (existing). Analysis (a script, not a live component):
- **Pairs:** tokens bought by BOTH `patient_sleeve` and a `badday_*` time-box bot.
- **Compare:** realized `pnl_pct` per arm on the same token; report mean, median, tail-capture
  (share of trades realizing >+25%), -22%-stop hit rate, and hold-time delta.
- **Bar to advance to a live probe:** patient arm **mean** beats time-box arm mean by a margin
  that survives the execution haircut (~1.5% round-trip) AND tail-capture is materially higher,
  at **n>=30 paired tokens, >=10 distinct**, held-out by time half.

## Testing (TDD)
- `winner_select_entry` gate: unit test the entry-block helper (block when not selected / signal
  missing; allow when selected). Mirror the existing gate-helper test pattern.
- Config load: `patient_sleeve.json` parses (BotConfig.from_json raises on unknown fields — so
  any new flag MUST be declared first; see C1 deploy-breaker history).
- tick() patience: a position that dips to -10% then recovers is NOT exited (no fast bail, stop
  at -22); exits at -22; rides via trail; time-box at 240min.
- Pre-live invariants suite stays green (paper bot, but verify no live blast-radius change).

## Risks & caveats (carried into the plan)
- **Paper overstates patient holds** (stale-price illusion + deep-stop gap-through): the -22 stop
  and long holds fill WORSE live than paper shows. Paper validates the *thesis*; live probe is the
  real test. State this in any result.
- **Slot/capital bill is real live**: 20 concurrent slots × size = the live capital commitment;
  paper hides it. Report the implied live capital alongside any go-live rec.
- **Fat-tail**: the sleeve will NOT have a green median; judge on mean + tail-capture only.
- **Survivorship in the winners' edge**: their hold-stats excluded open bags; our paper sleeve
  books everything, so our measured numbers are the honest (likely lower) version — a feature.
- **Don't conviction-size / don't widen to all entries** — both re-introduce the bleed the
  workflow proved.

## Open questions for the plan
1. TP policy: no-TP1 pure-trail vs small partial-TP1-then-ride (lean: partial 25% @ +15%).
2. `time_stop_minutes` 240 vs unbounded.
3. `winner_select_entry` flag vs bot_id check (lean: flag).
