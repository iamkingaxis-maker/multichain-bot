# RH deep-decode PROGRESS

## Status: DONE. Deliverable scratchpad/_rh_deep_decode.md written; racer shipped; RH suite exit 0 (515 passed).

## Final TODO state
- [x] decode + table
- [x] scratchpad/_rh_deep_decode.md
- [x] rh_deep_consolidated added to ROSTER (exclusion_group deepsynth, len 22)
- [x] roster-spec test (TestDeepConsolidatedRacer, 4 tests) + fixed 3 stale tests broken by uncommitted lowvar racers
- [x] RH suite exit 0 (515 passed, 2 skipped)
- [x] CUT recommendation: rh_demand_heavy (not deleted)


## Ground truth (AxiS, railway leaderboard, 07-12)
GREEN: rh_deep_only +4.65, rh_bites2 +4.56, rh_f_arc_scalp +3.08
RED:   rh_demand_heavy -14.61 (WORST), rh_wide_ladder -4.38, rh_moonbag -2.49

## Data reality
- Local scratchpad/robinhood_tapes/rh_paper_trades.jsonl is a PARTIAL earlier snapshot;
  its absolute per-bot P&L signs DIFFER from AxiS's railway read (local demand_heavy looks
  green). Ledger is per-bot & NOT deduped in the file (unique ms-ts seq); dedup is only in
  the downstream dashboard ingest. So use AxiS P&L as ground truth; use local tape for
  STRUCTURAL/behavioral distributions (entry depth, exit-kind mix) which are snapshot-stable.
- rh_f_arc_scalp / all factory racers = 0 local fires (require_session_anchor + <10m age);
  their edge is from the full-history backtest (_rh_candidate_factory.md) + railway.

## Config deltas (deterministic, the real story)
GREEN share: DEFAULT SCALP EXIT (tp1 +6/0.75, tp2 +12/0.25, stop -15, trail 3pp, NO moonbag,
  NO time box) + entries selected by PRICE STRUCTURE, NOT demand-chasing.
  - deep_only: deep dip -25 (capitulation)   [same scalp exit]
  - bites2:    default dip -12 + 2-bite cap   [same scalp exit]
  - f_arc_scalp: moderate -6..-25 + proven-vol >=$4.8k + early-arc <=+300% [same scalp exit]
RED each BREAK it:
  - demand_heavy: raises demand_min_buy 50->150 = filters on DEMAND-AT-THE-MOMENT, which RH
    winner-delta says does NOT separate winners/losers, and SOL mine says INVERTS (bigger
    buyers = MORE red = chasing strength). Over-trades a non-separating/anti axis. WORST.
  - wide_ladder: exit +10/+20 (vs +6/+12). RH fades revert fast; +10 rarely reached ->
    converts +6 winners into trail/stop. (local: TP2 13/97=13% vs ~22% peers; loss legs 34%.)
  - moonbag: keeps 10% residual, 0% floor, loose 20pp trail -> moonbag tail bleeds the rug
    down, giving back banked TP1. (local: 18 MOONBAG_FLOOR/TRAIL tail legs.)

## Causal lever
NOT entry depth (deep_only -28 deep, but bites2 -15 SHALLOW, and red demand_heavy -18 is
DEEPER than green bites2). The lever = (1) tight scalp exit discipline (bank fast @ +6,
tight 3pp trail, no bleeding tail) + (2) do NOT chase demand strength on entry.

## Consolidated recipe -> rh_deep_consolidated (exclusion_group "deepsynth")
deep capitulation entry (-25) + proven-vol floor $4.8k (anti thin-flush, structural not
demand-moment) + 2-bite cap + THE shared scalp exit (+6/0.75, +12/0.25, -15, trail 3pp,
no moonbag/box). Pre-registered n>=30 confirm (tokmed ex-top2 green, cat<=1/20).

## TODO
- [x] decode + table
- [ ] write scratchpad/_rh_deep_decode.md
- [ ] add rh_deep_consolidated to ROSTER
- [ ] add roster-spec test
- [ ] run RH suites (exit codes direct)
- [ ] recommend CUT: demand_heavy (evidence above); do NOT delete
