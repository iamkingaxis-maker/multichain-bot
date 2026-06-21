import os

import math
from core.fast_watch import reprice_change_pct, rt_mode


def test_reprice_identity_when_price_unchanged():
    # P_fresh == P_snap -> fresh_pc == snapshot_pc (inversion fallback property)
    assert reprice_change_pct(-20.0, 0.1521, 0.1521) == -20.0


def test_reprice_recovers_toward_high():
    # Snapshot: price 0.1521 is -20% off the 1h high => ref = 0.1521/0.8 = 0.190125
    # Fresh price 0.1998 => fresh_pc = (0.1998/0.190125 - 1)*100 = +5.09%
    out = reprice_change_pct(-20.0, 0.1521, 0.1998)
    assert math.isclose(out, 5.0855, abs_tol=0.01)


def test_reprice_deeper_dip_when_price_falls_further():
    # Fresh price BELOW snapshot => deeper negative pc
    out = reprice_change_pct(-20.0, 0.1521, 0.1300)
    assert out < -20.0


def test_reprice_none_on_bad_prices():
    assert reprice_change_pct(-20.0, 0.0, 0.1998) is None
    assert reprice_change_pct(-20.0, 0.1521, 0.0) is None
    assert reprice_change_pct(-20.0, 0.1521, -1.0) is None


def test_scan_yield_every_default_is_tight(monkeypatch):
    # The redesign tightens the cooperative-yield default from 8 to 4 so the
    # sync sweep cannot block the loop long enough to starve a ~3s fast tick.
    monkeypatch.delenv("SCAN_YIELD_EVERY", raising=False)
    import feeds.dip_scanner as ds
    # The default is read inline; assert the literal default in source is 4.
    import inspect
    # Scan the module source for the default.
    msrc = inspect.getsource(ds)
    assert 'os.environ.get("SCAN_YIELD_EVERY", "4")' in msrc


def test_rt_mode_env_default(monkeypatch):
    monkeypatch.delenv("RT_TRIGGER_MODE", raising=False)
    assert rt_mode("RT_TRIGGER_MODE") == "off"
    monkeypatch.setenv("RT_TRIGGER_MODE", "shadow")
    assert rt_mode("RT_TRIGGER_MODE") == "shadow"


def test_rt_mode_per_bot_override_wins(monkeypatch):
    monkeypatch.setenv("RT_TRIGGER_MODE", "off")
    # bot config override (dict form) beats the env default
    assert rt_mode("RT_TRIGGER_MODE", {"rt_trigger_mode": "enforce"}) == "enforce"


def test_rt_mode_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("RT_TRIGGER_MODE", "garbage")
    assert rt_mode("RT_TRIGGER_MODE", default="off") == "off"


def test_should_rearm_off():
    from core.fast_watch import should_rearm_this_tick
    assert should_rearm_this_tick("off") is False


def test_should_rearm_shadow_and_enforce():
    from core.fast_watch import should_rearm_this_tick
    assert should_rearm_this_tick("shadow") is True
    assert should_rearm_this_tick("enforce") is True


# ── Task 5: Component C — faithful fresh demand-turn ──────────────────────────
from core.fast_watch import demand_turn_fresh_ok
from feeds.tier3_features import compute_net_flow_windows


def test_demand_turn_fresh_ok_semantics():
    assert demand_turn_fresh_ok(0.4, True) is True
    assert demand_turn_fresh_ok(-0.1, True) is False
    assert demand_turn_fresh_ok(None, True) is None      # missing -> None, never True
    assert demand_turn_fresh_ok(0.4, False) is None      # fetch failed -> None
    assert demand_turn_fresh_ok("x", True) is None


def test_compute_net_flow_windows_15s_imbalance():
    # newest-anchored 15s window; all within 15s of the max ts
    trades = [
        {"kind": "buy",  "volume_usd": 100, "ts": "2026-06-21T00:00:10Z"},
        {"kind": "sell", "volume_usd": 40,  "ts": "2026-06-21T00:00:08Z"},
        {"kind": "buy",  "volume_usd": 60,  "ts": "2026-06-21T00:00:01Z"},
    ]
    out = compute_net_flow_windows(trades)
    assert out["net_flow_15s_usd"] == 120.0          # 100 - 40 + 60
    assert out["net_flow_15s_n"] == 3
    assert out["net_flow_15s_imbalance"] == 0.6      # 120 / 200


def test_rt_demand_turn_mode_default_off(monkeypatch):
    monkeypatch.delenv("RT_DEMAND_TURN_MODE", raising=False)
    assert rt_mode("RT_DEMAND_TURN_MODE") == "off"


# --- Task 6: trigger_source telemetry tag --------------------------------

def test_trigger_source_in_required_fields():
    from core import live_swap_log
    assert "trigger_source" in live_swap_log.REQUIRED_FIELDS


def test_log_live_swap_writes_trigger_source(tmp_path, monkeypatch):
    from core import live_swap_log
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("LIVE_SWAP_LOG_MODE", "on")
    live_swap_log.log_live_swap(side="buy", token_address="X", trigger_source="realtime")
    import json
    line = (tmp_path / "live_swaps.jsonl").read_text().strip().splitlines()[-1]
    assert json.loads(line)["trigger_source"] == "realtime"
