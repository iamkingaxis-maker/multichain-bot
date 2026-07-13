# SOL Young-Lane EXIT-Ladder Overhaul (2026-07-12)

**AxiS: the memecoin market is the hottest all year and we're underperforming on the
EXIT side.** Two decodes set the brief: (1) `_sol_winner_behavior.md` — the #1 leak is we
PANIC-CUT winners (48% of trips exit <2min RED; our best bucket 120-300s is the one we cut
short). (2) `_sol_hot_market.md` — the heat is in the right TAIL (reach>=30 21% recent vs
9% prior; reach>=50 7.7% vs 0), so a fixed +12 TP2 caps exactly the trips that now run.

This overhauls the exit ladder as **PAPER A/B bots** (control + 4 variants), ranked by
replay on the 955-trip tape, graded forward. EXIT-only (no sizing), no live enforce, no
commits. All Python writes utf-8. Tests pass (exit 0).

---

## 1. The replay table (955 trips, `scratchpad/sol_selection/_trips.json`, 07-02..07-12)

**Metrics.** `mean` = captured-pp (expectancy per trip). `ex2` = ex-top-2 token-median (the
HONEST robust metric — group by token, median per token, drop the 2 highest-count tokens,
median of the rest; two fat-tail promotions were reverted for gaming the sum). `cat%` =
share <=-25%. `oos` = green in all 4 chrono halves (W1/W2 x odd/even).

**Honest limit (memory: trail conclusions not trusted from replay).** Observed MFE (`peak`)
is TRUNCATED by the live exit -> any runner/moonbag leg is a **LOWER bound**. TP-harvest legs
at targets <= the live +6 TP1 are reliable. So replay **RANKS**; forward paper **CONFIRMS**.

### 1a. TP-side ladder variants (MFE-based replay)

| variant | mean (captured-pp) | med | **ex2** | min-half | wr% | cat% |
|---|---|---|---|---|---|---|
| **control** (6/.75, 12/.25, 2pp) | -0.55 | +0.42 | **-5.69** | -6.57 | 50 | 1.5 |
| barbell (6/.60, 12, mb .30/12pp) | -0.14 | -1.20 | **-5.69** | -6.57 | 49 | 1.5 |
| hot_tuned (6/.5, 20, 4pp) | +0.19 | -2.13 | **-5.69** | -6.57 | 49 | 1.5 |
| hot_barbell (6/.5, 18, mb .35/15pp) | +0.29 | -2.13 | **-5.69** | -6.57 | 49 | 1.5 |

**THE decisive finding: the TP-side variants lift the MEAN (+0.4 to +0.8pp, winner-preserving)
but leave ex-top-2 median UNCHANGED at -5.69.** Because ex2 is set by the LOSER cohort (66% of
trips never reach +6 TP1), and barbell/hot-tuned only change what happens to WINNERS. They are
mean/tail plays, not median plays — and the runner leg is a lower bound, so their true mean gain
is larger than shown. This is exactly the deep-exit doc's SOL verdict: the runner is unprovable
from truncated summary tape -> ship as forward-grading paper, don't over-claim.

### 1b. Bounce distribution — bounces DO run past +12 (justifies the runner lift)

Of 325 trips reaching +6 TP1: **MFE median +16.7, p75 +27.7, p90 +41, max +244.** 24% reach
+12, 16% reach +18, 6.4% reach +30. A full-out TP2 at +12 caps the median winner (+16.7) — the
barbell moonbag and the heat-gated TP2 lift (+18) recover it, and the heat gate fires the lift
**only when the tail is actually live** (never blanket, per `_sol_hot_market.md` 4-half OOS).

### 1c. Min-hold floor — the ONLY lever that moves the robust median

Panic-cut cohort (hold<120s, red, never reached +6, not a real -25 rug): **279 trips (29% of
volume), 98 tokens, med -8.2%.** The 120-300s held bucket: **56% WR, +4.5 med.**

**Same-token union (robust, holds token fixed):** 62 tokens had BOTH a <120s red cut AND a
>=120s hold — **holding beat cutting on 45/62 = 73% of tokens, +10.2pp median improvement.**

| min-hold framing | mean | med | **ex2** | min-half | wr% | cat% | oos |
|---|---|---|---|---|---|---|---|
| live-realized (all trips, reference) | +1.48 | -3.38 | **-5.78** | -6.57 | 46 | 2.8 | - |
| min-hold, conservative (cut + union improv) | +4.42 | +3.88 | **+2.85** | +1.36 | 67 | 2.3 | GREEN 4/4 |
| min-hold, upper (reprice -> +held_med) | +5.72 | +4.54 | **+4.54** | +4.54 | 75 | 1.9 | GREEN 4/4 |

Min-hold moves **ex-top-2 median -5.8 -> +2.9..+4.5, GREEN 4/4.** (Bounds: the upper is a
survivorship UPPER bound; the conservative adds only the same-token union improvement, capped.)

### 1d. COMBINED (min-hold + tail capture) — the recommended ladder

| combined | mean | med | **ex2** | min-half | wr% | cat% | oos |
|---|---|---|---|---|---|---|---|
| conservative | +2.76 | +4.54 | **+3.49** | +1.43 | 70 | 1.0 | GREEN 4/4 |
| upper | +4.05 | +4.54 | +4.54 | +4.54 | 79 | 0.6 | GREEN 4/4 |

Min-hold moves the median (loser cohort), the runner captures the tail (winner cohort), and
the panic-cut re-price + banked runner drop catastrophe to **1.0%.** Both axes move.

---

## 2. Recommended exit ladder vs current

| | current (control) | **recommended (min-hold + heat-runner)** |
|---|---|---|
| TP1 | +6 / 0.75 | +6 / 0.75 (UNCHANGED — the scalp is the reliable money) |
| TP2 | +12 / 0.25 | +12, lifted to **+18 when heat-regime HIGH at entry** |
| trail | 2pp | 2pp |
| pre-TP1 first 120s | soft cutters + -12 stop all live | **all suppressed; ONLY -25 rug tripwire** |
| stop | -12 | -12 (resumes the instant the 120s floor expires) |
| replay ex-top-2 | -5.7 | **+3.5 conservative (GREEN 4/4)** |
| replay captured-pp | -0.55 | **+2.76** |
| catastrophe rate | 1.5% | **1.0%** |

**Guardrail respected:** it is a FLOOR, not a longer target — the upper time-box (never_runner
/ slow_bleed 45min) resumes at 120s (600s+ is the WORST bucket, 11.7% catastrophic). The rug
tripwire (-25) stays live throughout the floor so it never rides a real liquidity pull down.

---

## 3. Paper A/B bots wired (working tree, NO commits, all PAPER, own singleton pools)

Entry config **byte-identical** to the young-lane funnel (`badday_young_rt_paper`) across the
whole family -> the ONLY delta is the exit ladder (verified via dataclass field-diff). Each has
its own singleton `exclusion_pool` (the established young-lane exit-A/B convention: moonbag_ab /
rt_paper) so every bot sees the FULL token stream -> paired-by-token grading AND independent
ex-top-2. `live_probe=false` on all (NEVER routes live).

| bot_id | exit delta vs control |
|---|---|
| `badday_young_exit_control` | current ladder (the A/B baseline) |
| `badday_young_exit_minhold` | + `min_hold_floor_secs=120`, `min_hold_floor_rug_pct=-25` |
| `badday_young_exit_barbell` | `tp1_sell_fraction=0.60` + moonbag `0.30` (floor 0, 12pp trail) |
| `badday_young_exit_heatrunner` | `regime_runner_lift=True`, `tp2_pct_hot=18` |
| `badday_young_exit_minhold_heat` | min-hold floor + heat-runner (the recommended combo) |

### Code (working tree, no commits)
- `core/bot_config.py`: `min_hold_floor_secs`, `min_hold_floor_rug_pct`, `regime_runner_lift`,
  `tp2_pct_hot` (all default OFF -> every existing bot byte-identical).
- `core/bot_evaluator.py`: `min_hold_floor_active`, `min_hold_rug_tripwire_fires` (pure, fail-safe).
- `core/heat_regime.py` (NEW): trailing reach20 rolling window (K=25, HIGH>=0.20), fleet-fed,
  fail-safe COLD on thin history. `HEAT_REGIME_MODE=off` kill.
- `core/per_bot_position_manager.py::tick`: min-hold floor block (suppresses soft cutters +
  -12 stop, keeps -25 rug tripwire; `MIN_HOLD_FLOOR_MODE=off|shadow|enforce`), heat-lift stamp
  at entry + TP2 target lift.
- `feeds/dip_scanner.py`: feeds every FULL close's peak into `heat_regime.record_close` (fail-soft).

### Tests
- `tests/test_min_hold_floor.py` (24): pure helpers, floor suppression, rug tripwire, resume-
  after-floor, TP1-still-fires, shadow-no-act, off-byte-identical, heat lift cold/hot/off,
  heat-regime window/threshold/mode. **24 passed.**
- Regression: `test_moonbag_exit`, `test_per_bot_position_manager`, `test_in_flight_floor`,
  `test_run_winners_exit`, `test_peel_exit`, `test_pre_live_invariants` — **118 passed.**
  `test_bot_config`, `test_bot_registry` — **18 passed.** Full fleet loads (156 bots), all
  paper family entry-identical / exit-isolated.

### Replay harness
- `scratchpad/sol_exit_overhaul/exit_replay.py` (+ `_replay_out.txt`). Reproduces the winner-
  behavior bucket table exactly (validates the pipeline), then the ladder/bounce/min-hold tables.

---

## 4. Pre-registered forward grade plan

- **Population:** young-lane paper family, exit-isolated, same entry funnel.
- **n>=30 closes per bot**, ex-top-2 token-median (NOT sum), vs `badday_young_exit_control`.
- **Winner = higher ex-top-2 AND higher captured-pp, OOS green in >=3/4 halves**, cat <=1/20.
- **Priors respected:** barbell/heat runner legs are replay LOWER bounds -> the runner edge is
  carried by forward paper, never by summary tape. Min-hold is graded on REALIZED trips (the
  same-token union + bounded replay only RANK it; the paper bot proves it).
- **Expected ranking (replay):** min-hold moves the robust median; barbell/heat-runner move the
  mean/tail; the **combo (`badday_young_exit_minhold_heat`) is the recommended ladder** — promote
  to live ONLY on forward green + AxiS go (memory: only AxiS retires/promotes candidates).
