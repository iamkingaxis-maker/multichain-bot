"""
Strategy 3 — Wallet Clustering (Integrated)

Discovers undiscovered alpha wallets by analyzing who bought winning
tokens early — before they appear on any leaderboard.

How it works:
  1. Every 4 hours: fetch recent tokens that made 5x+ gains on Solana
  2. For each winner, find wallets that bought in the first 10 minutes
  3. Score wallets across multiple wins
  4. Wallets with 3+ early wins → qualify and auto-add to
     CrossWalletConvergenceStrategy for real-time monitoring

Credit-conscious: caps at 10 tokens/cycle, 5 enhanced TX parses/token.
Budget: ~12.5 enhanced Helius API calls/hour.
"""

import asyncio
import logging
import aiohttp
import time
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

HELIUS_RPC_TPL = "https://mainnet.helius-rpc.com/?api-key={api_key}"
HELIUS_TXN_TPL = "https://api.helius.xyz/v0/transactions?api-key={api_key}"

MIN_GAIN_X          = 5.0    # Token must have gained 5x+ to qualify
EARLY_BUYER_MINUTES = 10     # Must have bought in first 10 min
MIN_WINS_TO_QUALIFY = 3      # Wallet must appear early on 3+ winners
LOOKBACK_HOURS      = 72

# Credit-saving caps
MAX_TOKENS_PER_CYCLE = 10    # Max winning tokens to analyse per scan
MAX_TXS_PER_TOKEN    = 5     # Max enhanced TX parses per token
_ENHANCED_TX_THROTTLE_SECS = 1.5  # Pause between enhanced API calls


@dataclass
class WalletScore:
    address: str
    wins: int = 0
    avg_gain_on_wins: float = 0.0
    earliest_buy_minutes: List[float] = field(default_factory=list)
    winning_tokens: List[str] = field(default_factory=list)
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    @property
    def avg_early_minutes(self) -> float:
        if not self.earliest_buy_minutes:
            return 0.0
        return sum(self.earliest_buy_minutes) / len(self.earliest_buy_minutes)

    @property
    def cluster_score(self) -> float:
        win_score   = min(self.wins / 10, 1.0) * 40
        speed_score = max(0, (10 - self.avg_early_minutes) / 10) * 25
        # Win rate approximated as 1.0 (we only add winners)
        wr_score    = 35
        return win_score + wr_score + speed_score

    def summary(self) -> str:
        return (
            f"Wallet {self.address[:8]}… | "
            f"Score: {self.cluster_score:.0f}/100 | "
            f"Wins: {self.wins} | "
            f"Avg entry: {self.avg_early_minutes:.1f}min after launch"
        )


@dataclass
class WinningToken:
    address: str
    symbol: str
    launch_time: datetime
    peak_gain_x: float
    early_buyers: List[str] = field(default_factory=list)


class WalletClusteringStrategy:
    """
    Discovers alpha wallets from winning token early-buyer analysis.
    Qualified wallets are fed into CrossWalletConvergenceStrategy.
    """

    def __init__(self,
                 helius_api_key: str,
                 telegram,
                 convergence_strategy=None,
                 min_cluster_score: float = 60.0,
                 rescan_interval_hours: int = 4):

        self.helius_rpc    = HELIUS_RPC_TPL.format(api_key=helius_api_key)
        self.helius_txn    = HELIUS_TXN_TPL.format(api_key=helius_api_key)
        self._helius_enabled = bool(helius_api_key)
        self.telegram      = telegram
        self.convergence   = convergence_strategy   # CrossWalletConvergenceStrategy
        self.min_score     = min_cluster_score
        self.rescan_secs   = rescan_interval_hours * 3600

        # State
        self.wallet_scores:    Dict[str, WalletScore] = {}
        self.qualified:        Set[str]               = set()
        self.tokens_analyzed:  Set[str]               = set()

        # Rate limiting
        self._last_enhanced: float = 0.0

        # Stats
        self.tokens_scanned       = 0
        self.wallets_discovered   = 0
        self.wallets_qualified    = 0

    def set_convergence_strategy(self, strategy):
        """Wire in a CrossWalletConvergenceStrategy after construction."""
        self.convergence = strategy

    async def run(self):
        logger.info(
            f"[WalletClustering] Started | "
            f"Looking for wallets early on {MIN_GAIN_X}x+ tokens | "
            f"Rescan every {self.rescan_secs//3600:.0f}h"
        )
        while True:
            try:
                await self._scan_cycle()
            except Exception as e:
                logger.error(f"[WalletClustering] Scan error: {e}")
            await asyncio.sleep(self.rescan_secs)

    async def _scan_cycle(self):
        if not self._helius_enabled:
            return
        logger.info("[WalletClustering] Starting scan cycle…")
        winners = await self._find_winning_tokens()
        logger.info(f"[WalletClustering] Found {len(winners)} winning tokens to analyse")

        # Prune tokens_analyzed to prevent unbounded memory growth (keep last 1000)
        if len(self.tokens_analyzed) > 1000:
            self.tokens_analyzed = set(list(self.tokens_analyzed)[-1000:])

        for token in winners:
            if token.address in self.tokens_analyzed:
                continue
            self.tokens_analyzed.add(token.address)
            self.tokens_scanned += 1

            buyers = await self._find_early_buyers(token)
            token.early_buyers = [b for b, _ in buyers]

            for wallet, tx_time in buyers:
                await self._score_wallet(wallet, token, tx_time)

        await self._evaluate_wallets()

    async def _find_winning_tokens(self) -> List[WinningToken]:
        winners = []
        try:
            url = "https://api.dexscreener.com/latest/dex/search?q=solana"
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status != 200:
                        return []
                    data  = await resp.json()
                    pairs = data.get("pairs", [])

            cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)

            for pair in pairs:
                if pair.get("chainId") != "solana":
                    continue

                pc_h24 = pair.get("priceChange", {}).get("h24", 0) or 0
                gain_x = (pc_h24 / 100) + 1
                if gain_x < MIN_GAIN_X:
                    continue

                created_ms = pair.get("pairCreatedAt", 0)
                if not created_ms:
                    continue
                launch_time = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
                if launch_time < cutoff:
                    continue

                token_addr = pair.get("baseToken", {}).get("address", "")
                token_sym  = pair.get("baseToken", {}).get("symbol", "?")

                if token_addr and token_addr not in self.tokens_analyzed:
                    winners.append(WinningToken(
                        address=token_addr,
                        symbol=token_sym,
                        launch_time=launch_time,
                        peak_gain_x=gain_x,
                    ))

        except Exception as e:
            logger.error(f"[WalletClustering] Find winners error: {e}")

        return winners[:MAX_TOKENS_PER_CYCLE]

    async def _find_early_buyers(self, token: WinningToken) -> List[str]:
        """Find wallets that bought within the first EARLY_BUYER_MINUTES."""
        early_buyers = []
        try:
            cutoff = token.launch_time + timedelta(minutes=EARLY_BUYER_MINUTES)

            payload = {
                "jsonrpc": "2.0", "id": 1,
                "method": "getSignaturesForAddress",
                "params": [token.address, {"limit": 50, "commitment": "confirmed"}]
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.helius_rpc, json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    data = await resp.json()
                    signatures = data.get("result", [])

            early_sigs = []
            for sig_info in signatures:
                block_time = sig_info.get("blockTime", 0)
                if not block_time:
                    continue
                tx_time = datetime.fromtimestamp(block_time, tz=timezone.utc)
                if token.launch_time <= tx_time <= cutoff:
                    early_sigs.append((sig_info["signature"], tx_time))

            for sig, tx_time in early_sigs[:MAX_TXS_PER_TOKEN]:
                buyer = await self._parse_buyer_from_tx(sig)
                if buyer and buyer not in early_buyers:
                    early_buyers.append((buyer, tx_time))

        except Exception as e:
            logger.debug(f"[WalletClustering] Early buyer error {token.symbol}: {e}")

        return early_buyers

    async def _parse_buyer_from_tx(self, signature: str) -> Optional[str]:
        # Throttle enhanced TX calls to protect Helius credits
        now = time.monotonic()
        gap = now - self._last_enhanced
        if gap < _ENHANCED_TX_THROTTLE_SECS:
            await asyncio.sleep(_ENHANCED_TX_THROTTLE_SECS - gap)
        self._last_enhanced = time.monotonic()

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

            for acct in tx.get("accountData", []):
                if acct.get("nativeBalanceChange", 0) < 0:
                    return acct.get("account")

        except Exception:
            pass
        return None

    async def _score_wallet(self, wallet: str, token: WinningToken,
                            tx_time: datetime):
        if wallet not in self.wallet_scores:
            self.wallet_scores[wallet] = WalletScore(
                address=wallet,
                first_seen=datetime.now(timezone.utc),
            )
            self.wallets_discovered += 1

        score = self.wallet_scores[wallet]
        score.wins += 1
        score.winning_tokens.append(token.address)
        score.avg_gain_on_wins = (
            (score.avg_gain_on_wins * (score.wins - 1) + token.peak_gain_x)
            / score.wins
        )
        score.last_seen = datetime.now(timezone.utc)
        minutes_from_launch = (tx_time - token.launch_time).total_seconds() / 60
        score.earliest_buy_minutes.append(minutes_from_launch)

    async def _evaluate_wallets(self):
        for wallet, score in self.wallet_scores.items():
            if wallet in self.qualified:
                continue
            if score.wins >= MIN_WINS_TO_QUALIFY and score.cluster_score >= self.min_score:
                self.qualified.add(wallet)
                self.wallets_qualified += 1

                logger.info(f"[WalletClustering] NEW QUALIFIED WALLET: {score.summary()}")

                await self.telegram.send(
                    f"🔍 *New Alpha Wallet Discovered*\n\n"
                    f"👛 `{wallet[:8]}…{wallet[-4:]}`\n"
                    f"⭐ Cluster Score: {score.cluster_score:.0f}/100\n"
                    f"🏆 Early on {score.wins} winning tokens\n"
                    f"📈 Avg gain on wins: {score.avg_gain_on_wins:.1f}x\n"
                    f"⚡ Avg entry: {score.avg_early_minutes:.1f}min after launch\n\n"
                    f"Adding to convergence monitoring automatically."
                )

                if self.convergence:
                    self.convergence.add_wallet(wallet, quality_score=score.cluster_score)

    def get_stats(self) -> dict:
        return {
            "strategy":          "wallet_clustering",
            "tokens_scanned":    self.tokens_scanned,
            "wallets_discovered": self.wallets_discovered,
            "wallets_qualified":  self.wallets_qualified,
        }
