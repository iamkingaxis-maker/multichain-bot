"""ATTENTION FEED (2026-06-11, AxiS: "tap into the social/attention layer for free").

The elites trade the layer BEFORE price: attention. DexScreener publishes its
paid-attention data openly and keylessly:

  /token-boosts/latest/v1  - boosts being PURCHASED right now (someone is
                             spending real money on this token's visibility)
  /token-boosts/top/v1     - cumulative boost leaderboard
  /token-profiles/latest/v1- teams actively updating their marketing profile
                             (incl. the `cto` community-takeover flag)

Evidence this layer matters: the one violent runner on the 06-10 bad-day tape
(Gaejook, +6,935%) was actively boosted+social — and Gaejook/Jotchua sit on
the boost leaderboard right now. Attention precedes price; our features were
all price-derived until today.

This module polls every POLL_MIN minutes (3 tiny JSON payloads — egress ~nil),
keeps an in-memory map with first-seen tracking (boost RECENCY + VELOCITY),
persists to DATA_DIR, and serves lookups for entry stamping + /api/attention.
SHADOW-FIRST: features are stamped for mining; no gate uses them until the
outcomes validate (pre-reg: judge boosted-vs-not on universe win10 at n>=200
stamped entries).
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import time

logger = logging.getLogger(__name__)

_DATA_DIR = os.environ.get("DATA_DIR", ".")
_STATE = os.path.join(_DATA_DIR, "attention_feed.json")
POLL_MIN = 5.0
ENDPOINTS = {
    "latest": "https://api.dexscreener.com/token-boosts/latest/v1",
    "top": "https://api.dexscreener.com/token-boosts/top/v1",
    "profiles": "https://api.dexscreener.com/token-profiles/latest/v1",
}


class AttentionFeed:
    def __init__(self):
        self.enabled = os.environ.get(
            "ATTENTION_FEED", "on").strip().lower() not in ("off", "0", "false")
        self.map: dict = {}        # mint -> record
        self.last_poll_at = None
        self.polls = 0
        self._load()

    # ── persistence (survives restarts so first_seen/recency stay honest) ──
    def _load(self):
        try:
            with open(_STATE) as f:
                self.map = json.load(f)
        except Exception:
            self.map = {}

    def _save(self):
        try:
            tmp = _STATE + ".tmp"
            # cap the persisted map (boost interest decays fast)
            cutoff = time.time() - 7 * 86400
            self.map = {k: v for k, v in self.map.items()
                        if (v.get("last_seen_ts") or 0) >= cutoff}
            with open(tmp, "w") as f:
                json.dump(self.map, f)
            os.replace(tmp, _STATE)
        except Exception as e:
            logger.warning(f"[AttentionFeed] save failed: {e}")

    # ── polling ─────────────────────────────────────────────────────────────
    async def _fetch(self, session, url):
        try:
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"},
                                   timeout=__import__("aiohttp").ClientTimeout(total=15)) as r:
                if r.status == 200:
                    return await r.json()
        except Exception:
            pass
        return None

    async def _poll_once(self):
        import aiohttp
        now = time.time()
        async with aiohttp.ClientSession() as s:
            latest = await self._fetch(s, ENDPOINTS["latest"]) or []
            top = await self._fetch(s, ENDPOINTS["top"]) or []
            profiles = await self._fetch(s, ENDPOINTS["profiles"]) or []
        for row in latest:
            if row.get("chainId") != "solana":
                continue
            m = (row.get("tokenAddress") or "").lower()
            if not m:
                continue
            rec = self.map.setdefault(m, {"first_seen_ts": now})
            prev_total = rec.get("boost_total") or 0
            rec["boost_total"] = row.get("totalAmount") or 0
            rec["boost_latest"] = row.get("amount") or 0
            rec["boost_velocity"] = max(0, (rec["boost_total"] or 0) - prev_total)
            rec["links_n"] = len(row.get("links") or [])
            rec["last_seen_ts"] = now
        for row in top:
            if row.get("chainId") != "solana":
                continue
            m = (row.get("tokenAddress") or "").lower()
            if not m:
                continue
            rec = self.map.setdefault(m, {"first_seen_ts": now})
            rec["boost_total"] = max(rec.get("boost_total") or 0,
                                     row.get("totalAmount") or 0)
            rec["on_top_board"] = True
            rec["last_seen_ts"] = now
        for row in profiles:
            if row.get("chainId") != "solana":
                continue
            m = (row.get("tokenAddress") or "").lower()
            if not m:
                continue
            rec = self.map.setdefault(m, {"first_seen_ts": now})
            rec["profile_seen_ts"] = now
            rec["cto"] = bool(row.get("cto"))
            rec["links_n"] = max(rec.get("links_n") or 0, len(row.get("links") or []))
            rec["last_seen_ts"] = now
        self.polls += 1
        self.last_poll_at = now
        self._save()
        logger.info(f"[AttentionFeed] poll #{self.polls}: tracking {len(self.map)} "
                    f"attention tokens (latest={len(latest)} top={len(top)} "
                    f"profiles={len(profiles)})")

    async def run(self):
        if not self.enabled:
            logger.info("[AttentionFeed] disabled (ATTENTION_FEED=off)")
            return
        logger.info(f"[AttentionFeed] starting: every {POLL_MIN:.0f}min, 3 free endpoints")
        while True:
            try:
                await self._poll_once()
            except Exception as e:
                logger.warning(f"[AttentionFeed] poll error: {e}")
            await asyncio.sleep(POLL_MIN * 60)

    # ── lookups ─────────────────────────────────────────────────────────────
    def features(self, mint: str) -> dict:
        """Attention features for entry stamping. Empty dict = no attention."""
        rec = self.map.get((mint or "").lower())
        if not rec:
            return {}
        now = time.time()
        return {
            "attn_boost_total": rec.get("boost_total"),
            "attn_boost_latest": rec.get("boost_latest"),
            "attn_boost_velocity": rec.get("boost_velocity"),
            "attn_first_seen_min": round((now - (rec.get("first_seen_ts") or now)) / 60, 1),
            "attn_on_top_board": bool(rec.get("on_top_board")),
            "attn_profile_fresh": bool(rec.get("profile_seen_ts")
                                       and now - rec["profile_seen_ts"] < 6 * 3600),
            "attn_cto": bool(rec.get("cto")),
            "attn_links_n": rec.get("links_n"),
        }

    def summary(self) -> dict:
        now = time.time()
        hot = sorted(self.map.items(),
                     key=lambda kv: -(kv[1].get("boost_velocity") or 0))[:15]
        return {
            "enabled": self.enabled,
            "polls": self.polls,
            "tracking": len(self.map),
            "last_poll_at": self.last_poll_at,
            "hottest_by_velocity": [
                {"mint": m, "boost_total": r.get("boost_total"),
                 "velocity": r.get("boost_velocity"),
                 "age_min": round((now - (r.get("first_seen_ts") or now)) / 60)}
                for m, r in hot],
        }


_FEED = AttentionFeed()


def get_feed() -> AttentionFeed:
    return _FEED
