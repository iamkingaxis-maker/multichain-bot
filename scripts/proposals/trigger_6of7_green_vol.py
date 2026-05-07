"""trigger_6of7_green_vol — 6 of last 7 bars green + 1m green + vol >= 1.5x avg30.

Mined round-2 fast-mover validation: WR 63.7%, avg +$1.36, n=1708 fires.
Catches "mostly green" sequences where 1 red bar broke the run but
momentum resumed. Different from momentum_continuation (strict 4 consec).
"""
NAME = "trigger_6of7_green_vol"
DESCRIPTION = "ENTER on 6 of last 7 bars green + cur green + vol >= 1.5x avg30"
NEEDS_OHLC = True


def should_enter(o, h, l, c, v=None, em=None, recent_bars=None):
    if not recent_bars or len(recent_bars) < 31:
        return False
    cur = recent_bars[-1]
    if cur['o'] <= 0 or cur['c'] <= cur['o']:
        return False
    last7 = recent_bars[-7:]
    greens = sum(1 for b in last7 if b['o'] > 0 and b['c'] > b['o'])
    if greens < 6:
        return False
    if v is None:
        return False
    prior30 = recent_bars[-31:-1]
    vols = [b.get('v', 0) for b in prior30 if b.get('v') is not None]
    if not vols or len(vols) < 20:
        return False
    avg30 = sum(vols) / len(vols)
    if avg30 <= 0 or v / avg30 < 1.5:
        return False
    return True
