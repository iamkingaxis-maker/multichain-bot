# Liquidity-Conditional Exit-Tail — Design

**Date:** 2026-06-24
**Status:** design (approved approach: liquidity FLOOR, badday family + live probe first)
**Author:** AxiS + Claude (Opus)

## Goal

Stop the live exit **gap-through tail** — the occasional sell that fills 20–30%+
worse than mid because the book was too thin to absorb the exit — by refusing
(or, later, shrinking) entries into tokens we cannot exit cleanly. "Don't enter
what you can't exit." This widens realized net by killing the single largest
remaining exit leak, without touching the loss-cut logic (in-flight floor, hard
stop, falling_day_flush, giveback) that is already enforced and working.

## Problem (data-grounded, 2026-06-24)

Real **exit** fills from the live $5 probe (`/api/live-swaps`, n≈60 sells):

- **Median sell slippage 1.27%** — typical exits fill clean.
- **Tail blows out:** ANT **30.3%**, $CWIF **17.6%** fill-vs-mid on the sell.

A single 30% gap-through does more P&L damage than dozens of clean 1.3% exits.
And it is **un-fixable with exit logic**: when the fill itself gaps past the
price, no stop/floor/trail can catch it (the in-flight-floor code comment says
exactly this: "live microcap stops gap THROUGH … that residual is a separate
feed-gap guard"). Paper hides it entirely (paper fills *at* the stop), so the
live probe is the only source of truth. The only real lever is to **constrain
entry to books deep enough to exit** — which also coincides with the
structure-edge `liq≥48k` arm (deep book = better entry edge *and* clean exit).

### Why it isn't already solved by existing liquidity gates

- Fleet anti-rug floor `liq≥25k` (enforce) — a rug guard, not exit-calibrated;
  the gap-throughs happened above it.
- Structure-edge `liq≥48k` arm (shadow, `OR pc_h6≥0`) — passes a thin book when
  `pc_h6≥0`, so a reclaimed-but-thin token still enters and can gap on exit.

This gate is a **tighter, exit-calibrated liquidity floor scoped to the
badday family + the live probe**, layered on top of the fleet anti-rug floor.

### Blockers found while scoping

1. **`liquidity_usd` is `None` on every live-swap record (0/28).** The schema
   field exists in `core/live_swap_log.py:REQUIRED_FIELDS` but the emit site
   passes nothing — so slip cannot be correlated to liquidity.
2. **`core/fill_calibration.py` only processes BUYS** and buckets by
   `liquidity_usd` (thin/mid/deep) — so today, with liquidity `None`, every
   record falls in "unknown" and the calibration is a no-op for exits.

The scaffold is half-built; this design finishes it and points it at sells.

## Architecture — 4 parts, in dependency order

Approach: small, reversible, env-flagged additions. Pure logic in helper
modules (unit-testable); thin wiring in the scanner. Every new gate is
`off`/`shadow`/`enforce` and **fail-OPEN on missing liquidity** (never block a
trade because a feature is absent). Money path stays deterministic; the
calibration is a *measurement tool*, not an input the live gate reads at
decision time (keeps the gate simple and the threshold human-reviewed — honoring
the no-fit-from-thin-data rule).

### Part 1 — Capture liquidity on every live swap (PREREQUISITE)

Pass the token's `liquidity_usd` (and `mcap`) into the `log_live_swap(...)` calls
at both the BUY and SELL emit sites in `feeds/dip_scanner.py`. Both values are
present in the pair/bundle at decision time (already resolved for other gates,
e.g. `_ar_liq` for the anti-rug floor). No schema change (fields already in
`REQUIRED_FIELDS`). After deploy the log backfills naturally; nothing downstream
calibrates until this lands.

- **Files:** `feeds/dip_scanner.py` (buy emit ~`_emit_buy_telemetry`; the sell
  emit site), locate by the `log_live_swap(` calls.
- **Risk:** none — pure telemetry, fail-open (None stays None as today).

### Part 2 — Extend `fill_calibration` to sells (MEASUREMENT)

Add sell-side calibration: per-liquidity-bucket **exit** slippage, capturing the
**tail** (`slip_p90` and max), not just `slip_p50`. Surface it so a human can
read the thin/mid/deep exit-slip table and choose the floor.

- **Files:** `core/fill_calibration.py` — add a `side` parameter (or a parallel
  `calibrate_exit_from_live_swaps`) that aggregates `side=='sell'` successful
  records the same way buys are aggregated today; keep the existing buy path
  unchanged. Expose via the existing `/api/fill-speed`/calibration surface (or a
  new read-only field) so the table is visible.
- **Output shape:** `{thin/mid/deep/overall: {slip_p50, slip_p90, n}}` for sells.

### Part 3 — Liquidity-exit-floor gate (the lever; SHADOW first)

A pure predicate + thin wiring, modeled on `structure_edge_blocks`:

- **Pure helper** in `core/bot_evaluator.py`:
  `liquidity_exit_floor_blocks(liquidity_usd, floor_usd) -> (bool, str)` —
  block when `liquidity_usd` is a finite number **and** `< floor_usd`; **fail-OPEN**
  (return `False`) when liquidity is `None`/NaN (can't disprove safety → don't
  block). Pure, never raises.
- **Wiring** in `feeds/dip_scanner.py` entry path (alongside the structure-edge
  gate): resolve `LIQ_EXIT_FLOOR_MODE` (`off`/`shadow`/`enforce`, default
  `shadow`) and `LIQ_EXIT_FLOOR_USD` (env, the calibrated threshold). **Scoped to
  the badday family + the live probe** (`bot_id.startswith("badday_")`), matching
  the in-flight-floor scope boundary. Forward-candle-scored via `record_verdict`
  (`filter_name="liquidity_exit_floor"`), guarded by the auto-rollback watcher
  (add to `MONITORED_GATES`), and per-cycle deduped (mirror `_se_shadow_seen`).
- **Threshold source:** `LIQ_EXIT_FLOOR_USD` is set by a human from Part 2's
  exit-slip table — NOT auto-read from calibration at decision time. Default
  stays conservative/shadow until the data justifies a value.

### Part 4 — Accrue → calibrate → enforce (VALIDATION GATE)

Run Parts 1–3 with `LIQ_EXIT_FLOOR_MODE=shadow`. Accrue live exit-slip-by-liquidity
until there are enough thin-book exits to read the tail (bar: **≥10 thin-bucket
exits with measured slip**, and the floor's blocked-cohort forward outcome is
checked by the rollback watcher so we don't clip winners). Then set
`LIQ_EXIT_FLOOR_USD` to the liquidity below which exit `slip_p90` exceeds the
target (start target ≈ **8%**), and flip `LIQ_EXIT_FLOOR_MODE=enforce`. No
threshold is fit from the current n≈2 gap-throughs.

## Data flow

```
decision-time pair (liquidity_usd, mcap)
   │
   ├─► Part 1: log_live_swap(..., liquidity_usd, mcap)  → live_swaps.jsonl
   │
   ├─► Part 3: liquidity_exit_floor_blocks(liq, LIQ_EXIT_FLOOR_USD)
   │            shadow → record_verdict(liquidity_exit_floor)  (forward-scored)
   │            enforce → block entry (badday/probe only), fail-open if liq None
   │
   └─► (offline) Part 2: calibrate_exit_from_live_swaps(records)
                 → thin/mid/deep exit slip_p50/p90  → human sets LIQ_EXIT_FLOOR_USD
                 → Part 4 enforce
```

## Error handling / safety

- Every helper PURE + FAIL-OPEN: missing/NaN liquidity → do **not** block.
- Gate scoped to badday + probe; never touches other bots.
- `enforce` blocks ENTRY only — never alters an open position's exits (those
  remain governed by the already-enforced loss-cut stack).
- Auto-rollback watcher reverts the gate to shadow if its blocked cohort is
  forward-winning (clipping winners).
- Threshold is human-set from reviewed data; the live gate never fits from a
  thin sample (honors the forecast-calibration rule).
- No `PAPER_MODE` flip; live A/B not part of this build.

## Testing

- `liquidity_exit_floor_blocks`: blocks below floor, passes at/above,
  fail-open on `None`/NaN, env-overridable floor, explicit-arg-wins.
- `calibrate_exit_from_live_swaps`: buckets sells by liquidity; tail (p90)
  computed; ignores buys/failed/garbage; empty → `{}`; thin-sample safe.
- Part 1: a `log_live_swap` with `liquidity_usd` writes it to the record
  (extend the existing live-swap-log test).
- `test_pre_live_invariants.py` stays green (run before any enforce/live).

## Open questions resolved

- **Mechanism:** liquidity FLOOR (not proportional sizing) — simpler, matches
  existing gates; size-cap is a possible later layer.
- **Scope:** badday family + live probe first; widen fleet-wide only after the
  floor is proven on the probe's real exit data.
- **Threshold:** human-set from Part 2's exit-slip table at the Part 4 bar; not
  auto-fit. Start target exit `slip_p90` ≈ 8%.

## Out of scope

- Proportional liquidity sizing (possible follow-on).
- Winner-side structure-conditional TP (separate idea, gated on structure data).
- Fleet-wide rollout (after badday/probe proof).
- Any live A/B or `PAPER_MODE` change.
