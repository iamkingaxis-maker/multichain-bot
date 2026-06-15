"""Meta chameleon — the fixed dynamic bot (2026-06-12). Geometry-only retune,
quiesce on open positions, clamps, persistence, boot overlay."""
import json
import time
import types

import core.meta_chameleon as ch
from core.bot_config import BotConfig
import pytest


@pytest.fixture(autouse=True)
def _wear_on(monkeypatch):
    """Board-archetype WEARING is opt-in since the 2026-06-15 wear-gate (production
    default = green base = the +$ static twin). These tests exercise the wearing
    mechanism, so enable it here; the green-base/regime tests are unaffected (the gate
    only fires when an archetype qualifies)."""
    monkeypatch.setenv("CHAMELEON_WEAR_ARCHETYPES", "on")


def _cfg():
    return BotConfig.from_json("config/bots/meta_chameleon.json")


def _pm(cfg, open_positions=0):
    return types.SimpleNamespace(
        config=cfg,
        iter_positions=lambda: [object()] * open_positions,
    )


def _scanner(pm):
    return types.SimpleNamespace(bot_position_managers={"meta_chameleon": pm})


def test_regime_red_only_on_broad_red_not_euphoria():
    """Root-cause fix 2026-06-15: red deep-flush must fire on broad-RED capitulation
    ONLY, never SOL-euphoria (deep-flush dip-buying inverts in euphoria -> the chameleon
    was deep-flushing into the SOL-pump and bleeding -$90)."""
    R = lambda regime: ch._regime_is_red(types.SimpleNamespace(_cycle_regime=regime))
    assert R({"sol_pc_h24": 0.0, "regime_h1_neg_pct": 55}) is True     # broad capitulation -> red
    assert R({"sol_pc_h24": 3.0, "regime_h1_neg_pct": 15}) is False    # SOL euphoria -> NOT red (stay green)
    assert R({"sol_pc_h24": 3.0, "regime_h1_neg_pct": 55}) is True     # both -> red (broad_red present)
    assert R({"sol_pc_h24": -1.0, "regime_h1_neg_pct": 19}) is False   # good regime -> not red
    assert R(None) is False                                             # no regime snapshot -> not red


class _FakeSensor:
    def __init__(self, board, geo):
        self._board, self._geo = board, geo

    def scoreboard(self, now=None):
        return {"windows": {"6h": self._board}}

    def archetype_geometry(self, arch, now=None, window_secs=21600, min_n=8):
        return self._geo.get(arch)


def _patch(monkeypatch, tmp_path, sensor):
    monkeypatch.setattr(ch, "_TUNE_FILE", str(tmp_path / "tune.json"))
    monkeypatch.setattr(ch, "_last_check", 0.0)
    import core.meta_sensor as ms
    monkeypatch.setattr(ms, "_SENSOR", sensor)


GEO = {"n": 12, "wr": 0.75, "med_win_pct": 35.0, "med_loss_pct": -28.0,
       "med_hold_secs": 1800, "p75_hold_secs": 5400,
       "wallets": {"Wal1": 5, "Wal2": 4, "Wal3": 3},
       "n_wallets": 3, "top_wallet_share": 0.42}


def test_tune_from_geometry_and_clamps():
    # GEO med_loss -28 -> 1.2*-28=-33.6 -> copy-stop-floor caps at -25 (2026-06-13)
    t = ch.tune_from_geometry(GEO)
    assert t == {"time_stop_minutes": 90.0, "tp1_pct": 35.0, "hard_stop_pct": -25.0}
    wild = {"med_win_pct": 400.0, "med_loss_pct": -95.0, "p75_hold_secs": 10 * 86400}
    t = ch.tune_from_geometry(wild)
    assert t["tp1_pct"] == 60.0 and t["time_stop_minutes"] == 780.0
    assert t["hard_stop_pct"] == -25.0   # -114 clamped to -60 then floored to -25
    assert ch.tune_from_geometry({"med_win_pct": None, "p75_hold_secs": None}) is None


def test_copy_stop_floor_enforced_on_apply_of_persisted_loose_tune():
    # a persisted/overlay tune with a loose -60 stop must be floored when APPLIED
    # (the boot-overlay path bypasses tune_from_geometry).
    cfg = _cfg()
    ch._apply(cfg, {"time_stop_minutes": 90.0, "tp1_pct": 11.0, "hard_stop_pct": -60.0})
    assert cfg.hard_stop_pct == -25.0
    # a tighter stop is untouched
    ch._apply(cfg, {"hard_stop_pct": -12.0})
    assert cfg.hard_stop_pct == -12.0


def test_copy_stop_floor_only_bites_deep_tail():
    # a SHALLOW archetype stop (tight) is untouched; only the deep/loose stop is floored
    mid = {"med_win_pct": 12.0, "med_loss_pct": -15.0, "p75_hold_secs": 1200}  # 1.2*-15=-18
    assert ch.tune_from_geometry(mid)["hard_stop_pct"] == -18.0   # in (-25,-10), untouched
    deep = {"med_win_pct": 12.0, "med_loss_pct": -50.0, "p75_hold_secs": 1200}     # 1.2*-50=-60
    assert ch.tune_from_geometry(deep)["hard_stop_pct"] == -25.0     # floored
    none_loss = {"med_win_pct": 12.0, "med_loss_pct": None, "p75_hold_secs": 1200}  # -60 default
    assert ch.tune_from_geometry(none_loss)["hard_stop_pct"] == -25.0


def test_retune_applies_on_flat_book(monkeypatch, tmp_path):
    cfg = _cfg()
    sensor = _FakeSensor({"timebox": {"n": 12, "wr": 0.75}}, {"timebox": GEO})
    _patch(monkeypatch, tmp_path, sensor)
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=time.time())
    assert cfg.time_stop_minutes == 90.0
    assert cfg.tp1_pct == 35.0
    assert cfg.hard_stop_pct == -25.0   # copy-stop-floor (was -33.6 pre-2026-06-13)
    st = json.load(open(str(tmp_path / "tune.json")))
    assert st["meta_chameleon"]["archetype"] == "timebox"


def test_quiesce_defers_until_flat(monkeypatch, tmp_path):
    cfg = _cfg()
    sensor = _FakeSensor({"surgical": {"n": 10, "wr": 0.8}}, {"surgical": GEO})
    _patch(monkeypatch, tmp_path, sensor)
    pm = _pm(cfg, open_positions=3)
    ch.maybe_retune(_scanner(pm), now=time.time())
    assert cfg.time_stop_minutes == 240.0          # unchanged — book not flat
    st = json.load(open(str(tmp_path / "tune.json")))
    assert st["meta_chameleon"]["pending"]["archetype"] == "surgical"
    # book goes flat -> deferred tune applies on next check
    monkeypatch.setattr(ch, "_last_check", 0.0)
    pm.iter_positions = lambda: []
    ch.maybe_retune(_scanner(pm), now=time.time())
    assert cfg.time_stop_minutes == 90.0


def test_green_momentum_default_when_no_copyable_winner(monkeypatch, tmp_path):
    # No board archetype clears the bars -> the chameleon must NOT stand down (idle
    # through winning tape was the bug). It runs the proven timebox DIRECT geometry
    # and ALLOWS entries; a prior board-wear that tightened the runner stop is
    # REPAIRED back to -60 (raw, not the -25 copy-floor, not left at the tight -13).
    cfg = _cfg()
    object.__setattr__(cfg, "hard_stop_pct", -13.0)   # simulate prior board-wear corruption
    sensor = _FakeSensor({"timebox": {"n": 3, "wr": 0.9},        # n too thin
                          "pond": {"n": 20, "wr": 0.4}}, {})     # wr too low
    _patch(monkeypatch, tmp_path, sensor)
    monkeypatch.setattr(ch, "_entries_cache", {})
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=time.time())
    assert cfg.hard_stop_pct == -60.0              # runner stop REPAIRED (not -25, not held -13)
    assert cfg.time_stop_minutes == 240.0
    assert cfg.tp1_pct == 20.0
    assert any(c[0] == "liquidity_usd" and c[2] == 15000.0 for c in cfg.entry_gate)  # 15k floor matches timebox_mcap
    st = json.load(open(str(tmp_path / "tune.json")))
    assert st["meta_chameleon"]["archetype"] == ch.GREEN_ARCHETYPE
    ok, why = ch.entries_allowed("meta_chameleon", now=time.time())
    assert ok and "green-momentum" in why


def test_same_archetype_fresher_numbers_hold(monkeypatch, tmp_path):
    cfg = _cfg()
    sensor = _FakeSensor({"timebox": {"n": 12, "wr": 0.75}}, {"timebox": GEO})
    _patch(monkeypatch, tmp_path, sensor)
    now = time.time()
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=now)
    assert cfg.tp1_pct == 35.0
    # same archetype, jittered geometry 90min later — must NOT churn
    sensor._geo = {"timebox": dict(GEO, med_win_pct=12.0)}
    monkeypatch.setattr(ch, "_last_check", 0.0)
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=now + 5400)
    assert cfg.tp1_pct == 35.0


def test_deterioration_triggers_fast_switch(monkeypatch, tmp_path):
    cfg = _cfg()
    sensor = _FakeSensor({"timebox": {"n": 12, "wr": 0.75}}, {"timebox": GEO})
    _patch(monkeypatch, tmp_path, sensor)
    now = time.time()
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=now)
    assert cfg.tp1_pct == 35.0
    # 2h later (inside the 6h soft cadence): timebox COLLAPSES (wr 0.30),
    # surgical qualifies -> deterioration rule fires immediately
    surg = dict(GEO, med_win_pct=15.0, p75_hold_secs=1200)
    sensor._board = {"timebox": {"n": 10, "wr": 0.30},
                     "surgical": {"n": 10, "wr": 0.70}}
    sensor._geo = {"timebox": dict(GEO, wr=0.30),
                   "surgical": surg}
    monkeypatch.setattr(ch, "_last_check", 0.0)
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=now + 7200)
    assert cfg.tp1_pct == 15.0                     # switched to surgical
    assert cfg.time_stop_minutes == 20.0


def test_challenger_domination_beats_soft_cadence(monkeypatch, tmp_path):
    cfg = _cfg()
    sensor = _FakeSensor({"timebox": {"n": 12, "wr": 0.62}}, {"timebox": GEO})
    _patch(monkeypatch, tmp_path, sensor)
    now = time.time()
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=now)
    # 2h later: timebox still healthy (0.62) but surgical runs 0.85 on n=14
    surg = dict(GEO, wr=0.85, n=14, med_win_pct=18.0)
    sensor._board = {"timebox": {"n": 12, "wr": 0.62},
                     "surgical": {"n": 14, "wr": 0.85}}
    sensor._geo = {"timebox": dict(GEO, wr=0.62), "surgical": surg}
    monkeypatch.setattr(ch, "_last_check", 0.0)
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=now + 7200)
    assert cfg.tp1_pct == 18.0                     # challenger took over


def test_retune_floor_blocks_thrash(monkeypatch, tmp_path):
    cfg = _cfg()
    sensor = _FakeSensor({"timebox": {"n": 12, "wr": 0.75}}, {"timebox": GEO})
    _patch(monkeypatch, tmp_path, sensor)
    now = time.time()
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=now)
    # 30min later even a collapsing current archetype cannot retune (floor)
    sensor._board = {"timebox": {"n": 10, "wr": 0.30},
                     "surgical": {"n": 10, "wr": 0.70}}
    sensor._geo = {"timebox": dict(GEO, wr=0.30),
                   "surgical": dict(GEO, med_win_pct=15.0)}
    monkeypatch.setattr(ch, "_last_check", 0.0)
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=now + 1800)
    assert cfg.tp1_pct == 35.0


def test_unlabeled_and_single_wallet_archetypes_rejected(monkeypatch, tmp_path):
    cfg = _cfg()
    solo = dict(GEO, n_wallets=1, top_wallet_share=1.0)
    sensor = _FakeSensor({"unlabeled": {"n": 30, "wr": 0.9},
                          "surgical": {"n": 12, "wr": 0.8}},
                         {"unlabeled": GEO, "surgical": solo})
    _patch(monkeypatch, tmp_path, sensor)
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=time.time())
    assert cfg.time_stop_minutes == 240.0   # neither identity-coherent signal


def test_pending_force_applies_after_max_age(monkeypatch, tmp_path):
    cfg = _cfg()
    sensor = _FakeSensor({"surgical": {"n": 10, "wr": 0.8}}, {"surgical": GEO})
    _patch(monkeypatch, tmp_path, sensor)
    now = time.time()
    pm = _pm(cfg, open_positions=3)         # book never goes flat
    ch.maybe_retune(_scanner(pm), now=now)  # queued
    assert cfg.time_stop_minutes == 240.0
    monkeypatch.setattr(ch, "_last_check", 0.0)
    ch.maybe_retune(_scanner(pm), now=now + ch.PENDING_FORCE_SECS + 60)
    assert cfg.time_stop_minutes == 90.0    # force-applied despite open book


def test_boot_overlay_reapplies(monkeypatch, tmp_path):
    monkeypatch.setattr(ch, "_TUNE_FILE", str(tmp_path / "tune.json"))
    json.dump({"meta_chameleon": {"tune": {"time_stop_minutes": 55.0,
                                           "tp1_pct": 14.0,
                                           "hard_stop_pct": -22.0},
                                  "archetype": "surgical"}},
              open(str(tmp_path / "tune.json"), "w"))
    cfg = _cfg()
    ch.apply_overlay(cfg)
    assert (cfg.time_stop_minutes, cfg.tp1_pct, cfg.hard_stop_pct) == (55.0, 14.0, -22.0)


def test_pond_age_band_tunes_with_coverage():
    geo = dict(GEO, med_age_h=2.0, p75_age_h=10.0, age_coverage=0.8)
    t = ch.tune_from_geometry(geo)
    assert t["entry_age_max_h"] == 20.0            # 2x p75, inside clamps
    # low coverage -> pond dial NOT tuned (don't steer on a 20% sample)
    geo = dict(GEO, p75_age_h=10.0, age_coverage=0.2)
    assert "entry_age_max_h" not in ch.tune_from_geometry(geo)
    # clamps
    geo = dict(GEO, p75_age_h=500.0, age_coverage=0.9)
    assert ch.tune_from_geometry(geo)["entry_age_max_h"] == 168.0


def test_apply_rebuilds_entry_gate_preserving_other_conditions():
    cfg = _cfg()
    ch._apply(cfg, {"entry_age_max_h": 48.0})
    gate = [list(c) for c in cfg.entry_gate]
    assert ["entry_age_hours", "<=", 48.0] in gate
    feats = [c[0] for c in gate]
    assert "wash_suspected" in feats and "liquidity_usd" in feats
    assert feats.count("entry_age_hours") == 1


def test_slow_style_qualifies_from_24h_window(monkeypatch, tmp_path):
    cfg = _cfg()
    swing = dict(GEO, med_hold_secs=6 * 3600, p75_hold_secs=10 * 3600,
                 med_win_pct=25.0)

    class _DualSensor(_FakeSensor):
        def scoreboard(self, now=None):
            return {"windows": {"6h": {},                       # closes too slow
                                "24h": {"swing": {"n": 10, "wr": 0.8}}}}

        def archetype_geometry(self, arch, now=None, window_secs=21600, min_n=8):
            if arch == "swing" and window_secs >= 24 * 3600:
                return swing
            return None

    _patch(monkeypatch, tmp_path, _DualSensor({}, {}))
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=time.time())
    assert cfg.time_stop_minutes == 600.0          # p75 10h
    assert cfg.tp1_pct == 25.0


def test_standby_gate_blocks_until_first_meta(monkeypatch, tmp_path):
    sensor = _FakeSensor({}, {})
    _patch(monkeypatch, tmp_path, sensor)
    monkeypatch.setattr(ch, "_entries_cache", {})
    ok, why = ch.entries_allowed("meta_chameleon")
    assert not ok and "no meta worn" in why


def test_standby_gate_allows_while_meta_alive_hysteresis(monkeypatch, tmp_path):
    cfg = _cfg()
    sensor = _FakeSensor({"surgical": {"n": 10, "wr": 0.8}}, {"surgical": GEO})
    _patch(monkeypatch, tmp_path, sensor)
    monkeypatch.setattr(ch, "_entries_cache", {})
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=time.time())   # wears surgical
    ok, why = ch.entries_allowed("meta_chameleon")
    assert ok and "surgical" in why
    # board decays to 0.50 — BELOW qualify (0.60) but ABOVE deteriorate (0.45)
    # -> hysteresis keeps entries flowing (no flapping at the qualify line)
    sensor._geo = {"surgical": dict(GEO, wr=0.50)}
    monkeypatch.setattr(ch, "_entries_cache", {})
    ok, _ = ch.entries_allowed("meta_chameleon")
    assert ok
    # decays below deteriorate bar -> STANDBY (via whichever tripwire sees it
    # first — this fake sensor serves the same geo to every window, so the
    # fresh-90min check fires before the 6h deterioration check)
    sensor._geo = {"surgical": dict(GEO, wr=0.30)}
    monkeypatch.setattr(ch, "_entries_cache", {})
    ok, why = ch.entries_allowed("meta_chameleon")
    assert not ok and ("decayed" in why or "fresh-90min" in why)


def test_standby_gate_fail_closed_without_sensor(monkeypatch, tmp_path):
    monkeypatch.setattr(ch, "_TUNE_FILE", str(tmp_path / "tune.json"))
    monkeypatch.setattr(ch, "_entries_cache", {})
    import core.meta_sensor as ms
    monkeypatch.setattr(ms, "_SENSOR", None)
    ok, why = ch.entries_allowed("meta_chameleon")
    assert not ok and "sensor" in why


class _RateSensor(_FakeSensor):
    def __init__(self, board, geo, rate=(5, 5.0), fresh=None):
        super().__init__(board, geo)
        self._rate, self._fresh = rate, fresh

    def buy_rate(self, arch, now=None):
        return self._rate

    def archetype_geometry(self, arch, now=None, window_secs=21600, min_n=8):
        if window_secs == ch.FRESH_WINDOW_SECS:
            return self._fresh
        return self._geo.get(arch)


def _wear_meta(monkeypatch, tmp_path, sensor):
    cfg = _cfg()
    _patch(monkeypatch, tmp_path, sensor)
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=time.time())
    monkeypatch.setattr(ch, "_entries_cache", {})
    return cfg


def test_own_fills_dial_two_of_three(monkeypatch, tmp_path):
    sensor = _RateSensor({"surgical": {"n": 10, "wr": 0.8}}, {"surgical": GEO})
    _wear_meta(monkeypatch, tmp_path, sensor)
    now = time.time()
    ch.record_close("meta_chameleon", "T1", +5.0, True, "surgical")
    ch.record_close("meta_chameleon", "T2", -8.0, True, "surgical")
    ch.record_close("meta_chameleon", "T3", -3.0, True, "surgical")   # 2 of 3 lost
    ok, why = ch.entries_allowed("meta_chameleon", now=now)
    assert not ok and "own-fills" in why
    # pause expires after OWN_FILLS_PAUSE_SECS
    monkeypatch.setattr(ch, "_entries_cache", {})
    ok, _ = ch.entries_allowed("meta_chameleon", now=now + ch.OWN_FILLS_PAUSE_SECS + 1)
    assert ok


def test_own_fills_legs_accumulate_to_position_net(monkeypatch, tmp_path):
    sensor = _RateSensor({"surgical": {"n": 10, "wr": 0.8}}, {"surgical": GEO})
    _wear_meta(monkeypatch, tmp_path, sensor)
    # TP1 +30 leg, then final trail leg -5 -> position NET +25 = a WIN
    ch.record_close("meta_chameleon", "TX", +30.0, False, "surgical")
    ch.record_close("meta_chameleon", "TX", -5.0, True, "surgical")
    st = json.load(open(str(tmp_path / "tune.json")))
    closes = st["meta_chameleon"]["recent_closes"]
    assert closes[-1]["win"] is True and closes[-1]["net"] == 25.0


def test_buy_rate_collapse_blocks(monkeypatch, tmp_path):
    sensor = _RateSensor({"surgical": {"n": 10, "wr": 0.8}}, {"surgical": GEO},
                         rate=(1, 6.0))     # 1 recent vs norm 6/30min
    cfg = _wear_meta(monkeypatch, tmp_path, sensor)
    ok, why = ch.entries_allowed("meta_chameleon")
    assert not ok and "buy-rate collapsed" in why


def test_buy_rate_thin_norm_no_signal(monkeypatch, tmp_path):
    sensor = _RateSensor({"surgical": {"n": 10, "wr": 0.8}}, {"surgical": GEO},
                         rate=(0, 1.0))     # norm below BUYRATE_MIN_NORM
    _wear_meta(monkeypatch, tmp_path, sensor)
    ok, _ = ch.entries_allowed("meta_chameleon")
    assert ok


def test_fresh_window_wr_break_blocks(monkeypatch, tmp_path):
    fresh = dict(GEO, wr=0.20, n=6)
    sensor = _RateSensor({"surgical": {"n": 10, "wr": 0.8}}, {"surgical": GEO},
                         fresh=fresh)
    _wear_meta(monkeypatch, tmp_path, sensor)
    ok, why = ch.entries_allowed("meta_chameleon")
    assert not ok and "fresh-90min" in why


def test_pending_queued_at_preserved_across_requeues(monkeypatch, tmp_path):
    # bug fix 2026-06-13: a busy book re-queues the same pending each cycle; the
    # original queued_at MUST be preserved so the 2h force-apply accumulates
    # (was reset to ~0 every cycle, silently defeating the backstop).
    cfg = _cfg()
    # worn=conviction (cooled 0.55); surgical dominates (1.0, n>=12); book busy.
    sensor = _RateSensor(
        {"conviction": {"n": 50, "wr": 0.55}, "surgical": {"n": 18, "wr": 1.0}},
        {"conviction": dict(GEO, wr=0.55), "surgical": dict(GEO, wr=1.0)})
    monkeypatch.setattr(ch, "_TUNE_FILE", str(tmp_path / "tune.json"))
    import core.meta_sensor as ms
    monkeypatch.setattr(ms, "_SENSOR", sensor)
    json.dump({"meta_chameleon": {"archetype": "conviction", "tuned_at": 1.0,
                                  "tune": {"time_stop_minutes": 240.0}}},
              open(str(tmp_path / "tune.json"), "w"))
    pm = _pm(cfg, open_positions=2)              # book NOT flat -> queue
    monkeypatch.setattr(ch, "_last_check", 0.0)
    ch.maybe_retune(_scanner(pm), now=10_000.0)
    st = json.load(open(str(tmp_path / "tune.json")))
    qa1 = st["meta_chameleon"]["pending"]["queued_at"]
    assert st["meta_chameleon"]["pending"]["archetype"] == "surgical"
    assert qa1 == 10_000.0
    # re-queue 1000s later (still busy, same challenger) -> queued_at PRESERVED
    monkeypatch.setattr(ch, "_last_check", 0.0)
    ch.maybe_retune(_scanner(pm), now=11_000.0)
    st2 = json.load(open(str(tmp_path / "tune.json")))
    assert st2["meta_chameleon"]["pending"]["queued_at"] == 10_000.0  # NOT reset to 11000


def test_worn_vetoed_no_copyable_falls_to_green_default(monkeypatch, tmp_path):
    # 2026-06-13 watch: worn=thesis_holder, own-fills net-negative (vetoed). The
    # only other board leader is time_boxer, but it's ONE wallet's style
    # (n_wallets=1 -> fails the >=2-wallet consensus). best_qualifying returns None.
    # The veto must still REFUSE to re-wear the bleeder (thesis_holder) — but rather
    # than STAND DOWN idle (the old behavior; AxiS 2026-06-14: "it won't get off its
    # ass and trade"), the chameleon falls to the proven GREEN-MOMENTUM default and
    # keeps trading the winner.
    cfg = _cfg()
    sensor = _RateSensor(
        {"thesis_holder": {"n": 20, "wr": 0.86}, "time_boxer": {"n": 18, "wr": 0.70}},
        {"thesis_holder": dict(GEO, wr=0.86),
         "time_boxer": dict(GEO, wr=0.70, n_wallets=1, top_wallet_share=1.0)})
    monkeypatch.setattr(ch, "_TUNE_FILE", str(tmp_path / "tune.json"))
    import core.meta_sensor as ms
    monkeypatch.setattr(ms, "_SENSOR", sensor)
    closes = [{"ts": 100.0 + i, "win": False, "net": -12.0, "archetype": "thesis_holder"}
              for i in range(6)]  # 6 net-negative thesis_holder copies -> veto fires
    json.dump({"meta_chameleon": {"archetype": "thesis_holder", "tuned_at": 1.0,
                                  "tune": {"time_stop_minutes": 90.0},
                                  "recent_closes": closes}},
              open(str(tmp_path / "tune.json"), "w"))
    monkeypatch.setattr(ch, "_last_check", 0.0)
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=10_000.0)
    st = json.load(open(str(tmp_path / "tune.json")))
    # bleeder REFUSED (veto worked) but NOT idle — runs the proven green default
    assert st["meta_chameleon"]["archetype"] == ch.GREEN_ARCHETYPE
    assert cfg.hard_stop_pct == -60.0                   # proven runner geometry asserted
    # and entries are ALLOWED (active), not standby
    monkeypatch.setattr(ch, "_entries_cache", {})
    ok, why = ch.entries_allowed("meta_chameleon", now=10_050.0)
    assert ok and "green-momentum" in why


def test_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("META_CHAMELEON", "off")
    cfg = _cfg()
    sensor = _FakeSensor({"timebox": {"n": 12, "wr": 0.75}}, {"timebox": GEO})
    _patch(monkeypatch, tmp_path, sensor)
    ch.maybe_retune(_scanner(_pm(cfg, 0)), now=time.time())
    assert cfg.time_stop_minutes == 240.0


def test_hard_to_copy_archetype_needs_higher_bar_and_own_proof(monkeypatch, tmp_path):
    # thesis_holder (hard-to-copy) at 0.70 must NOT qualify (needs 0.75);
    # a copy-friendly archetype (surgical) at 0.65 DOES (bar 0.60).
    cfg = _cfg()
    sensor = _RateSensor(
        {"thesis_holder": {"n": 20, "wr": 0.70}, "surgical": {"n": 12, "wr": 0.65}},
        {"thesis_holder": dict(GEO, wr=0.70), "surgical": dict(GEO, wr=0.65)})
    _patch(monkeypatch, tmp_path, sensor)
    arch, geo = ch.best_qualifying(sensor, now=time.time())
    assert arch == "surgical"        # thesis_holder filtered by the higher bar
    # clearing 0.75 is NOT enough for a hard-to-copy meta: UNPROVEN it's still not
    # explored (the survivorship trap) -> None (maybe_retune runs green-momentum).
    sensor2 = _RateSensor({"thesis_holder": {"n": 20, "wr": 0.78}},
                          {"thesis_holder": dict(GEO, wr=0.78)})
    assert ch.best_qualifying(sensor2, now=time.time(), own_closes=None)[0] is None
    # but a thesis_holder our OWN money has PROVEN positive (>=4 fills, edge>0) IS worn.
    own = [{"archetype": "thesis_holder", "net": 6.0}] * 5
    assert ch.best_qualifying(sensor2, now=time.time(), own_closes=own)[0] == "thesis_holder"


def test_qualify_wr_for_helper():
    assert ch._qualify_wr_for("thesis_holder") == 0.75
    assert ch._qualify_wr_for("conviction") == 0.60
    assert ch._qualify_wr_for("surgical") == 0.60


# ── Own-fill veto (2026-06-13 watch): the chameleon's OWN money vetoes
# re-wearing a survivorship-inflated hard-to-copy archetype it's bleeding on ──
def test_own_fill_vetoed_net_negative():
    """6 net-negative thesis_holder copies -> our money vetoes re-wearing it."""
    rec = {"recent_closes": [
        {"archetype": "thesis_holder", "win": False, "net": -15.0},
        {"archetype": "thesis_holder", "win": False, "net": -16.0},
        {"archetype": "thesis_holder", "win": True,  "net": 8.0},
        {"archetype": "thesis_holder", "win": False, "net": -22.0},
        {"archetype": "thesis_holder", "win": False, "net": -16.0},
        {"archetype": "thesis_holder", "win": False, "net": -13.0},
    ]}
    assert ch._own_fill_vetoed(rec) == frozenset({"thesis_holder"})


def test_own_fill_vetoed_clears_when_net_positive():
    """Net-positive over the window (good tape) -> NOT vetoed (don't ban it)."""
    rec = {"recent_closes": [
        {"archetype": "thesis_holder", "win": True,  "net": 20.0},
        {"archetype": "thesis_holder", "win": True,  "net": 18.0},
        {"archetype": "thesis_holder", "win": False, "net": -10.0},
        {"archetype": "thesis_holder", "win": True,  "net": 15.0},
        {"archetype": "thesis_holder", "win": True,  "net": 11.0},
        {"archetype": "thesis_holder", "win": False, "net": -8.0},
    ]}
    assert ch._own_fill_vetoed(rec) == frozenset()


def test_own_fill_vetoed_needs_min_n():
    """< OWN_FILL_VETO_N closes -> no verdict yet (no veto on thin evidence)."""
    rec = {"recent_closes": [{"archetype": "thesis_holder", "win": False, "net": -15.0}] * 5}
    assert ch._own_fill_vetoed(rec) == frozenset()


def test_own_fill_vetoed_only_hard_to_copy():
    """Copy-friendly archetypes are NOT re-wear-vetoed (the 1h own-fills cooldown
    + standby gate handle their transient losses); the veto targets the
    survivorship-inflated hard-to-copy metas only."""
    rec = {"recent_closes": [{"archetype": "surgical", "win": False, "net": -15.0}] * 8}
    assert ch._own_fill_vetoed(rec) == frozenset()


def test_best_qualifying_respects_veto():
    """A vetoed hard-to-copy archetype is skipped so the search falls through to the
    next-best copy-friendly meta — the thesis_holder doom-loop break. The veto matters
    even for a thesis_holder our own money had PROVEN positive: a fresh bleed-streak
    vetoes re-wearing it (unproven hard-to-copy is never explored anyway)."""
    sensor = _RateSensor(
        {"thesis_holder": {"n": 20, "wr": 0.85}, "surgical": {"n": 12, "wr": 0.65}},
        {"thesis_holder": dict(GEO, wr=0.85), "surgical": dict(GEO, wr=0.65)})
    own = [{"archetype": "thesis_holder", "net": 6.0}] * 5   # proven-positive -> wearable
    # vetoed -> falls to surgical despite thesis_holder's higher board WR + own-proof
    arch, _ = ch.best_qualifying(sensor, now=time.time(), own_closes=own,
                                 veto=frozenset({"thesis_holder"}))
    assert arch == "surgical"
    # un-vetoed -> proven-positive thesis_holder wins
    arch2, _ = ch.best_qualifying(sensor, now=time.time(), own_closes=own)
    assert arch2 == "thesis_holder"


# ── Own-realized-edge selection (2026-06-14, AxiS: "rotate into REAL winners") ──
# Pick the archetype that wins for OUR copy (own realized $/trade), not the board's
# survivorship-inflated WR. Proven-positive (>=4 own-fills, edge>0) ranked by edge;
# proven-negative skipped even at high board WR; unproven explored by board WR; stand
# down when the only candidate is a proven copy-loser.
def test_own_edge_helper():
    closes = [{"archetype": "a", "net": 2.0}, {"archetype": "a", "net": -1.0},
              {"archetype": "b", "net": 5.0}]
    assert ch._own_edge(closes, "a") == (0.5, 2)      # (2 + -1)/2
    assert ch._own_edge(closes, "b") == (5.0, 1)
    assert ch._own_edge(closes, "c") == (None, 0)     # no fills
    assert ch._own_edge(None, "a") == (None, 0)       # no closes


def test_best_qualifying_skips_proven_copy_loser():
    # thesis_holder: high board WR (0.88) but proven own-NEGATIVE -> SKIP the trap;
    # time_boxer: lower board WR but proven own-POSITIVE -> wear it.
    sensor = _FakeSensor(
        {"thesis_holder": {"n": 20, "wr": 0.88}, "time_boxer": {"n": 12, "wr": 0.62}},
        {"thesis_holder": dict(GEO, wr=0.88), "time_boxer": dict(GEO, wr=0.62)})
    own = [{"archetype": "thesis_holder", "net": -5.0}] * 5 + [{"archetype": "time_boxer", "net": 3.0}] * 5
    arch, _ = ch.best_qualifying(sensor, now=time.time(), own_closes=own)
    assert arch == "time_boxer"


def test_best_qualifying_ranks_by_own_edge_not_board_wr():
    # two proven-positive: higher OWN edge wins even with LOWER board WR.
    sensor = _FakeSensor(
        {"surgical": {"n": 12, "wr": 0.80}, "time_boxer": {"n": 12, "wr": 0.62}},
        {"surgical": dict(GEO, wr=0.80), "time_boxer": dict(GEO, wr=0.62)})
    own = [{"archetype": "surgical", "net": 1.0}] * 5 + [{"archetype": "time_boxer", "net": 8.0}] * 5
    arch, _ = ch.best_qualifying(sensor, now=time.time(), own_closes=own)
    assert arch == "time_boxer"   # +8/tr beats surgical's +1/tr despite surgical's higher board WR


def test_best_qualifying_explores_unproven_by_board_wr():
    # nothing proven (<4 own-fills each) -> bootstrap-explore the best board WR.
    sensor = _FakeSensor(
        {"surgical": {"n": 12, "wr": 0.80}, "time_boxer": {"n": 12, "wr": 0.62}},
        {"surgical": dict(GEO, wr=0.80), "time_boxer": dict(GEO, wr=0.62)})
    own = [{"archetype": "surgical", "net": 1.0}]   # only 1 fill -> unproven
    arch, _ = ch.best_qualifying(sensor, now=time.time(), own_closes=own)
    assert arch == "surgical"     # highest board WR (exploration bootstrap)


def test_best_qualifying_never_explores_unproven_hard_to_copy():
    # thesis_holder is HARD_TO_COPY: even UNPROVEN (no own-fills) with the highest board
    # WR, it must NOT be explored (it's a known survivorship trap) -> best_qualifying
    # returns None -> maybe_retune falls to the green-momentum default, not the trap.
    sensor = _FakeSensor(
        {"thesis_holder": {"n": 55, "wr": 0.94}},
        {"thesis_holder": dict(GEO, wr=0.94)})
    arch, _ = ch.best_qualifying(sensor, now=time.time(), own_closes=None)
    assert arch is None


def test_best_qualifying_stands_down_when_only_proven_loser():
    # the ONLY board candidate is a proven copy-loser -> stand down (None), don't wear it.
    sensor = _FakeSensor(
        {"thesis_holder": {"n": 20, "wr": 0.88}},
        {"thesis_holder": dict(GEO, wr=0.88)})
    own = [{"archetype": "thesis_holder", "net": -5.0}] * 5
    arch, _ = ch.best_qualifying(sensor, now=time.time(), own_closes=own)
    assert arch is None


def test_best_qualifying_board_wr_fallback_without_own_closes():
    # backward compat: no own_closes -> rank by board WR (existing behavior, existing tests).
    sensor = _FakeSensor(
        {"surgical": {"n": 12, "wr": 0.80}, "time_boxer": {"n": 12, "wr": 0.62}},
        {"surgical": dict(GEO, wr=0.80), "time_boxer": dict(GEO, wr=0.62)})
    arch, _ = ch.best_qualifying(sensor, now=time.time())
    assert arch == "surgical"
