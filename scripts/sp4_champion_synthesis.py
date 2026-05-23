"""SP4: greedy synthesis of a champion config from winning bots.

Usage: python scripts/sp4_champion_synthesis.py
Output:
- config/bots/champion_proposal.json (overwrites)
- reports/champion_synthesis.md (reasoning)
"""
from __future__ import annotations
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from scripts.sp4_common import (
    BotMetrics, fetch_all_trades, pair_buys_sells, compute_metrics,
)


MIN_BASELINE_SAMPLE = 30


def pick_best_from_pair(a: BotMetrics, b: BotMetrics) -> BotMetrics:
    if a.pnl_per_trade is None and b.pnl_per_trade is None:
        return a
    if a.pnl_per_trade is None:
        return b
    if b.pnl_per_trade is None:
        return a
    return b if b.pnl_per_trade > a.pnl_per_trade else a


def _pick_field(field_name, candidates, bot_id_to_value, fallback_value):
    best = None
    for c in candidates:
        if c.sample_n < 5:
            continue
        if best is None or (c.pnl_per_trade or -1e9) > (best.pnl_per_trade or -1e9):
            best = c
    if best is None:
        return fallback_value, "baseline_v1 (no candidates had n>=5)"
    return bot_id_to_value[best.bot_id], best.bot_id


def synthesize_champion(metrics_by_id, baseline_config_path, out_config_path, out_reasoning_path):
    baseline_metrics = metrics_by_id.get("baseline_v1")
    if baseline_metrics is None or baseline_metrics.sample_n < MIN_BASELINE_SAMPLE:
        print(
            f"REFUSED: baseline_v1 has only "
            f"{baseline_metrics.sample_n if baseline_metrics else 0} trades, "
            f"need >={MIN_BASELINE_SAMPLE}. Re-run after more data."
        )
        return False

    baseline_config = json.loads(baseline_config_path.read_text())
    champion = dict(baseline_config)
    reasoning = [
        f"# Champion synthesis - {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Baseline: n={baseline_metrics.sample_n}, $/tr=${baseline_metrics.pnl_per_trade:+.2f}",
        "",
        "Greedy field-by-field synthesis.",
        "",
        "## Field choices",
        "",
    ]

    def _candidates(*bot_ids):
        return [metrics_by_id[bid] for bid in bot_ids
                if bid in metrics_by_id and metrics_by_id[bid].sample_n > 0]

    chosen_alpha, src_alpha = _pick_field(
        "alpha_multiplier",
        _candidates("baseline_v1", "no_alpha_sizing"),
        {"baseline_v1": 1.5, "no_alpha_sizing": 1.0},
        fallback_value=1.5,
    )
    champion["alpha_multiplier"] = chosen_alpha
    reasoning.append(f"- `alpha_multiplier={chosen_alpha}` <- from `{src_alpha}`")

    chosen_mc, src_mc = _pick_field(
        "max_concurrent_positions",
        _candidates("baseline_v1", "narrow_concurrent", "wide_concurrent"),
        {"baseline_v1": 3, "narrow_concurrent": 1, "wide_concurrent": 5},
        fallback_value=3,
    )
    champion["max_concurrent_positions"] = chosen_mc
    reasoning.append(f"- `max_concurrent_positions={chosen_mc}` <- from `{src_mc}`")

    chosen_stop, src_stop = _pick_field(
        "hard_stop_pct",
        _candidates("baseline_v1", "tight_stop", "wide_stop"),
        {"baseline_v1": -15.0, "tight_stop": -10.0, "wide_stop": -20.0},
        fallback_value=-15.0,
    )
    champion["hard_stop_pct"] = chosen_stop
    reasoning.append(f"- `hard_stop_pct={chosen_stop}` <- from `{src_stop}`")

    exit_candidates = _candidates("baseline_v1", "runner_tilt_aggressive", "scalp_only")
    best_exit = None
    for c in exit_candidates:
        if c.sample_n < 5:
            continue
        if best_exit is None or (c.pnl_per_trade or -1e9) > (best_exit.pnl_per_trade or -1e9):
            best_exit = c
    exit_ladder_values = {
        "baseline_v1": (5.0, 0.75, 10.0, 0.25, 3.0),
        "runner_tilt_aggressive": (8.0, 0.33, 20.0, 0.33, 4.0),
        "scalp_only": (3.0, 1.0, 999.0, 0.0, 999.0),
    }
    src_exit = best_exit.bot_id if best_exit else "baseline_v1"
    tp1, tp1_sf, tp2, tp2_sf, trail = exit_ladder_values.get(src_exit, exit_ladder_values["baseline_v1"])
    champion["tp1_pct"] = tp1
    champion["tp1_sell_fraction"] = tp1_sf
    champion["tp2_pct"] = tp2
    champion["tp2_sell_fraction"] = tp2_sf
    champion["trail_pp"] = trail
    reasoning.append(
        f"- Exit ladder (tp1={tp1}, tp1_sf={tp1_sf}, tp2={tp2}, "
        f"tp2_sf={tp2_sf}, trail={trail}) <- from `{src_exit}`"
    )

    chosen_sol, src_sol = _pick_field(
        "sol_macro_h6_block_threshold",
        _candidates("baseline_v1", "sol_h6_loose", "sol_h6_tight", "sol_h6_extreme"),
        {"baseline_v1": -0.3, "sol_h6_loose": -0.1, "sol_h6_tight": -0.5, "sol_h6_extreme": -1.0},
        fallback_value=-0.3,
    )
    champion["sol_macro_h6_block_threshold"] = chosen_sol
    reasoning.append(f"- `sol_macro_h6_block_threshold={chosen_sol}` <- from `{src_sol}`")

    chosen_psych, src_psych = _pick_field(
        "mcap_psych_pc_h24_max",
        _candidates("baseline_v1", "psych_h24_50", "psych_h24_100", "psych_h24_150"),
        {"baseline_v1": 80.0, "psych_h24_50": 50.0, "psych_h24_100": 100.0, "psych_h24_150": 150.0},
        fallback_value=80.0,
    )
    champion["mcap_psych_pc_h24_max"] = chosen_psych
    reasoning.append(f"- `mcap_psych_pc_h24_max={chosen_psych}` <- from `{src_psych}`")

    chosen_vol, src_vol = _pick_field(
        "vol_h1_min",
        _candidates("baseline_v1", "vol_min_500", "vol_min_5k", "vol_min_10k"),
        {"baseline_v1": 1000.0, "vol_min_500": 500.0, "vol_min_5k": 5000.0, "vol_min_10k": 10000.0},
        fallback_value=1000.0,
    )
    champion["vol_h1_min"] = chosen_vol
    reasoning.append(f"- `vol_h1_min={chosen_vol}` <- from `{src_vol}`")

    ablation_map = {
        "no_turn": "filter_turn",
        "no_negative_net_flow_5m": "filter_negative_net_flow_5m",
        "no_seller_imbalance": "filter_seller_imbalance",
        "no_low_volatility": "filter_low_volatility",
        "no_vp_poc": "filter_vp_poc",
        "no_topping": "filter_topping",
        "no_above_vwap_chase": "filter_above_vwap_chase",
        "no_bs_m5_weak": "filter_bs_m5_weak",
        "no_blowoff_top": "filter_blowoff_top",
        "no_1m_steep_fall": "filter_1m_steep_fall",
    }
    filters_to_disable = []
    base_per = baseline_metrics.pnl_per_trade or 0.0
    for bot_id, filter_name in ablation_map.items():
        ab = metrics_by_id.get(bot_id)
        if ab is None or ab.sample_n < 5:
            continue
        ab_per = ab.pnl_per_trade or 0.0
        if ab_per > base_per + 0.05:
            filters_to_disable.append(filter_name)
            reasoning.append(
                f"- Disabling `{filter_name}` (ablation $/tr ${ab_per:+.2f} > "
                f"baseline ${base_per:+.2f})"
            )
    champion["filters_disabled"] = filters_to_disable

    champion["bot_id"] = "champion_proposal"
    champion["display_name"] = (
        f"Champion proposal (synthesized {datetime.now(timezone.utc).date().isoformat()})"
    )
    champion["enabled"] = False

    out_config_path.write_text(json.dumps(champion, indent=2, sort_keys=True))
    out_reasoning_path.write_text("\n".join(reasoning))
    print(f"Wrote champion proposal to {out_config_path}")
    print(f"Wrote reasoning to {out_reasoning_path}")
    return True


def main() -> int:
    trades = fetch_all_trades()
    paired = pair_buys_sells(trades)
    by_bot = defaultdict(list)
    for p in paired:
        by_bot[p.bot_id].append(p)
    metrics_by_id = {bid: compute_metrics(ps) for bid, ps in by_bot.items()}

    project_root = Path(__file__).parent.parent
    baseline_path = project_root / "config" / "bots" / "baseline_v1.json"
    out_config_path = project_root / "config" / "bots" / "champion_proposal.json"
    out_reasoning_path = project_root / "reports" / "champion_synthesis.md"
    out_reasoning_path.parent.mkdir(parents=True, exist_ok=True)

    ok = synthesize_champion(metrics_by_id, baseline_path, out_config_path, out_reasoning_path)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
