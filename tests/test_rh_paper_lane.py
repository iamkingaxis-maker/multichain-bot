# tests/test_rh_paper_lane.py
"""RH paper lane v1 — pure signal logic (no network). The lane mirrors the
Solana young probe: dip trigger + demand turn + retrace-micro + honeypot,
exits via the shared PerBotPositionManager."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from rh_paper_lane import (  # noqa: E402
    price_from_quote, dip_pct, demand_turn, entry_verdict, sell_slice,
    lp_drain_pct,
    DIP_TRIGGER_PCT, MIN_LIQ_USD, MAX_CONCURRENT, DAILY_LOSS_STOP_USD,
    DEMAND_MIN_BUY_USD, MIN_POOL_AGE_H, LP_DRAIN_ENTRY_PCT,
)

NOW = 1_000_000.0


def _row(kind, usd, dt):
    return {"kind": kind, "volume_usd": usd, "_epoch": NOW + dt}


class TestPriceFromQuote:
    def test_basic(self):
        # 1 ETH in -> 4000 tokens (18 dec) => 0.00025 ETH/token
        px = price_from_quote(10 ** 18, 4000 * 10 ** 18, 18)
        assert abs(px - 0.00025) < 1e-12

    def test_six_decimals(self):
        px = price_from_quote(10 ** 18, 4000 * 10 ** 6, 6)
        assert abs(px - 0.00025) < 1e-12

    def test_empty_quote_zero(self):
        assert price_from_quote(0, 100, 18) == 0.0
        assert price_from_quote(100, 0, 18) == 0.0
        assert price_from_quote(100, 100, None) == 0.0


class TestDipPct:
    def test_dip_from_window_high(self):
        s = [(NOW - 300, 1.0), (NOW - 200, 1.25), (NOW - 10, 1.0)]
        assert abs(dip_pct(s, NOW) - (-20.0)) < 1e-9

    def test_thin_series_none(self):
        assert dip_pct([(NOW - 10, 1.0)], NOW) is None
        assert dip_pct([], NOW) is None

    def test_stale_points_excluded(self):
        # the high is outside the window -> only 2 in-window points -> None
        s = [(NOW - 9000, 5.0), (NOW - 60, 1.0), (NOW - 10, 0.9)]
        assert dip_pct(s, NOW, window_s=600) is None

    def test_flat_no_dip(self):
        s = [(NOW - 60, 1.0), (NOW - 30, 1.0), (NOW - 5, 1.0)]
        assert abs(dip_pct(s, NOW)) < 1e-9


class TestDemandTurn:
    def test_net_inflow_confirms(self):
        rows = [_row("buy", 80, -20), _row("buy", 40, -10), _row("sell", 30, -5)]
        assert demand_turn(rows, NOW) is True

    def test_net_outflow_rejects(self):
        rows = [_row("buy", 60, -20), _row("sell", 200, -5)]
        assert demand_turn(rows, NOW) is False

    def test_tiny_buys_reject_even_if_positive(self):
        rows = [_row("buy", DEMAND_MIN_BUY_USD - 1, -10)]
        assert demand_turn(rows, NOW) is False

    def test_old_rows_ignored(self):
        rows = [_row("buy", 500, -300)]  # outside 30s window
        assert demand_turn(rows, NOW) is False


class TestEntryVerdict:
    def _ok(self, **kw):
        base = dict(dip=DIP_TRIGGER_PCT - 1, demand=True,
                    micro={"avoid_block": False}, liq_usd=MIN_LIQ_USD + 1,
                    honeypot_ok=True, open_count=0, cooldown_ok=True,
                    daily_pnl_usd=0.0)
        base.update(kw)
        return entry_verdict(**base)

    def test_all_gates_pass(self):
        v = self._ok()
        assert v["enter"] is True and v["blocks"] == []

    def test_each_gate_blocks(self):
        assert "no_dip" in self._ok(dip=None)["blocks"]
        assert "no_dip" in self._ok(dip=-5.0)["blocks"]
        assert "no_demand_turn" in self._ok(demand=False)["blocks"]
        assert "retrace_micro_avoid" in self._ok(
            micro={"avoid_block": True})["blocks"]
        assert "liq_floor" in self._ok(liq_usd=MIN_LIQ_USD - 1)["blocks"]
        assert "honeypot" in self._ok(honeypot_ok=False)["blocks"]
        assert "max_concurrent" in self._ok(
            open_count=MAX_CONCURRENT)["blocks"]
        assert "cooldown" in self._ok(cooldown_ok=False)["blocks"]
        assert "daily_loss_stop" in self._ok(
            daily_pnl_usd=DAILY_LOSS_STOP_USD)["blocks"]

    def test_rug_guard_gates(self):
        # pool-age floor (rug-guard port)
        assert "age_floor" in self._ok(age_h=MIN_POOL_AGE_H - 0.1)["blocks"]
        assert self._ok(age_h=MIN_POOL_AGE_H + 0.1)["enter"] is True
        assert self._ok(age_h=None)["enter"] is True     # unknown age: no signal
        # lp-drain entry veto
        assert "lp_drain" in self._ok(drain_pct=LP_DRAIN_ENTRY_PCT - 1)["blocks"]
        assert self._ok(drain_pct=-5.0)["enter"] is True  # mild wobble ok
        assert self._ok(drain_pct=None)["enter"] is True  # no series yet

    def test_blocks_accumulate(self):
        v = self._ok(dip=None, demand=False, honeypot_ok=False)
        assert v["enter"] is False and len(v["blocks"]) == 3


class TestLpDrainPct:
    """Keyless LP-drain signal: pct off the 15-min liq high (mirrors the
    Solana lp_delta_15m_pct that flagged every doomed CLOPY-class entry)."""

    def test_drain_measured_from_window_high(self):
        s = [(NOW - 600, 40000.0), (NOW - 300, 42000.0), (NOW - 10, 21000.0)]
        assert abs(lp_drain_pct(s, NOW) - (-50.0)) < 0.01

    def test_stable_liq_no_drain(self):
        s = [(NOW - 300, 30000.0), (NOW - 10, 30000.0)]
        assert abs(lp_drain_pct(s, NOW)) < 1e-9

    def test_thin_series_none(self):
        assert lp_drain_pct([(NOW - 10, 30000.0)], NOW) is None
        assert lp_drain_pct([], NOW) is None

    def test_old_samples_excluded(self):
        s = [(NOW - 5000, 100000.0), (NOW - 200, 30000.0), (NOW - 10, 29000.0)]
        # the 100k sample is outside the window -> only ~3% drain
        assert lp_drain_pct(s, NOW) > -5.0


class TestSellSlice:
    """Regression: exit-engine sell_fraction = fraction of ORIGINAL, clamped
    to remaining. Cost basis on the requested (unclamped) fraction produced
    the BILLY -75% phantom loss (2026-07-10): trail asked 1.0 with only 0.25
    left, ledger booked $25 cost against a $6 slice."""

    def test_full_from_full(self):
        assert sell_slice(1.0, 1.0) == (1.0, 0.0)

    def test_tp1_partial(self):
        f, rem = sell_slice(1.0, 0.75)
        assert abs(f - 0.75) < 1e-12 and abs(rem - 0.25) < 1e-12

    def test_billy_case_trail_after_tp1(self):
        f, rem = sell_slice(0.25, 1.0)   # asked full, only 25% left
        assert abs(f - 0.25) < 1e-12 and rem == 0.0

    def test_nothing_left(self):
        assert sell_slice(0.0, 1.0) == (0.0, 0.0)


class TestTokenResolution:
    """Regression: feed.watch entries carry NO token key (candidate dict is
    popped at promotion) — the lane MUST resolve tokens from the firehose
    registry or it silently never quotes (quotes=0/evals=0 bug, 2026-07-10)."""

    def _lane(self, registry):
        from rh_paper_lane import PaperLane

        class FakeFeed:
            watch = {"0xp1": {"sym": "T", "liq": 20000.0}}  # no token key!
        return PaperLane(FakeFeed(), executor=object(), registry=registry)

    def test_registry_resolves(self):
        lane = self._lane({"0xp1": {"token": "0xtok", "fee": 3000}})
        assert lane._token_for("0xp1") == "0xtok"

    def test_watch_alone_yields_none(self):
        lane = self._lane({})
        assert lane._token_for("0xp1") is None

    def test_position_meta_fallback(self):
        lane = self._lane({})
        lane.pos_meta["0xp1"] = {"token": "0xheld"}
        assert lane._token_for("0xp1") == "0xheld"


class TestQuotePriority:
    """Exit-blindness regression (trail-width analysis 2026-07-10): open
    positions must be quoted FIRST and outside the entry-candidate budget —
    LOCKIN gapped through its trail because a quiet position got crowded out
    of the shared quote budget during busy ticks."""

    def test_positions_precede_hot_and_survive_budget(self, monkeypatch):
        import rh_paper_lane as mod

        class FakeFeed:
            watch = {}
            eth_price = 2000.0
        lane = mod.PaperLane(FakeFeed(), executor=object(), registry={})
        now = 1_000_000.0
        # 10 loud entry candidates + 1 quiet open position
        for i in range(10):
            p = f"0xhot{i}"
            lane.last_trade[p] = now - i
            FakeFeed.watch[p] = {"sym": f"H{i}", "liq": 50000.0}
        lane.pos_meta["0xquiet"] = {"qty_orig": 1.0, "remaining_frac": 1.0,
                                    "token": "0xtok", "sym": "Q",
                                    "entry_px": 1.0, "entry_ts": now - 900}
        # no recent trade for 0xquiet, and it's NOT in feed.watch
        quoted = []
        monkeypatch.setattr(lane, "_token_for",
                            lambda pool: quoted.append(pool) or None)
        lane._quote_hot(now)
        assert quoted[0] == "0xquiet"                 # position first
        assert len(quoted) <= mod.MAX_HOT_QUOTES      # budget respected overall


class TestStatePersistence:
    """Open paper positions must survive restarts (parity with the Solana
    bot_state stores — a crash mid-hold must never orphan a position)."""

    def _lane(self, tmp_path, monkeypatch):
        import rh_paper_lane as mod
        monkeypatch.setattr(mod, "STATE", str(tmp_path / "state.json"))

        class FakeFeed:
            watch = {}
        return mod.PaperLane(FakeFeed(), executor=object(), registry={})

    def test_roundtrip_open_position(self, tmp_path, monkeypatch):
        import rh_paper_lane as mod
        lane = self._lane(tmp_path, monkeypatch)
        lane.pm.open_position(token="0xp1", entry_price=1e-8, size_usd=25.0,
                              entry_time=100.0, address="0xtok")
        lane.pos_meta["0xp1"] = {"qty_orig": 5000.0, "remaining_frac": 1.0,
                                 "token": "0xtok", "sym": "T",
                                 "entry_px": 1e-8, "entry_ts": 100.0}
        lane.daily_pnl_usd = -3.5
        lane.save_state()
        lane2 = self._lane(tmp_path, monkeypatch)
        lane2.restore_state()
        assert "0xp1" in lane2.pos_meta
        assert lane2.pos_meta["0xp1"]["qty_orig"] == 5000.0
        assert lane2.pm.get_position("0xp1") is not None
        assert abs(lane2.daily_pnl_usd - (-3.5)) < 1e-9

    def test_no_state_file_clean_start(self, tmp_path, monkeypatch):
        lane = self._lane(tmp_path, monkeypatch)
        lane.restore_state()
        assert lane.pos_meta == {} and lane.daily_pnl_usd == 0.0


class TestRunnerScoreStamp:
    """Shadow stamp (2026-07-10 monster-vs-regular decode): every SELL ledger
    row carries runner_score/runner_reasons computed from the pool's live
    tape. Shadow only — no decision reads it."""

    def _lane(self, tmp_path, monkeypatch):
        import rh_paper_lane as mod
        monkeypatch.setattr(mod, "STATE", str(tmp_path / "state.json"))
        monkeypatch.setattr(mod, "LEDGER", str(tmp_path / "ledger.jsonl"))
        monkeypatch.setattr(mod, "POSTEXIT_PENDING", str(tmp_path / "pe.jsonl"))

        class FakeQuote:
            amount_out = int(0.005 * 1e18)

        class FakeExec:
            def quote_sell(self, token, qty):
                return FakeQuote()

            def token_decimals(self, token):
                return 18

        class FakeFeed:
            watch = {}
            eth_price = 2000.0
        return mod.PaperLane(FakeFeed(), executor=FakeExec(), registry={})

    def _sell(self, lane, pool, now, tape_rows):
        from types import SimpleNamespace
        lane.pm.open_position(token=pool, entry_price=1e-8, size_usd=25.0,
                              entry_time=now - 1200, address="0xtok")
        meta = {"qty_orig": 1000.0, "remaining_frac": 1.0, "token": "0xtok",
                "sym": "T", "entry_px": 1e-8, "entry_ts": now - 1200}
        lane.pos_meta[pool] = meta
        lane.tape[pool] = tape_rows
        lane._paper_sell(pool, meta, SimpleNamespace(
            kind="TP1", sell_fraction=0.75, reason="tp1"), now)

    def _sell_rows(self, tmp_path):
        import json
        out = []
        with open(tmp_path / "ledger.jsonl", encoding="utf-8") as f:
            for line in f:
                r = json.loads(line)
                if r.get("ev") == "sell":
                    out.append(r)
        return out

    def test_sell_row_carries_runner_score(self, tmp_path, monkeypatch):
        lane = self._lane(tmp_path, monkeypatch)
        now = 1_000_000.0
        # monster-shaped fake tape: pre-run baseline + accelerating upsized
        # fresh-maker buys in the last 10 min (firehose row shape: maker
        # SURVIVES into the buffer via on_row -> _drain)
        rows = [{"kind": "buy", "volume_usd": 20.0, "maker": f"p{i}",
                 "_epoch": now - 620 - 20 * i} for i in range(20)]
        rows += [{"kind": "buy", "volume_usd": 30.0, "maker": f"p{i}",
                  "_epoch": now - 580 + 25 * i} for i in range(10)]
        rows += [{"kind": "buy", "volume_usd": 40.0, "maker": f"m{i}",
                  "_epoch": now - 290 + 14 * i} for i in range(20)]
        self._sell(lane, "0xpool", now, rows)
        sells = self._sell_rows(tmp_path)
        assert len(sells) == 1
        assert sells[0]["runner_score"] is not None
        assert 0.0 <= sells[0]["runner_score"] <= 1.0
        assert sells[0]["runner_score"] >= 0.9          # monster shape
        assert isinstance(sells[0]["runner_reasons"], dict)
        assert set(sells[0]["runner_reasons"]["subs"]) == {
            "flow", "accel", "size", "fresh"}

    def test_thin_tape_stamps_none_not_zero(self, tmp_path, monkeypatch):
        lane = self._lane(tmp_path, monkeypatch)
        now = 1_000_000.0
        rows = [{"kind": "buy", "volume_usd": 20.0, "maker": "m",
                 "_epoch": now - 100}]                   # 1 trade = unreadable
        self._sell(lane, "0xpool", now, rows)
        sells = self._sell_rows(tmp_path)
        assert sells[0]["runner_score"] is None          # never 0
        assert sells[0]["runner_reasons"]["reason"] == "thin_tape"

    def test_no_tape_fails_open(self, tmp_path, monkeypatch):
        lane = self._lane(tmp_path, monkeypatch)
        self._sell(lane, "0xpool", 1_000_000.0, [])
        sells = self._sell_rows(tmp_path)
        assert sells[0]["runner_score"] is None
        assert sells[0]["pnl_usd"] is not None           # sell itself booked


class TestExitEngineParity:
    """The lane must use the probe's exit semantics (shared engine)."""

    def test_tp1_partial_then_tp2(self):
        from core.bot_config import BotConfig
        from core.per_bot_position_manager import PerBotPositionManager
        cfg = BotConfig(bot_id="rh_paper_young", display_name="t",
                        tp1_pct=6.0, tp1_sell_fraction=0.75, tp2_pct=12.0)
        pm = PerBotPositionManager(cfg)
        pm.open_position(token="P", entry_price=1.0, size_usd=25.0,
                         entry_time=0.0, address="tok")
        d1 = pm.tick(token="P", current_price=1.07, now=10.0, vol_m5_usd=1000)
        tp1 = [x for x in d1 if x.kind == "TP1"]
        assert tp1 and abs(tp1[0].sell_fraction - 0.75) < 1e-9
        pm.close_position("P", exit_price=1.07, exit_time=10.0,
                          reason="tp1", sell_fraction=0.75)
        d2 = pm.tick(token="P", current_price=1.13, now=20.0, vol_m5_usd=1000)
        assert any(x.kind == "TP2" for x in d2)
