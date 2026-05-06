"""filter_double_bottom — BLOCK if entry is at rock-bottom of BOTH
5m range AND 1h range simultaneously.

Targets the PAYmo 12:44 stop-out (-$2.71) which slipped past every
other filter:
  - bs_m5 = 3.00 (buyer-dominant — passes seller_dominant)
  - pct_in_1h_range = 0.009 (rock bottom)
  - pct_in_5m_range = 0.039 (rock bottom)
The orderflow looked fine but the price was at the absolute floor of
both windows. Knife-catching from both micro and macro perspectives.

Distinct from filter_double_bear which uses bs_m5+p1h. This uses p5m+p1h.
Uses only entry_meta fields → all three validation tiers run.
"""

NAME = "filter_double_bottom"
DESCRIPTION = "BLOCK if pct_in_5m_range < 0.10 AND pct_in_1h_range < 0.10"
NEEDS_OHLC = False


def should_block(o, h, l, c, v=None, em=None):
    if not em:
        return False  # fail-open
    p5m = em.get('pct_in_5m_range')
    p1h = em.get('pct_in_1h_range')
    if p5m is None or p1h is None:
        return False  # fail-open
    return p5m < 0.10 and p1h < 0.10
