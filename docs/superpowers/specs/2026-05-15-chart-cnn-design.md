# Lightweight CNN Chart Classifier — Design

**Status:** Design (pre-implementation)
**Date:** 2026-05-15
**Owner:** jcoleman-droid

## Problem

Numerical TA (chart_reader.py composite_score, mtf_score, sweep verdicts) misses visual patterns that an experienced trader sees at a glance — V-bottom reversals, double-bottoms, head-and-shoulders, etc. Today's audit of `pullback_in_uptrend` BULLISH misfire and `clean_break` losers shows the same shape: numerical features pass, but the chart "obviously" looked bad. We need a model that consumes the chart as an image and outputs a structured verdict — usable at three points: live entry gate (shadow first), peak-recorder enrichment, post-mortem audit.

## Goals

- **Single CNN model** with two prediction heads: named pattern + outcome probability
- Plugs into three layers: live entry signal, post-entry shadow, post-mortem auditing
- Production-safe: missing weights or render failures degrade silently, never block trading
- Forward-collected dataset that grows automatically as the bot runs
- Train/serve skew prevented by a single shared image renderer

## Non-goals

- Transformer / ViT architecture (data-scarcity risk; revisit when dataset > 5K)
- Self-supervised pretraining (premature with current dataset size)
- Predicting peak% or time-to-peak (deferred to v2)
- Replacing existing chart_reader.py (additive, not a swap)
- Live ENFORCED gating in v1 (shadow-only until forward validation passes)

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  TRAIN TIME (one-shot, weekly retrain)                              │
│                                                                      │
│  /api/trades + chart_data → render_chart_image → labeled dataset    │
│         ↓                                          ↓                 │
│  (~500 historical seed)                     chart_cnn_v1.pt          │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  INFERENCE TIME (every scan, ~30ms total)                           │
│                                                                      │
│  candles → render → CNN.predict() →                                 │
│       { pattern, pattern_conf, outcome_prob }                       │
│                                                                      │
│  Consumed by: dip_scanner (live shadow), peak_recorder, postmortem. │
└──────────────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────────────┐
│  FORWARD COLLECTION (continuous, every scan)                        │
│                                                                      │
│  Every candidate (signal/blocked/winner/loser) →                    │
│  dump .npy + .json to /data/cnn_dataset/forward/{date}/             │
│  Feeds the next retrain cycle.                                       │
└──────────────────────────────────────────────────────────────────────┘
```

### Key principles
- **One image renderer** shared across train + inference + forward collection. Train/serve skew is structurally impossible.
- **Model artifacts are optional.** If `chart_cnn_v1.pt` is missing, the bot logs a warning at startup and proceeds without CNN predictions. CNN can never block trading.
- **All integrations are shadow-first.** v1 writes predictions to `entry_meta_dict` but does not gate any decision. Promotion to enforced requires 7 days of forward-validation passing the calibration thresholds in the Testing section.

## Components

### New files

**`models/chart_cnn.py`** — PyTorch model.
- 4 conv blocks (Conv2d → BatchNorm → ReLU → MaxPool), input shape `(3, 64, 64)`
- 1 fully-connected layer (~128 dims)
- Two heads: pattern (softmax over ~10 classes), outcome (sigmoid scalar)
- ~100K params total. CPU-inference target: 20-50ms.

**`feeds/chart_image_renderer.py`** — shared image renderer.
- Input: `candles_1m`, `candles_5m`, `candles_15m` (existing `Candle` dataclass).
- Output: `numpy.ndarray` of shape `(3, 64, 64)` dtype uint8, or `None` if any TF has fewer than 30 bars.
- Per TF rendered as a 64-pixel-wide greyscale strip:
  - X-axis: 60 most-recent candles, oldest left
  - Y-axis: log-normalized price range over the window (token-agnostic)
  - Body fill: 255 if green close ≥ open, 128 if red
  - Wick: 64
  - Empty: 0
- Channels: `image[0]` = 1m, `image[1]` = 5m, `image[2]` = 15m.

**`scripts/backfill_chart_dataset.py`** — historical seed.
- Pulls all closed trades from `/api/trades` (paginated).
- For each, fetches pre-entry `chart_data` (1m + 5m + 15m) via existing `assemble_chart_data`.
- Renders 3-channel image; writes `.cnn_dataset/v1/{addr}_{entry_ts}.npy` plus a label JSON.
- Pattern label: from `chart_reader.pattern_5m`. Outcome label: `1` if total pnl > 0, `0` otherwise.

**`scripts/train_chart_cnn.py`** — training pipeline.
- Loads `.cnn_dataset/v1/*.npy` + labels.
- Date-stratified split (train < cutoff, val ≥ cutoff) to prevent temporal leakage.
- PyTorch training loop, ~10 epochs, Adam optimizer.
- Loss: cross-entropy for pattern head + binary cross-entropy for outcome head, weighted 1:1.
- Saves best-val-loss weights to `models/chart_cnn_v1.pt`.
- Prints confusion matrix for pattern head, calibration plot for outcome head.

**`core/chart_cnn_inference.py`** — production inference singleton.
- Lazy-loads `chart_cnn_v1.pt` at first call (one-time cost).
- Public API: `predict(candles_1m, candles_5m, candles_15m) → dict | None`.
- Return shape: `{pattern: str, pattern_conf: float, outcome_prob: float}`.
- LRU cache keyed by `(token_address, candles_1m[-1].open_time)` — same minute = no re-inference.
- Returns `None` on any failure (missing weights, render failure, inference exception).
- Self-disable for 60s after any uncaught exception, then retry. Throttled WARNING log on each disable.

**`feeds/forward_dataset_collector.py`** — continuous data collection.
- Called from `dip_scanner.py` after every scan iteration that has chart_data.
- Renders image, writes `.npy` + partial JSON to `/data/cnn_dataset/forward/{YYYY-MM-DD}/{addr}_{ts}.npy`.
- Partial JSON has all context labels (`triggers_fired`, `filters_blocked`, `hour_ct`, `mcap_usd`) but `outcome_label = null` until the trade closes (or remains null if no buy).
- Disk-space guard: skip write with throttled WARNING if disk > 95% full.

### Modifications to existing files

**`feeds/dip_scanner.py`** — shadow integration.
- After `_chart_ctx_dict` is built (around line 2406), call `chart_cnn_inference.predict()`.
- Append `cnn_pattern`, `cnn_pattern_conf`, `cnn_outcome_prob` to `entry_meta_dict`.
- No gating in v1.

**`core/peak_recorder.py`** — capture at init.
- In `init_position`, call `chart_cnn_inference.predict()` on the entry candles and stamp result into the trace JSON. Lets us correlate CNN's entry verdict with eventual outcome.

**`scripts/postmortem.py`** — audit integration.
- On loser audit, render and run CNN on pre-entry context.
- Print pattern verdict + outcome probability alongside the existing chart_reader output.

## Data flow

### Image format
- Shape: `(3, 64, 64)` uint8 numpy array.
- Channel 0: 1m TF, channel 1: 5m TF, channel 2: 15m TF.
- 60 most-recent candles per TF, rendered as a 64-px-wide greyscale strip.
- Price axis: log-normalized over the per-TF window (each channel independent), making the model token-agnostic.

### Label format
```json
{
  "addr": "gvv7sfu6fhjssvxfpg7xqfnwar3c7ykcc74rqe7bpump",
  "ts": 1715723200,
  "pattern_label": "double_bottom",
  "outcome_label": 1,
  "outcome_pnl_pct": 4.03,
  "context": {
    "triggers_fired": ["patient_bottom", "informed_cluster"],
    "filters_blocked": [],
    "hour_ct": 22,
    "mcap_usd": 512832
  }
}
```

`pattern_label` taken from `chart_reader.pattern_5m`. v1 supports the existing pattern_5m class set (~10 classes including `double_bottom`, `bullish_engulfing`, `symmetrical_triangle`, etc., plus `none`).

`outcome_label` is `1` if total pnl > 0 at close, `0` otherwise. `outcome_pnl_pct` retained as a regression target for a possible v2 outcome-magnitude head.

### Pattern class set
Initial classes (subject to chart_reader's emitted vocabulary):
- `none` (no recognized pattern)
- `double_bottom`
- `bullish_engulfing`
- `bearish_engulfing`
- `symmetrical_triangle`
- `ascending_triangle`
- `descending_triangle`
- `head_and_shoulders`
- `inverse_head_and_shoulders`
- `v_bottom`

If chart_reader emits fewer than 10 unique values during backfill, the class set shrinks to match. Class imbalance handled with weighted sampling at training time.

### Backfill flow
```
for each closed trade in /api/trades:
  - fetch pre-entry chart_data (1m + 5m + 15m)
  - render 3-channel image
  - pattern_label ← chart_reader.pattern_5m
  - outcome_label ← sign(total_pnl)
  - save .npy + .json
```

### Forward collection flow (continuous)
```
in dip_scanner cycle, after _chart_ctx_dict built:
  - render image
  - dump .npy + partial JSON (outcome_label = null)
  - on trade close: append outcome_label, outcome_pnl_pct to the JSON
  - on no-buy: outcome_label remains null (still useful for pattern-head training)
```

### Inference flow
```
dip_scanner has candles → renderer → CNN → {pattern, conf, outcome_prob}
                ↓
       cached for 60s per (addr, latest_1m_open_time)
                ↓
   written to entry_meta_dict alongside chart_score, chart_mtf_score, etc.
```

## Error handling

The CNN must never break trading. All failures degrade gracefully:

1. **Model file missing** — log `INFO` once at startup, set inference singleton to `disabled=True`. All `predict()` calls return `None`. Bot proceeds normally.
2. **Render failure** (insufficient candles, NaN price, etc.) — renderer returns `None`. Inference returns `None`. `cnn_*` fields in entry_meta stay `None`. No exception bubbles up.
3. **Inference exception** (corrupt weights, torch crash, OOM) — caught at the call site, throttled-WARNING logged once per 5 min, singleton self-disables for 60 s then retries. No log spam, no wedge.
4. **Forward collector disk full** — pre-write disk check; on > 95 % full, drop write silently with throttled WARNING.
5. **Forward dataset corruption** — training pipeline skips unreadable files with WARNING. Pipeline reports `loaded N of M files` so data loss is visible.
6. **Train/serve skew** — structurally prevented: one `chart_image_renderer.py` called from train, inference, and collector. No format drift possible.

### Inference budget

| Step | Budget | Target |
|---|---|---|
| Render image | < 10 ms | ~3 ms (pure numpy) |
| Model forward pass | < 50 ms | ~20 ms on CPU |
| Total per call | < 100 ms | ~30 ms |
| Cached repeat (same minute) | < 1 ms | LRU dict hit |

Calls exceeding 200 ms log a WARNING — diagnostic signal that something is wrong with the environment.

## Testing

### Unit tests
1. **Renderer determinism** — identical Candle inputs produce identical bytes. Catches matplotlib / numpy version drift.
2. **Renderer shape invariants** — output always `(3, 64, 64)` uint8 when ≥ 30 bars per TF. Edge cases: all-flat candles, all-green, all-red, extreme outliers.
3. **Model loader** — randomly-initialized model can `predict()` on a synthetic image without errors.
4. **End-to-end smoke** — one historical trade through render → predict, assert outputs in valid ranges.

### Validation gates (not unit tests — measured on held-out set)
5. **Pattern agreement** — when both CNN and chart_reader emit a pattern, agreement rate ≥ 60 %. Below that, CNN failed to learn the labels.
6. **Outcome calibration** — predicted `outcome_prob` bucketed against actual win rate. A well-calibrated model: 0.8-bucket predictions win ~80 % of the time. Used to choose the eventual live-gating threshold.

### Promotion gate (shadow → enforced)
Before any CNN prediction gates a live trade:
- `n ≥ 100` forward predictions accumulated
- Outcome calibration within ±10 % across all probability buckets
- CNN pattern matches chart_reader pattern ≥ 70 % of the time on the held-out set
- Zero production exceptions for 7 consecutive days

## Open questions

None at design time. Implementation plan will address sequencing, file paths, and dataset versioning.

## Risks and mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| 500-trade seed is too small to train | High | Forward collector grows dataset 500-2000/day. Initial model may be weak; explicitly shadow-only until validation passes. |
| Pattern labels biased by chart_reader's mistakes | High | Documented limitation. v2 can add a small manual-label override set if needed. |
| Inference latency exceeds budget | Low | 64×64 + 100k params is fast on CPU. Cached per-minute. Budget already conservative (3-4× target). |
| Outcome head over-fits to recent regime | Medium | Date-stratified split + weekly retrain. Calibration plot makes regime drift visible. |
| Disk fills up with forward collector data | Low | Disk-space guard + 30-day retention. Old `.npy` files purged weekly. |
| Model file corruption breaks startup | Low | Lazy-load wrapped in try/except; corruption disables CNN, bot continues. |

## Out-of-scope (deferred to v2+)

- Peak% and time-to-peak prediction heads (need more data + a regression-specific eval).
- Self-supervised pretraining on unlabeled DexScreener charts.
- ViT / transformer architecture upgrade (revisit when forward dataset > 5K).
- Live ENFORCED gating (gate is calibration-based; shadow first).
- Cross-token transfer learning (treat every memecoin as same distribution for now).
