# scripts/rh_chain_feed.py
"""Robinhood Chain keyless ON-CHAIN trade feed — v1 (task #494).

Replaces GeckoTerminal polling (scripts/robinhood_tape_recorder.py: 6 pools
max, 7s pacing) with direct eth_getLogs polling on the public RPC — no vendor,
no key, all pools, near-real-time (~1.5s poll).

LIVE-VERIFIED 2026-07-10 against https://rpc.mainnet.chain.robinhood.com:
  * SwapRouter02.factory() == 0x1F7D7550B1B028f7571E69a784071F0205fd2eFA
    (matches core/rh_execution.UNISWAP_V3_FACTORY) — V3 PoolCreated: ~2.2k
    pools per 100k blocks (~2.8h), overwhelmingly WETH-quoted.
  * Uniswap V2-style factories EXIST and are active (Robinfun V5 graduates
    to V2 per core/rh_execution): 0x8bce...937f (~51 pairs/2.8h, ~1.5k swaps
    per 17min) + 0xfc2e...20a9 — both watched for PairCreated.
  * Batch JSON-RPC works; single-address getLogs over 100k blocks ~0.4s;
    deep multi-address windows time out (-32000) -> we chunk + halve.
  * Keyless ETH/USD: slot0 of the WETH/USDG 0.01% V3 pool
    0x52e65b17fb6e5ba00ed806f37afcd2daa50271ca (token0=WETH, USDG 6 dec);
    GeckoTerminal simple-price is the fallback. Refreshed every 5 min.
  * Block timestamps are COARSE (~1s granularity; consecutive 100ms blocks
    share a timestamp) — lag_secs resolution is therefore ~1s.

Output: EXACT Solana rip_tape schema, one JSON per line, appended to
scratchpad/robinhood_tapes/tape_<pool12>.jsonl (same dir/naming as the GT
recorder so tapes merge):
    {"kind":"buy"|"sell","volume_usd":f,"ts":ISO8601,"maker":wallet,
     "pair":pool,"sym":name,"lag_secs":f}
lag_secs (block-ts -> wall-clock-seen) is an EXTRA field per the latency
mandate — analysis scripts ignore unknown keys. Dedupe internally by
(tx_hash, logIndex); no tx field is written.

kind: buy = pool RECEIVED WETH (trader spent ETH on the token).
volume_usd = |WETH leg| * ETH/USD.  maker = tx `from` (true wallet, matches
GT tx_from_address) via ONE batched eth_getTransactionByHash round per cycle.

Young-pool filter mirrors the GT recorder: liq >= $5k (WETH vault balance
* 2 * ETH/USD) and age <= 24h. Only WETH-quoted pools are taped (kind and
volume need a WETH leg).

READ-ONLY, keyless, per-session (max_minutes arg; no 24/7 assumption).
RPC hiccups (429 / -32000 timeouts / network) retry with backoff and never
crash the loop.

Usage: python scripts/rh_chain_feed.py [max_minutes]
Env:   RH_FEED_RPC (default public RPC), RH_FEED_POLL_SECS (1.5),
       RH_FEED_LOOKBACK_H (6), RH_FEED_WATCH_MAX (150),
       RH_FEED_MIN_LIQ (5000), RH_FEED_MAX_AGE_H (24)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── chain constants (shared with the execution rail where they exist) ────────
try:
    from core.rh_execution import (DEFAULT_RPC_URL as _RPC_DEFAULT,
                                   RH_CHAIN_ID,
                                   UNISWAP_V3_FACTORY as _V3F,
                                   WETH9 as _WETH)
    WETH = _WETH.lower()
    V3_FACTORY = _V3F.lower()
    RPC_DEFAULT = _RPC_DEFAULT
except Exception:  # pragma: no cover - core should always import
    RPC_DEFAULT = "https://rpc.mainnet.chain.robinhood.com"
    RH_CHAIN_ID = 4663
    WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
    V3_FACTORY = "0x1f7d7550b1b028f7571e69a784071f0205fd2efa"

# V2-style factories seen live emitting PairCreated (2026-07-10; the first is
# the dominant one — Robinfun V5 graduation target).
V2_FACTORIES = (
    "0x8bceaa40b9acdfaedf85adf4ff01f5ad6517937f",
    "0xfc2e4da3edb2e18100473339c763705d263d20a9",
)

# WETH/USDG 0.01% V3 pool (token0 = WETH, USDG = 6 decimals) — keyless ETH/USD.
ETH_USD_POOL = "0x52e65b17fb6e5ba00ed806f37afcd2daa50271ca"
ETH_USD_POOL_WETH_IS_T0 = True
ETH_USD_STABLE_DECIMALS = 6

# keccak topic0 of the canonical signatures
TOPIC_V3_SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
TOPIC_V2_SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
TOPIC_POOL_CREATED = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
TOPIC_PAIR_CREATED = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"

# eth_call selectors
SEL_SLOT0 = "0x3850c7bd"
SEL_BALANCE_OF = "0x70a08231"
SEL_SYMBOL = "0x95d89b41"

OUT_DIR = os.path.join("scratchpad", "robinhood_tapes")

# ── knobs (env-overridable) ──────────────────────────────────────────────────
POLL_SECS = float(os.environ.get("RH_FEED_POLL_SECS", "1.5"))
LOOKBACK_H = float(os.environ.get("RH_FEED_LOOKBACK_H", "6"))
WATCH_MAX = int(os.environ.get("RH_FEED_WATCH_MAX", "150"))
MIN_LIQ = float(os.environ.get("RH_FEED_MIN_LIQ", "5000"))
MAX_AGE_H = float(os.environ.get("RH_FEED_MAX_AGE_H", "24"))
CAND_MAX = 5000           # candidate pools kept for liq recheck (age-pruned)
LIQ_PER_CYCLE = 25        # balanceOf liq checks piggybacked on each cycle batch
META_EVERY_CYCLES = 120   # pools_meta snapshot cadence (~3 min at 1.5s)
ETH_PRICE_REFRESH_S = 300.0
BATCH_CHUNK = 80          # max requests per JSON-RPC batch POST (public-RPC safe)
BATCH_PACE_S = 0.3        # pause between batch chunks (resets ban at full speed)
BLOCK_FETCH_MAX = 120     # full-block fetches per cycle (maker + ts source)


# ══════════════════════════════════════════════════════════════════════════════
# PURE decode helpers (no network — unit-tested in tests/test_rh_chain_feed.py)
# ══════════════════════════════════════════════════════════════════════════════
def _s256(word_hex: str) -> int:
    """Signed int256 from a 64-char hex word (two's complement)."""
    v = int(word_hex, 16)
    return v - (1 << 256) if v >= (1 << 255) else v


def _word(data_hex: str, i: int) -> str:
    """i-th 32-byte word of a 0x-prefixed data blob."""
    d = data_hex[2:] if data_hex.startswith("0x") else data_hex
    return d[i * 64:(i + 1) * 64]


def _topic_addr(topic: str) -> str:
    """Lowercase 0x40 address from a 32-byte indexed topic."""
    return "0x" + topic[-40:].lower()


def parse_pool_created(log: dict) -> dict:
    """Uniswap V3 factory PoolCreated(token0,token1,fee idx; tickSpacing,pool).
    -> {pool, token0, token1, fee, dex:'v3', block}"""
    return {
        "pool": "0x" + _word(log["data"], 1)[-40:].lower(),
        "token0": _topic_addr(log["topics"][1]),
        "token1": _topic_addr(log["topics"][2]),
        "fee": int(log["topics"][3], 16),
        "dex": "v3",
        "block": int(log["blockNumber"], 16),
    }


def parse_pair_created(log: dict) -> dict:
    """Uniswap V2 factory PairCreated(token0,token1 idx; pair,allPairsLength).
    -> {pool, token0, token1, fee:None, dex:'v2', block}"""
    return {
        "pool": "0x" + _word(log["data"], 0)[-40:].lower(),
        "token0": _topic_addr(log["topics"][1]),
        "token1": _topic_addr(log["topics"][2]),
        "fee": None,
        "dex": "v2",
        "block": int(log["blockNumber"], 16),
    }


def classify_v3_swap(data_hex: str, weth_is_token0: bool):
    """V3 Swap data = (amount0 int256, amount1 int256, sqrtPriceX96, liquidity,
    tick). Amounts are POOL deltas: >0 pool received. Pool received WETH ->
    trader spent ETH -> BUY of the token. -> ('buy'|'sell', weth_wei) | None."""
    amount0 = _s256(_word(data_hex, 0))
    amount1 = _s256(_word(data_hex, 1))
    weth_delta = amount0 if weth_is_token0 else amount1
    if weth_delta > 0:
        return ("buy", weth_delta)
    if weth_delta < 0:
        return ("sell", -weth_delta)
    return None


def classify_v2_swap(data_hex: str, weth_is_token0: bool):
    """V2 Swap data = (amount0In, amount1In, amount0Out, amount1Out) uint256,
    pool's perspective (In = pool received). Net WETH in -> BUY.
    -> ('buy'|'sell', weth_wei) | None."""
    a0_in = int(_word(data_hex, 0), 16)
    a1_in = int(_word(data_hex, 1), 16)
    a0_out = int(_word(data_hex, 2), 16)
    a1_out = int(_word(data_hex, 3), 16)
    net = (a0_in - a0_out) if weth_is_token0 else (a1_in - a1_out)
    if net > 0:
        return ("buy", net)
    if net < 0:
        return ("sell", -net)
    return None


def sqrtprice_to_eth_usd(sqrt_price_x96: int, weth_is_token0: bool,
                         stable_decimals: int) -> float:
    """ETH/USD from a WETH<->stable V3 pool's slot0 sqrtPriceX96.
    raw price = token1_atomic / token0_atomic = (sqrtP / 2^96)^2."""
    raw = (sqrt_price_x96 / 2 ** 96) ** 2
    if weth_is_token0:
        return raw * 10 ** (18 - stable_decimals)
    return (1.0 / raw) * 10 ** (18 - stable_decimals)


def decode_symbol(hex_result: str) -> str:
    """ERC20 symbol() eth_call result -> str. Handles ABI-string and raw
    bytes32 answers; anything undecodable -> '?'. Pure, never raises."""
    try:
        h = hex_result[2:] if hex_result.startswith("0x") else hex_result
        raw = bytes.fromhex(h)
        s = ""
        if len(raw) >= 64:
            ln = int.from_bytes(raw[32:64], "big")
            if 0 < ln <= len(raw) - 64:
                s = raw[64:64 + ln].decode("utf-8", "replace")
        elif raw:  # bytes32-style symbol
            s = raw.rstrip(b"\x00").decode("utf-8", "replace")
        s = s.replace("�", "").strip()
        return s or "?"
    except Exception:
        return "?"


def iso_utc(epoch: float) -> str:
    """ISO8601 UTC like the GT recorder writes (second resolution)."""
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(epoch))


def tape_row(kind: str, weth_wei: int, eth_price_usd: float, block_ts: int,
             maker: str, pool: str, sym: str, seen_ts: float) -> dict:
    """One rip_tape row (exact Solana schema + lag_secs latency field)."""
    return {
        "kind": kind,
        "volume_usd": round(weth_wei / 1e18 * eth_price_usd, 2),
        "ts": iso_utc(block_ts),
        "maker": maker,
        "pair": pool,
        "sym": sym,
        "lag_secs": round(seen_ts - block_ts, 2),
    }


def dedupe_key(log: dict) -> tuple:
    """(tx_hash, logIndex) — internal dedupe identity of a swap log."""
    return (str(log.get("transactionHash", "")).lower(),
            int(log.get("logIndex", "0x0"), 16)
            if isinstance(log.get("logIndex"), str) else int(log.get("logIndex") or 0))


def pctl(sorted_vals: list, q: float) -> float:
    """Percentile (nearest-rank) of an ASCENDING-sorted list; 0.0 if empty."""
    if not sorted_vals:
        return 0.0
    i = min(len(sorted_vals) - 1, max(0, int(q * len(sorted_vals))))
    return sorted_vals[i]


# ══════════════════════════════════════════════════════════════════════════════
# RPC client (retry/backoff; batch; never crashes the caller loop)
# ══════════════════════════════════════════════════════════════════════════════
class LogRangeTimeout(RuntimeError):
    """Server-side -32000 'log query timed out' — caller should shrink range."""


class Rpc:
    def __init__(self, url: str):
        self.url = url
        self._id = 0
        self.n_429 = 0
        self.n_timeout = 0
        # local-clock -> server-clock offset (secs to ADD to time.time()).
        # Local Windows clocks drift several seconds (measured -4.2s on
        # 2026-07-10) which corrupts lag_secs; we calibrate against the RPC's
        # own HTTP Date header (1s granularity, same infra as the chain).
        self.clock_offset = 0.0
        self._clock_ts = 0.0

    def _post(self, payload, timeout=20):
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json",
                     "User-Agent": "Mozilla/5.0",
                     "Accept": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            t1 = time.time()
            if t1 - self._clock_ts > 60.0 and (t1 - t0) < 1.0:
                try:
                    srv = parsedate_to_datetime(r.headers["Date"]).timestamp()
                    # Date is second-truncated -> +0.5 recenters the bucket
                    self.clock_offset = srv + 0.5 - (t0 + t1) / 2.0
                    self._clock_ts = t1
                except Exception:
                    pass
            return json.load(r)

    def now(self) -> float:
        """Server-calibrated wall clock (for lag measurement)."""
        return time.time() + self.clock_offset

    def call(self, method: str, params: list, tries: int = 5):
        """Single call. Retries 429/network with backoff; -32000 log-timeout
        raises LogRangeTimeout immediately (caller halves the range)."""
        last = None
        for i in range(tries):
            self._id += 1
            try:
                out = self._post({"jsonrpc": "2.0", "id": self._id,
                                  "method": method, "params": params})
                err = out.get("error")
                if err:
                    if "timed out" in str(err.get("message", "")):
                        self.n_timeout += 1
                        raise LogRangeTimeout(str(err))
                    raise RuntimeError(f"{method}: {err}")
                return out["result"]
            except LogRangeTimeout:
                raise
            except urllib.error.HTTPError as e:
                last = e
                if e.code == 429:
                    self.n_429 += 1
                    time.sleep(1.5 * (i + 1))
                    continue
                time.sleep(0.8 * (i + 1))
            except Exception as e:  # URLError / conn reset / timeout / bad JSON
                last = e
                # connection resets = the RPC's hard throttle: back off HARD
                time.sleep(4.0 * (i + 1)
                           if "10054" in str(e) or "reset" in str(e).lower()
                           else 0.8 * (i + 1))
        raise RuntimeError(f"{method} failed after {tries} tries: {last}")

    def batch(self, reqs: list) -> dict:
        """Batched calls -> {id: result}. Per-item errors -> missing id.
        Chunked at BATCH_CHUNK; 429 retried per chunk."""
        results = {}
        for ofs in range(0, len(reqs), BATCH_CHUNK):
            chunk = []
            for j, (method, params) in enumerate(reqs[ofs:ofs + BATCH_CHUNK]):
                chunk.append({"jsonrpc": "2.0", "id": ofs + j,
                              "method": method, "params": params})
            for i in range(3):
                try:
                    out = self._post(chunk)
                    if isinstance(out, dict):  # error envelope
                        break
                    for o in out:
                        if o.get("result") is not None:
                            results[o["id"]] = o["result"]
                    break
                except urllib.error.HTTPError as e:
                    if e.code == 429 and i < 2:
                        self.n_429 += 1
                        time.sleep(2.0 * (i + 1))
                        continue
                    break
                except Exception as e:
                    # conn reset = hard throttle: long backoff, then give up on
                    # this chunk (missing ids just wait for the next round)
                    time.sleep(5.0 * (i + 1)
                               if "10054" in str(e) or "reset" in str(e).lower()
                               else 1.0 * (i + 1))
            if ofs + BATCH_CHUNK < len(reqs):
                time.sleep(BATCH_PACE_S)  # pace multi-chunk batches
        return results


# ══════════════════════════════════════════════════════════════════════════════
# Feed
# ══════════════════════════════════════════════════════════════════════════════
def _append(path, rec):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, separators=(",", ":")) + "\n")


class Feed:
    def __init__(self, rpc_url: str):
        self.rpc = Rpc(rpc_url)
        self.watch = {}       # pool -> {sym, dex, weth0, liq, created_block, seen}
        self.cand = {}        # pool -> {dex, weth0, token, created_block, fee}
        self.block_ts = {}    # block_number -> timestamp (cache)
        self.eth_price = None
        self.eth_price_ts = 0.0
        self.spb = 0.1        # sec/block, calibrated live
        self.liq_queue = []   # pools awaiting a liq check (amortized per cycle)
        self.pending_sym = {} # pool -> [liq, tries]: promotion awaiting symbol()
        self.latest_block = 0
        self.latest_ts = 0
        self.total_taped = 0
        self.all_lags = []
        os.makedirs(OUT_DIR, exist_ok=True)
        self.meta_path = os.path.join(OUT_DIR, "pools_meta.jsonl")

    # ── time / price ─────────────────────────────────────────────────────
    def note_head(self, blk: dict):
        n, ts = int(blk["number"], 16), int(blk["timestamp"], 16)
        if self.latest_block and n > self.latest_block and ts > self.latest_ts:
            self.spb = max(0.02, min(2.0,
                (ts - self.latest_ts) / (n - self.latest_block)))
        if n >= self.latest_block:
            self.latest_block, self.latest_ts = n, ts
            self.block_ts[n] = ts

    def sync_head(self):
        self.note_head(self.rpc.call("eth_getBlockByNumber", ["latest", False]))

    def est_block_ts(self, block: int) -> int:
        if block in self.block_ts:
            return self.block_ts[block]
        return int(self.latest_ts - (self.latest_block - block) * self.spb)

    def age_h(self, created_block: int) -> float:
        return max(0.0, (self.latest_block - created_block) * self.spb / 3600.0)

    def refresh_eth_price(self):
        """slot0 of the WETH/USDG pool; GT simple-price fallback; keeps last
        known value on any failure (taping skips while price is None)."""
        try:
            r = self.rpc.call("eth_call", [{"to": ETH_USD_POOL,
                                            "data": SEL_SLOT0}, "latest"])
            p = sqrtprice_to_eth_usd(int(_word(r, 0), 16),
                                     ETH_USD_POOL_WETH_IS_T0,
                                     ETH_USD_STABLE_DECIMALS)
            if 50.0 < p < 1_000_000.0:
                self.eth_price = p
                self.eth_price_ts = time.time()
                return
        except Exception as e:
            print(f"[price] slot0 failed: {type(e).__name__}: {e}", flush=True)
        try:  # keyless GT fallback, only when on-chain read failed
            url = ("https://api.geckoterminal.com/api/v2/simple/networks/"
                   f"robinhood/token_price/{WETH}")
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=15) as r:
                d = json.load(r)
            prices = ((d.get("data") or {}).get("attributes") or {}) \
                .get("token_prices") or {}
            p = float(prices.get(WETH) or prices.get(WETH.lower()) or 0)
            if 50.0 < p < 1_000_000.0:
                self.eth_price = p
                self.eth_price_ts = time.time()
        except Exception as e:
            print(f"[price] GT fallback failed: {type(e).__name__}", flush=True)

    # ── discovery ────────────────────────────────────────────────────────
    def _ingest_creation(self, log):
        t0 = log["topics"][0].lower()
        try:
            info = (parse_pool_created(log) if t0 == TOPIC_POOL_CREATED
                    else parse_pair_created(log))
        except Exception:
            return
        pool = info["pool"]
        if pool in self.watch or pool in self.cand:
            return
        if WETH not in (info["token0"], info["token1"]):
            return  # only WETH-quoted pools are classifiable/tapeable
        self.cand[pool] = {
            "dex": info["dex"],
            "weth0": info["token0"] == WETH,
            "token": info["token1"] if info["token0"] == WETH else info["token0"],
            "created_block": info["block"],
            "fee": info["fee"],
        }

    def backfill_discovery(self, lookback_blocks: int):
        """Trailing-window PoolCreated/PairCreated scan, chunked with
        halve-on-timeout (deep windows can -32000 on the public node)."""
        addrs = [V3_FACTORY] + list(V2_FACTORIES)
        topics = [[TOPIC_POOL_CREATED, TOPIC_PAIR_CREATED]]
        frm = max(1, self.latest_block - lookback_blocks)
        chunk = 90_000
        n_logs = 0
        while frm <= self.latest_block:
            to = min(frm + chunk - 1, self.latest_block)
            try:
                logs = self.rpc.call("eth_getLogs", [{
                    "fromBlock": hex(frm), "toBlock": hex(to),
                    "address": addrs, "topics": topics}])
                for lg in logs:
                    self._ingest_creation(lg)
                n_logs += len(logs)
                frm = to + 1
                time.sleep(0.3)
            except LogRangeTimeout:
                if chunk <= 5_000:
                    print(f"[disc] backfill skipping {frm}..{to} (timeouts)",
                          flush=True)
                    frm = to + 1
                    chunk = 20_000
                else:
                    chunk //= 2
                time.sleep(1.0)
            except Exception as e:
                print(f"[disc] backfill window failed: {e}", flush=True)
                frm = to + 1
                time.sleep(1.0)
        print(f"[disc] backfill: {n_logs} creations -> {len(self.cand)} "
              f"WETH-quoted candidates", flush=True)

    def _refill_liq_queue(self):
        """Rebuild the amortized liq-check queue: age-prune, cap sets, then
        queue = watched + candidates (newest first). LIQ_PER_CYCLE of these
        ride along on each cycle's batch — a full blocking sweep at startup
        got the public RPC to escalate from 429s to TLS connection resets
        (measured 2026-07-10), so liquidity checking is amortized instead."""
        for p in [p for p, c in self.cand.items()
                  if self.age_h(c["created_block"]) > MAX_AGE_H]:
            del self.cand[p]
            self.pending_sym.pop(p, None)
        for p in [p for p, w in self.watch.items()
                  if self.age_h(w["created_block"]) > MAX_AGE_H]:
            print(f"[disc] -{self.watch[p]['sym']} aged out (>{MAX_AGE_H:.0f}h)",
                  flush=True)
            del self.watch[p]
        if len(self.watch) > WATCH_MAX:
            keep = sorted(self.watch.items(), key=lambda kv: -kv[1]["liq"])[:WATCH_MAX]
            dropped = set(self.watch) - {k for k, _ in keep}
            self.watch = dict(keep)
            print(f"[disc] watch cap {WATCH_MAX}: dropped {len(dropped)} "
                  f"lowest-liq pools", flush=True)
        if len(self.cand) > CAND_MAX:
            keep = sorted(self.cand.items(),
                          key=lambda kv: -kv[1]["created_block"])[:CAND_MAX]
            self.cand = dict(keep)
        self.liq_queue = list(self.watch) + sorted(
            self.cand, key=lambda p: -self.cand[p]["created_block"])

    def snapshot_meta(self):
        for pool, w in self.watch.items():
            _append(self.meta_path, {
                "ev": "snapshot", "pool": pool, "sym": w["sym"],
                "liq": round(w.get("liq") or 0.0, 2),
                "age_h": round(self.age_h(w["created_block"]), 2),
                "ts": iso_utc(time.time())})

    # ── tape ─────────────────────────────────────────────────────────────
    def _filter_swaps(self, logs: list) -> list:
        """Split a mixed getLogs answer: ingest creations, return the swap
        logs that belong to watched pools."""
        swaps = []
        for lg in logs:
            t0 = (lg.get("topics") or [""])[0].lower()
            if t0 in (TOPIC_POOL_CREATED, TOPIC_PAIR_CREATED):
                self._ingest_creation(lg)
            elif t0 in (TOPIC_V3_SWAP, TOPIC_V2_SWAP):
                if lg.get("address", "").lower() in self.watch:
                    swaps.append(lg)
        return swaps

    def poll_cycle(self, last_scanned: int):
        """ONE batched POST per cycle: latest head + all logs since
        last_scanned (swaps on watched pools, creations on the factories).
        Returns (new_last_scanned | None, swap_logs). None = the getLogs leg
        failed/timed out — caller retries next cycle (dedupe absorbs overlap)."""
        addrs = list(self.watch) + [V3_FACTORY] + list(V2_FACTORIES)
        res = self.rpc.batch([
            ("eth_getBlockByNumber", ["latest", False]),
            ("eth_getLogs", [{"fromBlock": hex(last_scanned + 1),
                              "toBlock": "latest", "address": addrs,
                              "topics": [[TOPIC_V3_SWAP, TOPIC_V2_SWAP,
                                          TOPIC_POOL_CREATED,
                                          TOPIC_PAIR_CREATED]]}]),
        ])
        head, logs = res.get(0), res.get(1)
        if head:
            self.note_head(head)
        if logs is None:
            return None, []
        return self.latest_block, self._filter_swaps(logs)

    def poll_bounded(self, frm: int, to: int) -> list:
        """Catch-up fallback: bounded single getLogs (used when the unbounded
        leg keeps timing out after a stall)."""
        addrs = list(self.watch) + [V3_FACTORY] + list(V2_FACTORIES)
        logs = self.rpc.call("eth_getLogs", [{
            "fromBlock": hex(frm), "toBlock": hex(to), "address": addrs,
            "topics": [[TOPIC_V3_SWAP, TOPIC_V2_SWAP,
                        TOPIC_POOL_CREATED, TOPIC_PAIR_CREATED]]}])
        return self._filter_swaps(logs)

    def process_cycle(self, swap_logs: list) -> list:
        """Decode swaps -> tape rows, with ONE combined batch RPC round:
        tx `from` (maker) + exact block timestamps + LIQ_PER_CYCLE amortized
        balanceOf liq checks + pending symbol() promotions.
        Returns the lag_secs values of freshly taped rows."""
        if self.eth_price is None:
            return []
        seen_wall = self.rpc.now()  # server-calibrated (see Rpc.clock_offset)
        pending = []
        for lg in swap_logs:
            pool = lg["address"].lower()
            w = self.watch.get(pool)
            if w is None:
                continue
            key = dedupe_key(lg)
            if key in w["seen"]:
                continue
            t0 = lg["topics"][0].lower()
            try:
                if t0 == TOPIC_V3_SWAP:
                    res = classify_v3_swap(lg["data"], w["weth0"])
                else:
                    res = classify_v2_swap(lg["data"], w["weth0"])
            except Exception:
                res = None
            if res is None:
                continue
            kind, weth_wei = res
            pending.append({"pool": pool, "kind": kind, "weth_wei": weth_wei,
                            "key": key, "block": int(lg["blockNumber"], 16),
                            "tx": key[0],
                            "fallback_maker": _topic_addr(lg["topics"][2])
                            if len(lg.get("topics") or []) >= 3 else ""})
        # amortized liq checks: LIQ_PER_CYCLE pools per cycle, newest first
        if not self.liq_queue:
            self._refill_liq_queue()
        liq_pools = []
        while self.liq_queue and len(liq_pools) < LIQ_PER_CYCLE:
            p = self.liq_queue.pop(0)
            if p in self.cand or p in self.watch:
                liq_pools.append(p)
        sym_pools = [p for p in list(self.pending_sym) if p in self.cand]

        # ONE combined batched round. Maker + exact block ts both come from
        # FULL block bodies (tx objects carry `from`): one getBlockByNumber
        # per swap block replaces one getTransactionByHash per swap — the
        # per-tx variant saturated the public RPC at ~8 swaps/s (run 3).
        blocks = sorted({p["block"] for p in pending})[:BLOCK_FETCH_MAX]
        reqs = ([("eth_getBlockByNumber", [hex(b), True]) for b in blocks] +
                [("eth_call", [{"to": WETH,
                                "data": SEL_BALANCE_OF + "0" * 24 + p[2:]},
                               "latest"]) for p in liq_pools] +
                [("eth_call", [{"to": self.cand[p]["token"],
                                "data": SEL_SYMBOL}, "latest"])
                 for p in sym_pools])
        res = self.rpc.batch(reqs) if reqs else {}
        tx_from = {}
        for j, b in enumerate(blocks):
            r = res.get(j)
            if not r:
                continue
            if r.get("timestamp"):
                self.block_ts[b] = int(r["timestamp"], 16)
            for tx in r.get("transactions") or []:
                if isinstance(tx, dict) and tx.get("hash") and tx.get("from"):
                    tx_from[str(tx["hash"]).lower()] = tx["from"].lower()
        if len(self.block_ts) > 5000:
            for b in sorted(self.block_ts)[:2500]:
                del self.block_ts[b]

        # liq results -> refresh watched / stage promotions
        base = len(blocks)
        for i, pool in enumerate(liq_pools):
            r = res.get(base + i)
            if r is None:
                continue
            try:
                liq = int(r, 16) / 1e18 * 2.0 * self.eth_price
            except (ValueError, TypeError):
                continue
            if pool in self.watch:
                self.watch[pool]["liq"] = liq
            elif liq >= MIN_LIQ and pool not in self.pending_sym:
                self.pending_sym[pool] = [liq, 0]

        # symbol results -> complete promotions
        base += len(liq_pools)
        for j, pool in enumerate(sym_pools):
            r = res.get(base + j)
            st = self.pending_sym.get(pool)
            if st is None or pool not in self.cand:
                self.pending_sym.pop(pool, None)
                continue
            if r is None and st[1] < 3:
                st[1] += 1  # retry the symbol fetch next cycle
                continue
            liq = st[0]
            self.pending_sym.pop(pool, None)
            c = self.cand.pop(pool)
            sym = decode_symbol(r or "0x")
            age = self.age_h(c["created_block"])
            self.watch[pool] = {"sym": sym, "dex": c["dex"],
                                "weth0": c["weth0"], "liq": liq,
                                "created_block": c["created_block"],
                                "seen": set()}
            _append(self.meta_path, {
                "ev": "discovered", "pool": pool, "sym": sym,
                "liq": round(liq, 2), "age_h": round(age, 2),
                "dex": c["dex"], "fee": c["fee"], "ts": iso_utc(time.time())})
            print(f"[disc] +{sym} ({c['dex']}) liq=${liq:,.0f} "
                  f"age={age:.1f}h", flush=True)

        lags = []
        per_pool = {}
        for p in pending:
            w = self.watch[p["pool"]]
            w["seen"].add(p["key"])
            if len(w["seen"]) > 8000:
                w["seen"] = set(list(w["seen"])[-4000:])
            row = tape_row(
                kind=p["kind"], weth_wei=p["weth_wei"],
                eth_price_usd=self.eth_price,
                block_ts=self.est_block_ts(p["block"]),
                maker=tx_from.get(p["tx"], p["fallback_maker"]),
                pool=p["pool"], sym=w["sym"], seen_ts=seen_wall)
            _append(os.path.join(OUT_DIR, f"tape_{p['pool'][:12]}.jsonl"), row)
            lags.append(row["lag_secs"])
            per_pool[p["pool"]] = per_pool.get(p["pool"], 0) + 1
        self.total_taped += len(lags)
        self.all_lags.extend(lags)
        for pool, n in per_pool.items():
            print(f"[tape] {self.watch[pool]['sym']:<14} +{n:3d} trades "
                  f"(total {self.total_taped})", flush=True)
        return lags


def main():
    max_minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0
    rpc_url = os.environ.get("RH_FEED_RPC", RPC_DEFAULT)
    feed = Feed(rpc_url)

    # FAIL-CLOSED chain check (never tape a different chain into these files)
    cid = int(feed.rpc.call("eth_chainId", []), 16)
    if cid != RH_CHAIN_ID:
        print(f"[rh-feed] FATAL: chain_id={cid}, expected {RH_CHAIN_ID}",
              flush=True)
        sys.exit(1)

    feed.sync_head()
    feed.refresh_eth_price()
    if feed.eth_price is None:
        print("[rh-feed] FATAL: no ETH/USD price (slot0 + GT both failed)",
              flush=True)
        sys.exit(1)
    print(f"[rh-feed] chain {cid} head={feed.latest_block} "
          f"eth=${feed.eth_price:,.2f} poll={POLL_SECS}s "
          f"liq>=${MIN_LIQ:.0f} age<={MAX_AGE_H:.0f}h watch<={WATCH_MAX} "
          f"clock_offset={feed.rpc.clock_offset:+.2f}s", flush=True)

    lookback_blocks = int(LOOKBACK_H * 3600 / max(feed.spb, 0.02))
    feed.backfill_discovery(lookback_blocks)
    print(f"[rh-feed] {len(feed.cand)} candidates queued for amortized liq "
          f"checks ({LIQ_PER_CYCLE}/cycle, newest first) — recording "
          f"{max_minutes:.0f}min", flush=True)

    t_end = time.time() + max_minutes * 60
    feed.sync_head()  # tape from NOW — backfill time is not catch-up
    last_scanned = feed.latest_block
    cycle = 0
    poll = POLL_SECS
    cycle_durs = []
    misses = 0
    while time.time() < t_end:
        cycle += 1
        t0 = time.time()
        n429_before = feed.rpc.n_429
        try:
            if time.time() - feed.eth_price_ts > ETH_PRICE_REFRESH_S:
                feed.refresh_eth_price()
            new_last, swaps = feed.poll_cycle(last_scanned)
            if new_last is not None:
                last_scanned = max(last_scanned, new_last)
                misses = 0
            else:
                misses += 1
                if misses >= 3:  # unbounded leg keeps failing: bounded catch-up
                    try:
                        feed.sync_head()
                        to = min(last_scanned + 2000, feed.latest_block)
                        if to > last_scanned:
                            swaps = feed.poll_bounded(last_scanned + 1, to)
                            last_scanned = to
                            misses = 0
                    except (LogRangeTimeout, RuntimeError) as e:
                        print(f"[cycle {cycle}] catch-up failed: {e}", flush=True)
            lags = feed.process_cycle(swaps)
            if lags:
                s = sorted(lags)
                print(f"[lat] median_lag={pctl(s, 0.5):.2f}s "
                      f"p95={pctl(s, 0.95):.2f}s n={len(s)}", flush=True)
            if cycle % META_EVERY_CYCLES == 0:
                feed.snapshot_meta()
            if cycle % 20 == 0:
                s = sorted(feed.all_lags)
                print(f"[cycle {cycle}] watching {len(feed.watch)} pools "
                      f"({len(feed.cand)} cand) | trades {feed.total_taped} "
                      f"| lag med={pctl(s, 0.5):.2f}s p95={pctl(s, 0.95):.2f}s "
                      f"| 429s={feed.rpc.n_429}", flush=True)
        except Exception as e:
            print(f"[cycle {cycle}] {type(e).__name__}: {e}", flush=True)
            time.sleep(min(10.0, poll * 3))
        dur = time.time() - t0
        cycle_durs.append(dur)
        # adaptive pacing: back off while the RPC throttles, decay back after
        if feed.rpc.n_429 > n429_before:
            poll = min(10.0, poll * 1.5)
        else:
            poll = max(POLL_SECS, poll * 0.9)
        time.sleep(max(0.0, poll - dur))

    s = sorted(feed.all_lags)
    d = sorted(cycle_durs)
    print(f"[rh-feed] done: {feed.total_taped} trades, "
          f"{len(feed.watch)} watched pools -> {OUT_DIR}", flush=True)
    print(f"[rh-feed] lag: median={pctl(s, 0.5):.2f}s p95={pctl(s, 0.95):.2f}s "
          f"n={len(s)} | cycle_dur median={pctl(d, 0.5):.2f}s "
          f"p95={pctl(d, 0.95):.2f}s | 429s={feed.rpc.n_429} "
          f"log_timeouts={feed.rpc.n_timeout}", flush=True)


if __name__ == "__main__":
    main()
