"""EXPERIMENT SCORECARD — the grading loop (2026-07-12).

One READ-ONLY command that reads every pre-registered "grade at n>=X ex-top-2"
bar scattered across scratchpad/_*.md + configs and converts today's pile of
shadow stamps + paper A/B bots into DECISIONS:

    PROMOTE   — bar met (ex-top-2 median > 0, >=50% tokens green, n >= bar)
    RETIRE    — clearly failed (n >= bar, median <= 0 AND < 50% tokens green)
    MIXED     — n >= bar but only one green criterion met (borderline; eyeball it)
    ACCRUING  — n < bar (still collecting forward tape — expected for most)
    NO-DATA   — no forward stamps/closes yet

It FLAGS. It NEVER promotes, retires, changes config, or touches live — AxiS /
the main session makes the call. Run it at the session ritual:

    python scripts/sync_trades_cache.py --full   # fresh SOL entry_meta (egress: once/day)
    python scripts/experiment_scorecard.py        # read every bar, print state

## The honest metric (the standard this codebase now uses)
ex-top-2 token-median = group trips by token, take each token's MEDIAN return,
DROP the 2 tokens with the highest per-token median, then median of the rest.
GREEN = ex2 > 0 AND >=50% of tokens green. LIFETIME SUM IS BANNED as a verdict
(two fat-tail promotions were reverted 2026-07-12 for exactly that). Per-cohort
drop — each experiment drops its OWN 2 best tokens.

## Data sources (all local caches; pull once, reuse; fail-soft)
- SOL:  _trades_cache.json  (buy->sell join + scrub; shadow stamps in entry_meta,
        exit A/B bots badday_young_exit_*)
- RH:   scratchpad/robinhood_tapes/rh_paper_trades.jsonl  (paper racers)
- rug:  scratchpad/rug_cohort_labels.jsonl  (built by scripts/rug_cohort_label.py)
- bs:   wraps scratchpad/rh_blockscout/compare.py

Any missing source is reported as "no data yet", never a crash.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from collections import defaultdict

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOL_CACHE = os.path.join(REPO, "_trades_cache.json")
RH_LEDGER = os.path.join(REPO, "scratchpad", "robinhood_tapes", "rh_paper_trades.jsonl")
RUG_LABELS = os.path.join(REPO, "scratchpad", "rug_cohort_labels.jsonl")
BS_COMPARE = os.path.join(REPO, "scratchpad", "rh_blockscout", "compare.py")
OUT_MD = os.path.join(REPO, "scratchpad", "_experiment_scorecard.md")

RH_ENTRY_USD = 25.0
RH_CONTROL = "rh_young_v1"


# ══════════════════════════════════════════════════════════════════════════
#  PURE LOGIC (tested in tests/test_experiment_scorecard.py)
# ══════════════════════════════════════════════════════════════════════════

def _num(x):
    try:
        if x is None or isinstance(x, bool):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def per_token_medians(trips):
    """trips: list of {token, ret}. -> {token: median_ret} over rows with a ret."""
    by = defaultdict(list)
    for t in trips:
        r = _num(t.get("ret"))
        if r is None:
            continue
        by[t.get("token") or t.get("address") or ""].append(r)
    return {tok: statistics.median(v) for tok, v in by.items() if v}


def ex_top2(trips):
    """The honest metric. Returns a dict:
       n_tokens, ex2_median, pct_green, plain_median.
    ex2_median: median of per-token medians AFTER dropping the 2 tokens with the
    highest per-token median (per-cohort fat-tail drop). pct_green: % of ALL
    tokens whose per-token median > 0. None values on empty cohort."""
    pt = per_token_medians(trips)
    n = len(pt)
    if n == 0:
        return {"n_tokens": 0, "ex2_median": None, "pct_green": None,
                "plain_median": None}
    meds = sorted(pt.values())               # ascending; drop the TOP 2
    kept = meds[:-2] if n > 2 else meds       # <=2 tokens: nothing to drop
    ex2 = statistics.median(kept) if kept else statistics.median(meds)
    green = sum(1 for m in pt.values() if m > 0)
    # STABILITY dimension (2026-07-13 goal: "clearly profitable AND stable —
    # both sides show extreme per-bot P&L volatility"). Stability = low tail +
    # low dispersion, judged on per-token medians (not per-trip, so a few rug
    # tokens can't hide behind many small banked wins).
    catastrophic = sum(1 for m in pt.values() if m < -20.0)
    dispersion = statistics.pstdev(meds) if n > 1 else 0.0
    return {"n_tokens": n,
            "ex2_median": round(ex2, 2),
            "pct_green": round(100.0 * green / n, 1),
            "plain_median": round(statistics.median(meds), 2),
            "pct_catastrophic": round(100.0 * catastrophic / n, 1),
            "dispersion": round(dispersion, 1)}


def stability_verdict(metrics, n_bar, green_floor=55.0):
    """'clearly profitable AND stable' bar (2026-07-13 goal). STABLE requires all:
    ex2>=0 (not fat-tail-dependent), >=green_floor% tokens green (consistency),
    <=5% catastrophic tokens (tail capped). Below n_bar -> ACCRUING. This is a
    SEPARATE, STRICTER lens than verdict()'s PROMOTE — a bot can PROMOTE on median
    yet be UNSTABLE (fat tail). Cross-window OOS consistency is graded elsewhere."""
    n = metrics.get("n_tokens") or 0
    if n == 0:
        return "NO-DATA"
    if n < n_bar:
        return "ACCRUING"
    ex2 = metrics.get("ex2_median")
    grn = metrics.get("pct_green")
    cat = metrics.get("pct_catastrophic")
    if ex2 is None or grn is None or cat is None:
        return "NO-DATA"
    if ex2 >= 0 and grn >= green_floor and cat <= 5.0:
        return "STABLE"
    return "UNSTABLE"


def verdict(metrics, n_bar):
    """Map ex_top2 metrics + a pre-registered n_bar to a decision string."""
    n = metrics.get("n_tokens") or 0
    if n == 0:
        return "NO-DATA"
    if n < n_bar:
        return "ACCRUING"
    ex2 = metrics.get("ex2_median")
    grn = metrics.get("pct_green")
    if ex2 is None or grn is None:
        return "NO-DATA"
    median_ok = ex2 > 0
    green_ok = grn >= 50.0
    if median_ok and green_ok:
        return "PROMOTE"
    if (not median_ok) and (not green_ok):
        return "RETIRE"
    return "MIXED"


# ══════════════════════════════════════════════════════════════════════════
#  DATA LOADERS (fail-soft; every one returns ([], note) on any problem)
# ══════════════════════════════════════════════════════════════════════════

def load_sol_trips(cache_path):
    """buy->sell join over _trades_cache.json with the standing SCRUB RULE
    (drop ret>0 & hold<10s). Each trip carries token/address/ret/hold/bot + the
    buy's entry_meta. Returns (trips, note). note surfaces freshness/staleness."""
    if not os.path.exists(cache_path):
        return [], f"no cache at {os.path.relpath(cache_path, REPO)} " \
                   f"(run scripts/sync_trades_cache.py --full)"
    try:
        with open(cache_path, encoding="utf-8") as f:
            recs = json.load(f)
    except Exception as e:
        return [], f"cache unreadable: {e}"
    if not isinstance(recs, list):
        recs = recs.get("trades", []) if isinstance(recs, dict) else []
    buys = [r for r in recs if r.get("type") == "buy"]
    sells = [r for r in recs if r.get("type") == "sell"
             and "cancelled on restart" not in (r.get("reason") or "").lower()]
    bidx = defaultdict(list)
    for b in buys:
        bidx[(b.get("bot_id"), b.get("address"))].append(b)
    for lst in bidx.values():
        lst.sort(key=lambda r: r.get("time") or "")
    trips = []
    for s in sells:
        cands = bidx.get((s.get("bot_id"), s.get("address")), [])
        ep = _num(s.get("entry_price"))
        st = s.get("time") or ""
        best = None
        for b in cands:  # latest matching buy before the sell
            if (b.get("time") or "") > st:
                continue
            bp = _num(b.get("entry_price"))
            if ep and bp and abs(bp - ep) / ep < 0.02:
                best = b
        if best is None:  # fallback: any entry_price match
            for b in cands:
                bp = _num(b.get("entry_price"))
                if ep and bp and abs(bp - ep) / ep < 0.02:
                    best = b
        if best is None:
            continue
        ret = _num(s.get("pnl_pct"))
        hold = _num(s.get("hold_secs"))
        if ret is not None and hold is not None and ret > 0 and hold < 10:
            continue  # SCRUB
        trips.append({
            "bot": s.get("bot_id"), "token": s.get("token"),
            "address": s.get("address"), "ret": ret, "hold": hold,
            "time": best.get("time"), "sell_time": s.get("time"),
            "entry_meta": best.get("entry_meta") or {},
            # sell-side (exit) shadow flags carried onto the trip so the SOL_SHADOW
            # grader can cohort on them via a "sell:<field>" key (2026-07-13).
            "bleed_cut_shadow_would_cut": bool(s.get("bleed_cut_shadow_would_cut")),
        })
    newest = max((t.get("sell_time") or "" for t in trips), default="")
    note = f"{len(trips)} trips, newest {newest[:19] or 'n/a'}"
    return trips, note


def load_rh_trips(ledger_path):
    """Reconstruct closed RH paper trips per (bot,pool), split at fully==True.
    Returns (trips, note). Each trip: {bot, token(=pool), ret(=pnl_pct)}."""
    if not os.path.exists(ledger_path):
        return [], f"no RH ledger at {os.path.relpath(ledger_path, REPO)}"
    rows = []
    try:
        with open(ledger_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(d.get("ts", ""))[:4] == "1970":
                    continue
                rows.append(d)
    except Exception as e:
        return [], f"RH ledger unreadable: {e}"
    sells_by_key = defaultdict(list)
    for d in rows:
        if d.get("ev") == "sell":
            sells_by_key[(d.get("bot_id"), d.get("pool"))].append(d)
    trips = []
    for (bot, pool), sells in sells_by_key.items():
        sells.sort(key=lambda x: x.get("ts", ""))
        cur = []
        for s in sells:
            cur.append(s)
            if s.get("fully"):
                pnl_usd = sum(_num(x.get("pnl_usd")) or 0.0 for x in cur)
                trips.append({
                    "bot": bot or RH_CONTROL, "token": pool,
                    "ret": pnl_usd / RH_ENTRY_USD * 100.0,
                    "sell_time": cur[-1].get("ts", ""),
                })
                cur = []
    newest = max((t.get("sell_time") or "" for t in trips), default="")
    note = f"{len(trips)} closed trips, newest {newest[:19] or 'n/a'}"
    return trips, note


def load_rug_cohort(labels_path):
    """Read the labeled rug cohort (built forward by scripts/rug_cohort_label.py).
    Returns (counts, feature_sep_lines, note). Grading is definitional (a labeled
    catastrophic-rug outcome), so we report the cohort composition + separation."""
    if not os.path.exists(labels_path):
        return {}, [], f"no labels at {os.path.relpath(labels_path, REPO)} " \
                       f"(run scripts/rug_cohort_label.py)"
    rows = []
    try:
        with open(labels_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        return {}, [], f"labels unreadable: {e}"
    from collections import Counter
    counts = dict(Counter(r.get("label") for r in rows))
    cat = [r for r in rows if r.get("label") == "catastrophic" and r.get("features")]
    alive = [r for r in rows if r.get("label") == "alive" and r.get("features")]
    sep = []
    if len(cat) >= 5 and alive:
        keys = set()
        for r in cat + alive:
            keys |= set((r.get("features") or {}).keys())
        for k in sorted(keys):
            cv = [r["features"][k] for r in cat if k in r["features"]]
            av = [r["features"][k] for r in alive if k in r["features"]]
            if cv and av:
                sep.append(f"{k}: cat={statistics.median(cv):.2f}(n={len(cv)}) "
                           f"alive={statistics.median(av):.2f}(n={len(av)})")
    note = f"{len(rows)} labeled mints"
    return counts, sep, note


def run_bs_compare(compare_path):
    """Wrap the existing bs_ vs eth_getLogs grader (subprocess, capture)."""
    if not os.path.exists(compare_path):
        return None, f"no grader at {os.path.relpath(compare_path, REPO)}"
    try:
        r = subprocess.run([sys.executable, compare_path],
                           capture_output=True, text=True, timeout=120, cwd=REPO)
        out = (r.stdout or "").strip() or (r.stderr or "").strip()
        return out, None
    except Exception as e:
        return None, f"compare.py failed: {e}"


# ══════════════════════════════════════════════════════════════════════════
#  EXPERIMENT REGISTRY  (each pre-registered bar, enumerated)
# ══════════════════════════════════════════════════════════════════════════
#
# kind = sol_shadow : filter SOL trips whose buy entry_meta[key] in favor, grade
#        sol_ab     : SOL A/B exit bot (bot_id) graded on its own trips + vs control
#        rh_racer   : RH racer (bot_id) graded on its trips + vs rh_young_v1
#        rug        : rug cohort composition (definitional)
#        bs         : bs_ vs eth_getLogs agreement (wrapped grader)

SOL_SHADOW = [
    # name, entry_meta key, favor-values, n_bar, pre-reg file
    ("deep_capitulation_shadow", "deep_capitulation_shadow", {"DEEP"}, 20,
     "_sol_deep_gate.md — deep-flush cohort (deep-alone was -3.0; needs green)"),
    ("deep_combo_shadow", "deep_combo_shadow", {"FAVOR"}, 20,
     "_sol_deep_gate.md — DEEP & liq>=30k green bar (in-sample +4.6)"),
    ("green_cohort_membership", "green_cohort", {"base", "liq_bsh1", "liq_ubuy"}, 15,
     "_sol_green_cohort_sweep.md — liq>=45k&bs_h1>=1.6 union bar"),
    ("aged_pond_absorb_shadow", "aged_pond_absorb_shadow", {"FAVOR"}, 20,
     "_sol_aged_pond_mine.md — 6-24h pond & nf15>=0.4 (in-sample +2.7)"),
    ("deep_exit_spec_shadow", "deep_exit_spec_shadow", {"BARBELL_DEEP", "BARBELL_VDEEP"}, 30,
     "_deep_exit_optimization.md — deep-cohort barbell exit (measure-only)"),
    ("rug_gate_buy", "rug_gate_buy", None, 30,
     "rug_cohort_labels.jsonl — hidden-supply / rug gate cohort"),
    # EXIT shadow (sell-side flag, not entry_meta): cohort = positions the slow-bleed
    # rule WOULD cut at ~120s (still making new lows AND peak-so-far<+2%). Graded on
    # FINAL held-to-close return: a RED cohort here means cutting is CORRECT (these
    # bleed further), so the normal PROMOTE=green verdict is INVERTED and relabelled
    # SHADOW-EXIT below — read the ex2 as "counterfactual loss if NOT cut". Offline
    # (badday >=07-03): saves losers 4/4 OOS qtrs, ~25% winner-kill (deep-V tail).
    ("bleed_cut_would_cut", "sell:bleed_cut_shadow_would_cut", {True}, 30,
     "_sol_bleed_detector_0713.md — slow-bleed cut@120s (would-cut cohort; red=cut-correct)"),
]

# SOL exit-ladder A/B family (byte-identical entry, exit-only delta), n>=30 vs control
SOL_AB_BOTS = ["badday_young_exit_control", "badday_young_exit_minhold",
               "badday_young_exit_barbell", "badday_young_exit_heatrunner",
               "badday_young_exit_minhold_heat"]
SOL_AB_CONTROL = "badday_young_exit_control"
SOL_AB_BAR = 30
SOL_AB_PREG = "_sol_exit_overhaul.md — exit A/B, n>=30 vs exit_control"


def _grade_cohort(trips, n_bar):
    m = ex_top2(trips)
    m["verdict"] = verdict(m, n_bar)
    m["n_bar"] = n_bar
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sol-cache", default=SOL_CACHE)
    ap.add_argument("--rh-ledger", default=RH_LEDGER)
    ap.add_argument("--rug-labels", default=RUG_LABELS)
    ap.add_argument("--no-bs", action="store_true", help="skip the bs_ compare wrap")
    ap.add_argument("--sync", action="store_true",
                    help="run sync_trades_cache.py --full first (egress: once/day)")
    args = ap.parse_args()

    if args.sync:
        print("[scorecard] syncing SOL trades cache (--full) ...")
        try:
            subprocess.run([sys.executable,
                            os.path.join(REPO, "scripts", "sync_trades_cache.py"),
                            "--full"], timeout=600, cwd=REPO)
        except Exception as e:
            print(f"[scorecard] sync failed (continuing on existing cache): {e}")

    rows = []            # (chain, name, metrics, bar, note, preg)
    notes = []           # data-source freshness lines

    # ── SOL shadow stamps + A/B bots ──────────────────────────────────────
    sol_trips, sol_note = load_sol_trips(args.sol_cache)
    notes.append(f"SOL trades: {sol_note}")
    for name, key, favor, n_bar, preg in SOL_SHADOW:
        if key.startswith("sell:"):   # sell-side (exit) shadow flag on the trip
            skey = key[5:]
            sub = [t for t in sol_trips if t.get(skey) in favor]
        elif favor is None:    # presence-of-key stamp (rug_gate_buy)
            sub = [t for t in sol_trips if key in (t.get("entry_meta") or {})]
        else:
            sub = [t for t in sol_trips
                   if (t.get("entry_meta") or {}).get(key) in favor]
        m = _grade_cohort(sub, n_bar)
        if favor is None and m.get("verdict") not in ("NO-DATA", "ACCRUING"):
            # population-health stamp, NOT a promote/retire lever: a red number
            # here means the recent fleet is red, never "disable this gate".
            m["verdict"] = "BASELINE"
        if key.startswith("sell:") and m.get("verdict") not in ("NO-DATA", "ACCRUING"):
            # exit-cut shadow: PROMOTE=green is INVERTED (a red would-cut cohort =
            # cutting is correct). Relabel so nobody misreads red as a failure.
            m["verdict"] = "SHADOW-EXIT"
        rows.append(("SOL", name, m, preg))

    # A/B exit bots (grade each bot's own trips; control's ex2 shown for context)
    ctrl_trips = [t for t in sol_trips if t.get("bot") == SOL_AB_CONTROL]
    ctrl_m = ex_top2(ctrl_trips)
    for bot in SOL_AB_BOTS:
        sub = [t for t in sol_trips if t.get("bot") == bot]
        m = _grade_cohort(sub, SOL_AB_BAR)
        if bot != SOL_AB_CONTROL and ctrl_m["ex2_median"] is not None \
                and m["ex2_median"] is not None:
            m["vs_control"] = round(m["ex2_median"] - ctrl_m["ex2_median"], 2)
        rows.append(("SOL-A/B", bot, m, SOL_AB_PREG))

    # ── RH paper racers (all racers in the ledger vs rh_young_v1) ──────────
    rh_trips, rh_note = load_rh_trips(args.rh_ledger)
    notes.append(f"RH paper: {rh_note}")
    rh_by_bot = defaultdict(list)
    for t in rh_trips:
        rh_by_bot[t.get("bot")].append(t)
    rh_ctrl_m = ex_top2(rh_by_bot.get(RH_CONTROL, []))
    for bot in sorted(rh_by_bot):
        m = _grade_cohort(rh_by_bot[bot], 30)
        if bot != RH_CONTROL and rh_ctrl_m["ex2_median"] is not None \
                and m["ex2_median"] is not None:
            m["vs_control"] = round(m["ex2_median"] - rh_ctrl_m["ex2_median"], 2)
        rows.append(("RH", bot, m,
                     f"rh_paper_lane.py — n>=30 closes vs {RH_CONTROL}"))

    # ── rug cohort + bs compare (reported separately, not ex-top-2) ────────
    rug_counts, rug_sep, rug_note = load_rug_cohort(args.rug_labels)
    notes.append(f"rug cohort: {rug_note}")
    bs_out, bs_err = (None, "skipped (--no-bs)") if args.no_bs \
        else run_bs_compare(BS_COMPARE)
    notes.append("bs_ compare: " + (bs_err or "ran (see section below)"))

    _render(rows, notes, rug_counts, rug_sep, rug_note, bs_out, bs_err, rh_by_bot)


# ══════════════════════════════════════════════════════════════════════════
#  RENDER — markdown file + terse console summary
# ══════════════════════════════════════════════════════════════════════════

_ICON = {"PROMOTE": "PROMOTE ✅", "RETIRE": "RETIRE ❌", "MIXED": "MIXED ⚠",
         "BASELINE": "BASELINE ·", "ACCRUING": "accruing …", "NO-DATA": "no data —"}
_RANK = {"PROMOTE": 0, "MIXED": 1, "RETIRE": 2, "BASELINE": 3, "ACCRUING": 4, "NO-DATA": 5}


def _fmt(x):
    return f"{x:+.1f}" if isinstance(x, (int, float)) else "—"


def _render(rows, notes, rug_counts, rug_sep, rug_note, bs_out, bs_err, rh_by_bot=None):
    rows_sorted = sorted(rows, key=lambda r: (_RANK.get(r[2]["verdict"], 9),
                                              -(r[2]["n_tokens"] or 0)))
    from datetime import datetime, timezone
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    md = []
    md.append("# STATE OF THE EXPERIMENTS — scorecard\n")
    md.append(f"_Generated {stamp} · READ-ONLY (flags only; AxiS/main promotes)_\n")
    md.append("**Metric:** ex-top-2 token-median (per-token median, drop each "
              "cohort's 2 best tokens, median of the rest). GREEN = ex2 > 0 AND "
              ">=50% tokens green. Lifetime sum BANNED.\n")
    md.append("**Verdicts:** PROMOTE (bar met) · RETIRE (n>=bar, clearly failed) "
              "· MIXED (n>=bar, one criterion) · ACCRUING (n<bar) · NO-DATA.\n")
    md.append("\n## Data sources\n")
    for n in notes:
        md.append(f"- {n}")

    md.append("\n## Ranked table\n")
    md.append("| verdict | chain | experiment | n_tok | ex2-med | %grn | "
              "bar | vs ctrl | pre-reg |")
    md.append("|---|---|---|---:|---:|---:|---:|---:|---|")
    for chain, name, m, preg in rows_sorted:
        vc = m.get("vs_control")
        md.append(
            f"| {_ICON.get(m['verdict'], m['verdict'])} | {chain} | `{name}` | "
            f"{m['n_tokens'] or 0} | {_fmt(m['ex2_median'])} | "
            f"{_fmt(m['pct_green'])} | {m['n_bar']} | "
            f"{_fmt(vc) if vc is not None else '—'} | {preg.split(' — ')[0]} |")

    md.append("\n## Rug cohort (labeled forward; definitional grade)\n")
    md.append(f"- {rug_note}")
    if rug_counts:
        md.append(f"- composition: {rug_counts}")
    if rug_sep:
        md.append("- feature separation (median cat vs alive):")
        for s in rug_sep:
            md.append(f"    - {s}")
    else:
        md.append("- feature separation unlocks at catastrophic n>=5")

    md.append("\n## bs_ vs eth_getLogs (graduation grader wrap)\n")
    if bs_err:
        md.append(f"- {bs_err}")
    elif bs_out:
        md.append("```\n" + bs_out + "\n```")

    md.append("\n## Legend / next\n")
    md.append("- PROMOTE flags cleared their bar — bring to AxiS with the pre-reg "
              "file for the live/enforce decision (this tool never promotes).")
    md.append("- ACCRUING/NO-DATA is expected: forward tape just started; most "
              "shadow stamps and paper A/B bots have not reached their n bar.")
    md.append("- Re-run after each `sync_trades_cache.py --full` — idempotent.")

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")

    # ── terse console summary ─────────────────────────────────────────────
    from collections import Counter
    tally = Counter(m["verdict"] for _, _, m, _ in rows)
    print(f"\n══ EXPERIMENT SCORECARD ({stamp}) ══")
    for n in notes:
        print("  " + n)
    print(f"\n  {'verdict':<12}{'chain':<8}{'experiment':<34}"
          f"{'n':>4}{'ex2':>7}{'%grn':>6}{'bar':>5}")
    print("  " + "-" * 76)
    for chain, name, m, _ in rows_sorted:
        print(f"  {m['verdict']:<12}{chain:<8}{name[:33]:<34}"
              f"{m['n_tokens'] or 0:>4}{_fmt(m['ex2_median']):>7}"
              f"{_fmt(m['pct_green']):>6}{m['n_bar']:>5}")
    print("  " + "-" * 76)
    print("  tally: " + "  ".join(f"{k}={tally[k]}" for k in
                                  ("PROMOTE", "MIXED", "RETIRE", "BASELINE", "ACCRUING", "NO-DATA")
                                  if tally.get(k)))
    promotes = [name for _, name, m, _ in rows if m["verdict"] == "PROMOTE"]
    if promotes:
        print("  ⭐ CLEARED THE BAR (bring to AxiS): " + ", ".join(promotes))
    else:
        print("  nothing has cleared its bar yet — forward tape still accruing.")

    # ── STABILITY panel (2026-07-13 goal: 3 clearly-profitable+STABLE bots/side) ──
    # Stricter than PROMOTE: ex2>=0 AND >=55% green AND <=5% catastrophic tokens.
    # Shows the per-bot tail/dispersion so "stable" is measurable, not vibes.
    print(f"\n  ── STABILITY (goal: 3 stable+profitable per chain) ──")
    print(f"  {'stability':<11}{'chain':<8}{'bot':<30}"
          f"{'n':>4}{'ex2':>7}{'%grn':>6}{'cat%':>6}{'disp':>6}")
    stab_rows = [(c, nm, m) for c, nm, m, _ in rows_sorted
                 if c in ("RH", "SOL-A/B", "SOL")
                 and (m.get("n_tokens") or 0) > 0]
    stable_hits = {"SOL": [], "RH": []}
    for chain, name, m in stab_rows:
        sv = stability_verdict(m, m.get("n_bar", 30))
        if sv == "NO-DATA":
            continue
        print(f"  {sv:<11}{chain:<8}{name[:29]:<30}"
              f"{m['n_tokens']:>4}{_fmt(m['ex2_median']):>7}{_fmt(m['pct_green']):>6}"
              f"{_fmt(m.get('pct_catastrophic')):>6}{_fmt(m.get('dispersion')):>6}")
        if sv == "STABLE":
            stable_hits["RH" if chain == "RH" else "SOL"].append(name)
    print(f"  STABLE count -> SOL: {len(stable_hits['SOL'])}/3  RH: {len(stable_hits['RH'])}/3  "
          f"(goal: 3 each, n>=bar + green in majority of OOS windows)")

    # ── RH REGIME-NET panel (2026-07-13 goal: net-$/pos higher AND regime-robust) ──
    # The honest dollar metric. median-% HID that every RH bot lost on the bad
    # regime day (07-11) and won on the good one (07-12) — beta to regime, not an
    # edge. Here: net-$/position AFTER friction, split PER DAY. REGIME-ROBUST =
    # net-positive on EVERY day seen (the bar AxiS set: "sustainable across regimes").
    # Paper pnl_usd ALREADY nets the 1% pool fee + price impact + gas (real
    # eth_call quotes, rh_paper_lane.py:23). This friction is the estimated
    # LIVE-EXTRA on top: latency-slippage (price moves during the ~1.2s fill the
    # paper quote doesn't model). So net-after-this ~= a conservative LIVE net.
    RH_FRICTION_USD = 0.20
    if not rh_by_bot:
        rh_by_bot = {}
    print(f"\n  ── RH REGIME-NET (net-$/pos after ~${RH_FRICTION_USD} friction, per regime-day) ──")
    print(f"  {'bot':<20}{'n':>4}{'net$/pos':>9}{'total$':>8}{'days+':>7}  per-day net$/pos")
    regime_robust = []
    for bot in sorted(rh_by_bot, key=lambda b: -sum(
            (t.get("ret") or 0) / 100.0 * RH_ENTRY_USD for t in rh_by_bot[b])):
        trips = rh_by_bot[bot]
        if len(trips) < 8:
            continue
        byday = defaultdict(list)
        for t in trips:
            net = (t.get("ret") or 0.0) / 100.0 * RH_ENTRY_USD - RH_FRICTION_USD
            byday[(t.get("sell_time") or "")[:10]].append(net)
        allnet = [n for v in byday.values() for n in v]
        tot = sum(allnet)
        npos = len(allnet)
        dplus = sum(1 for v in byday.values() if sum(v) > 0)
        robust = dplus == len(byday) and len(byday) >= 2
        perday = " ".join(f"{d[5:]}:{sum(v)/len(v):+.2f}" for d, v in sorted(byday.items()))
        flag = "  <== REGIME-ROBUST" if robust else ""
        print(f"  {bot[:19]:<20}{npos:>4}{tot/npos:>+9.2f}{tot:>+8.1f}"
              f"{dplus:>4}/{len(byday)}  {perday}{flag}")
        if robust:
            regime_robust.append(bot)
    print(f"  REGIME-ROBUST (net+ every day) -> {regime_robust or 'NONE — RH net is regime-beta; levers = tail-cap + regime-sizing gate + more tape'}")

    print(f"\n  full table -> {os.path.relpath(OUT_MD, REPO)}")


if __name__ == "__main__":
    main()
