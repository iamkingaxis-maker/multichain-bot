# tests/test_rh_paper_lane.py
"""RH paper lane v1 — pure signal logic (no network). The lane mirrors the
Solana young probe: dip trigger + demand turn + retrace-micro + honeypot,
exits via the shared PerBotPositionManager."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from rh_paper_lane import (  # noqa: E402
    price_from_quote, dip_pct, demand_turn, entry_verdict, sell_slice,
    DIP_TRIGGER_PCT, MIN_LIQ_USD, MAX_CONCURRENT, DAILY_LOSS_STOP_USD,
    DEMAND_MIN_BUY_USD,
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

    def test_blocks_accumulate(self):
        v = self._ok(dip=None, demand=False, honeypot_ok=False)
        assert v["enter"] is False and len(v["blocks"]) == 3


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
