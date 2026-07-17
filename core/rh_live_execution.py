# core/rh_live_execution.py
"""Robinhood Chain LIVE execution policy layer — BUILT, TESTED, PARKED DORMANT.

This module fronts core/rh_execution.RhExecutor (the verified swap rail) with
the Solana live discipline, so that when the aged-pool racers grade green the
go-live is a CONFIG FLIP, not a build. As of 2026-07-11 NO key exists, NO
funding exists, and every money path below refuses unless the TRIPLE GATE is
open (mirror of the Solana LIVE_CONFIRMED / PAPER_MODE / key discipline):

    RH_LIVE_CONFIRMED=true   — explicit human ack (AxiS), never defaulted
    RH_PAPER_MODE=false      — paper wins by DEFAULT; only the literal
                               string "false" opens this leg
    RH_PRIVATE_KEY present   — the hot-wallet key (never logged / repr'd;
                               held only inside eth_account)

All three or nothing: any missing leg -> RhLiveGateError, no network I/O on
the money path. Gates are read at CALL time (env), never cached at import.

ROUTER PROVENANCE (why these swaps are safe to sign):
  Constants come from core/rh_execution.py (LIVE-VERIFIED 2026-07-09) and
  were RE-VERIFIED on-chain 2026-07-11 by this build (read-only eth_call):
    * eth_chainId == 0x1237 (4663) on the official RPC.
    * SwapRouter02 0xCaf6…5cb2: WETH9() == the WETH9 const AND factory() ==
      the factory const (code present, 24,497 bytes).
    * QuoterV2 0x33e8…A9E7 answers the SAME WETH9()/factory() — the quoter
      the paper lane prices with and the router the live path fills through
      provably point at one deployment.
    * scripts/rh_chain_feed.py discovers pools from that SAME factory
      (V3_FACTORY == SwapRouter02.factory()) — the pool set we trade is the
      pool set this router routes. A different router would be a fund-loss
      bug; do not change these constants without re-running the verification
      (scratchpad/rh_live_exec/PROGRESS.md documents the procedure).

WHAT LIVES HERE (all offline-testable, all dormant by default):
  1. RhLiveExecutor      — live_buy / live_sell with containment:
       * per-position cap    RH_LIVE_MAX_POSITION_USD (default 25)
       * daily loss halt     RH_LIVE_DAILY_STOP_USD  (default 25, buys only)
       * slippage bound      per-call bps, hard ceiling SLIPPAGE_BPS_CEILING
       * gas-cost cap        RH_LIVE_MAX_GAS_COST_ETH (default 0.0005 ETH,
                             ~300x the measured per-side gas — a runaway
                             bound, enforced FAIL-CLOSED pre-sign)
       * canary halt check   buys refuse while the sell-path canary is red;
                             SELLS ARE NEVER GATED (exits always free to try)
       * nonce serialization + receipt confirmation + revert decoding come
         from RhExecutor (send lock, pending nonce, wait_for_receipt with
         timeout) — decode helpers below add Error(string)/Panic(uint).
  2. RhSellCanary        — the never-buys-while-sells-broken rule (2026-07-10
       Solana incident, core/sell_path_canary.py), RH analog: a periodic
       REAL exit-quote probe through the exact sell-path code (QuoterV2
       batch quoter) on every open position; with no positions it proves the
       quote PIPE (a well-formed revert IS a pass — the read worked). N
       consecutive failures (RH_CANARY_MAX_FAILS, default 3) or a wedged
       probe loop -> halt flag -> the lane's entry path and live_buy refuse.
       Flag is a FILE (cross-process): the lane and any live runner agree.
  3. Wallet-truth        — rh_wallet_truth(): on-chain native ETH + WETH of
       the hot wallet vs a persisted baseline (armed on first call while the
       triple gate is OPEN, exactly like /api/wallet-truth's
       live_wallet_baseline.json). Writes a status JSON the uploader can
       ship to the dashboard. The on-chain delta is the ONLY honest P&L.
  4. RhDailyPnl          — persisted realized-P&L day counter feeding the
       daily loss halt (UTC day roll; unreadable state fails CLOSED).

ENV SUMMARY (all optional; defaults = fully dormant paper behavior):
  RH_LIVE_CONFIRMED / RH_PAPER_MODE / RH_PRIVATE_KEY   — the triple gate
  RH_LIVE_MAX_POSITION_USD=25   RH_LIVE_DAILY_STOP_USD=25
  RH_LIVE_SLIPPAGE_BPS=300      RH_LIVE_MAX_GAS_COST_ETH=0.0005
  RH_SELL_CANARY=auto|on|off    (auto = ON when RH_PAPER_MODE=false)
  RH_CANARY_MAX_FAILS=3         RH_CANARY_INTERVAL_S=60
  RH_LIVE_STATE_DIR=scratchpad/robinhood_tapes   (canary flag, baseline,
      wallet-truth status, daily-pnl state; set to $DATA_DIR on Railway)
  RH_WALLET_ADDRESS             (keyless wallet-truth watching, optional)

GO-LIVE FLIP SEQUENCE (future session, after racers grade green + AxiS go):
  1. python tests/test_rh_pre_live_invariants.py   (must exit 0)
  2. fund the hot wallet; set RH_PRIVATE_KEY (env only, never a file)
  3. set RH_LIVE_CONFIRMED=true, RH_PAPER_MODE=false, RH_LIVE_STATE_DIR
  4. rh_wallet_truth() once — confirms balances read AND arms the baseline
  5. verify the sell path END-TO-END with a dust position before any buy
     (sell-path canary green is necessary, not sufficient — 07-10 rule)
  6. explicit AxiS approval recorded, then start the lane in live mode
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from typing import Optional

from core.rh_execution import (
    RH_CHAIN_ID,
    WETH9,
    RhExecutionError,
    RhExecutor,
    RhSwapError,
)

logger = logging.getLogger(__name__)

# ── containment defaults (task-specified) ────────────────────────────────────
DEFAULT_MAX_POSITION_USD = 25.0
DEFAULT_DAILY_STOP_USD = 25.0
DEFAULT_SLIPPAGE_BPS = 300
SLIPPAGE_BPS_CEILING = 1000          # >10% slippage bound is never sane here
DEFAULT_MAX_GAS_COST_ETH = 0.0005    # runaway bound; measured gas ~1.5e-6 ETH

# ── canary constants (mirror core/sell_path_canary.py) ──────────────────────
CANARY_GRACE_SECS = 180.0            # post-spawn grace before "no data" fails
CANARY_STALE_FACTOR = 4.0            # missed probes (wedged loop) -> unhealthy
DEFAULT_CANARY_MAX_FAILS = 3
DEFAULT_CANARY_INTERVAL_S = 60.0
CANARY_BLOCK_REASON = "sell_canary_halt"

# missing-flag-file grace anchor: if canary mode is ON but no probe has ever
# written the flag file, buys are allowed only briefly after this module
# loaded (the wedged/never-started canary loop must fail CLOSED, mirroring
# SellPathCanary.spawned_at).
_MODULE_SPAWNED_AT = time.time()

# state-file basenames (under rh_state_dir())
CANARY_FLAG_BASENAME = "rh_canary_state.json"
DAILY_PNL_BASENAME = "rh_live_daily_pnl.json"
WALLET_BASELINE_BASENAME = "rh_live_wallet_baseline.json"
WALLET_TRUTH_BASENAME = "rh_wallet_truth.json"

# solidity revert selectors
REVERT_ERROR_SELECTOR = "08c379a0"   # Error(string)
REVERT_PANIC_SELECTOR = "4e487b71"   # Panic(uint256)
PANIC_CODES = {
    0x01: "assert failed", 0x11: "arithmetic overflow/underflow",
    0x12: "division by zero", 0x21: "invalid enum value",
    0x22: "storage byte array misencoded", 0x31: "pop on empty array",
    0x32: "array index out of bounds", 0x41: "out of memory",
    0x51: "uninitialized function pointer",
}

_TX_HASH_RE = re.compile(r"tx=(0x[0-9a-fA-F]{64})")


# ── errors ───────────────────────────────────────────────────────────────────
class RhLiveGateError(RhExecutionError):
    """A live function was called while the triple gate is closed."""


class RhContainmentError(RhExecutionError):
    """Position cap / daily loss halt / slippage bound refused the order."""


class RhCanaryHaltError(RhContainmentError):
    """Sell-path canary is red — live buys refused (sells never gated)."""


# ── triple gate (env read at CALL time, never cached) ────────────────────────
def rh_paper_mode() -> bool:
    """PAPER unless the env is the LITERAL string 'false' (Solana parity:
    the dangerous direction requires an exact, deliberate value)."""
    return str(os.environ.get("RH_PAPER_MODE", "true")).strip().lower() != "false"


def rh_live_confirmed() -> bool:
    return str(os.environ.get("RH_LIVE_CONFIRMED", "")).strip().lower() == "true"


def rh_key_present() -> bool:
    return bool(os.environ.get("RH_PRIVATE_KEY"))


def rh_live_gate() -> tuple:
    """(allowed, reason). FAIL-CLOSED: every leg must be explicitly open.
    The reason string is safe to log (never contains key material)."""
    missing = []
    if not rh_live_confirmed():
        missing.append("RH_LIVE_CONFIRMED!=true")
    if rh_paper_mode():
        missing.append("RH_PAPER_MODE!=false")
    if not rh_key_present():
        missing.append("RH_PRIVATE_KEY absent")
    if missing:
        return False, "live gate CLOSED: " + ", ".join(missing)
    return True, "live gate open"


# ── shared state dir ─────────────────────────────────────────────────────────
def rh_state_dir() -> str:
    return os.environ.get("RH_LIVE_STATE_DIR") or os.path.join(
        "scratchpad", "robinhood_tapes")


def _state_path(basename: str) -> str:
    return os.path.join(rh_state_dir(), basename)


def _atomic_write_json(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def _read_json(path: str) -> Optional[dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ── revert decoding (pure + a FAIL-OPEN replay helper) ───────────────────────
def decode_revert_data(data) -> str:
    """Solidity revert payload (bytes | 0x-hex | '') -> human string.
    Handles Error(string), Panic(uint256), empty, and raw-hex fallback.
    Pure, never raises (instrumentation)."""
    try:
        if isinstance(data, (bytes, bytearray)):
            h = bytes(data).hex()
        else:
            h = str(data or "")
            if h.startswith("0x"):
                h = h[2:]
        h = h.lower()
        if not h:
            return "revert (no data)"
        if h.startswith(REVERT_ERROR_SELECTOR):
            body = bytes.fromhex(h[8:])
            if len(body) >= 64:
                length = int.from_bytes(body[32:64], "big")
                s = body[64:64 + length].decode("utf-8", errors="replace")
                return f"revert: {s}"
        if h.startswith(REVERT_PANIC_SELECTOR):
            code = int(h[8:8 + 64] or "0", 16)
            return "panic 0x%02x (%s)" % (
                code, PANIC_CODES.get(code, "unknown panic"))
        return f"revert data 0x{h[:72]}"
    except Exception:
        return "revert (undecodable)"


def fetch_revert_reason(w3, tx_hash: str) -> Optional[str]:
    """Replay a mined-but-reverted tx via eth_call at its block to recover
    the revert payload. FAIL-OPEN: instrumentation only — any problem
    (pruned state, node quirk, unmined tx) -> None, never raises."""
    try:
        tx = w3.eth.get_transaction(tx_hash)
        call = {"from": tx["from"], "to": tx["to"],
                "data": tx.get("input") or tx.get("data"),
                "value": int(tx.get("value") or 0),
                "gas": int(tx.get("gas") or 0) or None}
        call = {k: v for k, v in call.items() if v is not None}
        block = tx.get("blockNumber")
        w3.eth.call(call, block_identifier=block if block is not None else "latest")
        return None  # replay succeeded — no reason recoverable
    except Exception as e:
        data = getattr(e, "data", None)
        if isinstance(data, dict):        # some providers nest {data: {…}}
            data = data.get("data") or data.get("originalError", {}).get("data")
        if data:
            return decode_revert_data(data)
        msg = str(e)
        return msg[:200] if msg else None


def explain_swap_error(executor: Optional[RhExecutor], err: Exception) -> str:
    """RhSwapError -> message enriched with a decoded revert reason when the
    error carries a tx hash and the chain will replay it. FAIL-OPEN."""
    msg = str(err)
    try:
        m = _TX_HASH_RE.search(msg)
        if m and executor is not None and executor.w3 is not None:
            reason = fetch_revert_reason(executor.w3, m.group(1))
            if reason:
                return f"{msg} [{reason}]"
    except Exception:
        pass
    return msg


# ── gas-cost cap (pure check + a subclass hook; rh_execution untouched) ─────
def enforce_gas_cap(gas: int, max_fee_per_gas: int, cap_wei) -> None:
    """FAIL-CLOSED: worst-case gas spend (gas_limit * maxFeePerGas) must not
    exceed the cap. cap_wei None = no cap (paper parity)."""
    if cap_wei is None:
        return
    worst = int(gas) * int(max_fee_per_gas)
    if worst > int(cap_wei):
        raise RhSwapError(
            f"gas-cost cap: worst-case {worst / 1e18:.6f} ETH "
            f"(gas={gas} x maxFee={max_fee_per_gas}) exceeds cap "
            f"{int(cap_wei) / 1e18:.6f} ETH — refusing to sign")


class GasCappedExecutor(RhExecutor):
    """RhExecutor + a pre-sign worst-case gas-cost bound. Behavior is
    byte-identical to RhExecutor when max_gas_cost_wei is None."""

    def __init__(self, *args, max_gas_cost_wei: Optional[int] = None, **kw):
        super().__init__(*args, **kw)
        self.max_gas_cost_wei = max_gas_cost_wei

    def _build_tx(self, call: dict, gas_fallback: int = 600_000) -> dict:
        tx = super()._build_tx(call, gas_fallback)
        enforce_gas_cap(tx["gas"], tx["maxFeePerGas"], self.max_gas_cost_wei)
        return tx


# ── sell-path canary (RH analog of core/sell_path_canary.py) ────────────────
def canary_mode_on() -> bool:
    """RH_SELL_CANARY=on|off|auto (default auto). auto = ON exactly when a
    live session is being attempted (RH_PAPER_MODE=false) — the incident
    mandate is 'default ON when live'. Paper default stays byte-identical."""
    v = str(os.environ.get("RH_SELL_CANARY", "auto")).strip().lower()
    if v in ("on", "1", "true"):
        return True
    if v in ("off", "0", "false"):
        return False
    return not rh_paper_mode()


def canary_interval_s() -> float:
    try:
        return float(os.environ.get("RH_CANARY_INTERVAL_S",
                                    DEFAULT_CANARY_INTERVAL_S))
    except Exception:
        return DEFAULT_CANARY_INTERVAL_S


def canary_max_fails() -> int:
    try:
        return max(1, int(os.environ.get("RH_CANARY_MAX_FAILS",
                                         DEFAULT_CANARY_MAX_FAILS)))
    except Exception:
        return DEFAULT_CANARY_MAX_FAILS


def rh_canary_flag_path() -> str:
    return os.environ.get("RH_CANARY_FLAG_PATH") or _state_path(
        CANARY_FLAG_BASENAME)


class RhSellCanary:
    """State machine: N consecutive exit-quote failures, only-failures-ever,
    or a wedged probe loop (stale last probe) -> unhealthy -> buys halt.
    A single transient failure inside the N debounce does NOT halt. Sells
    are never gated by this object — it feeds the BUY path only."""

    def __init__(self, interval_secs: Optional[float] = None,
                 max_fails: Optional[int] = None,
                 spawned_at: Optional[float] = None):
        self.interval_secs = float(interval_secs if interval_secs is not None
                                   else canary_interval_s())
        self.max_fails = int(max_fails if max_fails is not None
                             else canary_max_fails())
        self.spawned_at = time.time() if spawned_at is None else float(spawned_at)
        self.last_ok_ts = None
        self.last_fail_ts = None
        self.consecutive_fails = 0

    def record(self, ok: bool, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        if ok:
            self.last_ok_ts = now
            self.consecutive_fails = 0
        else:
            self.last_fail_ts = now
            self.consecutive_fails += 1

    def healthy(self, now: Optional[float] = None) -> bool:
        """True = live buys allowed. FAIL-CLOSED past the boot grace."""
        now = time.time() if now is None else now
        if self.consecutive_fails >= self.max_fails:
            return False
        probes = [t for t in (self.last_ok_ts, self.last_fail_ts) if t]
        if not probes:
            return (now - self.spawned_at) < CANARY_GRACE_SECS
        # a wedged/stopped probe loop must not stay healthy forever
        return (now - max(probes)) < self.interval_secs * CANARY_STALE_FACTOR

    def status_line(self, now: Optional[float] = None) -> str:
        now = time.time() if now is None else now
        return ("healthy=%s ok_age=%s fail_age=%s consec_fails=%d/%d" % (
            self.healthy(now),
            "%.0fs" % (now - self.last_ok_ts) if self.last_ok_ts else "-",
            "%.0fs" % (now - self.last_fail_ts) if self.last_fail_ts else "-",
            self.consecutive_fails, self.max_fails))

    # ── cross-process flag file (state, not verdict: readers re-evaluate
    # healthy(now) so a stale writer can never pin 'healthy') ────────────────
    def to_state(self) -> dict:
        return {"spawned_at": self.spawned_at, "last_ok_ts": self.last_ok_ts,
                "last_fail_ts": self.last_fail_ts,
                "consecutive_fails": self.consecutive_fails,
                "interval_secs": self.interval_secs,
                "max_fails": self.max_fails,
                "written_ts": time.time()}

    @classmethod
    def from_state(cls, blob: dict) -> "RhSellCanary":
        c = cls(interval_secs=blob.get("interval_secs"),
                max_fails=blob.get("max_fails"),
                spawned_at=blob.get("spawned_at"))
        c.last_ok_ts = blob.get("last_ok_ts")
        c.last_fail_ts = blob.get("last_fail_ts")
        c.consecutive_fails = int(blob.get("consecutive_fails") or 0)
        return c

    def write_flag(self, path: Optional[str] = None) -> None:
        """Persist the state (FAIL-OPEN on IO: the in-process reader still
        has the object; the cross-process reader ages the file out)."""
        try:
            _atomic_write_json(path or rh_canary_flag_path(), self.to_state())
        except Exception as e:
            logger.warning("[rh-canary] flag write failed: %s", e)


def probe_exit_quotes(executor: RhExecutor, holdings) -> bool:
    """One canary probe through the EXACT sell-path code (QuoterV2 via the
    batch quoter inside quote_sell). holdings: [(token_addr, atomic_amount)].

    The canary's job is to catch a BROKEN SELL PATH (RPC/quoter/transport
    dead), NOT an individual unsellable bag. Health is anchored on the
    TRANSPORT probe — a WETH->WETH tier batch through the same batch quoter
    (a well-formed per-tier revert IS a pass; mirrors the Solana USDC
    zero-balance-is-a-pass rule). Transport dead -> RED (the real disaster).

    With the transport proven ALIVE, an open position that STILL can't quote
    is a DEAD/no-route bag (rug or dust), not a path failure — and halting
    buys forever cannot make it sellable. (2026-07-14 incident: a GOATAI rug
    stranded as an open position by the abandoned barbell live probe made the
    canary RED every tick and froze the whole lane's live buys, including the
    healthy green aged_derisk.) So a dead bag is LOGGED as a write-off
    candidate but does NOT poison the canary. Transport dead is still RED.
    Returns True=healthy (path can quote), False=sell path is broken."""
    try:
        # transport health: the same batch-quoter pipe every real sell uses.
        transport_ok = False
        batch = executor._quote_all_tiers_batched(WETH9, WETH9, 10 ** 15)
        if batch is not None:          # well-formed response (even all-revert = {})
            transport_ok = True
        else:
            w3 = executor._require_w3()
            transport_ok = int(w3.eth.chain_id) == RH_CHAIN_ID
        if not transport_ok:
            return False               # broken pipe = the disaster the canary is for
        if not holdings:
            return True
        # transport is alive; per-position quotes are now diagnostic only.
        for token, amount in holdings:
            q = executor.quote_sell(token, int(amount))
            if q is None or not q.amount_out:
                logger.warning(
                    "[rh-canary] holding %s unquotable but transport OK -> "
                    "dead/no-route bag (write-off candidate), NOT halting buys",
                    token)
        return True
    except Exception as e:
        logger.warning("[rh-canary] probe failed: %s", e)
        return False


def rh_canary_entry_block(now: Optional[float] = None) -> Optional[str]:
    """The BUY-path check: CANARY_BLOCK_REASON while the canary is red, else
    None. Canary mode off (paper default) -> always None (byte-identical
    paper lane). Missing flag file counts as 'no probe yet': allowed only
    inside the module boot grace, then FAIL-CLOSED (a canary loop that never
    started must not silently permit live buys forever)."""
    if not canary_mode_on():
        return None
    now = time.time() if now is None else now
    blob = _read_json(rh_canary_flag_path())
    if blob is None:
        if (now - _MODULE_SPAWNED_AT) < CANARY_GRACE_SECS:
            return None
        return CANARY_BLOCK_REASON
    try:
        canary = RhSellCanary.from_state(blob)
        return None if canary.healthy(now) else CANARY_BLOCK_REASON
    except Exception:
        return CANARY_BLOCK_REASON   # unreadable state = unknown = halt buys


# ── daily realized-P&L store (feeds the daily loss halt) ────────────────────
class RhDailyPnl:
    """Persisted UTC-day realized P&L. record() is called by the live path
    after each realized close; today_usd() feeds the buy halt. Reads go to
    the FILE first (cross-process truth) with the in-memory copy as fallback;
    a state that can be neither read nor reconstructed returns None and the
    caller must FAIL CLOSED (halt buys on unknown risk state)."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or _state_path(DAILY_PNL_BASENAME)
        self._mem: Optional[dict] = None

    @staticmethod
    def _day(now: Optional[float] = None) -> str:
        return datetime.fromtimestamp(
            time.time() if now is None else now, tz=timezone.utc
        ).strftime("%Y-%m-%d")

    def _load(self, now: Optional[float] = None) -> Optional[dict]:
        blob = _read_json(self.path)
        if blob is None:
            blob = self._mem
        if blob is None:
            return None
        if blob.get("day") != self._day(now):     # UTC day rolled
            return {"day": self._day(now), "realized_usd": 0.0, "n": 0}
        return dict(blob)

    def record(self, pnl_usd: float, now: Optional[float] = None) -> dict:
        st = self._load(now) or {"day": self._day(now),
                                 "realized_usd": 0.0, "n": 0}
        st["realized_usd"] = float(st.get("realized_usd") or 0.0) + float(pnl_usd)
        st["n"] = int(st.get("n") or 0) + 1
        self._mem = dict(st)
        try:
            _atomic_write_json(self.path, st)
        except Exception as e:      # in-memory copy still counts (fallback)
            logger.warning("[rh-live] daily-pnl persist failed: %s", e)
        return st

    def today_usd(self, now: Optional[float] = None) -> Optional[float]:
        """Realized USD today; 0.0 when no state has ever existed (a fresh
        deploy genuinely has no realized P&L); None ONLY when state exists
        but is unreadable garbage — callers halt on None."""
        blob = _read_json(self.path)
        if blob is None and os.path.exists(self.path):
            return None if self._mem is None else self._pick(self._mem, now)
        st = self._load(now)
        if st is None:
            return 0.0
        return self._pick(st, now)

    def _pick(self, st: dict, now: Optional[float]) -> float:
        if st.get("day") != self._day(now):
            return 0.0
        try:
            return float(st.get("realized_usd") or 0.0)
        except Exception:
            return 0.0


# ── wallet-truth (mirrors /api/wallet-truth semantics on RH rails) ──────────
def _mask(addr: Optional[str]) -> Optional[str]:
    if not addr:
        return None
    return addr[:6] + "…" + addr[-4:]


# Off-limits PERSONAL holdings — never shown/counted as bot positions (AxiS rule).
_OFF_LIMITS_SYMS = {"GFOF"}
_OFF_LIMITS_SUBSTR = ("cmoon",)


def _held_meme_positions(wallet: str, ex, eth_price_usd) -> list:
    """Enumerate the wallet's held meme tokens (open positions) marked to their
    REAL sell quote — the visibility MetaMask can't give on a custom-added chain.
    Personal holdings (GFOF/Cmoon) excluded; unsellable (honeypot/rug) -> $0 (no
    quote-price illusion). FAIL to [] — never blocks wallet-truth."""
    try:
        from core.rh_blockscout import _get_json
        data = _get_json(f"/api/v2/addresses/{wallet.lower()}/tokens?type=ERC-20")
    except Exception:
        return []
    positions = []
    for t in ((data.get("items") if isinstance(data, dict) else []) or []):
        tok = t.get("token", {}) or {}
        sym = tok.get("symbol") or "?"
        addr = tok.get("address_hash") or tok.get("address")
        dec = int(tok.get("decimals") or 18)
        raw = int(t.get("value") or 0)
        if raw <= 0 or not addr:
            continue
        if sym in _OFF_LIMITS_SYMS or any(s in sym.lower()
                                          for s in _OFF_LIMITS_SUBSTR):
            continue
        val_usd = 0.0
        try:
            q = ex.quote_sell(addr, raw)
            eth_out = (q.amount_out / 1e18) if (q and getattr(
                q, "amount_out", None)) else 0.0
            if eth_price_usd and eth_price_usd > 0:
                val_usd = round(eth_out * eth_price_usd, 2)
        except Exception:
            val_usd = 0.0   # unsellable -> $0 (honeypot/rug), honest not illusion
        positions.append({"sym": sym, "token": addr,
                          "qty": round(raw / 10 ** dec, 4),
                          "value_usd": val_usd,
                          "sellable": bool(val_usd and val_usd > 0.5)})
    return positions


def rh_wallet_truth(executor: Optional[RhExecutor] = None,
                    eth_price_usd: Optional[float] = None,
                    baseline_path: Optional[str] = None,
                    status_path: Optional[str] = None) -> dict:
    """On-chain hot-wallet truth: native ETH + WETH now, delta vs a persisted
    baseline. THE only honest live P&L (Solana rule: on-chain delta, nothing
    simulated). Baseline arms automatically on the FIRST call while the
    triple gate is OPEN (mirror of live_wallet_baseline.json); a deliberate
    re-baseline after deposits/withdrawals is rh_wallet_rebase().

    Keyless watching works via RH_WALLET_ADDRESS. Balance-read errors return
    {ok: False, error} — NEVER a stale/zero number (2026-07-10 incident
    class), and never touch the baseline. Status JSON is written to
    rh_wallet_truth.json for the uploader/dashboard (FAIL-OPEN write)."""
    baseline_path = baseline_path or _state_path(WALLET_BASELINE_BASENAME)
    status_path = status_path or _state_path(WALLET_TRUTH_BASENAME)
    gate_open, gate_reason = rh_live_gate()
    out = {"ok": False, "chain": "robinhood", "chain_id": RH_CHAIN_ID,
           "paper_mode": rh_paper_mode(), "live_gate": gate_open,
           "gate_reason": gate_reason,
           "ts": datetime.now(timezone.utc).isoformat()}
    try:
        ex = executor or RhExecutor()
        wallet = ex.wallet_address or os.environ.get("RH_WALLET_ADDRESS")
        if not wallet:
            out["error"] = "no wallet (no RH_PRIVATE_KEY and no RH_WALLET_ADDRESS)"
            return out
        out["wallet"] = _mask(wallet)
        eth_now = float(ex.eth_balance(wallet))                 # FAIL-CLOSED
        weth_now = float(ex.token_balance(WETH9, wallet)) / 1e18
        total = eth_now + weth_now
        out.update({"ok": True, "eth_now": round(eth_now, 8),
                    "weth_now": round(weth_now, 8),
                    "total_eth": round(total, 8)})
        # Stamp the live ETH price + USD valuation whenever a price is passed,
        # INDEPENDENT of the baseline (which only arms in live). This lets the
        # dashboard wallet card render total USD in PAPER mode too — the whole
        # point of the always-on balance display (2026-07-13). delta_usd below
        # still layers on once the baseline exists.
        if eth_price_usd and eth_price_usd > 0:
            out["eth_price_usd"] = eth_price_usd
            out["total_usd"] = round(total * eth_price_usd, 2)
        # OPEN POSITIONS (2026-07-14): held meme tokens marked to their REAL sell
        # quote — the view MetaMask can't render on a custom-added chain. Personal
        # holdings (GFOF/Cmoon) excluded; unsellable (honeypot/rug) -> $0.
        out["positions"] = _held_meme_positions(wallet, ex, eth_price_usd)
        out["positions_usd"] = round(
            sum((p.get("value_usd") or 0) for p in out["positions"]), 2)
        baseline = _read_json(baseline_path)
        if baseline is None and gate_open:
            baseline = {"total_eth": total, "eth": eth_now, "weth": weth_now,
                        "ts": time.time(), "wallet": _mask(wallet)}
            try:
                _atomic_write_json(baseline_path, baseline)
            except Exception as e:
                logger.warning("[rh-truth] baseline write failed: %s", e)
        if baseline is not None:
            out["baseline_eth"] = round(float(baseline["total_eth"]), 8)
            out["baseline_ts"] = baseline.get("ts")
            out["delta_eth"] = round(total - float(baseline["total_eth"]), 8)
            if eth_price_usd and eth_price_usd > 0:
                out["delta_usd"] = round(out["delta_eth"] * eth_price_usd, 2)
                out["eth_price_usd"] = eth_price_usd
        else:
            out["note"] = "baseline arms on first call while the live gate is open"
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
    try:
        _atomic_write_json(status_path, out)
    except Exception as e:
        logger.warning("[rh-truth] status write failed: %s", e)
    return out


def rh_wallet_rebase(executor: Optional[RhExecutor] = None,
                     baseline_path: Optional[str] = None) -> dict:
    """DELIBERATE re-baseline (after deposits/withdrawals) — the analog of
    POST /api/wallet-truth/rebase. Reads fresh balances FAIL-CLOSED and
    overwrites the baseline. Never called automatically."""
    baseline_path = baseline_path or _state_path(WALLET_BASELINE_BASENAME)
    ex = executor or RhExecutor()
    wallet = ex.wallet_address or os.environ.get("RH_WALLET_ADDRESS")
    if not wallet:
        raise RhExecutionError("rebase: no wallet address available")
    eth_now = float(ex.eth_balance(wallet))
    weth_now = float(ex.token_balance(WETH9, wallet)) / 1e18
    baseline = {"total_eth": eth_now + weth_now, "eth": eth_now,
                "weth": weth_now, "ts": time.time(), "wallet": _mask(wallet)}
    _atomic_write_json(baseline_path, baseline)
    return baseline


# ── the live executor (policy wrapper; every money path triple-gated) ───────
def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except Exception:
        return default


class RhLiveExecutor:
    """LIVE policy layer. Construction is always allowed (status/wallet-truth
    reads are read-only); live_buy/live_sell REFUSE unless the triple gate is
    open. Containment (position cap, daily loss halt, canary halt, slippage
    ceiling, gas cap) applies to BUYS; sells are gated only by the triple
    gate itself — exits must always be free to try."""

    def __init__(self, executor: Optional[RhExecutor] = None,
                 daily: Optional[RhDailyPnl] = None):
        self.max_position_usd = _env_float("RH_LIVE_MAX_POSITION_USD",
                                           DEFAULT_MAX_POSITION_USD)
        self.daily_stop_usd = abs(_env_float("RH_LIVE_DAILY_STOP_USD",
                                             DEFAULT_DAILY_STOP_USD))
        self.default_slippage_bps = int(_env_float("RH_LIVE_SLIPPAGE_BPS",
                                                   DEFAULT_SLIPPAGE_BPS))
        self.max_gas_cost_eth = _env_float("RH_LIVE_MAX_GAS_COST_ETH",
                                           DEFAULT_MAX_GAS_COST_ETH)
        # 2026-07-17 live-side hardening (07-15 audit findings): the paper
        # lane's max_concurrent cap does NOT protect the live path — this
        # executor had NO concurrency cap and NO balance check, so a lane bug
        # (or a state wipe re-arming a bot) could stack live positions or spend
        # the wallet to zero. Both guards are ON-CHAIN-TRUTH based (wallet
        # enumeration, not lane state) so they survive exactly the ephemeral-FS
        # wipes that caused past incidents.
        self.max_concurrent = int(_env_float("RH_LIVE_MAX_CONCURRENT", 1))
        self.gas_reserve_eth = _env_float("RH_LIVE_GAS_RESERVE_ETH", 0.001)
        self.daily = daily or RhDailyPnl()
        self._ex = executor

    def __repr__(self) -> str:   # never expose key material
        ok, reason = rh_live_gate()
        return (f"<RhLiveExecutor gate={'OPEN' if ok else 'closed'} "
                f"cap=${self.max_position_usd} stop=${self.daily_stop_usd}>")

    # ── plumbing ─────────────────────────────────────────────────────────
    def _executor(self) -> RhExecutor:
        if self._ex is None:
            self._ex = GasCappedExecutor(
                max_gas_cost_wei=int(self.max_gas_cost_eth * 1e18))
            self._ex.connect()          # chain-id check FAIL-CLOSED (4663)
        return self._ex

    def _require_live(self) -> None:
        ok, reason = rh_live_gate()
        if not ok:
            raise RhLiveGateError(reason)
        # an injected executor must actually be able to sign
        if self._ex is not None and getattr(self._ex, "paper_only", True):
            raise RhLiveGateError(
                "live gate CLOSED: executor is paper-only (no key loaded)")

    def _bps(self, max_slippage_bps: Optional[int]) -> int:
        bps = int(self.default_slippage_bps if max_slippage_bps is None
                  else max_slippage_bps)
        if not (0 < bps <= SLIPPAGE_BPS_CEILING):
            raise RhContainmentError(
                f"slippage bound {bps} bps outside (0, {SLIPPAGE_BPS_CEILING}]")
        return bps

    # ── halt surface (buys only) ─────────────────────────────────────────
    def buys_halted(self, now: Optional[float] = None) -> Optional[str]:
        """Reason string when live buys must refuse, else None. FAIL-CLOSED:
        an unreadable daily-P&L state halts (unknown risk = no new risk)."""
        pnl = self.daily.today_usd(now)
        if pnl is None:
            return "daily_pnl_unreadable"
        if pnl <= -self.daily_stop_usd:
            return f"daily_loss_stop ({pnl:+.2f} <= -{self.daily_stop_usd:.2f})"
        cb = rh_canary_entry_block(now)
        if cb:
            return cb
        return None

    # ── money paths ──────────────────────────────────────────────────────
    def live_buy(self, token_addr: str, usd_size: float,
                 eth_price_usd: float,
                 max_slippage_bps: Optional[int] = None) -> dict:
        """LIVE buy: triple gate -> containment -> canary -> swap. Returns
        the rh_live_swaps.jsonl telemetry record (fill_vs_mid + latency
        stamps come from RhExecutor._execute_and_record)."""
        self._require_live()
        if not (usd_size and usd_size > 0):
            raise RhContainmentError(f"buy size must be > 0, got {usd_size}")
        if usd_size > self.max_position_usd:
            raise RhContainmentError(
                f"position cap: ${usd_size:.2f} > "
                f"RH_LIVE_MAX_POSITION_USD ${self.max_position_usd:.2f}")
        if not (eth_price_usd and eth_price_usd > 0):
            raise RhContainmentError(
                f"eth_price_usd must be > 0, got {eth_price_usd}")
        bps = self._bps(max_slippage_bps)
        halt = self.buys_halted()
        if halt:
            if halt.startswith(CANARY_BLOCK_REASON):
                raise RhCanaryHaltError(
                    f"live buy refused: {halt} (sell path unproven — "
                    f"never buys while sells are broken)")
            raise RhContainmentError(f"live buy refused: {halt}")
        eth_amount = float(usd_size) / float(eth_price_usd)
        ex = self._executor()
        # BALANCE CHECK (2026-07-17): the buy must leave the gas reserve
        # intact. Without this, a lane bug could spend the wallet to zero and
        # strand every open position unsellable (no gas). eth_balance is
        # FAIL-CLOSED (raises on read error -> no buy on unknown balance).
        wallet = ex.wallet_address
        bal = float(ex.eth_balance(wallet))
        if eth_amount > bal - self.gas_reserve_eth:
            raise RhContainmentError(
                f"balance check: buy needs {eth_amount:.6f} ETH but wallet has "
                f"{bal:.6f} - {self.gas_reserve_eth:.4f} gas reserve")
        # CONCURRENCY CAP (2026-07-17): count OPEN live positions from the
        # WALLET-TRUTH snapshot (sellable non-dust bags), not lane state — a
        # state wipe must not let a re-armed bot stack positions. The snapshot
        # is the lane's 5-min keyless refresh (zero buy-path latency; an
        # in-path Blockscout+quotes enumeration would cost 1.5-2.5s and bust
        # the <=2s latency-parity budget). Missing/stale snapshot = unknown
        # risk state = FAIL CLOSED (RhDailyPnl convention) — wallet-truth
        # running is a go-live prerequisite anyway. A same-window double-buy
        # (snapshot lag) is caught by the balance check above at current
        # wallet scale; revisit the bump bookkeeping if positions ever get
        # small relative to the wallet.
        if self.max_concurrent > 0:
            n_open = self._open_position_count_from_snapshot()
            if n_open is None:
                raise RhContainmentError(
                    "concurrency check: wallet-truth snapshot missing/stale "
                    "(>10min) — unknown open-position count, refusing buy "
                    "(is the lane's wallet-truth refresher running?)")
            if n_open >= self.max_concurrent:
                raise RhContainmentError(
                    f"concurrency cap: wallet holds {n_open} open "
                    f"position(s) >= RH_LIVE_MAX_CONCURRENT "
                    f"{self.max_concurrent}")
        try:
            return ex.quote_and_swap_buy(token_addr, eth_amount, bps)
        except RhSwapError as e:
            raise RhSwapError(explain_swap_error(ex, e)) from e

    def _open_position_count_from_snapshot(self) -> Optional[int]:
        """Open live positions per the lane's wallet-truth snapshot: sellable
        bags worth >= $2 (dust and dead/routeless rugs are $0/valueless there
        and don't count). None = snapshot missing or older than 10 min —
        caller must FAIL CLOSED on unknown risk state. Read-only, no RPC."""
        try:
            blob = _read_json(_state_path(WALLET_TRUTH_BASENAME)) or {}
            ts = blob.get("ts")
            if not ts:
                return None
            dt = datetime.fromisoformat(str(ts))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if (datetime.now(timezone.utc) - dt).total_seconds() > 600:
                return None
            pos = blob.get("positions")
            if not isinstance(pos, list):
                return None
            return sum(1 for p in pos
                       if isinstance(p, dict)
                       and (p.get("value_usd") or 0) >= 2.0)
        except Exception:
            return None

    def live_sell(self, token_addr: str, token_amount="all",
                  max_slippage_bps: Optional[int] = None) -> dict:
        """LIVE sell: triple gate + slippage ceiling ONLY. No canary, no
        caps, no daily stop — exits are always free to try."""
        self._require_live()
        bps = self._bps(max_slippage_bps)
        ex = self._executor()
        try:
            return ex.swap_sell(token_addr, token_amount, bps)
        except RhSwapError as e:
            raise RhSwapError(explain_swap_error(ex, e)) from e

    def record_realized(self, pnl_usd: float,
                        now: Optional[float] = None) -> dict:
        """Book a realized close into the daily loss halt."""
        return self.daily.record(pnl_usd, now)
