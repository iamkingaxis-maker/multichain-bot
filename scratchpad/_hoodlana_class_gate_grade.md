# HOODLANA-Class Holder-Structure Gate — Grade Report

Date: 2026-07-11. Mission: can an entry-time HOLDER-STRUCTURE gate (rugcheck /report topHolders,
same fetch the scanner already makes) catch HOODLANA-class hidden-supply dumps with winner-kill
<= 5% and negligible buy-volume impact?

**VERDICT: YES — a derived joint rule meets the bar.**
`hidden_share >= 60 AND total_holders < 1000`
(hidden_share = 100 − pool_pct − top10_real_pct − insider_pct, i.e. supply mass that is neither in
the pool, nor visible in the real top-10, nor insider-tagged)
- HOODLANA at entry: **CAUGHT** (hidden_share = 72.8, chain-verified; holders ~O(100))
- Winner-kill: **4.4%** (2/45 anybot def) / **3.7%** (1/27 strict def) — both <= 5% bar
- Universe block: **6.0%** (3/50 recent buys; 9.4% of alive-universe) — small
- Tighter variant `hidden >= 70 AND holders < 1000`: kill 2.2%/0.0%, block 2.0%, still catches
  HOODLANA but with only 2.8pp margin under its 72.84 — brittle; prefer 60.

The originally proposed axis `shoulder_11_20_pct` is **NOT discriminative** (winners median 10.0
vs universe 10.3 — same distribution). The signal lives in the DERIVED hidden mass, not in ranks
11–20 specifically. `insider` flags are dead (all zeros in every fetched report) and
`graphInsidersDetected` is non-discriminative (kills 6–38% of winners at any useful threshold).

---

## 1. HOODLANA entry-time reconstruction (chain truth, artifact `hoodlana_class_gate/hoodlana_recon.json`)

Mint `C4TFLdu1…pump`, supply 1B, created 02:10:50 UTC 07-11. PumpSwap pool state
`F6KmxYyuMDUUN2YBTGxFCirwaTXCS8TQopRvi2GCQps1` (= markets[].pubkey; it OWNS both vaults — the
forensics note calling F6Kmx "the vault" was one level off; token vault is `EpuNPqc…WagU`).
Reconstructed from vault pre/post token balances via core.rpc_pool (9000 sigs paged, no Helius):

| UTC | pool token vault (% of 1B) | pool WSOL |
|---|---|---|
| 02:15 | 127.0M (12.70%) | 154.8 |
| 02:20 | 124.5M (12.45%) | 158.2 |
| 02:25 | 119.4M (11.94%) | 165.2 |
| 02:30 | 127.8M (12.78%) | 154.7 |
| **02:35** | **963.6M (96.36%)** | **20.6** |
| 02:40 | 963.2M (96.32%) | 20.6 |
| 02:45 | 964.5M (96.45%) | 20.6 |

**The entire dump executed inside one ~5-minute window (02:30 → 02:35): ~835M tokens (83.5% of
supply) hit the pool and drained 87% of the SOL side (154.7 → 20.6).** No LP pull ever happened —
lpLockedPct stayed 100/clean because the LP tokens were never touched; the pool was drained by
SELLING hidden supply into it.

Entry-time feature values (what the gate would have seen):
- pool_topholder_pct ≈ **12.45** (chain-verified at 02:20)
- top10_holder_pct (non-pool) = **14.71** (recorded at our entry)
- **hidden_share = 100 − 12.45 − 14.71 = 72.84** — nearly 3/4 of supply invisible to both checks
- total_holders at entry: not directly measurable retroactively; currently 82 (post-rug). A
  20–30-min-old token with top10 avg 1.5%/wallet plausibly had O(100–500) holders. The
  `holders < 1000` conjunct is **highly likely but not chain-verified** — flagged honestly.
- Post-rug rugcheck now: pool = 98.87% top holder, lpLockedPct null/100 — consistent with the
  prompt's forensics.

## 2. Cohorts (all features from fresh rugcheck /report fetches, one per mint, 2.5s pacing)

- **Winners**: from `_full_trades.json` (5000-row pull), sells joined to prior buys per
  (bot_id, address) by timestamp. Two defs: **strict** = mint net-positive summed across ALL bots
  (55 mints, 27 alive); **anybot** = ANY bot closed the mint net-positive (92 mints, 45 alive).
  Only ALIVE winners fetched (death_split.json): current-state approximates entry-state for
  survivors; dead winners' current state is post-death and unusable. **Winner-kill is therefore
  measured on alive winners only, and current-state ≈ entry-state is an approximation** (tokens
  have aged; structure has evolved — both directions possible).
- **Universe**: 50 most recent distinct buy mints (2026-07-08 13:13 → 07-09 02:04 UTC), any
  outcome; 32 alive / 18 dead. Estimates buy-volume impact. Dead-universe current-state is
  post-death (pool-inflated, hidden-deflated), which UNDERSTATES block-rate on those rows —
  alive-universe block % shown as the honest upper column.
- **Feature correction required**: rugcheck topHolders now carry NO `tag` field, so production
  `core/holder_features.py` pool_topholder_pct (tag-based) reads **0 for every token** and the pool
  vault silently counts as a "real" holder. Pools were identified here by joining
  `topHolders.owner/address` against `markets[].pubkey` + `liquidityA/B` (+ our own recorded
  `pair_address`, + Raydium V4 authority `5Q544f…`). Verified on HOODLANA: its 98.87% holder's
  owner == its pump_fun_amm market pubkey. **If this gate ships, holder_features.py needs this
  owner-join fix — the tag filter is dead.**
- Known data noise: a few rugcheck pct sums exceed 100 (one universe token top10=145), giving
  negative hidden_share for a handful — these can never false-block (rule is >=), left as-is.

## 3. Rule grade table

killA = winner-kill % on anybot-alive (n=45); killS = strict-alive (n=27); uniBlk = universe
(n=50); uniAlv = alive-universe (n=32); HOOD = catches HOODLANA-at-entry (from §1 reconstruction).

| rule | killA% | killS% | uniBlk% | uniAlv% | HOOD |
|---|---|---|---|---|---|
| hidden_share >= 40 | 71.1 | 66.7 | 58.0 | 81.2 | YES |
| hidden_share >= 50 | 55.6 | 44.4 | 44.0 | 68.8 | YES |
| hidden_share >= 60 | 24.4 | 14.8 | 24.0 | 37.5 | YES |
| hidden_share >= 70 | 11.1 | 3.7 | 12.0 | 18.8 | YES |
| **hidden >= 60 AND holders < 1000** | **4.4** | **3.7** | **6.0** | **9.4** | **YES** |
| hidden >= 60 AND holders < 2000 | 11.1 | 11.1 | 10.0 | 15.6 | YES |
| **hidden >= 70 AND holders < 1000** | **2.2** | **0.0** | **2.0** | **3.1** | **YES (2.8pp margin)** |
| hidden >= 70 AND holders < 2000 | 4.4 | 3.7 | 2.0 | 3.1 | YES |
| shoulder_11_20 >= 10 | 48.9 | 51.9 | 56.0 | 62.5 | ? (entry value unknowable) |
| shoulder_11_20 >= 15 | 2.2 | 0.0 | 8.0 | 9.4 | ? |
| insider_pct >= 5 (any) | 0.0 | 0.0 | 0.0 | 0.0 | ? (flag dead in API) |
| graph_insiders >= 50 | 13.3 | 14.8 | 14.0 | 15.6 | ? (HOODLANA now reads 0) |
| pool<20 AND top10<25 | 24.4 | 14.8 | 26.0 | 40.6 | YES |

Who the recommended rule kills/blocks (hidden>=60 & holders<1000): `8LZKRa3f…` (winner both defs,
hidden 63.2, holders 910), `8NGpdSE1…` (anybot winner, hidden 71.5, holders 777), `FY9yQSFokA…`
(universe non-winner, hidden 62.7, holders 627). All three are young/small-holder-base tokens with
the exact HOODLANA shape — these are the trades the gate is designed to refuse.

## 4. Caveats / what this does NOT prove

1. **Catch-side n=1.** HOODLANA is the only chain-verified catastrophic hidden-supply dump we
   have. The rule provably catches *it*; the class catch-rate is unvalidated (same limitation the
   actor-behavior forensics hit). The forensics' "persist entry-state, build labeled cohort
   n>=30" recommendation still stands.
2. Winner-side current-state ≈ entry-state approximation (alive winners only; 28–47 dead winners
   unmeasurable).
3. HOODLANA `holders < 1000` at entry inferred, not measured (82 now, post-rug).
4. Threshold margin: HOODLANA hidden = 72.84. y=60 leaves 12.8pp margin; y=70 leaves 2.8pp.
   Recommend y=60.
5. Requires the pool-identification fix in holder_features.py (owner-join vs dead tag filter)
   before the feature is even computable in production.

## 5. Artifacts

- `scratchpad/hoodlana_class_gate/` — PROGRESS.md, features.jsonl (80 rows), features_corrected.json,
  grade_results.json, cohorts.json, raw/ (80 trimmed reports incl. markets pubkeys),
  hoodlana_recon.json (9000 sigs + checkpoints), hoodlana_current_rc.json, scripts
  (build_cohorts.py, fetch_features.py, refetch_markets.py, hoodlana_vault_recon.py, grade.py).
