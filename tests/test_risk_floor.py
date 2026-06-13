"""Phase-1 risk floors: per-bot daily-loss halt + per-token re-entry cap.
Production-bot features (shadow-measured fleet-wide, enforced on the production config).
Spec: docs/superpowers/specs/2026-06-01-phase1-risk-floor-design.md"""
from core.per_bot_capital import PerBotCapital
from core.per_bot_position_manager import PerBotPositionManager
from core.bot_config import BotConfig

TODAY = "2026-06-01T12:00:00+00:00"        # noon UTC = 07:00 CT Jun-1
# Daily floors roll at CT 00:00 (2026-06-13 fix), so "tomorrow" must be the
# next CT day: noon UTC Jun-2 = 07:00 CT Jun-2. (00:30 UTC Jun-2 would be
# 19:30 CT Jun-1 — still the same CT day, deliberately NOT a rollover.)
TOMORROW = "2026-06-02T12:00:00+00:00"


# ── A) daily-loss halt ──────────────────────────────────────────────────────
def _cap():
    c = PerBotCapital("b", 2000.0)
    c._daily_pnl_date = "2026-06-01"
    return c

def test_daily_loss_breached_true_when_at_or_below_limit():
    c = _cap(); c.daily_pnl_usd = -100.0
    assert c.daily_loss_breached(100.0, TODAY) is True
    c.daily_pnl_usd = -100.01
    assert c.daily_loss_breached(100.0, TODAY) is True

def test_daily_loss_not_breached_above_limit():
    c = _cap(); c.daily_pnl_usd = -99.99
    assert c.daily_loss_breached(100.0, TODAY) is False

def test_daily_loss_off_when_limit_none_or_zero():
    c = _cap(); c.daily_pnl_usd = -500.0
    assert c.daily_loss_breached(None, TODAY) is False
    assert c.daily_loss_breached(0, TODAY) is False

def test_daily_loss_resets_after_ct_rollover():
    c = _cap(); c.daily_pnl_usd = -200.0
    assert c.daily_loss_breached(100.0, TODAY) is True
    # next CT day -> rollover zeroes daily_pnl -> not breached
    assert c.daily_loss_breached(100.0, TOMORROW) is False
    assert c.daily_pnl_usd == 0.0


# ── B) per-token re-entry cap ───────────────────────────────────────────────
def _pm():
    return PerBotPositionManager(BotConfig(bot_id="b", display_name="b"))

def test_token_buys_counts_and_increments():
    pm = _pm()
    assert pm.token_buys_today("AAA", TODAY) == 0
    pm._record_token_buy("AAA", TODAY); pm._record_token_buy("AAA", TODAY)
    assert pm.token_buys_today("AAA", TODAY) == 2
    assert pm.token_buys_today("BBB", TODAY) == 0   # per-token independent

def test_token_buys_reset_next_ct_day():
    pm = _pm()
    pm._record_token_buy("AAA", TODAY); pm._record_token_buy("AAA", TODAY)
    assert pm.token_buys_today("AAA", TODAY) == 2
    assert pm.token_buys_today("AAA", TOMORROW) == 0   # rolled over (next CT day)
    pm._record_token_buy("AAA", TOMORROW)
    assert pm.token_buys_today("AAA", TOMORROW) == 1

def test_open_position_increments_token_buys():
    pm = _pm()
    pm.open_position("AAA", entry_price=1.0, size_usd=20.0, entry_time=0.0)
    assert pm.token_buys_today("AAA") >= 1

def test_reentry_cap_logic_blocks_n_plus_1():
    # cap=3: the 4th buy of the same token today should be at/over the cap
    pm = _pm()
    for _ in range(3): pm._record_token_buy("AAA", TODAY)
    assert pm.token_buys_today("AAA", TODAY) >= 3   # >= cap => block the next
