#!/usr/bin/env python3
"""rh_fidelity_reconcile.py — RH live-vs-paper FIDELITY GAP (the SOL playbook,
finally ported to Robinhood Chain). MEASUREMENT ONLY: read-only, no trading, no
deploy.

WHY THIS EXISTS (2026-07-14): RH paper booked SELLS at the QuoterV2 quote without
checking whether a real sell would execute — so honeypot / rug tokens showed as
small-loss "wins" while live reverted to total losses. Paper reported ~-$5 while
the wallet lost ~$56. Same simulated-P&L illusion we killed on Solana; it was
never ported. This tool makes the honest number un-ignorable:

  HONEST live P&L = (ETH + WETH + held meme tokens MARKED TO THEIR REAL SELL
                     QUOTE, off-limits personal tokens excluded)
                    - (go-live baseline + net deposits - net withdrawals)

and prints it next to the lane's paper-booked P&L so the FIDELITY GAP is loud.
A held token whose real sell quote is ~0 (honeypot/rug) is worth ~0 here — no
quote-price illusion. Run it every RH session (wallet-truth-first discipline).

Usage:
  python scripts/rh_fidelity_reconcile.py --baseline-usd 75 --net-deposit 0 \
         [--paper-booked-usd -4.83]
"""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys

WALLET = os.environ.get("RH_WALLET_ADDRESS",
                        "0xa454C67853A5Ac88Ad45af9E9A41870F30039c05")
BS = os.environ.get("RH_BLOCKSCOUT_BASE",
                    "https://robinhoodchain.blockscout.com").rstrip("/")
# Off-limits personal holdings — NEVER counted as bot P&L (memory rule).
OFF_LIMITS_SYMBOLS = {"GFOF"}
OFF_LIMITS_SUBSTR = ("cmoon",)  # Cmoon personal token


def _curl_json(url: str):
    r = subprocess.run(["curl", "-s", "--max-time", "25", url],
                       capture_output=True, text=True)
    return json.loads(r.stdout)


def eth_usd() -> float:
    # cheap: reuse the lane's price if present, else a conservative default.
    try:
        return float(os.environ.get("RH_ETH_USD", "1800"))
    except Exception:
        return 1800.0


def wallet_eth() -> float:
    d = _curl_json(f"{BS}/api/v2/addresses/{WALLET.lower()}")
    return int(d.get("coin_balance") or 0) / 1e18


def held_tokens():
    d = _curl_json(f"{BS}/api/v2/addresses/{WALLET.lower()}/tokens?type=ERC-20")
    out = []
    for t in d.get("items", []):
        tok = t.get("token", {}) or {}
        sym = tok.get("symbol") or "?"
        addr = tok.get("address_hash") or tok.get("address")
        dec = int(tok.get("decimals") or 18)
        raw = int(t.get("value") or 0)
        if raw <= 0 or not addr:
            continue
        if sym in OFF_LIMITS_SYMBOLS or any(s in sym.lower()
                                            for s in OFF_LIMITS_SUBSTR):
            continue  # AxiS personal — never counted
        out.append({"sym": sym, "addr": addr, "dec": dec, "raw": raw})
    return out


def real_sell_value_eth(addr: str, raw_amount: int) -> float:
    """Mark a held token to its REAL sell quote (QuoterV2). Honeypot/rug -> ~0.
    This is the anti-illusion core: value is what we could ACTUALLY sell for,
    not a mid-price mark."""
    try:
        from core.rh_execution import RhExecutor
        q = RhExecutor().quote_sell(addr, raw_amount)
        out = getattr(q, "amount_out", None)
        return (out / 1e18) if out else 0.0
    except Exception:
        return 0.0  # cannot quote -> treat as unsellable (conservative, honest)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline-usd", type=float, required=True,
                    help="wallet USD value at go-live (before this session's trading)")
    ap.add_argument("--net-deposit", type=float, default=0.0,
                    help="deposits minus withdrawals during the session (USD)")
    ap.add_argument("--paper-booked-usd", type=float, default=None,
                    help="the lane's paper-booked realized P&L for the live bot(s)")
    a = ap.parse_args()

    px = eth_usd()
    eth = wallet_eth()
    eth_usd_val = eth * px
    toks = held_tokens()
    tok_lines, tok_usd = [], 0.0
    for t in toks:
        sell_eth = real_sell_value_eth(t["addr"], t["raw"])
        v = sell_eth * px
        tok_usd += v
        tok_lines.append(f"    {t['sym']:10} real-sell-value=${v:8.2f}"
                         f"  ({'DEAD/honeypot ~0' if v < 0.5 else 'live'})")

    honest_now = eth_usd_val + tok_usd
    honest_pnl = honest_now - (a.baseline_usd + a.net_deposit)

    print("=== RH FIDELITY RECONCILE (on-chain truth, honeypot-marked) ===")
    print(f"  ETH+WETH:                 ${eth_usd_val:8.2f}  ({eth:.6f} ETH @ ${px:.0f})")
    print(f"  held meme tokens (real sell value, personal excluded): ${tok_usd:8.2f}")
    for l in tok_lines:
        print(l)
    print(f"  --------")
    print(f"  HONEST wallet value now:  ${honest_now:8.2f}")
    print(f"  baseline+deposits:        ${a.baseline_usd + a.net_deposit:8.2f}")
    print(f"  ==> HONEST live P&L:      ${honest_pnl:+8.2f}   <== the only real number")
    if a.paper_booked_usd is not None:
        gap = a.paper_booked_usd - honest_pnl
        print(f"\n  lane PAPER-BOOKED P&L:    ${a.paper_booked_usd:+8.2f}")
        print(f"  ==> FIDELITY GAP:         ${gap:+8.2f}  "
              f"(paper OVER-counts by ${gap:+.2f})")
        if abs(gap) > 3:
            print("  !! PAPER IS LYING -- do NOT trust paper rankings until the "
                  "lane books unsellable sells at 0. Apply this gap as a haircut.")


if __name__ == "__main__":
    main()
