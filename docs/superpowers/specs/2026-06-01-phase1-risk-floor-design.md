# Phase-1 Risk Floor — Design Spec (2026-06-01)

**Goal:** two un-glamorous, *validated* risk floors that cap downside regardless of
entry signal — a **per-bot daily-loss halt** and a **per-token re-entry cap**. Both
close gaps confirmed this session. Neither requires the mining-workflow params or the
persistence model (those gate Phases 2–3). Ship **measure-only shadow first**, enforce
only after forward-proof + explicit approval (paper→live discipline).

**Principle (from the 8hr postmortem):** you can't pick winners at entry, but you can
bound how much any single bad day or single token can cost. These are stop-losses on
*the system*, not edge bets.

---

## Component A — Per-bot daily-loss halt

**Problem (confirmed):** `core/per_bot_capital.py` `PerBotCapital` tracks `daily_pnl_usd`
with a UTC-00:00 rollover (`_check_daily_rollover`) but has **no halt** — a bot can bleed
unbounded in a day (the cap2k/defenders did; also surfaced chasing the −$219 artifact).
The global `risk_manager.py` has a halt, but the multi-bot fleet doesn't route per-bot
through it.

**Design:**
- **Config** (`core/bot_config.py` `BotConfig`): add
  `daily_loss_limit_usd: Optional[float] = None` (None = off, backward-compatible).
  Optionally `daily_loss_limit_pct: Optional[float] = None` (pct of starting balance,
  scales across $20 vs $650 bots). Production-candidate default: `pct = 5.0` (≈ −$100 on
  $2000). Resolve to a USD limit at load.
- **`PerBotCapital.should_halt_daily(limit_usd, now_iso=None) -> bool`**: calls
  `_check_daily_rollover(now_iso)`; returns `limit_usd is not None and self.daily_pnl_usd <= -limit_usd`.
- **Enforcement point:** `feeds/dip_scanner.py:_execute_bot_buy` (~L745), BEFORE
  `capital.reserve_for_buy` (L768). If halted → skip the buy, increment a reject counter,
  log `[DipScanner] daily-loss halt: bot=… daily=$… <= -limit`. **SELLs are never blocked**
  (closing a position must always be allowed).
- **Auto-clear:** at UTC 00:00 via the existing `_check_daily_rollover` (no extra code).
- **Mode flag** (`DAILY_HALT_MODE` env or per-bot config: `"shadow"` | `"enforce"`,
  default `"shadow"`): shadow = stamp `would_halt_daily` counter + log, **do not block**;
  enforce = block.

**Tests** (`tests/test_daily_halt.py`):
- `should_halt_daily` True when `daily_pnl_usd <= -limit`, False above, False when limit None.
- Resets to not-halted after a UTC-day rollover.
- A SELL is never blocked even while halted (enforcement-point test).
- pct→usd resolution correct.

---

## Component B — Per-token re-entry cap

**Problem (confirmed):** positions are **one per (bot, token)** (`_positions[token]`,
`open_position` rejects a second), so the death-spiral is *sequential* re-buys — one bot
bought **SPCX 16×**, BULL 12×, IDLE 11× (buy → stop → re-buy → stop …). `buy_counts` is
*tracked* (`trader.reentry.buy_counts`, trader.py:2009/2122) but **never gated**, and
`reentry_cooldown_secs` is `null` on most bots. Re-entry into a dumping token compounds
the loss; capping it is the implementable slice of the concentration lever a *single*
production bot controls (cross-bot crowding is Phase 2).

**Design:**
- **Config:** add `max_token_buys_per_day: Optional[int] = None` (None = off).
  Production default: **3** (analysis: cap≈3 captured most of the per-bot re-entry savings
  while keeping the early winners).
- **Per-bot per-token counting:** add to `PerBotPositionManager` a dict
  `_token_buys: {token: {"date": utc_date, "count": int}}`, incremented in
  `open_position`, reset when the UTC date rolls. Persist in `to_dict`/`from_dict`
  (survives restart, like the existing position state).
  - *Option (more robust to slow accumulation):* a rolling W-hour window
    (`{token: [ts,...]}`, count within last W h). Default to per-UTC-day for simplicity;
    note rolling as a tunable follow-up.
- **Enforcement point:** `_execute_bot_buy` (~L745), before `reserve_for_buy`. If
  `max_token_buys_per_day` set and the bot's buys of this token today `>= cap` → skip,
  counter, log. Co-locate with the existing `in_reentry_cooldown` check (L761).
- **Mode flag** (`REENTRY_CAP_MODE`: `"shadow"` | `"enforce"`, default `"shadow"`).

**Tests** (`tests/test_reentry_cap.py`):
- count increments per `open_position`; the (cap+1)th buy of the same token same day is blocked.
- resets next UTC day; different tokens counted independently; None = off.
- persistence round-trips through `to_dict`/`from_dict`.

---

## Rollout (both components)

1. **Ship in SHADOW** — stamp `would_*` counters + log lines, zero behavior change.
2. **Measure forward** — the nightly analyzer + risk-monitor (already live) report: how often
   each would fire, and the **winner-kill ratio** (equal-weight $ of winners the halt/cap
   would have blocked vs losers avoided). Target ≥ 2:1 before enforcing.
3. **Enforce after approval** — flip to `"enforce"` on the production-candidate config
   (`champion_premium_tightexit`) first, then fleet-wide once confirmed.
4. **Pre-live gate** — `tests/test_pre_live_invariants.py` + explicit approval before any
   `PAPER_MODE=false`.

## Files to touch
- `core/bot_config.py` — add `daily_loss_limit_usd` / `daily_loss_limit_pct`, `max_token_buys_per_day`.
- `core/per_bot_capital.py` — add `should_halt_daily()`.
- `core/per_bot_position_manager.py` — `_token_buys` counting + persistence.
- `feeds/dip_scanner.py:_execute_bot_buy` — enforce both gates (shadow/enforce) before
  `reserve_for_buy`; add `daily_halt_block` / `reentry_cap_block` to the cycle rejects log.
- `scripts/live_forward_test.py` — phantom-parity siblings if any verdict is stamped into entry_meta.
- `tests/test_daily_halt.py`, `tests/test_reentry_cap.py`.

## Non-goals (later phases)
- Co-entry **size-throttle** curve → Phase 2 (needs the mining workflow's validated curve).
- **Scale-in** on demand-persistence → Phase 3 (needs the persistence model from the backfill).
- Cross-bot/fleet per-token exposure cap → scanner-level option, after the per-bot cap proves out.
