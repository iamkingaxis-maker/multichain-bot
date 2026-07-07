# tests/test_young_pond_decouple.py
"""Young-pond reservation decouple (2026-07-07 throughput fix).

Raising YOUNG_TOKEN_MAX_AGE_H to 36 (for the live probe's pond) had reserved the
ENTIRE 0-36h band for probe bots via the single-threshold buy_gate_skip, starving
every non-probe bot (the flush family + the new volume lanes) of the young tokens
where the action is. buy_gate_skip_age decouples: production bots are reserved-out
ONLY of the freshest rug-bait (< reserve_below_h), the probe stays young-only
(< max_age_hours). The live probe's gating is unchanged.
"""
from core.young_token_probe import buy_gate_skip_age


R = 2.0    # reserve_below_h  (production skips younger than this)
M = 36.0   # max_age_hours    (probe trades younger than this)
PROBE, PROD = True, False


def _skip(age, is_probe):
    return buy_gate_skip_age(age, is_probe, reserve_h=R, max_h=M, probe_on=True)


class TestProductionUnstarved:
    def test_10h_token_now_traded(self):
        # THE fix: a 10h token was skipped by production (reserved) — now traded.
        assert _skip(10.0, PROD) is False

    def test_mid_band_traded(self):
        for age in (2.0, 6.0, 24.0, 35.9, 100.0):
            assert _skip(age, PROD) is False, f"production wrongly skipped {age}h"

    def test_freshest_rugbait_still_reserved(self):
        # production is still protected from the < reserve window
        assert _skip(0.5, PROD) is True
        assert _skip(1.99, PROD) is True

    def test_reserve_boundary(self):
        assert _skip(2.0, PROD) is False   # exactly reserve -> not younger -> trade


class TestProbeUnchanged:
    def test_probe_trades_its_pond(self):
        for age in (0.5, 2.0, 10.0, 35.9):
            assert _skip(age, PROBE) is False, f"probe wrongly skipped {age}h"

    def test_probe_skips_older_than_ceiling(self):
        assert _skip(36.0, PROBE) is True   # exactly ceiling -> young-only -> skip
        assert _skip(80.0, PROBE) is True


class TestEdges:
    def test_probe_off_never_skips(self):
        assert buy_gate_skip_age(10.0, PROD, reserve_h=R, max_h=M, probe_on=False) is False
        assert buy_gate_skip_age(10.0, PROBE, reserve_h=R, max_h=M, probe_on=False) is False

    def test_unknown_age_preserves_old_bias(self):
        # unknown age: probe (young-only) skips; production allows
        assert _skip(None, PROBE) is True
        assert _skip(None, PROD) is False
        assert _skip("garbage", PROBE) is True
        assert _skip("garbage", PROD) is False
