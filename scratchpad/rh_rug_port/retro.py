# scratchpad/rh_rug_port/retro.py
"""Retrospective at-entry rug-signal reconstruction for labeled RH pools.

For each case (rug or survivor control), reconstruct what was READABLE AT OUR
ENTRY TIME purely from event logs (RH public RPC has NO archive state, so
historical balanceOf fails — but full Transfer/Mint/Burn logs exist):

  1. entry block  = binary search block timestamps around our ledger entry ts
  2. pool created = PoolCreated/PairCreated log on the factory filtered by token
  3. holder map   = replay ERC20 Transfer logs from token genesis -> entry block
       -> total supply, pool %, top1/top10 % (ex-pool/ex-burn/ex-token-contract),
          shoulder_11_20, n_holders, creator (first mint recipient) remaining %
  4. LP custody   = pool Mint/Burn events -> net liquidity per owner; owner
       classified contract vs EOA via eth_getCode (immutable, read at latest)
  5. same holder map replayed to HEAD -> what actually happened after (did the
       pool drain / creator dump), for the hit/miss table.

Costs (RPC calls, logs, seconds) measured per case — this decides what is
affordable per-entry in the paper lane.

READ-ONLY, keyless, paced (a live lane session may share the public RPC).
Usage: python scratchpad/rh_rug_port/retro.py [case ...]   (default: all)
Output: scratchpad/rh_rug_port/retro_<name>.json + console summary.
"""
import calendar
import json
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(os.path.dirname(_HERE))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.join(_ROOT, "scripts"))

from rh_chain_feed import Rpc, RPC_DEFAULT, LogRangeTimeout, _word  # noqa: E402

WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
V3_FACTORY = "0x1f7d7550b1b028f7571e69a784071f0205fd2efa"
V2_FACTORIES = ("0x8bceaa40b9acdfaedf85adf4ff01f5ad6517937f",
                "0xfc2e4da3edb2e18100473339c763705d263d20a9")

TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOPIC_POOL_CREATED = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
TOPIC_PAIR_CREATED = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
# Uniswap V3 pool events (canonical)
TOPIC_V3_MINT = "0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde"
TOPIC_V3_BURN = "0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c"

ZERO = "0x" + "0" * 40
DEAD = "0x000000000000000000000000000000000000dead"

PACE_S = 0.15          # between single calls (live lane may share the RPC)
CHUNK0 = 400_000       # initial getLogs window; halved on timeout/overflow

# ── the labeled cases ────────────────────────────────────────────────────────
CASES = [
    # name, pool, token, entry ts (our ledger), label
    dict(name="CASHCATGAME", label="RUG(-97.7%)",
         pool="0xa63dfae2d5f6f40dc23e7b99b1f137dcc22dd310",
         token="0x9c358f9dd2d374fb996c76314692944f5523a776",
         entry_ts="2026-07-11T03:39:05"),
    dict(name="MONSIEUR", label="RUG(-94% post-exit)",
         pool="0x5839405cbdb54cc99d934c2bf8e06dd388223fd9",
         token="0x133f1bc183e20bbc4aac114e4ef6c893576dadc0",
         entry_ts="2026-07-10T21:33:12"),
    dict(name="Halp", label="RUG(-90%)",
         pool="0x8fe3889cbec2af20df0982556c40deadad707bfb",
         token="0x1746a62bb1425633635f41b08bf1713f5f128239",
         entry_ts="2026-07-10T16:52:51"),
    dict(name="TREAT", label="RUG(-65% pop)",
         pool="0x9925048c66b7f650619a59efb1b87cc6e9db9b58",
         token="0xb08534ad0c71b87d5826f3ad971bd94a898cc5c3",
         entry_ts="2026-07-10T16:52:51"),
    dict(name="KUNA", label="RUG(-69% pop)",
         pool="0xd139e1ad29d6cfa86828f3c9f74cb28625f2e3b4",
         token="0x87df8bb4e5f8e53ff3bc8a26f7edfbf409017e58",
         entry_ts="2026-07-10T22:53:13"),
    # ── survivor controls (aged pools, still alive; tokens from registry) ──
    dict(name="Ape", label="SURVIVOR",
         pool="0x8b35e235e9c9da99bce1f64e13e3a9ef8766084d", token=None,
         entry_ts="2026-07-10T21:33:12"),
    dict(name="RANGER", label="SURVIVOR",
         pool="0x9e9038860b777b977eb08421eeaf99bc8673bacf", token=None,
         entry_ts="2026-07-10T21:33:12"),
    dict(name="hehe", label="SURVIVOR",
         pool="0xed30eadbc93277aff0a349f22198207fb79c3539", token=None,
         entry_ts="2026-07-10T21:33:12"),
    dict(name="BILLY", label="SURVIVOR",
         pool="0xd60990c1d9b9612d0e7b7351d83e36a0356e3b20", token=None,
         entry_ts="2026-07-10T21:33:12"),
]

REGISTRY = os.path.join(_ROOT, "scratchpad", "rh_history", "pools_registry.jsonl")


class Meter:
    def __init__(self):
        self.calls = 0
        self.logs = 0
        self.t0 = time.time()

    def snap(self):
        return {"rpc_calls": self.calls, "logs": self.logs,
                "secs": round(time.time() - self.t0, 1)}


def call(rpc, m, method, params):
    m.calls += 1
    time.sleep(PACE_S)
    return rpc.call(method, params)


def iso_to_epoch(s):
    """Ledger ts strings are UTC — timegm, NOT mktime (mktime applies the
    local CDT/CST offset and was putting entries 1h early, i.e. before the
    young pools even existed)."""
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%S"))


def block_at_ts(rpc, m, target_epoch, head_block):
    """Binary search the last block with ts <= target."""
    lo, hi = 1, head_block
    while lo < hi:
        mid = (lo + hi + 1) // 2
        b = call(rpc, m, "eth_getBlockByNumber", [hex(mid), False])
        if b is None:
            hi = mid - 1
            continue
        ts = int(b["timestamp"], 16)
        if ts <= target_epoch:
            lo = mid
        else:
            hi = mid - 1
    return lo


def get_logs_chunked(rpc, m, address, topics, frm, to, chunk=CHUNK0):
    """Chunked getLogs with halve-on-timeout/overflow. Returns list of logs."""
    out = []
    f = frm
    while f <= to:
        t = min(f + chunk - 1, to)
        try:
            logs = call(rpc, m, "eth_getLogs", [{
                "fromBlock": hex(f), "toBlock": hex(t),
                "address": address, "topics": topics}])
            if len(logs) >= 10_000 and chunk > 2_000:
                chunk //= 4      # answer truncated at cap: redo smaller
                continue
            out.extend(logs)
            m.logs += len(logs)
            f = t + 1
        except (LogRangeTimeout, RuntimeError) as e:
            if chunk <= 2_000:
                print(f"    [warn] getLogs {f}..{t} failed hard: {e}")
                f = t + 1
                chunk = 20_000
            else:
                chunk //= 2
            time.sleep(1.0)
    return out


def registry_lookup(pool):
    """token + creation block from the local pools registry (no RPC)."""
    pl = pool.lower()
    with open(REGISTRY, encoding="utf-8") as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("pool") == pl:
                tok = r["token1"] if r["token0"] == WETH else r["token0"]
                return tok, int(r["block"]), r.get("dex")
    return None, None, None


def find_pool_creation(rpc, m, token, head):
    """PoolCreated/PairCreated on the factories filtered by token topic.
    Narrow topic filter -> full-range getLogs is cheap server-side."""
    pad = "0x" + "0" * 24 + token[2:].lower()
    for topics in ([TOPIC_POOL_CREATED, pad], [TOPIC_POOL_CREATED, None, pad],
                   [TOPIC_PAIR_CREATED, pad], [TOPIC_PAIR_CREATED, None, pad]):
        try:
            logs = call(rpc, m, "eth_getLogs", [{
                "fromBlock": "0x1", "toBlock": hex(head),
                "address": [V3_FACTORY] + list(V2_FACTORIES),
                "topics": topics}])
        except (LogRangeTimeout, RuntimeError):
            continue
        m.logs += len(logs)
        if logs:
            return int(logs[0]["blockNumber"], 16)
    return None


def replay_transfers(logs, upto_block=None):
    """Transfer logs -> balances dict + total supply (mint-burn) + first mint."""
    bal = {}
    minted = burned = 0
    first_mint = None
    for lg in logs:
        blk = int(lg["blockNumber"], 16)
        if upto_block is not None and blk > upto_block:
            continue
        tps = lg.get("topics") or []
        if len(tps) < 3:
            continue
        src = "0x" + tps[1][-40:].lower()
        dst = "0x" + tps[2][-40:].lower()
        try:
            v = int(lg["data"], 16)
        except (ValueError, TypeError):
            continue
        if src == ZERO:
            minted += v
            if first_mint is None:
                first_mint = {"to": dst, "block": blk,
                              "tx": lg.get("transactionHash")}
        else:
            bal[src] = bal.get(src, 0) - v
        if dst in (ZERO,):
            burned += v
        else:
            bal[dst] = bal.get(dst, 0) + v
    supply = minted - burned
    return bal, supply, first_mint


def holder_structure(bal, supply, pool, token, extra_exclude=()):
    """The hidden-supply feature set, EX-pool/EX-burn/EX-token-contract."""
    if supply <= 0:
        return None
    excl = {pool.lower(), token.lower(), ZERO, DEAD} | {a.lower() for a in extra_exclude}
    pool_pct = bal.get(pool.lower(), 0) / supply * 100.0
    holders = sorted(((a, v) for a, v in bal.items()
                      if v > 0 and a not in excl), key=lambda x: -x[1])
    pcts = [v / supply * 100.0 for _, v in holders]
    return {
        "pool_pct_of_supply": round(pool_pct, 2),
        "token_contract_pct": round(bal.get(token.lower(), 0) / supply * 100.0, 2),
        "dead_pct": round(bal.get(DEAD, 0) / supply * 100.0, 2),
        "n_holders": len(holders),
        "top1_pct": round(pcts[0], 2) if pcts else 0.0,
        "top10_pct": round(sum(pcts[:10]), 2),
        "shoulder_11_20_pct": round(sum(pcts[10:20]), 2),
        "top10_addrs": [a for a, _ in holders[:10]],
    }


def lp_custody_v3(rpc, m, pool, frm, to):
    """V3 Mint/Burn events -> net liquidity per owner + owner class."""
    logs = get_logs_chunked(rpc, m, pool, [[TOPIC_V3_MINT, TOPIC_V3_BURN]],
                            frm, to)
    liq = {}
    for lg in logs:
        t0 = lg["topics"][0].lower()
        owner = "0x" + lg["topics"][1][-40:].lower()
        try:
            amt = int(_word(lg["data"], 1 if t0 == TOPIC_V3_MINT else 0), 16)
        except (ValueError, IndexError):
            continue
        liq[owner] = liq.get(owner, 0) + (amt if t0 == TOPIC_V3_MINT else -amt)
    out = []
    for owner, net in sorted(liq.items(), key=lambda x: -x[1]):
        try:
            code = call(rpc, m, "eth_getCode", [owner, "latest"])
            is_contract = bool(code and code != "0x")
        except RuntimeError:
            is_contract = None
        out.append({"owner": owner, "net_liquidity": str(net),
                    "is_contract": is_contract})
    return out, len(logs)


def lp_custody_v2(rpc, m, pool, frm, to):
    """V2: LP token IS the pair — Transfer logs on the pair address."""
    logs = get_logs_chunked(rpc, m, pool, [TOPIC_TRANSFER], frm, to)
    bal, supply, _ = replay_transfers(logs)
    out = []
    for owner, v in sorted(bal.items(), key=lambda x: -x[1]):
        if v <= 0:
            continue
        try:
            code = call(rpc, m, "eth_getCode", [owner, "latest"])
            is_contract = bool(code and code != "0x")
        except RuntimeError:
            is_contract = None
        out.append({"owner": owner, "lp_pct": round(v / supply * 100, 2)
                    if supply else None, "is_contract": is_contract})
    return out, len(logs)


def run_case(rpc, head, case):
    m = Meter()
    name, pool = case["name"], case["pool"].lower()
    token = case.get("token")
    print(f"\n=== {name} [{case['label']}] pool={pool[:12]} ===")

    reg_token, created_block, dex = registry_lookup(pool)
    token = (token or reg_token or "").lower() or None
    if created_block is None and token:
        created_block = find_pool_creation(rpc, m, token, head)
    if token is None:
        # pool not in registry and token unknown: read token0/token1
        try:
            t0 = call(rpc, m, "eth_call", [{"to": pool, "data": "0x0dfe1681"}, "latest"])
            t1 = call(rpc, m, "eth_call", [{"to": pool, "data": "0xd21220a7"}, "latest"])
            a0, a1 = "0x" + t0[-40:], "0x" + t1[-40:]
            token = a1 if a0 == WETH else a0
        except RuntimeError as e:
            print(f"  token discovery failed: {e}")
            return None
        created_block = created_block or find_pool_creation(rpc, m, token, head)
    if dex is None:
        dex = "v3"  # all lane pools are v3 unless registry said otherwise
    entry_epoch = iso_to_epoch(case["entry_ts"])
    entry_block = block_at_ts(rpc, m, entry_epoch, head)
    print(f"  token={token[:12]} created_block={created_block} "
          f"entry_block={entry_block} dex={dex}")
    if created_block is None:
        created_block = max(1, entry_block - 2_000_000)
        print(f"  [warn] creation unknown -> scanning from {created_block}")

    # token Transfer logs: creation margin -> HEAD (one scan serves both the
    # at-entry replay and the at-head "what happened" replay)
    scan_from = max(1, created_block - 300_000)
    t_logs = get_logs_chunked(rpc, m, token, [TOPIC_TRANSFER], scan_from, head)
    n_entry_logs = sum(1 for lg in t_logs
                       if int(lg["blockNumber"], 16) <= entry_block)
    print(f"  transfer logs: {len(t_logs)} total, {n_entry_logs} at-entry")

    bal_e, sup_e, first_mint = replay_transfers(t_logs, upto_block=entry_block)
    bal_h, sup_h, _ = replay_transfers(t_logs)
    creator = (first_mint or {}).get("to")
    hs_entry = holder_structure(bal_e, sup_e, pool, token)
    hs_head = holder_structure(bal_h, sup_h, pool, token)
    creator_pct_entry = (round(bal_e.get(creator, 0) / sup_e * 100, 2)
                         if creator and sup_e else None)
    creator_pct_head = (round(bal_h.get(creator, 0) / sup_h * 100, 2)
                        if creator and sup_h else None)
    # creator classification (launchpad contract vs EOA)
    creator_is_contract = None
    if creator:
        try:
            code = call(rpc, m, "eth_getCode", [creator, "latest"])
            creator_is_contract = bool(code and code != "0x")
        except RuntimeError:
            pass

    # LP custody at entry + at head
    if dex == "v2":
        lp_entry, n_lp = lp_custody_v2(rpc, m, pool, created_block, entry_block)
        lp_head, _ = lp_custody_v2(rpc, m, pool, created_block, head)
    else:
        lp_entry, n_lp = lp_custody_v3(rpc, m, pool, created_block, entry_block)
        lp_head, _ = lp_custody_v3(rpc, m, pool, created_block, head)

    res = {
        "name": name, "label": case["label"], "pool": pool, "token": token,
        "dex": dex, "created_block": created_block,
        "entry_ts": case["entry_ts"], "entry_block": entry_block,
        "creator": creator, "creator_is_contract": creator_is_contract,
        "creator_pct_at_entry": creator_pct_entry,
        "creator_pct_at_head": creator_pct_head,
        "n_transfer_logs_at_entry": n_entry_logs,
        "n_transfer_logs_total": len(t_logs),
        "n_lp_event_logs": n_lp,
        "holders_at_entry": hs_entry,
        "holders_at_head": hs_head,
        "lp_custody_at_entry": lp_entry[:8],
        "lp_custody_at_head": lp_head[:8],
        "cost": m.snap(),
    }
    out_path = os.path.join(_HERE, f"retro_{name}.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(res, fh, indent=1)
    he = hs_entry or {}
    print(f"  AT-ENTRY: pool={he.get('pool_pct_of_supply')}% "
          f"top10={he.get('top10_pct')}% top1={he.get('top1_pct')}% "
          f"shoulder={he.get('shoulder_11_20_pct')}% "
          f"holders={he.get('n_holders')} creator={creator_pct_entry}%")
    hh = hs_head or {}
    print(f"  AT-HEAD : pool={hh.get('pool_pct_of_supply')}% "
          f"top10={hh.get('top10_pct')}% creator={creator_pct_head}%")
    print(f"  LP@entry: " + "; ".join(
        f"{o['owner'][:10]}({'C' if o['is_contract'] else 'EOA'})"
        for o in lp_entry[:3]))
    print(f"  cost: {res['cost']}")
    return res


def main():
    only = set(sys.argv[1:])
    rpc = Rpc(os.environ.get("RH_FEED_RPC", RPC_DEFAULT))
    head = int(rpc.call("eth_blockNumber", []), 16)
    print(f"[retro] head={head}")
    results = []
    for case in CASES:
        if only and case["name"] not in only:
            continue
        try:
            r = run_case(rpc, head, case)
            if r:
                results.append(r)
        except Exception as e:
            print(f"  [FAIL] {case['name']}: {type(e).__name__}: {e}")
    with open(os.path.join(_HERE, "retro_all.json"), "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=1)
    print(f"\n[retro] {len(results)} cases -> retro_all.json")


if __name__ == "__main__":
    main()
