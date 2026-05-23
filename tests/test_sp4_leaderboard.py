from pathlib import Path
from scripts.sp4_leaderboard import build_leaderboard_markdown
from scripts.sp4_common import BotMetrics


def test_leaderboard_renders_table_sorted_by_metric():
    metrics = [
        BotMetrics(bot_id="b_high", sample_n=30, total_pnl_usd=15.0,
                   pnl_per_trade=0.5, win_rate=0.6, avg_win_usd=2.0,
                   avg_loss_usd=-1.5, best_trade_usd=8.0,
                   worst_trade_usd=-3.0, throughput_x_pnl=15.0),
        BotMetrics(bot_id="b_low", sample_n=10, total_pnl_usd=-5.0,
                   pnl_per_trade=-0.5, win_rate=0.3, avg_win_usd=1.0,
                   avg_loss_usd=-2.0, best_trade_usd=2.0,
                   worst_trade_usd=-4.0, throughput_x_pnl=-5.0),
    ]
    md = build_leaderboard_markdown(metrics, sort_by="throughput_x_pnl")
    assert "b_high" in md
    assert "b_low" in md
    assert md.index("b_high") < md.index("b_low")
    assert "$/tr" in md
    assert "WR" in md


def test_leaderboard_includes_confidence_label():
    metrics = [BotMetrics(
        bot_id="b_thin", sample_n=3, total_pnl_usd=2.0,
        pnl_per_trade=0.67, win_rate=0.67, avg_win_usd=2.0,
        avg_loss_usd=-1.0, best_trade_usd=3.0, worst_trade_usd=-1.0,
        throughput_x_pnl=2.0,
    )]
    md = build_leaderboard_markdown(metrics, sort_by="total_pnl_usd")
    assert "Very low" in md
