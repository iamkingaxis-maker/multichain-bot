from scripts.sp4_filter_attribution import (
    build_filter_attribution_markdown, ABLATION_FILTER_MAP,
)
from scripts.sp4_common import BotMetrics


def test_attribution_computes_delta_baseline_minus_ablation():
    baseline = BotMetrics(
        bot_id="baseline_v1", sample_n=30, total_pnl_usd=15.0,
        pnl_per_trade=0.5, win_rate=0.6, avg_win_usd=2.0,
        avg_loss_usd=-1.5, best_trade_usd=8.0, worst_trade_usd=-3.0,
        throughput_x_pnl=15.0,
    )
    ablations = {
        "no_topping": BotMetrics(
            bot_id="no_topping", sample_n=35, total_pnl_usd=7.0,
            pnl_per_trade=0.2, win_rate=0.5, avg_win_usd=1.5,
            avg_loss_usd=-1.5, best_trade_usd=6.0, worst_trade_usd=-3.0,
            throughput_x_pnl=7.0,
        ),
    }
    md = build_filter_attribution_markdown(baseline, ablations)
    assert "filter_topping" in md
    assert "+0.30" in md or "0.30" in md


def test_ablation_filter_map_has_10_entries():
    assert len(ABLATION_FILTER_MAP) == 10
    assert ABLATION_FILTER_MAP["no_topping"] == "filter_topping"
    assert ABLATION_FILTER_MAP["no_turn"] == "filter_turn"
