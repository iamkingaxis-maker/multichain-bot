# Broad-Pond Candidate Decode Mission

Date: 2026-06-15 (analysis only, realized data, no deploys/config changes)
Goal: decode broad-pond early-buyer candidates to find NEW copyable winners + ideally a
non-dip-scalper entry edge.

## Source reality check

The task named `_our_pond_candidates.json` as a 125-wallet file (field "wallet", sorted by
winner_hits desc then early_vol). **That file does not exist in that form.** The on-disk
`_our_pond_candidates.json` (mtime 08:42) currently holds only **2 wallets** — both on the
skip list:
- AgmLJBMD... (known unfollowable proxy ghost)
- gasTzr94... (later confirmed unfollowable)

`harvest_our_pond.py` overwrites that file each run; the discovery log
(`_our_pond_discovery_log.json`) shows the cumulative harvested set is ~9 distinct wallets,
all either skip-listed or already classified in memory.

To honor the intent (decode the larger pond-overlap early-buyer set), I decoded:
1. Every non-skip wallet in the harvest discovery log (6 wallets), AND
2. The full `_new_wallet_candidates.json` runner early-buyer set (11 wallets, sorted by
   early_vol — structurally identical to the file the task described, just 11 not 125).

Total decoded: 17 wallets. Skipped per instruction: 8zkgFGVZ, 1eveYY, AgmLJBMD.

## (a) Copyable wallets found

**NONE.** No wallet cleared the copyable bar (>=8 distinct tokens AND >=8 realized closes
AND scanner-overlap>0 AND win_med>|loss_med|). Decode results:

### Harvest-log candidates (overlap our pond, but pure holders/proxies)
| wallet | tokens | closed | open | overlap | verdict |
|---|---|---|---|---|---|
| 8w1Zdm1G | 13 | 0 | 13 | 77% | 0 closed, 0.00 SOL median = holder/proxy |
| 7iVCXQn4 | - | - | - | - | UNFOLLOWABLE custody (no parseable trades) |
| 2tgUbS9U | 19 | 0 | 19 | 5% | 0 closed, holder; discovery gap |
| iK7Bmy | 27 | 0 | 27 | 48% | 0 closed (confirms memory: holder front-runner) |
| gasTzr94 | - | - | - | - | UNFOLLOWABLE custody (confirms memory) |
| HzA15v | 32 | 0 | 32 | 16% | 0 closed proxy ghost (confirms memory) |

### `_new_wallet_candidates.json` runner early-buyers (all MM/snipe/proxy cluster)
| wallet | tokens | closed | size SOL | hold | WR | overlap | verdict |
|---|---|---|---|---|---|---|---|
| 2pqEjzff | 1 | 1 | 553 fixed | 16m | 100% | 0% | MM/proxy, 1 token |
| FcoFoNCm | 1 | 1 | 607 fixed | 17m | 100% | 0% | MM/proxy, 1 token |
| FQbEHhyt | 1 | 1 | 595 fixed | 14m | 100% | 0% | MM/proxy, 1 token |
| EhNsRZM9 | 1 | 1 | 574 fixed | 17m | 0% | 0% | MM/proxy, 1 token |
| 6hE35qr8 | 1 | 1 | 554 fixed | 18m | 100% | 0% | MM/proxy, 1 token |
| DpWMPx8j | 1 | 1 | 624 fixed | 15m | 0% | 0% | MM/proxy, 1 token |
| 5L6GxeEt | 2 | 2 | 177 | 6m | 0% | 0% | snipe bot, 2 tokens |
| TEGQuLRf | 2 | 2 | 179 fixed | 6m | 50% | 0% | snipe bot, 2 tokens |
| 88Juc3T1 | 2 | 2 | 167 | 7m | 50% | 0% | snipe bot, 2 tokens |
| 8xVpe7Dz | 2 | 2 | 177 fixed | 6m | 0% | 0% | snipe bot, 2 tokens |
| D3VwXzDx | 3 | 3 | 76 | 3m | 33% | 0% | snipe bot, 3 tokens |

Key tell: the 11 runner early-buyers all cluster on the SAME 2-3 tokens
(cXYSgY37M9, 3v7aKn655k, HMUSwR6wUQ), with large fixed sizes (76-624 SOL/token), tiny
3-18min holds, and **0% scanner overlap** with our pond. That is a co-located MM/sniper
pack on a few specific launches, not a set of independent copyable traders. The
`early_vol_usd` sort surfaces exactly this whale/MM signature (the documented net-SOL /
volume trap).

## (b) Best new-archetype wallet + trigger spec

**No valid target.** Step 3 (entry-mine a non-dip-scalper copyable wallet) has no
candidate: every decoded wallet is either (i) 0 realized closes (holder/proxy), (ii)
unfollowable custody, or (iii) a 1-3 token MM/snipe bot far below the >=8/>=8 bar with no
transferable entry thesis. No entry-mine was run (would be noise on n<=3 same-token bots).

**Verdict: no NEW edge surfaced.** Not even "just another dip-scalper" — these are not
copyable traders at all. The two real copyable winners from this pond family remain the
already-known dip-scalpers (8zkg, 1eve / cool-1h edge already shipped).

## Lessons / next push (not doom — the map)

- This particular harvest snapshot is dominated by an MM/sniper cluster; one snapshot ranked
  by early_vol_usd cannot find copyable traders (re-confirms: net-SOL/volume = loser-filter
  ONLY; followability + realized diverse-WR is the seat gate).
- To find a real non-dip-scalper, the pond harvest needs a **distinct-token diversity floor
  (reject <=3 tokens) BEFORE ranking**, and a **size cap to drop 100+ SOL whales/MM bots**,
  applied at harvest time — then rank survivors by realized diverse sell-through, not vol.
- RECURRENCE is still the missing validator: the harvest has only 1 run logged
  ("0 wallets early across >=2 runs"). Re-run the harvester across more days and decode only
  wallets recurring across >=2 runs with >=8 distinct tokens.
