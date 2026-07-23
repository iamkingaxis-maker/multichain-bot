# PATH-TO-GREEN MEMO — 2026-07-23

I have everything I need — the realizability number is triangulated three ways and the live realized dollars are fresh. Writing the memo.

---

# PATH-TO-GREEN — RH Memecoin Operation
**For AxiS. Fidelity-honest, realized dollars only. Numbers verified this session (2026-07-23) against `candidates_sl1.jsonl.gz` n=63,972 + live `/api/rh-paper` n=407/162/658, ~18-23h fresh, last event 0.0h ago.**

## 1. THE ONE-SENTENCE VERDICT

**No — there is no proven path to sustained, scalable, bill-paying green; the whole thesis hinges on one load-bearing assumption (that a trailing stop captures 40-50% of the runner peak), and I measured that capture at 2.8% median / 4.4% mean three independent ways — so on the strict realized-dollar standard we have reached *less-red, not green*, and the single 2-week test that could still salvage a narrow cell is currently un-runnable because the instrument that measures it is dark.**

## 2. THE DECISION TREE — critical realizability gate FIRST

Nothing downstream matters if the tail is a mirage. It measures as a mirage. Here is the exact ordered sequence, each with a kill number.

**GATE 0 — REALIZABILITY (settles everything else).**
The idealized let-run edge (+5.6/+10.1%) assumes a trailing stop harvests ~45% of the price-path peak. What I found:
- Replay, mfe≥100 runners (n=6,278): median peak **179%**, path-real ladder realizes median **9.8%** → **realized/peak = 2.79% median, 4.43% mean.**
- Independent gb path-sim (lever-3): 2.7% median / 7.1% mean — same answer.
- Live let-run: winners *are* being caught (TP2 mean +106%, POST_TP1_TRAIL +26.8%) yet net/buy is still **−3.32%** because 114 of 235 sells are PRE_STOP_BAIL (−7%) + HARD_STOP (−49%).

The peak is *fillable* (it's a real trade price, pknet/mfe≈0.97) but *not capturable* — the failure mode is give-back, not spike-illusion. Either way, "capture 45%" is wrong by ~6-16×.
- **The measurement to settle it live:** `peak_pnl_pct` on every let-run sell → median `realized_pnl_pct / peak_pnl_pct` on POST_TP1_TRAIL + TP2 exits, n≥30 closed.
- **BLOCKING PROBLEM:** `peak_pnl_pct` is emitting on **0 of 235** live sells. The gate is un-runnable until that field is wired. **This is build item #1 — one field.**
- **KILL** if median live capture < 20%. **GO** only if ≥25% AND net/buy > 0. *Replay predicts ~3-7% → this gate most likely fails, and if it fails the power-law/let-run thesis is dead and you stop here.*

**GATE 1 — the one residual entry cell (only if Gate 0 passes).**
Under honest booking (dead/rug = −100), every scalable cohort is red:
- Broad let-run-ish (aged_sl1): **−9.2%** mean.
- Runner + liquidity floor (lever-2's claimed +8% "green"): **−11.9% entry / −18.0% token-deduped.** The +8% was pure mfe-capture illusion — refuted.
- **Only** aged 24h+ & dip≤−30 sits near zero: **+0.04% entry / −1.71% token-deduped** (capped ladder); lever-4's pure-let-run version reads +2.17%. That is the sole non-negative cell.
- **Live seat restricted to age≥24h & dip≤−30 & let-run.** Measure net/buy $, **token-deduped**, over n≥30 closed, ≥5 distinct days. **KILL** if net/buy ≤ 0 after fees OR drop-top-2 flips negative. **GO** if net/buy > +$0.30 (>+1.2% @ $25) AND drop-top-2 positive.

**GATE 2 — throughput reality (only if Gate 1 passes).** Measure live round-trip slippage at $25/$50/$100. **KILL scaling** if $100 round-trip > 5%. This sets the hard size ceiling (see §3).

**GATE 3 — regime router.** The multiplier is real but **+0.29pp causal — ~10× too small to flip a −0.28% core.** Keep it as a drawdown-reducer only; **KILL any framing of it as the thing that makes the core green.** It does not.

## 3. HONEST CAPITAL MATH (IF the path works — i.e. Gate 0+1 pass, which is <30% likely)

Best case is the aged-deep-flush cell at its replay-best +2.17%/entry (~$0.40 net @ $25), ~20-35 entries/day live:

| Size/entry | Round-trip slip | Per-entry edge | $/day (≈25 entries) |
|---|---|---|---|
| $25 | ~0.5% | +2.2% → +$0.40 | **+$10** |
| $100 | ~2-3% | ~+1% → +$1.0 | **+$25 (ceiling)** |
| $250 | ~4.9% | edge eaten → negative | **−$/day** |
| $2,500 | ~40% | catastrophic | **−$3,400/day** |

Zero-crossing on AMM impact into $20k-median pools is ~$200-300/entry. **Even if every gate passes, the entire realizable band is ~$10-30/day, and it goes negative above ~$200/entry.** Liquidity is a physical wall; size is the ceiling, not the lever. This does not pay bills. Career-mode's "scale capital into measured numbers" has no runway here because the measured numbers cap at pocket change.

## 4. FALSIFIERS — what tells us in 1-2 weeks there is NO path in memecoins → change waters

1. **Gate 0 fails:** live let-run median capture < 20% over n≥30 → the runner tail is not harvestable → the power-law/let-run thesis is a mirage. *(Replay predicts this outcome.)*
2. **Gate 1 fails:** aged-deep-flush live cell red, token-deduped, n≥30 → the last non-negative cohort doesn't transfer, and entry AUC is already at its 0.59-0.62 ceiling → no entry edge remains.
3. **$100 round-trip > 5% live** → even a real edge can't scale past ~$25-100/entry → confirms the throughput cap.
4. **Control keeps winning:** if over more days `rh_dipall_ctrl` (winner-*capping*) keeps losing LESS than `rh_letrun` (it's −1.57% vs −3.32% right now) → the exit-architecture thesis is refuted on realized $.

**If (1) AND (2) both fail → there is no scalable green in RH memecoins. Change waters.** That is the decisive 2-week outcome, and current evidence points at it.

## 5. STOP IMMEDIATELY (wasted motion)

1. **STOP mining entry features / chasing AUC** — 0.59-0.62 ceiling is established; filters halve bleed, never flip sign. Dead lever.
2. **STOP any analysis anchored on mfe / ret20 / "capture X% of peak."** Three of the four levers were fooled by exactly this banned method. Only ladder-realized $ and live realized sells count.
3. **KILL `rh_letrun_runner` now** — worst live bot (−$3.01/entry, −12.03%), a refuted forward-return artifact; runner-entry is actively destructive. Don't wait for n≥30.
4. **Quarantine the crashed tape-walk** `scratchpad/_realize_rows.jsonl.gz` (EOFError, un-finalized, 0.1% booked −100 vs the honest 6.8%; `_realize_walk.log` has no DONE line). Its +7% headline is a survivorship artifact — make sure nobody re-cites it.
5. **STOP treating replay-green as go-live evidence.** Every replay-green cell here lives in a 3-day early-July window (78.9% of rows on 07-08/09/10, ZERO rows after 07-11); the hard recent regime is entirely absent.
6. **STOP capital-scaling as the revenue plan** — above ~$100/entry more size = more loss.

---

**The one genuinely new, verified, non-illusory result to keep:** the exit-architecture insight is *directionally* real — TP2 +106% and trail +26.8% are real caught continuations, so capped ladders do decapitate. But net stays red because the *loser* side bleeds to −49% HARD_STOPs. That reframes the actual open lever away from "let winners run" (capture fails) and "runner entry" (destructive) toward the **loser-side dollar-conversion problem** (matches the standing RH finding: losses close full-size at 2× win size). If anything gets one more try after Gate 0, it is an earlier loss-cut on let-run positions (SL1-style bank before the −49% stop), graded on realized $ at n≥30 — not another entry mine.

**Bottom line for a demoralized desk: the path is narrow, hinges entirely on realizability, which measures at ~3-7% capture (needs ~45%) and is <30% likely to clear the live gate; the 2-week test that settles it is Gate 0, and it can't even run until `peak_pnl_pct` is wired. Wire that field, let the three let-run bots close n≥30, and the capture ratio will tell you the truth cleanly. Current evidence says it will say no.**

Key files: replay `C:\Users\jcole\multichain-bot\scratchpad\rh_factory\candidates_sl1.jsonl.gz`; live pulls `C:\Users\jcole\multichain-bot\scratchpad\_ptg_live_rh_letrun.json` (and `_rh_letrun_runner`, `_rh_dipall_ctrl`); quarantine target `C:\Users\jcole\multichain-bot\scratchpad\_realize_rows.jsonl.gz`.