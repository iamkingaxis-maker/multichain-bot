# RH Rug-Defense Port — PROGRESS

Mandate: port Solana rug-defense concepts (hidden-supply gate, LP custody, labeled cohort)
to RH chain (EVM), SHADOW-STAMP ONLY into rh_paper_lane.py. No commits, no live changes,
no Railway, no Solana paths. RPC paced (live lane may share the public RPC).

## Status: COMPLETE 2026-07-11 (shadow stamps live at next lane restart)

Full report: scratchpad/_rh_rug_port.md (retro table, costs, stamp fields, grading plan).

## Shipped (working tree, NO commits)
- core/rh_rug_signals.py — pure replay/aggregation + compute_entry_stamp (tiered,
  paced, budgeted 90s/60k logs, FAIL-OPEN).
- scripts/rh_paper_lane.py — _stamp_rug_signals/_rug_stamp_row wired after _paper_buy
  (daemon thread, single-flight lock, 10-min pool cache, RH_RUG_STAMP=0 kill switch,
  {"ev":"rug_signals"} ledger rows). Running lane process untouched.
- tests/test_rh_rug_signals.py (16) + TestRugSignalStamp in tests/test_rh_paper_lane.py
  (4). Suites: 130 passed (rh_paper_lane 37, fleet, aged_racers, rug_signals).
- scratchpad/rh_rug_port/: retro.py (harness), retro_*.json (9 cases), grade_stamps.py
  (offline grader; run with --absorb for the Δpool_pct labeler).

## Key findings (details in _rh_rug_port.md)
- RH rugs = DUMP-class, not LP-pulls: all 9 pools' LP NFTs owned by ONE launchpad
  custodian 0x7f03effbd7ceb22a3f80dd468f67ef27826acd85 via canonical NFPM 0x73991a25…;
  rug LPs never pulled. Creator cannot pull on hood.fun graduations.
- At-entry tell exists: rugs enter pool_pct<25 with fat shoulder (sh/top10>=0.6) or a
  whale overhang (CASHCATGAME top1 25%->11.9%). joint_dump_shape = 5/5 catch, 1/4 kill
  on the tiny set (NOT promotable — accrue).
- Labeler: Δpool_pct(head-entry) >= +15pp — rugs +14..+71, survivors <=+1.
- Costs: pool_pct shape = 2 eth_calls; full stamp ~10-20 calls/5-30s young,
  budget-capped for aged; measured Ape end-to-end 11 calls/9.1s, supply-match true.
- No archive state; no keyless explorer holders API (Next.js frontend only) —
  Transfer-log replay is the method, replay_supply_match validates genesis reached.
- retro.py gotcha fixed: ledger ts are UTC — calendar.timegm, never mktime-time.timezone
  (1h CDT error put entries before pool creation).

## Grading (next sessions)
python scratchpad/rh_rug_port/grade_stamps.py [--absorb]
Bar: n>=30 rugged, catch cap-hitting class, winner-kill<=5%, AxiS approval.
