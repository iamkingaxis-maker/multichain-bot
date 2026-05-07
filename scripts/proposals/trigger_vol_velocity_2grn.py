"""trigger_vol_velocity_2grn — accelerating volume + 2-bar green run.

19th parallel trigger. Mined from gap analysis (round 4): targets
FAST_WIN bars NOT caught by any of the prior 18 triggers.

Pattern fires when:
  1. Last 2 bars green (cur green AND prior green)
  2. Volume strictly increasing across last 3 bars: vol[i] > vol[i-1] > vol[i-2]
  3. Current bar body >= 2.0%
  4. Current vol >= 1.0x trailing 30-bar avg

Mechanism: each successive bar attracting more buyers than the last
(velocity, not just spike), within a 2-bar green sequence with strong
body. Captures momentum starts that spike-only or sequence-only
triggers miss.

Multi-cohort validation (gap-only — uncaptured by 7 representative
existing triggers):
  +10%/20min cohort:  WR=64.1%, +$1.42/trade, n=690, Stop=25.9%
  +12%/30min cohort:  WR≈65%, similar profile
  +15%/60min cohort:  WR≈65%, similar profile
  +20%/90min cohort:  WR≈65%, similar profile

Pareto frontier showed body=2.0%/vol=1.0x is highest WR among all
tested operating points (>1.2x vol gate strictly worse on both axes).
"""
NAME = "trigger_vol_velocity_2grn"
DESCRIPTION = "ENTER on rising vol velocity + 2 bars green + body >= 2.0% + vol >= 1.0x avg30"
NEEDS_OHLC = True


def should_enter(o, h, l, c, v=None, em=None, recent_bars=None):
    if not recent_bars or len(recent_bars) < 31:
        return False
    cur = recent_bars[-1]
    if cur['o'] <= 0 or cur['c'] <= cur['o']:
        return False
    p1 = recent_bars[-2]
    if p1['o'] <= 0 or p1['c'] <= p1['o']:
        return False
    v1 = cur.get('v', 0) or 0
    v2 = p1.get('v', 0) or 0
    v3 = recent_bars[-3].get('v', 0) or 0
    if not (v1 > v2 > v3 > 0):
        return False
    body_pct = (cur['c'] - cur['o']) / cur['o'] * 100
    if body_pct < 2.0:
        return False
    p30 = recent_bars[-31:-1]
    vols = [b.get('v', 0) for b in p30 if b.get('v') is not None]
    if not vols:
        return False
    av = sum(vols) / len(vols)
    if av <= 0 or v1 / av < 1.0:
        return False
    return True
