# Session Handoff — Entry-Stack Enforcement Day (2026-06-09)

**Bot URL**: https://gracious-inspiration-production.up.railway.app
**Mode: PAPER throughout** (`live_mode: False` verified after every deploy). No PAPER_MODE flip.
**HEAD**: `defcabd` (filter prune). Deploys this session: `d511b07` → `7928c67` → `0fb03cd` → `184a53e` → `defcabd` (+ runner-tilt `dce63b5` pre-session).

**THE HEADLINE: AxiS's "decisions/entries are the #1 issue" thesis was tested head-to-head against the size thesis and WON decisively. The validated entry stack is now ENFORCED fleet-wide with a control cohort, and 18 counterproductive filters were pruned. The forward gated-vs-control A/B starts today.**

---

## SHIPPED + DEPLOYED this session (all paper, verified)

1. **Fleet-wide ENTRY STACK enforcement** (`184a53e`) — every dip-path bot must pass:
   `shape_90m_drawdown_from_max_pct <= -16` AND `net_flow_60s_usd >= 100` AND `age >= 24h` AND `mcap 500k-10M`. Fail-open per missing field. Momentum path untouched.
   - Env: `ENTRY_STACK_MODE=off|shadow|enforce` (default **enforce**) — downgradeable without deploy.
   - **Control cohort stays UNGATED** (forward counterfactual): `baseline_v1`, `no_filters`, `pool_a_broad_control` (`ENTRY_STACK_CONTROL_BOTS=csv` to override).
   - Expect trade volume to DROP hard (~15% of historical entries pass). That's the design.
2. **Post-stack filter prune** (`defcabd`) — 18 blockable filters stop blocking GATED bots only:
   - 11 HARMFUL within stack-passers (blocked winners, n>=100): `filter_turn`(899), `filter_reviving_lifecycle`, `filter_stale_h1_peak`, `filter_lp_drain`, `filter_bs_m5_weak`, `filter_chasing_bounce`, `filter_1m_steep_fall`, `filter_seller_imbalance`, `filter_knife_catch_peak`, `filter_mtf_strong_downtrend`, `filter_negative_net_flow_5m`.
   - 7 INERT (<=10 blocks in 2,687 passers): `filter_clean_break_p90`, `filter_fake_bounce`, `filter_low_volatility`, `filter_microcap_trap`, `filter_quote_asymmetry`, `filter_sat_eve_midliq`, `filter_solo_decay`.
   - Control cohort + explicit `filters_enforced` bots keep exact old behavior. Verdicts still recorded (shadow record intact). Kill-switch: `ENTRY_STACK_FILTER_PRUNE=off`.
   - 26 filters measured ADDITIVE within the pond — kept (filter_1m, filter_a, bs_m5_low, confirmation_candle, real_dip_5, vp_poc, below_vwap_shadow, fofar, weak_bounce_v2, sweep_too_recent, dip_volume…).
3. **smart_follow watchlist → 7 diverse selectors** (`7928c67` then corrected `0fb03cd`) — cut 3 proven bleeders (8zkgFGV **−77.8 SOL/80 swaps**, DZcyYa9a −16.7, dmuXAmc −9.5) + flat/inactive (FYX5, 2Lsypd); kept Abk9Efh, 2tYcXQCfTtQg(+33.7), V21GW8P, HmP3Txu, D1aDZ, GGduK5, udH4u. K=3 unchanged. Backup: `config/follow_watchlist_12_pre_netsol_cut.bak`.
4. **Runner-tilt exit** (`dce63b5`, just pre-session) — peak-scaled post-TP1 trail (peak>25% → trail 25% of peak) + smart_follow TP1 0.85→0.65. Fix for POKE leaving +150pp on the table.
5. **Wallet-vetting toolchain** (committed): `scripts/score_wallet_diversity.py` (THE picker — SELECTOR vs MM_CHURN), `score_candidate_track_record.py` (net-SOL = loser-filter ONLY), `rank_watchlist.py`, `discover_wallets_dexscreener.py` (+ recurrence log), `validate_new_wallets.py`, `mine_wallet_entries.py`.

---

## KEY FINDINGS (the day's evidence chain)

### 1. Bleed-week decomposition (28d, 18,439 closed trades, cached `_bleed_trades.json` ~500MB)
- **11 BLEED days: fleet −$18,338. Entries passing the stack on those SAME days: −$395.** Entry discipline alone removes ~98% of the bleed.
- **Size thesis CONTRADICTED**: violators had SMALLER median size ($20 vs $30) yet lost 2.5× more per trade (−$1.45 vs −$0.58). It's the picks, not the bets.
- Binding gates: **dip_shallow (6,252 bleed-day violations) + flow_weak (5,827)**; age/mcap barely bind (166/136). Fleet median entry only −12.7% off 90m high.
- Cost: on GREEN days violators contributed +$1,368 vs passers +$799 → gating gives up ~half the green-day upside for not bleeding. Net over window: gated ≈ +$400 vs actual −$15,800.
- May 22–23 (−$5.8k) = mostly orphan-flush accounting era, excluded from gate attribution.

### 2. Post-stack filter audit (within 2,687 stack-passing trades)
- 91 measurable filters → **26 additive / 29 inert / 36 "harmful"** — but only ~52 filters can actually block (canonical `_filters_block` append list); the scariest "harmful" ones (two_pattern, token_ema, trend_score, vwap_h24, dying_volume…) are **verdict-only shadows** that never blocked. Actionable = the 18 pruned.
- WHY harmful-post-stack: deep dips with real flow LOOK "bearish/steep/seller-heavy" at the bottom by definition — fear-filters veto the dip the stack just selected.

### 3. Smart-money wallet vetting (the MM-bot trap)
- **net-SOL track record is a VOLUME metric** — it surfaced 6 "usable" wallets that were all single-token MM/churn bots (1-2 distinct tokens, one bought 89×, top%=100). 8gLQPr9Z was net **+12.9 SOL** and still garbage for following.
- **Diversity/selection scorer separates perfectly**: 7/7 watchlist selectors → SELECTOR (21-59 distinct tokens); 62/62 MM bots → MM_CHURN (validated twice). V21GW8P 75% realized WR, HmP3Txu 67% — restoring them was right.
- **DexScreener early-buyer discovery is structurally an MM-bot finder** (56/56 MM_CHURN) — most-active early buyers in a pool ARE its market makers. Don't re-mine trade logs for follow wallets.
- **Cross-token roster method DOES yield selectors**: top-40 by n_winners → 4 new SELECTORS: `4jkL4dN` (26 tokens, 67% rWR — best), `2x99WSHD` (36, 50%), `45Sn4KL1` + `9fcMp3GN` (40 tokens/22 sells each — SUSPECTED TWINS, identical stats, dup-check before adding). 21/40 RPC-failed — retry pass pending.

---

## OPEN ITEMS for AxiS

1. **Judge the gated-vs-control A/B** after a few days of forward data: gated fleet vs `baseline_v1`/`no_filters`/`pool_a_broad_control`, same days. Decomposition predicts gated ≈ flat-to-positive on bleed days while control bleeds. If control WINS over a fair sample, the gate is overturned — revisit honestly.
2. **Re-audit the pruned filters on forward data** (verdicts still recorded) — confirm the 11 "harmful" stay harmful out-of-sample before deleting their computation (CPU saving).
3. **Roster selector follow-up**: retry the 21 RPC-failed top-40 wallets; dup-check the 45Sn4KL1/9fcMp3GN twins; decide if 4jkL4dN earns watchlist or forward-shadow first.
4. **Wallet discovery recurrence**: run `python scripts/discover_wallets_dexscreener.py 2` manually on PC-on days — `_wallet_discovery_log.json` accumulates; recurring wallets = real candidates (one snapshot can't rank).
5. **Still designed-but-unwired** (deprioritized behind entry stack by today's evidence, not dead): daily_loss_limit_usd enforcement, per-token fleet exposure cap, day-state size dial, profit sweep (dormant, activates at go-live).
6. **STOP-WIDTH AUDIT ~2026-06-16** (when drawdown coverage matures): `max_drawdown_pct` capture went fully live 2026-06-08 (~130 sells/day carry it; before that only ~5% coverage — too thin to act). Question: is the −15% hard stop too wide for the pond cohort? Fragmentary data hints pond winners rarely dip below −8% (med −0.4%, p10 −3.2%, n=96 BIASED sample) → −10% stop could save ~6pp per hard-stopped trade at near-zero winner cost. Re-run `_exit_audit.py` on a week of full-coverage pond data, then decide. DO NOT act before coverage matures.
   Context (exit audit 2026-06-09, full peak coverage n=2,687): TP system confirmed tuned for the pond — 5th confirmation, pond-specific: peaks med +5.9%/p90 +12.4%, only 4% reach +25%; capture median 90% of peak, money-left median 0.8pp. Big losers (≤−8%) peaked at just +1.2% median (only 20% saw ≥+3%) — straight-down losers, not rescueable by TP tweaks; never-green faststop already handles them. Exits are NOT the leak; don't re-tune TP/trail.

## Notable context
- **Claude Fable 5 released today** (`claude-fable-5`, $10/$50 MTok). Claude Code auto-mode classifier breaks on it → use manual-approval mode (Shift+Tab). Memory: `reference_fable5_claude_code_setup.md`.
- Pre-existing test failure (NOT today's work): `test_bot_catalog.py::test_layered_defender_bots_present` (filter_rolling_ng config drift).
- Deploy etiquette: no poll-loop camping — one `railway deployment list` + one `/api/stats` check.

## Late-session additions (~19:00 UTC)
- **3 pond combo clones SHIPPED** (in-pond held-out mine, judged vs pool_a_stack at n>=50 distinct tokens): `pond_settled_flow_thin` (86% test WR, +$2.94/tr, 14-token caveat), `pond_settled_flow` (80%, +$2.05, 26-token anchor), `pond_ugly_mtf` (81%, +$1.79, orthogonal). Memory: `reference_pond_mine_2026_06_09.md`.
- **4th clone `pond_settled_flow_solcap`** (= settled_flow + sol_pc_h1<=0.3): SOL audit found red-side gate mis-aimed-but-inert (best band = modestly-red −0.7..−0.3: 79% WR; DON'T tighten) and SOL-GREEN h1>+0.3 the pond's worst cohort both halves but magnitude-unstable → A/B'd, not enforced.
- **Exit audit**: TP confirmed tuned (5th time, pond-specific); stop-width audit queued for ~06-16 (above).

## State at handoff (~19:00 UTC)
PAPER_MODE=true (verified). HEAD = pond_settled_flow_solcap deploy. Entry stack ENFORCED + 18-filter prune live on gated fleet; control cohort ungated; 4 pond clones racing. smart_follow on 7-selector watchlist, K=3. Forward A/Bs accumulating: (1) gated-vs-control bleed test, (2) 4 pond clones vs pool_a_stack, (3) solcap vs settled_flow.
