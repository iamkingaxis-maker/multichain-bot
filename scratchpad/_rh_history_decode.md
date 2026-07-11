# RH Chain — Full-History Wallet Decode v1 (2026-07-11)

Answers AxiS's "did you decode enough wallets?" — v0 (scratchpad/_rh_wallet_decode.md) was 1 day of
recorder tape, 24 audited winners. This run backfilled chain history keylessly via eth_getLogs and
re-ran the decode with day-robustness. All data under scratchpad/rh_history/.

## Headline answers
1. **Audited winner n: 24 -> 91** (pure on-tape cost basis, union-counted, AND net-positive on >=2
   distinct UTC days). 153 without the day filter. Median audited winner net +$185, mean +$256,
   p90 +$527, sum +$23.3k. pos-day distribution: 51 makers x2 days, 25 x3, 11 x4, 4 x5.
2. **The v0 "launch-strength scalper" is NOT the whole story — it was a 1-day artifact.** At scale,
   winner entries after positive 120s inflow = 66% vs losers 65% (v0 said 80/88) — strength-chasing
   does NOT separate winners from losers. What separates them: **hold time (19.2m vs 2.6m median)**
   and pool maturity (below). Size discipline still directionally holds (med buy $66 vs $89).
3. **Dip-buyer winners exist: 27/91 audited winners take >=50% of entries below the 10-min rolling
   high.** Dip share of entries: winners 37%, losers 44% — dip-entry alone is neither edge nor
   anti-edge here; the market's grain does not forbid our dip lane, but it doesn't reward it per se.
4. **Where the durable money is: established pools, not launches.** On full-history pools, audited
   winners' median entry is at pool age ~10.7 DAYS with 31.8m holds; on the young recorder pools the
   same cohort scalps 6.3m-old pools with 1.2m holds. Day-robust realized profit concentrates in the
   mature-pool style — echoes our Solana adolescent_absorb / winners-pond finding (6-24h+), vs the
   launch-scalp which v0 over-weighted. (Caveat: backfilled mid-tier pools are day-spread and
   therefore old; entry-age is partly shaped by that selection. The hold-time and per-style net
   contrasts are not.)

## Chain facts (from full discovery, block 1 -> head 6,519,958)
- Genesis 2026-04-30 (private/testing era); first pools mid-May; **real trading begins 2026-07-01**
  (public launch, $13.4M day); **bot era from 07-08** ($533M -> $790M/day, 10x the human era).
- **61,121 pools total** (54,948 V3 + 6,173 V2; 56,074 WETH-quoted). Pool creation: ~800-2,600/day
  07-01..07-07, then 14k-20k/day 07-08..07-10 (spam/bot launches).
- **10.36M swap logs chain-wide** across 55,172 WETH pools (full sweep, exact counts — not sampled).
  6,416 swaps in unregistered pools (other dexes), 166k in non-WETH-quoted pools: both excluded.
- Population quality census (>=30 swaps & >=$500 vol = "real"): **13,984 real pools, 41,188
  spam/dust**. Hard collapse (px -90% from peak with a -70% crossing): **8% of real pools**;
  median time-to-death **20 minutes** (p25 5m, p75 80m, n=1,129).

## Hour rulebook at scale (does 19-21 prime / 22-01 dead / 08-10 whale hold?)
**No — the old rulebook only fits 07-01 (launch day: 19-22 UTC was 74% of volume).** 07-02..07-07:
volume is broad 14-23 UTC (US day/evening), overnight 03-08 soft — closer to our Solana rulebook
than to the v0 RH claims. From 07-08 (bot era) volume runs hot ALL 24 hours (03-06 UTC spikes are
bot bursts, e.g. 07-10 03: 34k ETH). Net flow is positive nearly every hour of every day (chain in
inflow accrual). New-pool creation peaks 12-21 UTC. Full tables: rh_history/hour_rulebook.json.
Actionable: hour gating on RH should be REGIME-gated (human-era pattern vs bot-era flat), not fixed.

## Rug actors — still open, now with a measured population target
- Captured set contains only 3 collapsed pools -> **0 repeat pre-collapse net-positive sellers found;
  the v1 blacklist is empty, not disproven.** Population census says 1,129 collapsed pools exist with
  known collapse blocks; the resume-safe backfill (below) is the path to the cross-rug decode.
- v0's Halp lesson stands: LP-pull rugs never appear in swap tape; that actor class needs
  mint/burn + transfer decode (still not built).

## Sell-only (invisible cost basis) at scale
- 2,807 makers have sell-only ledgers in captured pools; **only 17% (469) have ANY buy elsewhere in
  the captured set** -> with 506 pools the "insider/sniper cost basis" stays invisible: **$950k of
  sell-side extraction has no on-tape buy** vs $93k resolvable. This number is the size of the class,
  not proven profit. More backfill raises resolution (14% -> 17% going from 484 -> 506 pools).

## Data & honest coverage
- Decode set: **506 pools / 97,280 maker-rows / 29,343 makers, span 06-16 -> 07-11** =
  25 full-history backfilled pools (3 lane + 22 mid-tier day-spread across 06-14..07-11 creation
  days) + 481 recorder-tape pools (07-10 day only). 0 maker-less rows.
- Chain-wide context for that set: 25+481 pools ~ 3.5% of swaps; the full sweep tape
  (sweep_logs.jsonl.gz, ALL 10.36M swaps, maker-less) backs the population stats.
- Bottleneck (measured, matters for any resume): maker identity requires full-block fetches
  (eth_getBlockByNumber(n,true)); the public RPC sustains ~20 blk/s alone but **~6 blk/s under
  contention with the live paper-lane session**; getLogs caps at 10k logs/query; **no archive state**
  (historical eth_call fails) — ETH/USD curve therefore rebuilt from WETH/USDG swap-event sqrtPrice
  (07-08+) + CoinGecko daily (before). V3-buy `recipient` topic == tx.from only 78% — rejected as a
  maker shortcut.
- What remains unknowable even with full history: open holdings (unpriced), sell-only wallets whose
  buys sit in un-backfilled pools, LP-pull rugs, cross-wallet identity (funding graph invisible).

## Resume state (a later agent continues in one command)
- `python scratchpad/rh_history/scripts/hist_backfill2.py 190000 <minutes>` (from repo root, RPC
  idle preferred) — skips the 25 done pools, continues the
  110 remaining of the 132-pool day-balanced selection (manifest: rh_history/backfill_manifest.json;
  order: mid smalls -> lane -> leaders). Sweep + discovery + price curve need NO redo.
- Next decode upgrades in priority order: (1) finish lane pools (our own market context),
  (2) target the 1,129 collapse-block pools for the rug-actor cross-reference,
  (3) re-run hist_decode.py (auto-merges hist_ + recorder tapes, dedupes).

## Actionables for the RH lane (all accrual-stage, no live changes)
1. **Do not flip the dip lane to a momentum lane** — v0's "winners buy strength" collapses at scale
   (66% vs 65%). The measured levers are hold-time (winners ~20-30m, losers ~2-3m) and small size.
2. **Add a mature-pool shadow racer**: entries on pools >6-24h old with sustained flow (the 91
   audited winners' dominant style), next to the young-pool lane — mirrors Solana adolescent_absorb.
3. **Keep the sniper-extraction gate idea** (v0 #1) — the $950k invisible-cost class is real and
   bigger at scale; per-pool sell_only share in first 30m is computable live from the feed.
4. Hour gates: regime-conditional only (see rulebook section).

Artifacts: rh_history/{pools_registry.jsonl, sweep_logs.jsonl.gz, sweep_counts.json, anchors.json,
eth_price_curve.json, eth_daily_usd.json, hour_rulebook.json, population_stats.json,
decode_results.json, backfill_manifest.json, hist_<pool12>.jsonl x25, lane_pools.json}.
Scripts: rh_history/scripts/{hist_disc,hist_sweep,hist_backfill2,hist_decode,hist_hours,hist_pop}.py
(resume = `python rh_history/scripts/hist_backfill2.py 190000 <minutes>` from repo root, then
hist_decode.py).
