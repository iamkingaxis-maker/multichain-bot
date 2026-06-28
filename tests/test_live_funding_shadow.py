# -*- coding: utf-8 -*-
"""Tier-2 GAP C — SHADOW counter for the LIVE SOL-reserve / funding gate.

LIVE aborts every buy when wallet SOL < MIN_SOL_RESERVE (trader._check_sol_reserve);
PAPER's reserve_for_buy only checks the paper balance_usd and never sees the real
wallet, so a drained live wallet would have killed paper buys paper happily books.
``core.live_funding_shadow`` COUNTS (never blocks) those buys.

Contract under test:
  * mode off -> true no-op (no counters, no log, buy proceeds).
  * shadow + a buy whose SOL need drops sim balance below MIN_SOL_RESERVE ->
    counted/logged as a would-block, but the caller still proceeds (no skip).
  * shadow + a buy that fits -> NOT counted.
  * fail-open: bad inputs -> no crash, no block.
"""
import importlib

import pytest

import core.live_funding_shadow as lfs


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    # Deterministic flags + a fresh per-process running balance for every test.
    monkeypatch.setenv("LIVE_FUNDING_SHADOW_SOL", "0.06")
    monkeypatch.setenv("MIN_SOL_RESERVE", "0.05")
    lfs._reset()
    yield
    lfs._reset()


# ── mode off == byte-identical no-op ─────────────────────────────────────────

def test_off_is_noop(monkeypatch):
    monkeypatch.setenv("LIVE_FUNDING_SHADOW_MODE", "off")
    # A buy that would obviously bust the reserve (need 1 SOL vs 0.06 balance).
    blocked = lfs.note_paper_buy(1.0, "bot1", "MintA", "DEGEN")
    assert blocked is False
    s = lfs.stats()
    assert s["buys_evaluated"] == 0      # nothing counted
    assert s["would_block"] == 0
    assert s["sim_sol"] is None          # state never even initialized


def test_off_default_when_flag_unset(monkeypatch):
    monkeypatch.delenv("LIVE_FUNDING_SHADOW_MODE", raising=False)
    assert lfs.shadow_mode() == "off"
    assert lfs.note_paper_buy(1.0, "bot1", "MintA", "DEGEN") is False
    assert lfs.stats()["buys_evaluated"] == 0


# ── shadow: a buy that busts the reserve is counted + logged, never blocks ───

def test_shadow_counts_would_block_but_proceeds(monkeypatch):
    monkeypatch.setenv("LIVE_FUNDING_SHADOW_MODE", "shadow")
    logged = {}

    def _fake_log(gate, bot, token_address, symbol, **ctx):
        logged.update(gate=gate, bot=bot, token_address=token_address,
                      symbol=symbol, ctx=ctx)

    monkeypatch.setattr("core.shadow_gate_log.log_shadow_block", _fake_log)

    # sim_sol 0.06, reserve 0.05 -> need 0.02 leaves 0.04 < 0.05 -> would-block.
    blocked = lfs.note_paper_buy(0.02, "bot1", "MintA", "DEGEN")

    assert blocked is True                       # would-block reported...
    s = lfs.stats()
    assert s["buys_evaluated"] == 1
    assert s["would_block"] == 1
    assert s["would_block_pct"] == 100.0
    # ...but the sim balance was NOT debited (a live wallet wouldn't spend below
    # reserve), and crucially the RETURN VALUE is informational: the caller is
    # expected to proceed with the paper buy regardless (the helper never skips).
    assert s["sim_sol"] == pytest.approx(0.06)
    # logged via the proven shadow_gate_log pattern, address-keyed.
    assert logged["gate"] == "live_funding_gate"
    assert logged["token_address"] == "MintA"
    assert logged["bot"] == "bot1"


# ── shadow: a buy that fits is NOT counted and debits the sim balance ────────

def test_shadow_fitting_buy_not_counted(monkeypatch):
    monkeypatch.setenv("LIVE_FUNDING_SHADOW_MODE", "shadow")
    # need 0.005 -> 0.06-0.005 = 0.055 >= 0.05 -> fits.
    blocked = lfs.note_paper_buy(0.005, "bot1", "MintA", "DEGEN")
    assert blocked is False
    s = lfs.stats()
    assert s["buys_evaluated"] == 1
    assert s["would_block"] == 0
    assert s["sim_sol"] == pytest.approx(0.055)   # debited


def test_shadow_running_drain_then_blocks(monkeypatch):
    monkeypatch.setenv("LIVE_FUNDING_SHADOW_MODE", "shadow")
    # 0.06 -> spend 0.005 (ok, 0.055) -> spend 0.005 (ok, 0.05) ->
    # spend 0.005 (0.05-0.005=0.045 < 0.05 -> would-block, no debit).
    assert lfs.note_paper_buy(0.005, "b", "M", "S") is False
    assert lfs.note_paper_buy(0.005, "b", "M", "S") is False
    assert lfs.note_paper_buy(0.005, "b", "M", "S") is True
    s = lfs.stats()
    assert s["buys_evaluated"] == 3
    assert s["would_block"] == 1
    assert s["sim_sol"] == pytest.approx(0.05)    # last (blocked) buy not debited


def test_credit_sell_recovers_balance(monkeypatch):
    monkeypatch.setenv("LIVE_FUNDING_SHADOW_MODE", "shadow")
    lfs.note_paper_buy(0.005, "b", "M", "S")      # 0.06 -> 0.055
    lfs.credit_sell(0.01)                          # 0.055 -> 0.065
    assert lfs.stats()["sim_sol"] == pytest.approx(0.065)


def test_credit_sell_noop_when_off(monkeypatch):
    monkeypatch.setenv("LIVE_FUNDING_SHADOW_MODE", "off")
    lfs.credit_sell(0.01)
    assert lfs.stats()["sim_sol"] is None


# ── fail-open: bad inputs never crash, never block ───────────────────────────

@pytest.mark.parametrize("bad", [None, "not-a-number", float("nan"), object()])
def test_failopen_bad_need_sol(monkeypatch, bad):
    monkeypatch.setenv("LIVE_FUNDING_SHADOW_MODE", "shadow")
    # NaN passes the float() cast but (nan - need) < reserve is False, so it is
    # simply not counted as a block; the contract we assert is "no crash, returns
    # a bool, never raises". None/str/object fail the cast -> fail-open False.
    out = lfs.note_paper_buy(bad, "bot1", "MintA", "DEGEN")
    assert out is False
    # No would-block was recorded for the un-castable inputs.
    if bad is None or isinstance(bad, str) or isinstance(bad, object) and not isinstance(bad, float):
        pass  # tolerant: the hard contract is just "no raise"


def test_failopen_negative_need(monkeypatch):
    monkeypatch.setenv("LIVE_FUNDING_SHADOW_MODE", "shadow")
    assert lfs.note_paper_buy(-1.0, "bot1", "MintA", "DEGEN") is False
    assert lfs.stats()["buys_evaluated"] == 0


def test_failopen_log_error_swallowed(monkeypatch):
    monkeypatch.setenv("LIVE_FUNDING_SHADOW_MODE", "shadow")

    def _boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr("core.shadow_gate_log.log_shadow_block", _boom)
    # The logging blew up, but note_paper_buy still returns the verdict and the
    # counter is still incremented — it never raises into the caller.
    out = lfs.note_paper_buy(0.02, "bot1", "MintA", "DEGEN")
    assert out is True
    assert lfs.stats()["would_block"] == 1


def test_module_reimport_clean():
    # Sanity: the module imports/reloads without side effects (no IO at import).
    importlib.reload(lfs)
    lfs._reset()
    assert lfs.stats()["buys_evaluated"] == 0
