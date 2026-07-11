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


# ── HOODLANA-class holder-structure stamps (2026-07-11 forensics) ──
def _th(pct, tag="", insider=False):
    return {"address": "x", "pct": pct, "tag": tag, "insider": insider}


def test_shoulder_11_20_extracted():
    # 10 holders @2% then 8 @5% (the HOODLANA fat-shoulder shape)
    th = [_th(2.0) for _ in range(10)] + [_th(5.0) for _ in range(8)]
    f = compute_holder_features({"topHolders": th})
    assert f["shoulder_11_20_pct"] == 40.0
    assert f["top10_holder_pct"] == 20.0


def test_pool_and_insider_pct_extracted():
    th = [_th(60.0, tag="AMM"), _th(9.0, insider=True), _th(3.0), _th(2.0, insider=True)]
    f = compute_holder_features({"topHolders": th, "totalHolders": 82})
    assert f["pool_topholder_pct"] == 60.0
    assert f["topholder_insider_pct"] == 11.0
    assert f["total_holders"] == 82
    # pool + insiders excluded from top10 as before (only the 3.0 holder is "real")
    assert f["top10_holder_pct"] == 3.0


def test_total_holders_bool_rejected():
    f = compute_holder_features({"topHolders": [_th(1.0)], "totalHolders": True})
    assert "total_holders" not in f


def test_hoodlana_entry_shape_passes_lp_gate():
    # HOODLANA at entry: lp_locked 100 not burned, rugcheck 1.0 -> LP gate PASSES
    # (documented: this gate does NOT catch the hidden-supply-dump class).
    assert rug_gate_verdict({"lp_locked_pct": 100.0, "lp_burned": False})[0] == "PASS"


# ── hidden-supply (HOODLANA-class) gate + vault-join pool identification ──
def test_pool_identified_by_vault_join_without_tags():
    # rugcheck dropped topHolders `tag` (2026-07) — pool must be found via
    # markets[].pubkey/liquidityA/B address join.
    rc = {
        "markets": [{"pubkey": "PAIR1", "liquidityA": "VAULTA", "liquidityB": "VAULTB",
                     "mintLP": "reallp", "lp": {"baseUSD": 100, "quoteUSD": 100, "lpLockedPct": 100.0}}],
        "topHolders": [
            {"address": "VAULTA", "owner": "POOLPDA", "pct": 12.45},
            {"address": "h1", "owner": "w1", "pct": 8.0},
            {"address": "h2", "owner": "w2", "pct": 6.71},
        ],
        "totalHolders": 82,
    }
    f = compute_holder_features(rc)
    assert f["pool_topholder_pct"] == 12.45          # vault excluded from real
    assert f["top10_holder_pct"] == 14.71            # only w1+w2
    assert f["hidden_supply_share_pct"] == 72.84     # 100 - 12.45 - 14.71


def test_hoodlana_entry_shape_now_BLOCKS():
    # The chain-reconstructed HOODLANA entry state: hidden 72.84, thin holder base.
    v, r = rug_gate_verdict({"lp_locked_pct": 100.0, "lp_burned": False,
                             "hidden_supply_share_pct": 72.84, "total_holders": 82})
    assert v == "BLOCK" and "HOODLANA class" in r[0]


def test_burned_lp_does_not_bypass_hidden_check():
    v, _ = rug_gate_verdict({"lp_burned": True,
                             "hidden_supply_share_pct": 72.84, "total_holders": 82})
    assert v == "BLOCK"


def test_hidden_supply_needs_both_conditions():
    # big hidden share but broad holder base -> PASS (distributed retail, not a cluster)
    assert rug_gate_verdict({"hidden_supply_share_pct": 75.0, "total_holders": 5000,
                             "lp_locked_pct": 100.0, "lp_burned": False})[0] == "PASS"
    # thin base but low hidden share -> PASS
    assert rug_gate_verdict({"hidden_supply_share_pct": 30.0, "total_holders": 82,
                             "lp_locked_pct": 100.0, "lp_burned": False})[0] == "PASS"


def test_hidden_supply_missing_fails_open():
    # neither signal's inputs -> NEUTRAL (fail-open, unchanged posture)
    assert rug_gate_verdict({})[0] == "NEUTRAL"
    # lp known-clean, hidden unknown -> PASS (not NEUTRAL)
    assert rug_gate_verdict({"lp_locked_pct": 100.0, "lp_burned": False})[0] == "PASS"


def test_raydium_v4_authority_counts_as_pool():
    rc = {"markets": [{"mintLP": "x", "lp": {"baseUSD": 1, "quoteUSD": 1, "lpLockedPct": 100.0}}],
          "topHolders": [{"address": "v", "owner": "5Q544fKrFoe6tsEbD7S8EmxGTJYAKtTVhAW5Q5pge4j1", "pct": 40.0},
                         {"address": "h", "owner": "w", "pct": 5.0}],
          "totalHolders": 200}
    f = compute_holder_features(rc)
    assert f["pool_topholder_pct"] == 40.0 and f["top10_holder_pct"] == 5.0
