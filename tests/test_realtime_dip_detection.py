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
    # Tightened 8 -> 4 -> 1 (2026-07-08 loop-unstarve): with read_chart no longer
    # a single long callback, yield after EVERY token so the per-token tails cannot
    # stack and starve the ~3s fast tick.
    monkeypatch.delenv("SCAN_YIELD_EVERY", raising=False)
    import feeds.dip_scanner as ds
    # The default is read inline; assert the literal default in source is 1.
    import inspect
    # Scan the module source for the default.
    msrc = inspect.getsource(ds)
    assert 'os.environ.get("SCAN_YIELD_EVERY", "1")' in msrc


def test_read_chart_yields_between_phases():
    # LOOP-UNSTARVE (2026-07-08): read_chart is async but its only await is skipped
    # when pre-fetched chart_data is passed, so its pure-Python analysis phases must
    # yield between themselves or one call blocks the loop (was the 48s stall). Guard
    # the cooperative yields against silent removal.
    import inspect
    import feeds.chart_reader as cr
    src = inspect.getsource(cr.read_chart)
    assert src.count("await asyncio.sleep(0)") >= 5, \
        "read_chart lost its between-phase cooperative yields (loop-unstarve regression)"


def test_fetch_candidates_offloads_discovery_parse():
    # LOOP-UNSTARVE (2026-07-08): the large gzipped discovery/enrich responses must
    # NOT be decompressed+parsed on the event loop (was the ~9s _read_ready block).
    # identity encoding kills on-loop decompress; orjson-in-a-thread frees the parse.
    import inspect
    import feeds.dip_scanner as ds
    src = inspect.getsource(ds.DipScanner._fetch_candidates)
    assert '"identity"' in src, "discovery fetch lost Accept-Encoding: identity"
    assert "to_thread(_fast_loads" in src, "discovery parse no longer offloaded to a thread"


def test_h24_history_save_is_offloaded():
    # LOOP-UNSTARVE (2026-07-08): the once-per-cycle history dump must serialize+write
    # off-loop (was a synchronous json.dump of a large dict on the loop).
    import inspect
    import feeds.dip_scanner as ds
    src = inspect.getsource(ds.DipScanner._save_h24_history)
    assert "async def" in src, "_save_h24_history must be async to offload its write"
    assert "to_thread" in src, "_save_h24_history no longer offloads serialize+write"


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


# --- Task 7: A/B bot configs --------------------------------------------
# NOTE deviation from plan: the control (nf15_live) is currently NEUTRALIZED
# (live_probe=false) — the sole live bot is badday_fill_probe_live ($5). And
# PAPER_MODE is currently false (live). So the treatment is created DORMANT
# (enabled=false): activating it is the GATED live step (needs AxiS go), to
# avoid a second live bot on deploy. Tests assert that safety, not the plan's
# original "control live_probe=true" (which would itself be a gated change).

def _load_bot_cfg(name):
    import json
    import os
    p = os.path.join("config", "bots", name)
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def test_control_bot_is_legacy_trigger():
    cfg = _load_bot_cfg("badday_flush_nf15_live.json")
    assert cfg.get("rt_trigger_mode", "off") == "off"
    assert cfg.get("rt_arm_mode", "off") == "off"
    assert cfg.get("rt_demand_turn_mode", "off") == "off"


def test_treatment_bot_is_realtime_capped_and_dormant():
    cfg = _load_bot_cfg("badday_flush_nf15_rt_live.json")
    assert cfg["rt_trigger_mode"] == "enforce"
    assert cfg["rt_arm_mode"] == "enforce"
    assert cfg["rt_demand_turn_mode"] == "enforce"
    # capped
    assert cfg["daily_loss_limit_usd"] <= 60
    # flat sizing, no conviction leverage (all multipliers neutral)
    assert cfg.get("alpha_multiplier", 1.0) == 1.0
    assert cfg.get("marginal_multiplier", 1.0) == 1.0
    # SAFETY: dormant until the gated live step (no 2nd live bot on deploy)
    assert cfg["enabled"] is False
    assert cfg["live_probe"] is True  # ready for the gated step


def test_treatment_matches_control_entry_gate():
    # The A/B isolates the TRIGGER only: entry gate + sizing must be identical.
    a = _load_bot_cfg("badday_flush_nf15_live.json")
    b = _load_bot_cfg("badday_flush_nf15_rt_live.json")
    assert a["entry_gate"] == b["entry_gate"]
    assert a["base_position_usd"] == b["base_position_usd"]
    assert a["hard_stop_pct"] == b["hard_stop_pct"]
