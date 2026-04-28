"""
Performance Tracker & Dashboard
Tracks Scanner, Copy Trader, and Scalper trades separately.
Persists trades to DATA_DIR/trades.json (defaults to ./trades.json locally,
/data/trades.json on Railway when DATA_DIR=/data).
"""

import asyncio
import logging
import json
import os
from datetime import datetime, timezone
from typing import Dict, List

logger = logging.getLogger(__name__)

DATA_DIR = os.environ.get("DATA_DIR", ".")
TRADE_LOG_FILE = os.path.join(DATA_DIR, "trades.json")
CLOSED_LOG_FILE = os.path.join(DATA_DIR, "closed_positions.csv")  # append-only, never reset


class PerformanceTracker:
    def __init__(self):
        self.trades: List[dict] = []
        self.scalpers = []          # registered scalper instances
        self._rug_blacklist: Dict[str, datetime] = {}  # token_address.lower() → expiry
        self._load_trades()

    def is_rugged(self, token_address: str) -> bool:
        """Return True if this token is in the rug blacklist and the ban hasn't expired."""
        expiry = self._rug_blacklist.get(token_address.lower())
        if expiry is None:
            return False
        if datetime.now(timezone.utc) >= expiry:
            del self._rug_blacklist[token_address.lower()]
            return False
        return True

    # ── Registration ─────────────────────────────────────────────────────

    def register_scalper(self, scalper):
        """Register a scalper so the dashboard can show its stats."""
        self.scalpers.append(scalper)

    # ── Trade Recording ──────────────────────────────────────────────────

    def record_buy(self, position):
        # Prefer strategy field if set; fall back to detecting from reason string
        strategy = getattr(position, "strategy", None) or self._detect_strategy(
            getattr(position, "reason", "")
        )
        trade = {
            "type": "buy",
            "strategy": strategy,
            "chain": getattr(position, "chain_id", "solana"),
            "token": getattr(position, "token_symbol", "?"),
            "address": getattr(position, "token_address", ""),
            "entry_price": getattr(position, "entry_price_usd", 0),
            "amount_usd": getattr(position, "amount_usd", 0) or getattr(position, "amount_sol_spent", 0),
            "time": datetime.now(timezone.utc).isoformat(),
            "reason": getattr(position, "reason", ""),
            "pair_address": getattr(position, "pair_address", "") or "",
            "entry_market_cap_usd": getattr(position, "entry_market_cap_usd", 0.0) or 0.0,
            "entry_age_hours": getattr(position, "entry_age_hours", 0.0) or 0.0,
            "entry_volume_h1_usd": getattr(position, "entry_volume_h1_usd", 0.0) or 0.0,
            "entry_meta": getattr(position, "entry_meta", None) or {},
        }
        self.trades.append(trade)
        self._save_trades()

    def record_sell(self, token_address: str, usd_received: float,
                    pnl: float, reason: str, pnl_pct: float = 0.0, **extra):
        # Try to get token symbol and chain from matching buy
        token_symbol = "?"
        chain = "unknown"
        entry_price = 0.0
        amount_usd = 0.0
        for t in reversed(self.trades):
            if t.get("type") == "buy" and t.get("address") == token_address:
                token_symbol = t.get("token", "?")
                chain = t.get("chain", "unknown")
                entry_price = t.get("entry_price", 0.0)
                amount_usd = t.get("amount_usd", 0.0)
                break

        # Derive pnl_pct from cost basis if caller didn't supply it
        if pnl_pct == 0.0 and amount_usd > 0 and pnl != 0.0:
            pnl_pct = round(pnl / amount_usd * 100, 2)

        exit_price = usd_received  # caller passes total USD, store as-is

        trade = {
            "type": "sell",
            "strategy": self._detect_strategy(reason),
            "chain": chain,
            "token": token_symbol,
            "address": token_address,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "usd_received": usd_received,
            "pnl": pnl,
            "pnl_pct": pnl_pct,
            "time": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            **extra,
        }
        self.trades.append(trade)
        self._save_trades()
        self._append_closed_position(trade)

    # ── Helpers ──────────────────────────────────────────────────────────

    def _detect_strategy(self, reason: str) -> str:
        r = reason.upper()
        if "SCALP" in r:
            return "scalper"
        elif "COPY" in r:
            return "copy"
        elif "PUMP" in r:
            return "pump"
        else:
            return "scanner"

    def _chain_key(self, chain: str) -> str:
        """Normalize chain id to sol/base/bnb for display."""
        c = chain.lower()
        if c in ("solana", "sol"):
            return "sol"
        elif c in ("base",):
            return "base"
        elif c in ("bsc", "bnb"):
            return "bnb"
        return c

    # ── Stats ─────────────────────────────────────────────────────────────

    def get_stats(self, strategy: str = None) -> dict:
        sells = [t for t in self.trades if t["type"] == "sell"]
        if strategy:
            sells = [t for t in sells if t.get("strategy") == strategy]
        if not sells:
            return {
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "total_pnl": 0,
                "avg_win": 0, "avg_loss": 0,
                "best_trade": 0, "worst_trade": 0
            }
        wins = [t for t in sells if t["pnl"] > 0]
        losses = [t for t in sells if t["pnl"] <= 0]
        return {
            "total_trades": len(sells),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(sells) * 100,
            "total_pnl": sum(t["pnl"] for t in sells),
            "avg_win": sum(t["pnl"] for t in wins) / len(wins) if wins else 0,
            "avg_loss": sum(t["pnl"] for t in losses) / len(losses) if losses else 0,
            "best_trade": max((t["pnl"] for t in sells), default=0),
            "worst_trade": min((t["pnl"] for t in sells), default=0)
        }

    def get_all_trades(self) -> List[dict]:
        """Return all recorded trades (buys and sells)."""
        return list(self.trades)

    def get_cumulative_pnl(self) -> List[dict]:
        """
        Return cumulative P&L series for chart, one entry per completed sell.
        Each entry: {"trade_num": n, "cumulative": x, "time": isostr}
        """
        sells = [t for t in self.trades if t["type"] == "sell"]
        result = []
        running = 0.0
        for i, t in enumerate(sells, 1):
            running += t.get("pnl", 0)
            result.append({
                "trade_num": i,
                "cumulative": round(running, 4),
                "time": t.get("time", "")
            })
        return result

    def get_drawdown_stats(self) -> dict:
        """Return peak P&L, current drawdown, and max drawdown from trade history."""
        series = self.get_cumulative_pnl()
        if not series:
            return {"peak_pnl": 0.0, "current_pnl": 0.0,
                    "current_drawdown": 0.0, "max_drawdown": 0.0, "max_drawdown_pct": 0.0}
        peak = 0.0
        max_dd = 0.0
        for pt in series:
            val = pt["cumulative"]
            if val > peak:
                peak = val
            dd = peak - val
            if dd > max_dd:
                max_dd = dd
        current = series[-1]["cumulative"]
        current_dd = max(0.0, peak - current)
        max_dd_pct = (max_dd / peak * 100) if peak > 0 else 0.0
        return {
            "peak_pnl":         round(peak, 2),
            "current_pnl":      round(current, 2),
            "current_drawdown": round(current_dd, 2),
            "max_drawdown":     round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 1),
        }

    def get_daily_pnl(self) -> float:
        """Return today's realized P&L (UTC date)."""
        today = datetime.now(timezone.utc).date().isoformat()
        total = 0.0
        for t in self.trades:
            if t["type"] == "sell":
                t_date = t.get("time", "")[:10]
                if t_date == today:
                    total += t.get("pnl", 0)
        return total

    def get_chain_stats(self) -> dict:
        """Return per-chain P&L breakdown from completed sells."""
        result = {"sol": {"pnl": 0.0, "capital": 0.0, "positions": 0},
                  "base": {"pnl": 0.0, "capital": 0.0, "positions": 0},
                  "bnb": {"pnl": 0.0, "capital": 0.0, "positions": 0}}

        for t in self.trades:
            key = self._chain_key(t.get("chain", ""))
            if key not in result:
                result[key] = {"pnl": 0.0, "capital": 0.0, "positions": 0}
            if t["type"] == "sell":
                result[key]["pnl"] += t.get("pnl", 0)
            elif t["type"] == "buy":
                result[key]["capital"] += t.get("amount_usd", 0)

        # Count open positions per chain from live scalpers
        for scalper in self.scalpers:
            try:
                stats = scalper.get_stats()
                chain_raw = stats.get("chain", "")
                key = self._chain_key(chain_raw)
                if key in result:
                    result[key]["positions"] += stats.get("active_scalps", 0)
            except Exception:
                pass

        return result

    # ── Dashboard Stats (web) ─────────────────────────────────────────────

    def get_dashboard_stats(self) -> dict:
        """Return stats in format expected by web dashboard."""
        overall = self.get_stats()
        scanner = self.get_stats("scanner")
        copy = self.get_stats("copy")
        scalper_stats = self.get_stats("scalper")

        sells = [t for t in self.trades if t["type"] == "sell"]
        recent_sells = list(reversed(sells[-50:]))

        # Open positions from live scalper instances
        open_positions = []
        for sc in self.scalpers:
            try:
                sc_stats = sc.get_stats()
                chain_raw = sc_stats.get("chain", "unknown")
            except Exception:
                chain_raw = "unknown"
            try:
                for addr, pos in (sc.open_positions_ref or {}).items():
                    symbol = getattr(pos, "token_symbol", addr[:8])
                    entry = getattr(pos, "entry_price_usd", 0)
                    # Use synced live price (updated by PositionManager every 30s)
                    current = getattr(pos, "current_price_usd", 0)
                    if current <= 0:
                        current = entry  # fall back to entry price until first update
                    # Use USD amount stored at entry (not SOL amount)
                    amount = getattr(pos, "amount_usd", 0) or getattr(pos, "amount_sol_spent", 0)
                    multiplier = (current / entry) if entry > 0 else 1.0
                    # Use stored pnl_usd if available (synced by PositionManager)
                    pnl_usd = getattr(pos, "pnl_usd", None)
                    if pnl_usd is None:
                        pnl_usd = (multiplier - 1) * amount
                    opened_at = getattr(pos, "entry_time",
                                        getattr(pos, "buy_time",
                                                getattr(pos, "open_time", None)))
                    hold_secs = 0
                    if opened_at:
                        try:
                            if isinstance(opened_at, (int, float)):
                                import time
                                hold_secs = int(time.time() - opened_at)
                            else:
                                hold_secs = int(
                                    (datetime.now(timezone.utc) - opened_at)
                                    .total_seconds()
                                )
                        except Exception:
                            hold_secs = 0
                    open_positions.append({
                        "token_address": addr,
                        "symbol": symbol,
                        "chain": getattr(pos, "chain_id", chain_raw),
                        "strategy": getattr(pos, "strategy", "scanner"),
                        "entry_price": entry,
                        "pnl_usd": round(pnl_usd, 2),
                        "multiplier": round(multiplier, 4),
                        "hold_secs": hold_secs,
                        "amount_usd": amount,
                    })
            except Exception:
                pass

        # Security stats from registered providers (filled by web_dashboard merge)
        return {
            "overall": overall,
            "daily_pnl": self.get_daily_pnl(),
            "strategies": {
                "scanner": scanner,
                "copy": copy,
                "scalper": scalper_stats
            },
            "chains": self.get_chain_stats(),
            "positions": open_positions,
            "recent_trades": recent_sells,
        }

    # ── Persistence ───────────────────────────────────────────────────────

    def _save_trades(self):
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(TRADE_LOG_FILE, "w") as f:
                json.dump(self.trades, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save trades: {e}")

    def _load_trades(self):
        if os.path.exists(TRADE_LOG_FILE):
            try:
                with open(TRADE_LOG_FILE, "r") as f:
                    self.trades = json.load(f)
                logger.info(f"Loaded {len(self.trades)} historical trades from {TRADE_LOG_FILE}")
            except Exception:
                self.trades = []

        # Close any orphaned open positions left over from a previous run.
        # On restart, the in-memory position state is gone — write a synthetic
        # "cancelled on restart" sell so the trade log stays consistent.
        #
        # SKIPPED IN LIVE MODE: real positions are real on-chain holdings; a
        # synthetic sell would silently zero them out in the trade log while
        # the tokens remain in the wallet.  Live restart-recovery is handled
        # by Trader.reconcile_positions_on_startup which queries the wallet
        # and Trader._restore_open_positions which rebuilds in-memory state
        # from the persisted open_positions.json.  See incident 2026-04-28
        # (TripleT) for the cause of this guard.
        _is_paper = not os.environ.get("SOLANA_PRIVATE_KEY", "").strip() \
                    or os.environ.get("PAPER_MODE", "").lower() in ("1", "true", "yes")
        if not _is_paper:
            logger.info("[Tracker] Live mode — skipping cancel-on-restart synthetic sells")
            return
        open_addrs: dict = {}  # address → buy trade
        for t in self.trades:
            addr = t.get("address", "").lower()
            if not addr:
                continue
            if t["type"] == "buy":
                open_addrs[addr] = t
            elif t["type"] == "sell":
                open_addrs.pop(addr, None)

        if open_addrs:
            now = datetime.now(timezone.utc).isoformat()
            for addr, buy in open_addrs.items():
                amount_usd = buy.get("amount_usd", 0.0)
                synthetic_sell = {
                    "type": "sell",
                    "strategy": buy.get("strategy", "scanner"),
                    "chain": buy.get("chain", "solana"),
                    "token": buy.get("token", "?"),
                    "address": buy.get("address", addr),
                    "entry_price": buy.get("entry_price", 0.0),
                    "exit_price": amount_usd,
                    "usd_received": amount_usd,
                    "pnl": 0.0,
                    "pnl_pct": 0.0,
                    "time": now,
                    "reason": "cancelled on restart",
                    "max_drawdown_pct": 0.0,
                    "hold_secs": 0,
                    "entry_market_cap_usd": buy.get("entry_market_cap_usd", 0.0),
                    "entry_age_hours": buy.get("entry_age_hours", 0.0),
                    "entry_volume_h1_usd": buy.get("entry_volume_h1_usd", 0.0),
                    # Carry forward Batch 1 fields from the buy so the synthetic
                    # sell mirrors the real-sell schema and downstream analysis
                    # doesn't see schema gaps for restart-cancelled positions.
                    "pair_address": buy.get("pair_address", "") or "",
                    "entry_meta": buy.get("entry_meta", {}) or {},
                    "peak_pnl_pct": 0.0,        # no hold occurred — cancellation is at $0 PnL
                    "peak_pnl_at_secs": 0,
                }
                self.trades.append(synthetic_sell)
                logger.info(
                    f"[Tracker] Closed orphaned position on restart: "
                    f"{buy.get('token', addr)} ({addr[:8]}…)"
                )
            self._save_trades()

    _CSV_COLUMNS = ["time", "chain", "token", "address",
                    "entry_price", "exit_price", "pnl", "pnl_pct",
                    "reason", "strategy", "max_drawdown_pct", "hold_secs",
                    "entry_market_cap_usd", "entry_age_hours", "entry_volume_h1_usd"]

    def _append_closed_position(self, trade: dict):
        """Append-only CSV log — never cleared by reset, survives restarts."""
        import csv
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            cols = self._CSV_COLUMNS
            if not os.path.exists(CLOSED_LOG_FILE):
                # Fresh file — write header then row.
                with open(CLOSED_LOG_FILE, "w", newline="") as f:
                    writer = csv.writer(f)
                    writer.writerow(cols)
                    writer.writerow([trade.get(c, "") for c in cols])
                return
            # File exists — check if header has all current columns.
            with open(CLOSED_LOG_FILE, "r", newline="") as f:
                existing_cols = next(csv.reader(f), [])
            if existing_cols != cols:
                # One-time migration: rewrite with new header, padding missing columns.
                # New columns that were being written before the header was updated
                # land in DictReader's restkey (None) as a list — rescue them.
                new_cols = [c for c in cols if c not in existing_cols]
                with open(CLOSED_LOG_FILE, "r", newline="") as f:
                    reader = csv.DictReader(f)
                    rows = []
                    for row in reader:
                        overflow = row.pop(None, None) or []
                        for i, col in enumerate(new_cols):
                            row[col] = overflow[i] if i < len(overflow) else ""
                        rows.append(row)
                with open(CLOSED_LOG_FILE, "w", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                    writer.writeheader()
                    for row in rows:
                        writer.writerow({c: row.get(c, "") for c in cols})
                logger.info(f"[Tracker] Migrated closed_positions.csv to add: "
                            f"{set(cols) - set(existing_cols)}")
            with open(CLOSED_LOG_FILE, "a", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([trade.get(c, "") for c in cols])
        except Exception as e:
            logger.error(f"Failed to append closed position: {e}")

    # ── Console Dashboard (background task) ───────────────────────────────

    async def run_dashboard(self):
        while True:
            await asyncio.sleep(300)
            self._print_dashboard()

    def _print_dashboard(self):
        overall = self.get_stats()
        scanner = self.get_stats("scanner")
        copy = self.get_stats("copy")
        scalper = self.get_stats("scalper")

        print("\n" + "=" * 56)
        print("  MULTI-CHAIN BOT v2 — PERFORMANCE DASHBOARD")
        print("=" * 56)

        print(f"\n  OVERALL")
        print(f"  Total Trades:  {overall['total_trades']} | "
              f"Win Rate: {overall['win_rate']:.1f}% | "
              f"PnL: ${overall['total_pnl']:+,.2f}")

        print(f"\n  SCANNER")
        print(f"  Trades: {scanner['total_trades']} | "
              f"Win Rate: {scanner['win_rate']:.1f}% | "
              f"PnL: ${scanner['total_pnl']:+,.2f}")

        print(f"\n  COPY TRADER")
        print(f"  Trades: {copy['total_trades']} | "
              f"Win Rate: {copy['win_rate']:.1f}% | "
              f"PnL: ${copy['total_pnl']:+,.2f}")

        print(f"\n  SCALPER")
        print(f"  Trades: {scalper['total_trades']} | "
              f"Win Rate: {scalper['win_rate']:.1f}% | "
              f"PnL: ${scalper['total_pnl']:+,.2f}")

        if self.scalpers:
            print(f"\n  LIVE SCALPER STATE")
            for s in self.scalpers:
                try:
                    stats = s.get_stats()
                    print(f"  [{stats['chain']}] Capital: "
                          f"${stats['available_capital']:.0f} | "
                          f"Active: {stats['active_scalps']} | "
                          f"Today: ${stats['daily_pnl']:+.2f}")
                except Exception:
                    pass

        recent = [t for t in self.trades if t["type"] == "sell"][-6:]
        if recent:
            print(f"\n  RECENT TRADES")
            for t in reversed(recent):
                pnl = t.get("pnl", 0)
                mark = "+" if pnl > 0 else "-"
                strat = t.get("strategy", "?")[:4].upper()
                print(f"  [{mark}] [{strat}] PnL: ${pnl:+.2f} | {t.get('reason', '')[:35]}")

        print("=" * 56 + "\n")
