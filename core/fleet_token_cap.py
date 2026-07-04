# -*- coding: utf-8 -*-
"""Fleet per-token CONCURRENCY cap (go-live audit #4, 2026-07-04).

The residual mirror pile-on: multiple badday_ bots enter the SAME token
near-simultaneously (07-03 BongoCat first-entry wave = 7 bots at once = 7x one
bot's damage; historical death-clusters were 30-71 bots on one token; June's
-$5k live day was ONE token x 198 entries). Consolidation cut the roster 14->9
but same-token first-entries still multiply damage — this caps how many
DISTINCT fleet bots may hold one token concurrently.

Scope: badday_-prefixed bots AND young-probe bots (config.young_token_probe) —
a 7-bot young pile-on is still a pile-on. Keying mirrors the streak-latch
drop-list: a held position is keyed by ADDRESS when available (symbols collide
— the SPCX phantom), symbol as fallback; the incoming buy queries with BOTH its
address and symbol so either representation matches.

SHADOW-FIRST: FLEET_TOKEN_CAP_MODE=off|shadow|enforce, default shadow —
measure winner-cost on the 9-bot roster before enforcing (the June analysis
validated cap 2-3 saving $1.4-2.9k vs ~$0.5k winner cost, but on the old
70-bot fleet). FLEET_TOKEN_CAP (default 3) = max OTHER holders; the
requesting bot itself is never counted. FAIL-OPEN everywhere: any counting
error -> no block (missing-data-read-as-zero bug-class rule).
"""
import os

DEFAULT_CAP = 3
_MODES = ("off", "shadow", "enforce")


def cap_mode() -> str:
    """Resolve FLEET_TOKEN_CAP_MODE. Unknown/garbage -> 'shadow' (the safe,
    log-only default — never let a typo silently enforce OR silently vanish)."""
    try:
        m = str(os.environ.get("FLEET_TOKEN_CAP_MODE", "shadow")).strip().lower()
    except Exception:
        return "shadow"
    return m if m in _MODES else "shadow"


def cap_n() -> int:
    """Resolve FLEET_TOKEN_CAP (max OTHER concurrent holders). Garbage or
    non-positive -> DEFAULT_CAP (a cap of 0/-1 would block every buy — that is
    what mode=enforce+tiny-cap is for, never a parse accident)."""
    try:
        n = int(str(os.environ.get("FLEET_TOKEN_CAP", str(DEFAULT_CAP))).strip())
    except (TypeError, ValueError, AttributeError):
        return DEFAULT_CAP
    return n if n > 0 else DEFAULT_CAP


def other_holders(holdings, requesting_bot, token_keys):
    """Sorted list of OTHER bot_ids currently holding the token.

    holdings: mapping bot_id -> iterable of held token keys (lowercased
    address-or-symbol, streak-latch keying). token_keys: the buy's query keys
    (its address AND symbol, so either representation matches). The requesting
    bot is ALWAYS excluded (its own re-entry is the reentry-cooldown's job,
    not the fleet cap's). FAIL-OPEN: garbage input -> [] / skip that bot;
    never raises."""
    out = []
    try:
        keys = {str(k).strip().lower() for k in (token_keys or ()) if k}
    except Exception:
        return out
    if not keys:
        return out
    try:
        items = list(holdings.items())
    except Exception:
        return out
    for bid, held in items:
        if bid == requesting_bot:
            continue
        try:
            if any(str(h).strip().lower() in keys for h in (held or ()) if h):
                out.append(bid)
        except Exception:
            continue  # one bot's garbage book must not kill the count
    return sorted(out)


def blocks(n_other_holders, cap) -> bool:
    """True when the fleet cap is full: ``cap`` OTHER bots already hold it."""
    try:
        return int(n_other_holders) >= int(cap)
    except (TypeError, ValueError):
        return False  # fail-open
