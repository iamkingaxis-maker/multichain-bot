"""Robinhood Chain (4663) EVM honeypot / scam-token guard — FAIL-CLOSED.

The EVM guard our Solana code never needed: EVM tokens are arbitrary code, so
before ANY buy we must prove the round trip. Verdict philosophy: UNKNOWN is
NOT SELLABLE — every error / missing pool / revert / unparseable answer
resolves to sellable=False. A skipped good token costs an entry; a honeypot
costs the whole position.

Third-party coverage (probed live 2026-07-09):
  * honeypot.is: chain 4663 answers {"code":400,"error":"Invalid chain"} —
    NOT supported. So this is a pure eth_call simulation, no external API.

Simulation (all read-only eth_calls, no key, no gas):
  1. BUY quote  — QuoterV2 WETH -> token across fee tiers (probe size
     RH_HONEYPOT_PROBE_ETH, default 0.01 ETH). No pool answers -> FAIL.
  2. SELL quote — QuoterV2 token -> WETH of the quoted token amount.
     Revert / zero -> FAIL (pool-level honeypot: one-way liquidity, or a
     fee-on-transfer token, which mechanically REVERTS on Uniswap V3 sells
     because the pool receives less than it is owed).
  3. Round-trip verdict — eth_back vs eth_in beyond the two pool-fee legs is
     attributed to token taxes/toxicity (pure math in verdict_from_round_trip;
     the QuoterV2 legs already include pool fee + price impact, so excess loss
     on a small probe is the token's doing). Excess loss > threshold -> FAIL.
  4. (Post-buy only) live sell eth_call — when `wallet_addr` actually HOLDS
     the token AND has approved SwapRouter02, we eth_call the real sell
     calldata from that wallet: a revert here is a hard honeypot signal
     (blacklists, trading-disabled, tax > minOut). Skipped (with a note in
     `checks`) when there is no balance/allowance to simulate with — steps
     1-3 still gate.

KNOWN LIMIT (documented, not hidden): QuoterV2 does pool math only — it can
NOT see transfer-tax bookkeeping directly. On V3 that is mostly moot (see
step 2: FoT sells revert), and step 4 catches wallet-specific traps after
entry. V2-graduated Robinfun tokens need a V2 simulation — explicit follow-up.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Combined round-trip excess loss (beyond pool fees) above which the token is
# treated as taxed/toxic. 15% combined tax already destroys our edge math.
DEFAULT_MAX_EXCESS_LOSS_PCT = 15.0
DEFAULT_PROBE_ETH = 0.01


def _fee_keep_fraction(fee_bps_buy: Optional[int], fee_bps_sell: Optional[int]) -> float:
    """Fraction of value a clean round trip keeps after the two POOL fee legs.
    Unknown fee tiers assume 1% (the worst common tier) — conservative, i.e.
    attributes LESS loss to the token, so it only ever relaxes the verdict
    threshold by a known bounded amount. Pure."""
    fb = int(fee_bps_buy) if fee_bps_buy else 10_000
    fs = int(fee_bps_sell) if fee_bps_sell else 10_000
    # Uniswap fee units are hundredths of a bip (10000 == 1%).
    return (1.0 - fb / 1_000_000.0) * (1.0 - fs / 1_000_000.0)


def verdict_from_round_trip(eth_in_wei, eth_back_wei,
                            fee_bps_buy: Optional[int] = None,
                            fee_bps_sell: Optional[int] = None,
                            max_excess_loss_pct: float = DEFAULT_MAX_EXCESS_LOSS_PCT) -> dict:
    """PURE verdict from a simulated buy->sell round trip. FAIL-CLOSED:
    non-numeric / non-positive inputs -> sellable=False.

    Returns {sellable, buy_tax_pct, sell_tax_pct, reason,
             round_trip_loss_pct, excess_loss_pct}.
    Excess loss (round-trip loss beyond the two pool-fee legs) is attributed
    to the token and split evenly across buy/sell as a COMBINED-tax estimate
    (QuoterV2 cannot apportion it; the split is reporting, the GATE uses the
    combined number)."""
    try:
        ei = float(eth_in_wei)
        eb = float(eth_back_wei)
    except (TypeError, ValueError):
        return {"sellable": False, "buy_tax_pct": None, "sell_tax_pct": None,
                "reason": "unparseable_round_trip", "round_trip_loss_pct": None,
                "excess_loss_pct": None}
    if ei <= 0 or eb < 0:
        return {"sellable": False, "buy_tax_pct": None, "sell_tax_pct": None,
                "reason": "unparseable_round_trip", "round_trip_loss_pct": None,
                "excess_loss_pct": None}

    keep = eb / ei
    loss_pct = round((1.0 - keep) * 100.0, 4)
    expected_keep = _fee_keep_fraction(fee_bps_buy, fee_bps_sell)
    # keep/expected_keep > 1 just means price impact rounding — clamp at 0.
    excess = max(0.0, (1.0 - keep / expected_keep)) * 100.0
    excess = round(excess, 4)
    tax_each = round(excess / 2.0, 4)
    if excess > float(max_excess_loss_pct):
        return {"sellable": False, "buy_tax_pct": tax_each,
                "sell_tax_pct": tax_each,
                "reason": f"excess_round_trip_loss {excess}% > {max_excess_loss_pct}%",
                "round_trip_loss_pct": loss_pct, "excess_loss_pct": excess}
    return {"sellable": True, "buy_tax_pct": tax_each, "sell_tax_pct": tax_each,
            "reason": "ok", "round_trip_loss_pct": loss_pct,
            "excess_loss_pct": excess}


def _fail(reason: str) -> dict:
    """The FAIL-CLOSED verdict shape."""
    return {"sellable": False, "buy_tax_pct": None, "sell_tax_pct": None,
            "reason": reason}


def simulate_sell(token_addr: str, wallet_addr: Optional[str] = None,
                  executor=None,
                  probe_eth: Optional[float] = None,
                  max_excess_loss_pct: float = DEFAULT_MAX_EXCESS_LOSS_PCT) -> dict:
    """Simulated buy+sell round trip -> honeypot verdict. FAIL-CLOSED: any
    exception, missing pool, or revert returns sellable=False (never raises).

    executor: anything exposing quote_buy / quote_sell / (optionally
    token_balance + a w3 for the post-buy live-sell eth_call). Defaults to a
    lazily-built core.rh_execution.RhExecutor from env (RH_RPC_URL) — network
    is only touched inside this call, so imports stay side-effect free.

    Returns {sellable, buy_tax_pct, sell_tax_pct, reason} plus diagnostic
    extras (probe_eth, fee tiers, round-trip numbers, checks performed).
    """
    try:
        if executor is None:
            from core.rh_execution import RhExecutor
            executor = RhExecutor()
        if probe_eth is None:
            probe_eth = float(os.environ.get("RH_HONEYPOT_PROBE_ETH",
                                             DEFAULT_PROBE_ETH))
        probe_wei = int(probe_eth * 1e18)
        checks = ["buy_quote"]

        # 1. buy quote (WETH -> token)
        buy_q = executor.quote_buy(token_addr, probe_wei)
        if buy_q is None or not getattr(buy_q, "amount_out", 0):
            v = _fail("no_buy_route: no V3 pool quoted WETH->token")
            v["checks"] = checks
            return v

        # 2. sell quote (token -> WETH) of exactly what the buy would return
        checks.append("sell_quote")
        sell_q = executor.quote_sell(token_addr, buy_q.amount_out)
        if sell_q is None or not getattr(sell_q, "amount_out", 0):
            v = _fail("sell_quote_reverted: token->WETH unquotable "
                      "(one-way pool / fee-on-transfer honeypot signature)")
            v["checks"] = checks
            return v

        # 3. round-trip verdict (pure math)
        checks.append("round_trip")
        verdict = verdict_from_round_trip(
            probe_wei, sell_q.amount_out,
            fee_bps_buy=getattr(buy_q, "fee", None),
            fee_bps_sell=getattr(sell_q, "fee", None),
            max_excess_loss_pct=max_excess_loss_pct)

        # 4. post-buy live sell simulation (only when the wallet can prove it)
        if verdict["sellable"] and wallet_addr:
            live = _live_sell_check(executor, token_addr, wallet_addr,
                                    getattr(sell_q, "fee", 3000))
            checks.append(f"live_sell_call:{live}")
            if live == "reverted":
                verdict = _fail("sell_call_reverted: real sell eth_call from "
                                "holder wallet reverted (hard honeypot signal)")

        verdict.update({
            "checks": checks, "probe_eth": probe_eth,
            "buy_fee_tier": getattr(buy_q, "fee", None),
            "sell_fee_tier": getattr(sell_q, "fee", None),
            "quoted_tokens_out": getattr(buy_q, "amount_out", None),
            "quoted_eth_back_wei": getattr(sell_q, "amount_out", None),
        })
        return verdict
    except Exception as e:  # FAIL-CLOSED: unknown -> not sellable
        logger.info("[rh-honeypot] simulation error for %s: %s", token_addr, e)
        return _fail(f"simulation_error: {e}")


def _live_sell_check(executor, token_addr: str, wallet_addr: str,
                     fee: int) -> str:
    """eth_call the REAL SwapRouter02 sell from `wallet_addr` when it holds
    the token (post-buy check). Returns 'ok' | 'reverted' | 'skipped_<why>'.

    FAIL-OPEN to 'skipped': no balance / no allowance is the WALLET's state,
    not evidence about the token — steps 1-3 already gated. A revert WITH
    balance+allowance present is a hard fail (caller turns it into
    sellable=False)."""
    try:
        bal = executor.token_balance(token_addr, wallet_addr)
        if not bal:
            return "skipped_no_balance"
        w3 = getattr(executor, "w3", None) or executor._require_w3()
        from core.rh_execution import (ERC20_ABI, SWAP_ROUTER02,
                                       build_sell_calldata)
        from web3 import Web3
        token = Web3.to_checksum_address(token_addr)
        wallet = Web3.to_checksum_address(wallet_addr)
        c = w3.eth.contract(address=token, abi=ERC20_ABI)
        allowance = int(c.functions.allowance(wallet, SWAP_ROUTER02).call())
        if allowance < bal:
            return "skipped_no_allowance"
        call = build_sell_calldata(token, bal, 0, wallet, fee)
        try:
            w3.eth.call({"from": wallet, "to": call["to"],
                         "data": call["data"], "value": 0})
            return "ok"
        except Exception:
            return "reverted"
    except Exception as e:
        logger.debug("[rh-honeypot] live sell check errored (%s) — skipped", e)
        return "skipped_error"
