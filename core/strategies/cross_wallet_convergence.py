"""
Strategy 6 — Cross-Wallet Convergence (Integrated)

Two or more INDEPENDENT high-quality wallets buying the same token
within 15 minutes = genuine conviction signal, not coordination.

When convergence is detected, routes through scanner.process_external_signal()
for security checks + position management.

Signal tiers:
  TIER 1 (2 wallets, 15min window)  → standard position
  TIER 2 (3 wallets, 15min window)  → strong conviction
  TIER 3 (4+ wallets, 15min window) → very strong conviction (rare)

Wallets are seeded from /data/seed_wallets.json (address → quality_score)
and grow dynamically as WalletClustering discovers new ones.
"""

import asyncio
import logging
import aiohttp
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set
from collections import defaultdict

logger = logging.getLogger(__name__)

CONVERGENCE_WINDOW_SECONDS = 900    # 15 minutes
MIN_WALLETS_FOR_SIGNAL     = 2
MIN_WALLET_QUALITY         = 50.0
MIN_CONVERGENCE_SCORE      = 55.0

# Rate limiting for enhanced Helius TX parsing
_ENHANCED_TX_THROTTLE_SECS = 1.0   # min gap between enhanced API calls


@dataclass
class WalletBuyEvent:
    wallet_address: str
    wallet_label: str
    wallet_quality_score: float
    token_address: str
    token_symbol: str
    buy_time: datetime
    buy_amount_usd: float
    tx_signature: str


@dataclass
class ConvergenceSignal:
    token_address: str
    token_symbol: str
    first_buy_time: datetime
    buy_events: List[WalletBuyEvent] = field(default_factory=list)
    executed: bool = False

    @property
    def wallet_count(self) -> int:
        return len({e.wallet_address for e in self.buy_events})

    @property
    def avg_wallet_quality(self) -> float:
        if not self.buy_events:
            return 0.0
        return sum(e.wallet_quality_score for e in self.buy_events) / len(self.buy_events)

    @property
    def window_seconds(self) -> float:
        if not self.buy_events:
            return 0.0
        times = [e.buy_time for e in self.buy_events]
        return (max(times) - min(times)).total_seconds()

    @property
    def tier(self) -> int:
        return min(self.wallet_count, 4)

    @property
    def convergence_score(self) -> float:
        count_score   = min(self.wallet_count / 4, 1.0) * 40
        quality_score = (self.avg_wallet_quality / 100) * 40
        speed = max(0, (CONVERGENCE_WINDOW_SECONDS - self.window_seconds)
                    / CONVERGENCE_WINDOW_SECONDS)
        speed_score = speed * 20
        return count_score + quality_score + speed_score

    def format_telegram(self) -> str:
        lines = "\n".join(
            f"  👛 {e.wallet_label} (score: {e.wallet_quality_score:.0f})"
            for e in self.buy_events
        )
        return (
            f"🤝 *Cross-Wallet Convergence* — Tier {self.tier}\n\n"
            f"🪙 ${self.token_symbol}\n"
            f"📊 Wallets: {self.wallet_count} independent\n"
            f"⭐ Score: {self.convergence_score:.0f}/100\n"
            f"⚡ Window: {self.window_seconds/60:.1f}min\n\n"
            f"Wallets:\n{lines}"
        )


class CrossWalletConvergenceStrategy:
    """
    Monitors independent quality wallets for convergent buying.
    When 2+ wallets buy the same token within 15 min, fires signal.
    """

    def __init__(self,
                 scanner,
                 telegram,
                 helius_api_key: str,
                 wallet_quality_scores: Dict[str, float] = None,
                 poll_interval_sec: int = 30):

        self.scanner          = scanner
        self.telegram         = telegram
        self.helius_rpc       = f"https://mainnet.helius-rpc.com/?api-key={helius_api_key}"
        self.helius_txn       = f"https://api.helius.xyz/v0/transactions?api-key={helius_api_key}"

        self.wallet_quality: Dict[str, float] = wallet_quality_scores or {}
        self.monitored_wallets: Set[str]       = set(self.wallet_quality.keys())
        self.poll_interval = poll_interval_sec

        # State
        self.active_signals: Dict[str, ConvergenceSignal] = {}
        self.known_txns:     Dict[str, Set[str]]           = defaultdict(set)
        self.traded_tokens:  Set[str]                      = set()

        # Enhanced TX rate limiting
        self._last_enhanced_call: float = 0.0

        # Stats
        self.signals_detected = 0
        self.trades_executed  = 0

    def add_wallet(self, address: str, quality_score: float = 65.0):
        """Add a wallet to monitor. Called by WalletClusteringStrategy."""
        self.wallet_quality[address] = quality_score
        self.monitored_wallets.add(address)
        logger.info(
            f"[CrossWalletConvergence] Added wallet "
            f"{address[:8]}… (score: {quality_score:.0f})"
        )

    def remove_wallet(self, address: str):
        """Remove a wallet from monitoring. Called by dashboard remove handler."""
        self.wallet_quality.pop(address, None)
        self.monitored_wallets.discard(address)
        self.known_txns.pop(address, None)
        logger.info(f"[CrossWalletConvergence] Removed wallet {address[:8]}…")

    def update_wallet_score(self, address: str, score: float):
        if address in self.wallet_quality:
            self.wallet_quality[address] = score

    async def run(self):
        logger.info(
            f"[CrossWalletConvergence] Started | "
            f"Monitoring {len(self.monitored_wallets)} wallets | "
            f"Min {MIN_WALLETS_FOR_SIGNAL} for signal | "
            f"Window: {CONVERGENCE_WINDOW_SECONDS//60}min"
        )
        while True:
            try:
                await self._poll_wallets()
                await self._check_expired_signals()
                await self._evaluate_active_signals()
            except Exception as e:
                logger.error(f"[CrossWalletConvergence] Error: {e}")
            await asyncio.sleep(self.poll_interval)

    async def _poll_wallets(self):
        for wallet in list(self.monitored_wallets):
            quality = self.wallet_quality.get(wallet, 0)
            if quality < MIN_WALLET_QUALITY:
                continue
            try:
                await self._check_wallet_transactions(wallet, quality)
            except Exception as e:
                logger.debug(f"[CrossWalletConvergence] Poll {wallet[:8]}: {e}")

    async def _check_wallet_transactions(self, wallet: str, quality: float):
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getSignaturesForAddress",
            "params": [wallet, {"limit": 5, "commitment": "confirmed"}]
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self.helius_rpc, json=payload,
                timeout=aiohttp.ClientTimeout(total=8)
            ) as resp:
                data = await resp.json()
                sigs = data.get("result", [])

        for sig_info in sigs:
            sig = sig_info.get("signature", "")
            if not sig or sig in self.known_txns[wallet]:
                continue
            self.known_txns[wallet].add(sig)
            # Prune to avoid unbounded memory growth
            if len(self.known_txns[wallet]) > 500:
                self.known_txns[wallet] = set(list(self.known_txns[wallet])[-500:])
            buy_event = await self._parse_buy_transaction(wallet, quality, sig)
            if buy_event:
                await self._record_buy_event(buy_event)

    async def _parse_buy_transaction(self, wallet: str, quality: float,
                                      signature: str) -> Optional[WalletBuyEvent]:
        # Throttle enhanced TX API calls to avoid burning credits
        now = time.monotonic()
        gap = now - self._last_enhanced_call
        if gap < _ENHANCED_TX_THROTTLE_SECS:
            await asyncio.sleep(_ENHANCED_TX_THROTTLE_SECS - gap)
        self._last_enhanced_call = time.monotonic()

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.helius_txn,
                    json={"transactions": [signature]},
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    data = await resp.json()
                    if not data:
                        return None
                    tx = data[0]

            events = tx.get("events", {})
            swap   = events.get("swap", {})
            if not swap:
                return None

            native_input  = swap.get("nativeInput")
            token_outputs = swap.get("tokenOutputs", [])
            if not native_input or not token_outputs:
                return None

            amount_sol = native_input.get("amount", 0) / 1e9
            if amount_sol < 0.05:
                return None

            token_out     = token_outputs[0]
            token_address = token_out.get("mint", "")
            token_symbol  = token_out.get("symbol", "?")
            if not token_address:
                return None

            # Approximate USD (sol price fetched from scanner's cache if available)
            sol_usd    = getattr(self.scanner, "_sol_usd", 150.0)
            amount_usd = amount_sol * sol_usd

            block_time = tx.get("timestamp", 0)
            buy_time   = (datetime.fromtimestamp(block_time, tz=timezone.utc)
                          if block_time else datetime.now(timezone.utc))

            return WalletBuyEvent(
                wallet_address=wallet,
                wallet_label=f"{wallet[:6]}…{wallet[-4:]}",
                wallet_quality_score=quality,
                token_address=token_address,
                token_symbol=token_symbol,
                buy_time=buy_time,
                buy_amount_usd=amount_usd,
                tx_signature=signature,
            )

        except Exception as e:
            logger.debug(f"[CrossWalletConvergence] Parse error: {e}")
            return None

    async def _record_buy_event(self, event: WalletBuyEvent):
        token = event.token_address
        if token in self.traded_tokens:
            return

        now = datetime.now(timezone.utc)

        if token not in self.active_signals:
            self.active_signals[token] = ConvergenceSignal(
                token_address=token,
                token_symbol=event.token_symbol,
                first_buy_time=event.buy_time,
            )

        signal = self.active_signals[token]

        # Don't count same wallet twice
        existing = {e.wallet_address for e in signal.buy_events}
        if event.wallet_address in existing:
            return

        # Check window
        window_end = signal.first_buy_time + timedelta(seconds=CONVERGENCE_WINDOW_SECONDS)
        if event.buy_time > window_end:
            # Start fresh
            self.active_signals[token] = ConvergenceSignal(
                token_address=token,
                token_symbol=event.token_symbol,
                first_buy_time=event.buy_time,
            )
            self.active_signals[token].buy_events.append(event)
            return

        signal.buy_events.append(event)
        logger.info(
            f"[CrossWalletConvergence] "
            f"{'🔥' if signal.wallet_count >= 3 else '📊'} "
            f"{event.token_symbol}: {signal.wallet_count} wallets | "
            f"{event.wallet_label} (score: {event.wallet_quality_score:.0f})"
        )

    async def _evaluate_active_signals(self):
        for token, signal in list(self.active_signals.items()):
            if signal.executed:
                continue
            if signal.wallet_count < MIN_WALLETS_FOR_SIGNAL:
                continue
            if signal.convergence_score < MIN_CONVERGENCE_SCORE:
                continue
            await self._execute_convergence_trade(signal)

    async def _execute_convergence_trade(self, signal: ConvergenceSignal):
        signal.executed = True
        self.traded_tokens.add(signal.token_address)
        self.signals_detected += 1

        logger.info(
            f"[CrossWalletConvergence] 🤝 SIGNAL: "
            f"{signal.token_symbol} | Tier {signal.tier} | "
            f"{signal.wallet_count} wallets | Score: {signal.convergence_score:.0f}"
        )

        reason = (
            f"CrossWalletConvergence Tier {signal.tier} | "
            f"{signal.wallet_count} wallets | "
            f"Score {signal.convergence_score:.0f}"
        )
        fired = await self.scanner.process_external_signal(
            token_address=signal.token_address,
            token_symbol=signal.token_symbol,
            reason=reason,
            signal_score=int(signal.convergence_score),
            strategy_tag="cross_wallet_convergence",
            skip_security=True,
        )
        if fired:
            self.trades_executed += 1
            await self.telegram.send(signal.format_telegram())

    async def _check_expired_signals(self):
        now = datetime.now(timezone.utc)
        for token in list(self.active_signals.keys()):
            signal = self.active_signals[token]
            if signal.executed:
                continue
            age = (now - signal.first_buy_time).total_seconds()
            if age > CONVERGENCE_WINDOW_SECONDS * 2:
                del self.active_signals[token]

    def get_stats(self) -> dict:
        return {
            "strategy":         "cross_wallet_convergence",
            "wallets_monitored": len(self.monitored_wallets),
            "active_signals":    len([s for s in self.active_signals.values()
                                      if not s.executed]),
            "signals_detected":  self.signals_detected,
            "trades_executed":   self.trades_executed,
        }
