"""
Dip-Buy Backtest v2 — Birdeye Historical OHLCV
===============================================
Replays the live bot's exact dip-buy logic against real historical
5-minute candle data from Birdeye.

Simulates:
  - h6 macro trend filter  (>-20% required)
  - 5m peak-based dip range (-12% to -45%)
  - Rug-dump detection     (5 consecutive red 5m candles)
  - Mandatory last_green
  - Recovery signals       (need 3/6: RSI reset, vol easing,
                            stabilizing, higher low, 1m momentum proxy)
  - 8% stop loss
  - Take profits at 10% (100%), 75% (75%), 150% (75%)
  - 6h max hold

Usage:
  python backtest/dip_buy_backtest.py
  python backtest/dip_buy_backtest.py --days 14 --tokens 150
"""

import asyncio
import aiohttp
import argparse
import statistics
import os
import sys
import time as _time
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Fix unicode on Windows terminals
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BIRDEYE_API  = "https://public-api.birdeye.so"
DEXSCREENER  = "https://api.dexscreener.com/latest/dex"

# ── Strategy parameters (must match live bot config) ─────────────────────────
STOP_LOSS_PCT     = 0.08     # 8%
TP1_PCT           = 0.10     # 10% — sell 50% of position (matches live bot)
TP1_SELL          = 0.50     # fraction sold at TP1
TP2_PCT           = 0.75     # 75% — sell 75% of remainder
TP2_SELL          = 0.75
TP3_PCT           = 1.50     # 150% — sell 75% of remainder
TP3_SELL          = 0.75
MAX_HOLD_CANDLES  = 72       # 6h @ 5-min candles
MIN_DIP_PCT       = -45.0    # must be at least this far below peak
MAX_DIP_PCT       = -12.0    # must not be deeper than this
H6_DROP_LIMIT     = -20.0    # macro trend gate
POSITION_USD      = 89.0     # position size matching live bot
CANDLE_WINDOW     = 30       # candles for peak/recovery analysis
MIN_CANDLES_DATA  = 200      # skip token if fewer historical candles
MIN_RECOVERY      = 3        # minimum recovery signals required
SL_COOLDOWN_CANDLES = 48     # 4h cooldown after stop-loss on same token


@dataclass
class BtTrade:
    symbol:         str
    address:        str
    entry_ts:       int
    entry_price:    float
    exit_price:     float
    exit_reason:    str
    pnl_pct:        float
    dip_pct:        float
    recovery_score: int
    hold_candles:   int


@dataclass
class BtResult:
    tokens_tested:  int
    total_trades:   int
    wins:           int
    losses:         int
    win_rate:       float
    avg_win_pct:    float
    avg_loss_pct:   float
    profit_factor:  float
    total_pnl_usd:  float
    max_drawdown:   float
    best_trade:     float
    worst_trade:    float
    sharpe:         float
    trades:         list = field(default_factory=list)

    def print_report(self):
        print("\n" + "=" * 62)
        print("  DIP-BUY BACKTEST RESULTS — SOLANA")
        print("=" * 62)
        print(f"  Tokens tested:    {self.tokens_tested}")
        print(f"  Total trades:     {self.total_trades}")
        print(f"  Wins / Losses:    {self.wins} / {self.losses}")
        print("-" * 62)
        print(f"  Win Rate:         {self.win_rate:.1f}%")
        print(f"  Avg Win:          +{self.avg_win_pct:.1f}%")
        print(f"  Avg Loss:         {self.avg_loss_pct:.1f}%")
        print(f"  Profit Factor:    {self.profit_factor:.2f}")
        print(f"  Total PnL:        ${self.total_pnl_usd:+,.2f}")
        print(f"  Max Drawdown:     {self.max_drawdown:.1f}%")
        print(f"  Best Trade:       +{self.best_trade:.1f}%")
        print(f"  Worst Trade:      {self.worst_trade:.1f}%")
        print(f"  Sharpe Ratio:     {self.sharpe:.2f}")
        print("-" * 62)

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

        print(f"  Strategy Grade:   {grade}")
        print("=" * 62)

        if self.trades:
            wins   = [t for t in self.trades if t.pnl_pct > 0]
            losses = [t for t in self.trades if t.pnl_pct <= 0]
            print("\n  Top 5 Wins:")
            for t in sorted(wins, key=lambda x: -x.pnl_pct)[:5]:
                s = (t.symbol[:10] + "..") if len(t.symbol) > 12 else t.symbol
                print(f"    {s:12} +{t.pnl_pct:.1f}%  dip={t.dip_pct:.1f}%  rec={t.recovery_score}/6  [{t.exit_reason}]")
            print("\n  Top 5 Losses:")
            for t in sorted(losses, key=lambda x: x.pnl_pct)[:5]:
                s = (t.symbol[:10] + "..") if len(t.symbol) > 12 else t.symbol
                print(f"    {s:12} {t.pnl_pct:.1f}%  dip={t.dip_pct:.1f}%  rec={t.recovery_score}/6  [{t.exit_reason}]")


class DipBuyBacktest:

    def __init__(self, birdeye_api_key: str):
        self.api_key = birdeye_api_key
        self.headers = {
            "X-API-KEY": birdeye_api_key,
            "x-chain":   "solana",
        }

    async def run(self, days: int = 14, max_tokens: int = 100) -> BtResult:
        print(f"\n[Backtest] Fetching token list...")
        tokens = await self._get_token_list(max_tokens)
        print(f"[Backtest] Got {len(tokens)} tokens — fetching {days}d of 5m candles...\n")

        all_trades = []
        tokens_tested = 0

        async with aiohttp.ClientSession() as session:
            for i, token in enumerate(tokens):
                sym  = token.get("symbol", "?")
                addr = token.get("address", "")
                if not addr:
                    continue

                display_sym = (sym[:12] + "..") if len(sym) > 14 else sym
                print(f"  [{i+1:3}/{len(tokens)}] {display_sym:14}", end=" ", flush=True)
                candles = await self._fetch_ohlcv(session, addr, days)

                if not candles or len(candles) < MIN_CANDLES_DATA:
                    print(f"skip ({len(candles) if candles else 0} candles)")
                    # no sleep needed — failed OHLCV fetch is already fast
                    continue

                tokens_tested += 1
                trades = self._simulate(candles, sym, addr)
                pnl = sum(t.pnl_pct for t in trades)
                print(f"{len(candles)} candles → {len(trades)} trades  PnL={pnl:+.1f}%")
                all_trades.extend(trades)
                await asyncio.sleep(0.2)   # respect rate limits after successful fetch

        return self._calculate_results(all_trades, tokens_tested)

    async def _get_token_list(self, limit: int) -> list:
        """
        Pull Solana memecoins with real historical trading data.
        Sources (in priority order):
          1. DexScreener token-boosts (paid boosts = verified real memecoins)
          2. DexScreener token-profiles (same)
          3. DexScreener search (meme/pump/cat/dog keywords on Solana)
          4. Birdeye tokenlist sweeping multiple pages for variety
        """
        tokens = {}

        async with aiohttp.ClientSession() as session:
            # ── 1. DexScreener top boosted tokens (paid = real projects) ─────
            for boost_endpoint in [
                "https://api.dexscreener.com/token-boosts/top/v1",
                "https://api.dexscreener.com/token-boosts/latest/v1",
                "https://api.dexscreener.com/token-profiles/latest/v1",
            ]:
                try:
                    async with session.get(
                        boost_endpoint, timeout=aiohttp.ClientTimeout(total=10)
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for d in data:
                                if d.get("chainId") != "solana":
                                    continue
                                addr = d.get("tokenAddress", "")
                                # symbol may not be in boost data — use address prefix
                                sym  = d.get("description", addr[:8]) or addr[:8]
                                if addr and addr not in tokens:
                                    tokens[addr] = {"address": addr, "symbol": sym}
                except Exception as e:
                    print(f"[warn] DexScreener boosts: {e}")
                await asyncio.sleep(0.3)

            # ── 2. DexScreener search: keywords that surface real memecoins ──
            for q in ["solana meme", "solana pump", "solana cat", "solana dog",
                      "solana pepe", "solana wojak", "solana ai", "solana based"]:
                try:
                    async with session.get(
                        f"{DEXSCREENER}/search?q={q.replace(' ', '%20')}",
                        timeout=aiohttp.ClientTimeout(total=10),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for p in data.get("pairs", []):
                                if p.get("chainId") != "solana":
                                    continue
                                liq = (p.get("liquidity") or {}).get("usd", 0) or 0
                                if liq < 5_000:
                                    continue
                                created = p.get("pairCreatedAt") or 0  # ms timestamp
                                # Prefer pairs at least 3 days old (enough candle history)
                                import time as _t
                                age_days = (_t.time() - created / 1000) / 86400 if created else 0
                                if age_days < 3:
                                    continue
                                addr = p.get("baseToken", {}).get("address", "")
                                sym  = p.get("baseToken", {}).get("symbol", "?")
                                if addr and addr not in tokens:
                                    tokens[addr] = {"address": addr, "symbol": sym}
                except Exception as e:
                    print(f"[warn] DexScreener search: {e}")
                await asyncio.sleep(0.3)
                if len(tokens) >= limit:
                    break

            # ── 3. Birdeye tokenlist sweep (high offset = finds older tokens) ─
            # Sort by last trade time descending to find recently active tokens
            if len(tokens) < limit:
                for offset in range(0, min((limit - len(tokens)) * 6, 1200), 50):
                    try:
                        async with session.get(
                            f"{BIRDEYE_API}/defi/tokenlist",
                            headers=self.headers,
                            params={
                                "sort_by":        "lastTradeUnixTime",
                                "sort_type":      "desc",
                                "offset":         offset,
                                "limit":          50,
                                "min_liquidity":  5_000,
                            },
                            timeout=aiohttp.ClientTimeout(total=12),
                        ) as resp:
                            if resp.status == 200:
                                data = await resp.json()
                                for t in data.get("data", {}).get("tokens", []):
                                    addr = t.get("address", "")
                                    sym  = t.get("symbol", "?")
                                    if not addr or addr in tokens:
                                        continue
                                    mc = t.get("mc") or 0
                                    if mc > 100_000_000:  # skip large-caps
                                        continue
                                    tokens[addr] = {"address": addr, "symbol": sym}
                            elif resp.status == 429:
                                await asyncio.sleep(3)
                    except Exception as e:
                        print(f"[warn] Birdeye: {e}")
                    await asyncio.sleep(0.3)
                    if len(tokens) >= limit:
                        break

        result = list(tokens.values())[:limit]
        return result

    async def _fetch_ohlcv(self, session, address: str, days: int) -> list:
        """Fetch historical 5-min OHLCV from Birdeye V3."""
        now       = int(_time.time())
        time_from = now - days * 86400
        candles   = []

        # Birdeye returns max 1000 records per call; 5-min for 14d = 4032 candles
        chunk_size = 1000 * 5 * 60   # 1000 candles × 5min in seconds
        t_start = time_from

        while t_start < now:
            t_end = min(t_start + chunk_size, now)
            try:
                async with session.get(
                    f"{BIRDEYE_API}/defi/v3/ohlcv",
                    headers=self.headers,
                    params={
                        "address":   address,
                        "type":      "5m",
                        "time_from": str(t_start),
                        "time_to":   str(t_end),
                    },
                    timeout=aiohttp.ClientTimeout(total=12),
                ) as resp:
                    if resp.status != 200:
                        break
                    data = await resp.json()
                    items = data.get("data", {}).get("items", [])
                    for c in items:
                        # v3 API uses unix_time/o/h/l/c/v; v1 uses unixTime/open/high/low/close/volume
                        candles.append([
                            c.get("unix_time") or c.get("unixTime", 0),
                            c.get("o") or c.get("open",  0),
                            c.get("h") or c.get("high",  0),
                            c.get("l") or c.get("low",   0),
                            c.get("c") or c.get("close", 0),
                            c.get("v") or c.get("volume", 0),
                        ])
                    if not items:
                        break
            except Exception:
                break
            t_start = t_end
            await asyncio.sleep(0.1)

        # Sort oldest-first, deduplicate by timestamp
        seen = set()
        unique = []
        for c in sorted(candles, key=lambda x: x[0]):
            if c[0] not in seen and c[4] > 0:
                seen.add(c[0])
                unique.append(c)
        return unique

    def _simulate(self, candles: list, symbol: str, address: str) -> list:
        """Walk candles applying dip-buy logic. Return list of BtTrade."""
        closes  = [c[4] for c in candles]
        highs   = [c[2] for c in candles]
        lows    = [c[3] for c in candles]
        volumes = [c[5] for c in candles]
        trades  = []
        in_trade_until  = -1   # candle index when current trade expires
        sl_cooldown_until = -1  # candle index when stop-loss cooldown expires

        for i in range(CANDLE_WINDOW + 72, len(candles) - MAX_HOLD_CANDLES - 1):
            if i <= in_trade_until:
                continue   # already in a position
            if i <= sl_cooldown_until:
                continue   # stop-loss cooldown active

            win = candles[i - CANDLE_WINDOW: i]
            if len(win) < CANDLE_WINDOW:
                continue

            wc = [c[4] for c in win]
            wh = [c[2] for c in win]
            wl = [c[3] for c in win]
            wv = [c[5] for c in win]
            current = wc[-1]

            # ── Macro trend: h6 = 72 × 5-min candles ────────────────────────
            if i >= 72:
                h6_ref = closes[i - 72]
                if h6_ref > 0:
                    h6_chg = (current - h6_ref) / h6_ref * 100
                    if h6_chg < H6_DROP_LIMIT:
                        continue

            # ── Grinder filter ───────────────────────────────────────────────
            peak_w  = max(wh)
            floor_w = min(wl)
            if floor_w <= 0 or peak_w / floor_w < 1.15:
                continue

            # ── Dip range ────────────────────────────────────────────────────
            if peak_w <= 0:
                continue
            dip_pct = (current - peak_w) / peak_w * 100
            if not (MIN_DIP_PCT <= dip_pct <= MAX_DIP_PCT):
                continue

            # ── RSI ──────────────────────────────────────────────────────────
            rsi = None
            if len(wc) >= 15:
                diffs  = [wc[j] - wc[j-1] for j in range(1, len(wc))]
                gains  = [d if d > 0 else 0.0 for d in diffs]
                losses = [-d if d < 0 else 0.0 for d in diffs]
                ag = sum(gains[-14:]) / 14
                al = sum(losses[-14:]) / 14
                rsi = 100.0 if al == 0 else 100.0 - (100.0 / (1.0 + ag / al))

            # Hard block: RSI > 80
            if rsi is not None and rsi > 80:
                continue

            # ── Rug-dump: 5 consecutive red 5m candles ───────────────────────
            if len(win) >= 5 and all(win[j][4] < win[j][1] for j in range(-5, 0)):
                continue

            # ── Mandatory last_green ─────────────────────────────────────────
            if len(wc) < 2 or wc[-1] <= wc[-2]:
                continue

            # ── Recovery signals ─────────────────────────────────────────────
            last_green  = True  # already checked above
            rsi_reset   = rsi is not None and 30.0 <= rsi <= 55.0

            vol_easing  = False
            if len(wv) >= 10:
                rec_avg  = sum(wv[-3:]) / 3
                pri_avg  = sum(wv[-10:-3]) / 7
                vol_easing = pri_avg > 0 and rec_avg < pri_avg * 0.70

            stabilizing = False
            if len(wc) >= 7:
                def _avg_move(sl, closes_ref):
                    return sum(
                        abs(closes_ref[k] - closes_ref[k-1]) / max(closes_ref[k-1], 1e-10)
                        for k in sl
                    ) / len(sl)
                stabilizing = (
                    _avg_move(range(-3, 0), wc) < _avg_move(range(-6, -3), wc) * 0.80
                )

            higher_low  = len(wl) >= 4 and wl[-1] > min(wl[-4:-1])

            # 1m momentum proxy: use sub-candles within the 5m window
            # We approximate using the last 5 five-min candles as a proxy
            momentum_1m = False
            if len(wc) >= 6:
                up = sum(1 for j in range(-5, 0) if wc[j] > wc[j-1])
                momentum_1m = up >= 3

            rec_score = sum([
                last_green, rsi_reset, vol_easing,
                stabilizing, higher_low, momentum_1m
            ])
            if rec_score < MIN_RECOVERY:
                continue

            # ── Entry confirmed — simulate forward ───────────────────────────
            entry_price    = current
            remaining      = 1.0    # fraction of position still held
            realized_pnl   = 0.0   # weighted pnl from partial sells (% × fraction)
            tp1_hit        = False
            tp2_hit        = False
            exit_reason    = "max-hold"
            hold_c         = 0
            final_close    = current

            for fwd in range(1, MAX_HOLD_CANDLES + 1):
                idx = i + fwd
                if idx >= len(candles):
                    break
                hi  = candles[idx][2]
                lo  = candles[idx][3]
                cls = candles[idx][4]
                final_close = cls
                hold_c = fwd

                # Stop loss (check low of candle)
                sl_price = entry_price * (1 - STOP_LOSS_PCT)
                if lo <= sl_price:
                    # Remaining fraction hits stop
                    realized_pnl += remaining * (-STOP_LOSS_PCT * 100)
                    remaining     = 0.0
                    exit_reason   = "stop-loss"
                    break

                # TP3 (check in reverse priority order on same candle)
                if remaining > 0 and hi >= entry_price * (1 + TP3_PCT) and tp2_hit:
                    sold          = remaining * TP3_SELL
                    realized_pnl += sold * TP3_PCT * 100
                    remaining    -= sold
                    exit_reason   = "tp3"

                # TP2
                elif remaining > 0 and hi >= entry_price * (1 + TP2_PCT) and tp1_hit and not tp2_hit:
                    sold          = remaining * TP2_SELL
                    realized_pnl += sold * TP2_PCT * 100
                    remaining    -= sold
                    tp2_hit       = True
                    exit_reason   = "tp2"

                # TP1 (only once)
                elif remaining > 0 and hi >= entry_price * (1 + TP1_PCT) and not tp1_hit:
                    sold          = remaining * TP1_SELL
                    realized_pnl += sold * TP1_PCT * 100
                    remaining    -= sold
                    tp1_hit       = True
                    if exit_reason == "max-hold":
                        exit_reason = "tp1"

                if remaining <= 0:
                    break

            # Any remaining fraction exits at last close (max-hold or partial)
            if remaining > 0:
                close_pnl     = (final_close / entry_price - 1) * 100 if entry_price > 0 else 0
                realized_pnl += remaining * close_pnl

            pnl_pct = realized_pnl

            trades.append(BtTrade(
                symbol=symbol,
                address=address,
                entry_ts=candles[i][0],
                entry_price=entry_price,
                exit_price=final_close,
                exit_reason=exit_reason,
                pnl_pct=pnl_pct,
                dip_pct=dip_pct,
                recovery_score=rec_score,
                hold_candles=hold_c,
            ))

            in_trade_until = i + hold_c
            # After a stop-loss, block re-entry on this token for 4h
            if exit_reason == "stop-loss":
                sl_cooldown_until = i + hold_c + SL_COOLDOWN_CANDLES

        return trades

    def _calculate_results(self, trades: list, tokens_tested: int) -> BtResult:
        if not trades:
            return BtResult(
                tokens_tested=tokens_tested, total_trades=0,
                wins=0, losses=0, win_rate=0, avg_win_pct=0,
                avg_loss_pct=0, profit_factor=0, total_pnl_usd=0,
                max_drawdown=0, best_trade=0, worst_trade=0, sharpe=0,
            )

        wins   = [t for t in trades if t.pnl_pct > 0]
        losses = [t for t in trades if t.pnl_pct <= 0]

        win_rate    = len(wins) / len(trades) * 100
        avg_win     = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
        avg_loss    = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
        gross_win   = sum(t.pnl_pct * POSITION_USD / 100 for t in wins)
        gross_loss  = abs(sum(t.pnl_pct * POSITION_USD / 100 for t in losses))
        pf          = gross_win / gross_loss if gross_loss > 0 else 999.0
        total_pnl   = sum(t.pnl_pct * POSITION_USD / 100 for t in trades)

        # Max drawdown on running equity
        equity = 0.0
        peak_eq = 0.0
        max_dd = 0.0
        for t in trades:
            equity += t.pnl_pct * POSITION_USD / 100
            if equity > peak_eq:
                peak_eq = equity
            dd = peak_eq - equity
            if dd > max_dd:
                max_dd = dd

        pnl_series = [t.pnl_pct for t in trades]
        if len(pnl_series) > 1:
            avg_r = sum(pnl_series) / len(pnl_series)
            std_r = statistics.stdev(pnl_series)
            sharpe = (avg_r / std_r) if std_r > 0 else 0
        else:
            sharpe = 0

        return BtResult(
            tokens_tested=tokens_tested,
            total_trades=len(trades),
            wins=len(wins),
            losses=len(losses),
            win_rate=win_rate,
            avg_win_pct=avg_win,
            avg_loss_pct=avg_loss,
            profit_factor=pf,
            total_pnl_usd=total_pnl,
            max_drawdown=max_dd,
            best_trade=max(t.pnl_pct for t in trades),
            worst_trade=min(t.pnl_pct for t in trades),
            sharpe=sharpe,
            trades=trades,
        )


async def main():
    parser = argparse.ArgumentParser(description="Dip-buy backtest using Birdeye OHLCV")
    parser.add_argument("--days",   type=int, default=14,  help="Days of history (default 14)")
    parser.add_argument("--tokens", type=int, default=200, help="Max tokens to test (default 200)")
    args = parser.parse_args()

    api_key = os.environ.get("BIRDEYE_API_KEY", "")
    if not api_key:
        # Try loading from config
        try:
            from utils.config import Config
            cfg = Config.load()
            api_key = cfg.birdeye_api_key
        except Exception:
            pass

    if not api_key:
        print("ERROR: BIRDEYE_API_KEY not set. Run with: BIRDEYE_API_KEY=your_key python backtest/dip_buy_backtest.py")
        sys.exit(1)

    bt = DipBuyBacktest(api_key)
    result = await bt.run(days=args.days, max_tokens=args.tokens)
    result.print_report()


if __name__ == "__main__":
    asyncio.run(main())
