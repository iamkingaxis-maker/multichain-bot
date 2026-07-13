# Smart-Money Follow Green-Cohort Hunt — PROGRESS

Goal: find a GREEN copyable smart-follow cohort by EX-TOP-2 token-median (drop 2 best tokens).
Green = ex-top-2 median > 0 AND >=50% tokens green. n>=15 distinct tokens. OOS 4-half. Lifetime SUM BANNED.

## Filters available (from core/strategies/smart_money_follow.py + follow_capital.py)
- copy_dial state: good / neutral / bad / warming (rolling-20 expectancy of follow book)
- tier / K: k3 (full consensus), k2 (high-tier pod), solo, convex; n = consensus wallet count
- flush_gate: pass/blocked/shadow_block (pc_h1 in [-30,-10])
- dist_guard: pass/blocked/shadow_block
- thin_book: liq<20k
- conviction_mult, fq_mean/would_size_mult, horizon

## Outcome source
- Authoritative copy outcome = /api/trades?full=1, strategy tag smart_follow*, join by token, realized pnl_pct on sell.
- entry_meta stamps: follow_fire_ts, follow_tier, follow_conviction_mult, follow_fq_mean
- follow_signals.jsonl (Railway, DATA_DIR) has full fire stamps incl copy_dial + token state.

## Status — COMPLETE. Verdict: NO green copyable cohort (see _sol_smartmoney_hunt.md)
- [x] Read smart_follow strategy + follow_capital + configs
- [x] Local follow_signals.jsonl STALE — pulled fresh /api/follow-logs (1635 fires 07-09..07-13)
- [x] Pulled /api/trades?full=1&all=1 (10921) + /api/follow-capital
- [x] DATA GAP: recent smart_follow copies NOT recorded per-token (trades store ends 07-05 baseline_v1;
      185 elite_exit all position_closed=False; follow_capital keeps only aggregate + last-40).
- [x] copy_dial = "bad" on 100% of 1635 recent fires; realized pool = -$122.03 since 06-11.
- [x] Built roster-OWN-return UPPER BOUND (copy <= elite own), ex-top-2 per cohort + OOS 4-half.
- [x] Best subset (elite-own, before copy-tax): gate-passed ex-top-2 +2.8% n=159, convex +3.5% n=126;
      but n>=4 leg -4.2% (WORST), realized pool red, survivorship-biased, copy-tax (+1.56% chase) eats it.

## Verdict
NO green smart-follow cohort. Best filtered subset ex-top-2 (elite-own UPPER BOUND) = +2.8%
(gate-passed) / +3.5% (convex) — fully consumed by documented copy-tax; REALIZED pool -$122, dial bad;
dial-good filter n=0; high-K/conviction filters invert. No shadow (nothing green). Re-mine only after
per-token realized copy-outcome logging exists.

## Artifacts
- scratchpad/_sol_smartmoney_hunt.md (full writeup)
- scratchpad/sol_smartmoney/{follow_logs.json, follow_capital.json, trades_full.json}
