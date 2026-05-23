"""SP5: generate suggested phantom-parity changes for the post-cutover baseline.

Usage: python scripts/sp5_phantom_champion.py
Output: reports/sp5_phantom_champion_diff.md
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path


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
    diffs = {}
    for field, phantom_default in PHANTOM_DEFAULTS.items():
        new_value = baseline_config.get(field)
        if new_value != phantom_default:
            diffs[field] = (phantom_default, new_value)
    return diffs


def build_phantom_diff_markdown(baseline_config: dict, diffs: dict) -> str:
    lines = [
        f"# Phantom-parity diff for post-cutover baseline",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "After cutover, config/bots/baseline_v1.json has these field values "
        "that DIFFER from what scripts/live_forward_test.py currently mirrors.",
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
            "**Action:** In scripts/live_forward_test.py, find the predicate for "
            "each of these filters and either delete the predicate row or change it "
            "to always return PASS."
        )
    lines.append("")
    lines.append("## Recommended workflow")
    lines.append("")
    lines.append("1. Read this diff")
    lines.append("2. Edit scripts/live_forward_test.py to align phantom with new baseline")
    lines.append("3. Run pytest tests/ -v to confirm no regressions")
    lines.append("4. Commit phantom changes")
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
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
