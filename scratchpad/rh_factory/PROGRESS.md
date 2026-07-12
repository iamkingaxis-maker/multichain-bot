# RH Candidate Factory — build progress

Mission: mine the full replayable history (sweep_logs 10.36M swaps, 07-01..11) for entry
configs that would pass the Phase-1 bar; four-half discipline; ship top survivors as new
"factory" paper racers in scripts/rh_paper_lane.py. NO commits.

Started: 2026-07-12 09:41 UTC

## Plan
- [ ] 1. factory_mine.py — extend mine_regimes.py: loose dip trigger (>=6%) superset,
        RICH entry stamps (dip depth, age, b30/s30/nb30, b120/s120, cum_eth vol proxy,
        arc position, pop-recency) + 3 realistic exit-ladder sims (scalp/aged/timebox)
        with entry +1% / exit -1% haircuts, gap-through-stop fills at observed px.
        Output: rh_factory/candidates.jsonl.gz. RUN IN BACKGROUND (~10-25 min).
- [ ] 2. winner_delta.py — the 91 audited day-robust winners' <1h entries vs our scalp
        racers' entries: same features, the separating signature. (runs while #1 mines)
- [ ] 3. factory_sweep.py — grid over entry cuts x 3 exit classes, graded per four halves
        (chrono W1/W2 x odd/even) against the Phase-1 bar (n>=20 distinct pools/half,
        tokmed ex-top2 green, cat<=1/20). Pop-retrace family = pop_ago_s filter.
- [ ] 4. Adversarial pass: neighborhood robustness of top cells, stale-resolution stress,
        survivorship + backtest-vs-paper gap statement.
- [ ] 5. Ship top 3-5 as LaneBot racers (exclusion_group="factory"), new pure gate
        helpers + tests, run RH suites (direct exit codes).
- [ ] 6. scratchpad/_rh_candidate_factory.md report.

## Checkpoints
- 09:41 read the moat: mine_regimes.py, rh_paper_lane.py (roster/entry_verdict/exits),
  hist_decode.py + trip_age_dist.py (winner cohort defs), analyze_regimes.py (four-half
  machinery), _rh_regime_system.md (validated cells + refuted list), ledger state
  (10 scalps net-red, 3 aged just born; totals above).
- Sweep log row: {p,k,w(eth),px,b,x,i} — NO maker, NO liq. Demand breadth in the mine =
  buy COUNT (nb30) proxy for distinct buyers (sweep rows are 1 swap = 1 tx); liq axis =
  cum_eth session volume proxy. Both stated in the honesty pass. hist_*/tape_* files DO
  carry makers -> winner-delta gets real distinct buyers.
- 09:45 factory_mine.py launched (bg): 2M rows/77s, ~16.8k cands per 2M -> est ~85k
  candidates. winner_delta.py DONE (rh_factory/winner_delta.json):
  * OUR paper fleet has ZERO <1h-band buys (345 total) — min_pool_age_h=1.0 blocks the
    entire 88%-win band; rh_launch_scalp (0.5-20min, strength-mode) took none either.
  * WINNER (93 makers, 294 <1h entries, 26 pools, 9 days) vs LOSER (61 makers, 311
    entries, 76 pools, 4 days) separators at entry time:
    - dip600: winners MODERATE pullbacks (p50 -8.6) vs losers deep flushes (p50 -15.6).
      Our all-buys dip p50 -17.2 = the LOSER profile.
    - arc (px vs first taped px): winners p50 +540% vs losers +1240% — losers buy LATE
      in the launch arc. arc<=1000 holds 130/294 winner vs 45/311 loser entries.
    - vol_pre: winners $16k med pre-volume vs $6.6k; nsw_pre 198 vs 77 — winners buy
      PROVEN pools, losers buy thin fresh ones.
    - nf120/dbuy120/nb30: NOT separators (both cohorts similar).
  * COMBO (dip -25..-6 & vol>=5k & nsw>=60 & arc cap): entry-level separation good,
    POOL-level thin (7 W vs 4 L pools) -> signature PROPOSES cuts only; the full-history
    sweep must validate at n>=20 pools/half (token-dedup discipline).
- 10:0x factory_sweep.py written+refactored (importable select/grade; run under main()).
  factory_adversarial.py written (neighborhood notches + stale-stress + drop-stale).
  Wiring design decided: firehose tape rows carry NO px -> runtime arc/pop facts come
  from the lane's QUOTE series + a lane-tracked first-seen px dict and a per-pool pop
  tracker (mirror of the mine's 1.35x detector, 600s window — narrower than the mined
  1800s pop_ago; noted as an approximation). New LaneBot fields (all default OFF):
  dip_max_depth_pct / min_buys_30s_count / max_arc_pct / require_pop_within_s.
- Mine at 8M/10.36M rows (452s), ~52k candidates. Monitor armed for DONE.
- 10:15 mine DONE (10.19M rows/591s, 64,164 cands, 62,726 written). Sweep v1 ran and
  produced 619 "survivors" — REJECTED by self-audit before belief:
  * BUG 1 (phantom spikes): V2 px = |wnet|/|tnet| glitch prints (1e20 pnl) fill TP legs
    at observed pnl -> net_usd overflow garbage, contaminated realized. FIX: TP fills
    capped at threshold+15pp; any leg capped at +300; trail/stop fills stay observed.
  * BUG 2 (dead-pool masking, the big one): res="stale" = pool NEVER traded again ->
    booked at last observed px as if sellable. 15-21% of top-cell trips were stale =
    rug/death survivorship INSIDE the backtest. FIX: at finalize, a trip whose pool
    went silent with (stream_end - t0) > horizon+slack books remaining at -90%
    (res="dead"); only end-of-stream trips keep last-px booking (res="stale_end").
  * Rerun mine + sweep with fixes; only then adversarial pass.
- 10:2x (post session-reset resume): fixes applied to factory_mine.py (LEG_CAP=300,
  TP_SLIP=15, DEAD_PNL=-90 for pools silent >horizon+10m before stream end, stale_end
  only for end-of-stream trips, drop <5min); sweep/adversarial updated to the new res
  taxonomy (dead baked into ret; stale_end = the only unknowable, stress = drop/-90).
  v2 mine+sweep launched (bg, ~12 min). Lane plumbing shipped meanwhile:
  * LaneBot fields: dip_max_depth_pct / min_buys_30s / max_arc_pct /
    require_pop_within_s (all default OFF -- 13 existing racers byte-identical).
  * Pure helpers: dip_depth_block, buys_breadth_block, arc_pct, arc_block, pop_fired,
    pop_recency_block. Lane: _note_px (first-seen px persisted in state file +
    pop_book tracker), factory shared facts in _consider_entries, extra_blocks wiring.
  * Existing RH lane suites: 116 passed post-plumbing (roster-shape tests will be
    updated when factory racers land).
  * Coordinator update noted: cold-start fix + liq seed shipped (d4923d7) -- cloud
    lane accrues 24/7, so paper confirmation throughput is real.
- 10:5x: + proven-volume gate (LaneBot.min_session_vol_usd, lane cum_vol tracker
  persisted in state file, helper proven_vol_block) -- the winner-delta vol_pre axis,
  exact runtime mirror for pools discovered at creation. 55 tests green.
  Note for racer mapping: paper history shows age<2h buys carry liq ~37k on RH
  (Robinfun seeds LP) -> factory young racers can KEEP min_liq_usd=30k (guard parity).
- FINAL (v2 results): mine v2 DONE (10.19M rows/583s; 63,972 written; dead booking +
  leg caps in). Sweep v2: 983 cells, 562 pass 4/4 -- but bar letter admits negative-
  NET cells (median green, dead tail eats the mean); shipping ALSO required positive
  net + min-half strength + neighborhood green + runtime expressibility.
- SHIPPED 5 factory racers (exclusion_group="factory") in scripts/rh_paper_lane.py:
  rh_f_pullback (u10m|sh|v3|d25|aged+arc<=300, tokmed_ex2 +2.46, min-half +1.97),
  rh_f_arc_scalp (u10m|mod|v3|d25|scalp+arc<=300, +1.97/+1.85),
  rh_f_popret (u10m|deep|v.3|d50n|scalp+popret, +1.94/+1.92, cat 0.0),
  rh_f_reload24 (>24h|vdeep|v10|d25|aged, +1.78/+1.08, net +$1,285; DORMANT until
  RH_FEED_MAX_AGE_H>24), rh_f_reload_mid (6-24h|vdeep|v.3|d50n|aged, +0.93/+0.76).
- Adversarial: all 5 neighborhood-GREEN every notch; stale_end stress: 4/4 for four,
  rh_f_pullback 2/4 in the -90 worst case (caveat stated in report).
- Tests: test_rh_factory_racers.py = 33; full RH suites 428 passed / 2 skipped, exit 0.
- Report: scratchpad/_rh_candidate_factory.md (winner-delta signature, sweep tables
  per half, adversarial findings, racer specs, pre-registered confirmation bar:
  n>=30 closes each, tokmed ex-top2 green, cat<=1/20, direction = cell, else retire).
STATUS: COMPLETE (2026-07-12). Working tree only -- NO commits (per mission).
