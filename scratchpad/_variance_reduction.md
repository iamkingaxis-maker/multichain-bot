# Variance-reduction mine — kill the "violent swings", keep volume + edge

**Goal (AxiS):** "stabilize RH and our sol live bots to be profitable and not have
violent swings while keeping high trade volume." Cut per-trip and per-day P&L
**variance per unit of edge** WITHOUT (a) cutting volume or (b) touching entry sizing.

**Data.** RH: 457 closed trips reconstructed from `rh_paper_trades.jsonl` partial-sell
log (group sells by (bot_id,pool), split at `fully`, sum pnl_usd over $25 entry;
`scratchpad/variance_reduction/rh_trips.py`). Solana: 955 realized young-lane trips
(`scratchpad/sol_selection/_trips.json`, scrub already applied; carries `mae` =>
proper stop simulation). Levers scored on realized data, both chains.

## Baselines
| chain | trips | per-trip pnl mean | per-trip **stdev** | worst | per-day pnl stdev |
|---|---|---|---|---|---|
| RH (pnl_pct on $25) | 457 | −0.12% | **13.96** | −100.1% | 772 (3 days, noisy) |
| Solana (ret %) | 955 | +1.48% | **18.29** | −98.9% | 231 (11 days) |

Both left tails are real catastrophe rugs (RH LP_DRAIN −100, Solana gap −98.9).

## Lever ranking — variance-cut / volume-retained / edge-change

| # | Lever | Chain | per-trip stdev cut | volume kept | edge Δ (mean) | notes |
|---|---|---|---|---|---|---|
| **1** | **Catastrophe cap** (early de-risk / floor) | RH | **−20.0%** (13.96→11.17) | **100%** | **−0.12→+0.63** ↑ | floor −20; 22 trips reshaped; worst −100→−20 |
| | | Solana | −7.4% (18.29→16.93) | **100%** | +1.48→+1.93 ↑ | MAE-gated floor −20; 14 clipped; gap-rugs escape a *price* stop (worst stays −98) → live form = **exposure** de-risk |
| **2** | **Hold-time box** (600s) | RH | −5.5% (→13.19) | 92% | +0.09 ↑ | over-box cohort n=38: stdev 20.6, mean −1.10 |
| | | Solana | −5.6% (→17.26) | 92% | +0.43 ↑ | over-box cohort n=77: stdev 26.9, mean −3.38 |
| | Hold-time box (300s) | RH / Sol | −9.6% / −5.2% | 81% / 79% | +0.06 / +0.41 ↑ | tighter box, more volume cost |
| **3** | **Per-token daily cap** (de-cluster) | Solana | day-stdev **−42%** (K5) … **−73%** (K1) | 55% … 18% | −0.8 … −3.7 ↓ | biggest **DAY**-variance lever; measured edge/vol cost because today's clustering was into WINNERS |
| | | RH | day-stdev −87…−92% | 23%…10% | ↓ | extreme same-pool re-biting; heavy measured volume cost |
| **4** | **Earlier principal de-risk** (bigger TP1 slice) | RH (A/B) | −2.4% (8.15→7.96) | 100% | +0.51→−0.97 ↓ | aged_derisk 0.75@+6 vs aged_hold 0.50; the tail-cut came from the 20-min **exposure cap** (=Lever 1-as-cap), NOT the bigger slice; n=15/31 |
| | | Solana | — | — | — | not simulable (peak/MFE not populated in trips) |

### REFUTED as variance tools
- **Rug-signal stamp as an entry gate (RH):** rug-stamped pools had **higher** mean
  (+3.13, stdev 11.85) than clean pools (−4.21, stdev 15.29). The stamp does not
  separate the losers — gating on it would drop volume AND edge. Catastrophe defense
  must be an **exit/exposure** mechanic, not a rug-stamp entry veto.
- **Bigger TP1 slice alone (Lever 1):** banking 0.75 vs 0.50 at TP1 barely moved stdev
  (−2.4%) and cost edge; the variance win in that racer is its exposure cap.

## Top recommendation — #1 lever that keeps volume

**Catastrophe cap via EARLY exposure de-risk.** It is the only lever that cuts
variance while retaining **100% of trades** AND **improving edge on both chains**
(it reshapes the EXIT, never drops an entry, never touches sizing). Because live
microcap stops **gap through** (a −98% LP pull fills far past a −12/−15 price stop —
seen on both tapes), the live-executable form is **de-risking EXPOSURE** (force the
position down to ~25% early) rather than only tightening the price stop. That caps
the gap-rug tail a price stop can't catch.

- **RH:** per-trip stdev **−20.0%** (13.96→11.17), mean **−0.12% → +0.63%**, worst
  **−100% → −20%**, **100% volume**.
- **Solana:** per-trip stdev **−7.4%** (18.29→16.93), mean **+1.48% → +1.93%**,
  **100% volume**.

**#2 (complementary): hold-time box** — the >10-min-hold scalp cohort is *both*
higher-variance and negative-edge on both chains; a 10-min box cuts stdev ~5-6% and
lifts edge at ~8% volume cost. Stack it with the cap.

**Day-swings specifically:** the per-token daily cap (Lever 3) is the heavyweight
(Solana day-stdev −42% at K=5, keeping 55% of the raw same-token re-bites). Its
measured volume/edge cost is an artifact of the backtest not refilling freed slots —
in live the sibling redirects to a DIFFERENT token (fleet volume retained). The
cross-sibling form is already live on the aged cohort (`exclusion_group="aged"`) and
in `core/fleet_token_cap.py` (shadow-first, cap 3). Recommend widening that shadow to
the young lane, not a hard per-token cap that clips winner pile-ons.

## What is wired (working tree, no commits, no Solana-live enforce)

**RH paper racers** (`scripts/rh_paper_lane.py`, config-only — machinery already
existed: `derisk_slice`/DERISK_CAP, `time_stop_minutes`, `exclusion_group`). Entry
size UNTOUCHED (default $25). Both `exclusion_group="lowvar"` (Lever 3 lite: never
pile the same token, so one rug can't hit both):
- `rh_lowvar_catstop` — Lever 1(cap): 5-min derisk→25% + tighter −12 hard stop.
- `rh_lowvar_box` — Lever 2: 10-min hold-time box.
- Pre-registered: grade at n≥30 closes vs `rh_young_v1` control; WIN = lower stdev +
  lower worst-trip with mean not worse.

**Solana SHADOW stamp** (`core/per_bot_position_manager.py`, block 0d) — STAMP-ONLY,
never appends/replaces a decision (live SOL bots byte-identical). `VARIANCE_SHADOW`
(default on, `=off` kills):
- `varshadow_cat_*` — pnl breached the catastrophe floor (`VARSHADOW_CAT_FLOOR`, −20).
- `varshadow_box_*` — held past `VARSHADOW_BOX_MIN` (10 min).
- Gives forward realized data to grade both levers against the enforced ladder before
  any enforce decision.

**Tests:** `tests/test_variance_shadow.py` (4 — stamps fire, enforcement unchanged,
never an exit kind, `=off` disables). Suites green: 212 passed across
test_variance_shadow / test_per_bot_position_manager / test_rh_aged_racers /
test_rh_factory_racers / test_moonbag_exit / test_exit_arm.

Analysis scripts: `scratchpad/variance_reduction/{rh_trips,rh_levers,sol_levers}.py`.
