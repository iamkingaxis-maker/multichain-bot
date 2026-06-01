# Phase-1 Risk Floor — Design Spec (2026-06-01)

**NORTH STAR: these are features OF THE PRODUCTION BOT — not fleet tuning.** The goal is
to find and harden ONE production config (leading candidate: `champion_premium_tightexit`
— deep-dip/pullback entry, tight exit, runner-tilt, ~85% WR / best equal-weight edge).
The 45-bot fleet is the **measurement apparatus** (A/B test bed) used to pick the
production bot's parameters — it is NOT a portfolio to optimize. Every gate below is a
candidate feature of the production config; the fleet only tells us where to set it.

**Goal:** two un-glamorous, *validated* risk floors for the production bot — a
**per-bot daily-loss halt** and a **per-token re-entry cap**. Neither requires the
mining-workflow params or the persistence model. Ship **measure-only shadow first**
(measured across the fleet test bed), then bake the validated parameters into the
production config; enforce after forward-proof + explicit approval (paper→live).

**Principle (from the 8hr postmortem):** you can't pick winners at entry, but you can
bound how much any single bad day or single token can cost the production bot.

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

## Rollout (both components) — production-bot-scoped

1. **Ship in SHADOW** — stamp `would_*` counters + log lines, zero behavior change.
2. **Measure across the fleet test bed** — the nightly analyzer + risk-monitor report: how
   often each would fire and the **winner-kill ratio** (equal-weight $ of winners the
   halt/cap would block vs losers avoided), to pick the right parameter. Target ≥ 2:1.
3. **Bake into the production config** — set the validated parameters on the
   production-candidate (`champion_premium_tightexit`) and enforce THERE. The fleet stays
   the lab; we are NOT flipping enforce fleet-wide as an end goal.
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

## Non-goals / correction (production-bot framing)
- **Cross-bot co-entry "crowding" throttle is NOT a production feature.** The −$2.54/trade
  crowding gradient is a FLEET artifact (44 other research bots converging on one token) —
  a *solo* production bot in live trading has no other bots crowding it. The only
  production-relevant slice of "concentration" is the bot's OWN re-entry, which is
  **Component B** above. The earlier "co-entry size-throttle curve (Phase 2)" is therefore
  dropped as a production lever — it was fleet-portfolio thinking. (The crowding finding
  stays useful only as a reason the fleet *aggregate* P&L is misleading — a measurement
  caveat, not a feature.)
- **Scale-in on demand-persistence → Phase 2 (was 3)** — a real production-bot feature
  (enter small, add as the trajectory confirms). Needs the persistence model from the backfill.
- The mining workflow's job is to validate the **production config** (entry archetype, exit
  ladder, stop, flat size) + these floors' parameters — not to tune the fleet.
