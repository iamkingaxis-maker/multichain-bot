# Token Bounce Persistence — 2026-07-06

**Question:** do tokens that bounced their last flush bounce the next one? Today's fleet data showed re-entries after a fleet WIN at 38.6% WR / +2.46 mean vs 21.5% base — real token-level property ("proven bouncer") or survivorship noise?

**Data:** zero egress — reused the trough study's cached 1m bars (40 pairs, DexScreener io + GeckoTerminal fill, window 2026-07-05T12:00Z → 07-06T11:29Z). Scripts `bounce_persist.py` / `bounce_persist2.py` / `bounce_persist3.py` + reports in session scratchpad `C:\Users\jcole\AppData\Local\Temp\claude\C--Users-jcole-multichain-bot\ecbaef77-2f98-4dc5-9231-4bd9a529e92c\scratchpad\`.

## Method (documented changes from spec)

**Troughs:** local min of 1m lows over ±15 bars (first-of-tie), decline from max high of preceding 60min, dedupe <600s keep-lower — same skeleton as the price study. Two tiers: **FLUSH ≥10% decline** (n=857 resolved troughs, 40 pairs), **DIP ≥5%** (n=898, 40 pairs).
- **Change 1 — bounce filter removed from trough detection.** The study's "≥5% bounce within 15min" requirement selects on the *outcome* — it censors failed flushes out of the fail branch and would rig the transition matrix. (Robustness run with the filter kept: base success 98.9%, fail branch n=9 — confirms the censoring.)
- **Change 2 — added a REAL outcome geometry.** The spec'd outcome (trough_low×1.06 before ×0.93, 30min) is **degenerate on this universe: 96.5% success (FLUSH), break n=1/857.** Two structural reasons: (a) +6% off the wick low is a wick-tag on tokens with 5-10% intrabar ranges; (b) the ±15-bar local-min definition forbids a lower low for 15min by construction. That geometry cannot host a 21.5%-base comparison.
  **REAL variant (primary): entry = close of the first bar after the trough bar (≤5min), success = high ≥ entry×1.06 before low ≤ entry×0.93 within 30min of entry** (timeout = fail). Base: FLUSH 77.9%, DIP 77.1%; outcome mix (FLUSH): hit 668, break 118, timeout 67, ambig 4. This mirrors what a bot buying the detected trough actually experiences.

## Results (REAL geometry)

**Transition matrix:**

| tier | P(s \| prev SUCCESS) | P(s \| prev FAIL) | gap | z | within-pair shuffle p |
|---|---|---|---|---|---|
| FLUSH | 79.0% (n=639) | 73.6% (n=178) | +5.4pp | 1.54 | 0.07-0.09 |
| DIP | 79.1% (n=665) | 69.4% (n=193) | +9.7pp | 2.81 | 0.006 |

(SPEC geometry for the record: FLUSH +13.5pp z=3.5, DIP +25.7pp z=6.9 — but on a 96% base with fail-branch n=24/34; not meaningful.)

**Concentration / robustness — the decisive test:**
- Per-pair sign test (≥3 transitions each prior-state): FLUSH **13/29 pairs positive, 16 opposite**; DIP **17/31 positive, 14 opposite** — a coin flip.
- Drop-top-K contributing pairs, pooled gap (shuffle p): FLUSH drop-3 (Elon/BABYANSEM/MENSA) → **+2.1pp, p=0.42**; drop-5 → +0.1pp. DIP drop-3 (yep/Elon/WIFBULL) → +6.0pp, p=0.12; drop-5 → **+3.9pp, p=0.31**.
- → The entire pooled effect lives in **3-5 pairs out of 40**. This is exactly the survivorship/concentration signature the question asked about.

**Market-state control:** other-pairs' 90min trough success rate barely predicts (HIGH vs LOW: +2.0/+2.4pp, z<0.9), and the own-prev gap persists within both strata — so what little persistence exists is not an hour-of-day market regime; it's just concentrated in a few tokens on this one window.

**Ordinal decay (k-th flush of the window): NO decay.** FLUSH: k=1 80.0%, k=2 60.0%, k=3 95.0%, k=4 82.5%, k=5+ 77.6% (n=40 per early cell, 697 at 5+). DIP same shape. Non-monotone; k5+ ≈ base; the k=2 dip is a small-n artifact. **No basis for "deprioritize ordinal ≥3".** (These liquid pairs flush ~21×/day median — ordinal saturates immediately.)

**Gap-to-prior decay:** recency helps *both* branches roughly equally (prevS: ≤1h 80.4-80.9% → 1-4h 75.1-75.7%; prevF: 71-76% → 64-68%); the S−F spread is ~flat across gaps. >4h cells are empty (n≤6) — these tokens flush too often for long gaps to exist. So "prior bounce within 4h" adds nothing beyond "prior bounce" here.

## Verdict

**"Proven bouncer" is NOT a validated token-level signal on this window.** Effect size pooled is only +5.4pp (FLUSH) / +9.7pp (DIP) on a 77% base, the per-pair split is a coin flip, and removing 3-5 pairs erases it (shuffle p → 0.1-0.7). The flush-ordinal decay curve is flat — no late-flush penalty either.

**Implication for the fleet stat (38.6% vs 21.5%):** token-level price persistence cannot be the mechanism — wrong base (bounce success is 77%, not 21.5%) and wrong magnitude (+5-10pp vs +17pp). The fleet's "re-entry after WIN" edge, if real, is encoding something else: the fleet's own entry/exit execution quality on that token, wallet-flow composition, or small-n luck in the fleet sample. Treat it as an *execution/selection* signal to be re-tested on fleet data with n reported, not as a token property.

**Do not ship** a prioritize-proven-bouncer or deprioritize-ordinal gate from this. If a lean is wanted for a shadow counter only: "previous flush FAILED from re-entry price within 4h" is mildly toxic (−8 to −10pp pooled) — same concentration caveat, so shadow-count it on the next fresh tape day before any gate discussion.

## Caveats
- Single ~23.5h window, top-40-by-activity pairs (the trough study's universe); dead/rugged tokens that stopped printing bars are underrepresented — which *inflates* apparent bounce rates and persistence.
- Trough-based framing structurally underweights "kept dumping" failures (a continued dump moves the local-min lower rather than logging a failed trough); the REAL geometry partially recovers this via the break/timeout branches.
- 1m bars: same-bar hit+break = ambiguous (n=4, counted as fail).
