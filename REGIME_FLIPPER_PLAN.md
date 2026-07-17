# THE REGIME FLIPPER — Operating Plan (2026-07-17)

AxiS's thesis, verbatim: *"flip flop to sol or rh and flip flop to the correct paper
bot each time. i dont think we will ever have one true bot."* The data agrees:
regimes flip day-to-day, bot P&L mean-reverts, and the family×regime mines show
green exists only as (right family × right regime). This plan is how the live seat
follows that truth safely.

## 1. Objective
One live seat (per funded chain). It sits on the highest-(edge × per-bot-volume)
bot for the CURRENT regime, and sits EMPTY when no regime pays. Per-bot volume is
the metric (AxiS: "i dont care about overall fleet volume").

## 2. Components (all built)
| piece | what | status |
|---|---|---|
| Sensor | `/api/regime` — trailing 4h tape-health per chain (median our-token drift, breadth) → HEALTHY/SICK/UNKNOWN | live |
| Decision | ROUTE_MAP in the same endpoint → TRADE(family list) / STAND_DOWN per chain | live |
| Recorder | 15-min snapshots → `bot_state/regime_history.jsonl` (makes every decision gradeable) | accruing |
| Envelope | fractional cap 0.35×wallet, loss-streak breaker 3×/1h, stop 1.5×position, balance+concurrency guards, canary, dust-sweep | armed at any live buy |
| Seat mechanics | `RH_LIVE_PROBE_BOTS=<bot>` (arm/swap, position-safe — sells route on buy-time flags), `__disabled__` (disarm) | proven |

## 3. Current decision rules (PROVISIONAL until Gate C)
- **RH HEALTHY** → route `rh_slcut_agedhold` (SL1 arm: replay n=64k +0.44–0.66pp/trade
  mean, loss-tail p05 −21.6→−15.4; 66 buys/day = passes the per-bot volume bar).
  Backups on edge (fail volume alone): strength_trail, deep_barbell_capped, f_reload_mid.
- **RH SICK/UNKNOWN** → STAND_DOWN (nothing green in sick windows; sick-tape volume is volume that loses).
- **SOL any state** → STAND_DOWN (3.4-day mine: NO family green even in healthy windows;
  flush/knife retired; admission_x arms + absorb-in-sick are the accruing candidates).

## 4. Flip mechanics + churn guards
- **Sensor hysteresis:** a state change must hold 2 consecutive reads before the route
  changes (the sol_bail flapping lesson: never buy the up-wobble and dump the down-wobble).
- **Cadence cap: max 1 seat change per 4h window** (the sensor's unit). Faster = noise-reacting.
- **Swap = position-safe by construction:** change `RH_LIVE_PROBE_BOTS` only; open
  positions still exit (sells key on buy-time `meta["live"]`). NEVER `RH_PAPER_MODE=true`
  with an open position.
- **Disarm-first bias:** on any doubt (stale sensor, instrument anomaly), the correct
  flip is to `__disabled__`. Standing down is always safe.

## 5. Validation gates (what must be true before money follows the router)
- **Gate A — routing works forward:** recorded decisions vs the windows that FOLLOWED
  them, n≥30 windows, TRADE-windows minus STAND_DOWN-windows spread positive.
  (~5 days of accrual; started 07-17.)
- **Gate B — the routed bot is real:** `rh_slcut_agedhold` forward record ≥5 days,
  ≥20 distinct tokens, drop-top-2 positive, tape-benchmarked, ≥30 closes. (~4 days out.)
- **Gate C — mapping stable:** family×regime map re-mined daily; the routed family stays
  healthy-green ≥5 consecutive days out-of-sample.

## 6. Phases
- **Phase 0 (now):** recommend-only. Router decides + records; nothing moves money.
  Daily: grade yesterday's decisions + re-mine the map (structure changes day to day).
- **Phase 1 (Gates A+B+C green):** MANUAL flips. The router's recommendation + the
  safe-live checklist land as a filled-in ARM command; **AxiS executes it.** Every flip
  logged; graded weekly.
- **Phase 2 (≥2 profitable Phase-1 weeks):** semi-auto — **STAND_DOWN flips execute
  automatically** (protective direction only: the system may always disarm itself);
  ARM/swap stays human.
- **Phase 3 (AxiS's explicit call, never default):** full auto within the envelope +
  cadence caps + daily loss stop.

## 7. Roles — the separation is deliberate and permanent
**The router chooses. The envelope contains. AxiS arms.** No phase removes the
envelope; no phase before 3 removes the human ARM; nothing ever auto-arms SOL while
its wallet is unfunded.

## 8. Honest limits + upgrade path
- 2-state regime (drift >−3%) is an MVP. Upgrade: hour-of-day × demand-composition
  states (the market rulebook) once the 2-state loop is validated end-to-end.
- Same-night windows share macro regime — Gate C exists precisely for this.
- Route on TAPE features only; bot-P&L routing is refuted (mean-reverts, inverts at 12h).
