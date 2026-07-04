"""Missing-data-read-as-zero bug-class regression tests (2026-07-02 sweep).

THE CLASS: a feature derived from FETCHED data (trade log, velocity, tier3
flows, maker data) defaults to 0/0.0/neutral when the fetch fails or returns
empty, and a gate then treats that default as a REAL measurement and blocks.
Three prior members: the rug-gate maker-key fix (07-01), the tier3 15s
imbalance n>=3 fix (07-01), and the ELON corpse-gate calm-branch fix (07-02).

This file locks in the 07-02 sweep fixes: missing data must NEVER produce a
block; real measured data must keep the exact gate behavior it had before.
"""
import math

import pytest

from feeds.trade_log_features import analyze as tlf_analyze
from feeds.trade_velocity import analyze as tv_analyze
from feeds.tier3_features import compute_net_flow_windows
from feeds.smart_money import extract_top_makers
from core.trigger_state_gates import trigger_state_verdicts
from core.bot_evaluator import (
    BotEvaluator,
    full_thesis_cohort_eval,
    winner_demand_selected,
    post_pump_corpse_blocks,
    nf5m_toxic_zone_blocks,
    _rug_structure_blocks,
)
from core.bot_config import BotConfig
from core.feature_bundle import FeatureBundle


def _bundle(**overrides):
    defaults = dict(
        token="TEST", address="a", pair_address="p", chain="solana",
        snapshot_ts=1716480000.0, price_usd=0.001, mcap_usd=4_000_000.0,
        age_hours=240.0,
        pc_h24=None, pc_h6=None, pc_h1=None, pc_m5=None,
        vol_h1_usd=50_000.0, bs_h1=None,
        sol_pc_h1=None, sol_pc_h4=None, sol_pc_h6=None, sol_pc_h24=None,
        btc_pc_h1=None, btc_pc_h6=None, btc_bs_h1=None,
        net_flow_15s_usd=None, net_flow_60s_usd=None, net_flow_5m_usd=None,
        top_buy_makers_n=None, p90_buy_size_usd=None,
        chart_mtf_score=None, chart_score=None,
        cnn_cluster_id=None, fusion_outcome_prob=None,
        triggers_fired=("vol_breakout",),
        triggers_shadow=(), filters_block=(), filters_pass=(), filters_shadow=(),
        raw_meta={},
    )
    defaults.update(overrides)
    return FeatureBundle(**defaults)


# ── trade_log_features: fabricated size/ratio defaults -> None ────────────────

def test_trade_log_empty_fetch_size_keys_are_none():
    """Empty trade log (fetch failure) => size/ratio features UNKNOWN, not 0."""
    f = tlf_analyze([])
    assert f["median_buy_size_usd"] is None
    assert f["p90_buy_size_usd"] is None
    assert f["mean_buy_size_usd"] is None
    assert f["buy_sell_volume_imbalance"] is None
    assert f["large_buyer_volume_pct"] is None
    assert f["largest_buy_to_largest_sell"] is None
    # maker-derived keys stay None (07-01 fix, must not regress)
    assert f["unique_buyers_n"] is None
    assert f["n_recurring_buyers_3plus"] is None
    # counts keep historical defaults (no blocking consumer)
    assert f["n_large_buys_500_30m"] == 0
    assert f["n_consecutive_buys_at_end"] == 0


def test_trade_log_real_buys_still_measured():
    """Real maker-tagged buys => real numbers, unchanged computation."""
    trades = [
        {"kind": "buy", "volume_usd": 50.0, "ts": "2026-07-02T00:00:00Z",
         "maker": "w1"},
        {"kind": "buy", "volume_usd": 30.0, "ts": "2026-07-02T00:00:01Z",
         "maker": "w2"},
        {"kind": "sell", "volume_usd": 20.0, "ts": "2026-07-02T00:00:02Z"},
    ]
    f = tlf_analyze(trades)
    assert f["median_buy_size_usd"] == 50.0  # sorted [30,50], n//2 = idx 1
    assert f["mean_buy_size_usd"] == 40.0
    assert f["unique_buyers_n"] == 2
    assert 0.0 < f["buy_sell_volume_imbalance"] < 1.0


def test_trade_log_maker_stripped_buys_unknown_buyers_but_real_sizes():
    """GT-fallback (maker stripped): sizes measured, buyer identity unknown."""
    trades = [
        {"kind": "buy", "volume_usd": 50.0, "ts": "2026-07-02T00:00:00Z"},
        {"kind": "buy", "volume_usd": 30.0, "ts": "2026-07-02T00:00:01Z"},
    ]
    f = tlf_analyze(trades)
    assert f["median_buy_size_usd"] == 50.0
    assert f["unique_buyers_n"] is None


# ── the FULL_THESIS enforce gate must not block on the empty-fetch defaults ───

def test_full_thesis_no_block_on_empty_fetch_median():
    """The bug: _empty() median=0.0 read as 'buyer$0<34.3 -> BLOCK' under
    FULL_THESIS_COHORT_MODE=enforce. With None the buyer half is missing =>
    fail-open (never dark the fleet on a data gap)."""
    med = tlf_analyze([])["median_buy_size_usd"]
    selected, blocked, why = full_thesis_cohort_eval(-12.0, med)
    assert blocked is False
    assert selected is False
    assert "buyer missing" in why


def test_full_thesis_still_blocks_on_measured_low_buyer():
    """Real measured low buyer size must KEEP blocking (no logic change)."""
    selected, blocked, why = full_thesis_cohort_eval(-12.0, 5.0)
    assert blocked is True


def test_winner_demand_not_selected_on_missing():
    sel, why = winner_demand_selected(tlf_analyze([])["median_buy_size_usd"])
    assert sel is False and why == ""


# ── trade_velocity: fabricated neutral buy_pressure -> None ──────────────────

def test_velocity_empty_fetch_buy_pressure_unknown():
    v = tv_analyze([])
    assert v["buy_pressure_60s"] is None
    # deliberately unchanged blanks (documented in trade_velocity.py):
    assert v["buys_per_min_recent"] == 0.0
    assert v["sells_per_min_recent"] == 0.0
    assert v["velocity_verdict"] == "QUIET"


def test_trigger_state_gates_na_on_empty_fetch_features():
    """The latent trigger-state member: blank bp=0.5 / bs_imb=0.5 evaluated as
    REAL flow, dropping informed_cluster (needs <=0.40), swing_structure_rsi
    (needs >=0.57) and whale_conviction (needs <=0.38) on a data gap. With the
    None blanks every gate reads 'na' (fail-open)."""
    feats = {}
    feats.update(tv_analyze([]))
    feats.update(tlf_analyze([]))
    v = trigger_state_verdicts(
        ("informed_cluster", "swing_structure_rsi", "whale_conviction"), feats)
    assert v == {"informed_cluster": "na", "swing_structure_rsi": "na",
                 "whale_conviction": "na"}


def test_trigger_state_gates_real_data_unchanged():
    assert trigger_state_verdicts(
        ("informed_cluster",), {"buy_pressure_60s": 0.30}
    ) == {"informed_cluster": "pass"}
    assert trigger_state_verdicts(
        ("informed_cluster",), {"buy_pressure_60s": 0.70}
    ) == {"informed_cluster": "block"}


# ── smart_money.extract_top_makers: fabricated 0 unique buyers -> None ────────

def test_top_makers_unknown_when_no_maker_data():
    assert extract_top_makers([])["top_buy_makers_n"] is None
    stripped = [{"kind": "buy", "volume_usd": 50.0, "ts": "t"}]
    assert extract_top_makers(stripped)["top_buy_makers_n"] is None


def test_top_makers_real_data_still_counted():
    trades = [
        {"kind": "buy", "volume_usd": 50.0, "maker": "w1"},
        {"kind": "buy", "volume_usd": 25.0, "maker": "w2"},
    ]
    out = extract_top_makers(trades)
    assert out["top_buy_makers_n"] == 2
    assert len(out["top_buy_makers"]) == 2


# ── corpse gate: calm branch never fires on unknown tape ──────────────────────

def test_post_pump_corpse_fail_open_on_unknown_bpm():
    # pumped h24 but bpm unknown (fetch failed) -> no block
    assert post_pump_corpse_blocks(10.0, 300.0, None)[0] is False
    # REAL measured calm tape still blocks (behavior preserved)
    assert post_pump_corpse_blocks(10.0, 300.0, 0.0)[0] is True


# ── tier3 net-flow / NF5M toxic zone: absent-key convention holds ─────────────

def test_tier3_net_flow_empty_returns_empty_dict():
    """{} (absent keys) — NOT 0.0 — so the NF5M toxic zone [0,+300) can never
    swallow a failed fetch (0.0 would sit INSIDE the block band)."""
    assert compute_net_flow_windows([]) == {}


def test_nf5m_toxic_zone_fail_open_on_missing_and_blocks_on_real_zero():
    blocked, why = nf5m_toxic_zone_blocks(None)
    assert blocked is False and "missing" in why
    blocked, why = nf5m_toxic_zone_blocks(float("nan"))
    assert blocked is False
    # a REAL measured 0.0 net flow is genuinely inside the toxic band —
    # that behavior is unchanged (the fix is upstream: a failed fetch now
    # produces an ABSENT key, never a fabricated 0.0).
    assert nf5m_toxic_zone_blocks(0.0)[0] is True
    assert nf5m_toxic_zone_blocks(-50.0)[0] is False
    assert nf5m_toxic_zone_blocks(300.0)[0] is False


# ── evaluator entry_gate: None/missing feature => condition skipped ───────────

def test_entry_gate_condition_fails_open_on_missing_feature():
    """core/bot_evaluator.py ~1231-1238: `raw_meta.get(f)` then
    `isinstance(v, (int, float))` else `continue` — a None/absent feature
    skips the condition (fail-OPEN), it never blocks the buy."""
    cfg = BotConfig(bot_id="t", display_name="T",
                    entry_gate=(("unique_buyers_n", ">=", 5.0),))
    ev = BotEvaluator(cfg)
    # missing entirely -> pass
    assert ev._token_regime_passes(_bundle(raw_meta={})) is True
    # None (the post-fix marker for a data gap) -> pass
    assert ev._token_regime_passes(
        _bundle(raw_meta={"unique_buyers_n": None})) is True
    # REAL failing value -> still blocks (behavior preserved)
    assert ev._token_regime_passes(
        _bundle(raw_meta={"unique_buyers_n": 2})) is False
    # REAL passing value -> passes
    assert ev._token_regime_passes(
        _bundle(raw_meta={"unique_buyers_n": 9})) is True


# ── rug gate: the original class member stays fixed ───────────────────────────

def test_rug_gate_fail_open_on_unknown_buyers():
    assert _rug_structure_blocks(
        _bundle(raw_meta={"unique_buyers_n": None}))[0] is False
    assert _rug_structure_blocks(_bundle(raw_meta={}))[0] is False
    # real zero still blocks
    assert _rug_structure_blocks(
        _bundle(raw_meta={"unique_buyers_n": 0}))[0] is True


# ── filter_bs_m5_weak rescue semantics (site logic mirrored) ──────────────────

def test_bs_m5_weak_unknown_buyers_is_not_low():
    """Mirrors the fixed inline predicate in feeds/dip_scanner.py (~11793):
    `unique_buyers_n` may only prove 'few buyers' when it is a MEASURED
    number; None (fetch failure / maker-stripped tape) must fail open."""
    def ub_low(v):
        return (isinstance(v, (int, float))
                and not isinstance(v, bool) and v < 12)
    assert ub_low(tlf_analyze([])["unique_buyers_n"]) is False   # gap
    assert ub_low(None) is False
    assert ub_low(True) is False
    assert ub_low(3) is True        # real low -> still blocks
    assert ub_low(15) is False      # real high -> rescue


class TestWhaleMaxBuyNoneOnEmpty:
    """2026-07-03: whale_max_buy_usd fabricated $0 on missing tape — a future
    absorption print-gate (max_print>=50) would block on failed fetches. Now
    None on empty; real tapes still produce the measured max."""

    def test_empty_tape_yields_none(self):
        from feeds.trade_log_features import analyze
        assert analyze([])["whale_max_buy_usd"] is None
        assert analyze(None)["whale_max_buy_usd"] is None

    def test_no_buys_yields_none(self):
        from feeds.trade_log_features import analyze
        out = analyze([{"kind": "sell", "volume_usd": 100.0}])
        assert out["whale_max_buy_usd"] is None

    def test_real_tape_measures_max(self):
        from feeds.trade_log_features import analyze
        out = analyze([{"kind": "buy", "volume_usd": 55.0},
                       {"kind": "buy", "volume_usd": 210.0}])
        assert out["whale_max_buy_usd"] == 210.0

    def test_boolean_whale_present_stays_false_on_empty(self):
        # absence-of-evidence IS the correct semantic for a presence trigger
        from feeds.trade_log_features import analyze
        assert analyze([])["whale_buy_present_2k"] is False


class TestEntryGateRequireData:
    """2026-07-04: demand-thesis bots opt into FAIL-CLOSED entry gates.
    young_absorb bled -40pp in a day when tape fetches failed and
    buyers/nf15=None waived the demand gates (5 no-data entries all lost;
    the observed-demand entry won). Default stays fail-open."""

    def _eval(self, require, meta):
        import json, pathlib
        from core.bot_config import BotConfig
        from core.bot_evaluator import BotEvaluator, FeatureBundle
        base = json.loads(pathlib.Path("config/bots/badday_young_absorb.json").read_text())
        base["entry_gate_require_data"] = require
        cfg = BotConfig(**base)
        ev = BotEvaluator.__new__(BotEvaluator)
        b = FeatureBundle.__new__(FeatureBundle)
        b.raw_meta = meta
        return ev._entry_gate_passes(cfg, b) if hasattr(ev, "_entry_gate_passes") else None

    def test_configs_carry_the_flag(self):
        import json, pathlib
        for name in ("badday_young_absorb", "badday_adolescent_absorb",
                     "badday_swing_latch"):
            c = json.loads(pathlib.Path(f"config/bots/{name}.json").read_text())
            assert c.get("entry_gate_require_data") is True, name

    def test_family_default_stays_fail_open(self):
        import json, pathlib
        from core.bot_config import BotConfig
        c = BotConfig(**json.loads(
            pathlib.Path("config/bots/badday_flush.json").read_text()))
        assert getattr(c, "entry_gate_require_data", False) is False
