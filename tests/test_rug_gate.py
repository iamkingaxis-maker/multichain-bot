"""rug_gate verdict + compute_holder_features LP-lock/burn/score extraction (2026-06-04)."""
from core.rug_gate import rug_gate_verdict, lp_lock_min_pct
from core.holder_features import compute_holder_features

SYS = "11111111111111111111111111111111"  # system address => burned LP


# ── rug_gate_verdict ──
def test_burned_passes():
    assert rug_gate_verdict({"lp_burned": True, "lp_locked_pct": 0.0})[0] == "PASS"  # burn > lock


def test_unlocked_not_burned_blocks():
    assert rug_gate_verdict({"lp_locked_pct": 0.0, "lp_burned": False})[0] == "BLOCK"


def test_locked_passes():
    assert rug_gate_verdict({"lp_locked_pct": 95.0, "lp_burned": False})[0] == "PASS"


def test_unknown_fails_open():
    assert rug_gate_verdict({})[0] == "NEUTRAL"
    assert rug_gate_verdict({"lp_locked_pct": None})[0] == "NEUTRAL"
    assert rug_gate_verdict({"lp_locked_pct": True})[0] == "NEUTRAL"


def test_threshold_env(monkeypatch):
    monkeypatch.setenv("RUG_GATE_LP_LOCK_MIN", "50")
    assert lp_lock_min_pct() == 50.0
    assert rug_gate_verdict({"lp_locked_pct": 40, "lp_burned": False})[0] == "BLOCK"
    assert rug_gate_verdict({"lp_locked_pct": 60, "lp_burned": False})[0] == "PASS"


# ── compute_holder_features LP extraction ──
def test_burn_override_sets_100():
    rc = {"markets": [{"mintLP": SYS, "lp": {"baseUSD": 100, "quoteUSD": 5000}}]}
    f = compute_holder_features(rc)
    assert f["lp_locked_pct"] == 100.0 and f["lp_burned"] is True


def test_dominant_pool_lplocked_extracted():
    rc = {"markets": [
        {"mintLP": "Xabc", "lp": {"baseUSD": 10, "quoteUSD": 100, "lpLockedPct": 12.0}},   # small
        {"mintLP": "Ydef", "lp": {"baseUSD": 100, "quoteUSD": 9000, "lpLockedPct": 88.0}},  # dominant
    ]}
    f = compute_holder_features(rc)
    assert f["lp_locked_pct"] == 88.0 and f["lp_burned"] is False  # dominant pool's value


def test_toplevel_lplocked_fallback():
    rc = {"lpLockedPct": 73.0, "markets": [{"mintLP": "Z", "lp": {"baseUSD": 50, "quoteUSD": 500}}]}
    f = compute_holder_features(rc)
    assert f["lp_locked_pct"] == 73.0


def test_score_extracted():
    assert compute_holder_features({"score_normalised": 42.5})["rugcheck_score"] == 42.5
    assert compute_holder_features({"score": 17})["rugcheck_score"] == 17.0


def test_no_markets_no_lp_field():
    f = compute_holder_features({"topHolders": []})
    assert "lp_locked_pct" not in f  # absent -> rug_gate fails open


def test_endtoend_burned_token_passes_gate():
    rc = {"markets": [{"mintLP": SYS, "lp": {"baseUSD": 100, "quoteUSD": 5000}}]}
    assert rug_gate_verdict(compute_holder_features(rc))[0] == "PASS"


def test_endtoend_unlocked_token_blocks():
    rc = {"markets": [{"mintLP": "realmint", "lp": {"baseUSD": 100, "quoteUSD": 5000, "lpLockedPct": 0.0}}]}
    assert rug_gate_verdict(compute_holder_features(rc))[0] == "BLOCK"


# ── one-shot-sniped 'bundle' rug gate (2026-06-14) ──
from core.bot_evaluator import _rug_bundle_blocks, _rug_bundle_mode, _rug_bundle_spread_max


class _B:
    def __init__(self, **meta):
        self.raw_meta = meta
        self.token = "TST"


def test_bundle_blocks_norepeat_and_sniped():
    # 0 recurring buyers AND top-10 bundled within 25s = one-shot sniped rug
    assert _rug_bundle_blocks(_B(n_recurring_buyers_3plus=0, top10_buyer_time_spread_sec=15))[0] is True


def test_bundle_passes_with_recurring_buyers():
    # real returning demand -> never block, even if sniped (winners look like this)
    assert _rug_bundle_blocks(_B(n_recurring_buyers_3plus=2, top10_buyer_time_spread_sec=5))[0] is False


def test_bundle_passes_organic_spread():
    # buyers spread out (organic) -> never block, even with 0 recurring
    assert _rug_bundle_blocks(_B(n_recurring_buyers_3plus=0, top10_buyer_time_spread_sec=200))[0] is False


def test_bundle_fails_open_when_missing():
    assert _rug_bundle_blocks(_B())[0] is False
    assert _rug_bundle_blocks(_B(n_recurring_buyers_3plus=0))[0] is False          # spread missing
    assert _rug_bundle_blocks(_B(top10_buyer_time_spread_sec=5))[0] is False       # recurring missing


def test_bundle_bool_not_treated_as_number():
    # guard: False must NOT be read as 0 (would falsely fire)
    assert _rug_bundle_blocks(_B(n_recurring_buyers_3plus=False, top10_buyer_time_spread_sec=5))[0] is False


def test_bundle_mode_default_enforce(monkeypatch):
    monkeypatch.delenv("RUG_BUNDLE_MODE", raising=False)
    assert _rug_bundle_mode() == "enforce"
    monkeypatch.setenv("RUG_BUNDLE_MODE", "shadow")
    assert _rug_bundle_mode() == "shadow"


def test_bundle_spread_threshold_env(monkeypatch):
    monkeypatch.setenv("RUG_BUNDLE_SPREAD_MAX_SEC", "40")
    assert _rug_bundle_spread_max() == 40.0
    assert _rug_bundle_blocks(_B(n_recurring_buyers_3plus=0, top10_buyer_time_spread_sec=35))[0] is True   # 35<=40
    assert _rug_bundle_blocks(_B(n_recurring_buyers_3plus=0, top10_buyer_time_spread_sec=45))[0] is False  # 45>40
