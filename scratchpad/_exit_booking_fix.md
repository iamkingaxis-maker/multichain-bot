# Paper exit-booking fidelity fix (公牛 twin) — 2026-07-06

Commit: `19bcb0f` on master (NOT pushed). Full suite: **2047 passed** (exit 0).

## Root cause — established exactly from the 公牛 sell record
Paper twin record (badday_young_absorb, 02:51:23Z): `exit_mid_price = 0.000111075`
(= the fired fresh price, **-9.34%** vs entry 0.000122512), reason
`in-flight velocity-bail pnl=-9.34% ... [reprice]`, but `exit_price = 0.00011920`
= **exactly** `entry * (1 + mae_pct/100)` with `mae_pct = -2.7037` stamped at
`mae_at_secs = 52` — 58s BEFORE the flush. Three compounding mechanisms in the
PAPER_FIDELITY=enforce sell branch of `_execute_bot_sell`:

1. **Stale-MAE clamp (the 公牛 6.6pp).** `paper_exit_decision`'s CLAMP-TO-LOW used
   `state_blob.mae_pct`, which only the SLOW sweep updates. The fast `[reprice]`
   bail fired below the stale MAE, and the clamp raised the booking back UP to it.
2. **Friction erasure (every slow-path bail).** The clamp ran AFTER slip+fee. On
   slow-path bails the MAE is stamped in the SAME tick == the decision price, so
   the clamp restored the booking to exactly the decision price — zero slip/fee.
   Ledger proof: HANDSEM -5.12/-5.12, trumplet -6.46/-6.46, ACM -5.35/-5.35,
   0x -4.73/-4.73 (booked pnl == decision pnl to 4dp).
3. **Refetch override.** `_execute_bot_sell` re-fetched a "fresh" price and booked
   that instead of the price the firing rule evaluated — a post-decision bounce /
   lagging source (ALYCIACOW: decision -9.69% → booked -3.47%).

## The fix (paper-side only; live sell path untouched)
- **Booking basis = the decision price** (`current_price`): fresh on the fast paths
  (they already pass `fresh_price` in — verified at 7070/7197/7311), the slow-sweep
  decision price on the slow path. The refetch is gone from booking.
- **`paper_exit_decision` reorder** (core/paper_fidelity.py): gap-through haircut +
  clamp-to-low act on the PRICE BASIS; the effective low is bounded by the decision
  print (`min(low_price, repriced)` — stale-MAE guard); slip+fee friction is applied
  BELOW the clamp so it is always paid. (For the no-clamp case the math is identical
  — multiplication commutes — so all existing pins still hold.)
- **Exit-side fill calibration** (mirror of the buy block at dip_scanner ~3252):
  new `core.fill_calibration.load_exit_calibration()` (side='sell', own mtime cache)
  → `calibrated_slip_pct` per liquidity bucket (fresh liq via `_fresh_exit_liquidity`)
  → `realistic_slip_with_cap(cap = PROBE_ULTRA_SELL_SLIPPAGE_BPS)`. Thin sample →
  conservative **1.0%/leg** default (`PAPER_EXIT_SLIP_DEFAULT_PCT`). Gated by the same
  `FILL_CALIBRATION_ENABLED` as the buy side; `off` → flat `PAPER_LIVE_SLIP_PCT`.
  Ultra platform fee (+0.5pp <24h) kept, per-leg, same as buys.
- **No double-charge**: enforce books the fidelity value DIRECTLY (bypasses
  `sell_fill_price`), exactly as before — now pinned by a test.
- Live sell-leg sample is already fat: **149 successful sells** in live_swaps
  (mid-liq p50 0.578%, thin 0.398%, unknown 1.206%, overall 0.703%) — the
  calibration engages immediately, no burn-in needed.

## Tests
- New `tests/test_exit_booking_fidelity.py` — 10 tests driving the REAL
  `_execute_bot_sell` wire + pure-function pins:
  (a) slow-path bail: booked pnl == decision pnl − modeled friction (pre-fix booked
  decision exactly); 公牛-shape stale-MAE regression; fast-path books the fired
  fresh price and IGNORES a wildly different refetchable price;
  (b) no-double-haircut (single friction application, explicitly ≠ the
  double-charged candidate);
  (c) calibration used when available / 1.0% conservative default when thin /
  sell-legs-only loader (buy cache untouched).
- `tests/test_paper_fidelity_wire_integration.py::_set_sell_env` now pins
  `FILL_CALIBRATION_ENABLED=off` + `ULTRA_FEE_MODEL=off` (same convention as the
  buy-side `_set_buy_env`).
- Full suite: **2047 passed**, 0 failed (exit code 0 verified separately).

## 公牛 recompute under the fix
Decision (paper fired fresh) −9.34%; drag = calibrated slip + 0.5 ultra + 0.17 fee:
| slip source | booked pnl |
|---|---|
| mid bucket p50 0.578% (fresh liq ~32k available) | **−10.47%** |
| unknown bucket 1.206% (no fresh WS liq) | −11.04% |
| thin default 1.0% | −10.85% |
| pre-fix actual booking | −2.70% |
| **live realized** | **−7.27%** (decision fresh_pnl −8.28) |

**Yes, the fix overshoots live on this trade by ~3.2–3.8pp pessimistic** (vs 4.6pp
FLATTERING before). Decomposition: ~1.1pp is decision-sample divergence (paper's
fast tape read −9.34 where live's read −8.28 — different fetch instants, not
booking); ~2.1pp is live's LUCKY favorable fill (its sell leg printed −1.11%
i.e. ABOVE mid, vs the calibrated +0.58% expected cost). In expectation (p50
fill) the residual is the tape divergence only, ~±1pp noise around zero.

## Residual gap sources NOT fixed (with size estimates)
1. **Decision-tape divergence** (~±1pp): paper's fast-sample deque vs live's
   exit-reprice fresh fetch sample different instants/sources. Symmetric noise.
2. **Post-decision drift/latency** (±1–2pp on bails, ~0 mean): live fills ~1.8s
   after decision (median execute 842ms + latency); paper books at decision
   instant. Tonight it helped live (+1.0pp bounce); on continuing flushes it
   hurts. Not modeled; would need a latency-drift term calibrated from
   (realized − decision) on live legs — sample exists (149 sells) if wanted.
3. **Possible ultra-fee double-count inside calibrated slip** (≤0.5pp on <24h
   tokens): if `fill_vs_mid_slippage_pct` on live sells already embeds the Ultra
   platform fee, adding `ultra_platform_fee_pct` on top over-charges. The BUY
   side has the identical convention (calibrated + upf) — kept symmetric rather
   than diverging; worth a one-off measurement on the live legs.
4. **EXIT_SLIP_LIQ shadow block** still refetches its own `_esl_fresh` for its
   would-book computation (shadow-only in prod, books nothing). If ever enforced
   it should also move to decision-basis.
5. **Gap-through modeling on true hard stops** unchanged (5% haircut bounded by
   the traded low); bails/velocity exits never had it (reason strings don't match
   the gap tokens) — unchanged by design.
