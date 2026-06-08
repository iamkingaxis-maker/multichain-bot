"""Per-wallet performance audit for the smart-money follow strategy (2026-06-08).

Joins the follow-signal log (which elite wallets triggered each fire, written by
core/strategies/smart_money_follow.py) to the realized trade outcomes, so every
wallet on the watchlist gets a LIVE track record — and the junk ones (whose signals
consistently lead to losing/flat trades) surface empirically instead of from stale
mining counts.

A token fired on K-wallet consensus, so each of the K wallets is credited with that
token's realized outcome (shared attribution). A wallet that keeps showing up on
losing tokens is the junk to prune.

Usage: python scripts/follow_wallet_audit.py [follow_signals.jsonl] [trades.json]
  - follow_signals.jsonl: defaults to $DATA_DIR/follow_signals.jsonl (or ./)
  - trades.json: a /api/trades dump (any limit). Defaults to _ex.json / _bug.json if present.
"""
from __future__ import annotations
import json, os, sys, collections, statistics


def _load_signals(path):
    sigs = []
    if not os.path.exists(path):
        return sigs
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            sigs.append(json.loads(line))
        except Exception:
            pass
    return sigs


def _token_realized(trades):
    """address(lower) -> (realized_pnl_sum, n_sells, n_wins) from sells."""
    out = collections.defaultdict(lambda: [0.0, 0, 0])
    for x in trades:
        if x.get("type") != "sell":
            continue
        a = (x.get("address") or "").lower()
        p = x.get("pnl")
        if not a or p is None:
            continue
        out[a][0] += p
        out[a][1] += 1
        if p > 0:
            out[a][2] += 1
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sig_path = args[0] if args else os.path.join(os.environ.get("DATA_DIR", "."), "follow_signals.jsonl")
    if not os.path.exists(sig_path):
        sig_path = "follow_signals.jsonl"
    tr_path = args[1] if len(args) > 1 else next((p for p in ("_ex.json", "_bug.json", "_go.json") if os.path.exists(p)), None)

    sigs = _load_signals(sig_path)
    print(f"follow signals logged: {len(sigs)}  (source: {sig_path})")
    if not sigs:
        print("\nNo follow-signals captured yet — the strategy logs them on each fire going")
        print("forward (deploy the smart_money_follow tracking first). Re-run once data accrues.")
        return
    if not tr_path:
        print("No trades file found (pass one: a /api/trades dump).")
        return
    trades = json.load(open(tr_path))
    if isinstance(trades, dict):
        trades = trades.get("trades", trades)
    tok_pnl = _token_realized(trades)

    # roster baseline (n_winners), optional
    nwin = {}
    rp = "_prune_mine/discovered_wallets.json"
    if os.path.exists(rp):
        try:
            nwin = {w: v.get("n_winners", 0) for w, v in json.load(open(rp)).items()}
        except Exception:
            pass

    # per-wallet: tokens it signaled + the realized outcome of those tokens
    w_tokens = collections.defaultdict(set)
    for s in sigs:
        for w in (s.get("wallets") or []):
            if s.get("token"):
                w_tokens[w].add(s["token"].lower())

    print(f"wallets that fired >=1 signal: {len(w_tokens)}")
    print(f"\n{'wallet':16s} sigs tok_closed realized$  WR    n_winners  verdict")
    rows = []
    for w, toks in w_tokens.items():
        closed = [t for t in toks if t in tok_pnl]
        pnl = sum(tok_pnl[t][0] for t in closed)
        nsell = sum(tok_pnl[t][1] for t in closed)
        nwins = sum(tok_pnl[t][2] for t in closed)
        wr = nwins / nsell if nsell else None
        rows.append((w, len(toks), len(closed), pnl, wr, nwin.get(w)))
    for w, nsig, nclosed, pnl, wr, nw in sorted(rows, key=lambda r: (r[3])):
        wrs = f"{wr:.0%}" if wr is not None else "  -"
        verdict = ("JUNK" if (nclosed >= 3 and pnl < 0 and (wr or 0) < 0.45)
                   else "weak" if (nclosed >= 3 and pnl < 0)
                   else "ok" if nclosed >= 1 else "no-closed-trades")
        print(f"  {w[:14]:14s} {nsig:4d} {nclosed:6d}    {pnl:+8.1f}  {wrs:>4}   {str(nw):>6}    {verdict}")

    junk = [r for r in rows if r[2] >= 3 and r[3] < 0 and (r[4] or 0) < 0.45]
    print(f"\nJUNK wallets (>=3 closed-token signals, negative realized, WR<45%): {len(junk)}")
    for r in junk:
        print(f"  prune candidate: {r[0]} (realized {r[3]:+.1f}, WR {(r[4] or 0):.0%})")


if __name__ == "__main__":
    main()
