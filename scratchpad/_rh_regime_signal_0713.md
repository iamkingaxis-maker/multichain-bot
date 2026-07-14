# RH regime-SIZING signal — bad-day detector (2026-07-13)

**AxiS goal:** make RH net-$/position higher AND **sustainable across regimes**. The
critical prior finding: EVERY RH racer lost on 07-11 (bad regime) and won on 07-12
(good regime) — the racers are **BETA to the RH market regime**, not a standalone
edge. Task: find a **real-time** signal that says "today is a bad day, trade
smaller/less" so bad days stop erasing the good ones, and design a **sizing** gate
around it. Metric = **NET-$/position** (median-% hid the regime loss). Honest low-n:
3 days ≈ 2–3 regime samples, so the gate ships **SHADOW** for forward validation.

Data: `scratchpad/robinhood_tapes/rh_paper_trades.jsonl` (local, not re-pulled).
Scripts: `_rh_regime_signal_0713.py` (characterization + sim), `_rh_regime_sweep.py`
(robustness sweep), `_rh_regime_shipcheck.py` (reproduces the projection through the
**shipped** `core/rh_regime.regime_size`). Standing SCRUB applied (drop ret>0 &
hold<10s → 9 trips). **447 closed trips, 07-10..07-12.**

---

## Ground truth — the regime is real and it's in NET-$, not median-%

| day | n | fleet NET-$ | **net-$/pos** | win-rate | median ret% |
|-----|--:|------------:|--------------:|---------:|------------:|
| 2026-07-10 | 70 | −$50.25 | **−$0.718** | 48.6% | −1.76% |
| **2026-07-11 (BAD)** | 162 | **−$214.98** | **−$1.327** | 38.3% | −5.14% |
| **2026-07-12 (GOOD)** | 215 | **+$221.86** | **+$1.032** | 63.3% | +6.0% |

Per-racer (07-11 → 07-12 net-$/pos; 07-10 rows predate `bot_id`): every racer that
traded the bad day was negative and nearly all flipped positive the next day —
`rh_wide_ladder` −2.21→+1.91, `rh_liq40` −2.09→+1.40, `rh_moonbag` −1.29→+1.27,
`rh_young_v1` −1.17→+1.32, `rh_demand_heavy` −0.49→+1.90, `rh_bites2` −1.14→+1.41.
(Only `rh_deep_only` stayed red both days.) **This is a market-wide regime beta**,
not idiosyncratic racer variance — so the lever is a market-wide "is today working?"
read applied to SIZE.

---

## Part 1 — which real-time signal cleanly ranks the bad day below the good?

I tested every decision-time-observable signal for whether it puts **07-11 below
07-10/07-12** *without hindsight*.

### REJECTED — external market-flow stamp (`buy_share_30m`, `netflow_30m`)
The obvious candidate **fails**, and the failure is instructive:

| day | trips w/ stamp | buy_share_30m (med) | netflow_30m (med) |
|-----|---:|---:|---:|
| 07-11 (bad) | **8** (late-day only) | **0.986** (high) | +$32.7k |
| 07-12 (good) | 215 | **0.887** (lower) | +$54.1k |

Two killers: (1) 07-11 is almost entirely **pre-stamp** — only 8 trips carry a
market stamp, all from 23:52 UTC, so there's no market read for most of the bad day;
(2) where it exists it is **inverted** — the GOOD day (07-12) ran at a *lower*
buy_share than the bad day's few samples, and netflow was positive on every stamped
trip (no crash in the tape). A `buy_share` sizing floor blocks **0** trips on 07-11
and only **downsizes the good day** (07-12 −$5.80 to −$17.77 depending on floor).
Market-flow demand does **not** carry the RH regime. (This is consistent with the
07-13 crash-gate note: the tape has no market cascade.)

### REJECTED — entry structure (`dip_pct`, `liq`)
Flat across days: dip median −19.6 / −17.8 / −18.5; liq median $44.6k / $41.5k /
$38.9k. No separation — entries look identical on good and bad days. The regime is in
the **outcomes**, not the entry setups.

### WEAK on its own — first-N-closed-trips of the day ("is today working?")
Directionally right for the good day but **noisy for the bad day**:

| | first-8 WR / net-$/pos | rest-of-day net-$/pos |
|---|---|---|
| 07-10 | 0.25 / −$4.24 | −$0.26 (day *recovered* — over-flagged) |
| **07-11** | **0.625 / +$0.51** | **−$1.42 (early looked FINE — under-flagged)** |
| 07-12 | 1.00 / +$1.93 | +$1.00 |

The first 8–10 trips of 07-11 looked OK (WR .63–.70, positive), so a first-N read
would have **missed** the bad day, and 07-10's ugly open recovered. Useful as a
same-day *confirmer*, not as the primary signal.

### ✅ WINNER — fleet-wide ROLLING REALIZED expectancy dial
The mean **net-$/position of the last 20 CLOSED positions across all racers** (this
is the module's *existing* `expectancy_dial`, reused as a decision-time read — **no
new tuned constant**). Per day, the **fraction of entries made while the dial was
negative** cleanly separated the regimes:

| day | rolling-dial median net/pos | **frac of entries with dial < 0** |
|-----|---:|---:|
| 07-12 (good) | +$0.88 | **0.28** |
| 07-10 | −$0.53 | 0.67 |
| **07-11 (bad)** | **−$0.88** | **0.87** |

Clean monotone rank: 07-11 > 07-10 > 07-12 in "defense fraction." It's **causal**
(the dial only sees positions that closed *before* the entry — no forward peek),
**self-referential** (available every day, incl. 07-10 which has no market stamp),
and it **refreshes fast**: median hold ≈ 92s (p90 ≈ 8 min), so on a persistent bad
day the dial goes negative within the first hour of closes and stays there — catching
**87% of the bad day's entries** while touching only **28% of the good day's**.

---

## Part 2 — the regime-SIZING gate

**Read (real-time, at entry):** `dial = mean net-$/pos of last 20 fleet closes`
(needs ≥10 closes to warm up). **`would_size = 0.3× if dial < 0 else 1.0×`.** Warm-up
(too few closes) → full size (you can't judge a regime before positions close).
Threshold `0` = breakeven (untuned); `0.3×` = AxiS's suggested defensive size;
window `20` = the pre-existing shipped dial window (**not** picked from this sweep).

### Projected net-$ saved (strictly-causal shadow sim, via shipped `regime_size`)

| day | base net-$ | would-size net-$ (0.3× on defense) | Δ | trips downsized |
|-----|-----------:|-----------------------------------:|---:|---:|
| 07-10 | −$50.25 | −$48.22 | +$2.03 | 47 |
| **07-11 (bad)** | −$214.98 | **−$89.16** | **+$125.82** | 141 |
| 07-12 (good) | +$221.86 | +$168.79 | **−$53.07** | 61 |
| **TOTAL** | **−$43.37** | **+$31.40** | **+$74.77** | |

The gate turns a losing 3-day stretch **profitable**: it **saves $125.82 on the bad
day** for a **$53.07 cost on the good day** — the asymmetry AxiS wants. The good-day
cost is the price of a *lagging* signal: the dial dips negative for ~28% of even a
good day (transient losing streaks), and those entries — taken right after a cluster
of losses — are genuinely lower-EV moments to press.

### Robustness (not a cherry-picked cut)
Over the full sweep, **every** (threshold ∈ {0,−.25,−.5,−.75,−1}, downsize ∈
{0.5,0.3,0}, window ∈ {8..25}) combination **improved the 3-day total** (Δ = +$45 to
+$133). Pausing (0×) on defense gives Δ **+$133.53** (bad day −$215→−$25, good day
−$62 cost). The 0.3× default is the conservative middle. Because improvement is
sign-stable across the whole grid, the result isn't a single fitted knob — but it is
still only **3 days**.

---

## What shipped (working tree only — NOT enforced/deployed/pushed)

- **`core/rh_regime.py`** — new pure functions:
  - `regime_size(dial, defense_mult=0.3, full_mult=1.0)` → `{score, would_size,
    state}`; negative rolling expectancy → `0.3×` (defense), else `1.0×`; warm-up
    fails to full size. Pure, forward-peek-free.
  - `regime_size_mode()` reads **`RH_REGIME_SIZE`** = `off` | `shadow` (**default**) |
    `enforce`. In shadow it only *stamps*; **no code path resizes on it** (enforce is
    not wired to the sizer either — promotion is a separate approved step).
  - `regime_stamp(...)` extended with `size_dial=` and now stamps **`regime_score`**
    (the dial value), **`would_size`**, `regime_size_state`, `regime_size_mode` on
    every entry.
  - Constants `RH_DEFENSE_SIZE_MULT=0.3`, `RH_FULL_SIZE_MULT=1.0`.
- **`scripts/rh_paper_lane.py`** — added a lane-level **`fleet_realized`** series
  (last 50 full-close realized $, close order, across ALL racers; persisted in
  `save_state`/`restore_state`), appended alongside the per-racer record on each full
  close, and fed to `regime_stamp(..., size_dial=expectancy_dial(self.fleet_realized))`.
  The per-racer dial (`st.recent_realized`) is too sparse to warm up within a bad day;
  the fleet series is the validated market-wide read.
- **`tests/test_rh_regime.py`** — `TestRegimeSize` (7 cases: defense downsizes,
  offense/zero full-size, warm-up fails-full, off-mode, downsize invariant, stamp
  shape, dial-fallback) + 3 lane cases (fleet-realized append + persistence, buy row
  carries the shadow sizing stamp while still booking full $25). **Full suite: 38
  passed.** Lane imports clean.

## Honesty ledger
- **3 days ≈ 2–3 regime samples → the gate CANNOT be validated.** Shipped SHADOW; it
  stamps `regime_score`/`would_size` and forward-grades. Directional only.
- **No overfitting:** threshold 0 (breakeven), 0.3× (AxiS's value), window 20 (the
  pre-existing dial window, chosen *before* this analysis). Sign-stable across the
  full robustness grid.
- **Lagging by construction:** the dial can't flag the first ~10 closes of a day
  (warm-up = full size). It works because bad regimes *persist* (07-11 stayed bad all
  day → 87% of entries caught). A day that flips mid-session will be caught late.
- **Reflexivity (for the eventual enforce step):** actually sizing down shrinks the
  P&L that feeds the dial. Shadow avoids this (the forward grade uses full-size
  realized P&L). If promoted, the lane must keep stamping the **full-size
  counterfactual** so the dial and the grade stay unbiased.
- **Promotion bar (pre-registered):** forward-grade in shadow until ≥2 more distinct
  bad-regime days land AND the shadow stamps show `would_size<1` entries were
  materially worse in net-$/pos than `would_size=1` entries at n≥~40 downsized entries
  across ≥2 bad days. Bring enforce to AxiS then — not before.
