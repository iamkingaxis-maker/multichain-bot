# Live Position-Risk Controls — Design Spec

**Date:** 2026-05-26
**Status:** Design only. PRE-LIVE feature — irrelevant in paper mode. Build is
gated behind `PAPER_TRADING=false` + explicit approval (same gate as the
profit-sweep spec). Pairs with the open "multi-bot daily-loss enforcement" item
from the 2026-05-23 security audit.

## Goal

Limit the **blast radius of a single bad token** once we trade real money. The
2026-05-26 afternoon exposed the failure mode: one token (TROLL) was re-bought
**108×** into a −16% slide and held ~6.5h, and across the fleet it landed in 30+
books at once. In *paper/research* mode that's harmless (see "Why not on the
fleet"), but with one live pool of capital it would be a real, concentrated draw.

## Why this does NOT belong on the 96-bot research fleet

The fleet is a **measurement instrument** — we read per-bot component EV, we do
NOT trade the fleet total. A cross-bot "≤ N bots may hold token X" cap would
**corrupt that measurement**: a bot blocked from a token because peers got there
first no longer reflects its own strategy, breaking the A/B. And the "correlated
drawdown across 30 bots" is 30 independent, well-powered measurements that the
token was a bad buy — useful data, not a portfolio hit. So per-token
concentration is a **live-portfolio control**, scoped here for the production
successor, NOT a fleet feature. (The measurement-clean fleet lever is the per-bot
re-entry cooldown — see the companion experiment.)

## Controls (live trader / production successor only)

### 1. Per-token exposure cap
- **One open position per token** at a time — no averaging into an existing
  position (re-buying a token you already hold).
- **Hard $ cap per token:** `position_usd <= MAX_TOKEN_EXPOSURE_PCT × working_capital`.
  With ~$2,000 working capital and a 10% cap, no single token can exceed ~$200 of
  exposure regardless of how attractive the signal looks.

### 2. Re-entry cooldown
- After fully closing a token, do not re-buy it for `REENTRY_COOLDOWN_SECS`.
  Directly counters the TROLL re-buy-the-knife loop. Reuses the existing
  `PerBotPositionManager.in_reentry_cooldown` + `reentry_cooldown_secs` mechanism
  (already shipped; `champ_reentry_throttle` uses it) — for live it becomes a
  trader-level default rather than a per-bot experiment flag.

### 3. (Tracked elsewhere) Multi-bot daily-loss cap
- The single-pool daily-loss limit is already an OPEN Tier-1 pre-live item in the
  2026-05-23 security audit. Listed here for completeness — these three controls
  form the live position-risk layer and should ship together.

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `POSITION_RISK_ENABLED` | `false` | Master switch. No-op in paper mode regardless. |
| `MAX_TOKEN_EXPOSURE_PCT` | `10.0` | Max % of working capital in any one token. |
| `ALLOW_TOKEN_AVERAGING` | `false` | If false, refuse a buy in a token already held. |
| `REENTRY_COOLDOWN_SECS` | `1800` | Min seconds after a full close before re-buying the token. |
| `DAILY_LOSS_LIMIT_USD` | — | (Companion control — see security audit.) |

## Where it lives

In the **live buy path** (`core/trader` / the production successor's position
open), gated `if not PAPER_TRADING and POSITION_RISK_ENABLED`. A single pure
helper `within_token_risk_limits(token, size_usd, open_positions, capital, now)
-> (ok: bool, reason: str)` does the math; the buy path calls it and refuses on
False. The helper is pure → unit-testable in isolation.

## Failure modes & invariants

- **Fail-CLOSED for risk:** if exposure can't be computed (missing balance/price),
  **block the buy** (opposite of the price-glitch guard, which fails open for a
  read — here a missing value must not let an oversized position through).
- **Never silently average:** a buy in a held token is refused (logged), not
  merged, unless `ALLOW_TOKEN_AVERAGING=true`.
- **Cooldown survives restart:** last-close timestamps persist with bot state
  (already true for the fleet path).

## Testing

- **Unit:** `within_token_risk_limits` — over-cap size blocked; held-token re-buy
  blocked (averaging off) / allowed (on); cooldown window respected; paper-mode
  no-op; fail-closed on missing inputs.
- **Integration:** in paper mode, confirm the controls are a strict no-op (zero
  behavior change) so flipping `POSITION_RISK_ENABLED` doesn't perturb the fleet.
- **Pre-live gate:** verify on the live champion with tiny size before full size.

## Explicitly out of scope (v1)

- Cross-bot/fleet coordination (wrong tool for the research instrument — see
  above). Correlation-cluster caps (no clean ex-ante memecoin "sector"). Dynamic
  per-token sizing by edge (that's Kelly territory — deferred until edges are
  Bayesian-confirmed).

## Why this shape

One pure money-math helper, fail-closed, paper-mode no-op, zero coupling to the
research fleet. It touches the live capital path, so — like profit-sweep — it's
deliberately minimal, loud, and off by default until explicitly enabled.
