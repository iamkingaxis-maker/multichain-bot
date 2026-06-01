"""Phantom-scrub daily_pnl_usd correctness (2026-05-31 regression).

The first phantom scrub subtracted the FULL phantom pnl from daily_pnl_usd even
when the phantom trade was dated on a prior UTC day than the bot's current daily
counter — corrupting today's value (champion_premium_tightexit: -$219.98 while
real daily was +$15.25). These tests pin:
  1. migrate() reduces balance+realized by the full phantom but reduces
     daily_pnl_usd ONLY by phantom dated on the bot's current daily_pnl_date.
  2. repair_phantom_daily_pnl() recomputes daily from today's real sells.
"""
import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.scrub_phantom_pnl import migrate, repair_phantom_daily_pnl

TODAY = datetime.now(timezone.utc).date().isoformat()
YESTERDAY = "2026-05-31" if TODAY != "2026-05-31" else "2026-05-30"


def _setup(tmp_path: Path, daily_pnl_usd: float, daily_date: str, trades: list):
    (tmp_path / "trades_multi.json").write_text(json.dumps(trades))
    bs = tmp_path / "bot_state"
    bs.mkdir()
    (bs / "botX.json").write_text(json.dumps({
        "bot_id": "botX", "balance_usd": 2000.0, "in_flight_usd": 0.0,
        "realized_pnl_total_usd": 250.0, "daily_pnl_usd": daily_pnl_usd,
        "daily_pnl_date": daily_date,
    }))
    return bs / "botX.json"


def test_migrate_does_not_subtract_prior_day_phantom_from_daily(tmp_path):
    # Phantom (+$235) dated YESTERDAY; bot's daily counter is TODAY at +$15.
    trades = [
        {"type": "sell", "bot_id": "botX", "token": "SPCX", "pnl": 235.0,
         "pnl_pct": 1180.0, "entry_price": 1.0, "exit_price": 12.8, "time": f"{YESTERDAY}T22:00:00"},
        {"type": "sell", "bot_id": "botX", "token": "REAL", "pnl": 15.0,
         "pnl_pct": 5.0, "entry_price": 1.0, "exit_price": 1.05, "time": f"{TODAY}T01:00:00"},
    ]
    sp = _setup(tmp_path, daily_pnl_usd=15.0, daily_date=TODAY, trades=trades)
    migrate(tmp_path, force=True)
    st = json.loads(sp.read_text())
    # balance + realized: full $235 removed (cumulative, date-independent)
    assert abs(st["balance_usd"] - (2000.0 - 235.0)) < 1e-6
    assert abs(st["realized_pnl_total_usd"] - (250.0 - 235.0)) < 1e-6
    # daily: untouched — phantom was a prior UTC day, real daily stays +$15
    assert abs(st["daily_pnl_usd"] - 15.0) < 1e-6


def test_migrate_subtracts_same_day_phantom_from_daily(tmp_path):
    # Phantom (+$235) dated TODAY; daily counter (TODAY) includes it (+$250).
    trades = [
        {"type": "sell", "bot_id": "botX", "token": "SPCX", "pnl": 235.0,
         "pnl_pct": 1180.0, "entry_price": 1.0, "exit_price": 12.8, "time": f"{TODAY}T02:00:00"},
        {"type": "sell", "bot_id": "botX", "token": "REAL", "pnl": 15.0,
         "pnl_pct": 5.0, "entry_price": 1.0, "exit_price": 1.05, "time": f"{TODAY}T03:00:00"},
    ]
    sp = _setup(tmp_path, daily_pnl_usd=250.0, daily_date=TODAY, trades=trades)
    migrate(tmp_path, force=True)
    st = json.loads(sp.read_text())
    assert abs(st["daily_pnl_usd"] - 15.0) < 1e-6  # 250 - 235 phantom = 15 real


def test_repair_recomputes_daily_from_real_today_sells(tmp_path):
    # Simulate the ALREADY-corrupted state: daily = -219.98, real today = +15.
    trades = [
        {"type": "sell", "bot_id": "botX", "token": "SPCX", "pnl": 0.0,
         "pnl_pct": 0.0, "phantom_scrubbed": True, "orig_pnl": 235.0,
         "time": f"{YESTERDAY}T22:00:00"},
        {"type": "sell", "bot_id": "botX", "token": "REAL", "pnl": 15.0,
         "pnl_pct": 5.0, "time": f"{TODAY}T01:00:00"},
    ]
    _setup(tmp_path, daily_pnl_usd=-219.98, daily_date=TODAY, trades=trades)
    # Repair needs the scrub sentinel naming botX as scrubbed.
    (tmp_path / "phantom_scrub_done.json").write_text(json.dumps({"scrubbed_bots": ["botX"]}))
    n = repair_phantom_daily_pnl(tmp_path, force=True)
    assert n == 1
    st = json.loads((tmp_path / "bot_state" / "botX.json").read_text())
    assert abs(st["daily_pnl_usd"] - 15.0) < 1e-6
    assert st["daily_pnl_date"] == TODAY


def test_repair_noop_without_scrub_sentinel(tmp_path):
    _setup(tmp_path, daily_pnl_usd=-219.98, daily_date=TODAY, trades=[])
    assert repair_phantom_daily_pnl(tmp_path, force=True) == 0


# ── self-healing scrub (catches NEW phantoms after the one-time scrub) ──────────
from scripts.scrub_phantom_pnl import scrub_unscrubbed_phantoms


def _setup_selfheal(tmp_path, daily_pnl_usd, daily_date, trades):
    (tmp_path / "trades_multi.json").write_text(json.dumps(trades))
    bs = tmp_path / "bot_state"; bs.mkdir()
    (bs / "premiumX.json").write_text(json.dumps({
        "bot_id": "premiumX", "balance_usd": 2096.70, "in_flight_usd": 0.0,
        "realized_pnl_total_usd": 96.70, "daily_pnl_usd": daily_pnl_usd,
        "daily_pnl_date": daily_date,
    }))


def test_selfheal_scrubs_new_spcx_phantom(tmp_path):
    # SPCX 4.2x phantom (exit/entry>3) booked today, not yet scrubbed.
    trades = [
        {"type": "sell", "bot_id": "premiumX", "token": "SPCX", "pnl": 64.20,
         "pnl_pct": 320.0, "entry_price": 0.00092, "exit_price": 0.00384,
         "time": f"{TODAY}T17:49:00"},
        {"type": "sell", "bot_id": "premiumX", "token": "REAL", "pnl": 32.50,
         "pnl_pct": 6.0, "time": f"{TODAY}T18:00:00"},
    ]
    _setup_selfheal(tmp_path, daily_pnl_usd=96.70, daily_date=TODAY, trades=trades)
    n = scrub_unscrubbed_phantoms(tmp_path)
    assert n == 1
    st = json.loads((tmp_path / "bot_state" / "premiumX.json").read_text())
    assert abs(st["realized_pnl_total_usd"] - (96.70 - 64.20)) < 1e-6   # ~32.50 real
    assert abs(st["balance_usd"] - (2096.70 - 64.20)) < 1e-6
    assert abs(st["daily_pnl_usd"] - (96.70 - 64.20)) < 1e-6            # same-day → subtracted
    tr = json.loads((tmp_path / "trades_multi.json").read_text())
    spcx = [t for t in tr if t["token"] == "SPCX"][0]
    assert spcx["phantom_scrubbed"] is True and spcx["pnl"] == 0.0 and spcx["orig_pnl"] == 64.20


def test_selfheal_idempotent_no_double_subtract(tmp_path):
    trades = [
        {"type": "sell", "bot_id": "premiumX", "token": "SPCX", "pnl": 64.20,
         "pnl_pct": 320.0, "entry_price": 0.00092, "exit_price": 0.00384,
         "time": f"{TODAY}T17:49:00"},
    ]
    _setup_selfheal(tmp_path, daily_pnl_usd=96.70, daily_date=TODAY, trades=trades)
    assert scrub_unscrubbed_phantoms(tmp_path) == 1
    realized1 = json.loads((tmp_path / "bot_state" / "premiumX.json").read_text())["realized_pnl_total_usd"]
    assert scrub_unscrubbed_phantoms(tmp_path) == 0   # second run: nothing left to scrub
    realized2 = json.loads((tmp_path / "bot_state" / "premiumX.json").read_text())["realized_pnl_total_usd"]
    assert realized1 == realized2                     # NOT double-subtracted


def test_selfheal_skips_already_scrubbed(tmp_path):
    trades = [
        {"type": "sell", "bot_id": "premiumX", "token": "SPCX", "pnl": 0.0, "pnl_pct": 0.0,
         "entry_price": 0.00092, "exit_price": 0.00384, "phantom_scrubbed": True,
         "orig_pnl": 64.20, "time": f"{TODAY}T17:49:00"},
    ]
    _setup_selfheal(tmp_path, daily_pnl_usd=32.50, daily_date=TODAY, trades=trades)
    assert scrub_unscrubbed_phantoms(tmp_path) == 0   # flagged → skipped (no re-subtract)


# ── drop-phantom scrub (OHLC-low confirmed; must NOT scrub real rugs) ──────────

def test_selfheal_scrubs_confirmed_drop_phantom(tmp_path):
    # E6ifp2 SPCX: hard stop filled at 0.0008 (−81%) but real low was 0.00313 →
    # below real low → phantom LOSS → restore it (realized goes UP).
    trades = [
        {"type": "sell", "bot_id": "premiumX", "token": "SPCX", "reason": "hard stop",
         "pnl": -24.29, "pnl_pct": -81.0, "entry_price": 0.00417, "exit_price": 0.0008,
         "pair_address": "DZxWcyPpTyr2", "time": f"{TODAY}T18:22:00+00:00"},
    ]
    _setup_selfheal(tmp_path, daily_pnl_usd=-24.29, daily_date=TODAY, trades=trades)
    n = scrub_unscrubbed_phantoms(tmp_path, low_fn=lambda pair: 0.00313)  # real low
    assert n == 1
    st = json.loads((tmp_path / "bot_state" / "premiumX.json").read_text())
    assert abs(st["realized_pnl_total_usd"] - (96.70 - (-24.29))) < 1e-6   # loss restored (+$24.29)
    assert abs(st["daily_pnl_usd"] - (-24.29 - (-24.29))) < 1e-6           # back to 0
    tr = json.loads((tmp_path / "trades_multi.json").read_text())[0]
    assert tr["phantom_scrubbed"] is True and tr["orig_pnl"] == -24.29


def test_selfheal_does_NOT_scrub_real_rug(tmp_path):
    # A genuine rug: stop filled at 0.00005, and the token's real low IS ~0.00005
    # (it really crashed there) → exit within real low → NOT a phantom → keep the loss.
    trades = [
        {"type": "sell", "bot_id": "premiumX", "token": "RUG", "reason": "hard stop",
         "pnl": -18.0, "pnl_pct": -90.0, "entry_price": 0.0005, "exit_price": 0.00005,
         "pair_address": "RugPair", "time": f"{TODAY}T18:22:00+00:00"},
    ]
    _setup_selfheal(tmp_path, daily_pnl_usd=-18.0, daily_date=TODAY, trades=trades)
    n = scrub_unscrubbed_phantoms(tmp_path, low_fn=lambda pair: 0.00005)  # real low = the rug
    assert n == 0   # exit (0.00005) >= real_low*0.85 → real → NOT scrubbed
    st = json.loads((tmp_path / "bot_state" / "premiumX.json").read_text())
    assert st["realized_pnl_total_usd"] == 96.70   # unchanged (real loss kept)


def test_selfheal_drop_requires_low_fn(tmp_path):
    # Without low_fn, a deep stop is NEVER scrubbed (no OHLC confirmation).
    trades = [
        {"type": "sell", "bot_id": "premiumX", "token": "SPCX", "reason": "hard stop",
         "pnl": -24.29, "pnl_pct": -81.0, "entry_price": 0.00417, "exit_price": 0.0008,
         "pair_address": "DZxWcyPpTyr2", "time": f"{TODAY}T18:22:00+00:00"},
    ]
    _setup_selfheal(tmp_path, daily_pnl_usd=-24.29, daily_date=TODAY, trades=trades)
    assert scrub_unscrubbed_phantoms(tmp_path) == 0   # no low_fn → not scrubbed
