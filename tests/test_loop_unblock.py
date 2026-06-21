"""Loop-unblock instrumentation guards (Task 1).

These are NOT behavioral tests — the new instrumentation is inline phase-timing
that only does anything when SCAN_PHASE_TIMING is on and is runtime-validated by
soak. We guard (1) the module still imports and (2) the new labeled subop keys
are present in the source so the soak breakdown has the expected buckets.
"""


def test_dip_scanner_imports():
    import feeds.dip_scanner  # noqa: F401


def test_subop_keys_documented():
    # Guard: the new instrumentation keys are referenced in the module source
    # (localization buckets for the loop-unblock soak). Source-text check —
    # there is no behavioral unit for inline instrumentation (runtime-validated).
    import inspect
    import feeds.dip_scanner as ds
    src = inspect.getsource(ds)
    for k in ("feat_tier2", "feat_tier3", "feat_fusion",
              "feat_triggers_a", "feat_triggers_b", "feat_triggers_c"):
        assert k in src, k
