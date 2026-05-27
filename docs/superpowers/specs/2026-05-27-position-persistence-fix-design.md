# Multi-Bot Position Persistence Fix — Design (2026-05-27)

## Problem

Multi-bot open positions are **not faithfully persisted/restored across restarts**,
and the dashboard open-position count is computed from a ledger formula that
doesn't match the real position book. Together they produce: per-bot open counts
6–7× over `max_concurrent` (no_filters 3→20), inflated/leaked `in_flight`
(~$397 stuck on no_filters), and "open" positions 24–48h+ old that will never close.

### Root cause (confirmed in code)

1. **Restart orphaning.** `feeds/dip_scanner.py::_restore_open_positions_from_trades`
   rebuilds `PerBotPositionManager._positions` from `trades_multi.json` but is
   lossy: it **skips any (bot, token) that has ANY sell** (line ~369) and restores
   only the first buy of a zero-sell token, dropping `tp1_hit` / `remaining_fraction`.
   So a position that was **post-TP1 (has a partial `fully_closed=False` sell) at
   restart** is never rebuilt → its trail/TP2/stop never fires → its terminal
   `fully_closed=True` sell is never written and its `in_flight` is never released.
2. **Over-counting metric.** `dashboard/web_dashboard.py::_build_bot_rows` computes
   `open_position_count = Σ_token max(0, buys − fully_closed_sells)` over the whole
   ledger. The live manager holds ≤1 position per token (dict keyed by token) and
   ≤ `max_concurrent` total, so a re-entered/orphaned token inflates the count far
   past reality.
3. **Amplifier.** ~5 deploys/restarts on 2026-05-27 each orphaned the then-open
   set; orphans accumulate.

Impact: (a) distorts the unrealized book that `--unrealized` bot comparisons rely
on; (b) slowly starves each bot's tradeable balance as `in_flight` leaks; (c) would
strand real capital if promoted to live.

## Approach

**Persist the real position book as the source of truth; restore from it; report
from it; one-time reconcile the existing leak.** Chosen over patching the ledger
reconstruction because the in-memory `PerBotPositionManager._positions` IS the
authoritative state — the bug is that it isn't durably saved. Reconstructing from
an append-only trade log can never recover `tp1_hit`/`remaining_fraction`/peak and
is inherently ambiguous on partial exits.

### Part 1 — Persist & restore `pm._positions`

- Extend `bot_state/{id}.json` with an `open_positions` list. Each entry serializes
  the fields needed to resume management: `token, entry_price, size_usd, entry_time,
  address, pair_address, tp1_hit, tp2_hit, peak_pnl_pct, peak_pnl_at_secs,
  remaining_fraction`.
- Write the snapshot whenever a bot's book changes (after the per-cycle position-
  management pass in `dip_scanner` that opens/ticks/closes positions). Snapshot is
  cheap (≤ a few positions/bot) and piggybacks on the existing per-cycle bot_state
  save.
- On boot, restore `pm._positions` from `open_positions` (authoritative, lossless),
  replacing `_restore_open_positions_from_trades`. Keep the trades-reconstruction
  only as a one-time fallback when `open_positions` is absent (pre-fix state) — and
  in that fallback, restore NOTHING (treat as clean slate; see Part 3).

### Part 2 — Fix the count

- `_build_bot_rows` reports `open_position_count = len(state["open_positions"])`
  (real held positions), not the `buys − sells` formula. Falls back to the old
  formula only if `open_positions` key is missing (transitional).

### Part 3 — One-shot reconcile (sentinel-guarded migration)

On first boot with the new system, each bot has no persisted `open_positions` yet,
and its `in_flight` is polluted by unrecoverable orphans. Reset to a clean slate:

- `open_positions = []`, `in_flight_usd = 0.0`,
  `balance_usd = paper_capital_usd + realized_pnl_total_usd`.

Rationale: the orphaned positions have no live manager entry, so they can never be
closed — booking them as released-at-cost (the leaked capital returns to balance)
is the only honest, bounded resolution. Realized P&L is untouched (the research
signal is preserved); only the stuck open-book capital is freed. Sentinel
`.positions_reconciled_v1`, backup bot_state first, never breaks boot — same pattern
as the GIGA/EURC scrubs.

## Files

- `core/per_bot_position_manager.py` — add `to_state_list()` / `load_state_list()`
  (serialize/deserialize `_positions`).
- `core/multi_bot_persistence.py` — persist `open_positions` in save_bot_state;
  add `_maybe_reconcile_positions()` boot hook (Part 3).
- `feeds/dip_scanner.py` — restore from `open_positions` on init; snapshot the book
  after each per-cycle management pass; retire the lossy trades-reconstruction.
- `dashboard/web_dashboard.py` — count = `len(open_positions)`.
- `scripts/reconcile_positions.py` — the sentinel-guarded one-shot (Part 3).
- Tests: `tests/test_per_bot_position_manager.py` (serialize round-trip),
  `tests/test_position_persistence.py` (restore lossless incl. tp1_hit/partial),
  `scripts/reconcile_positions.py --selftest`.

## Invariants to preserve

- `balance_usd + in_flight_usd − realized_pnl_total_usd ≈ paper_capital_usd` per bot.
- `in_flight_usd == Σ open_positions[i].size_usd * remaining_fraction[i]`.
- A bot never holds two positions of the same token; never exceeds `max_concurrent`
  for NEW opens (restore may temporarily exceed, by design).
- Run `tests/test_pre_live_invariants.py` before deploy (paper, but the invariant
  suite guards the accounting).

## Rollout

Commit → push → deploy. On boot: reconcile fires once (resets in_flight, frees
leaked capital, counts → real). Verify via SSH/`/api/bots`: open counts drop to
≤ `max_concurrent`, `in_flight` reflects only live positions, balances reconcile.
Then confirm over the next cycles that positions persist across a subsequent
restart (open a position, redeploy, confirm it survives and later closes normally).

## Risks

- **Snapshot frequency**: must snapshot on every book change or a crash between
  change and save re-orphans. Mitigation: snapshot in the same per-cycle path that
  already saves bot_state; accept ≤1 cycle of loss (matches existing durability).
- **Reconcile discards genuinely-live positions** open at deploy time. Acceptable:
  the old restore already orphaned them on every restart, so this is consistent
  with current reality and bounded (paper).
- **Capital math**: the reconcile must keep the balance invariant exact. Covered by
  the invariant test + a backup of bot_state.
