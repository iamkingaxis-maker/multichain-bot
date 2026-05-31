"""Tests for the rolling never-green scorer + its bot_evaluator gate.

Verifies the SAFETY properties (fail-open, mode gating, opt-in) — not the model
accuracy (that's validated offline in scripts/rolling_scorer.py + universe_scorer_test.py).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.ng_scorer import RollingNGScorer, _pair_completed, scorer_mode, NG_PEAK_THRESHOLD


def test_fail_open_when_untrained():
    sc = RollingNGScorer()
    # No model -> score None, should_block False (NEVER block on missing model).
    assert sc.score({"pc_h1": -5.0, "bs_h1": 1.2}) is None
    block, proba = sc.should_block({"pc_h1": -5.0})
    assert block is False and proba is None


def test_scorer_mode_default_off(monkeypatch):
    monkeypatch.delenv("NG_SCORER_MODE", raising=False)
    assert scorer_mode() == "off"
    monkeypatch.setenv("NG_SCORER_MODE", "ENFORCE")
    assert scorer_mode() == "enforce"


def test_pair_completed_labels_never_green():
    trades = [
        # token A: peak 0.3% -> never-green (1)
        {"bot_id": "b1", "type": "buy", "token": "A", "time": "2026-05-20T01:00:00+00:00",
         "entry_meta": {"pc_h1": -4.0, "bs_h1": 1.1}},
        {"bot_id": "b1", "type": "sell", "token": "A", "time": "2026-05-20T01:05:00+00:00",
         "peak_pnl_pct": 0.3, "fully_closed": True, "pnl": -2.0},
        # token B: peak 12% -> reached green (0)
        {"bot_id": "b1", "type": "buy", "token": "B", "time": "2026-05-20T02:00:00+00:00",
         "entry_meta": {"pc_h1": -2.0, "bs_h1": 1.9}},
        {"bot_id": "b1", "type": "sell", "token": "B", "time": "2026-05-20T02:05:00+00:00",
         "peak_pnl_pct": 12.0, "fully_closed": True, "pnl": 1.5},
    ]
    comp = _pair_completed(trades)
    by_tok = {c["tok"]: c for c in comp}
    assert by_tok["A"]["ng"] == 1   # peak 0.3 < threshold -> never-green
    assert by_tok["B"]["ng"] == 0   # peak 12 >= threshold -> reached green
    assert NG_PEAK_THRESHOLD == 2.0  # retargeted 2026-05-31 (peak<2 dominates peak<1)


def test_train_fail_open_on_insufficient_data():
    # Empty DATA_DIR -> not enough rows -> train returns False, stays fail-open.
    sc = RollingNGScorer()
    import tempfile
    d = tempfile.mkdtemp()
    os.environ["DATA_DIR"] = d
    try:
        assert sc.train() is False
        assert sc.model is None
        assert sc.should_block({"pc_h1": -5.0}) == (False, None)
    finally:
        os.environ.pop("DATA_DIR", None)


def test_log_decision_appends_and_failsoft(tmp_path, monkeypatch):
    import json
    from core import ng_scorer
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    ng_scorer.log_decision({"t": "2026-05-31T00:00:00+00:00", "bot": "champion_premium",
                            "token": "X", "p": 0.7, "thr": 0.5, "blocked": True,
                            "mode": "enforce", "triggers": ["deep_1h_dip"]})
    p = tmp_path / "ng_scorer" / "decisions.jsonl"
    assert p.exists()
    rec = json.loads(p.read_text(encoding="utf-8").strip())
    assert rec["bot"] == "champion_premium" and rec["blocked"] is True
    # fail-soft: an unserializable record must not raise
    ng_scorer.log_decision({"bad": object()})


if __name__ == "__main__":
    test_fail_open_when_untrained()
    test_pair_completed_labels_never_green()
    test_train_fail_open_on_insufficient_data()
    print("ng_scorer tests passed")
