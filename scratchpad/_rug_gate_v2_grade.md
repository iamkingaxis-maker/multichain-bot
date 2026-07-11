# Hidden-Supply Rug Gate v2 Grade — deepened cohort + holders-leg re-grade

Date: 2026-07-11 ~23:15 UTC. Follow-up to `_hoodlana_class_gate_grade.md` (80-mint grade).
Question: shipped cell `hidden>=60 AND total_holders<1000` (core/rug_gate.py, env-tunable
RUG_GATE_HIDDEN_MIN / RUG_GATE_HIDDEN_MAX_HOLDERS) — is the holders<1000 leg protecting
winners or letting rugs through? Trigger: LIZARD passed (hidden 72–73%, holders 1241–1394)
and dumped −9.9% on one bot.

**VERDICT: BOTH. Keep the shipped cell exactly as is, and ADD a second branch
`hidden>=80 AND holders<3000`. Do NOT loosen holders<1000 on the 60-branch.**

- The shipped cell caught **zero** of the two real catastrophic rugs that happened TODAY in
  the at-entry stamped tape (bebu −99.2% @ holders 2057; ANSUM −99.5% @ holders 1194 —
  both hidden >= 82, both above the 1000-holder cap).
- But loosening holders to catch them (60/2500) breaches the winner-kill bar: 14.4% (bar <=5%).
- The rugs are separable on the hidden axis instead: **both sat at hidden>=82 while nothing
  else we bought today exceeded 77.6.** `hidden>=80 AND holders<3000` catches 2/2 with
  **0.0% winner-kill on every measured plane** (0/132 alive-winners, 0/265 alive-universe,
  0/5 same-day stamped winners).
- The holders<3000 cap on the 80-branch is mandatory: uncapped `hidden>=80` kills 8.3% of
  alive winners, including +1675%, +3630%, +512%, +207% monsters — all with 10k–180k holders,
  where huge sub-top10 mass = healthy wide distribution, not an insider stash.
- **LIZARD verdict: correctly PASSED.** hidden 73.1 / holders 1241 sits in a cluster with
  same-day winners HAALAND (71.4/2433, +21.9% fwd), Hoppy (71.6/1479, +49.3%), CASHCAT
  (72.3/5214, +12.3%). LIZARD is alive ($23k liq; −61% fwd = bleeder, not rug) and the fleet
  realized **+$258 net** on it across bots. Any cell that blocks LIZARD also kills HAALAND
  and Hoppy. The −9.9% single-bot loss is exit-variance, not a gate miss.
- HOODLANA (hidden 72.84 at entry, holders O(100)) is caught ONLY by the 60/1000 branch —
  that is what the holders<1000 leg is for, and it stays.

## 1. What was built (scratchpad/rug_cohort_v2/)

1. **Universe expanded 198 -> 713 distinct mints** with touched-by-us provenance (35 local
   trade caches: scratchpad `_*trades*.json`, analysis/legacy_data, analysis/winloss_8hr,
   analysis/_prune_mine, analysis/_research, analysis/2026-06/data, + fresh 07-11 API pull).
2. **Two labeling-infrastructure bugs found and fixed in the data** (both also affect the
   existing `rug_cohort_labels.jsonl` 198-row file):
   - **DexScreener 30-mint batch endpoint silently drops pairs.** 11/15 sampled
     "pair-gone => catastrophic" mints resolve fine when queried one-per-request (one was
     ALIVE at +313%). All 420 batch-labeled cat/dead rows were requeried individually:
     **170 labels flipped** (catastrophic 282 -> 158). Anything downstream of batch labels
     (including the old file's 60-catastrophic count) was inflated.
   - **Timestamp bug (adversarial review):** `/api/trades` `time` is ISO-8601; `float()` on
     it made entry_ts None everywhere, so the 24h maturation gate never applied.
     `finalize_labels.py` reparses with ISO support (all 713 mints now have entry_ts),
     corrects 254 first-buy entry-price anchors, re-derives labels, and marks
     `provisional` = labeled <24h after first buy (30 rows, all from today).
3. **labels_final.jsonl (n=701 labeled):** 283 alive / 260 dead / 158 catastrophic
   (mature-only: 260 / 258 / 153).
4. **Current-state holder features** for 441 mints (all 158 catastrophic + 138 alive
   anybot-winners + 145 alive rest) from rugcheck /report, vault-join pool identification
   (markets[].pubkey + liquidityA/B(+Accounts) + our pair_address + Raydium V4 authority —
   the `tag` field is dead). 37/158 catastrophic no longer resolve on rugcheck (400/404).
5. **At-entry stamped slice (gold standard): n=27 mints** bought 07-11 16:50–22:19 UTC with
   entry-time `hidden_supply_share_pct`/`total_holders` stamps (post-405e73e). Forward
   outcome measured from the stamped buy's own entry price. ALL PROVISIONAL (0–6h forward).
6. **Winners:** 15,205 deduped sells across all caches -> 330 anybot / 208 strict
   net-positive mints. Winner-kill cohorts = label-alive AND realized-net-positive
   (rug-scalped-green mints like bebu +$64 realized are correctly EXCLUDED — realized PnL
   cannot label rugs; containment masks severity).

## 2. Post-rug signature (label check only — entry state NOT recoverable for rugs)

Catastrophic cohort current state (n=121 resolving): pool_pct median **89.9%**
(p25 79.2 / p75 95.4); **72% have pool>=80%** — the HOODLANA hidden-supply-dump signature
(supply sold INTO the pool; LP never pulled) dominates the catastrophic class. Post-rug
holders median 801 — post-rug state, unusable as an entry feature (why the catch side is
graded on the stamped slice only).

## 3. Threshold grid

Planes: **S** = at-entry stamped slice n=27 (cat n=2: bebu, ANSUM; same-day winners
fwd>=+20% n=5); **killA/killS** = current-state winner-kill on alive anybot (n=132) /
strict (n=75) winners; **uniBlk** = alive universe block (n=265); LIZ = blocks LIZARD;
HOOD = catches HOODLANA-at-entry (hidden 72.84 chain-verified; holders<1000 inferred).

| cell | S:cat | S:winKill | LIZ | killA% | killS% | uniBlk% | HOOD |
|---|---|---|---|---|---|---|---|
| **hid>=60 hold<1000 (shipped)** | **0/2** | 0/5 | no | **4.5** | 4.0 | 5.3 | YES |
| hid>=60 hold<1500 | 1/2 | 1/5 | YES | 6.1 | 6.7 | 6.8 | YES |
| hid>=60 hold<2000 | 1/2 | 1/5 | YES | 11.4 | 12.0 | 11.3 | YES |
| hid>=60 hold<2500 | 2/2 | 2/5 | YES | **14.4** | 14.7 | 13.6 | YES |
| hid>=65 hold<2500 | 2/2 | 2/5 | YES | 7.6 | 5.3 | 6.8 | YES |
| hid>=70 hold<2500 | 2/2 | **2/5 (HAALAND, Hoppy)** | YES | 1.5 | 0.0 | 2.3 | YES |
| hid>=75 hold<3000 | 2/2 | 0/5 | no | 1.5 | 0.0 | 1.1 | no |
| **hid>=80 hold<3000 (new branch)** | **2/2** | **0/5** | no | **0.0** | **0.0** | **0.0** | no |
| hid>=80 hold<none | 2/2 | 0/5 | no | 8.3 | 6.7 | 4.5 | no |
| hid>=60 hold<none | 2/2 | 3/5 | YES | 51.5 | 40.0 | 40.8 | YES |

(Full 48-cell grid: `rug_cohort_v2/grade_grid.json` / `_grade_out.txt`.)

**Composite recommendation `(hid>=60 & hold<1000) OR (hid>=80 & hold<3000)`:**
S:cat 2/2, stamped winner-kill 0/5, LIZARD passes, HOODLANA caught, killA 4.5% (6/132),
killS 4.0% (3/75) — **both under the <=5% hard bar** — uniBlk 5.3%.

Who the 60/1000 branch kills (current state): 6 mints, 5 of which are token-ret negative
anyway (only J5bTgnS76L +21% is a live winner-kill; 8G5ayEsJF4 reads holders=0 — a rugcheck
glitch; consider a `holders>0` sanity guard so API glitches don't false-block).

Margins on the 80-branch: ANSUM 81.98 (2.0pp above threshold), bebu 88.43 (8.4pp);
nearest non-rug below: 67febu 77.57 (2.4pp below). Thin, n=2 — env-tunable, refine on data.
Holders cap 3000: bebu 2057 leaves 943 margin; first current-state winner above sits at 3059.

## 4. Same-day natural experiment (stamped slice, all provisional 0–6h forward)

4 buys of exactly the shipped-cell class (hid>=60, hold<1000) slipped in pre-gate-deploy
(16:50–17:15): Grumpy +17.6%, KET −1.9%, 67febu +7.1%, Meowpin −43.4% — no rugs in the
blocked class today (block cost is small and noisy, consistent with the 4.5% kill estimate).
The day's two actual rugs both had >1000 holders. Rug rate 2/27 = 7.4% of distinct buys —
consistent with the RH-decode 8%.

## 5. Caveats (honest)

1. **Catch-side n=2** at-entry (+HOODLANA reconstructed = 3). The 80/3000 branch's 2/2 is
   perfect-but-thin. At ~27 stamped mints/day and ~7% catastrophic rate (~2/day),
   **n>=10 catastrophic at-entry stamps ≈ 5 trading days; n>=30 ≈ 15 days.** Re-grade at
   n>=10; thresholds are env/near-env tunable without redeploy churn.
2. **All 27 stamped outcomes are provisional** (0–6h forward, <24h maturation). Re-label
   tomorrow via the fixed scripts/rug_cohort_label.py before treating fwd numbers as final.
3. Winner-kill current-state ≈ entry-state approximation cuts BOTH ways: aged winners have
   grown holders (kill underestimated at entry), and their hidden has distributed (kill
   overestimated on hidden). The stamped slice — where entry is exact — agrees with the
   current-state grade on every recommended cell, which is the best available corroboration.
4. Catastrophic entry-state is unrecoverable post-rug (pool absorbs everything) — used for
   label validation only, never for rule grading.
5. The 80-branch does NOT catch HOODLANA-class (young, hidden ~72, holders O(100)); the
   shipped 60/1000 branch does. Neither blocks LIZARD-class bleeders (hid 70–75, holders
   1200–2500) — that class contains as many winners as bleeders and is not a rug class.

## 6. Implementation note (NOT shipped — working tree untouched in core/)

Second branch = ~8 lines in `core/rug_gate.py` signal 2 (mirror the existing conjunct with
`RUG_GATE_HIDDEN2_MIN=80`, `RUG_GATE_HIDDEN2_MAX_HOLDERS=3000`), plus optional `holders>0`
glitch guard. Requires AxiS approval per live-change rules.

## 7. Artifacts

`scratchpad/rug_cohort_v2/`: PROGRESS.md, mint_universe.json (713), labels_v2.jsonl,
labels_relabel_progress.jsonl, labels_final.jsonl (701), stamped_entries.json (27),
winners.json, features.jsonl + raw/ (441 fetches), features_current.json, grade_grid.json,
_grade_out.txt, scripts (build_universe / label_new / relabel_individual / finalize_labels /
compute_winners / fetch_rugcheck / grade_v2).
