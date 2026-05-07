"""trigger_hh10_strict_vol — 7+ higher-highs in last 10 + green + vol >= 1.5x.

Mined round-2 fast-mover validation: WR 61.3%, avg +$1.03, n=2304.
Different mechanism from consec-green triggers — looks at price-action
strength via HH count. Catches sustained climbs even when greens
aren't strictly consecutive.
"""
NAME = "trigger_hh10_strict_vol"
DESCRIPTION = "ENTER on 7+ higher-highs in last 10 1m bars + green + vol >= 1.5x"
NEEDS_OHLC = True


def should_enter(o, h, l, c, v=None, em=None, recent_bars=None):
    if not recent_bars or len(recent_bars) < 31:
        return False
    cur = recent_bars[-1]
    if cur['o'] <= 0 or cur['c'] <= cur['o']:
        return False
    last10 = recent_bars[-10:]
    hh = sum(1 for j in range(1, 10) if last10[j]['h'] > last10[j-1]['h'])
    if hh < 7:
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
