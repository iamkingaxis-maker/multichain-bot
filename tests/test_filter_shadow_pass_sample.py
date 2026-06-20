"""Tests for the 2026-06-19 filter-shadow VOLUME + SCORER-SAMPLING fix.

Two defects fixed:
  * FIX 1 — PASS sampling at the capture choke point: record ALL BLOCK verdicts;
    sample PASS ~1-in-N (FILTER_SHADOW_PASS_SAMPLE, default 50) DETERMINISTICALLY
    by md5(address+filter)%N (no random, no clock). N=0 => no PASS at all.
  * FIX 2 — scorer loads by AGE WINDOW [min_forward_min, max_age_min], NOT the
    most-recent-N lines, so mature records actually surface; empty-mature is
    logged, never silently returned.
"""
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ── FIX 1: deterministic PASS sampling ───────────────────────────────────────

def test_block_always_recorded():
    from feeds import filter_shadow_recorder as fsr
    # BLOCK and any non-PASS verdict must ALWAYS be recorded, at any N.
    for n in (1, 50, 1000):
        assert fsr.should_record_verdict("ANY", "filter_x", "BLOCK", sample_n=n)
        assert fsr.should_record_verdict("ANY", "filter_x", "SHADOW_BLOCK",
                                         sample_n=n)
        # N=0 means "no PASS at all" but BLOCK is still kept.
    assert fsr.should_record_verdict("ANY", "filter_x", "BLOCK", sample_n=0)


def test_pass_sample_zero_records_no_pass():
    from feeds import filter_shadow_recorder as fsr
    # N=0 => record NO pass at all (but BLOCK still kept — covered above).
    for addr in ("a", "b", "c", "deadbeef"):
        assert fsr.should_record_verdict(addr, "filter_x", "PASS",
                                         sample_n=0) is False


def test_pass_sample_deterministic_by_address_hash():
    from feeds import filter_shadow_recorder as fsr
    # Same address+filter => same decision every call (deterministic, no clock).
    addr, fname, n = "So11111111111111111111111111111111111111112", "filter_a", 50
    first = fsr.should_record_verdict(addr, fname, "PASS", sample_n=n)
    for _ in range(20):
        assert fsr.should_record_verdict(addr, fname, "PASS", sample_n=n) == first


def test_pass_sample_known_address_matches_md5_modulo():
    import hashlib
    from feeds import filter_shadow_recorder as fsr
    # The contract: keep PASS iff md5(addr+fname) % N == 0. Find an address that
    # IS sampled and one that is NOT, and assert should_record agrees exactly.
    n = 50
    fname = "filter_a"
    sampled = not_sampled = None
    for i in range(5000):
        addr = f"addr{i}"
        h = int(hashlib.md5((addr + fname).encode()).hexdigest(), 16) % n
        decision = fsr.should_record_verdict(addr, fname, "PASS", sample_n=n)
        assert decision == (h == 0)
        if h == 0 and sampled is None:
            sampled = addr
        if h != 0 and not_sampled is None:
            not_sampled = addr
    # both cases were observed (the helper isn't trivially all-True/all-False)
    assert sampled is not None and not_sampled is not None


def test_pass_sample_rate_is_roughly_one_in_n():
    from feeds import filter_shadow_recorder as fsr
    n = 50
    kept = sum(
        1 for i in range(50000)
        if fsr.should_record_verdict(f"tok{i}", "filter_a", "PASS", sample_n=n)
    )
    # ~1/50 = 2%; allow generous slack for hash distribution.
    assert 600 <= kept <= 1400  # ~1000 expected of 50000


def test_pass_sample_n_one_keeps_every_pass():
    from feeds import filter_shadow_recorder as fsr
    for i in range(100):
        assert fsr.should_record_verdict(f"t{i}", "f", "PASS", sample_n=1)


def test_pass_sample_env_default_is_50(monkeypatch):
    from feeds import filter_shadow_recorder as fsr
    monkeypatch.delenv("FILTER_SHADOW_PASS_SAMPLE", raising=False)
    assert fsr._pass_sample_n() == 50
    monkeypatch.setenv("FILTER_SHADOW_PASS_SAMPLE", "0")
    assert fsr._pass_sample_n() == 0
    monkeypatch.setenv("FILTER_SHADOW_PASS_SAMPLE", "10")
    assert fsr._pass_sample_n() == 10
    monkeypatch.setenv("FILTER_SHADOW_PASS_SAMPLE", "garbage")
    assert fsr._pass_sample_n() == 50  # fail-open to default


def test_pass_sample_failopen_keeps_record(monkeypatch):
    from feeds import filter_shadow_recorder as fsr
    # If hashing blows up, fail-open => record it (never silently drop a signal).
    import hashlib as _h

    def _boom(*a, **k):
        raise RuntimeError("hash gone")

    monkeypatch.setattr(_h, "md5", _boom)
    assert fsr.should_record_verdict("A", "f", "PASS", sample_n=50) is True


# ── FIX 2: scorer selects MATURE age window, not newest-N ─────────────────────

def _rec(ts_iso):
    return {"ts": ts_iso, "filter_name": "f", "verdict": "BLOCK",
            "pair_address": "P", "token_address": "A"}


def test_select_mature_records_window():
    from core import shadow_pnl_scorer as sps
    from datetime import datetime, timezone

    now = 1_000_000.0
    def iso(age_min):
        return datetime.fromtimestamp(now - age_min * 60, timezone.utc).isoformat()

    recs = [
        _rec(iso(5)),     # too young (5 < 30) -> excluded
        _rec(iso(45)),    # mature (30 <= 45 <= 1440) -> kept
        _rec(iso(120)),   # mature -> kept
        _rec(iso(2000)),  # too old (> 1440) -> excluded
    ]
    mature = sps.select_mature_records(recs, now, min_forward_min=30,
                                       max_age_min=1440)
    assert len(mature) == 2
    # exactly the two in-window records
    ages_kept = {r["ts"] for r in mature}
    assert iso(45) in ages_kept and iso(120) in ages_kept
    assert iso(5) not in ages_kept and iso(2000) not in ages_kept


def test_select_mature_boundaries_inclusive():
    from core import shadow_pnl_scorer as sps
    from datetime import datetime, timezone
    now = 1_000_000.0
    def iso(age_min):
        return datetime.fromtimestamp(now - age_min * 60, timezone.utc).isoformat()
    recs = [_rec(iso(30)), _rec(iso(1440))]  # both exactly on the bounds
    mature = sps.select_mature_records(recs, now, 30, 1440)
    assert len(mature) == 2  # inclusive on both ends


def test_select_mature_drops_unparseable_ts():
    from core import shadow_pnl_scorer as sps
    recs = [{"ts": "not-a-date"}, {"nots": 1}]
    assert sps.select_mature_records(recs, 1_000_000.0, 30, 1440) == []


def test_scorer_empty_mature_is_logged_not_silent(monkeypatch, tmp_path, caplog):
    """The empty-mature path must LOG (loaded/mature=0), not silently return."""
    import asyncio
    import logging
    from datetime import datetime, timezone
    from core import shadow_pnl_scorer as sps

    dd = tmp_path
    monkeypatch.setenv("DATA_DIR", str(dd))
    # Write a log whose only records are TOO YOUNG (age 1 min < 30).
    young = datetime.now(timezone.utc).isoformat()
    (dd / "filter_shadow_log.jsonl").write_text(
        '{"ts": "%s", "filter_name": "f", "verdict": "BLOCK", '
        '"pair_address": "P", "token_address": "A"}\n' % young)

    fetched = []

    class _NoFetchClient:
        async def fetch_1m(self, pair, limit=60):
            fetched.append(pair)
            return []

    monkeypatch.setattr(
        "feeds.dexscreener_client.DexScreenerClient", lambda *a, **k: _NoFetchClient())

    caplog.set_level(logging.INFO, logger="core.shadow_pnl_scorer")
    asyncio.run(sps._run_forward_candle_scorer())

    # NO network fetch (nothing matured) AND an explicit mature=0 log line.
    assert fetched == []
    assert any("mature=0" in r.message or "mature=0" in r.getMessage()
               for r in caplog.records)


def test_scorer_scores_mature_and_logs_counts(monkeypatch, tmp_path, caplog):
    import asyncio
    import logging
    from datetime import datetime, timezone, timedelta
    from core import shadow_pnl_scorer as sps

    dd = tmp_path
    monkeypatch.setenv("DATA_DIR", str(dd))

    # One MATURE BLOCK record (age 45 min) + one too-young record.
    old = (datetime.now(timezone.utc) - timedelta(minutes=45)).isoformat()
    young = datetime.now(timezone.utc).isoformat()
    block_ts = int(datetime.fromisoformat(old).timestamp())
    lines = [
        '{"ts": "%s", "filter_name": "filter_a", "verdict": "BLOCK", '
        '"pair_address": "P1", "token_address": "A1"}' % old,
        '{"ts": "%s", "filter_name": "filter_a", "verdict": "PASS", '
        '"pair_address": "P2", "token_address": "A2"}' % young,
    ]
    (dd / "filter_shadow_log.jsonl").write_text("\n".join(lines) + "\n")

    class _Bar:
        def __init__(self, t, c, hi=None, lo=None):
            self.open_time, self.close = t, c
            self.high = hi if hi is not None else c
            self.low = lo if lo is not None else c

    class _Client:
        def __init__(self):
            self.calls = []

        async def fetch_1m(self, pair, limit=60):
            self.calls.append(pair)
            # forward bars after block_ts -> +10% winner
            return [_Bar(block_ts, 100.0), _Bar(block_ts + 60, 110.0, hi=110.0)]

    client = _Client()
    monkeypatch.setattr(
        "feeds.dexscreener_client.DexScreenerClient", lambda *a, **k: client)
    monkeypatch.setenv("SHADOW_PNL_PACE_SECS", "0")

    caplog.set_level(logging.INFO, logger="core.shadow_pnl_scorer")
    asyncio.run(sps._run_forward_candle_scorer())

    # Only the MATURE pair P1 was fetched (the too-young PASS on P2 was excluded).
    assert client.calls == ["P1"]
    out = dd / "filter_shadow_pnl.json"
    assert out.exists()
    # Log reflects loaded/mature/scored counts.
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "loaded=2" in msgs and "mature=1" in msgs
