# Session Handoff — The Bad-Day Playbook Day (2026-06-10)

**Bot URL**: https://gracious-inspiration-production.up.railway.app
**Mode: PAPER throughout** (`live_mode: False` verified after every deploy). No PAPER_MODE flip.
**HEAD**: `7bb48bc`. 17 commits this session (a9918c5 → 7bb48bc), ~10 serialized deploys, suite **673 passing**.

**THE HEADLINE: the regime problem ("incredible days then a week of bleeding") got its full
counter-system. Five independent evidence lenses agreed: bad days don't kill the market, they
ROTATE it — to fresh launches, already-running momentum, and sub-500k microcaps, exactly the
zone our pond excludes. Shipped: the P7 regime dial (defense ENFORCED), a rug-screened bad-day
microcap family, SOL-gate removal on the bad-day vehicles, the smart-wallet full loop
(elite-exit mirroring / K-tier pods / realtime WS / 24-7 discovery), the walk-forward LIVE-SET
goal meter, and a daily accountability scorecard that grades every mechanism against its
pre-registered forecast.**

---

## SHIPPED + DEPLOYED this session (all paper, verified; newest first)

1. **Bad-day playbook** (`7bb48bc`):
   - **P7 regime dial** (`core/regime_dial.py`): size mult = min(yesterday fleet WR,
     first-quarter WR, rolling-20 expectancy). Walk-forward study (9d/1,263 closes, a-priori
     thresholds): −$677 → −$250 (63% of bleed removed); only policy catching loss-size days.
     **ASYMMETRIC: 0.5× defense ENFORCED on dip-pond sizing; 1.5× consensus upside SHADOW.**
     Exempt: momentum_mode, control cohort, `regime_dial_exempt` vehicles. Stamps
     `regime_dial_full/defense/signals` on every buy; live at `/api/regime-dial`.
     Env `REGIME_DIAL_MODE=enforce|shadow|off`. Pre-reg kill: <50% forecast acc at n>=10.
   - **badday_flush + badday_momo** (50–500k mcap, age>=6h, rug screens: unique_buyers>=12,
     recurring buyers>=1, wash-flag block, liq>=15k): flush entry pc_h1<=−20 (no positive-flow
     requirement — capitulation IS negative flow) / momentum entry pc_h1>=+30 + bs_m5 1.2–2.0.
     Fast spike exits (TP1 +6/0.75, TP2 +12/0.25, trail 2pp), gap guards, `entry_stack_exempt`.
     **Pre-reg: >= +$2/tr at n>=30 dial-bad closes AND catastrophe(<=−35% fills) <10%, else retire.**
   - **SOL gates OFF** the 8 bad-day vehicles (young_probe ×4, momentum ×2, badday ×2) —
     4 confirmations the gate points backwards there (would block 64% of bad-day opportunities
     at BETTER-than-allowed quality). Stays on the dip pond.
   - **`scripts/badday_scorecard.py`** — the daily no-narrative grader (see CADENCE below).
2. **Per-trigger token-state SHADOW** (`ac18284`, `core/trigger_state_gates.py`): the 06-08
   7-agent map (18 gates, 5 archetypes) stamps pass/block/na per fired trigger into
   `entry_meta.trigger_state_shadow`. Zero behavior change; enforce at n>=50/gate with WR lift
   (scorecard §5 tracks).
3. **Young-probe clone wave + momentum_shadow gap guards** (`cb07ce1`):
   - `young_probe_stair` (higher_low_5m + 30m pivot staircase; test 86% +$4.52/tr) and
     `young_probe_baseflow` (1s base confirmed + bs_h1>=1.41; test 95% +$3.39/tr) — mined from
     the 81-close family record; thesis = young winners are in confirmed short-term UPTRENDS
     (mirror of the pond's deep-dip thesis). 7–8-token diversity → fast-cut terms in configs.
   - momentum_shadow stop gap-through fixed (13 stops filled avg −15.6% on a −12 stop):
     **giveback floor** (peak>=+4 → exit −6, pre-TP1) + **fast-dump bail** (−9 any volume,
     pre-TP1). Config-driven (`giveback_floor_*`, `fast_bail_pnl_pct`), default-off fleet-wide,
     also enabled on the badday family.
4. **Walk-forward LIVE-SET goal meter** (`90508fa`): headline = bots already net-positive
   trailing-7d (>=3 closes) BEFORE the day started — what go-live would actually have run.
   Proof it measures right: the −$635 fleet day was **live-set +$46**. Streak counts live-set
   days. Also: era-proof dedup/attribution (tracker bot_id flips None↔baseline_v1 across
   restarts — cache key now excludes bot_id; smart_follow attribution keyed by address only).
5. **Hybrid cost model** (`ff34881`, AxiS decision: no paid tools until profitable):
   `scripts/sync_trades_cache.py` — incremental local cache (~300KB/sync vs 20MB full pulls).
   `--full` for entry_meta mines, `--rebuild` max ~1×/day. Bot stays on Railway; analysis local.
6. **On-bot continuous wallet discovery** (`6084134`, SW5): hourly GT→DexScreener early-buyer
   harvest on Railway, recurrence log in DATA_DIR, `/api/wallet-discovery`. (Pass #1 returned
   0 runners silently → GT diagnostics added in `ac18284`; check the log line on next passes.)
7. **smart_follow full loop** (`680db27`, SW1–SW4):
   - **Elite-exit mirroring**: >=2 trigger wallets SELL → we exit via
     `PositionManager.external_exit` ("follow them out"). Env `SMART_FOLLOW_ELITE_EXIT`.
   - **K-tier pods**: K=2 high-tier (V21GW8P+HmP3Txu, tag `smart_follow_k2`, 8/hr cap),
     K=1 solo (Abk9Efh, tag `smart_follow_solo`, 6/hr cap — the 06-09 flood lesson).
     `config/follow_tiers.json`. PM treats all `smart_follow*` tags identically (startswith).
   - **Fire-quality size SHADOW**: `config/follow_quality.json` → `would_size_mult` stamped
     per fire; enforce at n>=40/wallet.
   - **Realtime WS watch**: logsSubscribe on all 10 wallets; a notification wakes the sweep
     in seconds (5s floor). Poll fallback intact.
8. **pool_c_post_peak re-enabled** (`a614d89`): the 06-05 −EV disable was the REGIME (twin
   tightexit shows identical decay shape, was kept). Judge at n>=30 post-stack closes.
9. **Measurement integrity** (`50e74fb`, `d01c1bd`): trimmed /api/trades now carries bot_id;
   egress-throttled heavy pulls return explicit `{"egress_throttled":true}` (header-only flag
   was missed twice → false −$42/−$79 verdicts); heavy json.dumps off the event loop (was
   starving /api/stats); /api/goal reads tracker+store (smart_follow was invisible, −$38).
10. **Pond wave-2 clones** (`54c047c`): pond_ugly_rsi (82% +$2.40), pond_sweep_flow (84%
    +$1.11), pond_sweep_deep_thin (82% **+$3.85** best $-density, 11-tok → fast-cut terms).
11. **smart_follow stop-grace A/B** (`2ff2cc6`): treatment (token-addr parity even) defers
    hard stops 45min, −50% catastrophic floor; control untouched. From the post-stop test:
    **14/19 stops recovered >15% within 12h** (median ~+35%). Judge arms at ~20+ closes each.
12. **smart_follow flush-depth gate** (`a9918c5`): fires require pc_h1<=−10 (shallow fires
    were 22% WR, −$41/9). Env `SMART_FOLLOW_FLUSH_GATE`, threshold tunable. Blocked fires
    still logged with verdicts.
13. **Workflow cleanup** (`6568b69`, `388604d`, `fcf2dab`): repo root 450→68 files
    (analysis/ archive + MANIFEST + rules), 14 dormant bots retired (.json.off, 41 active
    catalog), dashboard goal-first (meter on top, GOAL CANDIDATES table, experiments collapsed).

---

## KEY FINDINGS (the day's evidence chain)

- **THE BAD-DAY ROTATION (5 lenses agree)**: (1) own trades: only moderate-vol-spike entries
  break even on bad days; every mcap/age/dip band bleeds. (2) universe 2,916 events, 2 bad-day
  folds: age<1h 72% win10 (monotonic to >3d 23%), pc_h1>+30 63%, deep flush <=−20 56%,
  **middle pc_h1 −5..+5 = 26% DEAD**, mcap<500k holds while 500k–5M = 22–33% (the pond band is
  the bad-day dead zone). (3) DexScreener live: 9 movers on the whole tape (normal: 30–50),
  median age 11.7h / FDV $184k / liq $29k; top runner boosted+social. (4) **10 elites: 303
  buys, 57 round-trips, 67% WR, +0.60 SOL ON the bad day** (HmP3Txu 8/8, new-add 2x99 9/9);
  the two slow accumulators (Abk9, 2tYcX) stood down — their own regime dial. Hunting ground:
  median $55k mcap, $19k liq. (5) MAITIU case (7Pk1…pump): 30 recorder sightings 12:00–18:00,
  15 winning windows to +44%, **100% blocked** (stack mcap floor on all 30).
- **RUG MINE (3,959 microcap events)**: catastrophes (<=−35% in 30min) are **YOUNG + FRANTIC**,
  not quiet — median age 1.9h vs runners 8.2h, 3× the hourly volume/churn. age>=6h cuts 79%
  of catastrophes keeping 55% of runners; composite screen → 20%→6% cat rate. (AxiS: "50-500k
  is where rugs live" → the screens are the family's foundation.)
- **SOL GATE backwards on bad days**: would block 64% of opportunities; blocked cohort BETTER
  than allowed (young: 64.1% vs 59.4% win10; flush: 59.0% vs 49.8%). 4th independent confirm.
- **P7 study** (9d candidate closes, thresholds a priori): P7 total −$250 vs baseline −$677;
  worst day −635→−335; the only policy that trims loss-size days (06-10). P4 yesterday-breadth
  HURT (−$743) — breadth keying dead, don't revisit.
- **Old findings reconciled**: "age<2h = 0% WR" (dip-style entries into young tokens = knife
  catching) vs universe "young runs 72%" (momentum-confirmation entries) — both true; the
  ENTRY decides. Young mine: winners are in confirmed uptrends (stair/baseflow clones).
- **Exit-horizon verdict landed**: post-stop test (after fixing empty pair_address on
  smart_follow buys — pool resolved via DexScreener) → 74% recovered → stop-grace A/B shipped.
- **Today (06-10) closed ≈ −$128 candidate / −$34 live-set**: dominated by the regime turn
  (control cohort flipped +$2.27 → −$27.59 — tape, not bots); smart_follow −$65 mostly
  pre-gate/pre-grace; the new defenses deployed mid-day.

## DAILY CADENCE (the accountability loop — run every morning)

```
python scripts/sync_trades_cache.py && python scripts/badday_scorecard.py
python scripts/goal_tracker.py --cache _trades_cache.json
```
Scorecard sections + pre-registered judgments:
1. Day verdict (fleet / candidate / walk-forward live-set)
2. **P7 dial forecast record** — KILL at <50% accuracy, n>=10 scored days
3. **badday family** — RETIRE at <+$2/tr @ n>=30 dial-bad closes OR cat-rate >=10%
4. Stop-grace arms (judge at ~20+ closes/arm)
5. Trigger-state shadow — ENFORCE candidates at n>=50/gate with >=8pp WR lift

## PENDING / WATCH

- **badday family first fires** — brand new; if too quiet, audit live availability of
  `unique_buyers_n` / `n_recurring_buyers_3plus` / `wash_suspected` in raw_meta.
- **Wave-2 ponds + young stair/baseflow** first closes; pond_ugly_mtf tripwire (48h zero-fire
  → re-audit `chart_mtf_score <= -0.01` vs live value rounding).
- **smart_follow new systems** first events: elite-exits in follow log (`type:elite_exit`),
  k2/solo pod fires, WS wake latency, grace-arm closes.
- **WalletDiscovery GT diagnostics** — pass #1 was 0 runners silently; the new log line
  (`GT: ok/failed`) tells why on the next hourly pass.
- **Cross-day recurrent wallets**: day-2 intersect found 18; ALL rejected as single-token
  churners by the diversity scorer. Funnel works; needs recurrence depth (3–4+ days) — now
  accumulating 24/7 on-bot. The 23-wallet roster retry is CLOSED: 0 selectors.
- Stop-width audit ~06-16 (drawdown coverage matures; per-bot records carry NO max_drawdown yet).
- Gated-vs-control entry-stack A/B + pruned-filter re-audit (need days of forward data).
- Boost/launchpad flags as entry features (free in the DexScreener pair payload; the bad-day
  attention signal — top bad-day runner was actively boosted). Not built yet.

## STANDING RULES (unchanged)

Paper only; never flip PAPER_MODE (pre-flight + explicit approval required). $0 tools only
(hybrid cost model; VPS ~$5/mo fallback if Railway bill trends past $25 — check the real
usage graph first). Commit→push→deploy ritual; no deploy camping (one status check).
All timestamps from `date -u`. Fleet = selection instrument; judge bots individually.
Goal: $100/day on the WALK-FORWARD LIVE SET; streak target 5 before the go-live conversation.
