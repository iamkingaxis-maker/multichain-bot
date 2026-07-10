# scripts/rh_firehose_feed.py
"""Robinhood Chain sequencer FIREHOSE decoder — v2 feed (pre-confirmation).

Subscribes to the keyless Arbitrum Nitro broadcast feed at
wss://feed.mainnet.chain.robinhood.com and decodes sequencer messages into
transactions BEFORE confirmation — ~100ms detection with zero polling and
zero rate limits, vs the v1 eth_getLogs poller (scripts/rh_chain_feed.py,
median lag 1.9-2.1s at its 1.5s public-RPC poll floor).

FEED FORMAT (live-verified 2026-07-10 against the real feed):
  * Text JSON frames: {"version":1,"messages":[{"sequenceNumber":N,
      "message":{"message":{"header":{"kind":3,"sender":"0xa4b0...
      73657175656e636572","blockNumber":<L1>,"timestamp":<unix s>,...},
      "l2Msg":"<base64>"},"delayedMessagesRead":M}}]}
  * One broadcast message per L2 block; header.timestamp is the L2 block
    timestamp (SECOND granularity — same resolution as v1's block ts).
  * l2Msg first byte = L2 message kind. Kind 3 (Batch) = repeated
    [8-byte big-endian length][sub-message], each sub-message again kind-
    prefixed; kind 4 (SignedTx) = one signed typed tx envelope follows.
    75s live sample: 1484 msgs, 14639 txs, ALL header kind 3, ALL l2Msg
    kind 3 wrapping kind-4 subs; tx envelopes were EIP-1559 (0x02) and
    legacy RLP.
  * On (re)connect the feed replays a backlog (~60-90s); dedupe by
    sequenceNumber + tx hash, and lag stats only count post-catch-up.

SWAP DECODE (pre-confirmation intent, from calldata):
  * Routers (live selector histogram, 75s): SwapRouter02
    0xCaf681a66D020601342297493863E78C959E5cb2 — exactInputSingle 0x04e45aaf
    (dominant), multicall(deadline) 0x5ae401dc, swapExactTokensForTokens
    (V2 leg) 0x472b43f3, multicall 0xac9650d8, exactOutputSingle 0x5023b4df,
    exactInput 0xb858183f, V3Router1-style 0x414bf389; Universal Router
    0x8876789976decbfcbbbe364623c63652db8c0904 — execute 0x3593564c /
    0x24856bc3 (commands 0x00/0x01/0x08/0x09 decoded; 0x10 V4_SWAP ignored —
    V4 swaps never hit our V2/V3 pools, so v1 doesn't tape them either).
  * Robinfun bonding-curve router traffic (top unknown `to` addresses) is
    NOT decoded — pre-graduation trades touch no V2/V3 pool, so the v1
    getLogs feed doesn't tape them either.  Follow-up if wanted.
  * Pool identity via CREATE2 (both LIVE-VERIFIED 2026-07-10):
      V3: canonical Uniswap V3 init code hash reproduces factory.getPool
          (WETH/USDG 100bps -> 0x52e6...71ca exact match).
      V2: canonical UniswapV2 pair init hash matches factory
          0x8bce...937f (the dominant/Robinfun-graduation factory);
          0xfc2e...20a9 does NOT match any known hash -> falls back to the
          token->pool registry built from discovery promotions.
  * Exact OUTPUT amounts are unknowable pre-confirmation.  volume_usd uses
    the WETH leg: exact when WETH is the input (or exactOutput with WETH
    out); otherwise the tx's own bound (amountOutMinimum / amountInMaximum)
    marked "vol_est": true.  All rows carry "pre_conf": true.

TAPE: identical rip_tape schema + dir as v1 (analysis ignores extra keys):
    {"kind","volume_usd","ts","maker","pair","sym","lag_secs"}
    -> scratchpad/robinhood_tapes/tape_<pool12>.jsonl
  ts = header timestamp (== L2 block ts); lag_secs = calibrated-wall-clock
  first sight minus header ts (clock calibrated against the RPC's HTTP Date
  header exactly like v1 — local Windows clock measured ~4s off).
  maker = ecrecover of the signed tx (coincurve-backed, ~0.5ms; taped
  subset only).  NOTE: do not run v1 and v2 into the same tape dir at the
  same time — rows would double-count (no tx field in the schema).

DISCOVERY/WATCH: reuses scripts/rh_chain_feed.Feed wholesale (backfill +
amortized liq checks + symbol promotion + age/liq gates) on a slow RPC
maintenance cadence (default 2.5s); the firehose only needs the watch set.

READ-ONLY, keyless, per-session (max_minutes arg).  WS drops reconnect with
exponential backoff and never crash the loop.

Usage: python scripts/rh_firehose_feed.py [max_minutes]
Env:   RH_FH_WS (feed url), RH_FEED_RPC (rpc for discovery/price/clock),
       RH_FH_MAINT_SECS (2.5), RH_FH_BACKLOG_MAX_S (120),
       RH_FH_OUT_DIR (default: v1's scratchpad/robinhood_tapes),
       RH_FH_DEBUG_TAPE (1 = also write fh_debug.jsonl sidecar with tx
       hashes + seen walls, for cross-checking against eth_getLogs)
       + v1's RH_FEED_MIN_LIQ / RH_FEED_MAX_AGE_H / RH_FEED_LOOKBACK_H etc.
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import rlp  # noqa: E402  (web3 transitive dep, verified importable)
import websockets  # noqa: E402
from eth_utils import keccak  # noqa: E402

from scripts.rh_chain_feed import (  # noqa: E402
    LOOKBACK_H,
    OUT_DIR as V1_OUT_DIR,
    RH_CHAIN_ID,
    RPC_DEFAULT,
    V2_FACTORIES,
    V3_FACTORY,
    WETH,
    Feed,
    _append,
    iso_utc,
    pctl,
    tape_row,
)

WS_URL = os.environ.get("RH_FH_WS", "wss://feed.mainnet.chain.robinhood.com")
OUT_DIR = os.environ.get("RH_FH_OUT_DIR", V1_OUT_DIR)
MAINT_SECS = float(os.environ.get("RH_FH_MAINT_SECS", "2.5"))
BACKLOG_MAX_S = float(os.environ.get("RH_FH_BACKLOG_MAX_S", "120"))
DEBUG_TAPE = os.environ.get("RH_FH_DEBUG_TAPE", "0") == "1"

SWAP_ROUTER02 = "0xcaf681a66d020601342297493863e78c959e5cb2"
UNIVERSAL_ROUTER = "0x8876789976decbfcbbbe364623c63652db8c0904"
ROUTERS = frozenset((SWAP_ROUTER02, UNIVERSAL_ROUTER))

# CREATE2 init code hashes — LIVE-VERIFIED 2026-07-10 (see module docstring).
V3_INIT_CODE_HASH = bytes.fromhex(
    "e34f199b19b2b4f47f68442619d555527d244f78a3297ea89325f843f87b8b54")
V2_INIT_CODE_HASH = bytes.fromhex(
    "96e8ac4277198ff8b6f785478aa9a39f403cb768dd02cbee326c3e7da348845f")
V2_CREATE2_FACTORY = V2_FACTORIES[0]  # 0x8bce... (canonical-hash verified)

# Nitro L2 message kinds (arbos/parse_l2.go)
L2MSG_KIND_BATCH = 3
L2MSG_KIND_SIGNED_TX = 4

# SwapRouter02 / V3Router1 / Universal Router selectors (bytes, no 0x)
SEL_EXACT_INPUT_SINGLE = bytes.fromhex("04e45aaf")
SEL_EXACT_OUTPUT_SINGLE = bytes.fromhex("5023b4df")
SEL_EXACT_INPUT = bytes.fromhex("b858183f")
SEL_EXACT_OUTPUT = bytes.fromhex("09b81346")
SEL_EXACT_INPUT_SINGLE_V1 = bytes.fromhex("414bf389")   # +deadline variants
SEL_EXACT_OUTPUT_SINGLE_V1 = bytes.fromhex("db3e2198")
SEL_EXACT_INPUT_V1 = bytes.fromhex("c04b8d59")
SEL_EXACT_OUTPUT_V1 = bytes.fromhex("f28c0498")
SEL_V2_SWAP_EXACT_IN = bytes.fromhex("472b43f3")        # SR02 V2 legs
SEL_V2_SWAP_EXACT_OUT = bytes.fromhex("42712a67")
SEL_MULTICALL = bytes.fromhex("ac9650d8")
SEL_MULTICALL_DEADLINE = bytes.fromhex("5ae401dc")
SEL_MULTICALL_PREVBLOCK = bytes.fromhex("1f0464d1")
SEL_UR_EXECUTE = bytes.fromhex("3593564c")
SEL_UR_EXECUTE_NODEADLINE = bytes.fromhex("24856bc3")

# Universal Router commands (command byte & 0x3f)
UR_V3_SWAP_EXACT_IN = 0x00
UR_V3_SWAP_EXACT_OUT = 0x01
UR_V2_SWAP_EXACT_IN = 0x08
UR_V2_SWAP_EXACT_OUT = 0x09


# ══════════════════════════════════════════════════════════════════════════════
# PURE decode helpers (no network — unit-tested in tests/test_rh_firehose_feed)
# ══════════════════════════════════════════════════════════════════════════════
def extract_messages(frame_text: str) -> list:
    """Broadcast frame JSON -> [(sequence_number, header_ts, l2msg_bytes)].
    Non-kind-3 headers and undecodable entries are skipped. Never raises."""
    out = []
    try:
        d = json.loads(frame_text)
    except Exception:
        return out
    for m in d.get("messages") or []:
        try:
            seq = int(m["sequenceNumber"])
            mm = (m.get("message") or {}).get("message") or {}
            hdr = mm.get("header") or {}
            if hdr.get("kind") != 3:  # L1MessageType_L2Message only
                continue
            ts = int(hdr.get("timestamp") or 0)
            b = base64.b64decode(mm.get("l2Msg") or "")
            out.append((seq, ts, b))
        except Exception:
            continue
    return out


def split_l2_msg(b: bytes, _depth: int = 0) -> list:
    """L2 message walk -> list of raw signed-tx envelopes (kind-4 payloads).
    Kind 3 = batch: repeated [8-byte BE length][sub-message]; sub-messages
    are again kind-prefixed (nesting handled, capped).  Truncated/oversized
    length prefixes end the walk cleanly.  Never raises."""
    out = []
    if not b or _depth > 4:
        return out
    kind = b[0]
    if kind == L2MSG_KIND_SIGNED_TX:
        if len(b) > 1:
            out.append(bytes(b[1:]))
    elif kind == L2MSG_KIND_BATCH:
        i, n = 1, len(b)
        while i + 8 <= n:
            ln = int.from_bytes(b[i:i + 8], "big")
            i += 8
            if ln <= 0 or i + ln > n:
                break
            out.extend(split_l2_msg(b[i:i + ln], _depth + 1))
            i += ln
    return out


def decode_signed_tx(raw: bytes):
    """Signed typed tx envelope -> {hash,to,value,data,type,raw} | None.
    Handles 0x02 (EIP-1559), 0x01 (EIP-2930), legacy RLP. Unknown types /
    malformed RLP -> None (never raises).  `to` lowercase 0x-hex ('' for
    contract creation); hash = keccak(envelope) == the on-chain tx hash."""
    try:
        t = raw[0]
        if t == 0x02:
            f = rlp.decode(raw[1:])
            to, val, data = f[5], f[6], f[7]
        elif t == 0x01:
            f = rlp.decode(raw[1:])
            to, val, data = f[4], f[5], f[6]
        elif t >= 0xc0:  # legacy: RLP list starts directly
            f = rlp.decode(raw)
            to, val, data = f[3], f[4], f[5]
            t = 0
        else:
            return None
        return {"hash": "0x" + keccak(raw).hex(),
                "to": ("0x" + to.hex()) if to else "",
                "value": int.from_bytes(val, "big"),
                "data": bytes(data), "type": t, "raw": bytes(raw)}
    except Exception:
        return None


def _au(args: bytes, i: int) -> int:
    """i-th 32-byte word of an args blob (after selector) as uint."""
    return int.from_bytes(args[32 * i:32 * (i + 1)], "big")


def _aa(args: bytes, i: int) -> str:
    """i-th word as lowercase address."""
    return "0x" + args[32 * i + 12:32 * (i + 1)].hex()


def _dyn_bytes(args: bytes, offset: int) -> bytes:
    """ABI dynamic bytes at absolute offset within args."""
    ln = int.from_bytes(args[offset:offset + 32], "big")
    return args[offset + 32:offset + 32 + ln]


def _dyn_bytes_array(args: bytes, offset: int) -> list:
    """ABI bytes[] at absolute offset within args."""
    n = int.from_bytes(args[offset:offset + 32], "big")
    base = offset + 32
    out = []
    for i in range(min(n, 64)):
        rel = int.from_bytes(args[base + 32 * i:base + 32 * (i + 1)], "big")
        out.append(_dyn_bytes(args, base + rel))
    return out


def _addr_array(args: bytes, offset: int) -> list:
    """ABI address[] at absolute offset within args."""
    n = int.from_bytes(args[offset:offset + 32], "big")
    base = offset + 32
    return ["0x" + args[base + 32 * i + 12:base + 32 * (i + 1)].hex()
            for i in range(min(n, 16))]


def decode_v3_path(path: bytes) -> list:
    """Uniswap V3 packed path -> [(tokenA, fee, tokenB), ...] hops."""
    hops = []
    i = 0
    while i + 43 <= len(path):
        a = "0x" + path[i:i + 20].hex()
        fee = int.from_bytes(path[i + 20:i + 23], "big")
        b = "0x" + path[i + 23:i + 43].hex()
        hops.append((a, fee, b))
        i += 23
    return hops


def _intent(dex, token_in, token_out, fee, exact_in, amount_in, amount_out,
            amount_in_max, amount_out_min):
    return {"dex": dex, "token_in": token_in.lower(),
            "token_out": token_out.lower(), "fee": fee, "exact_in": exact_in,
            "amount_in": amount_in, "amount_out": amount_out,
            "amount_in_max": amount_in_max, "amount_out_min": amount_out_min}


def decode_router_calldata(data: bytes, _depth: int = 0) -> list:
    """Router calldata -> list of pre-confirmation swap intents.
    Multi-hop paths keep only single-hop swaps (young WETH-quoted memecoin
    pools are single-hop; multi-hop is counted by the caller via the empty
    result).  Recurses into multicall/execute wrappers.  Never raises."""
    if len(data) < 4 or _depth > 3:
        return []
    sel, args = bytes(data[:4]), bytes(data[4:])
    out = []
    try:
        if sel == SEL_EXACT_INPUT_SINGLE:
            # (tokenIn, tokenOut, fee, recipient, amountIn, minOut, sqrtLimit)
            out.append(_intent("v3", _aa(args, 0), _aa(args, 1), _au(args, 2),
                               True, _au(args, 4), None, None, _au(args, 5)))
        elif sel == SEL_EXACT_OUTPUT_SINGLE:
            # (tokenIn, tokenOut, fee, recipient, amountOut, inMax, sqrtLimit)
            out.append(_intent("v3", _aa(args, 0), _aa(args, 1), _au(args, 2),
                               False, None, _au(args, 4), _au(args, 5), None))
        elif sel == SEL_EXACT_INPUT_SINGLE_V1:
            # (tokenIn, tokenOut, fee, recipient, deadline, amountIn, minOut, lim)
            out.append(_intent("v3", _aa(args, 0), _aa(args, 1), _au(args, 2),
                               True, _au(args, 5), None, None, _au(args, 6)))
        elif sel == SEL_EXACT_OUTPUT_SINGLE_V1:
            out.append(_intent("v3", _aa(args, 0), _aa(args, 1), _au(args, 2),
                               False, None, _au(args, 5), _au(args, 6), None))
        elif sel in (SEL_EXACT_INPUT, SEL_EXACT_OUTPUT):
            # struct offset -> (bytes path, recipient, amtA, amtB)
            s = _au(args, 0)
            path = _dyn_bytes(args, s + _au(args[s:], 0))
            hops = decode_v3_path(path)
            if len(hops) == 1:
                a, fee, b = hops[0]
                if sel == SEL_EXACT_INPUT:
                    out.append(_intent("v3", a, b, fee, True,
                                       _au(args[s:], 2), None, None,
                                       _au(args[s:], 3)))
                else:  # exactOutput: path is REVERSED (tokenOut first)
                    out.append(_intent("v3", b, a, fee, False, None,
                                       _au(args[s:], 2), _au(args[s:], 3),
                                       None))
        elif sel in (SEL_EXACT_INPUT_V1, SEL_EXACT_OUTPUT_V1):
            # struct (bytes path, recipient, deadline, amtA, amtB)
            s = _au(args, 0)
            path = _dyn_bytes(args, s + _au(args[s:], 0))
            hops = decode_v3_path(path)
            if len(hops) == 1:
                a, fee, b = hops[0]
                if sel == SEL_EXACT_INPUT_V1:
                    out.append(_intent("v3", a, b, fee, True,
                                       _au(args[s:], 3), None, None,
                                       _au(args[s:], 4)))
                else:
                    out.append(_intent("v3", b, a, fee, False, None,
                                       _au(args[s:], 3), _au(args[s:], 4),
                                       None))
        elif sel == SEL_V2_SWAP_EXACT_IN:
            # (amountIn, amountOutMin, address[] path, address to)
            path = _addr_array(args, _au(args, 2))
            if len(path) == 2:
                out.append(_intent("v2", path[0], path[1], None, True,
                                   _au(args, 0), None, None, _au(args, 1)))
        elif sel == SEL_V2_SWAP_EXACT_OUT:
            # (amountOut, amountInMax, address[] path, address to)
            path = _addr_array(args, _au(args, 2))
            if len(path) == 2:
                out.append(_intent("v2", path[0], path[1], None, False,
                                   None, _au(args, 0), _au(args, 1), None))
        elif sel in (SEL_MULTICALL, SEL_MULTICALL_DEADLINE,
                     SEL_MULTICALL_PREVBLOCK):
            arr_word = 0 if sel == SEL_MULTICALL else 1
            for inner in _dyn_bytes_array(args, _au(args, arr_word)):
                out.extend(decode_router_calldata(inner, _depth + 1))
        elif sel in (SEL_UR_EXECUTE, SEL_UR_EXECUTE_NODEADLINE):
            commands = _dyn_bytes(args, _au(args, 0))
            inputs = _dyn_bytes_array(args, _au(args, 1))
            for cmd_byte, inp in zip(commands, inputs):
                cmd = cmd_byte & 0x3f
                if cmd in (UR_V3_SWAP_EXACT_IN, UR_V3_SWAP_EXACT_OUT):
                    # (recipient, amount, amountBound, bytes path, payerIsUser)
                    hops = decode_v3_path(_dyn_bytes(inp, _au(inp, 3)))
                    if len(hops) != 1:
                        continue
                    a, fee, b = hops[0]
                    if cmd == UR_V3_SWAP_EXACT_IN:
                        out.append(_intent("v3", a, b, fee, True,
                                           _au(inp, 1), None, None,
                                           _au(inp, 2)))
                    else:  # exact-out path is reversed
                        out.append(_intent("v3", b, a, fee, False, None,
                                           _au(inp, 1), _au(inp, 2), None))
                elif cmd in (UR_V2_SWAP_EXACT_IN, UR_V2_SWAP_EXACT_OUT):
                    # (recipient, amount, amountBound, address[] path, payer)
                    path = _addr_array(inp, _au(inp, 3))
                    if len(path) != 2:
                        continue
                    if cmd == UR_V2_SWAP_EXACT_IN:
                        out.append(_intent("v2", path[0], path[1], None, True,
                                           _au(inp, 1), None, None,
                                           _au(inp, 2)))
                    else:
                        out.append(_intent("v2", path[0], path[1], None,
                                           False, None, _au(inp, 1),
                                           _au(inp, 2), None))
    except Exception:
        return out
    return out


def _create2(factory: str, salt: bytes, init_hash: bytes) -> str:
    return "0x" + keccak(b"\xff" + bytes.fromhex(factory[2:]) + salt +
                         init_hash)[12:].hex()


def v3_pool_address(token_a: str, token_b: str, fee: int,
                    factory: str = V3_FACTORY) -> str:
    """Uniswap V3 pool address via CREATE2 (canonical init code hash —
    live-verified against factory.getPool on RH chain 2026-07-10)."""
    t0, t1 = sorted((token_a.lower(), token_b.lower()))
    salt = keccak(bytes.fromhex("00" * 12 + t0[2:] + "00" * 12 + t1[2:] +
                                format(fee, "064x")))
    return _create2(factory, salt, V3_INIT_CODE_HASH)


def v2_pair_address(token_a: str, token_b: str,
                    factory: str = V2_CREATE2_FACTORY) -> str:
    """Uniswap V2 pair address via CREATE2 (canonical init code hash —
    verified for factory 0x8bce... only; 0xfc2e... uses a different,
    unknown init code, resolved via the discovery registry instead)."""
    t0, t1 = sorted((token_a.lower(), token_b.lower()))
    salt = keccak(bytes.fromhex(t0[2:] + t1[2:]))
    return _create2(factory, salt, V2_INIT_CODE_HASH)


def intent_to_trade(intent: dict, weth: str = WETH):
    """Swap intent -> ('buy'|'sell', weth_leg_wei, exact) | None.
    buy = trader spends WETH (token_in == WETH).  WETH leg preference:
      buy/exact_in   -> amount_in       (EXACT)
      buy/exact_out  -> amount_in_max   (estimate: user's max spend)
      sell/exact_out -> amount_out      (EXACT: WETH received)
      sell/exact_in  -> amount_out_min  (estimate: user's min receive)
    Token-token swaps (no WETH leg) -> None."""
    ti, to = intent["token_in"], intent["token_out"]
    if ti == weth and to != weth:
        if intent["exact_in"]:
            return ("buy", intent["amount_in"], True)
        return ("buy", intent["amount_in_max"], False)
    if to == weth and ti != weth:
        if intent["exact_in"]:
            return ("sell", intent["amount_out_min"], False)
        return ("sell", intent["amount_out"], True)
    return None


def recover_sender(raw: bytes) -> str:
    """ecrecover the tx sender from a signed envelope ('' on failure).
    coincurve-backed (~0.5ms) — called for taped swaps only."""
    try:
        from eth_account import Account
        return Account.recover_transaction(raw).lower()
    except Exception:
        return ""


# ══════════════════════════════════════════════════════════════════════════════
# Firehose runtime
# ══════════════════════════════════════════════════════════════════════════════
class Firehose:
    def __init__(self, feed: Feed):
        self.feed = feed                 # v1 Feed reused: watch/discovery/price
        self.registry = {}               # pool -> {token, fee, dex, weth0}
        self.token_pools = {}            # token -> [pools] (v2 fallback)
        self.seen_seq = -1
        self.seen_tx = set()             # taped tx hashes (backlog dedupe)
        self.caught_up = False
        self.total_taped = 0
        self.lags = []                   # post-catch-up lag_secs
        self.n_frames = 0
        self.n_msgs = 0
        self.n_txs = 0
        self.n_router_txs = 0
        self.n_intents = 0
        self.n_backlog_skipped = 0
        self.n_est_rows = 0
        self.n_reconnects = 0
        os.makedirs(OUT_DIR, exist_ok=True)
        self.debug_path = os.path.join(OUT_DIR, "fh_debug.jsonl")

    # ── watch-set registry (token/fee survive candidate promotion) ─────────
    def snapshot_pending(self) -> dict:
        return {p: dict(self.feed.cand[p])
                for p in self.feed.pending_sym if p in self.feed.cand}

    def absorb_promotions(self, snap: dict):
        for pool, c in snap.items():
            if pool in self.feed.watch and pool not in self.registry:
                self.registry[pool] = {"token": c["token"], "fee": c["fee"],
                                       "dex": c["dex"], "weth0": c["weth0"]}
                self.token_pools.setdefault(c["token"], []).append(pool)

    def resolve_watched_pool(self, intent: dict):
        """Swap intent -> watched pool address | None (CREATE2 primary,
        registry fallback for the non-canonical V2 factory)."""
        ti, to = intent["token_in"], intent["token_out"]
        token = to if ti == WETH else ti
        if intent["dex"] == "v3":
            pool = v3_pool_address(WETH, token, intent["fee"] or 0)
            return pool if pool in self.feed.watch else None
        pool = v2_pair_address(WETH, token)
        if pool in self.feed.watch:
            return pool
        for p in self.token_pools.get(token, ()):
            w = self.feed.watch.get(p)
            if w and w.get("dex") == "v2":
                return p
        return None

    # ── frame handling ──────────────────────────────────────────────────────
    def handle_frame(self, frame_text: str):
        self.n_frames += 1
        seen_wall = self.feed.rpc.now()  # server-calibrated clock (v1 method)
        for seq, hdr_ts, l2msg in extract_messages(frame_text):
            if seq <= self.seen_seq:
                continue  # backlog replay / duplicate
            self.seen_seq = seq
            self.n_msgs += 1
            lag = seen_wall - hdr_ts
            if not self.caught_up and lag < 3.0:
                self.caught_up = True
                print(f"[fh] caught up at seq={seq} lag={lag:.2f}s",
                      flush=True)
            if lag > BACKLOG_MAX_S:
                self.n_backlog_skipped += 1
                continue
            for raw in split_l2_msg(l2msg):
                self.n_txs += 1
                tx = decode_signed_tx(raw)
                if tx is None or tx["to"] not in ROUTERS:
                    continue
                self.n_router_txs += 1
                intents = decode_router_calldata(tx["data"])
                if not intents:
                    continue
                self.tape_intents(tx, intents, hdr_ts, seen_wall)

    def tape_intents(self, tx: dict, intents: list, hdr_ts: int,
                     seen_wall: float):
        if self.feed.eth_price is None:
            return
        if tx["hash"] in self.seen_tx:
            return
        maker = ""
        taped_any = False
        for i, intent in enumerate(intents):
            self.n_intents += 1
            trade = intent_to_trade(intent)
            if trade is None:
                continue
            pool = self.resolve_watched_pool(intent)
            if pool is None:
                continue
            kind, weth_wei, exact = trade
            # sanity: unset/absurd bounds (e.g. amountInMaximum = uint256 max
            # on exact-output swaps) must never become tape volume
            if not weth_wei or weth_wei > 10 ** 24:  # > 1M ETH = not a trade
                continue
            if not maker:
                maker = recover_sender(tx["raw"])
            w = self.feed.watch[pool]
            row = tape_row(kind=kind, weth_wei=weth_wei,
                           eth_price_usd=self.feed.eth_price,
                           block_ts=hdr_ts, maker=maker, pool=pool,
                           sym=w["sym"], seen_ts=seen_wall)
            row["pre_conf"] = True
            if not exact:
                row["vol_est"] = True
                self.n_est_rows += 1
            _append(os.path.join(OUT_DIR, f"tape_{pool[:12]}.jsonl"), row)
            taped_any = True
            self.total_taped += 1
            if self.caught_up:
                self.lags.append(row["lag_secs"])
            print(f"[fh-tape] {w['sym']:<14} {kind:<4} "
                  f"${row['volume_usd']:>10,.2f} lag={row['lag_secs']:.2f}s"
                  f"{' est' if not exact else ''}", flush=True)
            if DEBUG_TAPE:
                _append(self.debug_path, {
                    "tx": tx["hash"], "pool": pool, "kind": kind,
                    "dex": intent["dex"], "weth_wei": str(weth_wei),
                    "exact": exact, "seen_wall": round(seen_wall, 3),
                    "hdr_ts": hdr_ts, "lag_secs": row["lag_secs"],
                    "sym": w["sym"], "maker": maker,
                    "ts": iso_utc(hdr_ts)})
        if taped_any:
            self.seen_tx.add(tx["hash"])
            if len(self.seen_tx) > 20000:
                self.seen_tx = set(list(self.seen_tx)[-10000:])

    # ── maintenance (blocking; runs in a thread) ────────────────────────────
    def maintenance(self, last_scanned: int) -> int:
        """One v1 poll cycle for discovery/liq/symbol/price upkeep. The
        returned swap logs are DISCARDED (the firehose is the swap source).
        Returns the new last_scanned block."""
        try:
            if (time.time() - self.feed.eth_price_ts) > 300.0:
                self.feed.refresh_eth_price()
            new_last, _swaps = self.feed.poll_cycle(last_scanned)
            snap = self.snapshot_pending()
            self.feed.process_cycle([])  # liq checks + symbol promotions only
            self.absorb_promotions(snap)
            return max(last_scanned, new_last or 0)
        except Exception as e:
            print(f"[fh-maint] {type(e).__name__}: {e}", flush=True)
            return last_scanned

    # ── websocket loop (graceful reconnect, never crashes) ─────────────────
    async def run_ws(self, t_end: float):
        backoff = 1.0
        while time.time() < t_end:
            try:
                async with websockets.connect(
                        WS_URL, max_size=None, open_timeout=15) as ws:
                    print(f"[fh] connected {WS_URL}", flush=True)
                    backoff = 1.0
                    self.caught_up = False
                    while time.time() < t_end:
                        frame = await asyncio.wait_for(ws.recv(), timeout=30)
                        if isinstance(frame, bytes):
                            frame = frame.decode("utf-8", "replace")
                        self.handle_frame(frame)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                if time.time() >= t_end:
                    break
                self.n_reconnects += 1
                print(f"[fh] ws drop ({type(e).__name__}: {e}) — "
                      f"reconnect in {backoff:.0f}s", flush=True)
                await asyncio.sleep(backoff)
                backoff = min(60.0, backoff * 2)

    def stats_line(self, elapsed: float) -> str:
        s = sorted(self.lags)
        return (f"[fh] {elapsed/60:.1f}min: frames={self.n_frames} "
                f"msgs={self.n_msgs} txs={self.n_txs} "
                f"router_txs={self.n_router_txs} taped={self.total_taped} "
                f"(est={self.n_est_rows}) watch={len(self.feed.watch)} "
                f"lag med={pctl(s, 0.5):.2f}s p95={pctl(s, 0.95):.2f}s "
                f"n={len(s)} reconnects={self.n_reconnects}")


async def orchestrate(fh: Firehose, max_minutes: float):
    t_end = time.time() + max_minutes * 60
    ws_task = asyncio.create_task(fh.run_ws(t_end))
    last_scanned = fh.feed.latest_block
    t0 = time.time()
    last_stats = t0
    try:
        while time.time() < t_end and not ws_task.done():
            last_scanned = await asyncio.to_thread(fh.maintenance,
                                                   last_scanned)
            if time.time() - last_stats > 30.0:
                print(fh.stats_line(time.time() - t0), flush=True)
                last_stats = time.time()
            await asyncio.sleep(MAINT_SECS)
    finally:
        ws_task.cancel()
        try:
            await ws_task
        except (asyncio.CancelledError, Exception):
            pass
    print(fh.stats_line(time.time() - t0), flush=True)


def main():
    max_minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0
    feed = Feed(os.environ.get("RH_FEED_RPC", RPC_DEFAULT))

    # FAIL-CLOSED chain check (never tape a different chain into these files)
    cid = int(feed.rpc.call("eth_chainId", []), 16)
    if cid != RH_CHAIN_ID:
        print(f"[fh] FATAL: chain_id={cid}, expected {RH_CHAIN_ID}",
              flush=True)
        sys.exit(1)

    feed.sync_head()
    feed.refresh_eth_price()
    if feed.eth_price is None:
        print("[fh] FATAL: no ETH/USD price (slot0 + GT both failed)",
              flush=True)
        sys.exit(1)
    print(f"[fh] chain {cid} head={feed.latest_block} "
          f"eth=${feed.eth_price:,.2f} ws={WS_URL} "
          f"clock_offset={feed.rpc.clock_offset:+.2f}s out={OUT_DIR}",
          flush=True)

    lookback_blocks = int(LOOKBACK_H * 3600 / max(feed.spb, 0.02))
    feed.backfill_discovery(lookback_blocks)
    print(f"[fh] {len(feed.cand)} candidates queued; firehose starting "
          f"({max_minutes:.0f}min)", flush=True)

    fh = Firehose(feed)
    asyncio.run(orchestrate(fh, max_minutes))

    s = sorted(fh.lags)
    print(f"[fh] done: {fh.total_taped} trades taped "
          f"({fh.n_est_rows} vol_est), {len(feed.watch)} watched pools "
          f"-> {OUT_DIR}", flush=True)
    print(f"[fh] lag: median={pctl(s, 0.5):.2f}s p95={pctl(s, 0.95):.2f}s "
          f"n={len(s)} | msgs={fh.n_msgs} txs={fh.n_txs} "
          f"router_txs={fh.n_router_txs} backlog_skipped="
          f"{fh.n_backlog_skipped} reconnects={fh.n_reconnects}", flush=True)


if __name__ == "__main__":
    main()
