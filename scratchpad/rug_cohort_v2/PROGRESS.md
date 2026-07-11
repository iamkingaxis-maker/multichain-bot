# rug_cohort_v2 progress (2026-07-11/12)

Goal: deepen catastrophic-rug cohort, re-grade hidden-supply gate
(hidden>=60 AND holders<1000) — is the holders<1000 leg right?

## Done
1. mint_universe.json — 713 distinct mints from 35 local caches + today's API pull
   (506 new vs the 198 in rug_cohort_labels.jsonl; 494 with entry_price).
2. _trades_today.json — fresh 5000-row API pull 07-11 ~22:30 UTC.
   stamped_entries.json — 22 mints with AT-ENTRY hidden_supply_share_pct/total_holders
   stamps (post-405e73e). KEY EARLY FINDING: 2 same-day catastrophics
   (bebu hid=88.4 hold=2057 ret=-99.2; ANSUM hid=82.2 hold=1553 ret=-99.5) BOTH
   MISSED by holders<1000 leg. LIZARD hid=73.1 hold=1241 ret=-46. But winners
   HAALAND hid=71.4 hold=2433 ret=+126, Hoppy hid=71.6 hold=1479 ret=+37.
3. labels_v2.jsonl — 503 new mints labeled via DS 30-batch.
   BUG FOUND: DS batch endpoint DROPS pairs — 11/15 sampled "pair-gone
   catastrophic" resolve individually (one at +313%). Batch labels unreliable.
4. relabel_individual.py RUNNING (background bclvq3pvy) — requeries all 420
   cat/dead rows one-per-request -> labels_relabel_progress.jsonl (checkpoint)
   -> labels_final.jsonl. NOTE: original 198-file labels used same batch method;
   its cat/dead rows are being requeried too.
5. winners.json — 330 anybot / 208 strict winner mints from 15205 deduped sells.

## Coordinator update (07-11 ~22:35 UTC)
Timestamp bug (adversarial review): /api/trades `time` is ISO string; float()
raised -> entry_ts None everywhere -> 24h maturation gate never applied in
scripts/rug_cohort_label.py (now fixed in working tree). Response here:
finalize_labels.py reparses all caches with ISO support, corrects first-buy
entry_price, re-derives labels from stored price_now/liq_now, and flags
`provisional` = entry_ts unknown OR labeled <24h after first buy. Today's
stamped slice (buys 16:50-22:19 UTC 07-11) is inherently provisional —
outcomes are 0-6h forward; the report must say so.

## State after resume (07-11 ~22:50 UTC)
- relabel DONE 420/420: batch->individual flips 170 (cat 282->158!). labels_final.jsonl:
  283 alive / 260 dead / 158 catastrophic (mature: 260/258/153; provisional 30).
- finalize_labels.py applied ISO-ts fix: all 713 mints now have entry_ts;
  254 entry_price corrections; provisional flags set.
- stamped_entries.json now carries fwd_ret/label_fwd = forward return FROM THE
  STAMPED BUY (not historical first buy). Slice n=27:
  cat = bebu (hid 88.4, hold 2057) + ANSUM (hid 82.0, hold 1194) — both fwd -99,
  both MISSED by shipped cell (holders>=1000). Next-highest hidden = 67febu 77.6
  (+7.1 fwd). LIZARD 73.1/1241 fwd -61 but ALIVE ($23k liq) — bleeder, not rug.
  4 gate-would-block buys happened pre-gate (Grumpy/KET/67febu/Meowpin):
  fwd +17.6/-1.9/+7.1/-43.4 — no rugs in the blocked class today.
- fetch_rugcheck.py RUNNING (441 targets: 158 cat + 138 alive-winners + 145 alive-rest).

## COMPLETE (07-11 ~23:20 UTC)
7. fetch DONE: 441 targets, 386 raw reports (37 cat + 6 alive no longer resolve on rugcheck).
8. grade_v2.py run -> grade_grid.json + _grade_out.txt.
9. VERDICT in scratchpad/_rug_gate_v2_grade.md: keep hid>=60&hold<1000 (HOODLANA branch,
   killA 4.5%/killS 4.0% <=5% bar), ADD hid>=80&hold<3000 (bebu/ANSUM branch, 0.0% kill all
   planes, 2/2 catch). Do NOT loosen holders<1000. LIZARD = correct pass (bleeder, not rug;
   fleet realized +$258). Catch n=2 at-entry — re-grade at n>=10 (~5 trading days).
