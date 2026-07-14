# Peel-Geometry Grid Replay — decode geometry vs shipped — 2026-07-06

**Question:** today's behavior decode (`_behavior_decode_2026_07_06.md`) found winning wallets peel with a first slice ~32% of the exit (we ship 75% at TP1) and trails running 45–290min (median 141min; our 5pp trail closes in minutes). Does the DECODE's geometry beat the SHIPPED geometry (0.75 slice / 5pp giveback, conditional <+12) **on OUR entries**?

**Answer: half of it does.** The **slice** transfers: cutting the TP1 slice from 0.75 → 0.30 nearly triples the peel's edge (+40.2pp vs +14.5pp over the 4.5-day corpus, both halves positive, survives ex-best-token and ex-top-position checks). The **long trail does NOT transfer**: 8pp giveback is negative in every slice (−8 to −23pp), and 12pp is positive only via one outlier token (SHROOM +92.6; ex-best-token every 12pp cell is −22 to −70pp — fails the fat-tail test). Max-holds (3h/5h) never bind: with full ~5.8h bar coverage past TP1, **every** runner trail-closes within 42min (gb5/gb8) or 214min (gb12) — our post-TP1 price paths simply don't sustain the 141-minute rides the decode's winners get on *their* tokens. Keep 5pp; queue the 0.30 slice.

## Method — identical pipeline to `_tp_peel_replay.md`, plus honest friction

- Same corpus: `_tp_positions.json`, 238 positions since 07-01 (173 flush / 60 young_absorb / 5 adolescent_absorb), scrub rule already applied at construction (pnl>0 & hold<10s legs dropped). Same forward paths: `_tp_bars.jsonl` GT minute OHLC [entry, entry+6h], 235/238 covered.
- Same HYBRID mode (the decision-grade one): every actual exit up to and including TP1 kept at its ACTUAL fill; only the post-TP1 remainder is simulated. Same runner conservatism (bars strictly after TP1 ts; trail fills at min(trigger, bar open); peak updates end-of-bar; peak seeded at max(recorded peak at TP1, fill)).
- Same conditional structure: only TP1 fills < +12 convert to peel; the 14 wick fills (≥ +12) keep the ladder = realized in ALL variants. Hard floor −12 on the runner everywhere.
- **Pipeline identity check passed exactly:** re-running the original 0.50/5pp cell at zero friction through this script reproduces **+72.0pp** to the decimal (n=52), so every difference below is geometry + friction, not pipeline drift.
- **NEW — honest friction on the simulated runner leg** (post exit-booking-fidelity 19bcb0f: decision price − calibrated sell slip − fees per leg): runner fill × (1 − 0.703% calibrated sell slip [live p50, 149 legs] − 0.5% ultra platform fee) − $0.17 tx fee (= 0.17pp on $100 positions; the runner leg pays its own fee). Applied uniformly to every cell **including the shipped baseline**, so cell-vs-cell is apples-to-apples. The realized reference legs were booked pre-fix (no modeled friction), so the "delta vs realized" column is conservative by ~0.8pp/position against ALL peel cells equally. Friction is why the original +72.0 (0.50/5pp) reads +28.8 here: 52 × (0.17 fee + ~0.5 × 1.2pp leg slip) ≈ −40pp — the geometry comparison is unaffected.

**n everywhere:** comparable corpus 237 positions / **102 distinct tokens** (Fatcoin excluded from all columns equally — TP1-fired, no bars). TP1-fired 66; **eligible (fill < +12): 52 positions / 35 distinct tokens**; wick 14. Losers never fire TP1 → loser book identical in every cell (no-harm carried over from the original replay).

## Coverage limit (truncation honesty)

- Available bars past TP1: min 0.97h, p10 5.45h, **median 5.80h**, max 5.96h; **50/52 runners have ≥3h coverage, 49/52 have ≥5h**. So the 3h/5h max-holds ARE genuinely testable on this corpus (bars were harvested to entry+6h; the ~1h-coverage worry does not apply).
- Runner exit-time (from TP1): gb5 med 3min / p90 18 / max 32; gb8 med 6 / p90 25 / max 42; gb12 med 15 / p90 73 / max 214. **Zero runners still open at window end in every cell** (n_open = 0 across all 27 variants). 3h max-hold binds exactly once (one gb12 runner, +7.7pp improvement); 5h never binds.
- Consequence: the decode's 45–290min trail durations are **not reachable on our remainders** — within fully covered horizons, our post-TP1 paths hit peak−5 in ~3min and peak−12 in ~15min. This is a property of our entries' price paths, not data truncation. What we CAN'T test is a trail wider than 12pp or a floor below −12 (out of scope; floor is a keep).

## The grid — total book pp (237 comparable positions; realized reference −73.0pp)

Runner WR = runner net fill vs the TP1 fill it declined to take (the runner-half win/loss split; same runners across slices, weights differ). dWR = positions beating realized. Max-hold rows shown only where they change anything (they don't, except gb12/3h).

| cell (slice/giveback/maxhold) | total book | Δ vs realized | runner W-L (WR) | dWR | worst single token (Δpp) | still open @ end |
|---|---|---|---|---|---|---|
| **0.75 / 5pp / none — SHIPPED** | **−58.5** | **+14.5** | 18-34 (35%) | 25-26 | JELLYCAT −4.3 | 0 |
| 0.50 / 5pp / none (orig headline) | −44.2 | +28.8 | 18-34 (35%) | 19-33 | JELLYCAT −5.4 | 0 |
| **0.30 / 5pp / none — BEST** | **−32.7** | **+40.2** | 18-34 (35%) | 16-36 | JELLYCAT −6.2 | 0 |
| 0.30 / 8pp / none | −95.6 | −22.6 | 17-35 (33%) | 18-34 | BULLWIF −9.1 | 0 |
| 0.50 / 8pp / none | −89.1 | −16.1 | 17-35 (33%) | 18-34 | JELLYCAT −6.8 | 0 |
| 0.75 / 8pp / none | −80.9 | −8.0 | 17-35 (33%) | 18-34 | JELLYCAT −5.0 | 0 |
| 0.30 / 12pp / none | −50.7 | +22.2 | 19-33 (37%) | 16-36 | BULLWIF −17.4 | 0 |
| 0.30 / 12pp / 3h | −43.0 | +30.0 | 19-33 (37%) | 16-36 | BULLWIF −17.4 | 0 |
| 0.50 / 12pp / none | −57.0 | +15.9 | 19-33 (37%) | 17-35 | BULLWIF −12.6 | 0 |
| 0.75 / 12pp / none | −64.9 | +8.0 | 19-33 (37%) | 17-35 | BULLWIF −6.5 | 0 |

(All 5pp and 8pp cells are identical across max-holds — no runner lives 3h. Full 27-cell dump: `_peel_geom_grid_0706.json`.)

## Fat-tail check — top-5 / bottom-5 runner outcomes (net runner fill − TP1 fill, pp on the runner leg; same legs across slices)

| trail | top-5 | bottom-5 | verdict |
|---|---|---|---|
| gb5 | ELIZABETH +29.7, Bepe +27.4, SHROOM +22.2, gato +20.2, TOLY +14.3 | ELIZABETH −10.6, CA −6.5, vibes −5.0, Bullcoin −4.4, Catfish −4.4 | tail is broad — 5 distinct tokens carry it |
| gb8 | ELIZABETH +26.7, Bepe +24.5, TOLY +20.2, SHROOM +19.6, gato +17.2 | ELIZABETH −10.6, CA −9.2, Catfish −7.3, APU −7.3, vibes −7.3 | same tail, bigger giveback everywhere → net negative |
| gb12 | **SHROOM +133.6**, Bepe +24.7, ELIZABETH +22.8, HOPE +21.5, TOLY +16.2 | CA −13.2, ELIZABETH −13.2, Catfish −11.3, APU −11.3, ELIZABETH −11.3 | one-outlier variant |

Outlier-removal totals (Δ vs realized after removing the single best token / single best position):

| cell | Δ | ex-best-token | ex-best-position |
|---|---|---|---|
| 0.30/5pp | +40.2 | **+23.1** | **+20.7** |
| 0.50/5pp | +28.8 | +17.1 | +15.2 |
| 0.75/5pp (shipped) | +14.5 | +7.0 | +8.3 |
| 0.30/12pp | +22.2 | **−70.4** | −70.4 |
| 0.30/12pp/3h | +30.0 | −62.6 | −62.6 |
| 0.50/12pp | +15.9 | −49.9 | −49.9 |

**gb12 is disqualified**: its entire edge is SHROOM. gb5 survives at every slice. gb8 is the valley — wide enough to give back the whole gb5 exit premium, not wide enough to ride anything.

## Robustness of the winning cell (0.30 / 5pp / none)

- Halves: 07-01/02 **+6.9** (n=15) | 07-03+ **+33.4** (n=37) — both positive (gb12's H1 is −16.3 — another fail).
- Per-bot: flush **+48.0** (n=37) | young_absorb −4.0 (n=13) | adolescent_absorb −3.8 (n=2). Same lane pattern as the original replay — this is a **flush-lane** lever. (Shipped 0.75 is the only slice where young is positive, +1.8.)
- Gain structure: 16 gainers +104.2pp vs 36 losers −64.0pp; top-3 gains +19.5/+17.2/+14.6 (3 distinct tokens); worst-3 −6.2/−3.8/−3.4. Worst single-token outcome −6.2 (JELLYCAT). Fat-tail financed but not one-token financed.
- Trade-off stated plainly: per-position win rate drops from 25-26 (shipped) to 16-36 — the smaller slice converts many small realized wins into small friction-losses and pays for it with a broad right tail. At $100 bets with a −12 floor and worst-token −6.2pp, the variance is affordable; it is the decode winners' shape (their runner WR is also a tail game: our runner-half WR 35% with med runner below fill matches "peel med +9.8% comes from spaced slices, not from every runner winning").

## Verdicts

1. **Decode slice (~32% first slice): SUPPORTED on our entries.** 0.30/5pp = +40.2pp vs shipped +14.5pp, robust to outlier removal and half-split, flush lane.
2. **Decode trail duration (45–290min): NOT SUPPORTED on our entries.** Wider givebacks that would permit long rides lose (8pp) or win on one token (12pp). Our post-TP1 paths die in minutes; the decode winners' 141min median lives on tokens/entries we don't hold. Keep 5pp giveback and the −12 floor.
3. **Max-hold (3h/5h): moot at ≤12pp givebacks** — fully covered (50/52 ≥3h bars) and never binds except one gb12 case. No reason to ship a timer.
4. Caveats: same 4.5-day window and regime as the original replay (this is a re-slice, not new evidence); bar-granularity runner fills (pessimistic per original conservatism notes); young_absorb slightly negative at 0.30 (n=13 — keep young on 0.75 if the slice ships); the +12 conditioning threshold untouched here.

**Recommendation: keep the shipped conditional structure and 5pp trail; queue `tp1_sell_fraction`-under-peel 0.75 → 0.30 (flush lane only) as the peel_ab n≥20 checkpoint decision.** If peel_ab's live runners confirm the replay shape (runner WR ~35%, tail-financed, worst leg above −12 floor), the 0.30 slice is worth ~+25pp/window over shipped even after outlier removal. Do not widen the giveback.

## Intermediates

`scratchpad/_peel_geom_replay_0706.py` (grid), `_peel_geom_diag_0706.py` (identity check + coverage + exit times + outlier removal), `_peel_geom_diag2_0706.py` (halves + gain structure), `_peel_geom_grid_0706.json` (all 27 cells). Reuses `_tp_positions.json` + `_tp_bars.jsonl` untouched.
