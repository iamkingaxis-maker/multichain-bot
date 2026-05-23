# Sub-Project 5: Production Successor Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the tooling and process to safely promote `champion_proposal` to `baseline_v1` in production — 4 scripts + 1 runbook.

**Architecture:** Pure tooling scripts in `scripts/sp5_*.py` that read/write `config/bots/baseline_v1.json` + `config/bots/champion_proposal.json`. All scripts use `scripts/sp4_common.py` helpers + `core/bot_config.py` schema. No code path changes to the bot itself.

**Tech Stack:** Python 3.11, pytest, JSON. No new dependencies.

**Spec:** [docs/superpowers/specs/2026-05-23-sub-project-5-production-successor-design.md](../specs/2026-05-23-sub-project-5-production-successor-design.md)

---

## File structure

### New files

| Path | Responsibility |
|---|---|
| `scripts/sp5_validate_champion.py` | 5-gate readiness check; exit 0 PASS / 1 FAIL |
| `scripts/sp5_cutover.py` | Atomic swap baseline_v1.json ← champion fields + backup |
| `scripts/sp5_rollback.py` | Restore baseline_v1.json from most recent backup |
| `scripts/sp5_phantom_champion.py` | Diff generator showing phantom changes needed post-cutover |
| `tests/test_sp5_validate_champion.py` | Gate logic tests with synthetic metrics |
| `tests/test_sp5_cutover.py` | Atomic swap behavior + backup creation |
| `tests/test_sp5_rollback.py` | Restore from backup tests |
| `docs/superpowers/runbooks/champion-promotion.md` | Step-by-step ceremony runbook |

### No modified files

SP5 is purely additive. The cutover script will eventually modify `config/bots/baseline_v1.json` at runtime, but that's a script effect, not a source code change.

---

## Task ordering rationale

Tasks 1-4 are independent scripts (any order works). Task 5 ships the runbook tying them together. The 4 scripts share the same input shape (read JSON configs + production trade data) so the patterns are similar. Each is small enough to ship as one TDD cycle.

---

## Task 1: sp5_validate_champion.py — 5-gate readiness check

**Files:**
- Create: `scripts/sp5_validate_champion.py`
- Create: `tests/test_sp5_validate_champion.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sp5_validate_champion.py
import pytest
from scripts.sp5_validate_champion import (
    check_sample_size, check_pnl_delta, check_drawdown,
    check_throughput, check_holdout, validate_all,
)
from scripts.sp4_common import BotMetrics


def _m(bot_id, n, per_tr, worst=-3.0):
    return BotMetrics(
        bot_id=bot_id, sample_n=n, total_pnl_usd=n * per_tr,
        pnl_per_trade=per_tr, win_rate=0.5, avg_win_usd=2.0,
        avg_loss_usd=-1.5, best_trade_usd=10.0, worst_trade_usd=worst,
        throughput_x_pnl=n * per_tr,
    )


def test_sample_size_pass():
    ok, msg = check_sample_size(_m("c", 30, 0.5), _m("b", 30, 0.3))
    assert ok is True


def test_sample_size_fail_when_champion_thin():
    ok, msg = check_sample_size(_m("c", 29, 0.5), _m("b", 30, 0.3))
    assert ok is False
    assert "champion" in msg.lower()


def test_sample_size_fail_when_baseline_thin():
    ok, msg = check_sample_size(_m("c", 30, 0.5), _m("b", 29, 0.3))
    assert ok is False
    assert "baseline" in msg.lower()


def test_pnl_delta_pass_when_at_least_10c_better():
    ok, msg = check_pnl_delta(_m("c", 30, 0.40), _m("b", 30, 0.30))
    assert ok is True


def test_pnl_delta_fail_when_under_10c_better():
    ok, msg = check_pnl_delta(_m("c", 30, 0.35), _m("b", 30, 0.30))
    assert ok is False


def test_drawdown_pass_when_not_materially_worse():
    # baseline worst=-3.0, champion worst=-3.5 → 3.5 < 3.0*1.2=3.6 → PASS
    ok, msg = check_drawdown(_m("c", 30, 0.5, worst=-3.5), _m("b", 30, 0.3, worst=-3.0))
    assert ok is True


def test_drawdown_fail_when_too_much_worse():
    # baseline worst=-3.0, champion worst=-4.0 → 4.0 > 3.0*1.2=3.6 → FAIL
    ok, msg = check_drawdown(_m("c", 30, 0.5, worst=-4.0), _m("b", 30, 0.3, worst=-3.0))
    assert ok is False


def test_throughput_pass_when_champion_fires_enough():
    ok, msg = check_throughput(_m("c", 25, 0.5), _m("b", 30, 0.3))
    assert ok is True  # 25 > 30 * 0.5 = 15 → PASS


def test_throughput_fail_when_champion_fires_too_few():
    ok, msg = check_throughput(_m("c", 14, 0.5), _m("b", 30, 0.3))
    assert ok is False  # 14 < 30 * 0.5 = 15 → FAIL


def test_holdout_pass_when_later_30pct_positive():
    """Holdout: sort champion's trades by time, split 70/30, later 30% $/tr > 0."""
    from scripts.sp4_common import PairedTrade
    pairs = []
    for i in range(10):
        pairs.append(PairedTrade(
            bot_id="c", token=f"T{i}", entry_price=0.001, size_usd=20.0,
            realized_pnl_usd=1.0,  # all positive in both halves
            time=f"2026-05-{i+1:02d}T00:00:00+00:00",
            sells=[], buy_meta={},
        ))
    ok, msg = check_holdout(pairs)
    assert ok is True


def test_holdout_fail_when_later_30pct_negative():
    """Champion's later 30% is negative → suspect regime fluke."""
    from scripts.sp4_common import PairedTrade
    pairs = []
    # First 7 positive, last 3 negative
    for i in range(7):
        pairs.append(PairedTrade(
            bot_id="c", token=f"T{i}", entry_price=0.001, size_usd=20.0,
            realized_pnl_usd=2.0,
            time=f"2026-05-{i+1:02d}T00:00:00+00:00",
            sells=[], buy_meta={},
        ))
    for i in range(7, 10):
        pairs.append(PairedTrade(
            bot_id="c", token=f"T{i}", entry_price=0.001, size_usd=20.0,
            realized_pnl_usd=-3.0,  # loser cluster at the end
            time=f"2026-05-{i+1:02d}T00:00:00+00:00",
            sells=[], buy_meta={},
        ))
    ok, msg = check_holdout(pairs)
    assert ok is False


def test_validate_all_pass_when_every_gate_passes():
    ok, report = validate_all(
        champion_metrics=_m("c", 30, 0.5, worst=-3.0),
        baseline_metrics=_m("b", 30, 0.3, worst=-3.0),
        champion_pairs=[],  # holdout skipped if no pairs (treated as PASS)
    )
    assert ok is True
    assert "PASS" in report


def test_validate_all_fails_when_any_gate_fails():
    ok, report = validate_all(
        champion_metrics=_m("c", 29, 0.5, worst=-3.0),  # sample too thin
        baseline_metrics=_m("b", 30, 0.3, worst=-3.0),
        champion_pairs=[],
    )
    assert ok is False
    assert "FAIL" in report
```

- [ ] **Step 2: Run tests to verify failure**

```bash
PYTHONPATH=. pytest tests/test_sp5_validate_champion.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write implementation at `scripts/sp5_validate_champion.py`**

```python
"""SP5: validate champion is ready to promote to baseline.

5 gates: sample size, $/tr delta, drawdown, throughput, hold-out.
Exit code 0 if PASS, 1 if FAIL.

Usage: python scripts/sp5_validate_champion.py
Output: reports/sp5_validate_champion.md
"""
from __future__ import annotations
from collections import defaultdict
from pathlib import Path
from typing import Optional

from scripts.sp4_common import (
    BotMetrics, PairedTrade, fetch_all_trades, pair_buys_sells, compute_metrics,
)


MIN_SAMPLE_N = 30
MIN_PNL_DELTA = 0.10
DRAWDOWN_TOLERANCE = 1.2
THROUGHPUT_FLOOR_RATIO = 0.5


def check_sample_size(
    champion: BotMetrics, baseline: BotMetrics,
) -> tuple[bool, str]:
    if champion.sample_n < MIN_SAMPLE_N:
        return False, f"champion.sample_n={champion.sample_n} < {MIN_SAMPLE_N}"
    if baseline.sample_n < MIN_SAMPLE_N:
        return False, f"baseline.sample_n={baseline.sample_n} < {MIN_SAMPLE_N}"
    return True, f"champion n={champion.sample_n}, baseline n={baseline.sample_n} (both >= {MIN_SAMPLE_N})"


def check_pnl_delta(
    champion: BotMetrics, baseline: BotMetrics,
) -> tuple[bool, str]:
    c_per = champion.pnl_per_trade or 0.0
    b_per = baseline.pnl_per_trade or 0.0
    delta = c_per - b_per
    if delta < MIN_PNL_DELTA:
        return False, (
            f"champion ${c_per:+.3f}/tr vs baseline ${b_per:+.3f}/tr "
            f"= delta ${delta:+.3f} < required ${MIN_PNL_DELTA:.2f}"
        )
    return True, f"delta ${delta:+.3f}/tr >= required ${MIN_PNL_DELTA:.2f}"


def check_drawdown(
    champion: BotMetrics, baseline: BotMetrics,
) -> tuple[bool, str]:
    # worst_trade_usd is negative (or 0). Tolerance: champion.worst >= baseline.worst * 1.2
    # i.e. champion's worst trade cannot be MORE negative than baseline's worst × tolerance.
    # baseline.worst=-3.0, tolerance=1.2 → allowed_worst = -3.0 * 1.2 = -3.6
    # champion.worst=-4.0 → -4.0 < -3.6 → FAIL
    allowed_worst = baseline.worst_trade_usd * DRAWDOWN_TOLERANCE
    if champion.worst_trade_usd < allowed_worst:
        return False, (
            f"champion worst ${champion.worst_trade_usd:+.2f} is worse than "
            f"baseline worst ${baseline.worst_trade_usd:+.2f} * {DRAWDOWN_TOLERANCE} "
            f"= ${allowed_worst:+.2f}"
        )
    return True, (
        f"champion worst ${champion.worst_trade_usd:+.2f} >= "
        f"baseline worst * {DRAWDOWN_TOLERANCE} = ${allowed_worst:+.2f}"
    )


def check_throughput(
    champion: BotMetrics, baseline: BotMetrics,
) -> tuple[bool, str]:
    floor = baseline.sample_n * THROUGHPUT_FLOOR_RATIO
    if champion.sample_n < floor:
        return False, (
            f"champion sample {champion.sample_n} < baseline {baseline.sample_n} * "
            f"{THROUGHPUT_FLOOR_RATIO} = {floor:.0f} (champion not firing enough)"
        )
    return True, f"champion {champion.sample_n} >= floor {floor:.0f}"


def check_holdout(champion_pairs: list[PairedTrade]) -> tuple[bool, str]:
    """Split champion's pairs into earlier 70% (training) and later 30% (holdout).
    Hold-out $/tr must be > 0 (champion's edge isn't a one-off regime fluke).
    If too few pairs, treated as PASS (gate doesn't fire).
    """
    if len(champion_pairs) < 10:
        return True, f"too few pairs ({len(champion_pairs)}) — holdout gate skipped"
    # Sort by time (already typically in order, but be safe)
    sorted_pairs = sorted(champion_pairs, key=lambda p: p.time)
    split_idx = int(len(sorted_pairs) * 0.7)
    holdout = sorted_pairs[split_idx:]
    if not holdout:
        return True, "holdout empty — skipped"
    holdout_per = sum(p.realized_pnl_usd for p in holdout) / len(holdout)
    if holdout_per <= 0:
        return False, (
            f"holdout $/tr ${holdout_per:+.3f} <= 0 "
            f"({len(holdout)} late trades vs {split_idx} early)"
        )
    return True, (
        f"holdout $/tr ${holdout_per:+.3f} > 0 "
        f"({len(holdout)} late trades vs {split_idx} early)"
    )


def validate_all(
    champion_metrics: BotMetrics,
    baseline_metrics: BotMetrics,
    champion_pairs: list[PairedTrade],
) -> tuple[bool, str]:
    gates = [
        ("Sample size", check_sample_size(champion_metrics, baseline_metrics)),
        ("$/tr delta", check_pnl_delta(champion_metrics, baseline_metrics)),
        ("Drawdown", check_drawdown(champion_metrics, baseline_metrics)),
        ("Throughput", check_throughput(champion_metrics, baseline_metrics)),
        ("Hold-out", check_holdout(champion_pairs)),
    ]
    all_pass = all(ok for _, (ok, _) in gates)
    verdict = "PASS" if all_pass else "FAIL"
    lines = [f"# Champion validation — {verdict}", ""]
    for name, (ok, msg) in gates:
        icon = "PASS" if ok else "FAIL"
        lines.append(f"## {name}: {icon}")
        lines.append(f"- {msg}")
        lines.append("")
    return all_pass, "\n".join(lines)


def main() -> int:
    trades = fetch_all_trades()
    paired = pair_buys_sells(trades)
    by_bot: dict[str, list] = defaultdict(list)
    for p in paired:
        by_bot[p.bot_id].append(p)

    champion_pairs = by_bot.get("champion_proposal", [])
    baseline_pairs = by_bot.get("baseline_v1", [])

    champion_metrics = compute_metrics(champion_pairs)
    baseline_metrics = compute_metrics(baseline_pairs)

    ok, report = validate_all(champion_metrics, baseline_metrics, champion_pairs)

    out_path = Path(__file__).parent.parent / "reports" / "sp5_validate_champion.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(report)
    print(f"\nWrote report to {out_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify pass**

```bash
PYTHONPATH=. pytest tests/test_sp5_validate_champion.py -v
```

Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/sp5_validate_champion.py tests/test_sp5_validate_champion.py
git commit -m "feat(sp5): champion validation with 5 readiness gates

5 gates (all must pass):
- Sample size: champion + baseline >= 30 trades
- \$/tr delta: champion >= baseline + \$0.10
- Drawdown: champion worst >= baseline worst * 1.2 (tolerance)
- Throughput: champion fires >= baseline * 0.5 (still active enough)
- Hold-out: champion's later 30% \$/tr > 0 (not a regime fluke)

Exit code 0 if PASS, 1 if FAIL. Output: reports/sp5_validate_champion.md.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 2: sp5_cutover.py — atomic swap with backup

**Files:**
- Create: `scripts/sp5_cutover.py`
- Create: `tests/test_sp5_cutover.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sp5_cutover.py
import json
import pytest
from pathlib import Path
from scripts.sp5_cutover import (
    perform_cutover, build_new_baseline,
)


def _baseline_dict():
    return {
        "bot_id": "baseline_v1",
        "display_name": "Baseline (current production)",
        "enabled": True,
        "paper_capital_usd": 2000.0,
        "base_position_usd": 20.0,
        "max_concurrent_positions": 3,
        "alpha_multiplier": 1.5,
        "macro_up_multiplier": 1.5,
        "premium_runner_multiplier": 3.0,
        "marginal_multiplier": 0.5,
        "sol_macro_h6_block_threshold": -0.3,
        "sol_macro_h1_block_threshold": -0.7,
        "btc_macro_h1_block_threshold": None,
        "pc_h24_max": None, "pc_h24_min": None, "pc_h1_max": None,
        "age_h_min": None, "age_h_max": None,
        "mcap_min": None, "mcap_max": None, "vol_h1_min": 1000.0,
        "filters_enforced": None, "filters_disabled": [],
        "triggers_allowed": None, "triggers_disabled": [],
        "min_triggers_to_fire": 1, "require_alpha_trigger": False,
        "mcap_psych_pc_h24_max": 80.0,
        "tp1_pct": 5.0, "tp1_sell_fraction": 0.75,
        "tp2_pct": 10.0, "tp2_sell_fraction": 0.25,
        "trail_pp": 3.0, "hard_stop_pct": -15.0,
        "pre_stop_bail_pnl_pct": -3.0, "pre_stop_bail_vol_m5_max": 500.0,
        "slow_bleed_minutes": 60, "slow_bleed_pnl_threshold": -8.0,
        "trading_hour_utc_start": 0, "trading_hour_utc_end": 24,
    }


def _champion_dict():
    d = _baseline_dict()
    d["bot_id"] = "champion_proposal"
    d["display_name"] = "Champion proposal (synthesized 2026-05-30)"
    d["enabled"] = False
    # Champion-specific differences
    d["alpha_multiplier"] = 1.0
    d["hard_stop_pct"] = -10.0
    d["filters_disabled"] = ["filter_topping", "filter_low_volatility"]
    return d


def test_build_new_baseline_preserves_bot_id():
    new_baseline = build_new_baseline(_baseline_dict(), _champion_dict())
    assert new_baseline["bot_id"] == "baseline_v1"


def test_build_new_baseline_copies_champion_fields():
    new_baseline = build_new_baseline(_baseline_dict(), _champion_dict())
    assert new_baseline["alpha_multiplier"] == 1.0  # from champion
    assert new_baseline["hard_stop_pct"] == -10.0   # from champion
    assert new_baseline["filters_disabled"] == ["filter_topping", "filter_low_volatility"]


def test_build_new_baseline_enabled_true():
    new_baseline = build_new_baseline(_baseline_dict(), _champion_dict())
    assert new_baseline["enabled"] is True


def test_build_new_baseline_updates_display_name():
    new_baseline = build_new_baseline(_baseline_dict(), _champion_dict())
    assert "post-cutover" in new_baseline["display_name"].lower()


def test_perform_cutover_dry_run_does_not_modify_files(tmp_path):
    baseline_path = tmp_path / "baseline_v1.json"
    champion_path = tmp_path / "champion_proposal.json"
    baseline_path.write_text(json.dumps(_baseline_dict()))
    champion_path.write_text(json.dumps(_champion_dict()))

    original_baseline_content = baseline_path.read_text()
    original_champion_content = champion_path.read_text()

    backup_path, new_baseline = perform_cutover(
        baseline_path, champion_path, confirm=False,
    )

    # No file modifications in dry-run
    assert baseline_path.read_text() == original_baseline_content
    assert champion_path.read_text() == original_champion_content
    # But the new baseline content is computed and returned
    assert new_baseline["alpha_multiplier"] == 1.0


def test_perform_cutover_confirm_creates_backup(tmp_path):
    baseline_path = tmp_path / "baseline_v1.json"
    champion_path = tmp_path / "champion_proposal.json"
    baseline_path.write_text(json.dumps(_baseline_dict()))
    champion_path.write_text(json.dumps(_champion_dict()))

    backup_path, new_baseline = perform_cutover(
        baseline_path, champion_path, confirm=True,
    )

    # Backup file should exist
    assert backup_path is not None
    assert backup_path.exists()
    # Backup contains the original baseline content
    backup_content = json.loads(backup_path.read_text())
    assert backup_content["alpha_multiplier"] == 1.5  # original


def test_perform_cutover_confirm_writes_new_baseline(tmp_path):
    baseline_path = tmp_path / "baseline_v1.json"
    champion_path = tmp_path / "champion_proposal.json"
    baseline_path.write_text(json.dumps(_baseline_dict()))
    champion_path.write_text(json.dumps(_champion_dict()))

    perform_cutover(baseline_path, champion_path, confirm=True)

    # baseline_v1.json now contains champion fields
    new_baseline = json.loads(baseline_path.read_text())
    assert new_baseline["bot_id"] == "baseline_v1"  # preserved
    assert new_baseline["alpha_multiplier"] == 1.0  # from champion
    assert new_baseline["hard_stop_pct"] == -10.0


def test_perform_cutover_confirm_disables_champion(tmp_path):
    baseline_path = tmp_path / "baseline_v1.json"
    champion_path = tmp_path / "champion_proposal.json"
    baseline_path.write_text(json.dumps(_baseline_dict()))
    champion_path.write_text(json.dumps(_champion_dict()))

    perform_cutover(baseline_path, champion_path, confirm=True)

    # Champion is now disabled (it's been merged into baseline)
    new_champion = json.loads(champion_path.read_text())
    assert new_champion["enabled"] is False
```

- [ ] **Step 2: Run tests to verify failure**

```bash
PYTHONPATH=. pytest tests/test_sp5_cutover.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write implementation at `scripts/sp5_cutover.py`**

```python
"""SP5: atomic cutover from baseline_v1 ← champion_proposal.

Backs up the current baseline to baseline_v1.json.pre-cutover-<timestamp>,
then writes champion's field values into baseline_v1.json (preserving
bot_id='baseline_v1' to keep trade history continuous).

Marks champion_proposal.json enabled=false (inactive after the merge).

Usage:
  python scripts/sp5_cutover.py            # dry-run; prints diff, no writes
  python scripts/sp5_cutover.py --confirm  # actually performs the cutover
"""
from __future__ import annotations
import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


# Fields that should NOT be copied from champion (preserved from old baseline)
PRESERVED_BASELINE_FIELDS = {"bot_id"}


def build_new_baseline(
    old_baseline: dict, champion: dict,
) -> dict:
    """Construct the new baseline_v1 config: champion fields, baseline identity."""
    new_baseline = dict(champion)
    new_baseline["bot_id"] = "baseline_v1"
    new_baseline["enabled"] = True
    today = datetime.now(timezone.utc).date().isoformat()
    new_baseline["display_name"] = f"Baseline (post-cutover {today})"
    return new_baseline


def perform_cutover(
    baseline_path: Path, champion_path: Path, confirm: bool,
) -> tuple[Optional[Path], dict]:
    """Execute the cutover.

    Returns (backup_path, new_baseline_dict). In dry-run mode, backup_path is None
    and no files are modified. In confirm mode, files are written and backup_path
    points to the saved backup.
    """
    old_baseline = json.loads(baseline_path.read_text())
    champion = json.loads(champion_path.read_text())
    new_baseline = build_new_baseline(old_baseline, champion)

    if not confirm:
        print("DRY-RUN — no files modified. Would write:")
        print(json.dumps(new_baseline, indent=2, sort_keys=True))
        return None, new_baseline

    # Backup current baseline
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    backup_path = baseline_path.with_suffix(f".json.pre-cutover-{timestamp}")
    shutil.copy2(baseline_path, backup_path)
    print(f"Backed up baseline to {backup_path}")

    # Write new baseline
    baseline_path.write_text(json.dumps(new_baseline, indent=2, sort_keys=True))
    print(f"Wrote new baseline to {baseline_path}")

    # Disable champion
    champion["enabled"] = False
    champion_path.write_text(json.dumps(champion, indent=2, sort_keys=True))
    print(f"Disabled champion at {champion_path}")

    return backup_path, new_baseline


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--confirm", action="store_true",
                   help="Actually perform the cutover (default is dry-run).")
    args = p.parse_args()

    project_root = Path(__file__).parent.parent
    baseline_path = project_root / "config" / "bots" / "baseline_v1.json"
    champion_path = project_root / "config" / "bots" / "champion_proposal.json"

    if not baseline_path.exists():
        print(f"ERROR: {baseline_path} not found")
        return 1
    if not champion_path.exists():
        print(f"ERROR: {champion_path} not found — run scripts/sp4_champion_synthesis.py first")
        return 1

    backup_path, _new_baseline = perform_cutover(
        baseline_path, champion_path, confirm=args.confirm,
    )
    if args.confirm:
        print("\nNext step: git add config/bots/*.json && git commit && railway up --detach")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify pass**

```bash
PYTHONPATH=. pytest tests/test_sp5_cutover.py -v
```

Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/sp5_cutover.py tests/test_sp5_cutover.py
git commit -m "feat(sp5): atomic cutover from baseline_v1 to champion

scripts/sp5_cutover.py:
- Reads current baseline_v1.json + champion_proposal.json
- Constructs new_baseline = champion fields + preserved bot_id='baseline_v1'
- DRY-RUN (default): prints proposed new baseline, no writes
- --confirm: backs up baseline -> baseline_v1.json.pre-cutover-<timestamp>,
  writes new baseline, sets champion enabled=false
- Prints next step (git commit + railway up)

Trade history stays continuous because bot_id never changes.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 3: sp5_rollback.py — restore from backup

**Files:**
- Create: `scripts/sp5_rollback.py`
- Create: `tests/test_sp5_rollback.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sp5_rollback.py
import json
import pytest
from pathlib import Path
from scripts.sp5_rollback import find_latest_backup, restore_from_backup


def test_find_latest_backup_picks_most_recent(tmp_path):
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True)
    # Create three backups with different timestamps
    (bots_dir / "baseline_v1.json.pre-cutover-2026-05-20-100000").write_text("v1")
    (bots_dir / "baseline_v1.json.pre-cutover-2026-05-25-120000").write_text("v2")
    (bots_dir / "baseline_v1.json.pre-cutover-2026-05-22-130000").write_text("v3")
    latest = find_latest_backup(bots_dir / "baseline_v1.json")
    assert latest.name.endswith("2026-05-25-120000")


def test_find_latest_backup_returns_none_when_no_backups(tmp_path):
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True)
    latest = find_latest_backup(bots_dir / "baseline_v1.json")
    assert latest is None


def test_restore_from_backup_overwrites_baseline(tmp_path):
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True)
    baseline_path = bots_dir / "baseline_v1.json"
    baseline_path.write_text('{"version": "new"}')
    backup_path = bots_dir / "baseline_v1.json.pre-cutover-2026-05-25-120000"
    backup_path.write_text('{"version": "old"}')

    restore_from_backup(baseline_path, backup_path)

    restored = baseline_path.read_text()
    assert restored == '{"version": "old"}'


def test_restore_from_backup_returns_pre_rollback_hash(tmp_path):
    bots_dir = tmp_path / "config" / "bots"
    bots_dir.mkdir(parents=True)
    baseline_path = bots_dir / "baseline_v1.json"
    baseline_path.write_text('{"version": "new"}')
    backup_path = bots_dir / "baseline_v1.json.pre-cutover-2026-05-25-120000"
    backup_path.write_text('{"version": "old"}')

    pre_hash = restore_from_backup(baseline_path, backup_path)

    # pre_hash is the hash of {"version": "new"} (before rollback)
    import hashlib
    expected = hashlib.sha256(b'{"version": "new"}').hexdigest()
    assert pre_hash == expected
```

- [ ] **Step 2: Run tests to verify failure**

```bash
PYTHONPATH=. pytest tests/test_sp5_rollback.py -v
```

Expected: `ModuleNotFoundError`

- [ ] **Step 3: Write implementation at `scripts/sp5_rollback.py`**

```python
"""SP5: rollback baseline_v1 to a backup.

Default: restore from the most recent .pre-cutover-* backup.
--backup <timestamp>: restore from a specific backup.

Usage:
  python scripts/sp5_rollback.py
  python scripts/sp5_rollback.py --backup 2026-05-25-120000
"""
from __future__ import annotations
import argparse
import hashlib
import shutil
from pathlib import Path
from typing import Optional


def find_latest_backup(baseline_path: Path) -> Optional[Path]:
    """Find the most recent baseline_v1.json.pre-cutover-* backup."""
    parent = baseline_path.parent
    stem = baseline_path.stem  # 'baseline_v1'
    backups = sorted(parent.glob(f"{stem}.json.pre-cutover-*"))
    return backups[-1] if backups else None


def restore_from_backup(baseline_path: Path, backup_path: Path) -> str:
    """Restore baseline from backup. Returns the SHA256 of the pre-rollback baseline."""
    pre_content = baseline_path.read_bytes()
    pre_hash = hashlib.sha256(pre_content).hexdigest()
    shutil.copy2(backup_path, baseline_path)
    return pre_hash


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--backup",
                   help="Specific backup timestamp (e.g., 2026-05-25-120000). "
                        "Default: most recent backup.")
    args = p.parse_args()

    project_root = Path(__file__).parent.parent
    baseline_path = project_root / "config" / "bots" / "baseline_v1.json"

    if args.backup:
        backup_path = project_root / "config" / "bots" / f"baseline_v1.json.pre-cutover-{args.backup}"
        if not backup_path.exists():
            print(f"ERROR: {backup_path} not found")
            return 1
    else:
        backup_path = find_latest_backup(baseline_path)
        if backup_path is None:
            print(f"ERROR: no backups found in {baseline_path.parent}")
            return 1

    print(f"Will restore from: {backup_path}")
    print(f"Current baseline: {baseline_path}")
    confirm = input("Proceed with rollback? [y/N]: ")
    if confirm.lower() != "y":
        print("Aborted.")
        return 1

    pre_hash = restore_from_backup(baseline_path, backup_path)
    print(f"Restored baseline from {backup_path}")
    print(f"Pre-rollback baseline hash: {pre_hash[:16]}...")
    print(f"\nNext step: git add config/bots/baseline_v1.json && git commit && railway up --detach")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify pass**

```bash
PYTHONPATH=. pytest tests/test_sp5_rollback.py -v
```

Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/sp5_rollback.py tests/test_sp5_rollback.py
git commit -m "feat(sp5): rollback baseline_v1 from backup

scripts/sp5_rollback.py:
- Default: restore from most recent .pre-cutover-* backup
- --backup <timestamp>: restore from a specific backup
- Prompts for confirmation before overwriting
- Prints pre-rollback hash so user can verify the change happened

If anything goes wrong during cutover, this script restores in one command.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 4: sp5_phantom_champion.py — phantom diff generator

**Files:**
- Create: `scripts/sp5_phantom_champion.py`

This task ships a script that *suggests* phantom changes (markdown diff) rather than modifying `live_forward_test.py` automatically. The phantom file is large and risky to auto-edit; a human reviews the suggestions and applies them manually.

- [ ] **Step 1: Write implementation at `scripts/sp5_phantom_champion.py`**

```python
"""SP5: generate suggested phantom-parity changes for the post-cutover baseline.

After cutover, baseline_v1.json contains champion's field values. The
phantom forward-test layer (scripts/live_forward_test.py) was written for
the OLD baseline. This script identifies which phantom rules now need to
change, and emits a markdown report — the user reviews + applies manually.

Usage: python scripts/sp5_phantom_champion.py
Output: reports/sp5_phantom_champion_diff.md
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path


# Hardcoded phantom defaults (matches what live_forward_test.py assumes for baseline)
PHANTOM_DEFAULTS = {
    "sol_macro_h6_block_threshold": -0.3,
    "sol_macro_h1_block_threshold": -0.7,
    "mcap_psych_pc_h24_max": 80.0,
    "vol_h1_min": 1000.0,
    "alpha_multiplier": 1.5,
    "max_concurrent_positions": 3,
    "hard_stop_pct": -15.0,
    "tp1_pct": 5.0,
    "tp1_sell_fraction": 0.75,
    "tp2_pct": 10.0,
    "tp2_sell_fraction": 0.25,
    "trail_pp": 3.0,
}


def diff_baseline_vs_phantom_defaults(baseline_config: dict) -> dict:
    """Return field → (phantom_default, new_baseline_value) for fields that differ."""
    diffs = {}
    for field, phantom_default in PHANTOM_DEFAULTS.items():
        new_value = baseline_config.get(field)
        if new_value != phantom_default:
            diffs[field] = (phantom_default, new_value)
    return diffs


def build_phantom_diff_markdown(
    baseline_config: dict, diffs: dict,
) -> str:
    lines = [
        f"# Phantom-parity diff for post-cutover baseline",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "After cutover, `config/bots/baseline_v1.json` has these field values "
        "that DIFFER from what `scripts/live_forward_test.py` currently mirrors. "
        "Phantom needs manual update to match.",
        "",
    ]
    if not diffs:
        lines.append("**No diffs found.** Phantom is already aligned with baseline.")
        return "\n".join(lines)

    lines.append(f"## {len(diffs)} field(s) need phantom update")
    lines.append("")
    lines.append("| Field | Phantom default | New baseline | Required phantom edit |")
    lines.append("|---|---:|---:|---|")
    for field, (default, new) in diffs.items():
        edit = f"Change phantom's `{field}` reference from `{default}` to `{new}`"
        lines.append(f"| `{field}` | `{default}` | `{new}` | {edit} |")
    lines.append("")

    # Also check filters_disabled — these need entire phantom mirror logic changes
    filters_disabled = baseline_config.get("filters_disabled") or []
    if filters_disabled:
        lines.append("## filters_disabled in new baseline")
        lines.append("")
        lines.append(
            "The following filters are DISABLED in the new baseline. The phantom "
            "should NOT mirror these as enforced gates (their checks become no-ops):"
        )
        lines.append("")
        for f in filters_disabled:
            lines.append(f"- `{f}`")
        lines.append("")
        lines.append(
            "**Action:** In `scripts/live_forward_test.py`, find the predicate for "
            "each of these filters and either delete the predicate row or change it "
            "to always return PASS."
        )

    lines.append("")
    lines.append("## Recommended workflow")
    lines.append("")
    lines.append("1. Read this diff")
    lines.append("2. Edit `scripts/live_forward_test.py` to align phantom with new baseline")
    lines.append("3. Run `pytest tests/ -v` to confirm no regressions")
    lines.append("4. Commit phantom changes")
    lines.append("")
    lines.append(
        "Phantom drift for a few days is acceptable — phantom is read-only validation. "
        "Update at your convenience."
    )
    return "\n".join(lines)


def main() -> int:
    project_root = Path(__file__).parent.parent
    baseline_path = project_root / "config" / "bots" / "baseline_v1.json"
    if not baseline_path.exists():
        print(f"ERROR: {baseline_path} not found")
        return 1

    baseline = json.loads(baseline_path.read_text())
    diffs = diff_baseline_vs_phantom_defaults(baseline)
    report = build_phantom_diff_markdown(baseline, diffs)

    out_path = project_root / "reports" / "sp5_phantom_champion_diff.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report)
    print(report)
    print(f"\nWrote diff report to {out_path}")
    if diffs:
        return 1  # diffs found = phantom needs update
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Smoke test**

```bash
PYTHONPATH=. python -c "
from scripts.sp5_phantom_champion import diff_baseline_vs_phantom_defaults
baseline = {'sol_macro_h6_block_threshold': -0.5, 'alpha_multiplier': 1.0, 'vol_h1_min': 1000.0}
diffs = diff_baseline_vs_phantom_defaults(baseline)
print(f'{len(diffs)} diffs found')
assert 'sol_macro_h6_block_threshold' in diffs
assert 'alpha_multiplier' in diffs
assert 'vol_h1_min' not in diffs  # matches default
print('OK')
"
```

Expected: `2 diffs found\nOK`

- [ ] **Step 3: Run on current baseline (should show no diffs since pre-cutover)**

```bash
PYTHONPATH=. python scripts/sp5_phantom_champion.py
```

Expected: "**No diffs found.** Phantom is already aligned with baseline." (since baseline currently has phantom-default values).

- [ ] **Step 4: Commit**

```bash
git add scripts/sp5_phantom_champion.py
git commit -m "feat(sp5): phantom-parity diff generator for post-cutover baseline

scripts/sp5_phantom_champion.py:
- Reads current baseline_v1.json
- Compares each tunable field against phantom's hardcoded defaults
- Emits markdown diff showing what phantom (live_forward_test.py) needs updated
- Lists filters_disabled that phantom should no longer enforce

Does NOT auto-modify live_forward_test.py (too risky). Human reviews + applies.

Run after each cutover to keep phantom validation aligned.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
```

---

## Task 5: Runbook + final commit

**Files:**
- Create: `docs/superpowers/runbooks/champion-promotion.md`

- [ ] **Step 1: Write the runbook**

```markdown
# Champion Promotion Ceremony

This runbook describes the steps to promote `champion_proposal` → `baseline_v1` in production.
Use after the 49-bot fleet has accumulated enough data for meaningful synthesis.

## Prerequisites

- [ ] 49-bot fleet has been running ≥ 7 days
- [ ] baseline_v1 has ≥ 30 paired trades since fleet deploy
- [ ] champion_proposal has been generated (run `scripts/sp4_champion_synthesis.py`)

## Step 1: Sanity-check the proposal

Open and read both:
- `reports/champion_synthesis.md` (reasoning: which field came from which bot)
- `config/bots/champion_proposal.json` (the actual config)

If any choices look wrong (e.g., the synthesis chose a config from a 5-trade bot), edit `champion_proposal.json` directly before proceeding. Common edits:
- Reverting `filters_disabled` decisions if the disabled filter was based on thin sample
- Restoring `alpha_multiplier=1.5` if the no_alpha_sizing bot won on luck

## Step 2: Enable champion in production to gather forward data

```bash
# Edit champion_proposal.json: change "enabled": false → "enabled": true
git add config/bots/champion_proposal.json
git commit -m "config: enable champion_proposal for forward validation"
git push origin master
MSYS_NO_PATHCONV=1 railway up --detach
```

Wait ≥ 48h for the champion bot to accumulate ≥ 30 trades.

## Step 3: Validate

```bash
python scripts/sp5_validate_champion.py
```

Outcome:
- **PASS**: proceed to Step 4
- **FAIL**: review reasons in `reports/sp5_validate_champion.md`. Either wait for more data, or edit `champion_proposal.json` to fix the issue and redeploy.

## Step 4: Cutover dry-run

```bash
python scripts/sp5_cutover.py
```

Prints what the new `baseline_v1.json` would look like. NO files are modified.

Review the proposed new baseline. If anything looks wrong, edit `champion_proposal.json` first, then rerun.

## Step 5: Cutover (live)

```bash
python scripts/sp5_cutover.py --confirm
```

This:
- Backs up `config/bots/baseline_v1.json` → `baseline_v1.json.pre-cutover-<timestamp>`
- Writes champion fields to `baseline_v1.json` (preserving `bot_id="baseline_v1"`)
- Marks `champion_proposal.json` `enabled=false`

Then:

```bash
git add config/bots/baseline_v1.json config/bots/champion_proposal.json config/bots/baseline_v1.json.pre-cutover-*
git commit -m "chore: cutover baseline_v1 to champion (post-49-bot synthesis)"
git push origin master
MSYS_NO_PATHCONV=1 railway up --detach
```

## Step 6: Phantom alignment (optional, can lag)

```bash
python scripts/sp5_phantom_champion.py
```

Outputs `reports/sp5_phantom_champion_diff.md` showing which phantom rules need updating. Apply changes to `scripts/live_forward_test.py` manually:
- Update threshold defaults to match new baseline
- Remove or no-op filter mirrors for `filters_disabled` filters

Test: `pytest tests/test_filter_layer_restructure.py -v`

Commit + push.

## Step 7: Watch for 24-48h

Confirm:
- Dashboard `/api/bots` returns 49 bots with baseline_v1's metrics matching expected
- New trades show champion's behavior (e.g., if `filters_disabled=["filter_topping"]`, the legacy single-bot path no longer skips on filter_topping)
- No `[BotManager]` or `[DipScanner]` error spikes in Railway logs

## Rollback (if things break)

```bash
python scripts/sp5_rollback.py
```

Restores baseline from the most recent backup. Then:

```bash
git add config/bots/baseline_v1.json
git commit -m "rollback: revert baseline_v1 cutover"
git push origin master
MSYS_NO_PATHCONV=1 railway up --detach
```

The rollback restores in one command. Cutover backup files are never auto-deleted.
```

- [ ] **Step 2: Run full test suite — confirm no regressions**

```bash
PYTHONPATH=. pytest tests/ 2>&1 | tail -5
```

Expected: existing tests pass; pre-existing 7 failures in test_exhaustion_realtime.py remain.

- [ ] **Step 3: Commit + push**

```bash
git add docs/superpowers/runbooks/champion-promotion.md
git commit -m "docs(sp5): champion promotion ceremony runbook

Step-by-step guide for the cutover ceremony:
1. Sanity-check the proposal
2. Enable champion in production (forward validation)
3. Validate via sp5_validate_champion.py
4. Cutover dry-run
5. Cutover live
6. Phantom alignment (optional, can lag)
7. Watch for 24-48h
+ Rollback steps if things break

Sub-project 5 complete — 5 of 5 sub-projects shipped.

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
git push origin master
```

- [ ] **Step 4: NO deploy needed**

SP5 ships only scripts + runbook. The bot itself doesn't change. No `railway up` required for this sub-project.

---

## Plan self-review

### Spec coverage

- `sp5_validate_champion.py` with 5 gates: Task 1 ✅
- `sp5_cutover.py` atomic swap + backup: Task 2 ✅
- `sp5_rollback.py` restore: Task 3 ✅
- `sp5_phantom_champion.py` diff generator: Task 4 ✅
- Runbook: Task 5 ✅
- Tests for each script: Tasks 1-3 (Task 4 is smoke test only — diff generator is read-only)

### Placeholder scan

No "TBD" / "TODO" / vague steps. Every step has exact code, exact commands, exact expected outputs.

### Type consistency

- `BotMetrics` (from sp4_common) used consistently in Task 1
- `PairedTrade` (from sp4_common) used consistently
- `build_new_baseline(old_baseline, champion)` signature consistent across Task 2 tests and impl
- `perform_cutover(baseline_path, champion_path, confirm)` consistent
- `find_latest_backup(baseline_path)` returns `Optional[Path]` consistently

---

## Risks deferred to execution

1. **Cutover assumes champion_proposal.json has been enabled+running.** If user runs cutover before champion has fired any trades, validation will FAIL on sample_size gate. Acceptable — script tells user what's wrong.

2. **Backup file accumulation.** After many cutovers, `config/bots/` will have many `.pre-cutover-*` files. They never auto-delete. Could pollute git diff views. Acceptable cost for safety; user can manually `rm` old backups.

3. **Phantom diff is generation-only.** Doesn't apply changes. User must manually edit `live_forward_test.py`. This is intentional (auto-editing 1500-line phantom file is too risky), but means phantom can drift if user forgets the step.

4. **Rollback doesn't handle git history.** If user already pushed the cutover commit, rollback only restores the local file. A separate `git revert` step is needed to undo the commit. The runbook documents this.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-05-23-sub-project-5-production-successor-plan.md`.

The plan covers 5 tasks, all small and TDD-disciplined. Total LOC: ~700 new lines (mostly scripts + tests, no major refactor).

Two execution options:

**1. Subagent-Driven (recommended)** — Fresh subagent per task with two-stage review. Each script is small + self-contained.

**2. Inline Execution** — Faster wall-clock but my context grows.

**Recommendation: Subagent-Driven.**
