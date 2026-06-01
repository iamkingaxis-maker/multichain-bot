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
