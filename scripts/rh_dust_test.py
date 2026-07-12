# scripts/rh_dust_test.py
"""RH DUST TEST — the sell-path-first go-live step (2026-07-12).

The 07-10 incident rule: buys-working is NEVER evidence sells work — the
sell path must be proven END-TO-END with real money BEFORE any strategy buy.
This script, with the triple gate OPEN (RH_LIVE_CONFIRMED=true +
RH_PAPER_MODE=false + RH_PRIVATE_KEY):

  1. reads wallet-truth (native ETH + WETH) — proves balance reads AND arms
     the live baseline,
  2. BUYS ~$2 (RH_DUST_USD / --usd) of the deepest-liquidity WETH-pool token
     it can find (honeypot-checked; or --token 0x… to pin one),
  3. waits for the receipt (RhExecutor confirms in-line),
  4. SELLS IT ALL BACK,
  5. reads wallet-truth again and prints the round-trip cost,
  6. prints BOTH legs' full fill-time telemetry (decision->landed ms,
     fill-vs-quote pct, gas) and appends them to rh_live_fills.jsonl.

Exit codes: 0 = both legs landed + wallet-truth read both times.
  2 gate closed | 3 wallet-truth (before) failed | 4 no target token |
  5 buy failed | 6 sell failed | 7 wallet-truth (after) failed.

--dry-run: mocked executor + wallet truth, NO gate needed, NO network —
exercises the full flow/printing/exit-code logic (the test suite runs this).

NEVER requires the key at import; env is read at call time by the gate.

Usage:
  python scripts/rh_dust_test.py [--usd 2.0] [--token 0x...] [--dry-run]
                                 [--lookback-h 6]
"""
import argparse
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from core import rh_live_execution as rh_live  # noqa: E402

OUT_DIR = os.path.join("scratchpad", "robinhood_tapes")
LIVE_FILLS = os.path.join(OUT_DIR, "rh_live_fills.jsonl")
DEFAULT_USD = float(os.environ.get("RH_DUST_USD", "2.0"))
MIN_TARGET_LIQ_USD = 50_000.0     # dust target floor: deep books only


def _append(path, rec):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":"), default=str) + "\n")


def leg_report(name: str, rec: dict, t_decide: float, t_sent: float,
               t_landed: float) -> dict:
    """One leg's fill-time telemetry (same field names as the lane's
    fill_telemetry). Pure; prints + returns the dict."""
    tel = {
        "leg": name,
        "decision_ts": t_decide,
        "order_sent_ts": t_sent,
        "landed_wall_ts": t_landed,
        "decision_to_landed_ms": round((t_landed - t_decide) * 1000.0, 1),
        "exec_latency_ms": rec.get("total_latency_ms"),
        "fill_vs_quote_pct": rec.get("fill_vs_mid_slippage_pct"),
        "gas_cost_eth": rec.get("gas_cost_eth"),
        "route": rec.get("route"),
        "fee_tier": rec.get("fee_tier"),
        "tx": rec.get("tx_signature"),
    }
    print(f"  [{name}] decision->landed {tel['decision_to_landed_ms']}ms "
          f"(exec {tel['exec_latency_ms']}ms) "
          f"fill_vs_quote {tel['fill_vs_quote_pct']}% "
          f"gas {tel['gas_cost_eth']} ETH tier {tel['fee_tier']} "
          f"tx {tel['tx']}", flush=True)
    return tel


def pick_deepest_token(lookback_h: float = 6.0):
    """Deepest-liq WETH-pool token among recent pool creations: backfill the
    factory logs, batch WETH.balanceOf over every candidate pool (liq ≈
    2 * WETH side, the feed's own formula), rank desc, return the first
    token whose sell path passes the honeypot simulation.
    Returns (token, pool, liq_usd, eth_price) or None."""
    from rh_chain_feed import Feed, WETH, SEL_BALANCE_OF, RPC_DEFAULT
    from core.rh_honeypot import simulate_sell

    feed = Feed(os.environ.get("RH_FEED_RPC", RPC_DEFAULT))
    feed.sync_head()
    feed.refresh_eth_price()
    if not feed.eth_price:
        print("no ETH/USD price from the feed", flush=True)
        return None
    feed.backfill_discovery(int(lookback_h * 3600 / max(feed.spb, 0.02)))
    pools = list(feed.cand.items())
    if not pools:
        print("no candidate pools discovered", flush=True)
        return None
    print(f"ranking {len(pools)} candidate pools by WETH depth...",
          flush=True)
    liq = {}
    CHUNK = 150
    for i in range(0, len(pools), CHUNK):
        chunk = pools[i:i + CHUNK]
        res = feed.rpc.batch([
            ("eth_call", [{"to": WETH,
                           "data": SEL_BALANCE_OF + "0" * 24 + p[2:]},
                          "latest"]) for p, _ in chunk])
        for j, (p, _c) in enumerate(chunk):
            r = res.get(j)
            if r:
                try:
                    liq[p] = int(r, 16) / 1e18 * 2.0 * feed.eth_price
                except (ValueError, TypeError):
                    pass
    from core.rh_execution import RhExecutor
    ex = RhExecutor()          # keyless: honeypot sim is quotes only
    for pool in sorted(liq, key=lambda p: -liq[p]):
        if liq[pool] < MIN_TARGET_LIQ_USD:
            break
        token = feed.cand[pool].get("token")
        if not token:
            continue
        v = simulate_sell(token, executor=ex)
        if v.get("sellable"):
            return token, pool, liq[pool], feed.eth_price
        print(f"  skip {token[:10]} liq=${liq[pool]:,.0f} "
              f"(honeypot: {str(v.get('reason'))[:50]})", flush=True)
    print(f"no sellable pool above ${MIN_TARGET_LIQ_USD:,.0f} WETH depth",
          flush=True)
    return None


class DryRunLive:
    """Offline stand-in for RhLiveExecutor: canned successful records with
    the rh_live_swaps.jsonl shape. Lets the suite drive the WHOLE flow."""

    def __init__(self, fail_leg=None):
        self.fail_leg = fail_leg
        self.calls = []

    def _rec(self, side, amount_out):
        return {"side": side, "tx_signature": "0x" + ("ab" if side == "buy"
                                                      else "cd") * 32,
                "amount_out": amount_out, "quoted_out": amount_out,
                "real_fill_price": 1e-6, "decision_mid_price": 1e-6,
                "fill_vs_mid_slippage_pct": -0.4, "gas_cost_eth": 1.6e-6,
                "total_latency_ms": 420.0, "route": "uniswap_v3_direct",
                "fee_tier": 10000, "success": True}

    def live_buy(self, token, usd, eth_price, **kw):
        self.calls.append(("buy", token, usd))
        if self.fail_leg == "buy":
            raise rh_live.RhSwapError("dry-run forced buy failure tx=None")
        return self._rec("buy", 10 ** 18)

    def live_sell(self, token, amount, **kw):
        self.calls.append(("sell", token, amount))
        if self.fail_leg == "sell":
            raise rh_live.RhSwapError("dry-run forced sell failure tx=None")
        return self._rec("sell", int(0.00097e18))


def _dry_truth(**_kw):
    return {"ok": True, "wallet": "0xDRY…RUN", "eth_now": 0.01,
            "weth_now": 0.0, "total_eth": 0.01, "delta_eth": 0.0}


def run(argv=None, live=None, truth_fn=None, pick_fn=None) -> int:
    ap = argparse.ArgumentParser(description="RH dust test (sell-path-first)")
    ap.add_argument("--usd", type=float, default=DEFAULT_USD)
    ap.add_argument("--token", default=None)
    ap.add_argument("--lookback-h", type=float, default=6.0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if args.dry_run:
        live = live or DryRunLive()
        truth_fn = truth_fn or _dry_truth
        token, eth_price = args.token or "0x" + "d" * 40, 4000.0
        print("DRY RUN — mocked executor, no gate, no network", flush=True)
    else:
        ok, reason = rh_live.rh_live_gate()
        if not ok:
            print(f"REFUSED: {reason}", flush=True)
            print("The dust test moves real money — open the triple gate "
                  "first (see core/rh_live_execution.py docstring).",
                  flush=True)
            return 2
        live = live or rh_live.RhLiveExecutor()
        truth_fn = truth_fn or rh_live.rh_wallet_truth
        if args.token:
            from rh_chain_feed import Feed, RPC_DEFAULT
            feed = Feed(os.environ.get("RH_FEED_RPC", RPC_DEFAULT))
            feed.sync_head()
            feed.refresh_eth_price()
            token, eth_price = args.token, feed.eth_price
        else:
            picked = (pick_fn or pick_deepest_token)(args.lookback_h)
            if not picked:
                return 4
            token, pool, liq_usd, eth_price = picked
            print(f"target: {token} pool={pool[:10]} "
                  f"liq=${liq_usd:,.0f}", flush=True)
        if not eth_price:
            print("no ETH/USD price — cannot size the dust buy", flush=True)
            return 4

    # ── wallet truth BEFORE (also arms the baseline while the gate is open)
    before = truth_fn()
    print(f"wallet-truth before: {json.dumps(before, default=str)}",
          flush=True)
    if not before.get("ok"):
        print("wallet-truth read FAILED — refusing to trade blind "
              "(2026-07-10 rule: no reads, no money)", flush=True)
        return 3

    # ── BUY leg ──────────────────────────────────────────────────────────
    print(f"BUY ~${args.usd:.2f} of {token[:12]}…", flush=True)
    t_dec = time.time()
    t_sent = time.time()
    try:
        buy_rec = live.live_buy(token, args.usd, eth_price)
    except Exception as e:
        print(f"BUY FAILED: {type(e).__name__}: {e}", flush=True)
        return 5
    t_land = time.time()
    buy_tel = leg_report("dust_buy", buy_rec, t_dec, t_sent, t_land)
    _append(LIVE_FILLS, {"ts": time.time(), "usd": args.usd,
                         "token": token, "dry_run": args.dry_run,
                         **buy_tel})

    # ── SELL leg (ALL of it back — the point of the exercise) ────────────
    print("SELL it all back…", flush=True)
    t_dec = time.time()
    t_sent = time.time()
    try:
        sell_rec = live.live_sell(token, "all")
    except Exception as e:
        print(f"SELL FAILED: {type(e).__name__}: {e}", flush=True)
        print("*** DUST STUCK IN WALLET — sell path NOT proven. Do NOT "
              "arm live buys. Investigate before any strategy trade. ***",
              flush=True)
        return 6
    t_land = time.time()
    sell_tel = leg_report("dust_sell", sell_rec, t_dec, t_sent, t_land)
    _append(LIVE_FILLS, {"ts": time.time(), "token": token,
                         "dry_run": args.dry_run, **sell_tel})

    # ── wallet truth AFTER ───────────────────────────────────────────────
    after = truth_fn()
    print(f"wallet-truth after: {json.dumps(after, default=str)}", flush=True)
    if not after.get("ok"):
        print("post-trade wallet-truth read FAILED — legs landed but the "
              "read path is broken; fix before go-live", flush=True)
        return 7
    try:
        rt_eth = float(after["total_eth"]) - float(before["total_eth"])
        print(f"round-trip cost: {rt_eth:+.8f} ETH "
              f"(~${rt_eth * eth_price:+.4f})", flush=True)
    except Exception:
        pass
    print("DUST TEST PASSED — buy AND sell paths proven end-to-end.",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run())
