# Blockscout Data Source for the RH Rug/Holder Layer (2026-07-12, AxiS-approved)

SHADOW-FIRST replacement for the expensive on-chain holder reconstruction. The
Blockscout free explorer serves the SAME holder facts (ranking, supply,
holders_count) precomputed; we stamp its derived features (`bs_*`) ALONGSIDE the
existing eth_getLogs reconstruction so a grader decides which is more accurate
BEFORE anything trusts it. Nothing here gates an entry; nothing replaces the
reconstruction yet. Working tree only, no commits.

## The API (free, keyless, confirmed live)
Base `https://robinhoodchain.blockscout.com`:
- `GET /api/v2/tokens/{addr}` -> holders_count, total_supply, decimals,
  volume_24h, circulating_market_cap, exchange_rate, reputation, symbol, name.
- `GET /api/v2/tokens/{addr}/holders` -> items sorted by value DESC, each with
  address.{hash,is_contract,is_scam,reputation,metadata.tags}. 0x00..dEaD burn
  sits near the top.

## What's wired
- **`core/rh_blockscout.py`** (new): fail-open client.
  - `fetch_token_meta(addr)` -> holders_count, total_supply, decimals,
    volume_24h, mcap, reputation, exchange_rate, symbol, name (or {} on error).
  - `fetch_holder_distribution(addr, pool_addr, total_supply)` -> top1/top10/
    shoulder_11_20/hidden_supply_share/pool/burn pct + n_scam + n_holders_ranked.
  - `compute_distribution(rows, supply, pool_addr)` — PURE (unit-tested).
  - `blockscout_stamp(token, pool_addr)` -> the `bs_` dict for the ledger.
  - Robustness: 10s timeout, 10-min per-token hard cache, ONE `_get_json`
    network chokepoint, non-200/malformed handled, **NEVER raises** (any error ->
    {} or a `bs_source_ok=False` stamp with the full null key set).
- **`core/rh_rug_signals.compute_entry_stamp`**: after building the
  reconstruction stamp, `stamp.update(_blockscout_merge(token, pool))` merges the
  `bs_*` fields in. Gated by `RH_BLOCKSCOUT` (default `on`; `off` = no bs_ keys,
  byte-identical). The lane's `_rug_stamp_row` writes the merged stamp to the
  `{"ev":"rug_signals"}` ledger row unchanged — both sources land on ONE row.

## The bs_ fields (per rug_signals row)
| field | meaning |
|---|---|
| `bs_source_ok` | True only when supply + a holders page were both read |
| `bs_holders_count` | explorer total holders (meta) |
| `bs_reputation` | token reputation (`ok` / flagged) |
| `bs_total_supply` | on-chain total supply (string) |
| `bs_mcap`, `bs_volume_24h` | explorer market cap / 24h volume |
| `bs_top1_pct` | largest REAL holder (ex pool/burn) % of supply |
| `bs_top10_pct` | top-10 real holders % |
| `bs_shoulder_11_20_pct` | holders 11-20 % |
| `bs_hidden_supply_share_pct` | 100 - pool - top10 (== recon visible_float) |
| `bs_pool_pct` | pool/contract-held share (known pool addr + is_contract + pool tags) |
| `bs_burn_pct` | 0x00..dEaD + 0x0 held share |
| `bs_n_scam` | Blockscout is_scam-flagged holders on the page |
| `bs_n_holders_ranked` | real holders scored on the page (<=50; page-depth flag) |

Definitions MIRROR core/holder_features.py (Solana) so cross-chain rug-gate math
stays consistent. hidden_supply_share_pct collapses to the eth_getLogs
`visible_float_pct` formula exactly -> the direct grade axis.

## Cost win
| | reconstruction (eth_getLogs) | Blockscout |
|---|---|---|
| calls / token | 40-60 paced (aged hot) | **2** (meta + holders) |
| wall time | up to 90s (hard cap), 9-60s typical | ~1-6s cold, **0 on cache hit** |
| cache | per-pool 10 min (lane) | per-token 10 min (client) too |
Both run on the background stamper thread -> **zero** added entry latency either
way; the win is RPC-call volume and eliminating the 90s truncation tail (aged
tokens the replay can't finish, Blockscout serves instantly).

Live-measured (seedcoin, 1786 holders): `bs_source_ok`=True, hidden 61.57 /
top10 12.48 / pool 25.95 / burn 7.54, **2 calls / 5.96s cold**, cache hit 0.0s.

## Compare-grader plan (`scratchpad/rh_blockscout/compare.py`)
Joins `rug_signals` rows carrying BOTH sources and reports, per field pair
(bs_ vs recon: hidden↔visible_float, top10, top1, shoulder, pool):
mean/median |Δ|, within-tolerance %, and signed bias — plus the completeness
axis (how often the reconstruction `truncated`/`err`ed) and the cost delta.
Also spotlights the largest hidden-supply disagreements to eyeball.

**Graduation (bs_ REPLACES the reconstruction) when, over accrued live stamps:**
1. within-tol% high on the key pairs (hidden_supply_share, top10 first), no
   systematic bias, AND
2. Blockscout is at least as complete on the rows where the replay truncates
   (the aged-token tail is exactly where the free API pays off).
Until both hold, `bs_*` is SHADOW only. Graduation itself (dropping the 40-60-call
replay) is a follow-up change needing the grade + AxiS sign-off.

## Honest caveats
- bs_* scored over ONE holders page (<=50 rows). Mass hidden far down the tail
  could under-read top10 slightly; `bs_n_holders_ranked` flags page depth.
- `bs_pool_pct` classifies pool as {known pool addr} ∪ {is_contract holders} ∪
  {pool-tagged} — a non-pool contract holder (locker, bridge) inflates it. The
  reconstruction's `pool_pct_of_supply` is the exact pool vault balance; the
  grader's pool-pair diff will surface any systematic gap.
- `bs_holders_count` (explorer total) and recon `n_holders` (replay real holders
  ex pool/dead) use different denominators — reported side-by-side, NOT diffed.
