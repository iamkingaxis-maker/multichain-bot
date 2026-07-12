# Session Handoff — 2026-07-12 (evening) — DEEP/STABILITY FLEET + RH PROBE INFRA PROVEN

## STATE: SOL live 3 bots @ $22.5 (canary green, ~breakeven/-0.09 SOL day, churn+friction is the known drag). RH lane 23 racers PAPER 24/7. RH LIVE INFRA PROVEN (dust test passed) but probe OFF by AxiS decision ("stabilize + be green first").

## THE UNIFYING THESIS (5-agent fleet, commit 2c7d5f2, all shadow/paper, suite 2858):
"Buy structural capitulation, NEVER chase momentary demand strength, harvest base fast + keep a BREAKEVEN-FLOORED runner for the tail, cap catastrophe by early DE-RISK (not price stops — they gap through rug pulls). All volume-preserving."
- SELECTION (SOL shadow): deep(pc_h1<=-45) AND liq>=30k = ex2 tokmed +4.6 GREEN 3/4 halves. Chasing demand strength INVERTS. Hard-block cuts 80% vol -> SOFT-SLEEVE path. Stamps deep_capitulation_shadow + deep_combo_shadow; DEEP_COMBO_MODE enforce written+OFF (needs fwd n>=20 + AxiS).
- VARIANCE (both, #1 lever): CATASTROPHE CAP (early de-risk to ~25%) = -20% RH / -7.4% SOL per-trip stdev at 100% VOLUME, edge UP both chains. Rug-signal-as-entry-veto REFUTED (defense belongs in exit). VARIANCE_SHADOW (SOL) + rh_lowvar_catstop/box. ⭐ HIGHEST-LEVERAGE LIVE CHANGE when its fwd record confirms.
- RH DECODE: green racers = tight scalp exit (+6/0.75, no moonbag/box) + never raise demand floor. rh_deep_consolidated shipped. CUT candidate: demand_heavy (worst -$14.61, chases strength).
- DEEP EXIT: deep-flush bounce tail GROWS w/ depth (p90 +148->+260); BARBELL (60%@+5 + breakeven-floored 30% runner) beats scalp +1.3pp/deep-trade; time-box HURTS. rh_deep_barbell + deep_exit_spec_shadow.
- rh_f_popret (new factory racer, AxiS-spotted) = early top earner +$46/n=8 BUT that's fat-tail over +$1.94 backtest tokmed. GROW to n>=30 (fix pop_book restart-wipe), don't bank the $.

## RH LIVE PROBE — infra PROVEN, held OFF:
- Wallet 0xa454C67853A5Ac88Ad45af9E9A41870F30039c05 (AxiS's MetaMask key, funded 0.022 ETH ~$39 on Robinhood Chain; key in gitignored rh_wallet_key.txt). DUST TEST PASSED: $2 buy+sell round-trip, buy 2.3s / sell 3.6s decision->landed, cost -$0.07, rc=0. Sell path proven end-to-end.
- Probe rh_fill_probe ($7.50, 4/day, $25 stop) built + dormant (commit e1f78f3). ARM = RH_PRIVATE_KEY + RH_LIVE_CONFIRMED=true + RH_PAPER_MODE=false + RH_LIVE_PROBE_BOTS=rh_fill_probe on rh-paper-lane. Dust cmd: `python scripts/rh_dust_test.py --token 0x<lane-traded-deep-token>` (use a token the lane recently BOUGHT = V3-quotable; pools_meta liq-rank includes non-V3 pools that fail "no V3 route").
- AxiS DECISION: probe stays OFF until SOL+RH configs prove GREEN + stable. Then arm.

## DECISION PATH (self-executing shadows; AxiS pulls triggers):
1. deep+liq green shadow hits fwd n>=20 green -> propose SOFT-SLEEVE enforce (AxiS go).
2. catastrophe-cap shadow fwd record confirms -> ENFORCE on live SOL bots = the stabilize-and-green move (AxiS go). ⭐ do this first, it's highest leverage.
3. RH racers auto-grade n>=30: deepsynth/lowvar/deepexit/popret earn seats; demand_heavy earns retirement.
4. Once SOL live is green+stable under the cap+tilt -> THAT is when the RH probe turns on.

## Fable credits exhausted mid-session -> finished on Opus 4.8 (re-verified suites myself). Agents that died on limits were resumed from checkpoints via SendMessage; all fleet work landed.

---

# Session Handoff — 2026-07-12 ~14:45 UTC — FABLE DAY 2 (shipping sprint continued)

## LIVE (Solana): 3 bots @ $22.50 VERIFIED (config field = base_position_usd; absorb/vsnap had shipped at their paper $100 — fixed ee8a899, confirmed by live fill deployed=$22.5). Delta -0.025 SOL since the 2.115936 baseline (~breakeven through the shakeout). Canary healthy. LIVE_PER_TOKEN_MAX_USD=60, MAX_POSITIONS=1.

## Shipped today (all deployed + verified; suite 2722):
- 7a808ce post-TP1 FAST-WATCH (the 07-01 mine's +300-450 tok-pp lever): remainders ride the 2s loop through their OWN pm.tick; exit_cadence stamped -> grade at n>=50/arm (KPI fired-below-line <1pp vs 2.21pp systematic). Cleared by adversarial r2 + sell-serialization shim.
- d4923d7: r2 review fixes (leaderboard pre-reset P&L resurrection FIXED — authoritative-P&L class; allowlist glob anchored; _trade_sig collision; loud rotation failure) + RUG-GATE PREWARM (fire-time gate = dict read; the timing fix for buy drift med +3.4pp/p90 +14pp) + RH COLD-START fix (liq seed config/rh_liq_seed.json + burst + recheck ladder + interleave + AIMD throttle; cloud lane watch=0 all night -> watch=120 in 5 min).
- Young 03-08 lift (70a870e + moonbag twin in 7a808ce): four-half mine verdict — block was band-blind; young band regime-flat, KEEP block for older band. Guard: re-block if young 03-08 cat > rest+10pp @ n>=10.

## RH STATUS (the "need robinhood soon" push):
- CLOUD LANE FULLY OPERATIONAL 24/7: watch=183, 14k trades taped, lag p95 1.3s. AGED RACERS TRADING: aged_hold 41 closes day +$14.31 (first green for the thesis!), derisk -$8.91, deep -$1.21. Judge on the PRE-REG bar (tokmed n>=30, >=5 days, cat<=1/20) not day-$.
- CANDIDATE FACTORY agent (resumable): backtests configs over the 10.36M-swap replay vs the Phase-1 bar (four-half); 64k candidates mined; wires top 3-5 as "factory" racers. Backtest = race seat, NEVER live seat (paper confirms at n>=30).
- LIVE EXEC DORMANT + flip sequence ready. FILL PROBE offer standing: $5-10 live probe ~1 day after AxiS funds an RH wallet (mirrors Solana's badday_fill_probe_live history).
- Fill-quality data: bad fills are TIMING not size (worst +14.7% at $22-size); size impact ~+1.5-2pp per $100 on 25-30k pools -> scaling headroom to $50-75 on >=30k-liq pools when AxiS wants it (liquidity-scaled, after clean days).

## FACTORY RACERS SHIPPED (523fe21, roster 18): 5 backtest-mined configs vs the Phase-1 bar (four-half, 562/983 cells passed; dead pools booked -90% after self-caught survivorship bug). WINNER-DELTA: winners buy MODERATE pullbacks (-8.6%) EARLY in arc (+540%) on PROVEN-vol pools ($16k/200 swaps); losers buy deep flushes late on thin pools; our age floor had walled off the 88%-win <1h band (0/345 buys). Racers: rh_f_pullback (the winner cell, +$2.46 tokmed), rh_f_arc_scalp, rh_f_popret (cat 0%), rh_f_reload24 (net +$1,285, dormant til feed>24h), rh_f_reload_mid. PRE-REG: each confirms at n>=30 closes (tokmed ex-top2 green, cat<=1/20) or retires.

## RESUME-ON-LIMIT NOTE: Fable session limits killed agents twice (both times resumed clean from checkpoints via SendMessage). If the factory agent is dead at session start: resume it; its checkpoints are scratchpad/rh_factory/PROGRESS.md.

## Monitors this session (re-arm on new session): live wallet-truth v2 (change-only), fills monitor, cloud-lane log tail. Local RH lane session 10 running (expires ~300min; cloud is primary now — local optional).

---

# Session Handoff — 2026-07-11 ~23:10 UTC — LIVE RESUMED + THE FABLE SPRINT

## STATE: LIVE TRADING RESUMED (PAPER_MODE=false, 3 bots routing). Wallet 2.116 SOL baseline (AxiS deposited +1.221 SOL 07-11; baseline REBASED at 2.115936 — delta measures trading only). Canary healthy, Alchemy-primary RPC (publics demoted; drpc dropped — it 400s getTokenAccountsByOwner).

## LIVE MANDATE (AxiS "promote both", allowlisted in test_pre_live_invariants.py):
- badday_young_rt (the original probe), badday_young_absorb (FULL — n=146, +$478, green-on-red-day), badday_young_vsnap_ab (THIN n=37 — pre-registered KILL: first 10 live closes token-mean negative AND >5pp under its paper record -> live_probe=false + redeploy).
- LIVE_PER_TOKEN_MAX_POSITIONS=1 (tightened from 2): distinct tokens serve the mission, halves correlated rug tail. Sizing $11.25-22.5 nominal (fleet layer + derates).
- Day 1 back: 9 round-trips (~-0.03 SOL trading; HOMEBOY +18.6% blended winner; ANSUM & bebu went -99% AFTER our exits — containment held, and gate branch 2 now catches both at entry).

## RUG GATES (both ENFORCED live, core/rug_gate.py, all thresholds env-tunable):
- Branch 1: hidden_supply>=60 AND holders<1000 (HOODLANA class; kill 4.0-4.5%).
- Branch 2 (ff840aa): hidden>=80 AND holders<3000 (bebu/ANSUM class; 0.0% kill on every plane; holders cap MANDATORY — uncapped kills +1675%/+3630% retail-wide monsters).
- LP-unlock branch (MENSA class). Verdict stamped per-buy (rug_gate_buy). LIZARD correctly passes (fleet +$258 on it).
- Pool identification = VAULT JOIN (rugcheck dropped topHolders tag): markets[].pubkey+liquidityA/B+Raydium V4 auth.

## RH CHAIN — 24/7 ON RAILWAY (rh-paper-lane service, ~$2-3/mo, SERVICE_ROLE dispatch in main.py + supervise loop — F3 fix; lane exits 0 every 300min and restarts):
- 13 racers: 10 scalps (pinned <=24h universe) + 3 AGED (rh_aged_hold/derisk/deep; thresholds data-pinned from the >24h band: n=335 trips 73% win). Feed aged mode: RH_FEED_MAX_AGE_H=72, liq-ranked audition queue, ~50k candidates.
- Quote latency FIXED: batched fee-tier quoting 1.9s -> 0.14s (parity <=2s restored).
- Rug-defense stamps live (core/rh_rug_signals.py): RH rugs = hidden-supply DUMPS (LP unpullable — launchpad custodian owns all LP NFTs); joint_dump_shape 5/5 retro catch; absorption>=+15pp = the rug labeler. Grade at n>=30 rugged: scratchpad/rh_rug_port/grade_stamps.py.
- Regime v1 (core/rh_regime.py, four-half validated): aged avoid 19-21 UTC (the ONE gate, aged racers only); young lanes GATELESS by design (AxiS hypothesis CONFIRMED — young regime-flat; bot-era discovery bursts ~2x rug rate = stamp only); overnight 02-07 UTC is PRIME (v0 dead-zone refuted). Full regime dict stamped per buy.
- RH LIVE EXECUTION BUILT DORMANT (core/rh_live_execution.py; 64+15 tests): triple gate (RH_LIVE_CONFIRMED+RH_PAPER_MODE=false+key), canary analog, wallet-truth, $25/$25 caps, router provenance VERIFIED on-chain. Flip sequence in scratchpad/rh_live_exec/PROGRESS.md — Phase-1 bar + AxiS first.

## INFRA: memory cuts LIVE (ae724f8: ledger>21d rotates to archive at boot — 44,965 rows archived, base 70,124->25,159; entry_meta trims past newest 6000; leaderboard totals IDENTITY-pinned). Bill trajectory ~$30 -> ~$13-16/mo incl RH. Research endpoints = trailing-21d views (archive on disk).

## PRE-REGISTERED DECISION RUNBOOK (post-Fable sessions: execute, don't decide)
1. vsnap kill rule (above) — check at 10 live closes via /api/trades.
2. Rug-gate regrade at n>=10 at-entry-stamped catastrophics (~5 days @ current rates): rerun scratchpad/rug_cohort_v2/grade_v2.py. Bar: winner-kill<=5% + catches all known cases (HOODLANA 72.84/82ish, bebu 88.4/2057, ANSUM 82.0/1194).
3. Session ritual: wallet-truth delta FIRST (baseline 2.115936); python scripts/rug_cohort_label.py (needs DASHBOARD_USER/PASSWORD env; NEVER trust the DexScreener 30-mint batch for gone-mints — requery individually; NEVER urllib for DexScreener).
4. RH aged racers: grade at n>=30 closes each vs scalp control (tokmed, distinct tokens; pre-reg in _rh_aged_pool_racer_spec_notes.md).
5. RH Phase-1 bar (project_rh_mission): n>=20 distinct tok, tokmed green ex-top2, >=5 days, cat<=1/20, <=2s -> then Phase-2 flip sequence (rh_live_exec) + AxiS.
6. Sol young 03-08 mine (agent in flight at handoff time -> scratchpad/_sol_young_regime_mine.md): if verdict LIFT with 4/4 halves -> propose config change to AxiS (5h/day more live fills).
7. Mission: 20 fills / 4 distinct days -> AxiS funding talk. Count via scripts/probe_rate_report.py.
8. NEVER: `pytest | tail` before a deploy (swallowed exit code shipped a boot-crashing config today — invariant caught it); assume market prices from memory (SOL=$78, memory feedback_fetch_market_prices); cross-apply regime rules between chains without native four-half validation.

## MONITORS ARMED THIS SESSION (die with it — re-arm at next session): live wallet-truth change-monitor, live probe fills, Railway RH lane log tail (deduped). RH lane state is EPHEMERAL on Railway (no volume) — resets on redeploy; acceptable paper-v1.

## COMMITS TODAY: 405e73e..ff840aa (12 — gates, RH build x4, memory cuts, live exec, adversarial fixes, promotions, branch 2). All deploys verified; suite 2658 passing.

---

# Session Handoff — 2026-07-11 (~03:30 UTC) — LIVE PAUSED

## STATE: LIVE TRADING PAUSED (PAPER_MODE=true, verified). Wallet delta +0.0712 SOL since go-live (still green after HOODLANA rug). Book flat.
- Paused after HOODLANA (mint C4TFLdu1f2iGmKVv7crWVwQfRLApTgUFupxsvwvApump) rugged -98% despite passing ALL guards. Containment held (-$24.6 = the $25 cap). See memory project_live_paused_hoodlana.
- RESUME GATE (do NOT flip PAPER_MODE=false until): rug-forensics actor-behavior entry gate (catch HOODLANA-class vs winner-kill <=5%) + explicit AxiS approval + pre-live invariants.

## SAVED / DURABLE (all committed to git through a89a15b, survive /clear + scratch cleanup):
- RH decode reports: scratchpad/_rh_history_decode.md, _rh_wallet_decode.md, _rh_hour_rulebook.md, _runner_signature_report.md, _trail_width_analysis.md
- RH decode data: scratchpad/rh_history/{decode_results,population_stats,hour_rulebook,backfill_manifest}.json
- Memory: reference_rh_history_decode_2026_07_11 (key findings), project_live_paused_hoodlana (resume gate), project_rh_mission, feedback_sell_path_canary.
- NOT in git (local, resume-safe): scratchpad/rh_history/*.jsonl.gz (628MB raw sweep) + hist_*.jsonl tapes; continue via rh_history/scripts/hist_backfill2.py.

## RH DECODE HEADLINES (full detail in reference_rh_history_decode memory):
- Launch-scalper thesis RETRACTED (66% vs 65%). Real edge = AGED pools + longer holds -> build RH aged-pool racer (candidate). Dip lane still legit. Rug rate 8%, 20-min median death. Hour gate must be regime-conditional.

## STILL RUNNING when this session ended (a FRESH session cannot see these background agents — check their OUTPUT FILES on disk):
- RUG FORENSICS agent (the resume-gate work): deliverable scratchpad/_rug_forensics.md + scratchpad/rug_forensics/ — NOT yet on disk when handoff written = still crawling. If absent next session, re-launch the deployer/funder/insider-cluster forensic mine over confirmed rugs (see task #490 dev-not-dumped).
- RH paper fleet (10 racers) local: `python scripts/rh_paper_lane.py 300` (state restores). Uploader `scripts/rh_paper_upload.py`. Dashboard fleet card live (bots key).

## OPEN DECISIONS: build RH aged-pool racer (paper, parallel-safe); fold rug-forensics signature into #490 (serves BOTH chains — HOODLANA proved Solana has the 8% class too).
## Note: Fable5 safeguards intermittently flag this session's forensics vocabulary -> auto-switches to Opus 4.8; user re-selects Fable5 via /model. Cosmetic; nothing in the bot affected.

---

# Session Handoff — 2026-07-11 (~01:50 UTC)

## Wallet truth (Solana, LIVE)
- delta_sol **+0.3900** (baseline 0.8284), open=0, canary healthy, PAPER_MODE=false.
- Mission: fills **3/20**, days **1/4**. All 4 round-trips accounted; exec 1.44s med, fill_vs_mid +1.48% med.
- Probe liq floor lowered 30k->25k (ffa9296, AxiS-approved: "liquidity does not mean rug") — first fill under new floor pending.

## First actions next session
1. Wallet-truth delta FIRST (rule).
2. Restart RH fleet lane: `python scripts/rh_paper_lane.py 300` (state restores incl. open positions + day pnl; 10 racers). Re-arm monitors: fill relay (polls /api/live-swaps + wallet-truth every 30s), lane log tail, uploader loop (`scripts/rh_paper_upload.py` every ~3min).
3. Restart RH tape recorder (`python scripts/robinhood_tape_recorder.py 300`) or rely on rh_chain_feed.
4. Check/resume the FULL-HISTORY DECODE: scratchpad/rh_history/ (sweep DONE to head 6,522,384; sweep_counts/anchors/lane_pools/hour_rulebook present; backfill pass mid-flight — resume-safe; deliverable scratchpad/_rh_history_decode.md).
5. Solana prime window 13-22 UTC: watch probe fill rate under 25k floor; clean-dry-prime -> next ladder rung = RT_DEMAND to shadow (needs AxiS).

## Today shipped (all pushed through 6eae0a3)
- Sell-path canary (halts live buys if exits cannot size) + 3-layer RPC failover (neg-cache, breaker, 200-with-error) + Helius REMOVED (keyless publics + backups).
- Incident postmortem: buys-outliving-sells class dead; memory feedback_sell_path_canary.
- RH: fleet v1 (10 racers, 6969015), rug guards, exit-impact fix (sell-side ticking + rt-cost gate), persistence, post-exit +6h checks, runner_score shadow stamps (af383b8), moonbag A/B live on Railway (badday_young_moonbag_ab) + Solana post-exit tracker + hold-tape (HOLD_TAPE_MODE=on).
- Dashboard: sim-era sections removed; RH card + fleet leaderboard (26043ac; deploy was blocked by 1.6GB scratchpad upload -> .railwayignore fixed 6eae0a3; VERIFY the card serves `bots` key next session).
- Trail-width verdict: ladder stays; flush_runner_ab A/B accrues (enforce if positive w/ >=3 monsters or >=100 diverged pairs).

## Live observations (accrual-stage)
- QUANT case: rounds 1-2 + deep re-entries paid, shallow re-entries slaughtered — first_touch/bites2 racers green vs control -$18 day. Re-entry gate spec forming.
- rh_wide_ladder: 2 burns holding for +10 (RH pops die early) — early laggard.
- Moonbag lifecycle proven in the wild (TP2 15% + 10% kept -> floor exit ~breakeven).
- RH hour rulebook v0: 19-21 UTC prime, 22-01 dead (causal), 08-10 whale session unexplored.
- RH wallet decode v0: audited winners = launch-strength scalpers (rh_launch_scalp racer ships this); full-history decode will firm/refute.
- runner_score coverage: thin RH pools often <20 trades/10min -> Solana provides most of the validation sample.

## Open tasks: #490 (dev-not-dumped shadow), #495 (RH Phase-1 bar), #496 (memory re-audit ~$54/mo RAM -> unlocks Railway 24/7 RH lane), #497 (runner_score validation n>=30)
## Standing rules refreshed today: paper=data (no small paper daily stops); RH co-equal chain; RH latency parity (<=2s); never buys-while-sells-broken.

---

# Session Handoff — Smart-Wallet Rescue + Sustainability Day (2026-06-11)

**Bot URL**: https://gracious-inspiration-production.up.railway.app
**Mode: PAPER throughout** (`live_mode: False` verified after every deploy). No PAPER_MODE flip.
**HEAD**: `7756040`. 31 commits today on top of yesterday's 17 (the 06-10 "Bad-Day Playbook Day"
record is preserved below). Suite **692 passing**.

**THE HEADLINE: smart wallet went from "bleeding heavy, not ready" (AxiS, morning) to
effectively POSITIVE on the day (-$25.83 hot + $30 banked = +$4 since the 04:19 pool epoch,
peak recovery +$58 in under an hour after the bleed-cut). The fix was surgical, evidence-named
wallet cuts + size discipline + letting winners run — and the day ended with the
SUSTAINABILITY ENGINE built: a daily wallet-cycle loop (recruit -> vet -> judge -> cut) that
executed its first enforcement (V21GW8P, copy-tax TOXIC) within minutes of existing.
AxiS: "this is the type of daily profit im looking for... the correct cycling of new wallets
will be the key."**

---

## SHIPPED + DEPLOYED today (all paper, verified; newest first)

1. **WALLET CYCLE engine** (`4195d0a`, `scripts/wallet_cycle.py`) — the sustainability loop.
   Daily: DORMANCY (>36h silence = rotation cut) -> COPY-TAX verdicts (TOXIC at n>=10
   our-closes -> cut) -> vetted daily-positive RECRUITS promoted to consensus seats ->
   roster floor [6,12]. `--apply` executes the mechanical pre-registered rules with backups;
   pod seats never auto-assigned. **First run cut V21GW8P** (-$1.35/close, n=10 post-overhaul
   — the lifetime-COPYABLE wallet stopped being copyable under the new system). TAXED watch:
   HmP3 (-0.43/49), 45Sn (-0.10/167). k2 pod down to HmP3 until a recruit earns the seat.
2. **+4 daily-positive harvest keepers** (`1d2529d`): watchlist 6->10->9 (after the cycle cut).
   Wide-harvest (runner-recurrence + elite-cluster funnels) -> diversity scorer on a widened
   4-provider RPC pool (mainnet-beta, leorpc, publicnode, drpc — per-provider limits = ~4x
   headroom): 9 SELECTORS, 4 cleared the daily-positive bar: **1eveYYxZ (100% rWR, +5.39
   SOL), 2qnHs8fZ (25 tok, 100%), EGwERj1 (22 tok, 100%), HcLMmNx9 (42 tok, 75%)**.
   Heavy-history front-runners (AgmLJBMD = the documented 115-win reference, Em8J3gBW,
   gasTzr94) keep RPC-timing-out — queued for slow-paced re-score.
3. **Fleet token cap built then REVERTED same hour** (`35abf19`->`c0ca2eb`): AxiS — "fleet
   buying bad tokens is a sign of weakness across our bots, not a fleet issue." The cap also
   corrupts the selection instrument (bot #13's record depends on 12 neighbors). Memory
   strengthened (feedback_fleet_is_selection_instrument) — do NOT rebuild.
4. **Deploy 502s killed + daily-loss floor ENFORCED + attention flags** (`2a4020f`):
   - Web server binds BEFORE the ~2.5min fleet load; /api/stats answers {warming:true}.
   - `RISK_FLOOR_MODE=enforce` set on Railway: shadow data showed post-halt buys ran 51% WR
     / -$0.70/tr vs 60% baseline (net -$48 avoidable) — per-bot daily_loss_limit_usd +
     max_token_buys_per_day now BLOCK. A go-live prerequisite now aging in production.
   - `ds_boosts_active / ds_dex_id / ds_labels / ds_has_socials` stamped into entry_meta
     (the bad-day boosted-runner signal becomes minable).
5. **Funnel decomposition stamps** (`1a8ef02`): every unconverted smart_follow fire now logs
   {type:fire_unconverted, reason} — 6 named block points (low_score/already_holding/
   daily_limit/security_*/chart_dip_check/chase_guard/stale_score). Background: post-overhaul
   funnel was 14% converted / 16% flush-blocked / 6% already-open / 63% unnamed — now
   self-naming forward. Early pattern: security_BLOCK on convex fires (pre-registered:
   revisit only if >70% of convex fires at n>=30, judged vs blocked fires' universe outcomes).
6. **Permanent latency+conviction instrumentation** (`4b579ea`): every smart_follow position
   carries follow_fire_ts/price/tier/conviction_mult (buy size vs the wallet's 40-buy rolling
   median) — chase tax + latency now daily-auditable on closes. Also fixed sync --full to
   UPGRADE trimmed cache records (628 upgraded on first run) -> scorecard dial section went
   live: **first graded forecast = HIT (06-11 dial 0.5 vs realized -$11), 1/1**.
7. **TP1 fraction 0.65 -> 0.35** (`4c95721`): exit replay on 120 post-gate closes (per-trade
   peaks): entries already produce the convex shape (median peak +7.6%, p90 +18%); the 0.65
   dump capped it. Replayed +2.35%/tr -> +3.37 (+43%). Trail conclusions NOT trusted from
   replay (can't model continuation) — trail stays 4pp. Env SMART_FOLLOW_TP1_FRACTION.
8. **Daily-positive wallet finder** (`c9c9923`, `scripts/find_daily_positive_wallets.py`):
   the proven funnel formalized — recorder runners -> mid-tier buyers ($30-3k, skip the
   earliest-10% MM zone) -> >=3-runner recurrence -> SELECTOR class -> net-positive realized.
   First pass independently re-found AgmLJBMD (validation the method works).
9. **THE BLEED-CUT** (`1ec9c1c`) — the day's turning point. Post-overhaul per-wallet fire
   attribution named the bleed: **2tYcXQCf -$48.50/32tok + D1aDZ -$30.15/38tok = -$78 of the
   -$84 pool drawdown**. Cut both + dormant Abk9Efh (2+ days silent = rotated) + GGduK5 (0%
   own-WR). Watchlist 10->6; solo pod seat Abk9 -> 2x99WSHD; default size $100 -> **$50**
   (env SMART_FOLLOW_SIZE_USD). Vindicating detail: cut 2tYcX resurfaced in the harvest with
   GOOD signal hits — its tokens are fine, our copies of it bled. Quality != copyable.
10. **Badday admission lane** (`7b33bc8`, `core/badday_lane.py`): the zero-fires audit found
    the scanner's admission layer discards the family's prey (31 flush + 5 momo qualifying
    microcaps overnight, ZERO reached evaluation — mcap floor 500k, $200k/day vol floor, and
    9 regime rejects incl trend_reversal/red_h24/no_dip/bs_h6/seller gates). Lane mirrors the
    young/low-mcap probe pattern: ADMISSION (50-500k, age>=6h, liq>=15k, pc_h1<=-20 or >=+30;
    `badday_admit` cycle counter) + CONTAINMENT (sub-floor tokens tradeable ONLY by
    microcap-mandate bots or user-watchlist — **controls/production universes unchanged**).
    Env BADDAY_LANE. Memory saved: `feedback_pipeline_trace_before_build` — trace the FULL
    upstream pipeline at design time (AxiS: "build it that way from scratch").

Overnight (pre-bleed-cut, from the 06-10 evening): zombie-resurrection guard (`522206a` —
manual sells survive deploy overlap; MINER/ZOOMER were sold twice and resurrected twice),
trail peak-restore fix (`c8c0a51`), the CONVEX 4th tier (`3a94c85` — $25 probes, K=1 capped,
no flush gate, TP1 0.10, their -15 cut; **first fire today**, latent k2/solo $200-sizing bug
fixed), max-chase guard + copyability board (`dd5a69e`), smart-wallet own capital pool
(`5b3ae8c` — $1000/$1000 floor, epoch 06-11 04:19, virtual hourly sweeps; **$30 banked before
the overnight giveback** = banks-the-peak working; /api/follow-capital).

## LATE-SESSION ADDENDUM (~15:00-16:15 UTC) — the "why can they and we can't" arc

AxiS: "why are these smart wallets able to detect these great buys, but we arent?"
The answer (from the 500-rtrip decode + universe data): (1) they trade the ATTENTION layer
before price — our features were all price-derived echoes of their buys; (2) they sit
upstream in the cascade (we detect their footprints; they are the feet); (3) they watch
each other (a web; smart_follow buys us a node); (4) **the dirty secret: their WR is 51%**
— they are not detectors, they are HARVESTERS (tiny probes, minute cuts, breadth, uncapped
+107% p90 tails). Nobody picks winners reliably in this market; they built a machine where
picking barely matters. Two builds followed:

A. **THE CONVEX WING** (`57eec7b`): our proven lottery-segment entries + the elite payoff
   curve, judged head-to-head vs their grind parents:
   - young_probe_stair_convex / young_probe_baseflow_convex / badday_flush_convex
   - $25 probes, TP1 +5 sells 10%, TP2 +25 sells 20%, 70% rides the 4pp trail,
     -15 hard cut (their median loser), -9 fast bail.
   - PRE-REG: convex must beat parent $/tr at n>=25 closes each, or retire.
   - Endgame barbell: mid-cap grind pond (floor) + convex lottery wing (tails)
     + smart-follow (copy the masters). Candidate set 19 + smart_follow tiers.

B. **ATTENTION FEED** (`c8e4351`, `core/attention_feed.py`): the social/attention layer
   tapped for FREE — DexScreener token-boosts/latest (boosts being PURCHASED right now),
   token-boosts/top, token-profiles/latest (marketing pushes + `cto` flag). All keyless;
   3 tiny payloads / 5min. Validation: Gaejook+Jotchua (this week's missed bad-day runners)
   sit on the boost leaderboard RIGHT NOW. The feed keeps first-seen history across
   restarts -> boost RECENCY + VELOCITY (the derivative is the signal). Every entry stamps
   attn_boost_total/latest/velocity, attn_first_seen_min, attn_on_top_board,
   attn_profile_fresh, attn_cto, attn_links_n. `/api/attention` = velocity board.
   SHADOW-FIRST pre-reg: no gate uses attn_* until boosted-vs-not validates on our own
   outcomes at n>=200 stamped entries. Env ATTENTION_FEED=on|off.

Also: convex-wing deploy verified paper; fleet-cap revert stands (see standing rules).

## EVENING ADDENDUM 2 (~16:15-20:15 UTC) — guards, free firehoses, and three deploy-amnesia bugs

14 more commits (HEAD `5a5ecba`, suite **692**). The arc: AxiS's observations drove every fix.

### Smart wallet guard stack (all ENFORCE, all logged per-fire)
- **DISTRIBUTION GUARD** (`afeefff`): roster sell on token within 10min -> veto fire (both eyes
  record sells: RPC sweep + PumpPortal). Env SMART_FOLLOW_DIST_GUARD(_SEC). FIRST-SHIFT REPLAY
  (~90min): 6 vetoes = 3 dodges (MASCOTS k3 would-have-fired into **-43%**), 2 flats, 1 missed
  winner (ZOOMER +10.8) -> net +$22.75 raw / ~+$10-13 ladder-modeled. Refinement theory: sub-minute
  scalper sells carry little info (ANTH flat) — weight by seller hold time at n>=20 vetoes.
- **WON-TODAY VETO + 1h cooldown** (`fead8fe`->`e6bd5e8`): "8 losses on 2 tokens" autopsy —
  elonbucks was a WINNER (+$24 net; red rows = remainder slices), Deniz was the flaw: morning
  episode won+closed, 17:32 re-fire bought the exhausted run (-$40). Gap analysis n=49: after-WIN
  re-fires negative in EVERY gap bucket (-$58 <24h, still neg >24h); after-LOSS at 6h+ = **+$78**
  (re-accumulation). AxiS pushed back on my blanket 24h ("memecoins change a lot") -> replaced
  with outcome-conditioned: 1h anti-spam cooldown (persisted follow_fired.json) + veto ONLY
  tokens already won today (FollowCapital.token_pnl_today, persisted, day-rolls). After-loss
  re-buys flow again. won_today_veto records in follow log.
- Stack now: flush gate, chase guard, dist guard, won-today veto, elite-exit, conviction stamps,
  fractional copy-tax board, own capital pool.

### THREE deploy-amnesia bugs (the named pattern: in-memory state dies at cutover; 10+ deploys/day)
1. Fire cooldown wiped -> persisted (follow_fired.json).
2. FollowCapital exposure wiped -> deployed read $0 while $67 remainders rode; re-register
   restored positions after pool wiring in main (`78d4209`).
3. (Yesterday: trail peak amnesia.) Anything in-memory MUST persist or re-derive on boot.

### Wallet pipeline at full speed
- **ALCHEMY KEY live** (AxiS signed up; `02d2ed6` core/rpc_pool.py: env ALCHEMY_API_KEY or
  gitignored alchemy_key.txt; Railway var set; Alchemy-first + 4 public fallbacks in scorer/
  cycle/strategy). Heavy-wallet mystery SOLVED: AgmLJBMD/Em8J3gBW/gasTzr94 = **UNFOLLOWABLE**
  (Jupiter/proxy custody — owner-based parsing sees zero swaps; our sweep COULD NEVER see their
  buys either). Scorer verdict added (`672ea3d`). Thread closed permanently.
- **Wide harvest @ Alchemy speed**: 124 candidates / 3 funnels (runners 351, elite-cluster 292,
  roster 8543 rows — funnel C format bug fixed). **7 FRESH bench candidates** in
  _wide_harvest_results.json: AxQRySJb (83% rWR, 59 ndist, 2-funnel), CuTgJYbT (80%/10rt),
  7rbxsXch (79%/14rt), 5Er9zJ1V (69%/16rt), 3fuga4 (60%, 2-funnel), Ar2Y6o1Q, 2Lsypd.
  Per protocol: BENCHED, need time-separated re-measure at morning ritual before seating.
- **TOMBSTONE LEDGER** (`bc7cc57` config/follow_cuts.json): harvest resurfaced 2tYcXQCf (cut
  same morning) at 78% rWR -> quality != copyable. Cuts recorded+excluded from recruits();
  --apply auto-records. Cycle reruns: udH4u cut on FRACTIONAL verdict (-$2.22/close n=15);
  recruits 1eveYYxZ/HcLMmNx9 SPARED (frac n=4-5 under bar — multi-count artifact). Fractional
  attribution now permanent in wallet_cycle (`375b4db`). Roster 8/12 + bench 8.
- **PumpPortal firehose** (`f734ebe` core/pumpportal_feed.py): free keyless WS — watchlist
  account trades PARSED in realtime (0 RPC; signature-dedupe vs sweep via _seen),
  migrations->migrations.jsonl, launch registry. /api/pumpportal. Env PUMPPORTAL_FEED.
- GMGN probed: Cloudflare 403, dead keylessly.

### Fleet: the silence audit (AxiS: "young probe hasnt fired in days")
11/46 enabled bots had ZERO buys since the 06-09 entry-stack enforcement. ROOT CAUSE: the stack
(age>=24h, mcap>=500k) is the structural OPPOSITE of the young pond; family never exempted.
- 6 young bots stack-exempted (`7b1947d`), 2 sub-500k mcapgate bots exempted + low_mcap_probe
  mandate (`36cc420`). badday family: exempt but lane admissions episodic — tripwire stands.
- Fleet ~280 buys/day vs 724 pre-stack = ~60% intended selectivity + 40% this bug.
- NEW RULE: when a gate ships, audit every existing bot against it (pipeline-trace BACKWARDS).
  Morning ritual gains a silence check (any enabled bot 0 buys/48h = flag).

### Infra
- **20:00 SERVER-WIDE STALL solved** (`5a5ecba`): io.dexscreener rate-limited us -> each fetch
  hung a thread 10s -> scanner's DS calls saturated the GLOBAL to_thread pool (~32) -> dashboard
  serialization starved, ALL endpoints 000, buys 25s apart. Fix: private 4-thread executor for
  DS, timeouts 10->5s, circuit breaker (5 fails -> 5min open -> GT fallback). Verified healthy.
- Dashboard Open Positions card = SMART WALLET ONLY (`42c6092`, AxiS request) — fleet probes
  live in the Bots tab. Sizes decoded: odd numbers = remainders after banked TP slices.
- Cycle recruits() recognizes harvest keeper format (`0393fa7`).

### State at 20:12 UTC
Pool: -$41.71 hot + $30 banked = -$11.71 effective (morning low -$84). Day: 121 closes 81W/40L
(67% WR) net -$14.24 — hit rate fine, damage was the 4 oversized losers the new guards target.
4 open: WAR $50 fresh + GABLE/WERLD/Percolator remainders (all green, GABLE +27%@7h).

### MORNING RITUAL (updated)
sync --full -> badday_scorecard -> goal_tracker --cache -> wallet_cycle (--apply mechanical)
-> re-score the 7 bench candidates (2nd measure; survivors fill seats) -> silence check
(enabled bots 0 buys/48h) -> dist-guard veto replay (grade blocks) -> convex-vs-parent check.

## EVENING ADDENDUM 3 (~20:15-21:35 UTC) — THE $100/DAY PUSH (AxiS-approved 3 levers + floors)

Goal status at 21:15: live-set **+$34** (12 candidates green, none red), streak 0. AxiS: "what
else can we do to reach $100/day?" -> the gap is MAGNITUDE not edge. Shipped (`bb1aa96`,
`7756040`, suite 692, paper verified):

1. **OFFENSE DIAL UNLOCKED — for the qualified only** (`core/live_set.py` + bot_evaluator):
   P7's 1.5x upsize leaves shadow, applied ONLY to walk-forward LIVE-SET members (bot computes
   the set server-side from the same sources as /api/goal; 30min cache; fail-soft empty =
   defense-only). Offense lifts via max() — the defense floor NEVER weakens. Env
   REGIME_DIAL_OFFENSE=live_set(default)|off. Observable: `tier=...+dial1.5` on sized buys.
   Rationale: size-is-the-bleed was size on UNqualified bots; this is size on bots that earned
   it, regime-gated by the dial (forecast record 1/1).
2. **GOAL METER @$100 NORMALIZED** (goal_tracker): new column = live-set P&L at uniform $100
   positions ("would going live at real size have made the goal"); STREAK now counts the
   normalized line. Today +34/+34 (young earners already $100-sized; diverges when $50 ponds
   carry the set).
3. **YOUNG POND THROUGHPUT**: light/candidate closed the SAME 2 tokens today (+$15.81 each —
   the day's best live-set earners, pure duplication). All 4 young probes now share the
   `young_pond` exclusion pool (siblings take DISTINCT tokens) + max_concurrent 3->5 (proven
   pair) / 3->4 (stair/baseflow). Same entries, ~2x distinct at-bats.
4. **DAILY LOSS FLOORS ON ALL 22 GOAL CANDIDATES** (`7756040`): audit found ZERO candidates
   carried daily_loss_limit_usd despite RISK_FLOOR_MODE=enforce live (momentum_shadow bled
   -$34 unchecked today). Floor = max(15, 0.6x base size) ≈ 4 stops' worth — variance room,
   cascade-day halt.

**Deliberate non-actions**: do NOT pool the pond clones (same-token duplication IS the A/B
experiment until a winner is promoted at n>=50); do NOT add more bots/strategies (bottleneck =
verdict speed, not idea count).

**The math**: today's +$34 = ~2 effective young shots, unleveraged, smart wallet unguarded most
of the day. Tomorrow runs ~4 distinct young shots x 1.5x dial x guarded follow engine x (maybe)
badday conversions. Day P&L state at close of session: live-set +$34, smart-wallet pool -$41.73
hot + $30 banked = -$11.73 effective, 67% WR (81W/40L).

**MORNING RITUAL (final form)**: sync --full -> badday_scorecard -> goal_tracker --cache (note
the @$100 column + streak) -> wallet_cycle --apply -> re-score the 7 bench candidates (2nd
measure; survivors fill seats 9-12) -> silence check (enabled bots 0 buys/48h) -> dist-guard
veto replay -> convex-vs-parent + dial-offense first grades -> badday lane tripwire (~13:30 UTC,
1m-confirmation layer is the suspect if still dry).

## OVERNIGHT MISSION (06-12 ~04:30-08:00 UTC, AxiS-mandated "work on all of these") 

**Context**: pool gave back its afternoon gains overnight (-$25 effective at 04:09; ~115
fires/6h from night-active recruits). AxiS: "if they make that many bad buys, they arent good
wallets" -> HcLMmNx9 cut TOXIC same hour (frac -$1.14/close n=19.8); AxQRySJb promotion VETOED
by measure-2 (83%->43% in 9h); POOL DAILY FLOOR shipped (SMART_FOLLOW_DAILY_FLOOR_USD=40 —
fires halt at -$40/day realized; /api/follow-capital shows day_realized).

**Mission results (lanes A-E):**
A. **FIRE-QUALITY MINE** (106 converted fires, net -$362): liq<$20k fires = toxic in BOTH
   held-out halves (-$11.64 / -$7.05 per fire; 35 fires ≈ $290 of bleed) -> **THIN-BOOK GATE
   SHIPPED** (SMART_FOLLOW_MIN_LIQ_USD=20000, b7dd099). Also: K>=4 consensus 64% WR vs
   k3-exact 33%; convex tier +$2.47/fire vs k3 -$4.04 (observations, not shipped).
B. **EXIT-HORIZON MINE** (2000 elite exits): roster X-ray — 4jkL4dN ROTATED to 1-min scalper
   (610 exits, med ret -1.6%; flag for board), EGwERj1 healthiest (+9.1% med own-ret), HmP3
   own-trades ugly (-7.25% med). OUR disposition: winners close 13min median, losers 57min —
   but GRACE A/B says patience EARNS: treatment -$6.33/exit vs control -$8.37 (n=32/37,
   ~$65 saved) -> KEEP grace, judge final at n>=50/arm. Don't tighten loser exits yet.
C. **BADDAY AUTOPSY**: lane WORKS (TRILLION admitted sub-floor, containment blocking controls
   correctly). Bottleneck = the family's own 1m-confirmation layer. Tripwire decision 13:30 UTC.
D. **WALLET HUNT**: dmuX-axis funnel (co-buyers of gated-bench winning thesis mints) -> 2 NEW
   COPYABLE-GRADE: AKprbkX7 (+16.5% taxed, 75% WR), 76WoCZAG (70min holds). gasTzr94
   auto-rejected unfollowable (pipeline consistency). **Gated bench = 10 names**
   (_gated_bench.json; dmuX 247% taxed tops). AxQRySJb oscillates between windows = stays benched.
E. **GUARD GRADES**: dist-guard n=30 vetoes -> **+$40.93 exit-bounded** (~$4/veto rent) ✓.
   Won-today: 0 vetoes yet (trigger is rare by design). Attention-stamp EARLY peek (n=29):
   boosted tokens median -$6.05/tok vs plain +$2.99 — boost reads as an AVOID candidate
   (SPCX -$1315 and FCM -$608 death-clusters were both PAID-BOOSTED); shadow continues to n>=200.

**Also shipped:**
- **TRIGGER-STATE ENFORCEMENT built DORMANT** (b836c77): scorecard sec.5 crossed pre-reg
  (n>=50+lift) on 4 gates (calm_at_support 86/57, informed_cluster 66/55, support_with_60s_flow
  80/63, whale_conviction 74/61). ONE-VAR activation awaiting AxiS:
  TRIGGER_STATE_ENFORCE=calm_at_support,informed_cluster,support_with_60s_flow,whale_conviction
  (deep_1h_dip EXCLUDED — its gate reads backwards forward, block-cohort 88% WR; re-mine).
- fire_unconverted records now carry price/liq (26b28ac) — security_BLOCK cohort (149/150 of
  unconverted!) becomes replayable; only its 8% quick-death rate was measurable retroactively.
- **DIAL NOW 2/2** (06-11 forecast 0.5, realized -$146 -> HIT).
- 06-11 FINAL: live-set +$15 (@$100 +$15); 06-12 young qualified 8-bot live set.
- PRE-SEAT COPYABILITY GATE (9cbf5d8 scripts/copyability_gate.py): see memory
  reference_wallet_spec_copyability_2026_06_12 — judge the copyable SUBSET; validation set
  caught a hallucinated address (read configs, never reconstruct).

**Roster 7** (post HcLMmNx9 cut) | bench 10 gated | tombstones 7.

## TIME-OF-DAY DEEP ANALYSIS (06-12, AxiS-requested) — 6th time-axis falsification + one nuance

3 layers: (1) smart_follow 486 closes — apparent hour patterns are COMPOSITION artifacts
(full-window: overnight BEST 60% WR; post-overhaul-only: overnight WORST -$2.25/close — same
hours, opposite verdicts; the variable was the toxic-recruit era, not the clock; day-by-day
signs flip every block). (2) Fleet candidates 754 closes/8d: FLAT all blocks (±$0.46/close,
WR 54-56%, no block >4/8 days positive). (3) UNIVERSE 5000 dip events: the ONE real effect —
overnight has HALF the opportunities (15% share) but HIGHEST quality (med peak +9.5%, won10
49% vs 42% all other blocks). VERDICT: no time-based rules (would be backwards across
windows); don't throttle overnight (fewer-but-better setups); the copy dial (record-keyed)
is the correct bad-regime detector — catches grinds WHENEVER they occur. The 24/7 doctrine
survives with its strongest evidence yet.

## MORNING DECISIONS EXECUTED (06-12 ~05:40-06:30 UTC, AxiS)
- TRIGGER_STATE_ENFORCE set (4 gates live fleet-wide; controls exempt).
- BADDAY TRIPWIRE: lane tokens exempt from the 1m layer (no_1m_reversal/m1_top_tick/
  m1_false_bounce; 0e11b83) — 468 envelope events/24h @ 51% won10 were all filtered.
- GTA6 dead-cat lesson -> flush-depth WINDOW [-30,-10] (1507c99): deep-collapse fires
  (pc_h1 -43/-47) rode a dead-volume crash to -38% (-$55); mine had flagged the cohort.
- Pool floor: halted fires at -$79 day (working as designed) -> then AxiS: paper = learning
  -> SMART_FOLLOW_DAILY_FLOOR_USD=off (code stays; re-arm at go-live).
- COPY-REGIME DIAL shipped shadow (3ad8ceb): rolling-20 expectancy of the follow book
  (bad < -$1/close @ n>=12; overnight grind would have flagged by 01:30). Stamped per-fire,
  /api/follow-capital readout, scorecard grades -> enforcement on its record.
- SEATED 3 double-vetted thesis holders (c71f373): dmuXAmcX (210min holds, +257% taxed,
  consistent), 7Gi3RNdV, AKprbkX7. Roster 10. Benched: 7rbxsXch (margin halved), 3fuga4
  (63% scalp), AxQRySJb (cross-instrument volatility). 1eve/2qnHs/4jkL hit n>=10 TODAY.

## MORNING SESSION 2 (06-12 ~06:30-08:05 UTC) — capture build live, flush validates, full-system bug scan

### THE FLUSH STRATEGY VALIDATES (the day's headline)
badday_flush: **11-for-11, +$53.39** in its first 90min (SPCX +18.5%, Vox, TRILLION, Pnut x2,
Gus — +6/+12 ladder + trail). flush_convex: +$9.53 (11W/2L; caught Vox's full +42% run with the
patient curve — the head-to-head running live at $25). badday_momo: **-$38.49 (5W/7L)** — the
momo side is the WEAK half (never_runner kills + Vox double-hard-stop 2min apart = the
register_stop_loss NO-OP biting live). Family net +$24.43.

**FLUSH EDGE MAP** (684 universe flush events, the n>=30 review's refinement list):
- DEPTH MONOTONIC-BETTER: -20/-30: 42% won10 -> -60/-95: **67% won10 +18.5% med peak**
  (OPPOSITE sign vs smart_follow deep-fire toxicity — different pond physics: capitulation
  bounce on aged+liquid tokens vs chase-taxed thin-book follows)
- Round-trip risk RISES with depth (34% deepest) -> the fast ladder is STRUCTURALLY right
  (harvest the bounce, leave before the give-back)
- Age pond = 6-96h (52-54%); >96h decays to 32% -> n>=30 refinement: envelope age ceiling
- Rug screens = insurance not edge (50% vs 48%); <6h flushes bounce best (64%) = rug country
  now covered by the young band probes instead
- Mechanism: the flush bot is our COPYABLE version of elite knife-catching — minutes-for-the-
  bounce instead of seconds-for-the-tick

### YOUNG CAPTURE BUILD LIVE (657a7db + fixes)
3 band probes fishing the adjacent water (thinliq <2h/25-40k = the 69%-won10 prize band; mid
2-6h; late 6-24h), proven family self-gated to its exact water, lane envs widened (24h/25k/
100k — LOAD-BEARING: code default is 2h). First fires 06:40-06:58 — and they EXPOSED the
**band-collapse leak**: all three age-disjoint bands bought CPX because entry_gate's
entry_age_hours doesn't exist in raw_meta (it's lifecycle_age_hours) -> every age bound failed
OPEN. Fixed via alias resolution in the gate evaluator (8614a77) + band-disjointness regression
test. Pre-reg per band: n>=30 closes, >=+$1.50/tr promotes, negative retires.

### FULL-SYSTEM BUG SCAN (AxiS-ordered; 4 parallel auditors + the FTP lead) — 13 bugs fixed
1. **FTP RESURRECTION PUMP** (2ec4be3): one buy -> TEN sells (~-$27); deploy-overlap dupes +
   stale-book resurrection. Tombstones broadened to ALL full-close reasons (TP partials exempt).
2. Dip-branch re-entrancy guard (0c8ba41): poll could fire a 2nd close while realtime stop
   awaited Jupiter (the elonbucks slow-bleed-x4 signature).
3. Loss-cooldown UTC-midnight hole: now persisted token_lost_at timestamps (deploy-proof).
4-6. **All 3 daily circuit breakers reset at every deploy**: scalp $400 stop, RiskManager
   daily_pnl, per-bot per-token cap ('persist before enforce' — live_probe enforced!). All
   persisted (011fb62).
7-8. mcapgate dead-band (MY 36cc420 regression: low_mcap band = empty set; env LOW_MCAP_PROBE=0,
   flags keep lane mandate) + young vol_h1_min trap (sub-1h tokens report $0 h1 -> nulled on 7
   sub-2h bots) (1e57b1d).
9-11. 20:00-incident class remnants: PM 1S-bars + scanner 1S retry-ladder routed onto the DS
   private executor + breaker (shared_client()/run_ds_fetch()); throttle sleeps moved OUTSIDE
   client locks (DS+GT).
12. goodpond mcap_min config-truth; 13. band-collapse alias fix (above).
**QUEUED FOR AXIS (behavioral)**: register_stop_loss = deliberate NO-OP since 05-17 (momo's
Vox double-stop is live evidence); process_external_signal skips all re-entry cooldowns;
dead _dip_stop_streak (loaded, never written). Clean: all floors present, no field mismatches
in consumers, prior fixes held.

### ALSO THIS SESSION
- GTA6 dead-cat lesson -> flush-depth window already in handoff above; GTA6 total ~-$117 was
  partly RESURRECTION pumping (see #1), partly after-loss re-fires -> LOSS-COOLDOWN shipped
  (e01ff67: 6h after a losing token, the +$78/-$85 gap seam).
- Goal meter -$25 early-day audit: REAL (phantoms only -$0.55 in the live set; the big phantom
  pumps were smart_follow-side, not in the meter). Young family small red start.
- Smart wallet post-gate record: 22 toxic fires blocked in the first 40min (20 thin-book),
  ZERO post-gate losses; old-book zombies (GTA6/FTP) self-closed; 4 green riders incl
  Nong Yan +113%.
- Exit-horizon stamps live (fast/mid/slow per fire); fire-quality map refreshed; copy dial
  warming (n grows with each close).

### MORNING RITUAL ADDENDA
+ Grade flush vs momo SEPARATELY at family n>=30 (momo may retire while flush promotes).
+ Band probes: verify post-alias-fix fires respect band disjointness (no same-token across bands).
+ Decide: stop-cooldown re-enable (scanner + external paths) — Vox double-stop = the cost of no.

## MIDDAY SESSION (06-12 ~13:00-14:25 UTC) — THE WALLET-INTELLIGENCE DAY

**Arc**: "entire production performed horribly overnight" -> red-tape autopsy -> the
green-in-red hunt -> the Dw5Vykxu decode -> three structural builds + a standing
intelligence process. AxiS: "we seem to get our best info lately from other wallets."

### THE RED MORNING, DECOMPOSED (live-set -$63 final / all-cand -$319)
1. TAPE TURNED (controls bled; the dial's rolling-20 leg caught it -> defense 0.5 engaged,
   3rd live test). 2. badday_momo -$82 — FLOOR-LAG BUG: deploy-overlap sells leak P&L out of
   the floor's counter (ledger crossed -$60 at 09:43; capital read it at ~13:00 = 3h of
   post-floor buying). FIXED (1799230): per-bot daily pnl RE-DERIVES from the trades ledger
   at boot (ledger = authoritative, the tombstone principle). 3. mcapgate pair -$47/5h.
   RETIREMENTS (AxiS): badday_momo (failed pre-reg -$2.34/tr@n=35; flush carries the family
   alone), mcapgate pair (question answered: sub-500k + midcap gates = toxic, 2nd confirm),
   + earlier pond_ugly_mtf/rsi ("cant prove themselves" — 1 fire/48h can't reach n>=50).
   smart_follow's -$152 day = 86% GTA6/FTP resurrection-pump corpse (bug killed at 07:15,
   booked to the ledger); ex-zombies the gated engine ran ~flat on the red tape.

### FORECAST CALIBRATION (AxiS: "you predict great outcomes and the opposite happens")
Owned: winner's-curse mining, peak-proxy->dollars category error, day-1 plumbing tuition,
heartbeat narration. STANDING RULE (memory feedback_forecast_calibration): dollars only from
realized $/close @ n>=30 + empirical haircut; ceilings rank ponds, never forecast; new
launches = 48h burn-in label; forecasts carry bar/date/haircut or nothing.

### THE GREEN-IN-RED HUNT -> Dw5Vykxu DECODE -> 3 BUILDS
Hunt v1: ~empty. Hunt v2 (3 nets: wider mesh / BOTH-red-days / flush-co-buyers): red-tape
skill is RARE + hides behind proxy custody (5/26 unfollowable incl AgmLJBMD x9 runners);
2 COPYABLE-GRADE found. **Dw5Vykxu decoded** (118 tokens/5h): fixed 0.52-SOL spray, NO price
stop (held -27%), HARD 240min TIME stop (losers exit at 240:00 on the dot), winners sold on
strength +20..+124%. ANTH: our -15% stop at 12:59 was ITS entry at 13:06 -> +124% at 13:42.
**Our price-stops are its entry liquidity; red-tape chop executes price-stops at local
bottoms (the old 74%-of-stops-recover finding was this).**
-> BUILD 1: **PP_LAUNCH FIREHOSE LANE** (f537126): 68% of Dw5's tokens never reached our
   scanner — every feed is popularity-ranked = we live on the POST-trending tape while the
   PumpPortal firehose tracked 716 births/day unused. launch_candidates() rides the existing
   DS batch enrichment (cap 25/cycle, 15min LP floor), source=pp_launch, young-lane
   admission + containment unchanged. (Registry is in-memory: ~15min warmup per deploy.)
-> BUILD 2: **TIMEBOX_PROBE** (5ed6d0c): new primitive BotConfig.time_stop_minutes ->
   TIME_STOP ExitDecision. $75 fixed, -60 rug guard ONLY, 240min box, sell-ALL +20%,
   near-indiscriminate entries, dial-exempt, $90 floor, slow-bleed disarmed in-box.
-> BUILD 3: **wallet_decode.py** (f1c0f36): the standing instrument — trade map, sizing
   style, hold distros, TIME-BOX signature detection, W/L asymmetry, scanner/book overlap.
   RITUAL: decode every pre-seat candidate + 1 roster wallet/day (drift) + green-in-red
   windows after bad days. Memory: feedback_wallet_decode_intel.

### ROSTER X-RAY (all 10 decoded) -> CUTS + DOCTRINE
Spine confirmed: dmuX (4.92 SOL conviction, 60min holds, +328/-72 medians), 7Gi3
(+154%/-2.2% medians — the tightest loss control seen), AKprbkX7 (43min, 81%), 2x99
(91% WR anchor). DRIFT CONVICTIONS -> CUT (AxiS): 4jkL = dust-probe lottery sniper
(0.02 SOL, 0-min holds), 45Sn = pure 0-min scalper. Roster 10 -> 8, tombstoned with decode
evidence. UNIVERSAL findings: (a) ALL 10 wallets are conviction sizers — the fleet bets
flat everywhere; (b) the discovery gap is universal (scanner sees 13-50% of EVERY wallet's
pond — baseline to re-measure post-firehose); (c) even 91%-WR wallets carry -37..-45% loss
tails (validates our floors/guards on price-stopped bots).

### DOCTRINE A/Bs SHIPPED (e35ee4a; all single-variable vs proven parents, n>=30, 48h burn-in)
- badday_flush_conviction + young_probe_conviction: trigger-count sizing (1+0.5*(n-1), cap
  2x) — the unused conviction_sizing_mode machinery, finally tested.
- young_probe_surgical: the 7Gi3 geometry — fast-fail -4 any volume, +25% banks 10% arming
  an 8pp uncapped trail, -15 gap backstop.
Also: young_probe_stair freed from the young_pond exclusion pool (5th silencer class:
POOL STARVATION — zero lifetime fires; identical gates to its firing convex twin; siblings
always held the scarce tokens first). Fleet 48 enabled. Suite 698.

### STATE 14:25 UTC: roster 8 | bench: 2 green-in-red names + prior | fleet 48 | races
running: 3 doctrine A/Bs + timebox + 3 band probes + convex wing + flush(n->30 on dial-bad)
| dial DEFENSE 0.5 | floors ledger-true | pp_launch lane warming.

## AFTERNOON ADDENDUM (06-12 ~14:25-15:05 UTC) — bench decode sweep + the 5th geometry

### BENCH DECODE SWEEP (8 wallets) — archetype catalogue grows
- **4TeTtW1G = ARCHETYPE #5: the OVERNIGHT SWING** — 615min median holds (p75 13h!),
  conviction whale (4.76 SOL), 75% WR, and 86% SCANNER-VISIBLE universe (swings the
  ESTABLISHED tape, not the longtail). Green through both red windows.
- **Micro-time-box = standard pro practice**: 7rbxsXch kills 100% of losers @ ~5min,
  CuTgJYbT 80% @ 8min, 76WoCZAG 67% @ 12min — Dw5's 240min box was the long end of a
  recurring pattern. SHORT-box (~10min) geometry = thrice-validated, QUEUED (build when a
  current race clears a slot).
- 3fuga4 = first true FIXED-size sprayer decoded (94% WR, +20.6 med win, pure breadth).
- AxQRySJb enigma deepens: recent window 94% WR / +185% med win / best +612 — yet fails
  other instruments in other windows; instability IS the profile; stays benched.
- 8DRJdA5P = clean thesis-holder (64min, 81%, ±10 asymmetry) -> top seat candidate with
  4TeTtW1G for measure-2 at the ritual.

### PROBE_SWING SHIPPED (ba65470; fleet 49) — the archetype that re-tests our own history
The midcap stack pond graded 16% won10 / -EV at our FAST exits and was written off as dead
water — but 4TeTtW1G earns 75% WR swinging that EXACT visible tape at 10-13h holds.
probe_swing = the validated stack entries UNCHANGED (entry_stack NOT exempt — that's the
experiment) + only the timescale transplanted: 13h time-box (the new primitive), NO price
exits except -45 catastrophe, +15 sells 75% / +30 the rest, slow-bleed/stall/bails/never-
runner all disarmed (each would execute a 10h hold). PRE-REG n>=30, >=+$1.50/tr; the
SLOWEST race on the board (13h holds x 4 slots) — patience is part of the experiment.
If it wins: months of 'entry non-predictive' conclusions get a TIMESCALE asterisk.
If it loses: the pond's obituary completes and the archetype retests elsewhere.

### SIX GEOMETRIES NOW RACING (the decoded catalogue, each vs a proven baseline)
fast ladder (flush) | convex rides (wing) | time-box 240m (timebox_probe) | surgical
-4/uncapped (young_probe_surgical) | conviction sizing (x2 clones) | overnight swing
(probe_swing). Plus 3 band probes. QUEUED: micro-box ~10min; seat re-measures 8DRJdA5P +
4TeTtW1G; firehose overlap re-baseline in a few days (decode gave per-wallet 10-50%
visibility numbers to measure the pp_launch lane against).

## FILL-FIDELITY VERDICT (trust checkpoint — PASSED)

GT minute-candle method (trade-log endpoint self-throttled): 11/14 of today's fills INSIDE
real candle ranges; sell median gap +0.27% vs mid, buys -0.74%. **Paper fills are honest on
thin books.** Note: tracker SELL records store $-received in exit_price (decode trap) — real
exit price = entry_price * (1+pnl_pct/100). Script: analysis/2026-06/_fill_fidelity.py.

## SMART WALLET — state of the machine (the "huge potential one")

- **Roster (9)**: HmP3Txu, udH4u, 4jkL4dN, 2x99WSHD, 45Sn4KL1 + recruits 1eveYYxZ, 2qnHs8fZ,
  EGwERj1, HcLMmNx9. Pods: k2={HmP3}, solo={2x99WSHD}, convex={2x99, 45Sn, HmP3}.
- **Pool**: $1000/$1000 floor; day 1: realized -$25.83 hot, $30 swept = +$4 effective.
- **Fire path**: WS-latency sweep -> tier resolution (k3/k2/solo/convex, rate-capped) ->
  flush gate (pc_h1<=-10; convex exempt) -> chase guard (1.5%; convex 1.0%) -> $50 probes
  ($25 convex) -> security/chart -> pool capacity -> fill. Every fire stamps tier/conviction/
  fq/state; every non-fill logs a named reason; every position carries its audit trail.
- **Exits**: TP1 +5% sells 35% (convex 10%); peak-trail 4pp (restart-proof); elite-exit
  mirroring; stop-grace A/B (45min, -50 floor); gap guards.
- **Cycle cadence**: `wallet_cycle.py` daily (cuts+promotions), copyability board verdicts,
  finder feeds the bench, on-bot discovery 24/7.

## DAILY RITUAL (run every morning)

```
python scripts/sync_trades_cache.py --full
python scripts/badday_scorecard.py
python scripts/goal_tracker.py --cache _trades_cache.json
python scripts/wallet_cycle.py            # --apply for the mechanical rules
```
Pre-registered judgments: P7 dial KILL <50% acc @ n>=10 (record: 1/1 HIT); badday family
RETIRE <+$2/tr @ n>=30 dial-bad closes or cat>=10%; trigger-state ENFORCE @ n>=50/gate +8pp;
stop-grace arms @ ~20 closes; convex positive @ n>=25 probes; TOXIC wallets cut @ n>=10.

## PENDING / WATCH

- **badday family fires** — lane deployed ~13:30 UTC; `badday_admit` counter + first fires.
  If quiet by tomorrow, check the 1m-confirmation layer (deliberately left ON, the one
  un-bypassed gate; revisit with counter data).
- **Convex tier**: first fire happened; security_BLOCK pattern on its microcaps accumulating
  toward the n>=30 review.
- **Heavy-wallet re-score** (AgmLJBMD/Em8J3gBW/gasTzr94): slow-paced off-peak pass.
- **Goal meter**: live set was 6 bots today (4 ponds + 2 young probes); streak 0; smart_follow
  earns in via trailing-7d like everything else.
- **06-10 addenda still open**: gated-vs-control A/B, pruned-filter re-audit, stop-width
  audit ~06-16 (per-bot records still lack max_drawdown), pond_ugly_mtf 48h tripwire,
  wave-2 ponds + young stair/baseflow first closes, copyability board re-run ~06-12.
- Funnel C of the wide harvest (old-roster slice) has a format bug ('tuple' object) — fix
  before next pass.

## STANDING RULES (additions today in bold)

Paper only; never flip PAPER_MODE. $0 tools (4-provider free RPC pool now standard).
Commit→push→deploy; no camping. Timestamps from `date -u`. Fleet = selection instrument —
**fleet-aggregate caps corrupt it; rebuilt+reverted once, never again**. **Pipeline-trace
before build** (full upstream admission path, with data, at design time). Goal: $100/day
walk-forward live set, streak 5. **Wallet seats are cycled, never owned: dormancy >36h or
copy-tax TOXIC @ n>=10 = cut; daily-positive SELECTOR = seat.**

---

# PRIOR DAY (2026-06-10) — The Bad-Day Playbook Day (compressed)

17 commits: fleet-wide regime system. Five-lens study (own trades / 2,916-event universe
2-fold / DexScreener live tape / 10-elite on-chain / MAITIU case audit): bad days ROTATE the
market to fresh launches + running momentum + sub-500k microcaps; the pond band is the
bad-day dead zone; the middle (pc_h1 -5..+5) dies. Shipped: P7 regime dial (defense 0.5x
ENFORCED, consensus 1.5x shadow; study: -$677 -> -$250 over 9d), badday_flush/badday_momo
(rug-mined screens: age>=6h cuts 79% of catastrophes — rugs are YOUNG+FRANTIC), SOL gates
OFF the 8 bad-day vehicles (gate blocks 64% of bad-day opportunity at better-than-allowed
quality; elites bought 303x through red SOL), badday scorecard (the accountability loop),
walk-forward LIVE-SET goal meter (the -$635 fleet day was live-set +$46), per-trigger
token-state SHADOW (18 gates, 5 archetypes), young_probe_stair/baseflow (young winners are
in confirmed UPTRENDS — mirror of the pond thesis), momentum_shadow gap guards (giveback
floor + fast bail), pond wave-2 (ugly_rsi/sweep_flow/sweep_deep_thin), measurement-integrity
fixes (bot_id in trimmed responses, loud egress-throttle, off-loop serialization), hybrid
cost model (sync_trades_cache.py, ~300KB/sync), smart-wallet full loop SW1-SW5 (elite-exit,
K-tier pods, fire-quality shadow, realtime WS watch, on-bot 24/7 discovery), repo sweep
(root 450->68 files), 14 bots retired (41 active catalog), dashboard goal-first.
