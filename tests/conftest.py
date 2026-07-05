# -*- coding: utf-8 -*-
"""Shared test fixtures — cross-test contamination guards (2026-07-05 sweep).

ENV-LEAK GUARD: dozens of test files write os.environ directly (DATA_DIR,
*_MODE gates, slippage/fee knobs, EGRESS_*, SMART_FOLLOW_*, ...) and several
never restore — poisoning every test collected after them in the same process.
That masked real failures as "flaky" (e.g. the leaked PROBE_ULTRA_SLIPPAGE_BPS
=400 broke probe_bridge's 250bps assertion; a leaked NO_FAST_PRICE_GATE_MODE
caused the chronic test_no_fast_price_gate suite failures). Snapshot the whole
environment before each test and restore it after, so no test can leak env
state into another. Function-scoped on purpose: no module/session-scoped
fixture in this suite writes env (verified 2026-07-05), and per-test isolation
is exactly the contract we want.

This does NOT undo module reloads or other in-process state — only env vars.
"""
import os

import pytest


@pytest.fixture(autouse=True)
def _global_env_leak_guard():
    snap = dict(os.environ)
    yield
    # Restore additions/changes and re-add deletions.
    for k in list(os.environ.keys()):
        if k not in snap:
            del os.environ[k]
    for k, v in snap.items():
        if os.environ.get(k) != v:
            os.environ[k] = v
