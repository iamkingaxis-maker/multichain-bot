"""Trending token tracker — module-level shared store.

Any caller that fetches "trending" tokens from Axiom/DexScreener can stamp
this tracker via `mark_trending(token_address)`. The dip_scanner then checks
`is_trending(addr)` to set `is_trending_token` flag in entry_meta.

Entries expire after TTL_SECS to handle the rotating-trending-list nature
of Solana memecoins (a token can be trending for an hour then drop off).

Motivation: April high-WR era's defining pattern was "buy repeat dips on
the 3-5 tokens of the moment" (MAGA, WIFE, BULL, EITHER). Today's bot
fires on any matching microcap event regardless of whether the token is
actually a "trending" pick. This tracker re-enables the trending-bias edge
without disrupting the existing trigger architecture — it's purely
informational (entry_meta flag) until something else (sizing, gate, etc.)
acts on it.
"""
import time
import threading
from typing import Optional, Set


# Time a token entry stays "trending" after being marked.
# 30 minutes — matches typical hot-token cycle on Solana memecoins.
_TTL_SECS = 1800

# Address (lowercase) -> last-seen unix-ts
_seen: dict = {}
_lock = threading.Lock()


def mark_trending(token_address: str, source: str = "axiom") -> None:
    """Mark a token as currently-trending. Idempotent — overwrites timestamp."""
    if not token_address:
        return
    addr = token_address.lower()
    now = time.time()
    with _lock:
        _seen[addr] = now


def is_trending(token_address: str) -> bool:
    """True if the token was marked trending within the last TTL_SECS."""
    if not token_address:
        return False
    addr = token_address.lower()
    with _lock:
        ts = _seen.get(addr)
        if ts is None:
            return False
        return (time.time() - ts) < _TTL_SECS


def cleanup_expired() -> int:
    """Prune entries older than TTL. Returns count removed."""
    now = time.time()
    removed = 0
    with _lock:
        stale = [addr for addr, ts in _seen.items() if (now - ts) >= _TTL_SECS]
        for addr in stale:
            del _seen[addr]
            removed += 1
    return removed


def get_trending_addresses() -> Set[str]:
    """Snapshot of current trending addresses (post-expiry)."""
    cleanup_expired()
    with _lock:
        return set(_seen.keys())


def stats() -> dict:
    """Diagnostic stats — count, oldest entry age, newest entry age."""
    cleanup_expired()
    with _lock:
        if not _seen:
            return {"count": 0, "oldest_age_s": None, "newest_age_s": None}
        now = time.time()
        ages = [now - ts for ts in _seen.values()]
        return {
            "count": len(_seen),
            "oldest_age_s": max(ages),
            "newest_age_s": min(ages),
        }
