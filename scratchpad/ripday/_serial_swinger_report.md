# Serial-Swinger Discriminator Study — 2026-07-03

**Question:** what separates serial swingers (>=3 winning swings in the swing-latch sim) from one-and-done tokens, using features observable at/before the FIRST swing entry?

**Verdict: SHIP — but the discriminator is TOKEN AGE, and the shipped `badday_swing_latch` config points at the exact anti-pond (`age_h_min: 6.0`). Flip the age window to age <= ~1h and the per-token economics go from +2.7 to ~+28 net (median-positive, split-stable). The config as deployed selects a cohort that is 31% serial and −10 net/token.**

## Setup / replication

- Universe: merged minute bars `ohlc2_*.json` + `ohlc_*.json` (172 unique pairs; `ohlc_` is a 90-pair deeper subset; bars deduped by ts per pair). 169 pairs with >=30 bars. `_gt_bars/` checked and excluded (launch-arc mine: zero pair overlap, median 4 bars, no meta/tape).
- Sim replicated per spec: entry at close <= rolling_peak*0.65, exits on closes at +25 TP / −12 stop / 90-min timestop, peak reset from exit, per-token sequential. Win = gross pnl > 0.
- Replication vs motivating sim: 130 tokens produce >=1 swing (vs "216" quoted — different universe roll-up), 625 swings, after-WIN swings mean **+2.69** gross (vs +4.09 quoted). Direction and structure replicate (edge concentrates after wins; ~half of first bullets lose: first-swing win rate 49.2%); levels are universe-dependent — treat all magnitudes below as internally consistent within THIS 130-token set.
- Scripts: `serial_swinger_study.py` (sim + features → `_serial_rows.json`), `serial_discriminate.py` (threshold scan), `serial_validate.py` (splits + economics), `serial_final.py` (robustness). All in `scratchpad/ripday/`.

## Base rate

- **46/130 = 35.4%** of latched tokens are serial swingers (>=3 winning swings).
- Unconditioned latch economics (sum of swings until first loss, incl. the loss): **+7.12 gross/token, +2.68 net/token** at 2.6pp/swing round-trip, 1.71 legs/token. **Median −9.10** — classic fat-tail: 41% of tokens positive, top token = 41% of total net.

## Top discriminators (first-entry-observable)

### 1. Token age at first swing — age_h <= 1.0 (THE lever)

Serial swingers are tokens whose first −35% swing happens in their **first hour of life**. Median age at first swing: serial **0.70h** vs other **6.24h**.

| gate | n_sel | precision | recall | net/token | median net |
|---|---|---|---|---|---|
| age<=0.5h | 30 | 63% | 41% | +28.17 | +13.10 |
| **age<=1.0h** | **37** | **65%** | **52%** | **+28.18** | **+18.86** |
| age<=2.0h | 43 | 60% | 57% | +22.90 | +7.55 |
| age<=6.0h | 52 | 58% | 65% | +18.74 | +6.76 |
| age>6h | 35 | 31% | — | **−6.37** | −14.90 |
| age unknown (no meta) | 43 | 12% | — | −9.37 | −14.83 |

Fine bands: age 1–3h → net −5.5 (n=8, thin); 3–6h → −3.5 (n=7, thin). The <=6h aggregate is carried entirely by the <=1h core.

**Split stability (age<=1h):** net/token T1 +27.3 / T2 +28.9 (precision 82%/50%, but T2 base is 25% vs T1 46% → lift ~1.8–2.0x in both halves); alternating tokens +22.0 / +35.5. Concentration is LOW for this project: top token = 14% of cohort net, ex-top-3 still +18.4/token, **62% of tokens net-positive, median +18.9** — a median-positive selector, which almost nothing in this project has produced.

### 2. Pre-entry oscillation width — range_mean_60m >= 12 (proxy/fallback)

Mean per-minute (high−low)/close over the 60m before first entry: serial median 15.9 vs 6.9.

- Full set: prec 54% (base 35%), recall 61%, net **+10.83**/token — remarkably split-stable (+10.8/+10.9 time halves, +11.3/+10.3 token halves) but median only +1.6 and top-share 26% (ex-top-3 +3.25).
- **Confound:** spearman(age, range) = **−0.87** — it is mostly a youth proxy. Within age>1h it still points right (5/8 serial vs 12/40 baseline — THIN n=8); within the no-meta cohort it fails (2/13). Use as an AND-refinement or as the fallback when age is unknown, not standalone.
- **young AND range>=12:** prec 68%, net +28.71, median +18.86, stable across all four splits (+25.9..+32.1) — best precision cell, at recall 46% (n=31).

### 3. First-swing bounce speed — trough→+10% <= 5 min (continuation gate, NOT entry)

Observable only during the first swing, so it can't gate the first bullet — but it can gate whether the latch stays open after it: keep re-entry only if the first bounce was fast. Uncond +2.68 → **+3.75 net/token** (legs/token 1.71→1.64). Modest, additive, free to implement in the latch bookkeeping.

### Dead / unusable candidates

- **Tape absorption at first swing:** only 14/130 tokens have ANY tape prints in the entry window (tapes were event-window captures, not continuous) — **no coverage, untestable here.** Would need continuous tape recording at swing-entry time to instrument.
- **Participation (bars-printing rate):** saturated — both classes print ~every minute (bars_rate_full median 1.03 vs 1.01). This universe was pre-selected on traction, so the seven-study participation prior has no variance to work with here; NOT falsified. (Raw bars_rate_60m even points "down" for serial — mechanical artifact of <60m-old tokens having empty window minutes.)
- **Liquidity/mcap as positive selectors:** null on their own (serial vs other medians ~equal). But as FLOORS they interact badly with the young pond — see below.
- **vol_cv_60m (volume consistency):** null (0.75 vs 0.79).

## The config conflict (why this is urgent)

`config/bots/badday_swing_latch.json` currently has **`age_h_min: 6.0`, `liquidity_usd >= 25000`, `mcap_min: 50000`**. Tokens passing those three at first-swing time in this dataset: **n=13, 31% serial, net −9.98/token.** The gate excludes 30/46 serial swingers by age alone, and `liq>=25k` cuts over half the rest (serial median liq at first swing = 21.2k; young-cohort median 18.4k). As configured, the paper A/B is pointed at the anti-pond and should be expected to lose regardless of the latch logic.

Within the young pond, floors that keep the economics intact:
- young + liq>=10k: n=24, 71% serial, net +34.9, median +16.5
- young + liq>=25k: n=14, 71% serial, net +24.7 but median flips **−13.6** (thin, cuts too deep)
- young + mcap>=30k: n=21, 71% serial, net +33.6, median +14.1

## Recommendation — SHIP (config change, feature exists at decision time)

Age, liq, mcap are all already decision-time inputs in the bot. Proposed edit to `badday_swing_latch.json`:

```json
"age_h_min": 0.1,          // was 6.0 — this is the whole finding
"age_h_max": 1.5,          // precision operating point; 2.0 if volume too thin
"mcap_min": 30000.0,       // was 50000
// entry_gate: "liquidity_usd" >= 10000   (was 25000)
```

Pair it with the already-shipped **young holder rug guard** (top1>=30 | top10>=70 enforce) since this moves the bot squarely into the young lane — which is also the only currently-green lane, so the ponds agree.

Second wave (needs instrumentation, ship as shadow log first):
1. **range_mean_60m >= 12** as an AND-refinement — requires a 60m minute-bar buffer at entry (fast-watch rolling buffer or io.dexscreener bars). Log it per swing-latch entry now, enforce later if the age-gated cohort's precision needs the extra ~+3pp.
2. **Latch-continuation rule:** close the latch (no re-entry) if the first swing's trough→+10% took >5 min. Pure bookkeeping in the latch state, +~1.1 net/token.

**Expected per-token economics if latched only on the discriminator:** ~**+28 net/token** (gross ~+34.6, 2.46 legs/token, median +18.9, 62% of tokens positive, ex-top-3 +18.4) vs **+2.68** unconditioned — on n=37, stable across time halves and token halves.

## Caveats

- n=130 tokens; meta (age/liq/mcap) covers 87/130 — the no-meta 43 are 12% serial, so live (where age is always known) the gate's realized precision should hold or improve, but the meta-covered base rate (47%) is above the full-set base (35%): some of the raw precision is coverage, the ~1.8–2x LIFT is what validated.
- Age bands 1–3h and 3–6h are negative but THIN (n=8, n=7) — the age_h_max choice between 1.0 and 2.0 is a volume/precision trade made on thin cells.
- Magnitudes are from this cached-bar universe with close-based fills; the motivating sim's +4.09/swing did not fully reproduce here (+2.69) — apply the usual haircut discipline; enforce-bar stays per-token mean >= +2 over >= 5 days on the paper A/B, judged realized.
- One data-collection window (2026-06-30..07-03 traction set); no multi-week regime coverage.
