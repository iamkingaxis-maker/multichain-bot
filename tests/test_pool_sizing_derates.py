"""Pool sizing de-rates (2026-06-02 fleet-mine): _apply_pool_sizing_derates.
Cap-respecting positive selection — size only goes DOWN; smart-money cohort exempt.
- capped top10_holder_pct<50 -> x0.5
- concurrent>1 (open_count>=1) AND down-regime (sol_pc_h6<0 or btc_pc_h1<0) -> x0.5
- smart_wallet_count_total>=1 AND 5m_red_count>=6 AND net_flow_15s_usd>0 -> EXEMPT (full)
- fail-open on missing features
"""
import asyncio
import types
from feeds.dip_scanner import DipScanner


def _ds(holder=None):
    ds = DipScanner.__new__(DipScanner)
    async def _hf(addr):
        return holder
    ds._holder_features_cached = _hf
    return ds


def _bundle(meta=None, sol6=None, btc1=None):
    return types.SimpleNamespace(raw_meta=meta or {}, sol_pc_h6=sol6, btc_pc_h1=btc1)


def _pm(open_count=0):
    return types.SimpleNamespace(open_count=open_count,
                                 config=types.SimpleNamespace(pool_sizing_derates_enabled=True))


def _dec():
    return types.SimpleNamespace(address="addr", token="T")


def _run(ds, pm, bundle, size=100.0):
    return asyncio.run(ds._apply_pool_sizing_derates(_dec(), pm, bundle, size))


def test_smartmoney_cohort_exempt_full_size():
    # smart-money compound matches -> full size even with low concentration + concurrent/regime
    ds = _ds(holder={"top10_holder_pct": 20.0})
    b = _bundle({"smart_wallet_count_total": 2, "5m_red_count": 7, "net_flow_15s_usd": 50}, sol6=-1.0)
    size, tag = _run(ds, _pm(open_count=3), b)
    assert size == 100.0 and tag == "smartmoney_full"


def test_concentration_derate_half():
    ds = _ds(holder={"top10_holder_pct": 40.0})  # <50
    size, tag = _run(ds, _pm(open_count=0), _bundle({}, sol6=0.5))  # regime up, no concurrent
    assert size == 50.0 and tag == "conc<50"


def test_concentration_capped_drops_lp_artifact():
    # 163% (LP-accounting artifact) caps to 100 -> NOT <50 -> no de-rate
    ds = _ds(holder={"top10_holder_pct": 163.0})
    size, tag = _run(ds, _pm(open_count=0), _bundle({}, sol6=0.5))
    assert size == 100.0 and tag == "none"


def test_concurrent_regime_derate_half():
    ds = _ds(holder={"top10_holder_pct": 70.0})  # high conc, no de-rate from that
    size, tag = _run(ds, _pm(open_count=1), _bundle({}, sol6=-0.5))  # 2nd open + down regime
    assert size == 50.0 and tag == "conc_regime"


def test_concurrent_no_derate_in_up_regime():
    ds = _ds(holder={"top10_holder_pct": 70.0})
    size, tag = _run(ds, _pm(open_count=2), _bundle({}, sol6=0.5, btc1=0.5))  # regime UP
    assert size == 100.0 and tag == "none"


def test_both_derates_stack_quarter():
    ds = _ds(holder={"top10_holder_pct": 30.0})        # <50
    size, tag = _run(ds, _pm(open_count=1), _bundle({}, btc1=-0.3))  # concurrent + btc down
    assert size == 25.0 and "conc<50" in tag and "conc_regime" in tag


def test_missing_holder_fails_open():
    ds = _ds(holder=None)  # holder fetch returned nothing
    size, tag = _run(ds, _pm(open_count=0), _bundle({}, sol6=0.5))
    assert size == 100.0 and tag == "none"
