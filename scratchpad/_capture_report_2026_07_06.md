# Daily Capture Report — 2026-07-06 (first run of the ritual)

**Purpose (standing directive):** detect the winners we never touched. CAPTURE RATE = pond bounces traded / pond bounces available. Tool: `scripts/capture_report.py` (new, reusable; analysis only, no bot changes).

## Data
- Bars: session cache `bars_ext/` — 94 pairs, 1m OHLC through **16:00Z** (io.dexscreener; universe = top-40 by tape activity ∪ fleet trade pairs ∪ live-tape pairs from the 07-06 cascade study). No refresh needed (cache already reached 16:00Z).
- Trades: fresh `/api/trades?full=1&limit=5000` pull cached at `scratchpad/_trades_full_2026_07_06.json` (covers 06-27 → 07-06 16:21Z; today = 106 buys across 19 pairs).
- Symbols: `bars_ext_report.json`. Liq/mcap for touched pairs from buy `entry_meta`; for untouched pairs via one DexScreener batch lookup (`scratchpad/_capture_ds_lookup_2026_07_06.json`), scaled to trough time by in-bars price ratio (crude — supply-fixed assumption).

## Definitions
- **Bounce event**: bar low ≥10% below max high of preceding 60min (≥5 prior bars), then ≥+10% rise from trough low within 45min. WIDE tier: ≥+20%. Troughs deduped keep-lower within 45min. Trough-day = today 00:00Z→16:00Z.
- **CAPTURED** = badday-bot buy in pair within trough±15min. **NEAR-MISS** = any fleet buy same pair today outside window. **UNTOUCHED** = no fleet buy today. Unresolved troughs at tape end (28) excluded from denominator.

## Results
| tier | capture rate | captured | near-miss | untouched | n | median rise |
|---|---|---|---|---|---|---|
| BASE ≥+10% | **1.5%** | 11 | 150 | 549 | 710 | 27.0% |
| WIDE ≥+20% | **2.2%** | 10 | 96 | 355 | 461 | 39.9% |

Captured: popeyes 12:53 (+63%), CITH 12:26, Fro 13:41, SEMAN 09:23, HAALAND 09:50, DONALT 10:23 (+129%) & 11:20, ANIMEBULL 07:57, FROGBULL 13:58, ACM 14:50 (+65%), Goofreck 14:23. Flush family + young_absorb family; all 07-15Z.

Biggest near-misses (pair traded today, window missed): TOLY 13:56 +517%, DONALT 05:25 +320%, Fro 01:58 +272%, popeyes 00:20 +168%.

## Top-10 UNTOUCHED bounces (zero fleet interaction — checked by pair8 AND token name: none in feed)
| sym | pair8 | trough | rise% | decl% | age now | est mcap@trough | liq now | pond verdict @trough |
|---|---|---|---|---|---|---|---|---|
| Miku | Er2VcdAw | 09:12 | +1167 | −56 | 8.2h | ~$21k | $29k | OUT (micro; young lane) |
| SPYZER | J89ppGTD | 07:09 | +1060 | −52 | 11.6h | ~$39k | $10k | OUT/borderline (micro) |
| ANSEM'D | 3jMoFqRN | 05:13 | +824 | −70 | 13.1h | ~$53k | $17k | BORDERLINE (mcap ok, liq thin) |
| CEEZEE | CkFH3yws | 01:12 | +783 | −52 | 22.7h | ~$3k | $6k | OUT (dust) |
| BULLISH | AMyRQnka | 05:00 | +716 | −74 | 12.3h | ~$45k | $4k | OUT/borderline; later rugged |
| BIF | 6wjSpKYM | 11:09 | +583 | −42 | 13.9h | ~$827k | $143k | **IN POND — real miss** |
| LEVI | EqMxjt3v | 01:09 | +497 | −26 | 20.1h | ~$219k | $256k | **IN POND — real miss** |
| wifout | 5nFRogAy | 07:07 | +484 | −40 | 10.2h | ~$19k | $24k | OUT (micro) |
| Manga | 36kbwjSt | 07:15 | +443 | −67 | 10.3h | ~$37k | $3k | OUT; later rugged |
| wifout | 7iozyKas | 08:13 | +384 | −56 | 9.5h | ~$62k | $10k | BORDERLINE |

All 10 are age <24h (young pond). Trough bars sanity-checked: lows are multi-bar with real volume, not single-print wicks.

## Where the misses concentrate
- **By hour — FLAT, not a sleep artifact**: untouched by regime block: 00-03Z 94 (17%), 03-08 sleep 156 (28%), 09-13 dead 148 (27%), 13-16 prime 113 (21%). **67 WIDE (≥+20%) untouched bounces happened inside 13-16Z prime hours** while bots were awake and firing.
- **By universe**: the monster (>+400%) misses are dominated by sub-$50k-mcap micro-caps in the <24h lane — launch-arc/young-probe territory, deliberately outside the fleet-wide pond. But **BIF (+583%, ~$827k mcap, $143k liq) and LEVI (+497%, ~$219k mcap)** were squarely in the fleet-wide tradeable pond and got zero interaction: the live watchlist touched only 19 pairs today vs a 94-pair bouncing pond. Breadth, not schedule, is the gap.

## Caveats
- Denominator = the 94 cached pairs, not the full scanner universe; capture window is ±15min (strict); a bot may have SEEN and blocked these — blocked-would-buy isn't in the feed.
- Base rate context (cascade study): this pond runs ~94 flush onsets/hour with 88% bouncing ≥+6% — a raw 10/10 definition yields ~44 events/hour available; 1.5% capture is a breadth statement, not (yet) an edge-forgone statement. Pond-filtered capture rate (the true ritual metric) needs liq/mcap history per pair — only the top misses were classified this run.
- mcap@trough is price-scaled from current mcap (supply-fixed assumption; rugs/mints distort). Single day, bars end 16:00Z.

## Rerun tomorrow
`python scripts/capture_report.py --bars <bars_dir> --trades <fresh /api/trades?full=1 dump> --meta <pair8→sym json> --day YYYY-MM-DD`
