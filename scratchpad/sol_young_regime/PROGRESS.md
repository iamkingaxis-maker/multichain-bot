# SOL young-band x regime mine — PROGRESS

## Task
Was the 03-08 UTC sleep block age-banded when validated (07-01 family remine)? Does the young band
(<6h) deserve it? Bar: 4/4 halves (chrono W1/W2 x odd/even days) direction agreement, RH-mine style.

## Facts established
- [x] Config: badday_young_rt / badday_young_absorb / badday_young_vsnap_ab all carry
      trading_hour_utc_start=8 / trading_hour_utc_end=3 -> blocked 03-08 UTC, same as fleet. NOT age-banded.
- [x] trader.py also has a fleet-wide CT trading window (TRADING_START_HOUR_CT=3 default /
      END=17, Railway overrides) — 2026-05-14 era; gates ALL buys regardless of age.
- [x] 07-02 market rulebook (scratchpad/_market_rulebook.md): 03-08 verdict = KEEP for COMPOSITION
      reasons ("only 29% of 03-06 dips pass a demand gate, but demand-met ones bounce fine 57-81%").
      NOT age-banded anywhere in the rulebook. 06-09 flagged as best demand-met block (one-day-dominant).
      => the block was never validated per age band. AxiS's question is well-founded.

## Data built
- [x] Union of 25 trade caches -> positions.jsonl: 11,710 closed positions 05-16..07-11 (41 days),
      87% with lifecycle_age_hours. build_union.py + analyze.py in this dir.
- [x] Survivorship map: 03-08 UTC fills exist 05-20..06-30 ("open era", 20 days with >=5 fills);
      ZERO from 07-01 on (block enforced). Post-block 03-08 cells are survivorship holes, as feared.

## Core result (open-era days, 4 halves = chrono W1/W2 + odd/even)
- YOUNG (<6h): 03-08 vs rest delta wr = +1.4 / +5.6 / -7.1 / +15.1 pp; tokmed -13.4 / +2.4 / -8.9 / +1.1.
  SIGN FLIPS -> "03-08 hurts young" FAILS 4/4 (1/4 wr, 2/4 tokmed). Young 03-08 not worse; if anything better wr.
  n thin: 53 pos / 18 tokens young 03-08 total (per-half 6-12 tokens) — below the n>=20-token bar per half.
- MID (6-24h): also inconsistent (1/4). 03-08 mid actually best mid block all-data.
- OLDER (>24h): 03-08 WORSE in 4/4 halves (wr -8.5 / -22.5 / -15.2 / -16.1 pp; tokmed neg 4/4).
  => The 03-08 damage that justified the sleep block is an OLDER-BAND phenomenon.
- Catastrophe rate (pnl<=-30) young 03-08 11.3% vs rest 15.0% — NOT elevated overnight. Mid 1.0% vs 7.2%.
- Young 09-13 is the truly bad young block (wr 25.9%, med -7.95, n=108/23tok) — matches 07-02 rulebook.

## Universe corroboration (46k recorder dip events, 05-16..06-11 + 07-03..05)
- [x] Young raw: 03-08-worse = 0/4 on tokmed (03-08 BETTER +3.9/+10.8/+5.4/+5.0 in all four);
      cat30 (exit<=-30% in 30m) LOWER in 03-08 in 4/4 halves. universe_mine.py.
- [x] Gated emulation (liq>=25k, pc_h1<=-30, bs_m5>=1 = young-lane gate proxy): flat, 2/4 both ways.
      Supply: median 2 (mean 2.6) distinct gated young tokens per night in 03-08. gated_emul.py.
- [x] July block-era slice: young 03-08 wr 51.0 vs rest 41.9, tokmed -2.0 vs -13.7, cat30 15.3 vs 27.1
      — best young block while the fleet slept.
- [x] Older band universe: wr worse 4/4 (small), tokmed flat — own-trade older 4/4 penalty stands.

## Rug labels
- [x] rug_cohort_v2 labels: young-band 03-08 n=0 EVER (survivorship; lane born post-block). Cannot
      support either side. Older 03-08 n=9 cat 22% vs 4.7% rest (tiny, agrees with keeping older block).

## Provenance
- [x] Commit 1155730 (07-01): "UTC 03-08 overnight entry block fleet-wide" — validated on the FLUSH
      FAMILY's 7-day trades, applied band-blind to all 17 configs. Open-era 03-08 own mix was
      older n=738 vs young n=53 -> the evidence base was older-band dominated. Never age-banded.

## DONE — verdict written
- [x] scratchpad/_sol_young_regime_mine.md: LIFT 03-08 for young lane (5 config files, 0/24),
      KEEP everywhere else (older 4/4 harm re-confirmed), pre-registered grade
      (n>=20 tok / >=5 days / tokmed >= rest-2pp / cat<=rest+5pp; early re-block cat>rest+10pp @ n>=10).
      No code changed; main session ships with AxiS approval.
