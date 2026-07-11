# Railway Memory Re-Audit (#496) — 2026-07-11

**Goal:** find RAM headroom in the deployed Solana service to fund a 24/7 RH paper lane, bill trending <$25/mo.
**Method:** read-only. `railway logs` [MEM] heartbeat + local log pulls (historical RSS), full code inventory of feeds/dip_scanner.py + core/ + main.py + dashboard (two parallel explore passes), local tracemalloc measurement of ledger parse cost, and a 3-minute isolated-cwd sample run of `scripts/rh_paper_lane.py` (13 racers, live firehose) with 10s RSS sampling. No Railway config/env/deploy touched. (`railway ssh` for /data file sizes was denied by policy — ledger file size below is bracketed, not measured.)

---

## 1. Current memory picture

| When | RSS | store_resident | Source |
|---|---|---|---|
| 2026-07-06 18:55 | **3707.8 MB** | 64,581 | local log pull |
| 2026-07-10 16:33 | 3145.2 MB | 68,445 | local log pull |
| 2026-07-10 17:12 | 2974.4 MB | 68,496 | local log pull |
| 2026-07-11 17:25 (**5 min after boot**) | **3031.6 MB** | 69,710 | railway logs, current deploy |
| 2026-07-11 17:32 | 3035.6 MB | 69,710 | railway logs |

**Headline: the service is ~3.0 GB five minutes after a fresh boot.** This is not a slow leak — it is boot-resident state. At Railway's ~$10/GB-mo that is **~$30/mo of RAM for this service alone**, already over the whole-bill target before the RH lane is even discussed. Historical peak visible in pulls: 3.7 GB (07-06, long-uptime instance).

All the `[MEM]`-tracked per-token dicts are small and healthy (h24_history=103, sticky=419, fast_samples=362...) — the 2026-06-28 evictor works. The weight is elsewhere.

### Where the 3 GB lives (reconciled)

**The trades ledger is the service's RAM.** Chain of evidence:

- `store_resident=69,710` rows: `MultiBotTradeStore._base_cache` + `_jsonl_cache` hold the **entire parsed ledger resident forever** (core/multi_bot_persistence.py:433-453). One shared instance (main.py:459) — not duplicated — but never released.
- Row weights, measured on the local 5000-row `full=1` pull (`_full_trades.json`, 79 MB): **buy rows median 33.3 KB JSON, sells 2.2 KB; `entry_meta` is ~15 KB/row averaged and dominates everything else by 300x** (next-largest field is 45 B). tracemalloc: parsing costs **1.66x JSON size resident** (131 MB retained per 5000 enriched rows).
- Bracketing the deployed file (69.7k rows, ~40/60 buy/sell, older rows thinner): **ledger JSON ~500-900 MB on /data; resident `_base_cache` ~0.8-1.5 GB.**
- Boot compaction (`_ensure_trades_loaded`, multi_bot_persistence.py:350-396) parses the whole base + sidecar and `json.dumps` the union in one string — a **~2.5x-file-size transient peak** that glibc never returns to the OS. Boot peak + resident cache + baseline (numpy ~35-60 MB, aiohttp, feeds, ~150-250 MB) ≈ the observed 3.0 GB.
- Growth rate: +5.1k rows in 5 days ≈ ~1,000 rows/day ≈ **~15-35 MB/day of new JSON**, compounding at every boot.

### What got added since the 06-28 audit — verdict: clean

Everything shipped since 06-28 (WS-feed isolation + migrated-AMM vaults, sell-path canary, runner_score/HoldTape, post_exit_tracker, retrace microstructure, OHLCV sidecar, LP-drain insurance, rug-forensics stamps) is **bounded**: pure functions, disk-streaming JSONL writers, or capped/TTL structures (HoldTape 2000 rows/token + 30-min post-close drop; OHLCV 400 pts/open position; filter-shadow buf 5000, drained per cycle; onchain_ws pruned to the ~90-mint hot set). No new leak class. torch confirmed still gone; sklearn/scipy lazy and gated OFF (main.py:852-873); pandas absent. **But note:** the rug-forensics era also fattened `entry_meta` (filter verdicts, holder stamps, retrace-micro, runner shadow stamps all ride on the buy record) — the additions are RAM-clean in-process but are the reason each buy row costs 33 KB in the ledger.

---

## 2. Itemized savings table

| # | Item | File:line | Est. saving | Risk | Effort |
|---|---|---|---|---|---|
| 1 | **Ledger rotation at boot compaction** — fold rows older than N days (e.g. 21d) into `trades_multi_archive.json`, never loaded; `_base_cache` holds only the active window | multi_bot_persistence.py:350-396, 423-457 | **~0.9-1.4 GB** (resident + boot transient shrink together) | **MED-LOW.** `/api/trades` already caps at 5000 (≈5 days of fills) — no consumer served archived rows today. Risk = "overall since-inception" stats on /api/stats: preserve by writing an accumulated-totals snapshot at rotation time. Analysis workflows pull recent via API — unaffected. | ~100-150 lines |
| 2 | **Trim `entry_meta` from `_base_cache` rows beyond the newest ~6,000** (keep on disk; cache the old rows entry_meta-less) — alternative or complement to #1 | multi_bot_persistence.py:436-440 | **~0.7-1.2 GB resident** (boot-parse transient remains until #1) | **LOW.** `full=1` serves max 5000 newest; nothing reads old entry_meta from the resident cache. Disk stays lossless. | ~30 lines |
| 3 | **Stream the boot compaction** (line-wise fold, no whole-ledger `json.dumps` string) | multi_bot_persistence.py:386-393 | **~0.5-1.0 GB off the boot RSS peak** (fragmentation floor) | LOW — same output file | ~40 lines |
| 4 | `_jsonl_cache` sidecar grows ~15-35 MB/day resident until next redeploy (unbounded within a session) | multi_bot_persistence.py:447-453 | ~50-150 MB on long uptimes | LOW — mid-session mini-compaction or cap | small |
| 5 | `_h24_history` key count unbounded (deque() no maxlen, prune only fires on re-append; NOT covered by the evictor — it *looks* covered because it's in the [MEM] line) | dip_scanner.py:431, 9482, 25180 | ~10-30 MB, monotonic | LOW — add to `_evict_stale_token_state` | ~15 lines |
| 6 | numpy loaded at boot via RugClassifier import chain | main.py:60 → ml/rug_classifier.py | ~35-60 MB | MED (hot-path import latency on first rug check) | not worth it |
| 7 | Slow uncapped dicts: `Trader._token_decimals_cache` (trader.py:364), `PriceFeed._jup_block_seen/_jup_stale_logged` (price_feed.py:119-120), `PerBot._last_close_time` ×46 (per_bot_position_manager.py:156) | — | ~5-15 MB combined | LOW | tidy-up |

**Do-not-bother:** attention feed (7d TTL, ~2-5 MB), PumpPortal registries (capped 20k/30k, ~6 MB), holder cache (capped 2000, ~2 MB), HoldTape (capped, self-releasing), OHLCV sidecar (disk-streaming), shadow scorer (transient 6-hourly), dashboard (no resident ledger copy of its own — 17 call sites all per-request).

**Items 1+2+3 together: ~1.5-2.0 GB → service lands at ~1.0-1.3 GB RSS ≈ $10-13/mo RAM (from ~$30).**

---

## 3. RH lane measured working set (local, 2026-07-11)

3-minute live run of `scripts/rh_paper_lane.py` (13 racers: 10 scalp + 3 aged-pool, firehose WS connected, 41 watched pools by end, 15-22k txs/min decoded, quotes flowing):

- **RSS: 71.4 → 73.3 MB peak, flat** (10s sampling across the run)
- **CPU: 5 cpu-seconds / 120 s wall = ~4.2% of one core** (~0.04 vCPU)
- Growth bounds verified in code: per-pool tape/price/liq buffers trimmed (`del buf[:1000]`, `del s[:300]`), feed prunes pools >24h, watch set is liq/age-gated. Long-session steady state estimated **~100-150 MB** with headroom.

**Railway cost math** (≈$10/GB-mo RAM, ≈$20/vCPU-mo):

| Component | Estimate |
|---|---|
| RAM 0.10-0.15 GB | $1.0-1.5/mo |
| CPU 0.04-0.08 vCPU | $0.8-1.6/mo |
| **RH lane total** | **~$2-3/mo** |

**The prior ~$54/mo estimate is refuted by measurement — off by ~20x.** (It implicitly priced a main-bot-sized service; the lane is a 1,400-line single-process script with bounded buffers.)

---

## 4. Verdict

**(b) Separate small Railway service — the RH lane fits TODAY, before any main-service cut.** At ~$2-3/mo it is budget-noise, and a separate service keeps RH deploys decoupled from the live-trading service (which we must not redeploy casually — live probe just resumed, and no-redeploy-while-holding is standing law).

**But do the ledger work anyway — the audit's real finding is that the bill problem is the main service, not the lane.** ~3.0 GB × $10 ≈ $30/mo RAM means the <$25/mo bill target is already breached with or without RH. Recommended sequence:

1. **Now:** stand up `rh_paper_lane.py` as its own Railway service (~$2-3/mo). Unblocks 24/7 Phase-1 throughput immediately; kills the tape-gap problem. (Needs only: repo deploy with a start command, volume or upload-to-dashboard for the ledger — the existing `rh_paper_upload.py` POST path already works cross-machine.)
2. **Next deploy window (batched, NOT while holding, per standing rules):** ship savings #1+#2+#3 (ledger rotation + entry_meta cache trim + streamed compaction, ~150-200 lines total). Service drops to ~1.0-1.3 GB → **saves ~$17-20/mo**, and stops the ~$0.35/mo/day compounding from ledger growth.
3. Fold in #4/#5 (sidecar cap, h24_history eviction) with the same commit — small, same-risk-class.

**End state: main service ~$10-13/mo RAM + RH lane ~$2-3/mo — total RAM spend ~$13-16/mo, inside the $25 bill target with room for CPU/egress.** Path (a) alone (cut-to-fit in the same service) also works arithmetically but couples RH deploys to the live service; (c) architecture change is not needed.

*Caveats:* deployed ledger file size is bracketed (500-900 MB) not measured — container /data inspection was policy-denied; confirm with a one-line file-size stat in the next [MEM] report or at the next approved deploy. All savings estimates assume the 1.66x measured parse-residency ratio holds for disk-format rows.

---

## 5. IMPLEMENTED (2026-07-11, working tree — NOT committed, NOT deployed; live is ON so the main session reviews/commits/deploys in the next window)

**Cuts #1+#2+#3 + #5 shipped.** Full suite: **2528 passed, 2 skipped, 0 failed** (`python -m pytest tests/ -q`).

### What shipped

| Cut | Where | Mechanism |
|---|---|---|
| **#1 Ledger rotation** | `core/multi_bot_persistence.py` — `_rotate_ledger()`, called from `_ensure_trades_loaded` (boot compaction, under `self._lock`, before any reader) | Rows older than `LEDGER_ROTATE_DAYS` move to `trades_multi_archive.jsonl` (append-only + fsync, **never loaded at boot**). Per-bot aggregates of everything archived (`pnl`, `positions`, `wins`, `latest_time` — the leaderboard's exact sell-group math incl. MIN_TRADE_TIMESTAMP cutoff + cancelled-on-restart skip + reset_after_iso) are **re-derived from the archive file every rotation** (streamed line-by-line) and written atomically to `ledger_rotation_stats.json`. **NO-STRADDLE rule:** a `(bot_id, token)` group archives only when *every* row is old — position joins (leaderboard `(token, entry_price)` groups, `restore_positions`, live_faithful lots) never split; open-position tokens (bot_state books) protected outright. **Crash-safe/idempotent:** row-signature dedup between base and archive — a crash at any point re-heals at next boot with totals unchanged (pinned by test). |
| **#1 stat fold** | `core/ledger_stats.py` (new, pure) + `dashboard/web_dashboard.py::_build_bot_rows` | The leaderboard's sell aggregation extracted to `sell_stats(sells, archived, reset_after_iso)`; `_build_bot_rows` folds the archived aggregates via `trade_store.load_rotation_stats()` (mtime-cached). `/api/leaderboard`, `/api/bots`, `/api/live` totals **identical before/after rotation** (test-pinned + empirical smoke: totals `(-2.0, 400, 171)` invariant across all knob combos). `balance/realized_pnl_total_usd/daily_pnl_usd` come from bot_state — untouched by construction. |
| **#2 entry_meta cache trim** | `core/multi_bot_persistence.py` — `_trim_entry_meta()`, applied in `_read_disk_ledger` (append mode) + legacy `load_trades` parse | Cache rows older than the newest `LEDGER_META_KEEP_ROWS` keep all scalars but entry_meta slims to `{daily_halt_would_block, reentry_cap_would_block, _meta_trimmed}` (~100B vs ~15KB) — the two booleans preserved because `core/live_faithful_pnl.py` reads them full-history. **Disk stays lossless** (trim runs only on freshly parsed read-cache rows; boot compaction writes from its own untrimmed parse; legacy record_trade re-parses the file before appending). 6000 > the 5000-newest `/api/trades?full=1` cap. |
| **#3 streamed compaction** | `core/multi_bot_persistence.py` — `_atomic_write_stream()` | Boot compaction (+rotation rewrite) now streams per-row `json.dumps` into the temp file — kills the whole-ledger dumps string (the ~file-size, GIL-held, heap-floor transient). Same temp+`os.replace` atomicity. |
| **#5 _h24_history eviction** | `feeds/dip_scanner.py::_evict_stale_token_state` block 4 | Evicts keys whose newest sample is older than the filter's own 6h window (identical semantics — the next append would drop every sample before any read); open-position addrs protected; marks `_h24_history_dirty` so `h24_history.json` sheds them too. |

### Knobs (env, safe defaults, fail-open)
- `LEDGER_ROTATE_DAYS=21` — 0/off/unparseable ⇒ rotation disabled (load everything). Any rotation error ⇒ **loud ERROR log + full ledger loads** (current behavior).
- `LEDGER_META_KEEP_ROWS=6000` — 0/off/unparseable ⇒ no trim.
- Both are code defaults; nothing needs to be set on Railway (but can be, to tune/disable without redeploy).

### Expected MB on the deployed service (audit's measured ratios)
- #1: ~48k of 69.7k rows leave the loaded base (21d window ≈ 21k rows @ ~1k rows/day) ⇒ **~0.6-1.0 GB resident off**, and every future boot parses a ~70% smaller file.
- #2: of the ~21k remaining rows, all but the newest 6k drop their ~15KB meta ⇒ **~0.3-0.6 GB resident off** (meta is 300x every other field; empirical smoke: trim alone cut the synthetic cache 31.2→10.0 MB).
- #3: **~0.5-1.0 GB off boot PEAK** (the dumps transient is gone; the parse-side read_text+loads transient remains, now over a 70% smaller file).
- Net: **~3.0 GB → ~1.0-1.3 GB RSS** as estimated in §2; #5 stops a ~10-30 MB monotonic creep.

### Tests (new: `tests/test_ledger_rotation.py`, 13 tests)
Rotation leaderboard identity (+ reboot stability), crash-leftover dedup (no double-count), no-straddle group retention, open-position protection, unparseable-time fail-safe, env disable, fail-open on rotation error, today's-rows survival for the boot daily-pnl re-derive (circuit breakers), meta-trim window + whitelist + disk losslessness, trim disable, streamed-write round-trip, compaction no-loss/no-dup, `sell_stats` reset_after_iso fold semantics. Also: `tests/test_ledger_reader_freshness.py` fixture now pins `LEDGER_ROTATE_DAYS=0` (its fixed 2026-06 dates aged past 21d and were being *correctly* rotated).

### Consumers that WILL see a shorter window post-rotation (research/display only — flagged for review, none feed trading or the authoritative P&L)
- `/api/trades?all=1` → now "active window (21d) + sidecar", not since-inception. `?full=1`/limit paths (≤5000 newest) unaffected.
- SP4 attribution endpoints (`/api/attribution/*`, `/api/bots/{id}/details` — `pair_buys_sells` full-history), `/api/profit-sweep-sim` (full realized-pnl curve replay), honest-book / top-bots / live-faithful scoreboards → all become trailing-21d views. Since-inception raw rows remain on disk in `trades_multi_archive.jsonl` for offline analysis.
- `core/ng_scorer` (reads trades_multi.json from disk; 7d lookback ✓), `core/regime_pattern_miner` (3h ✓), regime dial (yesterday+rolling ✓), goal/live-set (7d ✓), boot daily re-derive (today ✓), `follow_capital.reconcile_from_ledger` (fires only on phantom-class corruption; smart_follow rows live in the tracker ledger anyway).
- entry_meta beyond the newest 6000 cache rows: `/api/bots/{bot_id}/trades` for a bot with no recent activity, and SP4 buy_meta reads on old rows, see the slim dict (`_meta_trimmed`) — disk lossless, and `live_faithful` keys preserved.

### Not shipped (deliberately)
- #4 sidecar mid-session mini-compaction (~50-150MB on long uptimes) — separate small change, next batch.
- #6 numpy lazy-load, #7 tidy-ups — audit already marked not-worth-it / tidy-up class.
