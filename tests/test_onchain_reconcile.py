# tests/test_onchain_reconcile.py
"""Unit tests for the PURE connection-chunk reconciler on OnchainWsFeed.

The reconciler decides which connection-loop tasks to START and which active
task keys to CANCEL so the live socket set tracks the (rotating) hot mint set.
It is deterministic + side-effect free => unit-testable without sockets.
"""

import asyncio

from core.onchain_ws_feed import OnchainWsFeed


def _feed():
    return OnchainWsFeed(get_sol_usd=lambda: 100.0)


def _key(chunk):
    """Same stable key the feed uses (sorted lowercased tuple of the chunk)."""
    return tuple(sorted(m.lower() for m in chunk))


def test_reconcile_empty_active_starts_all():
    f = _feed()
    desired = [["AAA", "BBB"], ["CCC"]]
    to_start, to_cancel = f._reconcile_connection_chunks(desired, set())
    assert {_key(c) for c in to_start} == {_key(["AAA", "BBB"]), _key(["CCC"])}
    assert to_cancel == []


def test_reconcile_stable_set_no_churn():
    f = _feed()
    desired = [["AAA", "BBB"], ["CCC"]]
    active = {_key(["AAA", "BBB"]), _key(["CCC"])}
    to_start, to_cancel = f._reconcile_connection_chunks(desired, active)
    assert to_start == []
    assert to_cancel == []


def test_reconcile_rotation_starts_new_cancels_gone():
    f = _feed()
    # Was tracking chunk {AAA,BBB}; now hot set rotated to {BBB-only} + {DDD}.
    desired = [["BBB"], ["DDD"]]
    active = {_key(["AAA", "BBB"])}
    to_start, to_cancel = f._reconcile_connection_chunks(desired, active)
    assert {_key(c) for c in to_start} == {_key(["BBB"]), _key(["DDD"])}
    assert to_cancel == [_key(["AAA", "BBB"])]


def test_reconcile_boot_empty_then_mints_arrive():
    f = _feed()
    # boot: nothing desired, nothing active
    to_start, to_cancel = f._reconcile_connection_chunks([], set())
    assert to_start == [] and to_cancel == []
    # mints arrive: should start them (self-heal from empty boot)
    to_start, to_cancel = f._reconcile_connection_chunks([["AAA"]], set())
    assert {_key(c) for c in to_start} == {_key(["AAA"])}
    assert to_cancel == []


def test_reconcile_all_dropped_cancels_all():
    f = _feed()
    active = {_key(["AAA"]), _key(["BBB"])}
    to_start, to_cancel = f._reconcile_connection_chunks([], active)
    assert to_start == []
    assert set(to_cancel) == active


def test_reconcile_key_is_order_insensitive():
    f = _feed()
    # desired chunk in a different member order than the active key
    desired = [["BBB", "AAA"]]
    active = {_key(["AAA", "BBB"])}
    to_start, to_cancel = f._reconcile_connection_chunks(desired, active)
    assert to_start == []
    assert to_cancel == []


# --- supervisor integration (apply_chunk_reconcile drives real asyncio tasks)


def _patch_conn_loop(feed, started, finished):
    """Replace _connection_loop with a controllable coroutine that records the
    chunk it was started for and parks until cancelled (recording cancel)."""
    async def _fake_loop(chunk):
        key = tuple(sorted(m.lower() for m in chunk))
        started.append(key)
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            finished.append(key)
            raise
    feed._connection_loop = _fake_loop


def test_supervisor_boot_empty_self_heals_when_mints_arrive():
    async def _run():
        f = _feed()
        started, finished = [], []
        _patch_conn_loop(f, started, finished)
        # boot empty -> no tasks
        f._apply_chunk_reconcile([])
        assert f._conn_tasks == {}
        # mints arrive -> task spawned (self-heal)
        f._apply_chunk_reconcile([["AAA", "BBB"]])
        await asyncio.sleep(0)  # let the task start
        assert _key(["AAA", "BBB"]) in f._conn_tasks
        assert started == [_key(["AAA", "BBB"])]
        # cleanup
        for t in f._conn_tasks.values():
            t.cancel()
        await asyncio.gather(*f._conn_tasks.values(), return_exceptions=True)
    asyncio.run(_run())


def test_supervisor_rotation_starts_new_and_cancels_gone():
    async def _run():
        f = _feed()
        started, finished = [], []
        _patch_conn_loop(f, started, finished)
        f._apply_chunk_reconcile([["AAA", "BBB"]])
        await asyncio.sleep(0)
        # rotate: AAA,BBB chunk gone; new DDD chunk
        f._apply_chunk_reconcile([["DDD"]])
        await asyncio.sleep(0)
        await asyncio.sleep(0)  # allow cancellation to propagate
        assert _key(["DDD"]) in f._conn_tasks
        assert _key(["AAA", "BBB"]) not in f._conn_tasks
        assert finished == [_key(["AAA", "BBB"])]  # old chunk was cancelled
        # cleanup
        for t in f._conn_tasks.values():
            t.cancel()
        await asyncio.gather(*f._conn_tasks.values(), return_exceptions=True)
    asyncio.run(_run())


def test_supervisor_stable_set_does_not_respawn():
    async def _run():
        f = _feed()
        started, finished = [], []
        _patch_conn_loop(f, started, finished)
        f._apply_chunk_reconcile([["AAA"]])
        await asyncio.sleep(0)
        f._apply_chunk_reconcile([["AAA"]])  # identical -> no churn
        await asyncio.sleep(0)
        assert started == [_key(["AAA"])]  # only spawned once
        assert finished == []
        for t in f._conn_tasks.values():
            t.cancel()
        await asyncio.gather(*f._conn_tasks.values(), return_exceptions=True)
    asyncio.run(_run())


def test_supervisor_respawns_finished_task_on_readd():
    async def _run():
        f = _feed()
        started = []
        # a loop that exits immediately (simulates a connection loop returning)
        async def _quick(chunk):
            started.append(tuple(sorted(m.lower() for m in chunk)))
        f._connection_loop = _quick
        f._apply_chunk_reconcile([["AAA"]])
        await asyncio.sleep(0)
        await asyncio.sleep(0)  # let it finish
        # re-add same chunk: done task pruned -> respawned
        f._apply_chunk_reconcile([["AAA"]])
        await asyncio.sleep(0)
        assert started == [_key(["AAA"]), _key(["AAA"])]
    asyncio.run(_run())
