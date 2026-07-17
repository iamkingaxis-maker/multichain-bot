#!/usr/bin/env python3
"""rh_slippage_study.py — retro slippage measurement from ON-CHAIN receipts.

RH live rows never recorded fill-vs-quote (`fill_vs_quote_pct: null` on every
live swap — 07-15 audit finding #5), so paper's costless-fill assumption has
never been checked against reality. But every swap's amountOutMinimum is in
the calldata, and minimum = quote x (1 - slippage_bps). So from receipts:

  MARGIN (assumption-free)  = actual_out / amountOutMinimum - 1
      0% = filled at the worst tolerated price; ~bps% = filled at the quote.
  IMPLIED SLIP vs quote     = actual_out / (min / (1 - bps)) - 1
      (assumes the default 300bps ceiling; the one 1000bps orphan sell is
       flagged; direction is what matters, not the 3rd decimal)

Reads the live wallet's from-txs off Blockscout, decodes exactInputSingle /
multicall calldata locally (eth_abi), takes actual fills from token-transfer
logs. Read-only, keyless, stdlib+eth_abi. Paced for Blockscout politeness.
"""
from __future__ import annotations
import json
import statistics as st
import sys
import time
import urllib.request

from eth_abi import decode as abi_decode

import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.rh_execution import WETH9 as _WETH9_CS  # the real chain constant

WALLET = "0xa454C67853A5Ac88Ad45af9E9A41870F30039c05"
WETH9 = str(_WETH9_CS).lower()
BS = "https://robinhoodchain.blockscout.com/api/v2"
SEL_EXACT_INPUT_SINGLE = "04e45aaf"    # SwapRouter02 (no deadline)
SEL_MULTICALL_DEADLINE = "5ae401dc"    # multicall(uint256,bytes[])
SEL_MULTICALL_PLAIN = "ac9650d8"       # multicall(bytes[]) — the lane's sells
SEL_UNWRAP = "49404b7c"                # unwrapWETH9(uint256,address)
DEFAULT_BPS = 300


LEGACY = "https://robinhoodchain.blockscout.com/api"


def _get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "rh-bot"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode())
        except Exception:
            if i == tries - 1:
                raise
            time.sleep(1.5 * (i + 1))        # transient 500/429 backoff


def _legacy_txs():
    """All from-wallet txs via the LEGACY API — one call, includes full input
    calldata + isError (the v2 tx-list endpoint was 500ing during the study;
    legacy is a separate code path AND removes ~340 per-tx detail calls)."""
    d = _get(f"{LEGACY}?module=account&action=txlist&address={WALLET}"
             f"&sort=asc")
    r = d.get("result")
    return [t for t in (r if isinstance(r, list) else [])
            if (t.get("from") or "").lower() == WALLET.lower()]


def _legacy_token_transfers():
    """hash -> [(token_addr, to, value_int)] via one tokentx call."""
    d = _get(f"{LEGACY}?module=account&action=tokentx&address={WALLET}"
             f"&sort=asc")
    out = {}
    for x in (d.get("result") or []):
        out.setdefault(x.get("hash"), []).append(
            ((x.get("contractAddress") or "").lower(),
             (x.get("to") or "").lower(),
             int(x.get("value") or 0)))
    return out


def _legacy_internal_eth_in():
    """hash -> native ETH received by the wallet (int wei) — the SELL
    proceeds: unwrapWETH9 pays out via an INTERNAL transfer (router->wallet),
    which never appears in tokentx (the WETH legs move pool->router->burn
    without touching the wallet). One txlistinternal call covers all sells."""
    d = _get(f"{LEGACY}?module=account&action=txlistinternal"
             f"&address={WALLET}&sort=asc")
    out = {}
    for x in (d.get("result") or []):
        if (x.get("to") or "").lower() == WALLET.lower():
            h = x.get("transactionHash") or x.get("hash")
            out[h] = out.get(h, 0) + int(x.get("value") or 0)
    return out


def _decode_swap_legs(raw: str):
    """exactInputSingle legs [(tokenIn, tokenOut, amountIn, amountOutMin)]
    from raw calldata (direct or inside multicall)."""
    raw = raw[2:] if raw.startswith("0x") else raw
    sel, body = raw[:8], bytes.fromhex(raw[8:])
    if sel == SEL_EXACT_INPUT_SINGLE:
        p = abi_decode(
            ["(address,address,uint24,address,uint256,uint256,uint160)"],
            body)[0]
        return [(p[0].lower(), p[1].lower(), int(p[4]), int(p[5]))]
    if sel == SEL_MULTICALL_DEADLINE:
        _, calls = abi_decode(["uint256", "bytes[]"], body)
        legs = []
        for c in calls:
            legs.extend(_decode_swap_legs("0x" + c.hex()))
        return legs
    if sel == SEL_MULTICALL_PLAIN:
        (calls,) = abi_decode(["bytes[]"], body)
        legs = []
        for c in calls:
            legs.extend(_decode_swap_legs("0x" + c.hex()))
        return legs
    return []                                # unwrap/approve/etc: not a swap


def main():
    alltx = _legacy_txs()
    ok = [t for t in alltx if t.get("isError") in ("0", 0, None)]
    fails = [t for t in alltx if t.get("isError") in ("1", 1)]
    transfers = _legacy_token_transfers()
    eth_in = _legacy_internal_eth_in()
    print(f"from-txs: {len(alltx)} (ok {len(ok)}, FAILED {len(fails)} = "
          f"{100*len(fails)/max(1,len(alltx)):.1f}% failed-tx rate)")
    rows = []
    for t in ok:
        h = t.get("hash")
        try:
            legs = _decode_swap_legs(t.get("input") or "")
            if not legs:
                continue
            (tin, tout, amt_in, amt_min) = legs[0]     # one swap leg per tx
            if amt_min <= 0:
                continue
            tt = transfers.get(h) or []
            actual = None
            if tout == WETH9:
                side = "sell"
                # actual = native ETH received via the unwrap internal tx
                # (WETH legs never touch the wallet, see _legacy_internal_eth_in)
                actual = eth_in.get(h)
                # direct-WETH fallback (no unwrap in the call chain)
                if not actual:
                    for tok, to, val in tt:
                        if tok == WETH9 and to == WALLET.lower():
                            actual = val
                            break
            else:
                side = "buy"
                for tok, to, val in tt:
                    if tok == tout and to == WALLET.lower():
                        actual = val
                        break
            if not actual:
                continue
            margin = actual / amt_min - 1.0
            quote = amt_min / (1 - DEFAULT_BPS / 1e4)
            slip = actual / quote - 1.0
            rows.append({"ts": t.get("timeStamp"), "side": side,
                         "margin_pct": round(100 * margin, 4),
                         "slip_vs_quote_pct": round(100 * slip, 4),
                         "hash": h})
        except Exception as e:
            print(f"  skip {str(h)[:14]}: {type(e).__name__}: {str(e)[:60]}")

    print(f"\ndecoded fills: {len(rows)}")
    for side in ("buy", "sell"):
        s = [r for r in rows if r["side"] == side]
        if not s:
            continue
        m = [r["margin_pct"] for r in s]
        q = [r["slip_vs_quote_pct"] for r in s]
        m.sort(); q.sort()
        print(f"\n{side.upper()} n={len(s)}")
        print(f"  margin over amountOutMinimum (assumption-free): "
              f"median {st.median(m):+.3f}%  p25 {m[len(m)//4]:+.3f}%  "
              f"p10 {m[len(m)//10]:+.3f}%  worst {m[0]:+.3f}%")
        print(f"  implied slip vs quote (bps=300 assumption):     "
              f"median {st.median(q):+.3f}%  mean {st.mean(q):+.3f}%  "
              f"worst {q[0]:+.3f}%")
        near_floor = sum(1 for x in m if x < 0.5)
        print(f"  fills within 0.5% of the minimum (near worst-case): "
              f"{near_floor}/{len(s)}")
    out = "scratchpad/_rh_slippage_study.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=1)
    print(f"\nrows -> {out}")


if __name__ == "__main__":
    main()
