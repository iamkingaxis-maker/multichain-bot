# Blockscout SHADOW source — PROGRESS

Status: BUILT, wired, tested, live-smoked. Working tree only (no commits).
Applies at next lane restart; running lane process untouched.

## Done
- [x] `core/rh_blockscout.py` — fail-open client. `fetch_token_meta`,
      `fetch_holder_distribution`, pure `compute_distribution`, `blockscout_stamp`
      (bs_ dict). 10s timeout, 10-min per-token cache, single `_get_json`
      network chokepoint, NEVER raises.
- [x] Distribution math mirrors core/holder_features.py:
      hidden_supply_share_pct = 100 - pool_pct - top10_pct (insider term = 0 on
      EVM) == the eth_getLogs `visible_float_pct` -> apples-to-apples grade.
- [x] Wired into `core/rh_rug_signals.compute_entry_stamp`: merges bs_* into the
      returned stamp ALONGSIDE the reconstruction. `RH_BLOCKSCOUT=on|off`
      (default on; off = byte-identical, no bs_ keys). Fail-open merge.
- [x] Tests: `tests/test_rh_blockscout.py` (20) — coercers, normalize, dist math
      (top10/hidden/burn/pool/shoulder/scam), fail-open (meta/holders/stamp/
      malformed), cache reuse + bypass. Plus `tests/test_rh_rug_signals.py`
      +1 (bs merge off = byte-identical) and its pure-RPC test pinned
      network-free. Full RH suite: 206 passed, exit 0.
- [x] Live smoke: seedcoin -> bs_source_ok True, hidden 61.57 / top10 12.48 /
      pool 25.95 / burn 7.54, 2 calls / 5.96s cold, cache hit 0.0s.
- [x] `compare.py` grader — joins dual-source rows, per-field agreement table
      (mean/median |Δ|, within-tol %, bias), completeness (truncated/err) and
      cost (recon RPC calls/secs vs BS 2 calls). Runs clean on current ledger
      (112 recon rows, 0 dual-source yet — expected pre-session).

## Cost win
Reconstruction: 40-60 paced eth_getLogs, up to 90s (aged hot tokens).
Blockscout: 2 keyless HTTP calls (meta + holders), ~1-6s cold, 10-min cached,
0 network on re-stamp. Both run on the background stamper thread -> zero entry
latency either way; the win is RPC-call volume + no 90s tail.

## Next (graduation gate)
1. Run a live lane session with RH_BLOCKSCOUT=on (default). Each booked entry now
   writes an {"ev":"rug_signals"} row carrying BOTH sources.
2. After stamps accrue: `python scratchpad/rh_blockscout/compare.py --tol 3`.
3. GRADUATE (bs_ replaces the reconstruction) when: within-tol% high on the key
   pairs (esp. hidden_supply_share vs visible_float, top10), no systematic bias,
   AND Blockscout is more complete on the rows where the reconstruction
   truncates. Until then bs_ is SHADOW only — nothing gates on it.
