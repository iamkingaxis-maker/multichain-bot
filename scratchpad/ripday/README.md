# scratchpad/ripday/ — rip-day (sol_pc_h6>1.5) wallet-decode dataset

All timestamps UTC. All files ASCII JSON/JSONL. Built 2026-07-01 by the harvester agent.
Decoder agents work on THESE LOCAL FILES ONLY (no network).

## Token universe
- `rip_runners.json` — dict token_mint -> {pair, peak (pct), sym, ts (event epoch), sol_pc_h6, liq, mcap, pc_h1, n_trades, buyers}. 89 tokens whose recorder event fired while sol_pc_h6>1.5 and peak>=25%.
- `rip_runners_live.json` — same, after the 20:47 UTC recorder refresh (authoritative superset).
- `recorder_runners.json` — dict token_mint -> {pair, peak, sym, ts:""}; the full 176-token recorder peak>=25% set (includes non-rip pump-day monsters = CONTRAST set: SUPERMAN, dog, TJR, Vulland, LUKE — see harvest_driver.py CONTRAST list).

## io.dexscreener trade tapes (wallet identity + trade legs)
- `tape_{pair8}.jsonl` — one file per pair (pair8 = first 8 chars of pair address). One trade per line:
  `{"token": mint, "pair": pair_addr, "sym": str, "kind": "buy"|"sell", "volume_usd": float, "ts": ISO8601+00:00, "maker": base58_wallet}`
  - Deduped on (ts, maker, volume_usd, kind). Accumulated over multiple sweeps ~20min apart, so coverage per token = from its oldest reachable trade (io returns last ~100 trades per call) through the last sweep. NOT a complete history — see tape_index.json for per-pair span.
  - "buy" = maker bought the token (sold SOL). volume_usd = USD size of the leg.
- `tape_index.json` — dict pair -> {token, sym, file, n_trades (deduped total), sweeps, oldest, newest (ISO ts of tape span)}.

## GT minute OHLC (price paths for entry-timing decode)
- `ohlc_{mint8}.json` — {token, pair (GT pool used), sym, event_ts, before_ts, n_bars, bars: [[epoch_s, o, h, l, c, vol_usd], ...] ascending}. limit=1000 minute bars ending ~4h after the recorder event => covers ~12h pre-run + the run.
- `sol_usd_minute.json` — {pool, n_bars, bars: [[epoch_s, o,h,l,c,vol], ...]} SOL/USD minute bars paged back to 2026-06-24 00:00 UTC. Use close for SOL price at any minute.
- `token_meta.json` — dict pair -> {name, pool_created_at, reserve_usd, fdv_usd, market_cap_usd, dex, price_usd, vol_h24, txns} from GT pools/multi (state AT HARVEST TIME 07-01 ~21:00 UTC, not at run time; run-time mcap/liq is in rip_runners.json fields).

## Wallet scoring (from tapes)
- `wallet_pnl.json` — {n_wallets, tok_sym: {mint: sym}, wallets: {wallet: {n_tokens_seen, n_tokens_traded (buy_usd>=20), n_pos, n_neg, n_open_bags, covered_net_closed_usd, pos_tokens, neg_tokens, tokens: {mint: {buy_usd, sell_usd, n_buys, n_sells, first_ts, last_ts, first_kind, sell_before_buy_usd, covered_sell_usd, net_usd, covered_net_usd}}}}}.
  - covered_net_usd = covered_sell_usd - buy_usd, where covered_sell_usd counts ONLY sells occurring AFTER the wallet's first in-tape buy (kills the sells-without-covered-buys inflation). Wallets with n_sells=0 = open bags (unrealized, not counted).
- `winners_prelim.json` — wallets with covered_net>0 on >=2 tokens (buy_usd>=20/token), ranked by (n_pos, net). FINAL winner bar is >=3 distinct profitable tokens — enforce downstream.

## Candidate identity unions (prior artifacts, local)
- `candidates_fulltrades.json` — {n_rip_hour_buys, n_wallets, recurrent_2plus, wallets:[{wallet, n_tokens, n_buys, vol_usd, tokens:[mints]}]} = buy-side makers visible at OUR rip-hour entries (_full_trades.json entry_meta.top_buy_makers where sol_pc_h6>1.5). Identity only — no P&L.
- `greenday_winners.json` — parsed _greenday_winners_out.txt: {wallets:[{wallet, hits, trips, wr_pct, net_sol, winner(bool = validated net-positive 06-29 greenday)}]}. 23 winners.
- `rip_artifact_buys.json` — subset of scratchpad/all_winner_buys.json (RPC-decoded 06-29/06-30 buys of 5 validated winners) falling inside rip windows: {wallet: [{mint, bt(epoch), sol, tok, price_sol}]}. CAVEAT: only ~6/251 artifact buys land in strict sol-rip windows; 70% cluster on 06-30 13:00-16:00 UTC = the PUMP day (contrast regime). Use scratchpad/all_winner_buys.json directly for the contrast set.

## Entry-timing recon (local join of tapes x OHLC)
- `rip_recon.jsonl` — one line per qualifying harvested BUY ($30..$3000) by a candidate wallet on a runner token: {wallet, token, sym, pair, ts, usd, bar_ts, entry_px, low15/30/90 (min low next 15/30/90m as pct vs entry, NEGATIVE = entry above later low), dip_from_high_90m_pct (entry vs prior 90m high), pos_in_prior_range (0=at 90m low, 1=at high), fwd_max_pct (max high next 6h vs entry), fwd_min_pct, mins_since_event, sol_pc_note}. Coverage limited to buys whose token has ohlc bars spanning the buy.

## Rip windows (UTC) — from recon
06-24 22:00-01:00, 06-25 05:00-09:00, 06-25 19:00-22:00, 06-26 00:00-01:00, 06-26 06:00-10:00,
06-26 15:00-21:00 (biggest), 06-27 15:00, 06-28 08:00-13:00, 06-29 03:00-04:00,
06-29 17:00-22:00 (artifact-covered), 06-30 18:00-19:40, 07-01 03:00-05:00, 07-01 13:00-19:00 (io-harvested).
NOTE: 06-30 10:00-18:00 violent token-pump day was NOT a SOL rip (contrast regime).

## Final counts (2026-07-01 21:22 UTC)
- 152 token tapes, 19,598 unique trades, 8,238 distinct maker wallets.
- Rip-window tape coverage: 07-01 13:00-19:00 = 2,691 trades / ~1,500 makers / 76 tokens (plus heavy 19:00-21:20 post-window tape for sell-leg tracking); 07-01 03:00-05:00 = 184 trades / 26 tokens; 06-30 18:00-19:40 = 227 trades / 19 tokens; 06-29 17:00-22:00 = ZERO io tape (recency gone) -> use greenday artifacts.
- 90 ohlc files (88 with bars, 53k+ minute bars; 67 cover event-60min preamble). sol_usd_minute.json = 11,994 bars 06-24 00:00 -> 07-01 21:11.
- wallet scoring: 36 wallets net-positive (covered) on >=2 tokens; 13 on >=3 tokens (winners_prelim.json). candidate_wallets_union.json = 240 ranked candidates, 16 multi-source.
- Notable: kEFiAX3jo5Nm (+$292 closed, 3/4 tokens, known follow wallet), DJocqRPK2u (+$488, prior decode artifact _decode_DJocqRPK.txt exists), 2tgUbS9UMoQD (21 pos tokens but net -$182 = high-churn spray bot), J1sfMsbxGN (3/3 pos).

## Caveats
- io tape reach is recency-limited (~100 trades/call): hot tokens = minutes of tape per sweep; cooled tokens = hours. Wallet P&L is complete ONLY for tokens whose whole run fits in tape span (check tape_index oldest vs rip_runners ts).
- Sells without a covered buy inflate naive net; use covered_net_usd.
- 06-24..06-28 windows: NO wallet trade history reachable (io recency gone, RPC banned) — price paths only.
- harvest_log.txt / _driver_out.txt / _driver_err.txt = harvest provenance.
