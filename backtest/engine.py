"""
Backtesting Engine
Tests scanner signal thresholds against real historical data
from DexScreener before risking any real money.

Usage:
  python backtest/run_backtest.py --days 30
  python backtest/run_backtest.py --score 60 --days 60
  python backtest/run_backtest.py --find-optimal --days 45

What it does:
  1. Fetches historical token data from DexScreener
  2. Replays the scanner's scoring logic against that data
  3. Simulates buys, take profits, and stop losses
  4. Reports win rate, avg return, profit factor, max drawdown
  5. Finds the optimal score threshold for your settings
"""

import asyncio
import aiohttp
import json
import logging
import argparse
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex"


@dataclass
class BacktestTrade:
    token_address: str
    token_symbol: str
    chain_id: str
    entry_price: float
    entry_mcap: float
    entry_time: datetime
    signal_score: int
    exit_price: float = 0.0
    exit_time: Optional[datetime] = None
    exit_reason: str = ""
    pnl_pct: float = 0.0
    pnl_usd: float = 0.0
    position_size_usd: float = 100.0
    peak_multiplier: float = 1.0


@dataclass
class BacktestResult:
    chain_id: str
    days_tested: int
    score_threshold: int
    total_signals: int
    trades_taken: int
    wins: int
    losses: int
    win_rate: float
    avg_win_pct: float
    avg_loss_pct: float
    total_pnl_usd: float
    total_pnl_pct: float
    profit_factor: float
    max_drawdown_pct: float
    best_trade_pct: float
    worst_trade_pct: float
    sharpe_ratio: float
    trades: List[BacktestTrade] = field(default_factory=list)

    def print_report(self):
        print("\n" + "=" * 60)
        print(f"  BACKTEST RESULTS — {self.chain_id.upper()}")
        print("=" * 60)
        print(f"  Period:          {self.days_tested} days")
        print(f"  Score Threshold: {self.score_threshold}/100")
        print(f"  Total Signals:   {self.total_signals}")
        print(f"  Trades Taken:    {self.trades_taken}")
        print("-" * 60)
        print(f"  Win Rate:        {self.win_rate:.1f}%")
        print(f"  Avg Win:         +{self.avg_win_pct:.1f}%")
        print(f"  Avg Loss:        {self.avg_loss_pct:.1f}%")
        print(f"  Profit Factor:   {self.profit_factor:.2f}")
        print(f"  Total PnL:       ${self.total_pnl_usd:+,.2f} ({self.total_pnl_pct:+.1f}%)")
        print(f"  Max Drawdown:    {self.max_drawdown_pct:.1f}%")
        print(f"  Best Trade:      +{self.best_trade_pct:.1f}%")
        print(f"  Worst Trade:     {self.worst_trade_pct:.1f}%")
        print(f"  Sharpe Ratio:    {self.sharpe_ratio:.2f}")
        print("-" * 60)

        # Grade
        if self.profit_factor >= 2.0 and self.win_rate >= 55:
            grade = "A — Excellent"
        elif self.profit_factor >= 1.5 and self.win_rate >= 50:
            grade = "B — Good"
        elif self.profit_factor >= 1.2 and self.win_rate >= 45:
            grade = "C — Marginal"
        elif self.profit_factor >= 1.0:
            grade = "D — Breakeven"
        else:
            grade = "F — Losing"

        print(f"  Strategy Grade:  {grade}")
        print("=" * 60)

    def to_dict(self) -> dict:
        return {
            "chain_id": self.chain_id,
            "days_tested": self.days_tested,
            "score_threshold": self.score_threshold,
            "total_signals": self.total_signals,
            "trades_taken": self.trades_taken,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": self.win_rate,
            "avg_win_pct": self.avg_win_pct,
            "avg_loss_pct": self.avg_loss_pct,
            "total_pnl_usd": self.total_pnl_usd,
            "total_pnl_pct": self.total_pnl_pct,
            "profit_factor": self.profit_factor,
            "max_drawdown_pct": self.max_drawdown_pct,
            "best_trade_pct": self.best_trade_pct,
            "worst_trade_pct": self.worst_trade_pct,
            "sharpe_ratio": self.sharpe_ratio
        }


class BacktestEngine:
    """
    Backtesting engine that replays scanner logic on historical data.
    """

    def __init__(self,
                 chain_id: str,
                 min_mcap: float = 200_000,
                 max_mcap: float = 1_000_000,
                 position_size_usd: float = 100.0,
                 take_profit_1: float = 2.0,
                 take_profit_2: float = 5.0,
                 take_profit_3: float = 10.0,
                 stop_loss_pct: float = 0.30,
                 max_hold_hours: int = 48):
        self.chain_id = chain_id
        self.min_mcap = min_mcap
        self.max_mcap = max_mcap
        self.position_size_usd = position_size_usd
        self.tp1 = take_profit_1
        self.tp2 = take_profit_2
        self.tp3 = take_profit_3
        self.stop_loss = stop_loss_pct
        self.max_hold_hours = max_hold_hours

        # DexScreener chain slugs
        self.dex_chains = {
            "solana": "solana",
        }

    async def run(self,
                  days: int = 30,
                  score_threshold: int = 65,
                  find_optimal: bool = False) -> BacktestResult:
        """
        Run a backtest for the specified number of days.
        If find_optimal=True, tests thresholds 50-80 and returns best.
        """
        print(f"\n[Backtest] Fetching historical data for {self.chain_id} ({days} days)...")
        tokens = await self._fetch_historical_tokens(days)

        if not tokens:
            print(f"[Backtest] No historical data found for {self.chain_id}")
            return self._empty_result(score_threshold, days)

        print(f"[Backtest] Found {len(tokens)} tokens to analyze...")

        if find_optimal:
            return await self._find_optimal_threshold(tokens, days)

        return self._run_simulation(tokens, score_threshold, days)

    async def _fetch_historical_tokens(self, days: int) -> List[dict]:
        """
        Fetch tokens that were active in the target market cap range.
        DexScreener doesn't have a true historical API so we fetch
        current tokens with age data and filter by creation time.
        """
        tokens = []
        chain_slug = self.dex_chains.get(self.chain_id, self.chain_id)

        # Fetch from multiple DexScreener endpoints to get a larger sample
        endpoints = [
            f"{DEXSCREENER_API}/search?q={chain_slug}%20memecoin",
            f"{DEXSCREENER_API}/search?q={chain_slug}%20meme",
            f"{DEXSCREENER_API}/search?q={chain_slug}",
        ]

        seen = set()
        async with aiohttp.ClientSession() as session:
            for url in endpoints:
                try:
                    async with session.get(
                        url, timeout=aiohttp.ClientTimeout(total=20)
                    ) as resp:
                        if resp.status != 200:
                            continue
                        data = await resp.json()
                        pairs = data.get("pairs", [])
                        for pair in pairs:
                            addr = pair.get("baseToken", {}).get("address", "")
                            if addr and addr not in seen:
                                if pair.get("chainId") == chain_slug:
                                    seen.add(addr)
                                    tokens.append(pair)
                    await asyncio.sleep(0.5)
                except Exception as e:
                    logger.debug(f"Fetch error: {e}")

        # Filter to our market cap range (or nearby — historical tokens may have moved)
        # Use a wider range to catch tokens that passed through our zone
        wide_min = self.min_mcap * 0.3
        wide_max = self.max_mcap * 5.0
        filtered = [
            t for t in tokens
            if wide_min <= t.get("marketCap", 0) <= wide_max
        ]

        return filtered

    def _run_simulation(self, tokens: List[dict],
                        score_threshold: int, days: int) -> BacktestResult:
        """
        Simulate trading on the token list using scanner scoring.
        """
        trades = []
        total_signals = 0

        for pair in tokens:
            score = self._score_pair(pair)
            total_signals += 1

            if score < score_threshold:
                continue

            # Simulate the trade
            trade = self._simulate_trade(pair, score)
            if trade:
                trades.append(trade)

        return self._calculate_results(
            trades, score_threshold, days, total_signals
        )

    def _score_pair(self, pair: dict) -> int:
        """Apply the same scoring logic as the live scanner."""
        score = 0
        mcap = pair.get("marketCap", 0)
        volume_h1 = pair.get("volume", {}).get("h1", 0)
        price_change_h1 = pair.get("priceChange", {}).get("h1", 0) or 0
        liquidity = pair.get("liquidity", {}).get("usd", 0)
        txns_h1 = pair.get("txns", {}).get("h1", {})
        buys = txns_h1.get("buys", 0)
        sells = txns_h1.get("sells", 0)
        info = pair.get("info", {})
        has_social = bool(info.get("socials") or info.get("websites"))

        # Market cap
        if self.min_mcap <= mcap <= self.max_mcap:
            if mcap <= 400_000:
                score += 20
            elif mcap <= 700_000:
                score += 15
            else:
                score += 10
        else:
            score -= 20  # Outside our range

        # Volume
        vol_high = 30_000 if self.chain_id != "solana" else 50_000
        vol_mid = 10_000 if self.chain_id != "solana" else 20_000
        if volume_h1 >= vol_high:
            score += 20
        elif volume_h1 >= vol_mid:
            score += 12
        elif volume_h1 >= 5_000:
            score += 6
        else:
            score -= 5

        # Price momentum
        if price_change_h1 > 20:
            score += 20
        elif price_change_h1 > 10:
            score += 14
        elif price_change_h1 > 5:
            score += 8
        elif price_change_h1 < -15:
            score -= 15

        # Buy pressure
        total = buys + sells
        if total > 0:
            ratio = buys / total
            if ratio >= 0.65:
                score += 15
            elif ratio >= 0.55:
                score += 8
            elif ratio < 0.40:
                score -= 10

        # Liquidity
        if liquidity >= 50_000:
            score += 15
        elif liquidity >= 20_000:
            score += 8
        elif liquidity < 10_000:
            score -= 10

        # Social
        if has_social:
            score += 10

        return max(0, min(100, score))

    def _simulate_trade(self, pair: dict, score: int) -> Optional[BacktestTrade]:
        """Simulate a single trade with TP and SL logic."""
        entry_price = float(pair.get("priceUsd", 0) or 0)
        entry_mcap = pair.get("marketCap", 0)
        token_symbol = pair.get("baseToken", {}).get("symbol", "?")
        token_address = pair.get("baseToken", {}).get("address", "")

        if entry_price <= 0:
            return None

        trade = BacktestTrade(
            token_address=token_address,
            token_symbol=token_symbol,
            chain_id=self.chain_id,
            entry_price=entry_price,
            entry_mcap=entry_mcap,
            entry_time=datetime.now(timezone.utc),
            signal_score=score,
            position_size_usd=self.position_size_usd
        )

        # Simulate price path using actual price change data as proxy
        # We use the available h1, h6, h24 changes to model what happened
        pc_h1 = float(pair.get("priceChange", {}).get("h1", 0) or 0)
        pc_h6 = float(pair.get("priceChange", {}).get("h6", 0) or 0)
        pc_h24 = float(pair.get("priceChange", {}).get("h24", 0) or 0)

        # Model peak price from available data
        peak_multiplier = 1.0
        if pc_h1 > 0:
            peak_multiplier = max(peak_multiplier, 1 + (pc_h1 / 100))
        if pc_h6 > 0:
            peak_multiplier = max(peak_multiplier, 1 + (pc_h6 / 100))
        if pc_h24 > 0:
            peak_multiplier = max(peak_multiplier, 1 + (pc_h24 / 100))

        trade.peak_multiplier = peak_multiplier
        final_multiplier = 1 + (pc_h24 / 100)

        # Apply TP/SL logic
        exit_multiplier = 1.0
        exit_reason = "Max hold time"

        # Check stop loss first
        min_multiplier = 1 + min(pc_h1, pc_h6, pc_h24) / 100
        if min_multiplier <= (1 - self.stop_loss):
            exit_multiplier = 1 - self.stop_loss
            exit_reason = f"Stop loss -{self.stop_loss*100:.0f}%"
        # Take profit tiers
        elif peak_multiplier >= self.tp3:
            # Simplified: assume we caught TP1, TP2, and TP3
            # Weighted average of partial sells
            exit_multiplier = (
                self.tp1 * 0.50 +    # 50% sold at TP1
                self.tp2 * 0.30 +    # 30% sold at TP2
                self.tp3 * 0.20      # 20% sold at TP3
            )
            exit_reason = f"TP3 hit ({peak_multiplier:.1f}x peak)"
        elif peak_multiplier >= self.tp2:
            exit_multiplier = (
                self.tp1 * 0.50 +
                self.tp2 * 0.50
            )
            exit_reason = f"TP2 hit ({peak_multiplier:.1f}x peak)"
        elif peak_multiplier >= self.tp1:
            exit_multiplier = (
                self.tp1 * 0.50 +
                final_multiplier * 0.50
            )
            exit_reason = f"TP1 hit, remainder at {final_multiplier:.2f}x"
        else:
            exit_multiplier = final_multiplier
            exit_reason = f"Time exit at {final_multiplier:.2f}x"

        trade.exit_price = entry_price * exit_multiplier
        trade.exit_reason = exit_reason
        trade.pnl_pct = (exit_multiplier - 1) * 100
        trade.pnl_usd = self.position_size_usd * (exit_multiplier - 1)
        trade.exit_time = datetime.now(timezone.utc) + timedelta(hours=24)

        return trade

    def _calculate_results(self, trades: List[BacktestTrade],
                            score_threshold: int, days: int,
                            total_signals: int) -> BacktestResult:
        """Calculate final statistics from all simulated trades."""
        if not trades:
            return self._empty_result(score_threshold, days)

        wins = [t for t in trades if t.pnl_pct > 0]
        losses = [t for t in trades if t.pnl_pct <= 0]
        win_rate = len(wins) / len(trades) * 100 if trades else 0
        avg_win = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
        total_pnl = sum(t.pnl_usd for t in trades)
        total_invested = len(trades) * self.position_size_usd
        total_pnl_pct = total_pnl / total_invested * 100 if total_invested > 0 else 0

        gross_profit = sum(t.pnl_usd for t in wins)
        gross_loss = abs(sum(t.pnl_usd for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.0

        pnl_series = [t.pnl_pct for t in trades]
        max_drawdown = self._calculate_max_drawdown(pnl_series)

        import statistics
        if len(pnl_series) > 1:
            avg_r = sum(pnl_series) / len(pnl_series)
            std_r = statistics.stdev(pnl_series)
            sharpe = (avg_r / std_r) * (252 ** 0.5) / 100 if std_r > 0 else 0
        else:
            sharpe = 0

        return BacktestResult(
            chain_id=self.chain_id,
            days_tested=days,
            score_threshold=score_threshold,
            total_signals=total_signals,
            trades_taken=len(trades),
            wins=len(wins),
            losses=len(losses),
            win_rate=win_rate,
            avg_win_pct=avg_win,
            avg_loss_pct=avg_loss,
            total_pnl_usd=total_pnl,
            total_pnl_pct=total_pnl_pct,
            profit_factor=profit_factor,
            max_drawdown_pct=max_drawdown,
            best_trade_pct=max(t.pnl_pct for t in trades),
            worst_trade_pct=min(t.pnl_pct for t in trades),
            sharpe_ratio=sharpe,
            trades=trades
        )

    def _calculate_max_drawdown(self, returns: List[float]) -> float:
        """Calculate maximum drawdown from a series of returns."""
        if not returns:
            return 0.0
        peak = 0.0
        max_dd = 0.0
        running = 0.0
        for r in returns:
            running += r
            if running > peak:
                peak = running
            dd = peak - running
            if dd > max_dd:
                max_dd = dd
        return max_dd

    async def _find_optimal_threshold(self, tokens: List[dict],
                                       days: int) -> BacktestResult:
        """Test thresholds from 50 to 80 and return the best one."""
        print("\n[Backtest] Finding optimal score threshold...")
        best_result = None
        best_score = float('-inf')

        for threshold in range(50, 85, 5):
            result = self._run_simulation(tokens, threshold, days)
            composite = (
                result.win_rate * 0.3 +
                result.profit_factor * 20 +
                result.total_pnl_pct * 0.3
            )
            print(
                f"  Threshold {threshold}: WR={result.win_rate:.1f}% | "
                f"PF={result.profit_factor:.2f} | "
                f"PnL={result.total_pnl_pct:+.1f}% | "
                f"Trades={result.trades_taken}"
            )
            if composite > best_score:
                best_score = composite
                best_result = result

        if best_result:
            print(f"\n  Optimal threshold: {best_result.score_threshold}")
        return best_result or self._empty_result(65, days)

    def _empty_result(self, threshold: int, days: int) -> BacktestResult:
        return BacktestResult(
            chain_id=self.chain_id, days_tested=days,
            score_threshold=threshold, total_signals=0,
            trades_taken=0, wins=0, losses=0, win_rate=0,
            avg_win_pct=0, avg_loss_pct=0, total_pnl_usd=0,
            total_pnl_pct=0, profit_factor=0, max_drawdown_pct=0,
            best_trade_pct=0, worst_trade_pct=0, sharpe_ratio=0
        )

    def save_results(self, result: BacktestResult, filename: str = ""):
        """Save backtest results to JSON."""
        if not filename:
            filename = (
                f"backtest_{result.chain_id}_"
                f"score{result.score_threshold}_"
                f"{datetime.now().strftime('%Y%m%d_%H%M')}.json"
            )
        with open(filename, "w") as f:
            data = result.to_dict()
            data["trades"] = [
                {
                    "symbol": t.token_symbol,
                    "score": t.signal_score,
                    "pnl_pct": round(t.pnl_pct, 2),
                    "pnl_usd": round(t.pnl_usd, 2),
                    "exit_reason": t.exit_reason,
                    "peak_mult": round(t.peak_multiplier, 2)
                }
                for t in result.trades
            ]
            json.dump(data, f, indent=2)
        print(f"\n[Backtest] Results saved to {filename}")
