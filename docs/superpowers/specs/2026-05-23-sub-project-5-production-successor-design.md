# Sub-Project 5: Production Successor Framework — Design Spec

**Status:** Awaiting user spec review
**Date:** 2026-05-23
**Parent project:** Multi-bot fleet (Sub-project 5 of 5 — final)
**Depends on:** Sub-projects 1-4 shipped (49-bot fleet running, SP4 attribution tooling live)

---

## Goal

Build the **tooling and process to safely promote `champion_proposal` to `baseline_v1`** in production. This sub-project ships the framework; the actual cutover happens later, gated on real fleet data (≥7 days of bake time so champion synthesis has enough sample).

After this sub-project, the cutover ceremony is reduced to a few `python scripts/sp5_*.py` invocations + a railway deploy. Rollback is a single script.

---

## Architecture

**5 deliverables**, all in `scripts/sp5_*.py` + one runbook in `docs/superpowers/runbooks/`:

| Component | Purpose |
|---|---|
| `scripts/sp5_validate_champion.py` | Gate check: does champion beat baseline by enough to promote? |
| `scripts/sp5_cutover.py` | Atomic swap: baseline_v1.json ← champion fields. Backs up baseline first. |
| `scripts/sp5_rollback.py` | Restore baseline_v1.json from backup. Reverse the cutover. |
| `scripts/sp5_phantom_champion.py` | Extend `scripts/live_forward_test.py` to mirror champion's decision rules. |
| `docs/superpowers/runbooks/champion-promotion.md` | Step-by-step cutover ceremony runbook. |

No new BotConfig fields. No dashboard changes. No deploy automation (cutover is deliberate, human-triggered).

---

## Validation gates (sp5_validate_champion.py)

Champion is **READY TO PROMOTE** if ALL gates pass:

| Gate | Threshold | Rationale |
|---|---|---|
| **Sample size** | `champion.sample_n ≥ 30 AND baseline.sample_n ≥ 30` | Need enough trades to compute meaningful metrics |
| **$/tr delta** | `champion.pnl_per_trade ≥ baseline.pnl_per_trade + 0.10` | Champion must show ≥ $0.10/tr improvement |
| **Drawdown** | `champion.worst_trade_usd ≥ baseline.worst_trade_usd × 1.2` | Tail risk not materially worse (1.2× tolerance) |
| **Throughput** | `champion.sample_n ≥ baseline.sample_n × 0.5` | Didn't kill too many entries (champion still fires) |
| **Hold-out check** | Split champion's trades into earlier 70% (training) and later 30% (hold-out). Hold-out $/tr must be > 0. | Champion's edge isn't a one-off regime fluke |

If all pass → output `reports/sp5_validate_champion.md` with **PASS** verdict + supporting numbers.
If any fail → output **FAIL** report listing the specific gates that failed and the values that caused failure.

Exit code 0 if PASS, 1 if FAIL — usable in CI/scripts.

---

## Cutover flow (sp5_cutover.py)

```
python scripts/sp5_cutover.py --confirm
```

1. Read current `config/bots/baseline_v1.json`
2. Read current `config/bots/champion_proposal.json`
3. Verify `sp5_validate_champion.py` exit code == 0 (won't proceed otherwise)
4. Backup: write current baseline to `config/bots/baseline_v1.json.pre-cutover-<YYYY-MM-DD-HHMMSS>`
5. Atomic swap: overwrite baseline_v1.json with champion fields, but PRESERVE:
   - `bot_id = "baseline_v1"` (don't rename — keeps trade history continuous)
   - `display_name = "Baseline (post-cutover {date})"`
   - `enabled = true`
   - All other fields copy from champion
6. Mark champion_proposal.json `enabled = false` (it's now inactive; the live champion IS baseline_v1)
7. Git commit with descriptive message + commit SHA tag
8. Print next step: "Run `railway up --detach` to deploy"

**Without `--confirm` flag:** dry-run mode — print what would happen but don't write files.

---

## Rollback flow (sp5_rollback.py)

```
python scripts/sp5_rollback.py [--backup <timestamp>]
```

1. Default: find the most recent `baseline_v1.json.pre-cutover-*` backup
2. With `--backup`: use the specified timestamp
3. Restore: copy backup → `baseline_v1.json`
4. Print pre-rollback hash of baseline_v1.json so the user can verify the change
5. Git commit with rollback message
6. Print next step: "Run `railway up --detach` to redeploy old baseline"

The backup files are NEVER auto-deleted — they accumulate across cutovers. (`.gitignore`'s `.pre-migrate` pattern keeps them out of git but local files persist.)

---

## Phantom champion mirror (sp5_phantom_champion.py)

`scripts/live_forward_test.py` currently mirrors only `baseline_v1`'s decision rules (the canonical phantom-parity layer). After cutover, the new baseline_v1 IS the former champion — so phantom needs to mirror champion's specific filter set + threshold values.

This script is **invoked once during cutover** to re-generate the phantom's filter logic to match the new baseline. It:

1. Reads `config/bots/baseline_v1.json` (post-cutover, which is the champion)
2. Identifies which filters are in `filters_disabled` — these get inverted in phantom (always PASS)
3. Identifies which threshold values differ from phantom's hardcoded defaults — emits warnings about which need manual adjustment
4. Writes a new `live_forward_test.py.cutover-<date>` showing the diff, plus a markdown report

The script doesn't auto-modify `live_forward_test.py` (too risky); it generates a diff for the user to review + apply manually. The phantom layer can lag the live bot by a few days without harm — phantom is read-only validation.

---

## Runbook (docs/superpowers/runbooks/champion-promotion.md)

Step-by-step ceremony for the human operator:

```
## Champion promotion ceremony

### Prerequisites
- [ ] 49-bot fleet has been running ≥ 7 days
- [ ] baseline_v1 has ≥ 30 paired trades since deploy
- [ ] champion_proposal has been generated (run sp4_champion_synthesis.py)

### Step 1: Sanity-check the proposal
- [ ] Read reports/champion_synthesis.md
- [ ] Read config/bots/champion_proposal.json
- [ ] If any choices look wrong, edit champion_proposal.json before proceeding

### Step 2: Enable champion in production to gather forward data
- [ ] In config/bots/champion_proposal.json, set "enabled": true
- [ ] git commit + push + railway up --detach
- [ ] Wait ≥ 48h for champion to accumulate ≥ 30 trades

### Step 3: Validate
- [ ] python scripts/sp5_validate_champion.py
  - If FAIL: review reasons, either wait for more data or edit champion config + redeploy
  - If PASS: proceed to step 4

### Step 4: Cutover (dry-run)
- [ ] python scripts/sp5_cutover.py           # NO --confirm — prints what would happen
- [ ] Review the diff between current baseline and the new baseline

### Step 5: Cutover (live)
- [ ] python scripts/sp5_cutover.py --confirm
- [ ] git push origin master
- [ ] railway up --detach

### Step 6: Phantom alignment
- [ ] python scripts/sp5_phantom_champion.py
- [ ] Review the suggested phantom diff
- [ ] Manually apply phantom changes if needed (low priority)

### Step 7: Watch for 24-48h
- [ ] Dashboard: confirm baseline_v1's metrics are at-or-above pre-cutover levels
- [ ] If baseline_v1 underperforms or crashes:
  - python scripts/sp5_rollback.py
  - git push + railway up
  - Investigate why champion didn't work in actual production
```

---

## Tests

### Unit tests
- `tests/test_sp5_validate_champion.py` — synthetic metrics, verify gate logic
- `tests/test_sp5_cutover.py` — synthetic baseline + champion JSONs, verify backup created, fields swapped correctly, original bot_id preserved
- `tests/test_sp5_rollback.py` — synthetic backup file, verify restore works

### Integration test
- `tests/test_sp5_cutover_dry_run.py` — full cutover dry-run on copies of real config files; verify no files are actually modified in --dry-run mode

---

## What this sub-project does NOT do

- **Actually run the cutover** — data-gated. Tooling exists; human triggers it.
- **Auto-promote on schedule** — too risky.
- **Champion validation via stat-sig tests** — sample sizes too thin. Gates are practical thresholds.
- **Phantom parity for all 49 bots** — only mirrors what becomes the new baseline post-cutover.
- **Live-mode cutover** — paper-mode only.
- **Multi-champion tournament** — one champion at a time.
- **Auto-rollback on drawdown** — too aggressive. Human decision.

---

## Risks

1. **Champion underperforms post-cutover.** Validation gates use historical data; live behavior may differ. **Mitigation:** rollback script + backup file. Rollback is one command + redeploy.

2. **Backup file not preserved.** If git or filesystem loses the backup, rollback fails. **Mitigation:** backup files are written to `config/bots/` (committed to git) and named with date+time. Even if locally deleted, git history has them.

3. **Champion config has subtle issues.** Greedy synthesis may pick incompatible field combinations. **Mitigation:** `BotConfig.__post_init__` invariants will raise at load time. Validation script also runs end-to-end import test before signing off.

4. **The cutover ceremony is non-trivial.** Multiple steps, multiple commits. **Mitigation:** runbook documents every step. Each script has `--help` for usage.

5. **Phantom parity drift.** After cutover, phantom may briefly mirror old behavior. **Mitigation:** acceptable — phantom is observational. Update at human convenience.

---

## Approval gate

Before writing the implementation plan:
1. Are the 5 validation gates the right ones? (sample size 30, $/tr delta 0.10, drawdown 1.2×, throughput 0.5×, hold-out positive)
2. Is the cutover ceremony correctly designed? (dry-run → validate → live cutover → watch → rollback path)
3. Is the runbook complete or should it have more steps?
4. Approval to proceed to writing-plans?
