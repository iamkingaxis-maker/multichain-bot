"""SOL SL1 loss-side ladder (2026-07-17 — RH-replay-validated port, n=63,972
paired candidates: mean +0.44-0.66pp/trade, loss-tail p05 -21.6% -> -15.4%).
Mirror of TP1 downward in core PerBotPositionManager: first touch of sl1_pct
pre-TP1 banks sl1_sell_fraction; the tail rides. Default None = byte-identical
for every unconfigured bot (the SOL fleet is unchanged except the 2 racers).
"""
from core.bot_config import BotConfig
from core.per_bot_position_manager import PerBotPositionManager


def _cfg(**ov):
    base = dict(bot_id="b1", display_name="B", tp1_pct=50.0,
                hard_stop_pct=-90.0, pre_stop_bail_pnl_pct=-99.0)
    base.update(ov)
    return BotConfig(**base)


def _open(pm):
    pm.open_position(token="TOK", entry_price=1.0, size_usd=20.0,
                     entry_time=0.0, address="0xtok")
    return pm


def test_sl1_fires_partial_and_latches():
    pm = _open(PerBotPositionManager(_cfg(sl1_pct=-6.0,
                                          sl1_sell_fraction=0.75)))
    d = pm.tick(token="TOK", current_price=0.93, now=120.0,
                vol_m5_usd=5000.0)                       # -7%, high vol
    sl1 = [x for x in d if x.kind == "SL1_DERISK"]
    assert len(sl1) == 1 and sl1[0].sell_fraction == 0.75
    pos = pm.get_position("TOK")
    assert pos.state_blob.get("sl1_fired") is True
    # deeper red later: latched, no re-fire
    d2 = pm.tick(token="TOK", current_price=0.90, now=240.0,
                 vol_m5_usd=5000.0)
    assert not [x for x in d2 if x.kind == "SL1_DERISK"]


def test_sl1_off_by_default_byte_identical():
    pm = _open(PerBotPositionManager(_cfg()))            # no sl1_pct
    d = pm.tick(token="TOK", current_price=0.90, now=120.0,
                vol_m5_usd=5000.0)
    assert not [x for x in d if x.kind == "SL1_DERISK"]


def test_sl1_no_fire_above_threshold_or_post_tp1():
    pm = _open(PerBotPositionManager(_cfg(sl1_pct=-6.0)))
    d = pm.tick(token="TOK", current_price=0.96, now=120.0,
                vol_m5_usd=5000.0)                       # -4%: above line
    assert not [x for x in d if x.kind == "SL1_DERISK"]
    pos = pm.get_position("TOK")
    pos.tp1_hit = True
    d2 = pm.tick(token="TOK", current_price=0.90, now=240.0,
                 vol_m5_usd=5000.0)                      # post-TP1: stands down
    assert not [x for x in d2 if x.kind == "SL1_DERISK"]


def test_hard_stop_still_beats_sl1_on_gap():
    pm = _open(PerBotPositionManager(_cfg(sl1_pct=-6.0, hard_stop_pct=-12.0)))
    d = pm.tick(token="TOK", current_price=0.80, now=120.0,
                vol_m5_usd=5000.0)                       # -20% gap-through
    assert any(x.kind == "HARD_STOP" for x in d)
    assert not [x for x in d if x.kind == "SL1_DERISK"]


def test_racers_configured_fleet_untouched():
    import glob
    import json as j
    on = []
    for f in glob.glob("config/bots/*.json"):
        c = j.load(open(f))
        if c.get("sl1_pct") is not None:
            on.append(c["bot_id"])
    assert sorted(on) == ["admission_x_liq_sl1", "badday_absorb_sl1_ab"]
