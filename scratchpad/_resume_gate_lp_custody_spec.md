# Resume-Gate Build Spec — LP-Custody Defense + Labeled Rug Cohort

Status: SPEC ONLY, build-gated on AxiS approval. Source: forensics verdict
(scratchpad/_rug_forensics.md, code-verified 2026-07-11). The actor-behavior gate FAILED the
catch-vs-winner-kill<=5% bar; this is the mechanism-aligned replacement.

## The confirmed failure (why HOODLANA was invisible)
`feeds/dev_wallet.py::_identify_dev_wallet` returns the largest NON-program holder as "dev".
PumpSwap AMM `pAMMBay…` is in KNOWN_PROGRAMS, so HOODLANA's 98.78%-in-pool supply was skipped;
a tiny retail holder was read as "dev" -> dev_pct under threshold -> all creator-dump gates passed.
The LP (the loaded gun) is excluded BY DESIGN. LP-pull rugs are structurally invisible to dev_wallet.py.

## ⚠️ THE TRAP that blocks retroactive validation (capture before anyone crawls)
A rugged token's LP is ALREADY DRAINED by the time we query it now. So CURRENT-STATE LP concentration
reads BACKWARDS for the dead cohort (post-rug pool is empty, looks "safe"). The loaded-gun LP state is
only observable AT ENTRY. => You CANNOT grade an LP-custody gate on existing touched-mint data.
Any RPC crawl over historical rugs measures post-rug state = meaningless. This is precisely why the
labeled cohort (#2) — entry-time snapshots going forward — is the ENABLER, not optional.

## Build #2 (ENABLER, do first): entry-time actor+LP snapshot cohort
At EVERY entry (paper + live), persist a snapshot to a durable jsonl:
  - mint, ts, our_entry_price
  - LP CUSTODY: pool vault holder(s) + % of supply in pool (READ the pAMMBay/Raydium/Orca vaults —
    the exact holders dev_wallet.py currently SKIPS), mintLP-null flag (markets[].mintLP), SOL-side
    reserve size, quote-reserve.
  - dev/creator: largest non-program holder + %, deployer addr, mint/freeze authority states.
  - top10 concentration (already have).
Then a follow-up job re-reads each mint at +1h/+6h/+24h/+48h and labels: RUGGED (LP drained / -80%+ /
liq<$5k) vs SURVIVED. Target n>=30 rugged before grading any gate.
Production change (entry path) => needs AxiS approval. Small + append-only + fail-open.

## Build #1 (the actual defense, grade on #2's data once n>=30)
Augment dev_wallet.py to SEE LP custody instead of excluding it:
  - Compute pool_pct_of_supply (do NOT skip pAMMBay/Raydium/Orca — read them).
  - Signal candidates to grade: pool holds >X% of supply AND mintLP null (unlocked LP) AND thin
    SOL-side reserve = LP-pull-ready. Grade catch (on rugged cohort) vs winner-kill (on survived
    cohort) — winner-kill<=5% hard bar.
  - Keep the existing dev-dump gate for the retail-dev case; ADD the LP read, don't replace.
Fail-open on RPC miss (never block scan). Env-flag shadow-first before enforce.

## Labeled cohort v1 results (2026-07-11, scripts/rug_cohort_label.py — 198 mints labeled)
104 alive / 60 catastrophic / 34 dead. SEPARATION IS REAL and INVERTED vs naive intuition:
catastrophic median top10=31.6 / top1=11.7 vs alive top10=59.2 / top1=20.7 — the dump class
enters LOW-visible-concentration (supply hidden below the top10 line; HOODLANA top10=14.71 fits).
lp_locked (100) and rugcheck_score (1.0) identical in both cohorts = useless for this class.

Threshold sweep (block if top10 < T), catch on catastrophic n=52 / kill on realized winners n=74:
  T=15: catch 28.8%  winner-kill 8.1%   T=20: 40.4% / 10.8%   T=25: 44.2% / 10.8%
=> NO top10-only threshold clears winner-kill<=5% while catching HOODLANA. A naive low-conc
BLOCK is REJECTED. The joint rule needs shoulder_11_20_pct (fat shoulder = insider split vs
thin tail = genuine distribution) — stamping since 405e73e; labeler joins features to outcomes
at each session ritual (`python scripts/rug_cohort_label.py`, needs DASHBOARD_USER/PASSWORD env).
Interim lever available to AxiS (not shipped — young-lane sizing is mid-A/B): 0.5x de-size on
top10<25 young entries — kills nothing, halves the cap-hitting tail on ~44% of the class.

## What does NOT clear the bar (settled — do not rebuild)
- Actor-identity blacklist: fresh-deployer kill 13%, sniped-own-launch kill 40% (both >5%, non-discriminative).
- Broad rug screen: containment makes avg rug EV-neutral (DEAD -3.04% vs ALIVE -3.61%); only the
  cap-hitting TAIL matters.
- Retroactive LP crawl: the trap above.

## Resume criteria (unchanged framing, new content)
PAPER_MODE=false only after: LP-custody gate graded on labeled cohort (n>=30 rugged) meeting
catch-cap-hitting-class + winner-kill<=5%, AxiS approval, pre-live invariants.
Related: memory project-live-paused-hoodlana (verdict), feeds/dev_wallet.py, scratchpad/rug_forensics/.
