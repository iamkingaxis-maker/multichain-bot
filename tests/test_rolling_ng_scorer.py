"""Production rolling never-green scorer (sklearn HistGBM port) — train/score/persist,
fail-open, and NaN-tolerant train/serve parity."""
import os
import tempfile
from core.rolling_ng_scorer import RollingNGScorer


def _synthetic(n=400):
    """Build a learnable set: high `pc_h1` -> never-green (dud). Grouped by token."""
    rows, y, grp = [], [], []
    for i in range(n):
        dud = i % 2
        # dud tokens have high pc_h1 + old age; greens are fresh + dipping
        rows.append({"pc_h1": (5.0 if dud else -5.0) + (i % 7) * 0.1,
                     "lifecycle_age_hours": (300.0 if dud else 5.0) + (i % 5),
                     "1m_volume_spike": 0.4 + (i % 3) * 0.1})
        y.append(dud)
        grp.append(f"tok{i % 20}")
    return rows, y, grp


def test_train_and_score_separates():
    rows, y, grp = _synthetic()
    s = RollingNGScorer(target_block_rate=0.2).train(rows, y, grp)
    assert s.clf is not None, "should have trained on 400 rows"
    # a clearly-dud-shaped entry scores higher than a clearly-green one
    dud = s.score({"pc_h1": 6.0, "lifecycle_age_hours": 320, "1m_volume_spike": 0.4})
    green = s.score({"pc_h1": -6.0, "lifecycle_age_hours": 4, "1m_volume_spike": 0.5})
    assert dud > green, f"dud {dud} should score above green {green}"


def test_threshold_blocks_high_scores():
    rows, y, grp = _synthetic()
    s = RollingNGScorer(target_block_rate=0.2).train(rows, y, grp)
    assert s.should_block({"pc_h1": 6.0, "lifecycle_age_hours": 320, "1m_volume_spike": 0.4})
    assert not s.should_block({"pc_h1": -6.0, "lifecycle_age_hours": 4, "1m_volume_spike": 0.5})


def test_fail_open_untrained():
    s = RollingNGScorer()
    assert s.score({"pc_h1": 6.0}) == 0.0
    assert s.should_block({"pc_h1": 6.0}) is False


def test_fail_open_insufficient_data():
    s = RollingNGScorer().train([{"pc_h1": 1.0}] * 10, [0] * 10, ["t"] * 10)
    assert s.clf is None and s.should_block({"pc_h1": 1.0}) is False


def test_missing_feature_at_scoring_does_not_raise():
    # train/serve skew: score an entry MISSING a trained feature -> NaN-imputed, no raise
    rows, y, grp = _synthetic()
    s = RollingNGScorer().train(rows, y, grp)
    v = s.score({"pc_h1": 6.0})  # no lifecycle_age_hours / 1m_volume_spike
    assert isinstance(v, float) and 0.0 <= v <= 1.0


def test_confound_features_excluded():
    rows, y, grp = _synthetic()
    # inject leakage columns that MUST be dropped
    for r in rows:
        r["pnl_pct"] = 50.0; r["peak_pnl_pct"] = 99.0; r["entry_ts_ms"] = 1.0
    s = RollingNGScorer().train(rows, y, grp)
    for bad in ("pnl_pct", "peak_pnl_pct", "entry_ts_ms"):
        assert bad not in s.feats, f"{bad} is leakage and must be excluded"


def test_save_load_roundtrip():
    rows, y, grp = _synthetic()
    s = RollingNGScorer().train(rows, y, grp)
    probe = {"pc_h1": 6.0, "lifecycle_age_hours": 320, "1m_volume_spike": 0.4}
    before = s.score(probe)
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "rng")
        s.save(path)
        s2 = RollingNGScorer().load(path)
        assert s2 is not None
        assert abs(s2.score(probe) - before) < 1e-9
        assert s2.feats == s.feats and s2.threshold == s.threshold


def test_load_missing_returns_none():
    assert RollingNGScorer().load("/nonexistent/path/xyz") is None
