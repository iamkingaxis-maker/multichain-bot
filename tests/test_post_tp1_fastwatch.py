# tests/test_post_tp1_fastwatch.py
"""POST-TP1 FAST-WATCH (2026-07-12; family re-mine 2026-07-01 item #2).

After TP1 banks the first slice, the remainder's exits (trail/TP2/stop) ran only
on the slow ~150s sweep — 393 trail closers booked peak-7.29pp vs the peak-2pp
config (3.42pp median fired-below-line = scan-cadence latency, ~+300-450
token-pp/8.5d pool). The fast tick now enrolls every open post-TP1 position and
re-runs the position's OWN pm.tick() exit rules on the freshest samples — no new
exit rules, purely a cadence/freshness upgrade, fires through the same
_execute_bot_sell with an exit_cadence="fastwatch" stamp.

Covered here: enrollment on TP1, eviction (close/TTL), cap FIFO, confirm-tick
wick guard (incl. the TP2 tier-flag burn guard), fail-open, kill switch, the
conservative peak ratchet, and the cadence stamp plumbing.
"""
import asyncio
import inspect
import types

import pytest

from core.bot_config import BotConfig
from core.fast_watch import (
    confirmed_peak_ratchet,
    post_tp1_fastwatch_confirm_ticks,
    post_tp1_fastwatch_enabled,
    post_tp1_fastwatch_max,
    post_tp1_fastwatch_ttl_secs,
    select_post_tp1_watches,
)
from core.per_bot_position_manager import PerBotPositionManager
from feeds.dip_scanner import DipScanner


MINT = "So11111111111111111111111111111111111111112"


# ── pure helpers ─────────────────────────────────────────────────────────────

class TestEnvHelpers:
    def test_enabled_default_on(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        assert post_tp1_fastwatch_enabled() is True

    @pytest.mark.parametrize("val", ["off", "0", "false", "no", " OFF "])
    def test_kill_switch_values(self, monkeypatch, val):
        monkeypatch.setenv("POST_TP1_FASTWATCH", val)
        assert post_tp1_fastwatch_enabled() is False

    def test_max_default_and_floor(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH_MAX", raising=False)
        assert post_tp1_fastwatch_max() == 10
        monkeypatch.setenv("POST_TP1_FASTWATCH_MAX", "0")
        assert post_tp1_fastwatch_max() == 1     # floor 1
        monkeypatch.setenv("POST_TP1_FASTWATCH_MAX", "garbage")
        assert post_tp1_fastwatch_max() == 10    # bad value -> default

    def test_ttl_default_lifetime(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH_TTL_SECS", raising=False)
        assert post_tp1_fastwatch_ttl_secs() == 0.0   # 0 = position lifetime
        monkeypatch.setenv("POST_TP1_FASTWATCH_TTL_SECS", "-5")
        assert post_tp1_fastwatch_ttl_secs() == 0.0

    def test_confirm_ticks_default(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH_CONFIRM_TICKS", raising=False)
        assert post_tp1_fastwatch_confirm_ticks() == 2


class TestSelectWatches:
    def test_fifo_cap(self):
        cands = [("c", 300.0), ("a", 100.0), ("b", 200.0)]
        kept, evicted = select_post_tp1_watches(cands, 2, 0.0, now=1000.0)
        assert kept == ["a", "b"] and evicted == []

    def test_ttl_evicts_old_watch(self):
        cands = [("old", 100.0), ("new", 950.0)]
        kept, evicted = select_post_tp1_watches(cands, 10, 60.0, now=1000.0)
        assert kept == ["new"] and evicted == ["old"]

    def test_ttl_zero_means_lifetime(self):
        cands = [("old", 100.0)]
        kept, evicted = select_post_tp1_watches(cands, 10, 0.0, now=1e9)
        assert kept == ["old"] and evicted == []

    def test_bad_ts_treated_as_now_never_raises(self):
        kept, evicted = select_post_tp1_watches(
            [("x", None), ("y", "bad")], 10, 60.0, now=1000.0)
        assert set(kept) == {"x", "y"} and evicted == []

    def test_empty(self):
        assert select_post_tp1_watches([], 10, 0.0, 0.0) == ([], [])


class TestConfirmedPeakRatchet:
    def test_both_above_ratchets_to_min(self):
        # entry 1.0, recorded peak +10; newest 2 fresh = +14, +16 -> peak +14
        got = confirmed_peak_ratchet([1.02, 1.14, 1.16], 1.0, 10.0, 2)
        assert got == pytest.approx(14.0)

    def test_single_glitch_high_does_not_ratchet(self):
        # only the newest print is above the peak — a glitch can't inflate it
        assert confirmed_peak_ratchet([1.02, 1.05, 1.30], 1.0, 10.0, 2) is None

    def test_below_peak_no_update(self):
        assert confirmed_peak_ratchet([1.05, 1.06], 1.0, 10.0, 2) is None

    def test_bad_data_fail_safe(self):
        assert confirmed_peak_ratchet([], 1.0, 10.0, 2) is None
        assert confirmed_peak_ratchet([1.1, 1.2], 0.0, 10.0, 2) is None
        assert confirmed_peak_ratchet(None, "x", None, 2) is None


# ── scanner-hook harness ─────────────────────────────────────────────────────

def _mk_pm(bot_id="badday_young_absorb", token="TOK", entry=1.0, peak=10.0,
           tp1_hit=True, **cfg_over):
    cfg = BotConfig(bot_id=bot_id, display_name=bot_id, tp1_pct=6.0,
                    tp1_sell_fraction=0.75, tp2_pct=15.0, trail_pp=2.0,
                    hard_stop_pct=-12.0, **cfg_over)
    pm = PerBotPositionManager(cfg)
    p = pm.open_position(token=token, entry_price=entry, size_usd=25.0,
                         entry_time=900.0, address=MINT)
    p.tp1_hit = tp1_hit
    p.peak_pnl_pct = peak
    return pm, p


def _mk_stub(pms, samples_by_addr, batch_result=None, batch_raises=False):
    """Duck-typed DipScanner stand-in for _post_tp1_fastwatch."""
    from collections import deque
    stub = types.SimpleNamespace()
    stub.bot_position_managers = pms
    stub._fast_samples = {a: deque(s, maxlen=20) for a, s in samples_by_addr.items()}
    stub.sells = []

    async def _sell(bot_id, token, decision, price, now, exit_cadence="main"):
        stub.sells.append((bot_id, token, decision, price, exit_cadence))

    async def _batch(addrs):
        if batch_raises:
            raise RuntimeError("boom")
        return batch_result or {}

    stub._execute_bot_sell = _sell
    stub._fast_batch_prices = _batch
    return stub


def _run(stub, prices=None, now=1000.0):
    cfg = types.SimpleNamespace(sample_window=20)
    if prices is None:
        # default: newest sample already polled this tick (opens-union skips)
        prices = {}
        for a, buf in stub._fast_samples.items():
            if len(buf):
                prices[a.lower()] = buf[-1]
    asyncio.run(DipScanner._post_tp1_fastwatch(stub, cfg, prices, now))


class TestEnrollment:
    def test_enrolls_post_tp1_position(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        pm, p = _mk_pm()
        stub = _mk_stub({pm.config.bot_id: pm}, {MINT: [1.09, 1.09]})  # above line
        _run(stub)
        assert p.state_blob.get("ptfw_enrolled") is True
        assert p.state_blob.get("ptfw_enrolled_ts") == pytest.approx(1000.0)
        assert stub.sells == []   # +9 is above the +8 trail line — no fire

    def test_pre_tp1_position_not_enrolled(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        pm, p = _mk_pm(tp1_hit=False, peak=0.0)
        stub = _mk_stub({pm.config.bot_id: pm}, {MINT: [1.01, 1.01]})
        _run(stub)
        assert "ptfw_enrolled" not in p.state_blob
        assert stub.sells == []

    def test_kill_switch_off_is_noop(self, monkeypatch):
        monkeypatch.setenv("POST_TP1_FASTWATCH", "off")
        pm, p = _mk_pm()
        stub = _mk_stub({pm.config.bot_id: pm}, {MINT: [1.07, 1.07]})  # would fire
        _run(stub)
        assert "ptfw_enrolled" not in p.state_blob
        assert stub.sells == []

    def test_eviction_on_close(self, monkeypatch):
        """A closed position vanishes from iter_positions => no watch, no crash."""
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        pm, p = _mk_pm()
        pm._positions.pop("TOK")   # closed
        stub = _mk_stub({pm.config.bot_id: pm}, {MINT: [1.07, 1.07]})
        _run(stub)
        assert stub.sells == []


class TestConfirmFire:
    def test_confirmed_trail_fires_with_fastwatch_stamp(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        monkeypatch.delenv("POST_TP1_FASTWATCH_CONFIRM_TICKS", raising=False)
        # entry 1.0, peak +10, trail 2pp -> line +8; two fresh samples at +7
        pm, p = _mk_pm()
        stub = _mk_stub({pm.config.bot_id: pm}, {MINT: [1.10, 1.07, 1.07]})
        _run(stub)
        assert len(stub.sells) == 1
        bot_id, token, decision, price, cadence = stub.sells[0]
        assert decision.kind == "POST_TP1_TRAIL"
        assert decision.sell_fraction == 1.0
        assert price == pytest.approx(1.07)
        assert cadence == "fastwatch"
        assert p.state_blob.get("ptfw_served") is True
        assert p.state_blob.get("ptfw_fire_pnl") == pytest.approx(7.0, abs=0.01)

    def test_single_wick_does_not_fire(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        # newest sample below the line but the prior one above -> unconfirmed
        pm, p = _mk_pm()
        stub = _mk_stub({pm.config.bot_id: pm}, {MINT: [1.10, 1.07]})
        _run(stub)
        assert stub.sells == []

    def test_confirmed_tp2_fires_partial_and_sets_flag(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        # tp2 at +15; two fresh samples above it
        pm, p = _mk_pm()
        stub = _mk_stub({pm.config.bot_id: pm}, {MINT: [1.16, 1.165]})
        _run(stub)
        assert len(stub.sells) == 1
        assert stub.sells[0][2].kind == "TP2"
        assert p.tp2_hit is True   # tier flag set by the REAL tick, pre-sell

    def test_unconfirmed_tp2_does_not_burn_tier_flag(self, monkeypatch):
        """The DONALT-class guard: the REAL pm.tick must not run (and set
        tp2_hit) on a single unconfirmed print above the TP2 line."""
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        pm, p = _mk_pm()
        stub = _mk_stub({pm.config.bot_id: pm}, {MINT: [1.10, 1.16]})
        _run(stub)
        assert stub.sells == []
        assert p.tp2_hit is False

    def test_confirmed_peak_ratchet_updates_position(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        # recorded peak +10 but both fresh samples higher (+12, +13, below TP2)
        pm, p = _mk_pm()
        stub = _mk_stub({pm.config.bot_id: pm}, {MINT: [1.12, 1.13]})
        _run(stub)
        assert p.peak_pnl_pct == pytest.approx(12.0)   # min of the newest 2
        assert stub.sells == []                        # above the new trail line

    def test_too_few_samples_waits(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        pm, p = _mk_pm()
        stub = _mk_stub({pm.config.bot_id: pm}, {MINT: [1.07]})   # 1 < confirm 2
        _run(stub)
        assert stub.sells == []


class TestCapAndTTL:
    def test_cap_fifo_limits_watches(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        monkeypatch.setenv("POST_TP1_FASTWATCH_MAX", "2")
        pms, positions = {}, []
        addrs = [f"Mint{i}{'x' * 40}" for i in range(3)]
        for i, a in enumerate(addrs):
            cfg = BotConfig(bot_id=f"bot{i}", display_name=f"bot{i}", tp1_pct=6.0,
                            tp1_sell_fraction=0.75, tp2_pct=15.0, trail_pp=2.0,
                            hard_stop_pct=-12.0)
            pm = PerBotPositionManager(cfg)
            p = pm.open_position(token=f"T{i}", entry_price=1.0, size_usd=25.0,
                                 entry_time=900.0, address=a)
            p.tp1_hit = True
            p.peak_pnl_pct = 10.0
            p.state_blob["ptfw_enrolled_ts"] = 100.0 + i   # FIFO order 0,1,2
            p.state_blob["ptfw_enrolled"] = True
            pms[f"bot{i}"] = pm
            positions.append(p)
        samples = {a: [1.07, 1.07] for a in addrs}        # all would fire
        stub = _mk_stub(pms, samples)
        _run(stub)
        fired_bots = sorted(s[0] for s in stub.sells)
        assert fired_bots == ["bot0", "bot1"]             # cap 2, oldest first

    def test_ttl_evicts_watch(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        monkeypatch.setenv("POST_TP1_FASTWATCH_TTL_SECS", "50")
        pm, p = _mk_pm()
        p.state_blob["ptfw_enrolled_ts"] = 100.0          # age 900s > 50s TTL
        p.state_blob["ptfw_enrolled"] = True
        stub = _mk_stub({pm.config.bot_id: pm}, {MINT: [1.07, 1.07]})
        _run(stub, now=1000.0)
        assert stub.sells == []
        assert p.state_blob.get("ptfw_ttl_evicted") is True


class TestFailOpen:
    def test_batch_price_crash_is_swallowed(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        pm, p = _mk_pm()
        stub = _mk_stub({pm.config.bot_id: pm}, {}, batch_raises=True)
        _run(stub, prices={})   # address unpriced -> opens-union raises inside
        assert stub.sells == []             # fell back to main cadence, no raise

    def test_sell_crash_does_not_propagate_and_frees_inflight(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        pm, p = _mk_pm()
        stub = _mk_stub({pm.config.bot_id: pm}, {MINT: [1.07, 1.07]})

        async def _boom(*a, **k):
            raise RuntimeError("sell path down")
        stub._execute_bot_sell = _boom
        _run(stub)   # must not raise
        assert stub._ptfw_inflight == set()   # finally: freed the slot

    def test_missing_position_manager_dict_is_noop(self, monkeypatch):
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        stub = types.SimpleNamespace()
        stub.bot_position_managers = None
        cfg = types.SimpleNamespace(sample_window=20)
        asyncio.run(DipScanner._post_tp1_fastwatch(stub, cfg, {}, 1000.0))


class TestCadencePlumbing:
    def test_execute_bot_sell_defaults_to_main(self):
        sig = inspect.signature(DipScanner._execute_bot_sell)
        assert sig.parameters["exit_cadence"].default == "main"

    def test_opens_union_fetches_unpolled_address(self, monkeypatch):
        """A held winner outside the armed set gets its fresh read via the
        one-batch opens-union (and fires once two ticks confirm)."""
        monkeypatch.delenv("POST_TP1_FASTWATCH", raising=False)
        pm, p = _mk_pm()
        # one prior sample below the line; this tick's batch serves another
        stub = _mk_stub({pm.config.bot_id: pm}, {MINT: [1.07]},
                        batch_result={MINT.lower(): 1.07})
        _run(stub, prices={})
        assert len(stub.sells) == 1
        assert stub.sells[0][4] == "fastwatch"
