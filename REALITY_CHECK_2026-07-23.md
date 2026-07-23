# REALITY-CHECK MEMO — 2026-07-23

All five findings reproduce on the fresh deduped local ledgers (20:05, ~3h old; fleet API last event 01:12 UTC — live pipe, not stale). Every load-bearing number holds or comes in slightly worse. Here is the memo.

---

# REALITY-CHECK MEMO — RH new seats (career mode)

**Verified 2026-07-23 01:24 UTC** on deduped local ledgers (`scratchpad/_fid_ledgers.json`, `scratchpad/_dead_tokens.json`). Local cache carried ~20-40% duplicate sell rows per bot — all numbers below are post-dedup and match the authoritative API signs. CHAIN=truth. **Bottom line up front: none of the three new seats is a live-or-scale candidate. All three are textbook replay-illusion traps.**

---

## 1. Per new seat — the one number that decides it

| Seat | Verdict | Decisive number | Action |
|---|---|---|---|
| **rh_young_proven** | **ILLUSION** | **−$78 realized / −$0.78/e / 38% win, red on BOTH live days (07‑22 −$47, 07‑23 −$31), 0/100 dead‑token buys** | **KILL** |
| **rh_established_dip** | **TOO‑EARLY (breakeven clone)** | **+$0.027/e (≈$0), drop‑top‑2 = −$0.37/e; loses to its own aged parent on identical tokens** | **KILL as configured** |
| **rh_mfrveto_ab** | **INERT SENSOR / redundant clone** | **mfr_active never once >0 across 3,293 fleet buys → veto has NEVER fired; −$0.47/e ≈ dipall_ctrl −$0.45/e** | **CUT from A/B** |

Fidelity is clean on all three (0–1 dead‑token buys, no phantom‑win rescue available) — so these are **durability/exit illusions, not dead‑token illusions**, which is the harder trap to see.

---

## 2. rh_young_proven — root cause + exact fix (PRIORITY)

**Root cause: EXIT‑LADDER INVERSION. Not a bad day, not weak entries.**

The "+1.8% median / 48% win" that justified the seat was **raw ret20 (forward return), never run through the bot's own SL1 ladder.** The selection is genuinely real — proven‑strength cells median **+1.15%** vs thin‑dip **−4.08%** — but the ladder destroys it:
- tp1+6 / tp2+14 + pre‑arm trail (arm+5 / fire peak−2) **cap 91% of the raw winners** → winners collapse to a **+2.36% mean**, while losers still bleed **−4.5%** after sl1 and the **−10 hard stop**.
- A ~50%‑win coin‑flip with capped upside and −6/−10 downside is **negative EV**. Cross‑day realized: **scalp_sl1 −1.64% / aged_sl1 −1.02%, negative on all 12 meaningful‑n days.**
- young_proven's **−10 hard stop is TIGHTER than the −15** both replay ladders used → its true realized number is **worse than −1.6%.**
- Compounded live: the population skewed **younger than the measured cell** (median 0.6h, every entry <6h, none aged) straight into the **14.1% <1h rug band** (vs aged **0.2%**).

Scrubbing cannot save it: it is −$78 with **zero** dead‑token buys, and scrubbing only ever removes booked wins — it cannot flip red→green.

**Exact fix: KILL. Do not build another young‑strength seat.** Pre‑registered rule for any retry: **re‑mine cells on LADDER‑REALIZED $ (aged_sl1/scalp_sl1 outcome dicts in `scratchpad/rh_factory/candidates_sl1.jsonl.gz`), NEVER on raw ret20/mfe.** That search already points at aged, so there is no young cell to rescue. This is the exits‑are‑the‑lever / AUC‑ceiling doctrine in one incident: **we mined an entry cell against forward return again.**

---

## 3. AGED family (aged_deep / aged_hold / aged_derisk) — verdict + honest $/day ceiling

**NOT the career go‑live candidate. Hold paper only. Do NOT scale capital.**

Decisive numbers (combined deep+derisk+hold):
- Lifetime realized **+$45.19**, but **07‑22 alone = +$60.58; every other day combined = −$15.39.** 100%+ of the edge is one day.
- **Top‑5 tokens = $51.84 (>100% of total) → drop‑top‑5‑tokens is NEGATIVE.**
- **Freshest independent day 07‑23 = RED** (~−$22 combined: deep −$11.30, hold −$0.39, derisk −$6.97, pro_agedflush −$3.93).
- **aged_derisk is net‑negative outright (−$7.58)** — it's a drag, drop it.
- On the one green day the **entry tape is blind to outcome**: the losers had *higher* netflow and buy_share than the winners. Profit was an exit‑side mean‑reversion lottery, undetectable at buy time.
- The +$42 leaderboard number is further flattered by **~$60 of unrealized open drawdown** (14 of 25 open positions are 07‑22 deep‑dip entries still under water).

**Honest $/day ceiling at $25 stakes: ≈ $0, tilted negative.** Ex‑outlier expectancy is ~−$0.5/e; at ~20 entries/day that's roughly **−$10/day**. Even the flattered lifetime +$0.31/e × 20 is only **+$6/day, and only on a favorable mean‑reversion tape you cannot detect in advance.** There is no reliable positive run‑rate. To be fair (not doom): the live‑matching `aged` ladder has a *positive* replay median (+2.33% / 58.7% win, mean −0.48%), so this is **marginal‑negative / coin‑flip, not structurally doomed** — but "marginal coin‑flip" is not something you fund bills with. Hold and accrue.

---

## 4. mfr_veto sensor — fix it or abandon it?

**ABANDON as wired. Do NOT recalibrate the 8% threshold.**

Decisive: **mfr_active = 0/None on all 3,293 fleet buys.** It has never fired and structurally cannot — `dead_block` refuses entries into labeled‑dead tokens, which starves the numerator (a token can only count as "recent entry into dead" if it *wasn't yet labeled* at buy = pure lookahead). All 307 historical threshold‑fires sit on 07‑13/16/21 under the **final static dead set** = hindsight; the live stream never fires. The bot is byte‑identical to dipall_ctrl and always will be.

**If a manufacturer veto is still wanted, rebuild on the LEADING dist_active first‑sell signal** (memory: 93% dump‑catch at 0s), weighted by capital/per‑lane, not the lagging fleet‑wide sellability sweep. **BUT — hard flag: dist_active is ALSO dark (0/None on all 3,293 buys).** The get‑ahead stamp produces zero signal today. **No manufacturer/distributor veto is buildable until the tape‑recorder / entry↔tape join bug (dark since 07‑18) is fixed.** That pipe repair — not another A/B arm — is the prerequisite.

---

## 5. RANKED next actions (each with its pre-registered bar)

**KILL now — verified dead‑weight, zero info loss:**
1. **rh_young_proven** — illusion; exits invert a real selection. *Bar to ever revisit: a cell positive on ladder‑realized $ at n≥30 with drop‑top‑2 ≥ 0. None exists in the young band → effectively permanent.*
2. **rh_f_popret** — #1 fleet bleeder, **−$206** (paper understates 2× via phantom/dead wins), ~no green days.
3. **rh_f_arc_scalp** — **−$110**, −$104 of it on a single day, ~no green days.
4. **rh_mfrveto_ab** — inert clone; cut from the A/B, keep dipall_ctrl as the baseline. *Cannot accrue n≥30 live vetoes — the veto never fires.*

**FIX — the single highest‑leverage item on the board (a pipe, not a strategy):**
5. **Tape‑recorder / entry↔tape join bug (dark since 07‑18).** mfr_active AND dist_active are 0/None on 3,293/3,293 buys. Until this is repaired, every manufacturer/distributor sensor is untestable — including the only get‑ahead thread memory says actually works (distributor first‑sell). *Bar: dist_active reads nonzero on live buys and matches the on‑chain first‑sell within the latency budget.*

**HOLD‑AND‑ACCRUE — paper only, no capital:**
6. **AGED family (aged_deep + aged_hold; drop aged_derisk, net‑negative).** *Pre‑registered go‑live bar: green on n≥20 across MULTIPLE independent days AND survives drop‑top‑5‑TOKENS positive. Current: 1‑of‑4 days, drop‑top‑5 negative. Fund nothing until it clears BOTH.*
7. **rh_established_dip** — a ~breakeven, strictly‑worse clone of the aged family; not worth its own seat. **Do NOT "deepen the dip trigger"** — that targets a correlate (dip depth). The real config difference is the **SL1_DERISK ladder it runs (−$1.88/e, 10.5% win over 19 fires) that the aged family doesn't.** If kept, the only honest experiment is: **turn SL1_DERISK OFF and check whether it converges to aged.** Otherwise fold it into aged.

**KEEP as controls (no action):** dipall_ctrl / dipall_knife baselines.

---

## Replay‑illusion trap — flag

All three new seats are the same trap we keep documenting:
- **young_proven** = graded on **raw ret20 (forward return)** → AUC‑ceiling / exits‑are‑the‑lever.
- **established / aged** "replay +$0.46/e green" = a **narrow overfit slice**; the broad replay cohort is mean‑negative.
- **Every "day‑1 live" and "aged 4‑days‑green" claim** = **single‑day (07‑22) survivorship** — the exact confound the young_proven verdict correctly rejects, applied symmetrically here.

**Career‑mode distance‑to‑revenue: the $25 pro‑seat gate is met by ZERO current seats.** The fastest honest path to a real number is fixing the tape pipe so the distributor first‑sell get‑ahead signal can finally be tested — not shipping another entry cell against forward return.

**Relevant files:** `C:\Users\jcole\multichain-bot\scratchpad\_fid_ledgers.json` (full fleet ledgers, dedup required), `C:\Users\jcole\multichain-bot\scratchpad\_dead_tokens.json` (28 terminal‑rug routes), `C:\Users\jcole\multichain-bot\scratchpad\rh_factory\candidates_sl1.jsonl.gz` (64k replay with ladder‑realized outcome dicts — the correct surface to re‑mine on).