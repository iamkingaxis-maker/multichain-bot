# tests/test_sell_serialization.py
"""Per-position sell serialization (adversarial review r2, 2026-07-12).

The post-TP1 fast-watch (7a808ce, POST_TP1_FASTWATCH default ON) fires exits
from the ~2s fast tick; the slow sweep can tick the SAME open position during
the live swap's multi-second await and re-emit un-flagged kinds (trail/stop/
moonbag) — a duplicate concurrent live sell (DONALT 2026-07-06 class). The
shared router `_execute_bot_sell` now serializes per (bot_id, token):

  1. a concurrent duplicate for the SAME position is skipped (dedupe);
  2. a different position (other bot or other token) is never blocked;
  3. the key is ALWAYS released — even when the inner sell raises — so a
     wedged key can never silently block a position's exits (the 2026-07-10
     never-buys-while-sells-broken incident class, exit-side);
  4. sequential sells for the same position still run (release-after-await).
"""
import asyncio

import pytest

from feeds.dip_scanner import DipScanner


def _bare_scanner(inner):
    sc = DipScanner.__new__(DipScanner)          # no heavy __init__
    sc._execute_bot_sell_inner = inner
    return sc


def test_concurrent_duplicate_is_skipped_but_lone_sells_run():
    calls = []
    release = asyncio.Event()

    async def slow_inner(bot_id, token, d, px, now, exit_cadence="main"):
        calls.append((bot_id, token, exit_cadence))
        await release.wait()                     # hold the key mid-await

    sc = _bare_scanner(slow_inner)

    async def go_simple():
        t1 = asyncio.ensure_future(
            sc._execute_bot_sell("b1", "TOK", None, 1.0, 0.0,
                                 exit_cadence="fastwatch"))
        await asyncio.sleep(0)
        await sc._execute_bot_sell("b1", "TOK", None, 1.0, 0.0)   # dup: skip
        release.set()                                             # free all
        await sc._execute_bot_sell("b2", "TOK", None, 1.0, 0.0)
        await sc._execute_bot_sell("b1", "OTHER", None, 1.0, 0.0)
        await t1
        await sc._execute_bot_sell("b1", "TOK", None, 1.0, 0.0)   # sequential

    asyncio.run(go_simple())
    assert calls == [
        ("b1", "TOK", "fastwatch"),   # first fire ran
        ("b2", "TOK", "main"),        # other bot, same token: not blocked
        ("b1", "OTHER", "main"),      # same bot, other token: not blocked
        ("b1", "TOK", "main"),        # sequential retry after release: runs
    ]
    assert sc._bot_sell_inflight == set()        # nothing left held


def test_key_released_even_when_inner_raises():
    async def boom(bot_id, token, d, px, now, exit_cadence="main"):
        raise RuntimeError("live swap exploded")

    sc = _bare_scanner(boom)

    async def go():
        with pytest.raises(RuntimeError):
            await sc._execute_bot_sell("b1", "TOK", None, 1.0, 0.0)
        assert sc._bot_sell_inflight == set()    # released despite the raise
        # and the position's exits are NOT blocked afterwards
        with pytest.raises(RuntimeError):
            await sc._execute_bot_sell("b1", "TOK", None, 1.0, 0.0)

    asyncio.run(go())
