"""filter_vol_collapse — BLOCK if 1m_volume_spike < 0.20 on entry candle.

Hypothesis: extremely low volume on the green clean_break candle means
the bounce is "air" — no real buying pressure. The price tick is from
illiquid order routing, not absorbed selling. These tend to fail.

Different from filter_confirmation_candle (threshold 1.0, anti-predictive
on broader population). 0.20 is much stricter — only the genuine
dead-volume edge cases.

Targets pattern: PAYmo 12:44 had 1m_volume_spike=0.107 (lost -$2.71).
"""

NAME = "filter_vol_collapse"
DESCRIPTION = "BLOCK if 1m_volume_spike < 0.20 (dead-volume entry)"
NEEDS_OHLC = False


def should_block(o, h, l, c, v=None, em=None):
    if not em:
        return False  # fail-open
    vs = em.get('1m_volume_spike')
    if vs is None:
        return False  # fail-open
    return vs < 0.20
