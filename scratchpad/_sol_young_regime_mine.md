# SOL young-band x 03-08 UTC regime mine (2026-07-11)

AxiS: "we didn't change our sol regime at all for young bots?" — correct, and the mine says that
matters. The 03-08 UTC sleep block was applied BAND-BLIND on 07-01 (commit 1155730: "UTC 03-08
overnight entry block fleet-wide", validated on the FLUSH FAMILY's own 7-day trades, all 17 badday
configs). It was never tested per age band. Tested now at the RH-mine bar (4 halves: chrono W1/W2
AND odd/even day-of-month), the block's damage is an **OLDER-band (>24h) phenomenon (4/4)**; the
**young band (<6h) shows NO 03-08 penalty on any lens**, and universe-level young outcomes are
flat-to-BETTER overnight — same shape as the RH chain's "young 02-07 UTC is GOOD" finding.

**VERDICT: LIFT 03-08 for the young lane (paper accrual now, pre-registered grade below). KEEP the
block everywhere else — older-band harm re-confirmed 4/4.**

## Evidence base (all local caches; no new pulls)
1. **Own paper closes**: union of 25 /api/trades caches -> 11,710 closed positions, 2026-05-16..07-11,
   87% with lifecycle_age_hours (scratchpad/sol_young_regime/{build_union.py,positions.jsonl}).
2. **Universe recorder** (entry-logic independent): union of 17 event caches -> ~46k dip events with
   age_hours + exit_pct (fwd-30m) + peak_pct; coverage 05-16..06-11 + 07-03..05 (block-era!).
3. **Rug cohort v2 labels** (701 labeled) — hour axis checked, see survivorship note.
4. Provenance docs: commit 1155730; scratchpad/_market_rulebook.md (07-02).

## Survivorship — stated up front
Own-trade 03-08 fills exist only on 20 "open-era" days (05-20..06-30). From 07-01 the block makes
own 03-08 cells EMPTY (0 fills, all bands) — absence of post-block trades is NOT evidence about the
band, and the labeled rug cohort has **ZERO young-band 03-08 entries ever** (the lane was born
07-03, after the block). All own-trade tests below run on open-era days only. The July universe
slice (07-03..05) is the only current-market 03-08 evidence — the recorder kept watching while the
fleet slept.

## THE KEY CELL — young (<6h) 03-08 vs young rest-of-day, four halves

| Lens | half | 03-08 cell | rest cell | delta (wr pp / tokmed pp) |
|---|---|---|---|---|
| Own closes (open era) | W1 | n=20/8tok wr 60.0 tokmed +1.2 | n=70/30 wr 58.6 tokmed +14.6 | **+1.4 / -13.4** |
| | W2 | n=33/10 wr 45.5 tokmed -8.2 | n=103/17 wr 39.8 tokmed -10.6 | **+5.6 / +2.4** |
| | even | n=35/12 wr 48.6 tokmed -5.9 | n=79/25 wr 55.7 tokmed +3.0 | **-7.1 / -8.9** |
| | odd | n=18/6 wr 55.6 tokmed +0.5 | n=94/26 wr 40.4 tokmed -0.7 | **+15.1 / +1.1** |
| Universe raw young | W1 | n=1140/137tok | n=3825/484 | **-0.0 / +3.9** |
| | W2 | n=1512/162 | n=6256/696 | **+2.2 / +10.8** |
| | even | n=1364/157 | n=5159/637 | **+2.3 / +5.4** |
| | odd | n=1288/142 | n=4922/627 | **+0.4 / +5.0** |
| Universe GATED young (lane-gate proxy: liq>=25k, pc_h1<=-30, bs_m5>=1) | W1 | n=141/27 | n=274/96 | **-5.6 / -3.2** |
| | W2 | n=105/32 | n=390/115 | **+6.3 / +7.8** |
| | even | n=134/25 | n=361/119 | **-1.0 / -7.0** |
| | odd | n=112/34 | n=303/97 | **+1.8 / +2.4** |

- "03-08 hurts young": own trades 1/4 (wr) 2/4 (tokmed); universe raw **0/4 on tokmed — 03-08 is
  BETTER in every half**; gated 2/4. **FAILS the 4/4 bar in every lens — no young penalty exists.**
- Universe young catastrophe proxy (exit<=-30% in 30m): 03-08 LOWER in **4/4** halves
  (-0.9 / -5.0 / -1.6 / -4.9 pp). Overnight young dips die LESS, not more.
- July block-era slice (market-wide, n=249/36tok in 03-08): young 03-08 wr 51.0 vs rest 41.9,
  tokmed -2.0 vs -13.7, cat30 15.3% vs 27.1% — the blocked hours were the BEST young block while
  we slept. One 2-day slice: color, not a half.

## The block is real — for the OLDER band (do not touch)
Own closes, older (>24h), 03-08-vs-rest, four halves: wr **-8.5 / -22.5 / -15.2 / -16.1 pp**,
tokmed negative 4/4. This is the family loss the 07-01 remine caught (family = flush bots; open-era
03-08 own-trade mix: older n=738 vs young n=53). Mid (6-24h) shows no consistent penalty (1/4) and
03-08 is its best block on the all-data table — a follow-on candidate, not part of this verdict.
Mechanism per the 07-02 rulebook: overnight dip COMPOSITION is demand-less (29% demand-pass, worst
of day) but demand-met dips bounce fine (57-81%) — and the young lane's entry gates
(unique_buyers_n>=10, net_flow_15s_imbalance>=0, liq>=25k) are precisely a demand gate. The block
is redundant protection for this lane and 5h/day of throughput on the rate mission.

## Rug-rate by band x hour
- Labeled cohort (701): young 03-08 **n=0** (survivorship — cannot be used either way). Older 03-08
  n=9 cat 22% vs rest 4.7% — direction agrees with keeping the older block (n tiny). Hour-level
  cat rate on all labels is flat (~12-33%, no 03-08 spike, 65 events).
- Universe cat30 proxy: young 03-08 lower 4/4 (above); older band cat30 ~1-2% everywhere (matches
  RH: rug risk is a young-pool phenomenon, but NOT an overnight one).

## Honest caveats
- Own-trade young 03-08 = 53 closes / 18 distinct tokens over the whole open era (6-12 tok/half) —
  below the n>=20-token bar on its own; the LIFT rests on (a) the block never having young-band
  evidence, (b) universe 4/4 flat-to-favorable at n=137-162 tok/half, (c) gated-emulation flat.
- Universe exit_pct is fee-less fwd-30m; relative comparisons only.
- Open-era young trades came from pre-young-lane bots (different entry logic) — composition risk
  both directions; that is exactly why the grade below runs on the lane's OWN realized closes.
- 06-12 lesson (hour patterns = composition artifacts) cuts FOR the lift: the fleet's overnight
  loss was composition (older, demand-less), not a market clock effect on gated young dips.
- Young 09-13 UTC looks genuinely bad on own trades (wr 25.9%, med -8.0, n=108/23tok, all-data) —
  consistent with the rulebook's 09-13 shadow-block; NOT part of this verdict, worth its own 4-half
  pass before anyone acts.

## Ship spec (verdict only — main session ships with AxiS approval; NO code changed by this mine)
Config (3 mission bots + their twins, so paper/live parity holds):
- config/bots/badday_young_rt.json, badday_young_absorb.json, badday_young_vsnap_ab.json
  (+ badday_young_rt_paper.json, badday_young_absorb_live.json):
  `"trading_hour_utc_start": 0, "trading_hour_utc_end": 24`  (0/24 = window disabled per
  core/bot_evaluator.py:1186; no test pins the young bots' window — test_wickride_ab/test_swing_latch
  pin their OWN configs only).
- Leave every other config at 8/3 (older-band harm re-confirmed 4/4). Leave adolescent lane as-is.
- Verify Railway TRADING_START_HOUR_CT/END_HOUR_CT don't re-impose a trader-level CT block upstream
  (open-era fills at all 24 hours say it's currently wide, but verify per railway-env-sync rule).
- Fleet is PAPER_MODE=true (HOODLANA pause) -> the lift accrues overnight paper data at zero live
  risk; nothing goes live before the rug-forensics resume gate + AxiS, unchanged.

Pre-registered grade (RH bar, union-counting across the 3 bots):
- Accrue until n>=20 distinct tokens entered 03-08 UTC across >=5 distinct days.
- KEEP LIFTED iff tokmed(03-08 closes) >= tokmed(same-bots rest-of-day closes, same days) - 2pp
  AND catastrophic-close rate (pnl<=-30%) in 03-08 <= rest + 5pp.
- EARLY RE-BLOCK (don't wait for n=20) if 03-08 cat-rate > rest + 10pp at n>=10 tokens, or any
  03-08 session trips a bot's $25 daily-loss cap. Re-block = restore 8/3 on the 5 files, one commit.
- Expected throughput: gated universe supply = median 2 (mean 2.6, max 8) distinct young candidates
  per night in 03-08; lane fires ~1.1 fills/hr at the block edges (h00-02: 30 fills/9d) ->
  estimate +3-6 fills/day toward the >=20/day rate mission.

Artifacts: scratchpad/sol_young_regime/{PROGRESS.md,build_union.py,analyze.py,universe_mine.py,
gated_emul.py,rug_join.py,positions.jsonl}.
