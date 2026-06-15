# Gap-Through Detector — Scope (2026-06-15)

**Goal:** stop the catastrophic single-poll `-66%` losses that are meta_chameleon's (and the
fleet's) real dollar bleed — the ones a price-reactive stop can never catch.

## The problem (evidence)
From the 18-Opus overhaul (wf_b7e6cb8b-2a6): the 5 worst chameleon losses were **single-poll
gap-throughs** — peak `0%` or `+7..+10%`, then the very next poll shows `-66/-70/-71%`. The
`-12` hard stop and `-6` give-back floor both fired *after* the cliff, at the cratered price.
Two of them had already blown past the `-6` floor. **A tighter threshold cannot help** — the
move happens entirely *between* polls. This is why the adversarial layer DROPPED the `-12`
fast-bail "fix": you can't react to a price you only see post-collapse.

The lever must be a **leading indicator** that fires *before/as* the cliff forms, or a
**faster cadence** so the stop sees the move mid-fall. Leading-indicator is higher leverage.

## Mechanism A (primary): liquidity-drain bail
A rug/dump drains LP (or a whale exits) — **liquidity falls before the price fully craters**.
Monitor each held position's current liquidity vs its entry liquidity; on a sharp drain, exit
at market *immediately*, regardless of the (lagging) price tick.

**Existing infra (feasible, low new surface):**
- `feeds/liquidity_flow.py` — `LiquidityFlowTracker` already records **per-token liquidity over
  time** (wired in `dip_scanner.py:270-273`). This is the drain signal source.
- Entry liquidity is already captured (`entry_liquidity_usd`, seen at `dip_scanner.py:982`);
  if not stamped on the position, stamp it in `_execute_bot_buy` `_pos.state_blob`.
- Exit loop `dip_scanner.py:1879` already fetches per-token `current_price` + `vol_m5` each
  cycle (`vols[pkey] = await self._get_vol_m5_for(token)`). Add a sibling `liq` fetch (or read
  the tracker) the same way and pass it into `tick()`.
- New `ExitDecision` kind `LIQ_DRAIN_BAIL` in `per_bot_position_manager.tick()` (extend the
  signature with `current_liq`, `entry_liq`).

**Detection rule (tune in shadow):**
- `drain_from_entry = (entry_liq - current_liq) / entry_liq`
- Fire when `current_liq < LIQ_DRAIN_FLOOR_FRAC * entry_liq` (e.g. liquidity fell below 50% of
  entry) **OR** a one-cycle drop `> LIQ_DRAIN_CYCLE_PCT` (e.g. >30% gone since last poll).
- **Winner-safe guard:** only fire when also `pnl_pct <= 0` (don't bail a token that's draining
  liquidity *because* of a parabolic exit-into-strength). Re-evaluate this guard in shadow —
  the worst gap-throughs had peak `+7..+10`, so a pure `pnl<=0` guard might miss those; may need
  "liq drained AND (pnl<=0 OR fell >X pp from peak this cycle)."

## Mechanism B (secondary): faster cadence for at-risk holds
Poll held positions more frequently than the new-token scan (e.g. every few seconds for
positions younger than N minutes or in thin-liq mints) so a reactive stop sees the fall
mid-way. Tradeoff: API load/cost; rugs can still outrun any cadence. **Lower priority** than A
(A catches the cause; B only shrinks the reaction gap). Scope B only if A under-catches.

## Rollout (shadow-first, the house discipline)
1. **SHADOW:** stamp `would_liq_drain_bail` + the would-be exit pnl on every held position each
   cycle (no action). `LIQ_DRAIN_MODE=shadow`. Run fleet-wide.
2. **Measure** against the *actual* gap-throughs: does the drain signal fire *before* the
   `-66%` print? Catch-rate on real gap-throughs vs winner-kill rate (fires on a token that
   would have recovered). Target: catch the cliff, ~0 winner-kill.
3. **ENFORCE** (`LIQ_DRAIN_MODE=enforce`) only after the shadow shows it leads the cliff and
   doesn't shred winners. Fleet-wide, fail-OPEN on missing liq data (don't freeze trading).

## Env / tunables
`LIQ_DRAIN_MODE` (shadow|enforce|off, default shadow) · `LIQ_DRAIN_FLOOR_FRAC` (0.5) ·
`LIQ_DRAIN_CYCLE_PCT` (0.30) · winner-safe guard params.

## Open questions
- Is per-cycle current liquidity actually fresh for held tokens, or is it cached/stale? (Verify
  `LiquidityFlowTracker` update cadence vs the exit-loop cadence before trusting the cycle-delta rule.)
- Do paper fills model the drain realistically? A shadow-measured "saved" pnl must use the
  drain-moment price, not the cratered one, or it overstates the save.
- Interaction with the live per-token exposure cap (8641be3) and RUG_BUNDLE gate — drain-bail is
  the EXIT-side complement to those ENTRY-side rug guards (memory: ~50% of rugs are entry-
  indistinguishable -> exit/stop lever is the only catch for that half).

## Success metric
Cut the `<= -35%` gap-through tail (which carries ~35% of fleet loss) without measurable
winner-kill. This is the EXIT-side half of the rug problem the entry gates can't solve.
