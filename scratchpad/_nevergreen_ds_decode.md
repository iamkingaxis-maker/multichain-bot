# NEVER-GREEN ENTRY DECODE — DexScreener microstructure study
2026-07-05 · bots: badday_flush, badday_young_absorb · sells since 2026-07-02 (4 days)
Data: per-bot trades API (labels + decision-time entry_meta) + live io.dexscreener probes.

## 0. Labels (scrub rule applied: 2 dropped; partial sells deduped to first leg)

| bot | NEVER_GREEN (peak<=0.5 & hold<=300) | BOUNCED (peak>=3) | OTHER | NG pp |
|---|---|---|---|---|
| badday_flush | 78 | 53 | 28 | **-452.1** |
| badday_young_absorb | 34 | 21 | 4 | **-229.9** |

Matches the brief exactly (78/-452, 34/-230).

## 1. Coverage (honesty first)

**Retro DS trade-log fetch is DEAD for this study.** The io.dexscreener log
(`/dex/log/amm/v4/{slug}/all/solana/{pair}`) holds only the **last ~100 swaps** — no
pagination (`c` param inert), and bars ignore `to`/`tb`. Probes: an entry from 40
minutes earlier was already flushed out of the log. Live sweep over 60 of our entry
pairs: **0/60 pre-entry windows retrievable**. 1m bars cap at 999 bars (~16.6h back).

**The decision-time tape was, however, already captured**: entry_meta on our own buy
rows carries the full DS-trade-log + bar feature set computed at the decision instant
(rt_* print counts/USD, buy_burst_30s, net_flow_15s/60s/5m, maker concentration,
whale_max_buy_usd, largest_buy_to_largest_sell, 1m/1s bar shape). All features below
are decision-time-only by construction (the scanner computes meta, then fires).

| feature family | coverage (218 entries) |
|---|---|
| rt_* / maker features (unique_buyers, top5, burst) | 105/218 (48%) |
| whale_max_buy_usd, largest_buy_to_largest_sell, median/p90 buy | 117-170/218 (54-78%) |
| 1m bar features | 137/218 (63%) |
| 1s bar features (red_count_60s, close_pos, vol_decay) | up to 204/218 (94%) |

Coverage is NOT random: flush tape coverage collapsed 100% (07-02) -> ~65% (07-03,
partial) -> ~35% (07-04/05). Any rt-only gate is below the 50% insufficient-data
line; the shipped predicate below uses largest_buy_to_largest_sell (61% within flush
NG+BOUNCED, fail-open on missing) and its value must be read with that caveat.

**Missing feature we could not measure at all:** largest single SELLER's share of
sell volume (maker-level sell concentration). Not in entry_meta, not retro-fetchable.
The live tape HAS makers (see §5) and `feeds/wallet_flow_features.py` already computes
seller_hhi — capture it into entry_meta now, decode it next week.

## 2. Feature separation (NG vs BOUNCED medians; AUC = P(NG>BOUNCED), 0.5 = null)

Pooled unless noted. Only the load-bearing rows; full table in `_ng_separation.json`.

| feature | med NG | med BOUNCED | AUC pooled | flush | young |
|---|---|---|---|---|---|
| **whale_max_buy_usd** (largest buy print) | 153 | 373 | **0.34** | 0.33 | 0.31 |
| whale_max / liquidity (bp) | 32 | 86 | 0.37 | 0.38 | 0.31 |
| **largest_buy_to_largest_sell** | 0.45 | 0.56 | **0.41** | 0.41 | 0.42 |
| rt_max_buy_usd | 351 | 442 | 0.39 | 0.34 | 0.51 |
| median_buy_size_usd (existing gate axis) | 10.0 | 12.0 | 0.43 | 0.40 | 0.46 |
| liquidity_usd (already gated) | 34k | 41k | 0.34 | 0.29 | 0.45 |
| 1s_vol_decay_120s (NG = vol NOT decaying) | 1.05 | 0.74 | 0.58 | 0.57 | 0.59 |
| net_flow_60s_usd (NG HIGHER — small-buy churn) | +85 | +24 | 0.56 | 0.62 | 0.42 |
| buy_burst_30s_count (final-30s buys) | 1 | 2 | 0.44 | 0.47 | 0.63* |
| 1s_red_count_60s | 3 | 4 | 0.53 | 0.48 | 0.67* |
| unique_buyers_n (existing gate axis) | 41 | 39.5 | 0.52 | 0.56 | 0.46 |
| rt_sells_n / rt_sells_usd / rt_max_sell_usd | ~= | ~= | 0.46-0.50 | — | — |

\* young-lane direction inverts vs flush — the ponds differ, as expected.

**The signature is a BUY-side absence, not a sell-side presence.** Sell prints, sell
USD, largest-sell size, and sell-velocity trend do NOT separate (AUC 0.46-0.50): both
never-green killers and bounces are being dumped equally hard at our trigger. What
separates them is whether the dump is MET: bounces have a large single buyer stepping
in (whale_max $373 vs $153; biggest-buy/biggest-sell 0.56 vs 0.45), killers have
small-buy retail churn (positive 60s net flow but tiny max print) and sustained
selling volume (1s_vol_decay >= 1). This is the greenday winner decode (winners = dip
MET BY buyer size) resurfacing at print granularity.

Per-day check: whale_max direction holds all 4 days (NG med 79/0/447/297 vs B med
331/213/672/680) but the LEVEL shifts ~5x with market heat -> absolute-$ gates are
regime-fragile; only the ratio form survives the half-split.

## 3. Candidate predicates (block entry when true; fail-open on missing features)

Winner-kill rule: reject if >15% of BOUNCED killed. Net pp = -(pnl of all blocked
entries incl OTHER), 4-day window. Halves = 07-02/03 vs 07-04/05.

| predicate | scope | NG recall | winner-kill | net pp/4d | halves net | verdict |
|---|---|---|---|---|---|---|
| **P1: largest_buy_to_largest_sell < 0.3** | flush | 17/78 (22%) | **6/53 (11%)** | **+92.3** | +73 / +19, dir Y/Y, all 4 days >= +1 | **PASS — ship shadow** |
| P2: P1 OR whale_max_buy_usd == 0 | flush | 33/78 (42%) | 10/53 (19%) | +201.6 | +182 / +19, dir Y/Y | FAILS 15% rule — shadow-only variant |
| P3: whale_max_buy_usd < 150 (absolute $) | flush | 23/78 (29%) | 9/53 (17%) | +136.2 | +129 / +7 | rejected: level-shift fragile (all value 07-02/03) |
| P4: 1s_red_count_60s >= 8 | young | 28/34 (82%) | 10/21 (48%) | -11.4 | -16 / +4 | REJECTED (winner-kill 3x over) |
| P5: no-tape (fail-closed on missing tape) | pooled | 67/112 (60%) | 28/74 (38%) | +112.7 | +101 / +12 | REJECTED as a gate — fix coverage instead (§5) |

P1 details: killed bounces are all small (peaks 3.8-10.2, realized +5..+8pp); NG saved
+148.7pp gross. Per-day net +44.0 / +28.9 / +1.0 / +18.4. Semantic: the largest buy
print in the decision tape is under 30% of the largest sell print — a whale dump met
by no comparable buyer.

**Young lane: NO predicate passes.** Every whale/absence gate that works on flush
kills young's fat-tail winners (whale==0 alone would have killed +45.6 and +70.3pp
young bounces). Young's never-green loss was concentrated in NO-TAPE entries (26/34
NG were no-tape; NG rate 72% no-tape vs 35% tape) — which the fail-closed no-data
gates shipped 07-04 already address (measured here: roughly pp-neutral over the
window but kills the -187pp NG variance). Young verdict: covered by existing ship;
no new gate.

## 4. Independence from existing machinery (not a re-ship)

- whale/lbls axis is NOT a proxy for the shipped crowd-quality axes: within entries
  already passing medbuy>=8, whale_max AUC stays 0.37 (n=70); within buyers>=20,
  0.40 (n=90). Rank-corr(whale_max, median_buy)=0.52 — related but independently
  informative. medbuy_sub8 (shadow, 07-04) reads the CROWD's median; P1 reads the
  single-largest-buyer vs single-largest-seller — different failure mode.
- sell-side features add nothing (AUC~0.5) — consistent with the badday gap audit
  ("entry covered, gaps are exit/size") now refined to: entry gaps exist only in
  buyer-ABSORPTION terms.

## 5. DS-fallback for None-tape entries: YES — decisive

113/218 entries (52%) had buyers=None at decision time (tape fetch failed -> rt_*
all None). Live sweep of 40 of those tokens: **40/40 return a valid io.dexscreener
trade log with maker addresses today** (median ~70 distinct makers/100 swaps); 20/20
tape-present controls likewise. The None-tape class is therefore transient fetch
failure (timeout / circuit / fast-path tape budget), NOT token invisibility — a
decision-time retry/second-chance DS fetch would fill the skips with real data
instead of skipping (young) or entering blind (flush). This matters doubly because
flush's 07-04/05 no-tape entries alone carried -107pp of NG damage that P1/P2 could
not see for lack of tape.

## 6. Estimated pp saved per week (if gated over the last 4 days, x7/4)

| bot | predicate | pp/week |
|---|---|---|
| badday_flush | P1 (ship) | **~+161 pp/wk** |
| badday_flush | P2 (shadow; if winner-kill relaxed/confirmed at n>=100) | ~+353 pp/wk potential |
| badday_young_absorb | none (existing fail-closed gate covers) | 0 new |

Haircut warning per calibration rule: 4 trading days, one market regime; treat as
direction + order-of-magnitude, not a forecast.

## 7. Ship recommendation

1. **SHIP (shadow-first): P1 `largest_buy_to_largest_sell < 0.3` entry gate on
   badday_flush only.** Feature already lives in entry_meta (trade_log_features);
   stamp-and-count shadow like giveback/medbuy_sub8, enforce after burn-in. Fail-open
   when the feature is missing. Do NOT apply to young.
2. **Stamp P2's whale==0 branch as a separate shadow** (flush) to grow n before the
   winner-kill verdict at the union threshold.
3. **Infra, arguably the bigger lever: decision-time tape retry/fallback** — one
   delayed re-fetch (or GT->DS cross-check) when the DS log comes back empty, before
   the entry decision. DS demonstrably has the data (40/40). Restores whale/lbls/rt
   coverage on hot days (35% -> ~100%) and un-blinds P1 exactly where NG damage now
   concentrates.
4. **Capture seller-maker concentration (seller_hhi/top1 share) into entry_meta now**
   so next week's decode can test whale-dump concentration — the one requested
   feature that was unmeasurable retro.

Files: dataset `scratchpad/_ng_dataset.json`, separation `scratchpad/_ng_separation.json`,
predicate grid `scratchpad/_ng_predicate_report.json`, DS sweep `scratchpad/_ng_ds_sweep.json`,
scripts `scratchpad/_ng_{label,join,analyze,predicates,ds_sweep}.py`.
