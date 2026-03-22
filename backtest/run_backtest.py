"""
Backtest Runner
Run this script to test your strategy before going live.

Examples:
  python run_backtest.py
  python run_backtest.py --days 30
  python run_backtest.py --score 70
  python run_backtest.py --find-optimal
  python run_backtest.py --days 60 --capital 2000
"""

import asyncio
import argparse
import sys
import os

# Allow imports from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import BacktestEngine


async def run(args):
    print(f"\n{'='*60}")
    print(f"  Running backtest: SOLANA")
    print(f"{'='*60}")

    all_results = []
    engine = BacktestEngine(
            chain_id="solana",
            min_mcap=args.min_mcap,
            max_mcap=args.max_mcap,
            position_size_usd=args.capital / 20,  # ~5% per trade
            take_profit_1=args.tp1,
            take_profit_2=args.tp2,
            take_profit_3=args.tp3,
            stop_loss_pct=args.stop_loss / 100
        )

    result = await engine.run(
        days=args.days,
        score_threshold=args.score,
        find_optimal=args.find_optimal
    )

    result.print_report()
    engine.save_results(result)
    all_results.append(result)

    # Recommendation
    r = all_results[0]
    print("\n  RECOMMENDATION:")
    if r.profit_factor < 1.0:
        print(f"  Strategy losing — raise score threshold or review rules")
    elif r.profit_factor < 1.3:
        print(f"  Marginal — consider raising threshold to {r.score_threshold + 5}")
    else:
        print(f"  Profitable — score {r.score_threshold} looks good")


def main():
    parser = argparse.ArgumentParser(
        description="Backtest the Multi-Chain Memecoin Bot strategy"
    )
    parser.add_argument("--days", type=int, default=30,
                        help="Days of history to test (default: 30)")
    parser.add_argument("--score", type=int, default=65,
                        help="Minimum signal score threshold (default: 65)")
    parser.add_argument("--find-optimal", action="store_true",
                        help="Test thresholds 50-80 and find the best one")
    parser.add_argument("--capital", type=float, default=2000,
                        help="Total capital in USD (default: 2000)")
    parser.add_argument("--min-mcap", type=float, default=200_000,
                        help="Minimum market cap (default: 200000)")
    parser.add_argument("--max-mcap", type=float, default=1_000_000,
                        help="Maximum market cap (default: 1000000)")
    parser.add_argument("--tp1", type=float, default=2.0,
                        help="Take profit 1 multiplier (default: 2.0)")
    parser.add_argument("--tp2", type=float, default=5.0,
                        help="Take profit 2 multiplier (default: 5.0)")
    parser.add_argument("--tp3", type=float, default=10.0,
                        help="Take profit 3 multiplier (default: 10.0)")
    parser.add_argument("--stop-loss", type=float, default=30,
                        help="Stop loss percentage (default: 30)")

    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
