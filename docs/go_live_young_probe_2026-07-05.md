# GO-LIVE RUNBOOK — badday_young_absorb T1 probe ($25)

Drafted 2026-07-05 03:30 UTC. Executes ONLY after: (1) 07-05 closes green for
young_absorb (completing 5/5 green days + n>=30 distinct tokens, the
pre-registered bar), (2) AxiS explicit approval, (3) hot wallet funded.

## Pre-conditions (state as of drafting)
- [x] Pre-flight: tests/test_pre_live_invariants.py — 16/16 PASS (2026-07-05 03:20 UTC)
- [x] BUY_REPRICE_MODE=enforce, EXIT_REPRICE_MODE=enforce (go-live prerequisites)
- [x] LIVE_CONFIRMED=true; STRATEGY_ALLOWLIST fail-closed
- [x] Probe config staged DORMANT: config/bots/badday_young_absorb_live.json
      ($25 base, 2 slots, $25 daily kill, live_probe=true, enabled=false)
- [x] Zero other enabled live_probe bots (fully fail-closed live set)
- [ ] **FUNDING (AxiS): hot wallet Ao8uMKCy… holds 0.1495 SOL (~$12).
      Needs ~0.75-0.9 SOL (~$60-70) for $25 x 2 slots + fees/rent buffer.**
- [ ] 07-05 young day close green (verdict ~00:15 UTC 07-06)
- [ ] AxiS approval

## Candidate evidence (at drafting)
- young_absorb: 4/4 green days (07-01→04: +3.27 / +2.41 / +9.90 / +6.78
  per-token scrubbed), distinct tokens ≈ 30+ with 07-05.
- Fee math: $25 in its pools ≈ 2.7pp round trip vs measured edge — viable.
- Guards live: holder rug guard (enforce), fail-closed demand gates,
  bail/stopout cooldowns, velocity+MAE floors, $25 daily kill on the probe.

## Execution order (one sitting, ~15 min, no positions open)
1. Confirm fleet flat: /api/bots open_position_count == 0 for all bots.
2. Re-run pre-flight: `python tests/test_pre_live_invariants.py` → 16/16.
3. Enable the probe: badday_young_absorb_live.json enabled=true; commit; push.
4. `railway up` — verify boot, PAPER_MODE still true, probe visible in /api/bots.
   (Paper cutover sanity: it may take paper young entries alongside
   young_absorb — expected, harmless, shares the young lane gates.)
5. AxiS funds wallet if not already (transfer BEFORE the flip, verify balance).
6. THE FLIP (AxiS present): railway variables --set "PAPER_MODE=false".
   Wait for cutover (live_mode=True in /api/stats). Do NOT clear P&L before
   cutover (paper-buy window artifact — June lesson).
7. Watch the first live swap end-to-end: decision log → swap attempt →
   on-chain fill → live_swaps.jsonl entry. Verify fill price vs decision
   (slippage ≈ measured 1.3%/leg band).
8. Honest P&L tracking from that moment = on-chain SOL delta +
   live_swaps.jsonl ONLY (never dashboard realized_pnl).

## Halt criteria (any one → railway variables --set "PAPER_MODE=true")
- Daily kill: probe hits -$25 realized on the day (config-enforced; verify).
- Any swap failure/anomaly (attempted != successful).
- Fill slippage > 3%/leg sustained (2+ fills).
- AxiS says stop.

## Ladder (edge-gated, never calendar-gated)
- T1 $25 → hold until: probe realized per-token >= +1pp over >= 3 live days
  AND >= 10 fills. Then T2 $50 (same criteria) → T3 $100.
- At $100: expected ~+$1.2-2.5/trade at young's measured edge → the $100/day
  target needs the coverage lever (firehose watch-earlier) delivering
  ~6-10 qualifying tokens/day. Track daily.

## Standing rules that survive the flip
- No redeploys while the probe holds a position.
- Every live loss autopsied same-hour.
- Paper fleet keeps running (research continues unchanged).
