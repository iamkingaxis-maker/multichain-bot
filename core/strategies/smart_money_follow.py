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
# Per-exit log (2026-06-08): record when an elite wallet that we followed EXITS the
# token (sells), with their hold time + their own SOL return. This is the calibration
# data for smart_follow's TP/stop — "follow them OUT" instead of a borrowed dip ladder.
_FOLLOW_EXITS_LOG = os.path.join(os.environ.get("DATA_DIR", "."), "follow_exits.jsonl")
_LOG_CAP = 5_000_000  # ~5MB per log; trims oldest half when exceeded

RPCS = ["https://api.mainnet-beta.solana.com", "https://solana.leorpc.com/?api_key=FREE"]
STABLE = {"So11111111111111111111111111111111111111112",
          "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
          "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
UA = {"User-Agent": "Mozilla/5.0"}


def _append_jsonl(path: str, rec: dict):
    """Append one record to a JSONL log (fail-soft, size-capped)."""
    try:
        with open(path, "a") as f:
            f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        if os.path.getsize(path) > _LOG_CAP:  # keep newest half when oversized
            with open(path) as f:
                lines = f.readlines()
            with open(path, "w") as f:
                f.writelines(lines[len(lines) // 2:])
    except Exception:
        pass  # tracking must never break the strategy


# Flush-depth fire gate (2026-06-10, AxiS-approved): only fire when the token is in a
# real flush. Stamped-fire outcomes (n=28 closed) show pc_h1 > -10 fires are the worst
# cohort (22% WR, -$41 over 9 fires); deep fires run 40-50% WR. The wallets we follow
# buy multi-hour theses — entering on a flat/pumping tape buys the fade.
# Modes: enforce (default) = block shallow fires; shadow = log verdict, still fire; off.
# Every fire (blocked included) keeps its _FOLLOW_LOG record for threshold tuning.
def _elite_exit_on() -> bool:
    return os.environ.get("SMART_FOLLOW_ELITE_EXIT", "on").strip().lower() != "off"


def _max_chase_pct() -> float:
    """Max % the fill may run past the fire-detection price (2026-06-10: measured
    chase mean +1.56% > the elites' ~$0.33/trade edge — don't be their exit)."""
    try:
        return float(os.environ.get("SMART_FOLLOW_MAX_CHASE_PCT", "1.5"))
    except Exception:
        return 1.5


def _flush_gate_mode() -> str:
    m = os.environ.get("SMART_FOLLOW_FLUSH_GATE", "enforce").strip().lower()
    return m if m in ("enforce", "shadow", "off") else "enforce"


def _flush_gate_max_pch1() -> float:
    try:
        return float(os.environ.get("SMART_FOLLOW_FLUSH_MAX_PCH1", "-10.0"))
    except Exception:
        return -10.0


def _load_json_cfg(path: str, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


class SmartMoneyFollowStrategy:
    def __init__(self, scanner, telegram=None, watchlist=None, quality=None,
                 k=3, window_sec=600, poll_interval_sec=120, fire_cooldown_sec=3600,
                 min_signal_score=70, position_manager=None):
        self.scanner = scanner
        self.telegram = telegram
        self.watchlist = list(watchlist or [])
        self.quality = quality or {}
        self.k = k
        self.window_sec = window_sec
        self.poll_interval = poll_interval_sec
        self.fire_cooldown = fire_cooldown_sec
        self.min_signal_score = min_signal_score
        self.position_manager = position_manager  # for elite-exit mirroring
        self._rr = 0
        self._seen = {}          # wallet -> set(recent sigs)
        self._buys = []          # [(token, wallet, blockTime)]
        self._fired = {}         # token -> fire_ts
        self._wallet_pos = {}    # (wallet, mint) -> (buy_bt, buy_sol) — open elite round-trips (for exit calibration)
        # ── 2026-06-10 "serious love" build (AxiS) ──────────────────────────
        # Elite-exit mirroring: remember WHICH wallets triggered each fire so
        # their SELLS can exit our position ("follow them out"). Evidence: the
        # dip exits fight multi-hour theses (74% of stops recovered >15%).
        self._fired_wallets = {}   # token -> set(trigger wallets)
        self._elite_sold = {}      # token -> set(trigger wallets that have SOLD)
        # K-tier pods (config/follow_tiers.json): K=2 high-tier + K=1 solo,
        # each rate-capped — the 06-09 K=1 flood starved the event loop, so
        # solo/k2 fire at most N times per rolling hour.
        tiers = _load_json_cfg("config/follow_tiers.json", {})
        self.high_tier = set(tiers.get("high_tier") or [])
        self.solo = set(tiers.get("solo") or [])
        # CONVEX pod (2026-06-10, 4th tier): copy the tail-hunters in THEIR
        # payoff shape — $25 probes, K=1 breadth, tiny TP1 partial (10%), 90%
        # rides the peak-scaled trail, fast -15 cut (their own median loser),
        # NO stop-grace, NO flush gate (they enter spikes, not dips), tighter
        # chase limit. Decode: 500 elite round-trips = 51% WR but winners p90
        # +107% — the tail IS the strategy; capping it was our copy's bug.
        self.convex = set(tiers.get("convex") or [])
        self._tier_fires = {"k2": [], "solo": [], "convex": []}   # rolling fire ts
        self.tier_caps_per_hour = {"k2": 8, "solo": 6, "convex": 8}
        # Fire-quality sizing (config/follow_quality.json): SHADOW ONLY —
        # stamps would_size_mult into the fire log; enforce at n>=40/wallet.
        self.fire_quality = {kk: v for kk, v in
                             _load_json_cfg("config/follow_quality.json", {}).items()
                             if not kk.startswith("_")}
        # Realtime hot-queue: WS notifications enqueue wallets for an immediate
        # targeted sweep instead of waiting out the poll interval.
        self._hot = set()
        self._hot_event = asyncio.Event()
        self._wallet_sizes = {}     # wallet -> recent buy sizes (conviction baseline)
        self._last_fire_sol = {}    # (mint, wallet) -> triggering buy SOL
        self.signals_fired = 0
        self.buys_seen = 0
        logger.info(f"[SmartFollow] init: {len(self.watchlist)} wallets | K={k} within {window_sec//60}min "
                    f"| poll {poll_interval_sec}s | k2_pod={len(self.high_tier)} solo={len(self.solo)} "
                    f"| elite_exit={'on' if position_manager else 'OFF (no PM ref)'} (free RPC, no Helius)")

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
                delta = post.get(mint, 0) - pre.get(mint, 0)
                if sol_delta is None:
                    continue
                if delta > 0 and sol_delta < 0:          # BUY: token up, SOL spent
                    out.append((mint, bt, "buy", -sol_delta))
                elif delta < 0 and sol_delta > 0:        # SELL: token down, SOL received
                    out.append((mint, bt, "sell", sol_delta))
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

    def _tier_cap_ok(self, tier: str, now: int) -> bool:
        lst = self._tier_fires[tier]
        lst[:] = [t for t in lst if now - t < 3600]
        return len(lst) < self.tier_caps_per_hour.get(tier, 0)

    async def _fire(self, session, mint, wset, now, tier):
        """Shared fire path for every tier: quote, flush gate, fire-quality
        size shadow, log, route through process_external_signal."""
        info = await self._token_info(session, mint)
        if not info:
            logger.info(f"[SmartFollow] {len(wset)} wallet(s) bought {mint[:10]} but no DexScreener price — skip")
            return
        self.signals_fired += 1
        tag = {"k3": "smart_follow", "k2": "smart_follow_k2",
               "solo": "smart_follow_solo", "convex": "smart_follow_convex"}[tier]
        reason = f"smart-follow[{tier}]: {len(wset)} wallet(s) bought within {self.window_sec//60}min"
        # flush-depth verdict: pc_h1 must show a real flush to fire.
        # CONVEX tier bypasses the gate (the tail-hunters enter SPIKES, not dips
        # — dip-conditioning their copies re-introduces the payoff mismatch);
        # verdict still logged for the forward audit.
        gate_mode = _flush_gate_mode() if tier != "convex" else "off"
        max_pch1 = _flush_gate_max_pch1()
        pc_h1 = info.get("pc_h1")
        shallow = pc_h1 is None or pc_h1 > max_pch1
        gate_verdict = "blocked" if (shallow and gate_mode == "enforce") else (
            "shadow_block" if (shallow and gate_mode in ("shadow", "off")) else "pass")
        # fire-quality size shadow (config/follow_quality.json): stamp only —
        # enforcement waits for n>=40/wallet on the new-list fire board.
        quals = [self.fire_quality[w] for w in wset if w in self.fire_quality]
        fq_mean = round(sum(quals) / len(quals), 2) if quals else None
        would_mult = (1.0 if fq_mean is None else
                      1.25 if fq_mean > 0.5 else
                      0.5 if fq_mean < -1.5 else
                      0.75 if fq_mean < 0 else 1.0)
        # conviction (2026-06-11): triggering buy size vs the wallet's own
        # rolling median — a 5x-median bet means more than a dust probe.
        conv = None
        for w in wset:
            sol = self._last_fire_sol.get(w)
            hist = self._wallet_sizes.get(w) or []
            if isinstance(sol, (int, float)) and len(hist) >= 5:
                med = sorted(hist)[len(hist) // 2]
                if med > 0:
                    c_ = sol / med
                    conv = c_ if conv is None else max(conv, c_)
        conv = round(conv, 2) if conv is not None else None
        logger.info(f"[SmartFollow] 🎯 {reason} | {info['symbol']} {mint[:10]} "
                    f"| flush_gate={gate_verdict} pc_h1={pc_h1} fq={fq_mean} "
                    f"would_mult={would_mult} conviction={conv}")
        # Track which wallets triggered this fire -> attribute trade outcomes to
        # wallets later (join to /api/trades by token) -> prune junk wallets empirically.
        _append_jsonl(_FOLLOW_LOG, {
            "ts": now, "token": mint, "symbol": info.get("symbol"),
            "wallets": sorted(wset), "n": len(wset),
            "tier": tier, "flush_gate": gate_verdict,
            "fq_mean": fq_mean, "would_size_mult": would_mult,
            "conviction_mult": conv,
            # token state at fire time (2026-06-09): already in hand from the
            # DexScreener quote — costs no extra fetch.
            "state": {
                "price": info.get("price"), "liq": info.get("liq"),
                "mcap": info.get("mcap"), "vol_h1": info.get("vol_h1"),
                "pc_h1": info.get("pc_h1"),
            },
        })
        if gate_verdict == "blocked":
            return
        # remember the triggers so their SELLS can exit us ("follow them out")
        self._fired_wallets[mint] = set(wset)
        self._elite_sold.pop(mint, None)
        try:
            _converted = await self.scanner.process_external_signal(
                token_address=mint, token_symbol=info["symbol"], reason=reason,
                signal_score=self.min_signal_score, strategy_tag=tag,
                skip_chart_dip=True,  # follow the wallets into strength; the dip gate
                                      # (built for dip-buying) rejects every follow signal
                price_usd=info["price"], liquidity_usd=info["liq"],
                volume_h1=info["vol_h1"], mcap=info["mcap"], price_change_h1=info["pc_h1"],
                # max-chase guard: skip the fill if price ran past the fire price
                # during security checks (we'd be buying the push, not the entry).
                # convex enters spikes -> tighter limit (late on a spike = the killer).
                max_price_usd=info["price"] * (1 + (min(_max_chase_pct(), 1.0)
                                                    if tier == "convex"
                                                    else _max_chase_pct()) / 100.0),
                # convex = many small tickets: $25 probes vs the $100 default
                override_usd=(25.0 if tier == "convex" else 0.0),
                # permanent latency/chase/conviction audit trail on the position
                signal_meta={"follow_fire_ts": now,
                             "follow_fire_price": info.get("price"),
                             "follow_tier": tier,
                             "follow_conviction_mult": conv,
                             "follow_fq_mean": fq_mean})
            if not _converted:
                # funnel decomposition (2026-06-11): name every unconverted fire
                _why = getattr(self.scanner, "_ext_block_reason", None) or "unknown"
                _append_jsonl(_FOLLOW_LOG, {
                    "type": "fire_unconverted", "ts": int(time.time()),
                    "token": mint, "tier": tier, "reason": _why})
                logger.info(f"[SmartFollow] fire UNCONVERTED {info['symbol']} "
                            f"{mint[:10]} reason={_why}")
        except Exception as e:
            logger.warning(f"[SmartFollow] process_external_signal error: {e}")

    async def _ws_watch(self):
        """Realtime wallet watching (2026-06-10): logsSubscribe(mentions=wallet)
        on the public Solana WS. A notification wakes the poll loop IMMEDIATELY
        (instead of waiting out the 30s interval) — catch the pop, not the fade.
        Fail-soft: any error -> reconnect with backoff; polling continues either way."""
        url = "wss://api.mainnet-beta.solana.com"
        backoff = 5
        while True:
            try:
                async with aiohttp.ClientSession() as sess:
                    async with sess.ws_connect(url, heartbeat=20) as ws:
                        sub_to_wallet = {}
                        for i, w in enumerate(self.watchlist):
                            await ws.send_json({
                                "jsonrpc": "2.0", "id": i + 1, "method": "logsSubscribe",
                                "params": [{"mentions": [w]}, {"commitment": "confirmed"}]})
                        logger.info(f"[SmartFollow] WS watch: subscribing {len(self.watchlist)} wallets")
                        backoff = 5
                        async for msg in ws:
                            if msg.type != aiohttp.WSMsgType.TEXT:
                                break
                            try:
                                j = json.loads(msg.data)
                            except Exception:
                                continue
                            if (isinstance(j.get("id"), int) and "result" in j
                                    and 1 <= j["id"] <= len(self.watchlist)):
                                sub_to_wallet[j["result"]] = self.watchlist[j["id"] - 1]
                                continue
                            if j.get("method") != "logsNotification":
                                continue
                            sub = (j.get("params") or {}).get("subscription")
                            w = sub_to_wallet.get(sub)
                            if w:
                                self._hot.add(w)
                                self._hot_event.set()
            except Exception as e:
                logger.info(f"[SmartFollow] WS watch error ({type(e).__name__}: {e}) — reconnect in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120)

    async def run(self):
        logger.info("[SmartFollow] starting run loop")
        asyncio.ensure_future(self._ws_watch())
        while True:
            try:
                await self._cycle()
            except Exception as e:
                logger.warning(f"[SmartFollow] cycle error: {e}")
            # A WS wallet notification cuts the wait short so the next sweep runs
            # while the buy is seconds old. 5s floor after a hot wake batches
            # notification bursts and protects the event loop + free-RPC budget.
            woke_hot = False
            try:
                await asyncio.wait_for(self._hot_event.wait(), timeout=self.poll_interval)
                woke_hot = True
            except asyncio.TimeoutError:
                pass
            self._hot_event.clear()
            self._hot.clear()
            if woke_hot:
                await asyncio.sleep(5)

    async def _cycle(self):
        now = int(time.time())
        async with aiohttp.ClientSession() as session:
            # Parallel sweep (2026-06-08): sweep all wallets concurrently (bounded by a
            # semaphore to respect free-RPC rate limits) so the whole pass finishes in
            # SECONDS instead of ~60-120s sequential. Combined with the shrunk top-tier
            # watchlist + tighter poll, this cuts follow latency from ~2-4min toward ~20-30s
            # so we catch the pop, not the fade.
            _sem = asyncio.Semaphore(5)

            async def _sweep(_w):
                async with _sem:
                    try:
                        return _w, await self._wallet_buys(session, _w)
                    except Exception:
                        return _w, []
            results = await asyncio.gather(*[_sweep(w) for w in self.watchlist])
            for w, got in results:
                for mint, bt, side, sol in got:
                    if side == "buy":
                        self._buys.append((mint, w, bt, sol)); self.buys_seen += 1
                        # rolling per-wallet buy-size history -> conviction stamp
                        h = self._wallet_sizes.setdefault(w, [])
                        h.append(sol)
                        del h[:-40]
                        # remember the elite's entry so we can measure their EXIT later
                        self._wallet_pos[(w, mint)] = (bt, sol)
                    else:  # SELL — an elite we may be following is EXITING
                        ent = self._wallet_pos.pop((w, mint), None)
                        if ent:
                            hold = max(0, bt - ent[0])
                            ret = (sol / ent[1] - 1.0) if ent[1] else None
                            _append_jsonl(_FOLLOW_EXITS_LOG, {
                                "ts": bt, "token": mint, "wallet": w,
                                "hold_secs": hold, "sol_in": round(ent[1], 4),
                                "sol_out": round(sol, 4),
                                "wallet_return_pct": round(ret * 100, 1) if ret is not None else None,
                            })
                        # ── Elite-exit mirroring ("follow them out", 2026-06-10) ──
                        # When enough of the wallets that TRIGGERED our entry have
                        # sold, exit our remainder on THEIR timing — replaces the
                        # dip ladder's guesswork on multi-hour follow theses.
                        trig = self._fired_wallets.get(mint)
                        if trig and w in trig and _elite_exit_on():
                            sold = self._elite_sold.setdefault(mint, set())
                            sold.add(w)
                            need = 2 if len(trig) >= 2 else 1
                            if len(sold) >= need:
                                closed = False
                                if self.position_manager is not None:
                                    try:
                                        closed = await self.position_manager.external_exit(
                                            mint, f"smart-follow elite-exit "
                                                  f"({len(sold)}/{len(trig)} triggers sold)")
                                    except Exception as e:
                                        logger.warning(f"[SmartFollow] elite-exit error: {e}")
                                logger.info(f"[SmartFollow] 🚪 elite-exit {mint[:10]}: "
                                            f"{len(sold)}/{len(trig)} triggers sold "
                                            f"-> position_closed={closed}")
                                _append_jsonl(_FOLLOW_LOG, {
                                    "type": "elite_exit", "ts": int(time.time()),
                                    "token": mint, "sold": sorted(sold),
                                    "trig_n": len(trig), "position_closed": closed,
                                })
                                self._fired_wallets.pop(mint, None)
                                self._elite_sold.pop(mint, None)
            # prune to window
            self._buys = [b for b in self._buys if now - b[2] <= self.window_sec]
            # TTL the open round-trips (drop entries whose buy is >24h old and never sold)
            self._wallet_pos = {k: v for k, v in self._wallet_pos.items() if now - v[0] <= 86400}
            bytok = {}
            fire_sol = {}
            for mint, w, bt, sol in self._buys:
                bytok.setdefault(mint, set()).add(w)
                fire_sol[(mint, w)] = sol
            for mint, wset in bytok.items():
                # K-tier resolution: K=3 full consensus -> K=2 high-tier pod ->
                # K=1 solo probe. Pods are rate-capped per rolling hour (the
                # 06-09 ungated K=1 starved the event loop).
                tier = None
                if len(wset) >= self.k:
                    tier = "k3"
                elif len(wset & self.high_tier) >= 2 and self._tier_cap_ok("k2", now):
                    tier = "k2"
                elif (wset & self.solo) and self._tier_cap_ok("solo", now):
                    tier = "solo"
                elif (wset & self.convex) and self._tier_cap_ok("convex", now):
                    tier = "convex"
                if tier is None:
                    continue
                if mint in self._fired and now - self._fired[mint] < self.fire_cooldown:
                    continue
                self._fired[mint] = now
                if tier in self._tier_fires:
                    self._tier_fires[tier].append(now)
                self._last_fire_sol = {w: fire_sol.get((mint, w)) for w in wset}
                await self._fire(session, mint, wset, now, tier)
            # prune fired + the elite-exit trigger maps alongside
            self._fired = {m: ts for m, ts in self._fired.items() if now - ts < self.fire_cooldown * 4}
            self._fired_wallets = {m: s for m, s in self._fired_wallets.items() if m in self._fired}
            self._elite_sold = {m: s for m, s in self._elite_sold.items() if m in self._fired}
            logger.info(f"[SmartFollow] cycle: buys_window={len(self._buys)} active_tokens={len(bytok)} "
                        f"max_consensus={max((len(s) for s in bytok.values()), default=0)} "
                        f"fired_total={self.signals_fired}")
