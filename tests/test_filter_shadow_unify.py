"""Pure-logic tests for the FILTER-FAMILY SHADOW P&L unification.

Covers (all pure / no live network):
  * record_verdict normalization (SHADOW_BLOCK->BLOCK, block->BLOCK, pass->PASS)
  * compute_filter_pnl: aggregation + SHADOW_BLOCK bucketed as BLOCK +
    DEDUP-BY-PAIR (one forward-candle fetch per pair_address, reused across
    every filter record sharing that pair) + pass_block_diff.
  * compute_gate_pnl emit-json round-trip (importable wrapper over the joiner).

ADDRESS-keyed throughout. P&L via pnl_pct / strategy-cap realized %; never the
corrupted feed `pnl` dollar field.
"""
import asyncio
import json
import os
import sys

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ── record_verdict normalization ─────────────────────────────────────────────

def test_record_verdict_normalizes_shadow_block(monkeypatch):
    from feeds import filter_shadow_recorder as fsr

    captured = {}

    class _FakeRec:
        def record(self, token_address, token_symbol, pair, filter_name,
                   verdict, block_reasons=""):
            captured["verdict"] = verdict
            captured["addr"] = token_address
            captured["filter"] = filter_name
            return True

    monkeypatch.setattr(fsr, "_singleton", _FakeRec())
    fsr.record_verdict("ADDR", "SYM", {"pairAddress": "P"},
                       "filter_chasing_top", "SHADOW_BLOCK", "r1")
    assert captured["verdict"] == "BLOCK"
    assert captured["addr"] == "ADDR"
    assert captured["filter"] == "filter_chasing_top"


def test_record_verdict_normalizes_case_and_pass(monkeypatch):
    from feeds import filter_shadow_recorder as fsr

    seen = []

    class _FakeRec:
        def record(self, *a, **k):
            seen.append(k.get("verdict") or a[4])
            return True

    monkeypatch.setattr(fsr, "_singleton", _FakeRec())
    fsr.record_verdict("A", "S", {}, "f", "block")
    fsr.record_verdict("A", "S", {}, "f", "PASS")
    fsr.record_verdict("A", "S", {}, "f", "pass")
    assert seen == ["BLOCK", "PASS", "PASS"]


def test_record_verdict_off_mode_is_dormant(monkeypatch):
    from feeds import filter_shadow_recorder as fsr

    called = []

    class _FakeRec:
        def record(self, *a, **k):
            called.append(1)
            return True

    monkeypatch.setattr(fsr, "_singleton", _FakeRec())
    monkeypatch.setenv("FILTER_SHADOW_CAPTURE_MODE", "off")
    fsr.record_verdict("A", "S", {}, "f", "BLOCK")
    assert called == []


def test_record_verdict_never_raises(monkeypatch):
    from feeds import filter_shadow_recorder as fsr

    class _Boom:
        def record(self, *a, **k):
            raise RuntimeError("boom")

    monkeypatch.setattr(fsr, "_singleton", _Boom())
    monkeypatch.delenv("FILTER_SHADOW_CAPTURE_MODE", raising=False)
    # must swallow the error (fail-open) and not propagate
    fsr.record_verdict("A", "S", {}, "f", "BLOCK")


# ── compute_filter_pnl: dedup-by-pair + SHADOW_BLOCK + aggregation ───────────

class _FakeBar:
    def __init__(self, open_time, close, high=None, low=None):
        self.open_time = open_time
        self.close = close
        self.high = high if high is not None else close
        self.low = low if low is not None else close


class _CountingClient:
    """Fake DexScreenerClient that counts fetch_1m calls per pair."""

    def __init__(self, bars_by_pair):
        self.bars_by_pair = bars_by_pair
        self.calls = []

    async def fetch_1m(self, pair, limit=60):
        self.calls.append(pair)
        return self.bars_by_pair.get(pair, [])


def _rec(filter_name, verdict, pair, ts):
    return {
        "ts": ts, "filter_name": filter_name, "verdict": verdict,
        "pair_address": pair, "token_address": "addr_" + pair,
        "token_symbol": "S",
    }


def test_compute_filter_pnl_dedups_fetch_by_pair():
    from scripts import audit_filter_shadow_log as af

    # block_ts at t=1000; forward bars at >1000 -> a clean +10% move on PAIR1
    bars = {
        "PAIR1": [_FakeBar(1000, 100.0), _FakeBar(1060, 110.0, high=110.0)],
        "PAIR2": [_FakeBar(1000, 100.0), _FakeBar(1060, 90.0, low=90.0)],
    }
    client = _CountingClient(bars)
    # 3 records on PAIR1 (different filters), 2 on PAIR2 -> still only 2 fetches
    ts = "1970-01-01T00:16:40+00:00"  # =1000s
    recs = [
        _rec("filter_a", "BLOCK", "PAIR1", ts),
        _rec("filter_b", "BLOCK", "PAIR1", ts),
        _rec("filter_c", "PASS", "PAIR1", ts),
        _rec("filter_a", "BLOCK", "PAIR2", ts),
        _rec("filter_d", "BLOCK", "PAIR2", ts),
    ]
    out = asyncio.run(af.compute_filter_pnl(
        records=recs, client=client, min_forward_min=0, now_ts=2000,
        pace_secs=0.0,
    ))
    # DEDUP: exactly one fetch per distinct pair
    assert sorted(client.calls) == ["PAIR1", "PAIR2"]
    assert "filter_a" in out
    # filter_a saw one BLOCK on PAIR1 (winner) and one BLOCK on PAIR2 (loser)
    fa = out["filter_a"]
    assert fa["n"] == 2  # both BLOCK records scored


def test_compute_filter_pnl_treats_shadow_block_as_block():
    from scripts import audit_filter_shadow_log as af

    bars = {"P": [_FakeBar(1000, 100.0), _FakeBar(1060, 90.0, low=90.0)]}
    client = _CountingClient(bars)
    ts = "1970-01-01T00:16:40+00:00"
    recs = [
        _rec("filter_chasing_top", "SHADOW_BLOCK", "P", ts),
        _rec("filter_chasing_top", "PASS", "P", ts),
    ]
    out = asyncio.run(af.compute_filter_pnl(
        records=recs, client=client, min_forward_min=0, now_ts=2000,
        pace_secs=0.0,
    ))
    f = out["filter_chasing_top"]
    # SHADOW_BLOCK must land in the BLOCK bucket (not dropped) -> diff computable
    assert f["block_n"] == 1
    assert f["pass_n"] == 1
    assert f["pass_block_diff"] is not None


def test_compute_filter_pnl_writes_json(tmp_path):
    from scripts import audit_filter_shadow_log as af

    bars = {"P": [_FakeBar(1000, 100.0), _FakeBar(1060, 110.0, high=110.0)]}
    client = _CountingClient(bars)
    ts = "1970-01-01T00:16:40+00:00"
    recs = [_rec("filter_a", "BLOCK", "P", ts)]
    out_path = tmp_path / "filter_shadow_pnl.json"
    out = asyncio.run(af.compute_filter_pnl(
        records=recs, client=client, min_forward_min=0, now_ts=2000,
        pace_secs=0.0, out_path=str(out_path),
    ))
    assert out_path.exists()
    on_disk = json.loads(out_path.read_text())
    assert "filter_a" in on_disk
    assert on_disk["filter_a"]["n"] == out["filter_a"]["n"]


# ── compute_gate_pnl emit-json ───────────────────────────────────────────────

def test_compute_gate_pnl_importable_and_emits_json(tmp_path):
    from scripts import shadow_gate_pnl as sg

    events_path = tmp_path / "events.jsonl"
    events = [
        {"ts": "1970-01-01T00:16:40+00:00", "gate": "regime_buy_gate",
         "bot": "botA", "token_address": "AAA", "symbol": "S",
         "would_block": True, "ctx": {}},
    ]
    with open(events_path, "w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    trades = [{"type": "sell", "bot_id": "botA", "address": "AAA",
               "entry_ts": 1000.0, "pnl_pct": -10.0, "size_usd": 10.0}]
    out_path = tmp_path / "shadow_gate_pnl.json"
    out = sg.compute_gate_pnl(str(events_path), trades, max_skew=600,
                              out_path=str(out_path))
    assert "regime_buy_gate" in out
    assert out_path.exists()
    on_disk = json.loads(out_path.read_text())
    assert on_disk["regime_buy_gate"]["losers_blocked"] == 1


def test_compute_gate_pnl_missing_events_returns_empty(tmp_path):
    from scripts import shadow_gate_pnl as sg
    out = sg.compute_gate_pnl(str(tmp_path / "nope.jsonl"), [], max_skew=600)
    assert out == {}


# ── unified /api/filter-shadow read helper: fail-open on missing files ────────

def test_read_filter_shadow_payload_missing_files_failopen(tmp_path):
    from dashboard.web_dashboard import read_filter_shadow_payload
    payload = read_filter_shadow_payload(str(tmp_path))
    assert payload["ok"] is True
    assert payload["filters"] == {}
    assert payload["gates"] == {}
    assert "note" in payload


def test_read_filter_shadow_payload_reads_precomputed(tmp_path):
    from dashboard.web_dashboard import read_filter_shadow_payload
    (tmp_path / "filter_shadow_pnl.json").write_text(
        json.dumps({"filter_a": {"n": 3, "wr": 66.0}}))
    (tmp_path / "shadow_gate_pnl.json").write_text(
        json.dumps({"regime_buy_gate": {"n_blocked": 5}}))
    payload = read_filter_shadow_payload(str(tmp_path))
    assert payload["ok"] is True
    assert payload["filters"]["filter_a"]["n"] == 3
    assert payload["gates"]["regime_buy_gate"]["n_blocked"] == 5
    assert "generated_at" in payload
