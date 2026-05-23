# Champion Promotion Ceremony

This runbook describes the steps to promote `champion_proposal` -> `baseline_v1` in production.

## Prerequisites

- [ ] 49-bot fleet has been running >= 7 days
- [ ] baseline_v1 has >= 30 paired trades since fleet deploy
- [ ] champion_proposal has been generated (run scripts/sp4_champion_synthesis.py)

## Step 1: Sanity-check the proposal

Open and read both:
- reports/champion_synthesis.md
- config/bots/champion_proposal.json

If any choices look wrong, edit champion_proposal.json directly before proceeding.

## Step 2: Enable champion in production to gather forward data

```bash
# Edit champion_proposal.json: "enabled": false -> true
git add config/bots/champion_proposal.json
git commit -m "config: enable champion_proposal for forward validation"
git push origin master
MSYS_NO_PATHCONV=1 railway up --detach
```

Wait >= 48h for the champion bot to accumulate >= 30 trades.

## Step 3: Validate

```bash
python scripts/sp5_validate_champion.py
```

- PASS: proceed to Step 4
- FAIL: review reports/sp5_validate_champion.md, fix or wait

## Step 4: Cutover dry-run

```bash
python scripts/sp5_cutover.py
```

Prints what the new baseline_v1.json would look like. NO files modified.

## Step 5: Cutover (live)

```bash
python scripts/sp5_cutover.py --confirm
git add config/bots/baseline_v1.json config/bots/champion_proposal.json config/bots/baseline_v1.json.pre-cutover-*
git commit -m "chore: cutover baseline_v1 to champion"
git push origin master
MSYS_NO_PATHCONV=1 railway up --detach
```

## Step 6: Phantom alignment (optional, can lag)

```bash
python scripts/sp5_phantom_champion.py
```

Review reports/sp5_phantom_champion_diff.md and apply changes to scripts/live_forward_test.py manually.

## Step 7: Watch for 24-48h

- Dashboard /api/bots returns 49 bots; baseline_v1 metrics at-or-above pre-cutover levels
- No error spikes in Railway logs

## Rollback (if things break)

```bash
python scripts/sp5_rollback.py
git add config/bots/baseline_v1.json
git commit -m "rollback: revert baseline_v1 cutover"
git push origin master
MSYS_NO_PATHCONV=1 railway up --detach
```
