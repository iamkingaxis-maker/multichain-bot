"""SMART-MONEY FOLLOW (free-RPC, 2026-06-08) — NEW strategy, runs 24/7 on Railway.

⚠️ This REPLACES the dormant Helius-wired cross_wallet_convergence.py. It uses ONLY free public
Solana RPC — no Helius, no paid APIs. Ignore the old convergence module; this is the live one.

Signal (mined 2026-06-07, see reference_smart_wallet_followsignal_2026_06_07):
  when K (default 3) distinct ELITE wallets from the watchlist BUY the same token within a rolling
  WINDOW (default 10min) -> route the token through scanner.process_external_signal() as a buy.
  (83% winner coverage, ~50min ahead of the median buyer.)

It reuses the bot's EXISTING infra: process_external_signal runs security + chart-dip checks, then
fires a buy through the shared trader (PAPER whenever PAPER_MODE=true — config.py force-clears the
private key, so trader.private_key=='' => every path is paper). Position management / exits are
handled by the normal PositionManager, identical to the fleet. Trades are tagged strategy='smart_follow'.

Money-safety: opens NO position itself; only calls process_external_signal, which is paper under
PAPER_MODE=true. Never touches PAPER_MODE. Fail-soft: any RPC/parse error is swallowed per-wallet.
"""
import asyncio
import json
import logging
import time
import aiohttp
import os

logger = logging.getLogger(__name__)

# Per-fire signal log (2026-06-08): record WHICH elite wallets triggered each follow
# so trade outcomes can be attributed back to specific wallets (join to trades by token)
# -> identify junk wallets empirically from live results. Capped so it can't grow unbounded.
_FOLLOW_LOG = os.path.join(os.environ.get("DATA_DIR", "."), "follow_signals.jsonl")
_FOLLOW_LOG_CAP = 5_000_000  # ~5MB; trims oldest half when exceeded

RPCS = ["https://api.mainnet-beta.solana.com", "https://solana.leorpc.com/?api_key=FREE"]
STABLE = {"So11111111111111111111111111111111111111112",
          "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
          "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
UA = {"User-Agent": "Mozilla/5.0"}


def _append_follow_signal(rec: dict):
    """Append one follow-fire record to _FOLLOW_LOG (fail-soft, size-capped)."""
    try:
        import time as _t
        line = json.dumps(rec, separators=(",", ":")) + "\n"
        with open(_FOLLOW_LOG, "a") as f:
            f.write(line)
        # cheap cap: if oversized, keep the newest half
        if os.path.getsize(_FOLLOW_LOG) > _FOLLOW_LOG_CAP:
            with open(_FOLLOW_LOG) as f:
                lines = f.readlines()
            with open(_FOLLOW_LOG, "w") as f:
                f.writelines(lines[len(lines) // 2:])
    except Exception:
        pass  # tracking must never break the strategy


class SmartMoneyFollowStrategy:
    def __init__(self, scanner, telegram=None, watchlist=None, quality=None,
                 k=3, window_sec=600, poll_interval_sec=120, fire_cooldown_sec=3600,
                 min_signal_score=70):
        self.scanner = scanner
        self.telegram = telegram
        self.watchlist = list(watchlist or [])
        self.quality = quality or {}
        self.k = k
        self.window_sec = window_sec
        self.poll_interval = poll_interval_sec
        self.fire_cooldown = fire_cooldown_sec
        self.min_signal_score = min_signal_score
        self._rr = 0
        self._seen = {}          # wallet -> set(recent sigs)
        self._buys = []          # [(token, wallet, blockTime)]
        self._fired = {}         # token -> fire_ts
        self.signals_fired = 0
        self.buys_seen = 0
        logger.info(f"[SmartFollow] init: {len(self.watchlist)} wallets | K={k} within {window_sec//60}min "
                    f"| poll {poll_interval_sec}s (free RPC, no Helius)")

    async def _rpc(self, session, method, params, tries=4):
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        for t in range(tries):
            url = RPCS[self._rr % len(RPCS)]; self._rr += 1
            try:
                async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=20)) as r:
                    if r.status == 429:
                        await asyncio.sleep(1.0 * (t + 1)); continue
                    j = await r.json()
                    if "result" in j:
                        return j["result"]
                    await asyncio.sleep(0.4 * (t + 1))
            except Exception:
                await asyncio.sleep(0.5)
        return None

    async def _wallet_buys(self, session, wallet):
        seen = self._seen.get(wallet, set())
        sigs = await self._rpc(session, "getSignaturesForAddress", [wallet, {"limit": 8}]) or []
        out, new = [], []
        for s in sigs:
            sig = s.get("signature"); bt = s.get("blockTime")
            if not sig or s.get("err") or not bt or sig in seen:
                continue
            new.append(sig)
            tx = await self._rpc(session, "getTransaction",
                                 [sig, {"maxSupportedTransactionVersion": 0, "encoding": "jsonParsed"}])
            await asyncio.sleep(0.2)
            if not tx or not tx.get("meta"):
                continue
            meta = tx["meta"]
            pre = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                   for b in (meta.get("preTokenBalances") or []) if b.get("owner") == wallet}
            post = {b.get("mint"): float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0)
                    for b in (meta.get("postTokenBalances") or []) if b.get("owner") == wallet}
            try:
                keys = [k if isinstance(k, str) else k.get("pubkey")
                        for k in tx["transaction"]["message"]["accountKeys"]]
                wi = keys.index(wallet)
                sol_delta = (meta["postBalances"][wi] - meta["preBalances"][wi]) / 1e9
            except Exception:
                sol_delta = None
            for mint in set(list(pre) + list(post)):
                if mint in STABLE:
                    continue
                if post.get(mint, 0) - pre.get(mint, 0) > 0 and sol_delta is not None and sol_delta < 0:
                    out.append((mint, bt))
        if new:
            self._seen[wallet] = set(list(seen)[-32:] + new)
        return out

    async def _token_info(self, session, mint):
        try:
            async with session.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
                                   headers=UA, timeout=aiohttp.ClientTimeout(total=12)) as r:
                j = await r.json()
            pairs = [p for p in (j.get("pairs") or []) if p.get("priceUsd")]
            if not pairs:
                return None
            pairs.sort(key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0), reverse=True)
            p = pairs[0]
            return {"symbol": (p.get("baseToken") or {}).get("symbol") or mint[:6],
                    "price": float(p["priceUsd"]),
                    "liq": float((p.get("liquidity") or {}).get("usd") or 0),
                    "vol_h1": float((p.get("volume") or {}).get("h1") or 0),
                    "mcap": float(p.get("marketCap") or p.get("fdv") or 0),
                    "pc_h1": float((p.get("priceChange") or {}).get("h1") or 0)}
        except Exception:
            return None

    async def run(self):
        logger.info("[SmartFollow] starting run loop")
        while True:
            try:
                await self._cycle()
            except Exception as e:
                logger.warning(f"[SmartFollow] cycle error: {e}")
            await asyncio.sleep(self.poll_interval)

    async def _cycle(self):
        now = int(time.time())
        async with aiohttp.ClientSession() as session:
            for w in self.watchlist:
                try:
                    got = await self._wallet_buys(session, w)
                except Exception:
                    got = []
                for mint, bt in got:
                    self._buys.append((mint, w, bt)); self.buys_seen += 1
            # prune to window
            self._buys = [b for b in self._buys if now - b[2] <= self.window_sec]
            bytok = {}
            for mint, w, bt in self._buys:
                bytok.setdefault(mint, set()).add(w)
            for mint, wset in bytok.items():
                if len(wset) < self.k:
                    continue
                if mint in self._fired and now - self._fired[mint] < self.fire_cooldown:
                    continue
                self._fired[mint] = now
                info = await self._token_info(session, mint)
                if not info:
                    logger.info(f"[SmartFollow] {len(wset)} elite bought {mint[:10]} but no DexScreener price — skip")
                    continue
                self.signals_fired += 1
                reason = f"smart-follow: {len(wset)} elite wallets bought within {self.window_sec//60}min"
                logger.info(f"[SmartFollow] 🎯 {reason} | {info['symbol']} {mint[:10]}")
                # Track which wallets triggered this fire -> attribute trade outcomes to
                # wallets later (join to /api/trades by token) -> prune junk wallets empirically.
                _append_follow_signal({
                    "ts": now, "token": mint, "symbol": info.get("symbol"),
                    "wallets": sorted(wset), "n": len(wset),
                })
                try:
                    await self.scanner.process_external_signal(
                        token_address=mint, token_symbol=info["symbol"], reason=reason,
                        signal_score=self.min_signal_score, strategy_tag="smart_follow",
                        skip_chart_dip=True,  # follow the wallets into strength; the dip gate
                                              # (built for dip-buying) rejects every follow signal
                        price_usd=info["price"], liquidity_usd=info["liq"],
                        volume_h1=info["vol_h1"], mcap=info["mcap"], price_change_h1=info["pc_h1"])
                except Exception as e:
                    logger.warning(f"[SmartFollow] process_external_signal error: {e}")
            # prune fired
            self._fired = {m: ts for m, ts in self._fired.items() if now - ts < self.fire_cooldown * 4}
            logger.info(f"[SmartFollow] cycle: buys_window={len(self._buys)} active_tokens={len(bytok)} "
                        f"max_consensus={max((len(s) for s in bytok.values()), default=0)} "
                        f"fired_total={self.signals_fired}")
