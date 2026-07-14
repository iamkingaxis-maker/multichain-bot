# Verification for the ENFORCED RH concentration rug gate (2026-07-13).
# Grades the SHIPPED core.rh_rug_signals.rug_gate_verdict (not a reimplemented
# predicate) on (a) the combined labeled at-entry set (ledger stamps + retro
# reconstructions from _rh_rug_port.md RQ3) and (b) the 20 accrued ledger
# rug_signals stamps. Reports catch / winner-kill / loss-hit. PONS must PASS.
import json
import os

from core.rh_rug_signals import rug_gate_verdict

# ── combined labeled set (identical rows to _rh_rug_gate_sweep.py) ────────────
# sym, label, top1, top10  (features are at-entry; label = worst realized trip)
R = [
    ('CASHCATWIF', 'RUG', 10.61, 50.56), ('CASHCATGAME', 'RUG', 11.9, 22.7),
    ('Halp', 'RUG', 1.6, 12.1),
    ('seedcoin', 'LOSS', 2.1, 17.41), ('MONSIEUR', 'LOSS', 2.0, 16.4),
    ('KUNA', 'LOSS', 2.0, 17.1), ('TREAT', 'LOSS', 2.0, 15.9),
    ('manhood', 'WIN', 4.68, 23.5), ('BOW', 'WIN', 3.19, 19.4),
    ('UTILITY', 'WIN', 2.0, 14.87), ('uhood', 'WIN', 2.94, 20.23),
    ('NASDOG', 'WIN', 3.27, 21.43), ('Artcoin', 'WIN', 2.56, 18.58),
    ('BROKEBEAR', 'WIN', 7.77, 22.7), ('Pointless', 'WIN', 2.83, 17.79),
    ('HOODBOT', 'WIN', 1.97, 17.7), ('DATABEAR', 'WIN', 1.84, 12.99),
    ('Hedge', 'WIN', 2.37, 20.04), ('BABYCASHCAT', 'WIN', 2.1, 13.65),
    ('POOCH', 'WIN', 2.85, 22.82), ('FOX', 'WIN', 1.81, 16.89),
    ('WALLET', 'WIN', 2.75, 14.01), ('SUIT', 'WIN', 2.22, 17.83),
    ('spinor', 'WIN', 5.93, 22.46), ('1c', 'WIN', 1.55, 11.35),
    ('Ape', 'WIN', 4.4, 21.9), ('RANGER', 'WIN', 5.0, 18.3),
    ('hehe', 'WIN', 1.9, 12.9), ('BILLY', 'WIN', 5.5, 21.3),
    # winner example named in the mandate — must PASS
    ('PONS', 'WIN', 2.49, 19.67),
]


def blocks(top1, top10):
    # feed the recon-shaped stamp; the shipped verdict falls back to top1/top10
    return rug_gate_verdict({"top1_pct": top1, "top10_pct": top10})["rug_gate_block"]


rugs = [r for r in R if r[1] == 'RUG']
wins = [r for r in R if r[1] == 'WIN']
loss = [r for r in R if r[1] == 'LOSS']

cr = [r[0] for r in rugs if blocks(r[2], r[3])]
wk = [r[0] for r in wins if blocks(r[2], r[3])]
lh = [r[0] for r in loss if blocks(r[2], r[3])]

print("=== SHIPPED rug_gate_verdict on combined labeled set "
      "(%d RUG / %d WIN / %d LOSS) ===" % (len(rugs), len(wins), len(loss)))
print("  CATCH (rugs blocked):      %d/%d  %s" % (len(cr), len(rugs), cr))
print("  WINNER-KILL (wins blocked): %d/%d  %s" % (len(wk), len(wins), wk))
print("  LOSS-HIT (losses blocked):  %d/%d  %s" % (len(lh), len(loss), lh))
pons = next(r for r in R if r[0] == 'PONS')
print("  PONS (top1 %.2f / top10 %.2f) -> %s" % (
    pons[2], pons[3], "BLOCK" if blocks(pons[2], pons[3]) else "PASS  (correct)"))
print("  winner-kill rate = %.1f%%  (bar: <= 5%%)" % (100.0 * len(wk) / len(wins)))
print()

# ── forward-grade sanity: the 20 accrued ledger rug_signals stamps ───────────
LEDGER = os.path.join("scratchpad", "robinhood_tapes", "rh_paper_trades.jsonl")
seen = {}
with open(LEDGER, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("ev") == "rug_signals":
            seen[r.get("token")] = r   # last stamp per token

flagged = []
for tok, r in seen.items():
    # prefer bs_ (what a live prewarm produces); fall back to recon
    v = rug_gate_verdict(r)
    if v["rug_gate_block"]:
        flagged.append((r.get("sym"), v["rug_gate_reason"], v["rug_gate_source"]))
print("=== accrued ledger stamps (%d distinct tokens) forward-grade ===" % len(seen))
print("  flagged: %d/%d  %s" % (len(flagged), len(seen), flagged))
