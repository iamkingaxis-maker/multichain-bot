# Profit-Sweep / Hot–Cold Wallet Separation — Design Spec

**Date:** 2026-05-25
**Status:** Design only. PRE-LIVE feature — irrelevant in paper mode. Build is
gated behind `PAPER_TRADING=false` + explicit approval (see pre-live checklist).

## Goal

Limit the blast radius of a hot-wallet key compromise. The production (hot)
wallet should hold only **working capital**; accumulated **profit** is swept to
a separate **cold wallet** that the bot can *send to* but has **no private key
to withdraw from**. If the hot key ever leaks, the attacker gets at most the
working float — never the banked profits.

## Threat model

- **In scope:** production wallet private key (`SOLANA_PRIVATE_KEY`) leaks via
  container compromise, log exposure, env-var exfiltration, or dependency
  supply-chain. Today that exposes the *entire* balance (float + all profit).
- **Out of scope:** compromise of the cold wallet (assumed offline / hardware),
  RPC-level censorship, MEV on the sweep tx (negligible — it's a plain transfer).

## Architecture

```
  ┌────────────────┐   trades (sign)    ┌─────────────────┐
  │  HOT wallet     │ ◄────────────────► │  Jupiter / DEX  │
  │  (bot signs)    │                    └─────────────────┘
  │  holds: float   │
  │  $WORKING_FLOOR │ ──── one-way sweep ────►  ┌────────────────┐
  └────────────────┘   (excess idle SOL)        │  COLD wallet    │
        ▲                                        │  address only;  │
        │ bot has private key                    │  bot has NO key │
        │                                        └────────────────┘
   only the float is ever at risk
```

- **Hot wallet** — existing signer. Holds the working float (e.g. $2,000 of SOL)
  plus whatever SOL profit hasn't been swept yet.
- **Cold wallet** — a separate Solana keypair. The server stores **only its
  public address** (`PROFIT_WALLET_ADDRESS`). Ideally a hardware/offline wallet.
- **Sweep** — a plain SOL transfer hot → cold of *idle SOL above the floor*.
  One-way by construction: the bot cannot move funds cold → hot.

## Configuration (env vars)

| Var | Default | Meaning |
|---|---|---|
| `PROFIT_SWEEP_ENABLED` | `false` | Master switch. No-op in paper mode regardless. |
| `PROFIT_WALLET_ADDRESS` | `""` | Cold destination. Validated; sweep refuses if empty/invalid. |
| `WORKING_CAPITAL_FLOOR_SOL` | — | SOL kept in hot at all times (working float, in SOL). |
| `SWEEP_THRESHOLD_SOL` | `1.0` | Only sweep when idle-above-floor exceeds this (avoids fee churn). |
| `SWEEP_GAS_BUFFER_SOL` | `0.05` | Always leave this for rent-exemption + tx fees. |
| `SWEEP_MIN_INTERVAL_SECS` | `3600` | Min time between sweeps. |
| `PROFIT_SWEEP_DRY_RUN` | `true` | Log intended sweep without sending (default on for first live runs). |

## Components

1. **`core/profit_sweeper.py`** — single-responsibility module:
   - `compute_sweepable_sol(hot_balance_sol, floor_sol, gas_buffer_sol) -> float`
     — pure function: `max(0, balance - floor - gas_buffer)`; returns 0 if below threshold.
   - `validate_destination(addr) -> bool` — valid base58 Solana pubkey, ≠ hot
     wallet, matches the configured `PROFIT_WALLET_ADDRESS` (defense against a
     mutated config redirecting funds). **Fail-closed:** any doubt → no sweep.
   - `sweep(...)` — builds, signs (hot key), sends a System Program transfer;
     returns the tx signature. Honors dry-run.
2. **Wiring** — a periodic task (reuse the existing main loop / a timer), gated:
   `if not PAPER_TRADING and PROFIT_SWEEP_ENABLED and enough_time_elapsed: sweep`.
   Runs *between* trade cycles; never blocks or competes with trading.
3. **Audit log** — every sweep (and every dry-run intent) logged with amount,
   destination, tx sig, and resulting balances. This is the only outbound
   non-swap transfer the bot makes — it must be loud and auditable.

## Data flow (one cycle)

1. Skip unless `not PAPER_TRADING and PROFIT_SWEEP_ENABLED` and
   `now - last_sweep >= SWEEP_MIN_INTERVAL_SECS`.
2. Fetch hot wallet SOL balance via RPC. On failure → skip this cycle (never block trading).
3. `sweepable = compute_sweepable_sol(balance, floor, gas_buffer)`.
4. If `sweepable < SWEEP_THRESHOLD_SOL` → skip.
5. `validate_destination(PROFIT_WALLET_ADDRESS)` → if invalid, **refuse + alert** (fail-closed).
6. If `PROFIT_SWEEP_DRY_RUN` → log intended sweep, set `last_sweep`, return.
7. Build + sign + send transfer of `sweepable` to cold. Confirm tx. Log. Set `last_sweep`.

## Failure modes & safety invariants

- **Never sweep below the floor** — `compute_sweepable_sol` clamps to ≥ 0 and
  subtracts both floor and gas buffer. Unit-tested.
- **Never sweep deployed capital** — only *idle SOL* is swept. While positions
  are open, that SOL isn't in the wallet (it's in tokens), so the balance-based
  calc naturally excludes it. The floor protects the rest.
- **Fail-closed on bad destination** — empty/invalid/mismatched address ⇒ no
  transfer. A compromised config can't redirect funds to an attacker because the
  destination is validated (and ideally pinned/allowlisted).
- **RPC failure ⇒ skip, retry next cycle** — sweeping is best-effort, never
  blocks the trading loop.
- **Idempotency / no double-sweep** — `last_sweep` timestamp + min-interval;
  confirm tx before updating state; a failed send doesn't advance `last_sweep`
  past the un-swept funds.
- **Dry-run first** — `PROFIT_SWEEP_DRY_RUN=true` for the first live days; verify
  the logged intents match expectations before sending real transfers.

## Testing

- **Unit:** `compute_sweepable_sol` (below floor → 0; above → balance−floor−gas;
  below threshold → 0). `validate_destination` (good/empty/malformed/==hot/!=configured).
  Min-interval gating. Paper-mode no-op.
- **Integration:** dry-run logs intended sweep without sending. Devnet transfer
  end-to-end (real signature, real balance change) before any mainnet use.
- **Pre-live gate:** one small real mainnet sweep, manually verified on-chain,
  before enabling at full size.

## Explicitly out of scope (v1)

- Multi-sig hot wallet, token-denominated sweeps (we sweep SOL only), automated
  cold→hot top-ups (defeats the one-way safety), scheduled tax accounting.

## Why this is the right shape

Single tiny module, one pure function doing the money math (trivially testable),
fail-closed destination validation, dry-run default, and zero coupling to the
trading path (best-effort timer). It touches the most sensitive code in the
system (signing + outbound transfer), so it's deliberately minimal and loud.
