# core/rh_rug_signals.py
"""RH-chain (4663) rug-defense signals — SHADOW-STAMP ONLY (2026-07-11).

Port of the Solana HOODLANA lessons to the EVM lane. NOTHING here blocks an
entry: the paper lane computes these AFTER a paper fill books (background
thread, zero latency budget) and appends an {"ev":"rug_signals"} ledger row.
The labeled-outcome pipeline (post-exit checks / cohort labeler) grades them
offline; only a graded signal meeting catch-vs-winner-kill<=5% may ever be
promoted to a gate — and that promotion needs AxiS approval.

What the Solana forensics established (scratchpad/_rug_forensics.md +
_resume_gate_lp_custody_spec.md):
  * HOODLANA was an LP-PULL rug: 98.78% of supply sat in the pool vault, which
    the dev-wallet proxy skipped BY DESIGN. The mechanism-aligned defense is
    LP/pool CUSTODY, not actor identity (actor axes kill 13-40% of winners).
  * The catastrophic-dump class enters LOW-visible-concentration (supply
    hidden below the top-10 line) — top10 ALONE does not clear winner-kill;
    the joint read needs shoulder_11_20 + pool share.
  * THE TRAP: post-rug state reads BACKWARDS (drained pool looks "safe").
    Signals must be captured AT ENTRY — which is exactly what the shadow
    stamp does, and what the EVM makes cheap (full event logs, no archive
    state needed: Transfer-log replay reconstructs any past holder map).

RH-chain retro validation (scratchpad/rh_rug_port/, 2026-07-11): the labeled
rug set (CASHCATGAME -97.7%, MONSIEUR, Halp, TREAT, KUNA) vs surviving aged
pools — per-case hit/miss table in scratchpad/_rh_rug_port.md. RH rugs seen
so far are DUMP-class (whale sells into the pool) rather than Solana-style
LP-pulls, so top1/top10 holder mass is the leading feature; LP custody is
stamped anyway (the LP-pull class exists on RH too — Halp lesson: LP pulls
never appear in swap tape).

DATA SOURCES (all keyless public-RPC):
  * eth_call at latest: totalSupply() + balanceOf(pool/dead) — pool share of
    supply costs 2 calls, no logs (the HOODLANA shape is 2 calls away).
  * eth_getLogs Transfer(token): full-history replay -> top-holder structure,
    creator (first mint recipient) remaining %. Budgeted: young tokens are a
    few thousand logs; the budget caps aged monsters (partial stamp flagged).
  * eth_getLogs Mint/Burn(pool) (V3) or Transfer(pair) (V2 LP token): who owns
    the liquidity and whether that owner is a contract (locker/manager) or an
    EOA (pull-ready human).

FAIL-OPEN EVERYWHERE: any RPC/decode failure yields a partial stamp with an
`err`/`truncated` field — never an exception into the caller.
"""
from __future__ import annotations

import os
import time
from typing import Optional

# ── chain constants (mirror scripts/rh_chain_feed.py; verified live) ─────────
WETH = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"

# keccak256 topics (verified via Web3.keccak 2026-07-11)
TOPIC_TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOPIC_V3_MINT = "0x7a53080ba414158be7ec69b987b5fb7d07dee101fe85488f0853ae16239d0bde"
TOPIC_V3_BURN = "0x0c396cd989a39f4459b5fa1aed6a9a8dcdbc45908acfd67e028cd568da98982c"

SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_BALANCE_OF = "0x70a08231"

ZERO_ADDR = "0x" + "0" * 40
DEAD_ADDR = "0x000000000000000000000000000000000000dead"

# ── budgets (per-entry cost is the design constraint; measured 2026-07-11:
# fresh token ~2.7k transfer logs / ~10 RPC calls / <15s; aged survivors ~1-3x.
# The caps below bound the worst case, not the typical one.) ─────────────────
MAX_TRANSFER_LOGS = 60_000    # holder replay abandoned past this (flag partial)
MAX_SECS = 90.0               # hard wall-clock budget for one stamp
PACE_S = 0.25                 # between RPC calls (the lane shares the RPC)
CHUNK0 = 400_000              # initial getLogs window (halved on timeout)
PREHISTORY_BLOCKS = 300_000   # token mint precedes pool creation by <= this
MAX_BACK_EXTENDS = 2          # extend-back attempts hunting the genesis mint
LP_OWNER_CODE_CHECKS = 4      # eth_getCode budget for LP owners

STAMP_VERSION = 1

# ── Blockscout SHADOW source (2026-07-12; core/rh_blockscout.py) ─────────────
# RH_BLOCKSCOUT=on (default) merges the cheap free-API holder features (bs_*)
# ALONGSIDE this eth_getLogs reconstruction so the grader can measure which is
# more accurate. off = byte-identical (no bs_ keys). FAIL-OPEN: the client never
# raises; a failure yields a bs_source_ok=False sub-stamp.
def _blockscout_enabled() -> bool:
    return os.environ.get("RH_BLOCKSCOUT", "on").lower() != "off"


def _blockscout_merge(token: str, pool: str) -> dict:
    """Fetch the bs_ shadow fields (or {} when disabled / on any error).
    Never raises."""
    if not _blockscout_enabled():
        return {}
    try:
        from core.rh_blockscout import blockscout_stamp
        return blockscout_stamp(token, pool_addr=pool)
    except Exception:
        return {}


# ══════════════════════════════════════════════════════════════════════════════
# RUG GATE v2 — TOP-HOLDER CONCENTRATION (2026-07-13; SHADOW by default)
# ══════════════════════════════════════════════════════════════════════════════
# The ONE pre-buy holder-distribution signal that graded clean on the RH paper
# ledger (scratchpad/_rh_rug_v2_0713.md). Over the combined ledger-stamped +
# retro at-entry set (3 labeled catastrophic rugs / 22 winners / 4 losses):
#
#     top1_pct >= 9   OR   top10_pct >= 30
#
# caught the TWO catastrophic dump-class rugs (CASHCATWIF -100%, CASHCATGAME
# -98% realized) with 0/22 winner-kill and 0/4 loss-hit — inside the Solana
# rug-gate's <=5% winner-kill bar. This is the DUMP-class tell: a whale sits
# positioned to sell its oversized stake into the pool. Every RH rug seen so far
# is dump-class (LP is launchpad-custodied on hood.fun; see RQ2 in
# _rh_rug_port.md), so concentration — not LP-pull custody — is the leading tell.
#
# WHAT IT MISSES (honest): the low-concentration LP-pull class. Halp (-90%) reads
# top1 1.6 / top10 12.1 at entry — indistinguishable from winners on holder
# distribution. Every predicate that caught Halp (nhold<250, fat shoulder,
# float>=60, pool<25) killed 2-20 of 22 winners. Halp is therefore left to the
# LP-custody stamp (lp_any_eoa_owner), which fires 0 on today's launchpad-
# custodied pools but is the mechanism-defense for the non-hood.fun EOA-LP class.
#
# LOW-N: 3 labeled rugs. AxiS directed SHIP-ENFORCE (2026-07-13) on the clean
# grade — the gate BLOCKS the two catastrophic dump-class rugs at 0/22 winner-
# kill (and 0/4 loss-hit), which clears the Solana <=5% bar, and it is fully
# env-reversible (RH_RUG_GATE=shadow downgrades to stamp-only with no deploy).
# Enforcement is LATENCY-SAFE because it NEVER runs the 90s eth_getLogs replay
# inline: the lane PREWARMS the Blockscout bs_top1/bs_top10 (2 calls, ~1-6s
# cold / 0 on cache hit, 10-min TTL) when a pool arms into the watch set, and
# the entry decision reads the warm verdict from cache (0 added latency on the
# detect->fill path; FAIL-OPEN on absent data — never a veto without data). The
# eth_getLogs recon top1/top10 remains the offline-graded shadow fallback.
RUG_GATE_TOP1_PCT = float(os.environ.get("RH_RUG_GATE_TOP1", "9"))
RUG_GATE_TOP10_PCT = float(os.environ.get("RH_RUG_GATE_TOP10", "30"))


def _rug_gate_mode() -> str:
    """RH_RUG_GATE mode. enforce (DEFAULT, shipped 2026-07-13 per AxiS on the
    0/22-winner-kill grade) = the verdict BLOCKS the entry (consumed by the
    lane's arm-time Blockscout prewarm gate). shadow = stamp the verdict, never
    block. off = no rug_gate_* keys at all. 'block' is a legacy alias for
    enforce. Env-reversible with no deploy (RH_RUG_GATE=shadow to downgrade)."""
    m = os.environ.get("RH_RUG_GATE", "enforce").strip().lower()
    if m == "block":
        m = "enforce"
    return m if m in ("off", "shadow", "enforce") else "enforce"


def rug_gate_enforcing() -> bool:
    """True when the concentration rug gate should BLOCK entries (not merely
    stamp them). The RH paper lane reads this at the entry decision; the
    post-fill shadow stamper ignores it (it only records the verdict)."""
    return _rug_gate_mode() == "enforce"


def rug_gate_verdict(stamp: dict, *, top1_thr: Optional[float] = None,
                     top10_thr: Optional[float] = None) -> dict:
    """PURE. Read a rug/Blockscout stamp -> concentration rug verdict. Prefers
    the Blockscout bs_* concentration (the practical PREWARM source) and falls
    back to the eth_getLogs reconstruction top1/top10. FAIL-OPEN: neither source
    present -> block=False, source='none' (never vetoes on absent data)."""
    t1 = RUG_GATE_TOP1_PCT if top1_thr is None else top1_thr
    t10 = RUG_GATE_TOP10_PCT if top10_thr is None else top10_thr
    top1 = stamp.get("bs_top1_pct")
    top10 = stamp.get("bs_top10_pct")
    src = "bs"
    if top1 is None and top10 is None:
        top1, top10, src = stamp.get("top1_pct"), stamp.get("top10_pct"), "recon"
    base = {"rug_gate_top1": top1, "rug_gate_top10": top10,
            "rug_gate_thr": [t1, t10], "rug_gate_mode": _rug_gate_mode()}
    if top1 is None and top10 is None:
        return {"rug_gate_block": False, "rug_gate_reason": None,
                "rug_gate_source": "none", **base}
    reasons = []
    if top1 is not None and float(top1) >= t1:
        reasons.append("top1_%.2f>=%.1f" % (float(top1), t1))
    if top10 is not None and float(top10) >= t10:
        reasons.append("top10_%.2f>=%.1f" % (float(top10), t10))
    return {"rug_gate_block": bool(reasons),
            "rug_gate_reason": ("concentration:" + ",".join(reasons)
                                if reasons else None),
            "rug_gate_source": src, **base}


# ── FAST LP-PULL EXIT BAIL (2026-07-13, scratchpad/_rh_exit_rug_0713.md) ──────
# The Halp lesson made concrete: Halp -90% was a SINGLE-BLOCK TOTAL LP pull
# (buy -> HARD_STOP in 10s), invisible to holder concentration (top1 1.6 /
# top10 12.1 — a winner shape) and UNSTOPPABLE by any exit (the first sell
# quote after the pull is already ~ -84%). The pre-buy defense for that class
# is LP CUSTODY (rug_gate / lp_any_eoa_owner), not distribution. What an EXIT
# bail CAN defend is the STAGED / partial-drain class (liquidity bled over
# seconds-to-minutes) — where reserves fall meaningfully BEFORE the price path
# fully collapses, so a bail that reads reserves (not price) exits while there
# is still a book to sell into. The lane's existing LP_DRAIN exit uses a 900s
# rolling window that needs >=2 in-window samples and is fed at MAINTENANCE
# cadence (min-60s liq refresh) — structurally too slow for a fast pull. This
# verdict is the fast complement: compare CURRENT reserves to the FIXED
# AT-ENTRY baseline (no window, no 2-sample requirement) and fire on the FIRST
# tick a >=thr collapse is observed. SHADOW by default (stamps a would-fire
# ledger row, changes NOTHING about trading); RH_FAST_LIQ_BAIL=block makes it
# an authoritative immediate full exit. UNVALIDATED: 0 staged pulls observed in
# the ledger yet (Halp was single-block), so this ACCRUES would-fire+outcome
# data in shadow — winner-kill (a big v3 swap can transiently move concentrated
# reserves) must be measured before any promotion.
FAST_LIQ_BAIL_PCT = float(os.environ.get("RH_FAST_LIQ_BAIL_PCT", "-35"))


def _fast_liq_bail_mode() -> str:
    """shadow (default) = stamp the would-fire, never sell. block = the verdict
    is an authoritative immediate full exit. off = no fast_liq_bail_* keys."""
    return os.environ.get("RH_FAST_LIQ_BAIL", "shadow").lower()


def fast_liq_bail_verdict(entry_liq, cur_liq, *,
                          thr_pct: Optional[float] = None) -> dict:
    """PURE. Fast per-tick LP-pull EXIT bail. Compares CURRENT pool liquidity
    to the liquidity AT ENTRY (a fixed baseline — NO rolling window and NO
    2-sample requirement, unlike lp_drain_pct) and fires on the first observed
    >=thr collapse. FAIL-OPEN: missing/invalid liq on either side ->
    block=False (never bails on absent data). Cannot save a single-block TOTAL
    pull (the sell quote is already ~0 before any bail can act); it defends the
    STAGED / partial-drain class, and its edge is exiting one-or-more ticks
    ahead of the price stop while a book still exists."""
    thr = FAST_LIQ_BAIL_PCT if thr_pct is None else thr_pct
    base = {"fast_liq_bail_thr": thr, "fast_liq_bail_mode": _fast_liq_bail_mode(),
            "fast_liq_bail_entry": entry_liq, "fast_liq_bail_cur": cur_liq}
    try:
        el = float(entry_liq) if entry_liq is not None else 0.0
        cl = float(cur_liq) if cur_liq is not None else 0.0
    except (TypeError, ValueError):
        el = cl = 0.0
    if el <= 0 or cl <= 0:
        return {"fast_liq_bail_block": False, "fast_liq_bail_drop": None,
                "fast_liq_bail_reason": None, **base}
    drop = (cl - el) / el * 100.0
    block = drop <= thr
    return {"fast_liq_bail_block": bool(block), "fast_liq_bail_drop": round(drop, 2),
            "fast_liq_bail_reason": ("liq %.0f->%.0f (%.1f%%) <= %.0f%%"
                                     % (el, cl, drop, thr)) if block else None,
            **base}


# ══════════════════════════════════════════════════════════════════════════════
# PURE decode/aggregation (no network — unit-tested in tests/test_rh_rug_signals)
# ══════════════════════════════════════════════════════════════════════════════
def _topic_addr(topic: str) -> str:
    return "0x" + str(topic)[-40:].lower()


def replay_transfers(logs: list, upto_block: Optional[int] = None) -> tuple:
    """ERC20 Transfer logs -> (balances, supply, first_mint) reconstructed by
    replay. supply = minted - burned (to 0x0 only; 0xdead balances are kept
    and surfaced separately by holder_structure). first_mint = the earliest
    from-ZERO transfer seen ({'to','block'}) or None — its recipient is the
    creator/launchpad proxy. Logs may be unsorted (sorted here by
    (blockNumber, logIndex)). Pure; skips undecodable rows."""
    def _key(lg):
        try:
            return (int(lg.get("blockNumber"), 16),
                    int(lg.get("logIndex", "0x0"), 16))
        except (TypeError, ValueError):
            return (1 << 62, 0)
    bal: dict = {}
    minted = burned = 0
    first_mint = None
    for lg in sorted(logs, key=_key):
        try:
            blk = int(lg["blockNumber"], 16)
        except (KeyError, TypeError, ValueError):
            continue
        if upto_block is not None and blk > upto_block:
            continue
        tps = lg.get("topics") or []
        if len(tps) < 3 or str(tps[0]).lower() != TOPIC_TRANSFER:
            continue
        src = _topic_addr(tps[1])
        dst = _topic_addr(tps[2])
        try:
            v = int(lg["data"], 16)
        except (KeyError, TypeError, ValueError):
            continue
        if src == ZERO_ADDR:
            minted += v
            if first_mint is None:
                first_mint = {"to": dst, "block": blk}
        else:
            bal[src] = bal.get(src, 0) - v
        if dst == ZERO_ADDR:
            burned += v
        else:
            bal[dst] = bal.get(dst, 0) + v
    return bal, minted - burned, first_mint


def holder_structure(bal: dict, supply: int, pool: str, token: str,
                     extra_exclude=()) -> Optional[dict]:
    """Balances -> the hidden-supply feature set. Excludes the POOL (read
    separately — the HOODLANA lesson is to SEE it, as its own number), the
    token contract itself (launchpad escrow), 0x0/0xdead, and extras.
    None when supply is non-positive (nothing to normalize by). Pure."""
    if supply is None or supply <= 0:
        return None
    excl = ({pool.lower(), token.lower(), ZERO_ADDR, DEAD_ADDR}
            | {str(a).lower() for a in extra_exclude})
    holders = sorted(((a, v) for a, v in bal.items()
                      if v > 0 and a not in excl), key=lambda x: -x[1])
    pcts = [v / supply * 100.0 for _, v in holders]
    return {
        "pool_pct_of_supply": round(bal.get(pool.lower(), 0) / supply * 100.0, 2),
        "token_contract_pct": round(bal.get(token.lower(), 0) / supply * 100.0, 2),
        "dead_pct": round(bal.get(DEAD_ADDR, 0) / supply * 100.0, 2),
        "n_holders": len(holders),
        "top1_pct": round(pcts[0], 2) if pcts else 0.0,
        "top10_pct": round(sum(pcts[:10]), 2),
        "shoulder_11_20_pct": round(sum(pcts[10:20]), 2),
        "top1_addr": holders[0][0] if holders else None,
    }


def lp_owners_from_events(logs: list) -> dict:
    """V3 pool Mint/Burn logs -> {owner: net_liquidity}. Mint data layout is
    (sender, amount, amount0, amount1) with owner in topic1; Burn data is
    (amount, amount0, amount1) with owner in topic1. Pure; skips undecodable."""
    liq: dict = {}
    for lg in logs:
        tps = lg.get("topics") or []
        if len(tps) < 2:
            continue
        t0 = str(tps[0]).lower()
        if t0 not in (TOPIC_V3_MINT, TOPIC_V3_BURN):
            continue
        owner = _topic_addr(tps[1])
        data = lg.get("data") or "0x"
        h = data[2:] if data.startswith("0x") else data
        word_i = 1 if t0 == TOPIC_V3_MINT else 0
        seg = h[word_i * 64:(word_i + 1) * 64]
        if len(seg) < 64:
            continue
        try:
            amt = int(seg, 16)
        except ValueError:
            continue
        liq[owner] = liq.get(owner, 0) + (amt if t0 == TOPIC_V3_MINT else -amt)
    return liq


def summarize_lp(liq_by_owner: dict, is_contract: Optional[dict] = None) -> dict:
    """{owner: net_liquidity} (+ optional {owner: bool} code map) -> the LP
    custody stamp: how many owners, who dominates, and whether ANY live
    liquidity is EOA-held (pull-ready human — the loaded-gun read). Pure."""
    is_contract = is_contract or {}
    live = sorted(((o, n) for o, n in liq_by_owner.items() if n > 0),
                  key=lambda x: -x[1])
    total = sum(n for _, n in live)
    top = live[0] if live else (None, 0)
    return {
        "lp_n_owners": len(live),
        "lp_top_owner": top[0],
        "lp_top_owner_share_pct": (round(top[1] / total * 100.0, 2)
                                   if total > 0 else None),
        "lp_top_owner_is_contract": is_contract.get(top[0]),
        "lp_any_eoa_owner": (any(is_contract.get(o) is False for o, _ in live)
                             if live else None),
        "lp_owners": [{"owner": o,
                       "share_pct": round(n / total * 100.0, 2) if total else None,
                       "is_contract": is_contract.get(o)}
                      for o, n in live[:4]],
    }


def hidden_supply_readout(pool_pct, top10_pct, shoulder_pct, n_holders,
                          top1_pct=None) -> dict:
    """The Solana joint-shape features, PRE-COMPUTED for offline grading (no
    thresholds enforced here — stamps are graded against labeled outcomes;
    naive top10-only cuts already FAILED winner-kill<=5% on Solana).
    visible_float_pct = supply outside pool+top10 (HOODLANA-class hides mass
    below the top-10 line in a thin base -> huge float, thin shoulder).
    whale_overhang_pct = the largest single non-pool holder (the RH dump-class
    tell seen on CASHCATGAME: 25% whale at entry). Pure; None-tolerant."""
    out = {"visible_float_pct": None, "whale_overhang_pct": top1_pct,
           "shoulder_to_top10_ratio": None}
    try:
        if pool_pct is not None and top10_pct is not None:
            out["visible_float_pct"] = round(
                max(0.0, 100.0 - float(pool_pct) - float(top10_pct)), 2)
        if top10_pct and shoulder_pct is not None and float(top10_pct) > 0:
            out["shoulder_to_top10_ratio"] = round(
                float(shoulder_pct) / float(top10_pct), 3)
    except (TypeError, ValueError):
        pass
    return out


def assemble_stamp(pool: str, token: str, *, quick: dict,
                   holders: Optional[dict], lp: Optional[dict],
                   creator: Optional[str], creator_pct,
                   cost: dict, truncated: bool = False,
                   err: Optional[str] = None) -> dict:
    """Merge the tiers into the final ledger-ready stamp dict. Pure."""
    h = holders or {}
    stamp = {
        "v": STAMP_VERSION,
        "pool": pool.lower(), "token": token.lower(),
        # tier A: 2-3 eth_calls, always present when RPC answered
        "pool_pct_of_supply": quick.get("pool_pct_of_supply"),
        "dead_pct": quick.get("dead_pct"),
        "total_supply": quick.get("total_supply"),
        # tier B: transfer replay (may be absent/truncated)
        "n_holders": h.get("n_holders"),
        "top1_pct": h.get("top1_pct"),
        "top10_pct": h.get("top10_pct"),
        "shoulder_11_20_pct": h.get("shoulder_11_20_pct"),
        "token_contract_pct": h.get("token_contract_pct"),
        "top1_addr": h.get("top1_addr"),
        "creator": creator,
        "creator_pct": creator_pct,
        "replay_supply_match": quick.get("replay_supply_match"),
        # tier C: LP custody
        **(lp or {"lp_n_owners": None, "lp_top_owner": None,
                  "lp_top_owner_share_pct": None,
                  "lp_top_owner_is_contract": None,
                  "lp_any_eoa_owner": None, "lp_owners": None}),
        "truncated": truncated,
        "err": err,
        "cost": cost,
    }
    stamp.update(hidden_supply_readout(
        stamp["pool_pct_of_supply"], stamp["top10_pct"],
        stamp["shoulder_11_20_pct"], stamp["n_holders"],
        top1_pct=stamp["top1_pct"]))
    return stamp


# ══════════════════════════════════════════════════════════════════════════════
# RPC-side computation (paced, budgeted, FAIL-OPEN)
# ══════════════════════════════════════════════════════════════════════════════
class _Budget:
    def __init__(self, max_secs: float = MAX_SECS):
        self.t0 = time.time()
        self.max_secs = max_secs
        self.calls = 0
        self.logs = 0

    def spent(self) -> bool:
        return (time.time() - self.t0) > self.max_secs

    def cost(self) -> dict:
        return {"rpc_calls": self.calls, "logs": self.logs,
                "secs": round(time.time() - self.t0, 1)}


def _call(rpc, budget: _Budget, method: str, params: list, tries: int = 2):
    """One paced RPC call through an rh_chain_feed.Rpc-like object."""
    budget.calls += 1
    time.sleep(PACE_S)
    return rpc.call(method, params, tries=tries)


def _get_logs_budgeted(rpc, budget: _Budget, address, topics,
                       frm: int, to: int, max_logs: int) -> tuple:
    """Chunked getLogs with halve-on-timeout. Returns (logs, complete_bool).
    Stops (partial) on log/time budget exhaustion."""
    out = []
    chunk = CHUNK0
    f = frm
    while f <= to:
        if budget.spent() or len(out) > max_logs:
            return out, False
        t = min(f + chunk - 1, to)
        try:
            logs = _call(rpc, budget, "eth_getLogs", [{
                "fromBlock": hex(f), "toBlock": hex(t),
                "address": address, "topics": topics}])
            if len(logs) >= 10_000 and chunk > 2_000:
                chunk //= 4       # server cap hit: redo the window smaller
                continue
            out.extend(logs)
            budget.logs += len(logs)
            f = t + 1
        except Exception as e:  # LogRangeTimeout / RuntimeError / transport
            if chunk <= 2_000:
                return out, False  # this window is unreadable: partial
            chunk //= 2
            if "timed out" not in str(e).lower():
                time.sleep(1.0)
    return out, True


def _balance_call(rpc, budget: _Budget, token: str, holder: str):
    r = _call(rpc, budget, "eth_call",
              [{"to": token, "data": SEL_BALANCE_OF + "0" * 24 + holder[2:].lower()},
               "latest"])
    return int(r, 16) if r and r != "0x" else 0


def compute_entry_stamp(rpc, pool: str, token: str,
                        created_block: Optional[int], head_block: int,
                        dex: str = "v3", max_secs: float = MAX_SECS) -> dict:
    """The full shadow stamp for one entry. NEVER raises — any failure returns
    a partial stamp with `err` set. `rpc` = scripts.rh_chain_feed.Rpc-like
    (call(method, params, tries)). Budgets: max_secs wall clock,
    MAX_TRANSFER_LOGS on the replay leg.

    Tiers (cheap first, so a blown budget still yields the HOODLANA read):
      A. totalSupply + balanceOf(pool) + balanceOf(dead)  -> pool_pct_of_supply
      B. Transfer-log replay -> top1/top10/shoulder/n_holders/creator%
      C. pool Mint/Burn (V3) or LP-token Transfer (V2) -> LP custody
    """
    budget = _Budget(max_secs)
    pool_l, token_l = pool.lower(), token.lower()
    quick: dict = {}
    holders = lp = None
    creator = creator_pct = None
    truncated = False
    err = None
    try:
        # ── tier A ───────────────────────────────────────────────────────────
        supply_r = _call(rpc, budget, "eth_call",
                         [{"to": token_l, "data": SEL_TOTAL_SUPPLY}, "latest"])
        total_supply = int(supply_r, 16) if supply_r and supply_r != "0x" else 0
        quick["total_supply"] = str(total_supply)
        if total_supply > 0:
            pool_bal = _balance_call(rpc, budget, token_l, pool_l)
            dead_bal = _balance_call(rpc, budget, token_l, DEAD_ADDR)
            quick["pool_pct_of_supply"] = round(pool_bal / total_supply * 100.0, 2)
            quick["dead_pct"] = round(dead_bal / total_supply * 100.0, 2)

        # ── tier C before B: LP custody is few logs, keep it inside budget ──
        if created_block:
            if dex == "v2":
                lp_logs, _c = _get_logs_budgeted(
                    rpc, budget, pool_l, [TOPIC_TRANSFER],
                    created_block, head_block, 10_000)
                bal_lp, sup_lp, _ = replay_transfers(lp_logs)
                liq = {o: v for o, v in bal_lp.items() if v > 0} if sup_lp else {}
            else:
                lp_logs, _c = _get_logs_budgeted(
                    rpc, budget, pool_l, [[TOPIC_V3_MINT, TOPIC_V3_BURN]],
                    created_block, head_block, 10_000)
                liq = lp_owners_from_events(lp_logs)
            is_c = {}
            for owner in [o for o, n in sorted(liq.items(), key=lambda x: -x[1])
                          if n > 0][:LP_OWNER_CODE_CHECKS]:
                try:
                    code = _call(rpc, budget, "eth_getCode", [owner, "latest"])
                    is_c[owner] = bool(code and code != "0x")
                except Exception:
                    pass
            lp = summarize_lp(liq, is_c)

        # ── tier B: transfer replay (budgeted; extend back hunting genesis) ─
        if created_block and not budget.spent():
            frm = max(1, created_block - PREHISTORY_BLOCKS)
            t_logs, complete = _get_logs_budgeted(
                rpc, budget, token_l, [TOPIC_TRANSFER],
                frm, head_block, MAX_TRANSFER_LOGS)
            extends = 0
            while (complete and t_logs and extends < MAX_BACK_EXTENDS
                   and frm > 1 and not budget.spent()):
                bal0, sup0, fm0 = replay_transfers(t_logs)
                if fm0 is not None:   # genesis mint reached
                    break
                nfrm = max(1, frm - PREHISTORY_BLOCKS * 4)
                more, complete = _get_logs_budgeted(
                    rpc, budget, token_l, [TOPIC_TRANSFER],
                    nfrm, frm - 1, MAX_TRANSFER_LOGS)
                t_logs = more + t_logs
                frm = nfrm
                extends += 1
            truncated = not complete or len(t_logs) > MAX_TRANSFER_LOGS
            bal, replay_supply, first_mint = replay_transfers(t_logs)
            # consistency check: replay vs on-chain totalSupply (a mismatch
            # means genesis was NOT reached -> holder numbers are partial)
            match = (total_supply > 0 and replay_supply > 0
                     and abs(replay_supply - total_supply) / total_supply < 1e-3)
            quick["replay_supply_match"] = bool(match)
            if not match:
                truncated = True
            holders = holder_structure(
                bal, total_supply if total_supply > 0 else replay_supply,
                pool_l, token_l)
            if first_mint:
                creator = first_mint["to"]
                base = total_supply if total_supply > 0 else replay_supply
                if base > 0:
                    creator_pct = round(bal.get(creator, 0) / base * 100.0, 2)
    except Exception as e:
        err = f"{type(e).__name__}: {e}"[:200]
    stamp = assemble_stamp(pool_l, token_l, quick=quick, holders=holders,
                           lp=lp, creator=creator, creator_pct=creator_pct,
                           cost=budget.cost(), truncated=truncated, err=err)
    # SHADOW: stamp Blockscout-derived features alongside the reconstruction
    # (default on; off = byte-identical). Fail-open — never raises.
    stamp.update(_blockscout_merge(token_l, pool_l))
    # RUG GATE v2 (2026-07-13): stamp the concentration verdict so it
    # forward-grades. This runs POST-FILL, so it only RECORDS the verdict —
    # the block flag is never acted on here regardless of mode (enforcement is
    # the lane's arm-time prewarm gate). off = no keys.
    if _rug_gate_mode() != "off":
        try:
            stamp.update(rug_gate_verdict(stamp))
        except Exception:
            pass
    return stamp
