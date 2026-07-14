# GIVEBACK / POST-TP1 EXIT LEVER — quantification on OUR OWN realized fills
2026-07-04 12:17 UTC · window 2026-06-27T12:17 → 2026-07-04T12:17 (7.0 days) · ANALYSIS ONLY, nothing shipped

Data: `/api/bots/{id}/trades?limit=1000` for badday_flush (control), badday_allday, badday_flush_rsi_ab, badday_young_absorb.
522 sell legs in window → 12 scrubbed (SCRUB RULE: pnl>0 & hold<10s) → 510 legs → 389 rounds → **265 per-token deduped rounds**
(dedup key = address + entry price; control-bot round preferred; raw numbers also shown).
Round = all sell legs sharing (bot, address, entry_price); realized = Σ(leg pnl × sell_fraction); peak = max leg peak_pnl_pct.

Baseline machinery LIVE in this window (matters for interpretation):
- BREAKEVEN_LOCK peak≥3 → **enforce** fleet-wide since ~07-01/02 (lock exits visible on all 4 bots from 07-02).
- Control ladder: TP1 +6 / 75%, then trail 2pp. badday_allday = wideexit arm: TP1 +13 / 30%, TP2 +30, trail 6pp.
- All PROFIT exits go through the fresh-re-tick reprice gate → measured fill slip on fires (decision-pnl − realized, median):
  **TP1 +1.9pp, trail +2.0pp, TP2 +2.5pp, lock +0.9pp** (n=106/65/46/29). The −2pp "pessimistic fill" below is therefore
  the MEASURED fill reality, not a stress haircut.

---

## Q1 — GIVEBACK SIZE

Rounds with peak ≥ 3 (deduped n=100): **total giveback (peak − realized) = 634 pp over 7d = 91 pp/day.**
Raw (all bots, no dedup, n=145): 999 pp = 143 pp/day.

| peak band | n | giveback | pp/day |
|---|---|---|---|
| 3–6  | 14 | 132 pp | 18.8 |
| 6–9  | 31 | 149 pp | 21.3 |
| 9–13 | 24 | 150 pp | 21.4 |
| 13+  | 31 | 203 pp | 29.0 |

- Pre-TP1 **band** (peak<6): 132 pp = **21%**. Post (peak≥6): 502 pp = 79%.
- But by TP1 **fill**: rounds where TP1 never filled carry **290 pp = 46%** of all giveback. That is the actionable half —
  the other half sits inside TP2/trail rounds where 75% was already banked and the trail is working as designed.
- By final exit reason: trail 198 pp (n=42) · breakeven-lock 186 pp (n=17) · TP2 145 pp (n=32) · MAE-floor 105 pp (n=9).
- Day concentration flag: **07-02 alone = 219 pp (35%)**; 06-29/30 nearly zero. One hot tape drives a third of the pool.
- Absolute ceiling (every peak≥3 round exits at peak−2, optimistic fills, zero runner cost — physically unreachable):
  446 pp/7d = **64 pp/day**. So even a perfect trail cannot touch ~30% of the measured giveback.

### Structural finding that reframes the lever
On the **control ladder (TP1=+6) there are ZERO rounds that peaked ≥6 without filling TP1** (n=0). The NEIL/FABLE class —
"peaked above meaningful profit, round-tripped to a lock/stop" — lives almost entirely in the **allday wideexit ladder's
6→13 transit window**: n=12 unfilled rounds, **159 pp/wk giveback** (NEIL +9.3→−1.8, FABLE +6.7→−6.3 both badday_allday;
SEMAN +4.3→−2.2 young_absorb, sub-TP1 by any ladder). The wideexit re-arm bought TP2 upside by opening an unguarded
+6..+13 zone where the only protection is the breakeven-lock at ~−2 (or a gap to −6).

Known cases verified in data: NEIL peak +9.3 → lock −1.8 ✓ · SEMAN +4.3 → lock −2.2 ✓ · FABLE +6.7 → lock fired at −6.27
(gap-through, saved_pp=0 — no lock threshold could have done better on that path) ✓.

---

## Q2 — POLICY REPLAY

**Bias statement (applies to every trail number):** replay is from (entry, peak, exit) triples without the intra-round
price path. It CANNOT see a trail firing on an intermediate wick before the final peak, so it (i) overstates trail exit
levels on recovered rounds and (ii) shows ZERO runner kills. Both fills are computed: OPT = exit exactly at peak−X;
PESS = peak−X−2 (the measured ~2pp reprice slip). Runner cost is bounded separately (Q3).

### (a) Peak-armed trail, full fleet, grid {X=1.5,2,3} × {arm Y=3,4,5,6} — delta vs current realized, deduped, 7d

| X | Y | fires | rec OPT | rec PESS | pess pp/day |
|---|---|---|---|---|---|
| 1.5 | 3 | 164 | 374 | 189 | 27.0 |
| 2 | 3 | 107 | 333 | **233** | 33.3 |
| 2 | 4 | 102 | 307 | 216 | 30.9 |
| 2 | 5 | 98 | 270 | 187 | 26.8 |
| 3 | 4 | 63 | 269 | 202 | 28.9 |
| 2 | 6 | 95 | 228 | 151 | 21.6 |

Recovery is spread across bands (X=2,Y=4 PESS: 65/63/69/19 pp in 3-6/6-9/9-13/13+).
BUT — see Q3 — the full-fleet variant's net goes ≈0-to-negative once realistic wick rates are applied. Not shippable.

### (a') Pre-TP1-ONLY trail (stands down once TP1 fills; post-TP1 the existing 2pp/6pp trail owns the exit)
TP1-unfilled rounds n=191; **zero rounds with realized ≥ +10 among them** → visible runner cost is structurally zero;
all runner risk is invisible pre-TP1 wicks on eventual TP1-hitters (n=74, 774 pp realized at stake).

| X | Y | fires | rec OPT | rec PESS | pess pp/day |
|---|---|---|---|---|---|
| 2 | 3 | 24 | 241 | 193 | 27.5 |
| 2 | 4 | 19 | 214 | 176 | 25.1 |
| **2** | **5** | **15** | **177** | **147** | **21.0** |
| 2 | 6 | 12 | 135 | 111 | 15.8 |

### (b) Winner-decode scratch (exit if <+1% at t=30min, TP1 unhit)
Fires n=12 (knee-to-+3 filter applied). pnl@30min is unobservable from triples → benefit is a bracket:
floor 0 · exit@0 mid **48 pp/7d (6.9/day)** · ceiling(+1) 60 pp. Overlaps the already-live `never_runner`
(peak<3, ~44min floor, 13 fires in window). Weak, unresolvable, partially shipped already. Pass.

### (c) BE-lock variants Y∈{5,6,7}, slip 1–2pp — **REFUTED as a lever**
Naive replay shows 18–63 pp/7d recovered, but it is **illusory**: lock@3 is ALREADY enforced, median live lock fill −2.7,
and **7 of 17 lock rounds gapped through to −3.6..−6.5** (FABLE −6.3 fired AT −6.27 — the lock saw nothing better).
The legs the replay "recovers" are exactly the gap-throughs where an exit at −slip was never available at any threshold.
Raising Y from 3 also surrenders the 3→Y saves already banked (path-observed: 23 saves, +22 pp). Keep lock@3, no variant.
This is consistent with the winner-decode refutation of BE-lock@+4.

---

## Q3 — RUNNER COST

Path-observed evidence (giveback_shadow, live on flush since 06-20, allday since 07-01, rsi_ab 06-29, young 07-03):
among shadow-live TP1-hitters (n=66 deduped), **3 (5%) did a FULL round-trip to ≤0 pre-TP1 before running**:
traindog (peak+3.3→−0.3→realized +4.0), #fairs (+7.2→−1.9→+12.8), bull (+7.4→−3.0→+24.8). **THIN n=3.**
Any pre-TP1 trail (even the loosest) cuts all three: observed-kill lower bound ≈ 30–36 pp/7d.
A 2pp wick fires far more often than a zero-cross, so true kills ≥ this. Empirical anchor for wick frequency:
post-TP1 with the live 2pp trail, **52% of TP1-hit rounds trail out before TP2** (34/66) — 2pp wicks are the norm, not the tail.

| policy | recovered PESS (7d) | runner kills (7d) | net (7d) |
|---|---|---|---|
| trail X=2,Y=3 full fleet | 233 | harsh bound 444 (n=18 runners); @52% wick ≈ 231 | **≈ +2 … −211** — sign not established |
| pre-TP1 trail X=2,Y=5 fleet | 147 | observed lower bound 31 (n=2); scaled @30/50/70% wick = 143/239/334 | **+116 … −187** — sign flips inside plausible wick range |
| **pre-TP1 trail X=2,Y=5, ALLDAY-scoped (6→13 guard)** | **111 (n=12 fires; floor ≈60 if every fire lands at first-arm +1)** | runner pool only n=8 (122 pp); observed transit zero-cross 2/8=25% (kill 31 pp); @52% wick ≈50; @**100%** wick ≈96 | **+61 … +15 (mid case; worst-floor-rec × max-kill ≈ −36)** |
| scratch-30min | 0–60 (mid 48) | ≈0 visible (TP1-hitters excluded by construction) | 0…+48, unresolvable |
| BE-lock Y=5/6/7 | illusory (gap-through) | #fairs/bull class kills −51 pp at Y=3-like arming | negative-to-zero — refuted |

The allday-scoped variant is the only configuration whose pessimistic net stays non-negative across essentially the whole
wick-rate range, because its kill pool is small (8 runners) and its recovery pool is deep (12 rounds averaging −2..−6
realized after peaking +6..+13). **All allday numbers are thin: n=12 recovery rounds, n=8 runners, 3-day shadow window.**

---

## Q4 — RECOMMENDATION

**Winning policy: pre-TP1 peak-armed trail, X=2pp below peak, arm Y=+5, scoped to the wideexit ladder (badday_allday),
standing down at TP1 fill.** Pessimistic-fill net **≈ +15…+61 pp/7d ≈ +2…+9 token-pp/day** (mid ~+8), vs runner
forfeit 31 pp observed / ≤96 pp at a 100% wick assumption on 8 runners.

**Ship as: env-flag SHADOW, not enforce. Not per-bot config yet.**
1. The decisive unknown — pre-TP1 2pp-wick rate on eventual TP1-hitters — flips the fleet-wide sign between 30% and 70%
   and cannot be resolved from (entry, peak, exit) triples. It IS resolvable from path data in ~1–2 weeks of tape.
2. Concretely: add a `giveback_trail_shadow` stamped per-sell exactly like `giveback_shadow_*`/`bel_shadow_*`
   (fired / pnl_at_fire / peak_at_fire / secs), armed pre-TP1 at peak≥5, fire at pnl ≤ peak−2, record-only. The existing
   `TRAIL_REPRICE_MODE=shadow` machinery (feeds/dip_scanner.py ~6814, core/fast_watch.py:705) is post-TP1-only and its
   `trail_reprice_shadow_*` state-blob fields are NOT surfaced on API sells (verified: 0 of 875 sells) — surface them too.
3. Decision gate: at n≥30 shadow-live TP1-hitters, if (kills observed at 2pp wick) < 50% of shadow-recovered pp under
   −2pp fills, promote allday-scoped enforce; fleet-wide stays shadow until separately cleared.

**Family estimate cross-check:** the +300–450 token-pp pool is REAL as a pool (634 pp/wk deduped giveback, 91 pp/day) but
it is NOT mechanically recoverable: measured ~2pp reprice slip on every profit fire, gap-throughs (7/17 locks), and
pre-TP1 wick kills shrink the defensible net to **~15–60 pp/wk (allday-scoped) up to ~+150 pp/wk fleet-wide only if the
observed 5% zero-cross kill rate holds** — roughly an order of magnitude below the headline. The single biggest identified
sub-hole is a design artifact, not a missing trail: the wideexit 6→13 unguarded transit (159 pp/wk, n=12).

Thin-n flags: kill evidence n=3 path-observations; allday shadow live only since 07-01; young_absorb shadow <1 day;
07-02 tape = 35% of the week's giveback; scratch-rule bracket unresolvable. All grid numbers are 7-day, single regime.

Working files: scratchpad/_gb_{bot}.json (raw pulls), _gb_rounds_dedup.json (round dataset),
_gb_analysis_out.txt / _gb_policy_out.txt / _gb_policy2_out.txt / _gb_policy3_out.txt (full tables).
