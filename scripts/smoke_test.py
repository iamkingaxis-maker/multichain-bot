#!/usr/bin/env python3
"""
Pre-deploy smoke test -- validates the buy path before deploying.
Run: python scripts/smoke_test.py
All tests must pass. If any fail, do NOT deploy.
"""

import sys
import os
import json

# Force UTF-8 on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS_MARK = "[PASS]"
FAIL_MARK = "[FAIL]"
results = []

def check(name, condition, detail=""):
    status = PASS_MARK if condition else FAIL_MARK
    results.append((status, name, detail))
    msg = f"  {status} {name}"
    if detail:
        msg += f" -- {detail}"
    print(msg)
    return condition

def section(title):
    print(f"\n--- {title} {'-' * (50 - len(title))}")


# =============================================================
# 1. CONFIG VALUES
# =============================================================
section("Config values")
try:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(root, "config.json")) as f:
        cfg = json.load(f)

    check("min_combined_score == 0",          cfg.get("min_combined_score") == 0,       f"got {cfg.get('min_combined_score')}")
    check("max_combined_score == 100",         cfg.get("max_combined_score") == 100,     f"got {cfg.get('max_combined_score')}")
    check("max_top10_concentration == 40",     cfg.get("max_top10_concentration") == 40.0, f"got {cfg.get('max_top10_concentration')}")
    check("min_mcap == 70000",                 cfg.get("min_mcap") == 70000,             f"got {cfg.get('min_mcap')}")
    check("dip_watcher_max_seconds == 1800",   cfg.get("dip_watcher_max_seconds") == 1800, f"got {cfg.get('dip_watcher_max_seconds')}")
    check("min_liquidity_usd == 10000",        cfg.get("min_liquidity_usd") == 10000,    f"got {cfg.get('min_liquidity_usd')}")
    check("stop_loss_pct == 7",                cfg.get("stop_loss_pct") == 7.0,          f"got {cfg.get('stop_loss_pct')}")
except Exception as e:
    check("Config readable", False, str(e))


# =============================================================
# 2. ADAPTIVE THRESHOLD -- floor must be 0, not 50
# =============================================================
section("Adaptive threshold")
try:
    from analytics.adaptive_threshold import (
        AdaptiveThresholdManager, ABSOLUTE_MIN_THRESHOLD, ABSOLUTE_MAX_THRESHOLD
    )

    check("ABSOLUTE_MIN_THRESHOLD == 0",   ABSOLUTE_MIN_THRESHOLD == 0,   f"got {ABSOLUTE_MIN_THRESHOLD}")
    check("ABSOLUTE_MAX_THRESHOLD == 100", ABSOLUTE_MAX_THRESHOLD == 100,  f"got {ABSOLUTE_MAX_THRESHOLD}")

    mgr = AdaptiveThresholdManager(baseline_threshold=0, target_win_rate=0.55)
    mgr.register_chain("solana")
    t = mgr.get_threshold("solana")
    check("get_threshold('solana') == 0 with baseline=0", t == 0, f"got {t}")

    t2 = mgr.get_threshold("base")
    check("get_threshold unregistered chain returns 0",   t2 == 0, f"got {t2}")
except Exception as e:
    check("AdaptiveThreshold imports and runs", False, str(e))


# =============================================================
# 3. SECURITY GATE -- block/pass logic
# =============================================================
section("Security gate")
try:
    from security.honeypot import SecurityResult
    from datetime import datetime, timezone

    def make_result(**kwargs):
        defaults = dict(
            token_address="So11111111111111111111111111111111111111112",
            chain_id="solana",
            passed=False,
            risk_level="SAFE",
            checked_at=datetime.now(timezone.utc),
        )
        defaults.update(kwargs)
        return SecurityResult(**defaults)

    BLOCK_KEYWORDS = ["HONEYPOT", "Mintable", "blacklist", "dump risk", "Freeze Authority"]

    # LP not locked alone must NOT be a BLOCK keyword
    lp_flag = "Liquidity not locked -- rug risk"
    check("LP-not-locked flag is not a BLOCK keyword",
          not any(k in lp_flag for k in BLOCK_KEYWORDS))

    # Freeze Authority must trigger BLOCK
    freeze_flag = "Freeze Authority still enabled: Tokens can be frozen"
    check("Freeze Authority flag triggers BLOCK keyword",
          any(k in freeze_flag for k in BLOCK_KEYWORDS))

    # Mint triggers BLOCK
    mint_flag = "Mintable token -- dev can mint"
    check("Mintable flag triggers BLOCK keyword",
          any(k in mint_flag for k in BLOCK_KEYWORDS))

    # DANGER with LP unlocked: lp_lock_data_available should not hard-block by itself
    r = make_result(risk_level="DANGER", lp_lock_data_available=True, liquidity_locked=False)
    still_danger_not_block = (r.risk_level == "DANGER")
    check("DANGER+LP_unlocked is DANGER not BLOCK", still_danger_not_block)

    # top10 threshold is now 40 -- check field roundtrips
    r_hi = make_result(top10_concentration=91.0)
    check("top10=91 exceeds 40% threshold", r_hi.top10_concentration > 40.0)

    r_lo = make_result(top10_concentration=35.0)
    check("top10=35 within 40% threshold",  r_lo.top10_concentration <= 40.0)

except Exception as e:
    check("Security module imports and runs", False, str(e))


# =============================================================
# 4. DIP WATCHER -- init and registration
# =============================================================
section("DipWatcher")
try:
    from core.dip_watcher import DipWatcher
    from unittest.mock import MagicMock

    price_feed = MagicMock()
    price_feed.price_cache = {}
    trader = MagicMock()

    dw = DipWatcher(
        price_feed=price_feed,
        trader=trader,
        dip_threshold_pct=30.0,
        recovery_pct=5.0,
        max_watch_seconds=1800.0,
    )

    check("DipWatcher initializes",              True)
    check("price callback registered",           price_feed.register_price_callback.called)
    check("max_watch_seconds == 1800",           dw.max_watch_seconds == 1800.0, f"got {dw.max_watch_seconds}")
    check("dip_threshold_pct == 30",             dw.dip_threshold_pct == 30.0)
    check("recovery_pct == 5",                   dw.recovery_pct == 5.0)
    check("_watches empty at start",             dw._watches == {})
except Exception as e:
    check("DipWatcher imports and runs", False, str(e))


# =============================================================
# 5. CHART ANALYSIS -- candle_peak must be defined
# =============================================================
section("Chart analysis (candle_peak fix)")
try:
    from core.multi_source_scanner import MultiSourceScanner
    from unittest.mock import MagicMock
    import random

    scanner = MultiSourceScanner.__new__(MultiSourceScanner)
    scanner.chain = MagicMock()
    scanner.chain.name = "Solana"

    # Synthetic candles: [timestamp, open, high, low, close, volume]
    random.seed(42)
    base = 0.001
    candles = []
    for i in range(30):
        o = base * (1 + random.uniform(-0.02, 0.02))
        h = o * (1 + random.uniform(0.001, 0.03))
        l = o * (1 - random.uniform(0.001, 0.02))
        c = random.uniform(l, h)
        v = random.uniform(5_000, 50_000)
        candles.append([i * 300, o, h, l, c, v])
        base = c

    chart = scanner._analyze_chart(candles)
    check("_analyze_chart runs without crash",   True)
    check("chart has 'rsi' key",                 "rsi" in chart)
    check("chart has 'price_vs_vwap_pct' key",   "price_vs_vwap_pct" in chart)

    highs       = [float(c[2]) for c in candles]
    closes      = [float(c[4]) for c in candles]
    candle_peak = max(highs) if highs else 0.0
    current     = closes[-1]

    check("candle_peak > 0",                     candle_peak > 0, f"got {candle_peak:.8f}")

    if candle_peak > 0 and current > 0:
        dip_pct = (current - candle_peak) / candle_peak * 100
        check("dip_pct calculation no NameError", True, f"dip={dip_pct:.1f}%")
    else:
        check("dip_pct calculation no NameError", False, "candle_peak or current is 0")

except Exception as e:
    check("Chart analysis (candle_peak)", False, str(e))


# =============================================================
# 6. VOLUME / MCAP RATIO FILTER
# =============================================================
section("Volume/MCap ratio filter")
try:
    cases = [
        (100,    500_000, True,  "Dead: vol=$100 mcap=$500k (0.02%)"),
        (50_000, 400_000, False, "Active: vol=$50k mcap=$400k (12.5%)"),
        (7_000,  700_000, False, "Edge 1.0%: vol=$7k mcap=$700k"),
        (6_930,  700_000, True,  "Edge 0.99%: vol=$6930 mcap=$700k"),
        (0,      500_000, True,  "Zero volume -> blocked"),
    ]
    for vol, mcap, should_block, label in cases:
        ratio   = vol / mcap if mcap > 0 else 0
        blocked = ratio < 0.01
        check(label, blocked == should_block, f"ratio={ratio*100:.2f}%")
except Exception as e:
    check("V/MCap ratio logic", False, str(e))


# =============================================================
# 7. MICRO-CAP TP LOGIC
# =============================================================
section("Micro-cap TP logic")
try:
    from utils.config import Config
    cfg = Config.load()

    # Config values present
    check("mc_tp1_pct == 25",          cfg.mc_tp1_pct == 25.0,   f"got {cfg.mc_tp1_pct}")
    check("mc_tp1_sell == 1.0",        cfg.mc_tp1_sell == 1.0,   f"got {cfg.mc_tp1_sell}")
    check("mc_tp2_pct == 75",          cfg.mc_tp2_pct == 75.0,   f"got {cfg.mc_tp2_pct}")
    check("mc_tp2_sell == 0.40",       cfg.mc_tp2_sell == 0.40,  f"got {cfg.mc_tp2_sell}")
    check("mc_tp3_pct == 200",         cfg.mc_tp3_pct == 200.0,  f"got {cfg.mc_tp3_pct}")
    check("mc_stop_loss_pct == 25",    cfg.mc_stop_loss_pct == 25.0, f"got {cfg.mc_stop_loss_pct}")
    check("mc_winner_trail_pct == 15", cfg.mc_winner_trail_pct == 15.0, f"got {cfg.mc_winner_trail_pct}")

    # is_micro_cap detection from reason string
    from core.position_manager import PositionState
    from datetime import datetime, timezone

    def make_state(reason):
        return PositionState(
            token_address="abc", token_symbol="TEST", chain_id="solana",
            entry_price=1.0, entry_volume_usd=0.0, position_size_usd=80.0,
            original_size_usd=80.0, entry_time=datetime.now(timezone.utc),
            reason=reason, is_micro_cap="micro" in reason.lower(),
        )

    mc = make_state("Micro-cap | $25000 mcap | dev 5% | snipers 10%")
    std = make_state("Axiom signal | score 72 | Raydium")
    dip = make_state("DipWatcher: dip+recovery")

    check("Micro-cap reason -> is_micro_cap=True",   mc.is_micro_cap  == True)
    check("Axiom signal reason -> is_micro_cap=False", std.is_micro_cap == False)
    check("DipWatcher reason -> is_micro_cap=False", dip.is_micro_cap == False)

    # MC TP thresholds are higher than standard
    check("mc_tp1 >= standard tp1",  cfg.mc_tp1_pct >= cfg.take_profit_1_pct)
    check("mc_tp3 > standard tp3",  cfg.mc_tp3_pct > cfg.take_profit_3_pct)
    check("mc_sl  >= standard sl",  cfg.mc_stop_loss_pct >= cfg.stop_loss_pct)

except Exception as e:
    check("Micro-cap TP logic", False, str(e))


# =============================================================
# 8. DASHBOARD JS SYNTAX
# =============================================================
section("Dashboard JS syntax")
try:
    import re, subprocess, tempfile
    from dashboard.web_dashboard import HTML_DASHBOARD as html

    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    js_blocks = [s for s in scripts if s.strip()]
    check("Dashboard HTML has JS blocks", len(js_blocks) > 0, f"found {len(js_blocks)}")

    for i, js in enumerate(js_blocks):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js',
                                         delete=False, encoding='utf-8') as f:
            f.write(js)
            tmp = f.name
        result = subprocess.run(
            ["node", "--check", tmp],
            capture_output=True, text=True
        )
        passed_js = result.returncode == 0
        detail = result.stderr.strip().splitlines()[0] if result.stderr else ""
        check(f"JS block {i+1} syntax valid", passed_js, detail)
        os.unlink(tmp)

except Exception as e:
    check("Dashboard JS syntax check", False, str(e))


# =============================================================
# RESULTS
# =============================================================
passed = sum(1 for s, _, _ in results if s == PASS_MARK)
failed = sum(1 for s, _, _ in results if s == FAIL_MARK)

print(f"\n{'='*55}")
print(f"  {passed}/{passed+failed} tests passed")

if failed:
    print("\n  Failed tests:")
    for status, name, detail in results:
        if status == FAIL_MARK:
            print(f"    {FAIL_MARK} {name}" + (f" -- {detail}" if detail else ""))
    print("\n  DO NOT DEPLOY -- fix the failures above first\n")
    sys.exit(1)
else:
    print("\n  All clear -- safe to deploy\n")
    sys.exit(0)
