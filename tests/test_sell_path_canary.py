# tests/test_sell_path_canary.py
"""Sell-path canary: 'no working exit -> no new entries' (2026-07-10 incident:
probe bought SMOLE 14 min after the first failed sell; sells were blind for
7+ hours while buys kept working)."""
from core.sell_path_canary import SellPathCanary, canary_mode_on, CANARY_MINT

T0 = 1_000_000.0


class TestCanaryState:
    def test_boot_grace_allows_then_fails_closed(self):
        c = SellPathCanary(interval_secs=60, spawned_at=T0)
        assert c.healthy(T0 + 10) is True          # inside grace, no data yet
        assert c.healthy(T0 + 179) is True
        assert c.healthy(T0 + 181) is False        # grace over, still no probe

    def test_pass_is_healthy(self):
        c = SellPathCanary(interval_secs=60, spawned_at=T0)
        c.record(True, T0 + 30)
        assert c.healthy(T0 + 60) is True

    def test_genuine_zero_read_counts_as_pass(self):
        # semantic: the wallet holding 0 of the canary mint is a PASS — the
        # loop records ok when bal >= 0 (0 included); only -1/None fails.
        c = SellPathCanary(spawned_at=T0)
        c.record(True, T0 + 5)                     # bal==0 -> ok=True upstream
        assert c.healthy(T0 + 10) is True

    def test_fail_blocks(self):
        c = SellPathCanary(spawned_at=T0)
        c.record(True, T0 + 30)
        c.record(False, T0 + 90)                   # newest probe failed
        assert c.healthy(T0 + 95) is False
        assert c.consecutive_fails == 1

    def test_recovery_after_fail(self):
        c = SellPathCanary(spawned_at=T0)
        c.record(False, T0 + 30)
        assert c.healthy(T0 + 35) is False
        c.record(True, T0 + 90)
        assert c.healthy(T0 + 95) is True
        assert c.consecutive_fails == 0

    def test_stale_success_ages_out(self):
        # wedged/dead canary loop must not count as healthy forever
        c = SellPathCanary(interval_secs=60, spawned_at=T0)
        c.record(True, T0 + 30)
        assert c.healthy(T0 + 30 + 239) is True    # < 4x interval
        assert c.healthy(T0 + 30 + 241) is False   # stale

    def test_only_failures_ever_seen(self):
        c = SellPathCanary(spawned_at=T0)
        c.record(False, T0 + 10)
        c.record(False, T0 + 70)
        assert c.healthy(T0 + 75) is False

    def test_env_kill_switch(self, monkeypatch):
        c = SellPathCanary(spawned_at=T0)
        c.record(False, T0 + 10)
        monkeypatch.setenv("SELL_PATH_CANARY_MODE", "off")
        assert c.healthy(T0 + 15) is True          # gated off entirely

    def test_mode_default_on(self, monkeypatch):
        monkeypatch.delenv("SELL_PATH_CANARY_MODE", raising=False)
        assert canary_mode_on() is True            # the incident's mandate

    def test_canary_mint_is_usdc(self):
        assert CANARY_MINT == "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
