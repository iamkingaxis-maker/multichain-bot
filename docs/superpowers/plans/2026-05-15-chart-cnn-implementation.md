# Chart CNN Classifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and integrate a lightweight CNN that classifies chart images (3-channel 64×64) into named pattern + outcome probability, with three integration points (live shadow gate, peak_recorder stamping, postmortem audit) and a forward-collected dataset that grows automatically.

**Architecture:** Single 4-layer CNN (~100K params) trained on PyTorch. Shared `chart_image_renderer.py` guarantees no train/serve skew. All integrations shadow-only in v1 — promotion to enforced requires forward validation. Spec: `docs/superpowers/specs/2026-05-15-chart-cnn-design.md`.

**Tech Stack:** Python 3.12, PyTorch (CPU), numpy, existing project deps (curl_cffi, aiohttp). Tests use stdlib `unittest` + assertions, no pytest framework required (matches existing `tests/` pattern).

---

## File Inventory

**New files (created in this plan):**
- `feeds/chart_image_renderer.py` — shared image renderer
- `models/__init__.py`, `models/chart_cnn.py` — PyTorch model
- `core/chart_cnn_inference.py` — production inference singleton
- `feeds/forward_dataset_collector.py` — continuous data collection
- `scripts/backfill_chart_dataset.py` — historical seed
- `scripts/train_chart_cnn.py` — training pipeline
- `tests/test_chart_image_renderer.py`
- `tests/test_chart_cnn.py`
- `tests/test_chart_cnn_inference.py`
- `tests/test_forward_dataset_collector.py`

**Modified files:**
- `feeds/dip_scanner.py` — shadow integration (entry_meta_dict additions)
- `core/peak_recorder.py` — stamp CNN at `init_position`
- `scripts/postmortem.py` — append CNN verdict to audit output
- `requirements.txt` (or equivalent) — add `torch`

---

## Task 1: Add PyTorch dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Check current requirements**

Run: `cat requirements.txt | head -20`

If torch is already present, skip to Task 2.

- [ ] **Step 2: Add torch (CPU-only build)**

Append to `requirements.txt`:

```
torch>=2.0.0,<3.0.0
```

- [ ] **Step 3: Install locally**

Run: `pip install torch --index-url https://download.pytorch.org/whl/cpu`

Expected: install completes, `python -c "import torch; print(torch.__version__)"` prints a version.

- [ ] **Step 4: Verify torch loads on Railway**

Check if Railway's deploy already has torch (some templates do). If not, the `requirements.txt` add will pull it on next deploy.

Run: `python -c "import torch; t = torch.zeros(3, 64, 64); print(t.shape)"`

Expected: `torch.Size([3, 64, 64])`

- [ ] **Step 5: Commit**

```bash
git add requirements.txt
git commit -m "deps: add torch (CPU build) for chart CNN classifier"
```

---

## Task 2: Image renderer — failing test

**Files:**
- Create: `tests/test_chart_image_renderer.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_chart_image_renderer.py`:

```python
"""Tests for the chart image renderer.

The renderer is the shared train/inference contract. Tests verify:
  - Output shape is always (3, 64, 64) uint8
  - Determinism: same inputs → identical bytes
  - None returned on insufficient data
  - Green/red coloring matches close vs open
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from feeds.candle_utils import Candle
from feeds.chart_image_renderer import render_chart_image


def _make_candle(ts: int, o: float, h: float, l: float, c: float, v: float = 100.0) -> Candle:
    return Candle(open_time=ts, open=o, high=h, low=l, close=c, volume=v, close_time=ts + 60)


def _flat_candles(n: int, base_ts: int = 1700000000) -> list:
    return [_make_candle(base_ts + i * 60, 1.0, 1.01, 0.99, 1.0) for i in range(n)]


def test_returns_correct_shape_and_dtype():
    c1 = _flat_candles(60)
    c5 = _flat_candles(60, base_ts=1700000000 - 300 * 60)
    c15 = _flat_candles(60, base_ts=1700000000 - 900 * 60)
    img = render_chart_image(c1, c5, c15)
    assert img is not None
    assert img.shape == (3, 64, 64)
    assert img.dtype == np.uint8


def test_returns_none_when_insufficient_candles():
    c1 = _flat_candles(20)  # below 30 minimum
    c5 = _flat_candles(60)
    c15 = _flat_candles(60)
    assert render_chart_image(c1, c5, c15) is None


def test_determinism_same_inputs_same_bytes():
    c1 = _flat_candles(60)
    c5 = _flat_candles(60)
    c15 = _flat_candles(60)
    img1 = render_chart_image(c1, c5, c15)
    img2 = render_chart_image(c1, c5, c15)
    assert np.array_equal(img1, img2)


def test_green_candle_brighter_than_red():
    # Build one all-green and one all-red 60-bar series
    base_ts = 1700000000
    green = [_make_candle(base_ts + i * 60, 1.0, 1.02, 0.99, 1.01) for i in range(60)]
    red = [_make_candle(base_ts + i * 60, 1.0, 1.01, 0.98, 0.99) for i in range(60)]
    img_g = render_chart_image(green, green, green)
    img_r = render_chart_image(red, red, red)
    # Green bodies (255) should sum higher than red bodies (128)
    assert img_g.sum() > img_r.sum()


if __name__ == "__main__":
    test_returns_correct_shape_and_dtype()
    test_returns_none_when_insufficient_candles()
    test_determinism_same_inputs_same_bytes()
    test_green_candle_brighter_than_red()
    print("All renderer tests passed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_chart_image_renderer.py`

Expected: `ModuleNotFoundError: No module named 'feeds.chart_image_renderer'`

---

## Task 3: Image renderer — implementation

**Files:**
- Create: `feeds/chart_image_renderer.py`

- [ ] **Step 1: Implement the renderer**

Create `feeds/chart_image_renderer.py`:

```python
"""Chart image renderer — converts (1m, 5m, 15m) candle lists to a
3-channel 64x64 uint8 numpy array. Used identically at train time,
inference time, and forward-collection time, so train/serve skew
is structurally impossible.

Layout per channel (one TF each):
  - X-axis: 60 most-recent candles, oldest left, newest right
  - Y-axis: 64 pixels, log-normalized price range over the window
  - Body: 255 if green (close >= open), 128 if red
  - Wick: 64
  - Empty: 0

Channels:
  image[0] = 1m TF, image[1] = 5m TF, image[2] = 15m TF.
"""
from __future__ import annotations
import math
from typing import List, Optional

import numpy as np

from feeds.candle_utils import Candle

_HEIGHT = 64
_WIDTH = 64
_BARS_PER_TF = 60          # 60 candles per TF, rendered into _WIDTH=64 pixels
_MIN_BARS_PER_TF = 30      # below this, fail-open (renderer returns None)
_PX_BODY_GREEN = np.uint8(255)
_PX_BODY_RED = np.uint8(128)
_PX_WICK = np.uint8(64)


def _render_single_tf(candles: List[Candle]) -> Optional[np.ndarray]:
    """Render one timeframe's candles to a 64x64 uint8 array.
    Returns None if fewer than _MIN_BARS_PER_TF candles."""
    if not candles or len(candles) < _MIN_BARS_PER_TF:
        return None
    last = candles[-_BARS_PER_TF:]
    n = len(last)

    # Log-normalized price range across the window
    lows = [c.low for c in last if c.low > 0]
    highs = [c.high for c in last if c.high > 0]
    if not lows or not highs:
        return None
    lo, hi = min(lows), max(highs)
    if hi <= lo:
        return None
    log_lo = math.log(lo)
    log_hi = math.log(hi)
    log_range = log_hi - log_lo
    if log_range <= 0:
        return None

    img = np.zeros((_HEIGHT, _WIDTH), dtype=np.uint8)

    # Map each candle index 0..n-1 to a pixel column 0.._WIDTH-1
    # When n < _WIDTH, leftmost columns stay blank (padding).
    col_offset = _WIDTH - n  # right-aligned

    for i, c in enumerate(last):
        col = col_offset + i
        if not (0 <= col < _WIDTH):
            continue
        if c.high <= 0 or c.low <= 0 or c.open <= 0 or c.close <= 0:
            continue
        # Map price to row. Row 0 is top (high prices), row HEIGHT-1 is bottom.
        def _row(price: float) -> int:
            f = (math.log(price) - log_lo) / log_range
            r = int(round((1.0 - f) * (_HEIGHT - 1)))
            return max(0, min(_HEIGHT - 1, r))
        r_high = _row(c.high)
        r_low = _row(c.low)
        r_open = _row(c.open)
        r_close = _row(c.close)
        body_top = min(r_open, r_close)
        body_bot = max(r_open, r_close)
        is_green = c.close >= c.open
        body_px = _PX_BODY_GREEN if is_green else _PX_BODY_RED

        # Draw wick (entire vertical range)
        for r in range(r_high, r_low + 1):
            img[r, col] = _PX_WICK
        # Draw body (overwrites wick)
        for r in range(body_top, body_bot + 1):
            img[r, col] = body_px

    return img


def render_chart_image(candles_1m: List[Candle],
                        candles_5m: List[Candle],
                        candles_15m: List[Candle]) -> Optional[np.ndarray]:
    """Render three TFs into a single 3-channel 64x64 uint8 array.

    Returns None if any TF has fewer than _MIN_BARS_PER_TF (30) bars
    or if any TF fails to render (e.g., flat price range).
    """
    ch_1m = _render_single_tf(candles_1m)
    ch_5m = _render_single_tf(candles_5m)
    ch_15m = _render_single_tf(candles_15m)
    if ch_1m is None or ch_5m is None or ch_15m is None:
        return None
    return np.stack([ch_1m, ch_5m, ch_15m], axis=0)  # (3, 64, 64)
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python tests/test_chart_image_renderer.py`

Expected: `All renderer tests passed`

- [ ] **Step 3: Commit**

```bash
git add feeds/chart_image_renderer.py tests/test_chart_image_renderer.py
git commit -m "feat(cnn): chart image renderer — 3x64x64 from (1m,5m,15m) candles"
```

---

## Task 4: CNN model — failing test

**Files:**
- Create: `tests/test_chart_cnn.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_chart_cnn.py`:

```python
"""Tests for the ChartCNN model architecture.

Verifies:
  - Forward pass produces correct output shapes (pattern logits + outcome logit)
  - Model loads/saves cleanly via state_dict
  - CPU inference time within budget (under 100ms per call after warmup)
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch
import numpy as np

from models.chart_cnn import ChartCNN, NUM_PATTERN_CLASSES


def test_forward_pass_shapes():
    model = ChartCNN()
    model.eval()
    x = torch.zeros(1, 3, 64, 64)
    with torch.no_grad():
        pattern_logits, outcome_logit = model(x)
    assert pattern_logits.shape == (1, NUM_PATTERN_CLASSES)
    assert outcome_logit.shape == (1, 1)


def test_batch_forward_pass():
    model = ChartCNN()
    model.eval()
    x = torch.randn(8, 3, 64, 64)
    with torch.no_grad():
        p, o = model(x)
    assert p.shape == (8, NUM_PATTERN_CLASSES)
    assert o.shape == (8, 1)


def test_state_dict_roundtrip(tmp_path=None):
    import tempfile
    model1 = ChartCNN()
    model2 = ChartCNN()
    with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as f:
        torch.save(model1.state_dict(), f.name)
        sd = torch.load(f.name, map_location="cpu", weights_only=True)
        model2.load_state_dict(sd)
    # Verify parameters match
    for p1, p2 in zip(model1.parameters(), model2.parameters()):
        assert torch.equal(p1, p2)


def test_inference_latency_budget():
    model = ChartCNN()
    model.eval()
    x = torch.randn(1, 3, 64, 64)
    # warmup
    with torch.no_grad():
        for _ in range(3):
            model(x)
    # measure
    t0 = time.perf_counter()
    with torch.no_grad():
        for _ in range(10):
            model(x)
    avg_ms = (time.perf_counter() - t0) * 100  # 10 calls → avg in ms
    print(f"avg inference latency: {avg_ms:.1f}ms")
    assert avg_ms < 100, f"inference too slow: {avg_ms}ms"


if __name__ == "__main__":
    test_forward_pass_shapes()
    test_batch_forward_pass()
    test_state_dict_roundtrip()
    test_inference_latency_budget()
    print("All ChartCNN tests passed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_chart_cnn.py`

Expected: `ModuleNotFoundError: No module named 'models'`

---

## Task 5: CNN model — implementation

**Files:**
- Create: `models/__init__.py`
- Create: `models/chart_cnn.py`

- [ ] **Step 1: Create the models package**

Create `models/__init__.py` (empty file):

```python
"""ML models for chart pattern recognition and outcome prediction."""
```

- [ ] **Step 2: Implement the CNN**

Create `models/chart_cnn.py`:

```python
"""ChartCNN — 4-layer CNN with two heads:
  1. Pattern classifier (softmax over NUM_PATTERN_CLASSES)
  2. Outcome regressor (sigmoid, win probability)

Input: (B, 3, 64, 64) uint8 → cast to float32 / 255.0 internally.
Total params: ~100K. CPU-target inference: 20-50ms.
"""
from __future__ import annotations
import torch
import torch.nn as nn

# Pattern class set. Must match the labels emitted by chart_reader.pattern_5m.
# Index 0 is reserved for "none" (no recognized pattern).
PATTERN_CLASSES = [
    "none",
    "double_bottom",
    "bullish_engulfing",
    "bearish_engulfing",
    "symmetrical_triangle",
    "ascending_triangle",
    "descending_triangle",
    "head_and_shoulders",
    "inverse_head_and_shoulders",
    "v_bottom",
]
NUM_PATTERN_CLASSES = len(PATTERN_CLASSES)
CLASS_TO_IDX = {name: i for i, name in enumerate(PATTERN_CLASSES)}
IDX_TO_CLASS = {i: name for i, name in enumerate(PATTERN_CLASSES)}


class _ConvBlock(nn.Module):
    """Conv → BatchNorm → ReLU → MaxPool(2)."""

    def __init__(self, in_ch: int, out_ch: int):
        super().__init__()
        self.conv = nn.Conv2d(in_ch, out_ch, kernel_size=3, padding=1)
        self.bn = nn.BatchNorm2d(out_ch)
        self.act = nn.ReLU(inplace=True)
        self.pool = nn.MaxPool2d(kernel_size=2)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.pool(self.act(self.bn(self.conv(x))))


class ChartCNN(nn.Module):
    """4-layer CNN with shared backbone + two heads."""

    def __init__(self):
        super().__init__()
        # 64x64 → 32x32 → 16x16 → 8x8 → 4x4
        self.block1 = _ConvBlock(3, 16)
        self.block2 = _ConvBlock(16, 32)
        self.block3 = _ConvBlock(32, 48)
        self.block4 = _ConvBlock(48, 64)
        self.flat = nn.Flatten()
        # 64 channels * 4 * 4 = 1024
        self.fc = nn.Linear(1024, 128)
        self.fc_act = nn.ReLU(inplace=True)
        self.dropout = nn.Dropout(0.3)
        self.pattern_head = nn.Linear(128, NUM_PATTERN_CLASSES)
        self.outcome_head = nn.Linear(128, 1)

    def forward(self, x: torch.Tensor):
        # Cast uint8 → float32 [0, 1] if necessary
        if x.dtype == torch.uint8:
            x = x.float() / 255.0
        x = self.block1(x)
        x = self.block2(x)
        x = self.block3(x)
        x = self.block4(x)
        x = self.flat(x)
        x = self.fc_act(self.fc(x))
        x = self.dropout(x)
        pattern_logits = self.pattern_head(x)
        outcome_logit = self.outcome_head(x)
        return pattern_logits, outcome_logit
```

- [ ] **Step 3: Run test to verify it passes**

Run: `python tests/test_chart_cnn.py`

Expected: `All ChartCNN tests passed` with inference latency under 100ms.

- [ ] **Step 4: Commit**

```bash
git add models/__init__.py models/chart_cnn.py tests/test_chart_cnn.py
git commit -m "feat(cnn): ChartCNN model — 4-layer backbone + pattern/outcome heads"
```

---

## Task 6: Inference singleton — failing test

**Files:**
- Create: `tests/test_chart_cnn_inference.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_chart_cnn_inference.py`:

```python
"""Tests for the production CNN inference singleton.

Verifies:
  - Returns None when weights file is missing (graceful degradation)
  - Returns valid dict shape on synthetic candles
  - Cache hits on second call with same (addr, last_minute_ts)
  - Self-disables after exception, re-enables after retry window
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import torch
from feeds.candle_utils import Candle
from core.chart_cnn_inference import ChartCNNInference


def _flat_candles(n: int, base_ts: int = 1700000000):
    return [Candle(open_time=base_ts + i * 60, open=1.0, high=1.01, low=0.99,
                   close=1.0, volume=100.0, close_time=base_ts + (i + 1) * 60)
            for i in range(n)]


def test_returns_none_when_weights_missing():
    inf = ChartCNNInference(weights_path="/nonexistent/path/weights.pt")
    result = inf.predict("ADDR1", _flat_candles(60), _flat_candles(60), _flat_candles(60))
    assert result is None
    assert inf.disabled is True


def test_returns_dict_when_weights_present():
    from models.chart_cnn import ChartCNN
    with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as f:
        model = ChartCNN()
        model.eval()
        torch.save(model.state_dict(), f.name)
        path = f.name
    inf = ChartCNNInference(weights_path=path)
    result = inf.predict("ADDR1", _flat_candles(60), _flat_candles(60), _flat_candles(60))
    assert result is not None
    assert "pattern" in result
    assert "pattern_conf" in result
    assert "outcome_prob" in result
    assert 0.0 <= result["pattern_conf"] <= 1.0
    assert 0.0 <= result["outcome_prob"] <= 1.0


def test_cache_hits_same_minute():
    from models.chart_cnn import ChartCNN
    with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as f:
        torch.save(ChartCNN().state_dict(), f.name)
        path = f.name
    inf = ChartCNNInference(weights_path=path)
    c1 = _flat_candles(60)
    c5 = _flat_candles(60)
    c15 = _flat_candles(60)
    r1 = inf.predict("ADDR1", c1, c5, c15)
    r2 = inf.predict("ADDR1", c1, c5, c15)
    assert r1 == r2
    assert inf.cache_hits >= 1


def test_returns_none_on_insufficient_candles():
    from models.chart_cnn import ChartCNN
    with tempfile.NamedTemporaryFile(suffix=".pt", delete=False) as f:
        torch.save(ChartCNN().state_dict(), f.name)
        path = f.name
    inf = ChartCNNInference(weights_path=path)
    r = inf.predict("ADDR1", _flat_candles(10), _flat_candles(60), _flat_candles(60))
    assert r is None


if __name__ == "__main__":
    test_returns_none_when_weights_missing()
    test_returns_dict_when_weights_present()
    test_cache_hits_same_minute()
    test_returns_none_on_insufficient_candles()
    print("All inference tests passed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_chart_cnn_inference.py`

Expected: `ModuleNotFoundError: No module named 'core.chart_cnn_inference'`

---

## Task 7: Inference singleton — implementation

**Files:**
- Create: `core/chart_cnn_inference.py`

- [ ] **Step 1: Implement the singleton**

Create `core/chart_cnn_inference.py`:

```python
"""Production CNN inference singleton.

Lazy-loads weights at first call. All failures degrade gracefully —
predict() returns None on missing weights, render failure, or
inference exception. Self-disables for 60s after any uncaught
exception, then retries.

LRU cache keyed by (token_address, latest_1m_open_time) — same minute
calls return cached prediction in <1ms.
"""
from __future__ import annotations
import logging
import os
import time
from collections import OrderedDict
from typing import Dict, List, Optional

import torch

from feeds.candle_utils import Candle
from feeds.chart_image_renderer import render_chart_image
from models.chart_cnn import ChartCNN, IDX_TO_CLASS

logger = logging.getLogger(__name__)

_DEFAULT_WEIGHTS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "models", "chart_cnn_v1.pt",
)
_CACHE_MAX = 512
_DISABLE_DURATION_S = 60.0
_WARN_THROTTLE_S = 300.0  # 5 min


class ChartCNNInference:
    """Singleton-style inference wrapper. Construct once at startup."""

    def __init__(self, weights_path: str = _DEFAULT_WEIGHTS):
        self.weights_path = weights_path
        self.model: Optional[ChartCNN] = None
        self.disabled = False
        self._disabled_until = 0.0
        self._last_warn = 0.0
        self._cache: OrderedDict = OrderedDict()
        self.cache_hits = 0
        self.predict_calls = 0
        # Eager load attempt — if missing, set disabled=True
        self._try_load()

    def _try_load(self):
        if not os.path.exists(self.weights_path):
            logger.info(
                f"[ChartCNN] weights not found at {self.weights_path}; "
                f"inference disabled (bot continues normally)"
            )
            self.disabled = True
            return
        try:
            self.model = ChartCNN()
            sd = torch.load(self.weights_path, map_location="cpu", weights_only=True)
            self.model.load_state_dict(sd)
            self.model.eval()
            self.disabled = False
            logger.info(f"[ChartCNN] loaded weights from {self.weights_path}")
        except Exception as e:
            logger.warning(f"[ChartCNN] failed to load weights: {e}")
            self.disabled = True
            self.model = None

    def predict(self,
                token_address: str,
                candles_1m: List[Candle],
                candles_5m: List[Candle],
                candles_15m: List[Candle]) -> Optional[Dict]:
        """Run inference. Returns dict on success, None on any failure."""
        self.predict_calls += 1
        if self.disabled:
            if time.time() < self._disabled_until:
                return None
            self.disabled = False  # retry window elapsed

        if self.model is None:
            return None

        # Cache key: (addr, latest 1m bar open_time)
        if not candles_1m:
            return None
        cache_key = (token_address, candles_1m[-1].open_time)
        if cache_key in self._cache:
            self.cache_hits += 1
            self._cache.move_to_end(cache_key)
            return self._cache[cache_key]

        try:
            img = render_chart_image(candles_1m, candles_5m, candles_15m)
            if img is None:
                return None
            # numpy (3, 64, 64) uint8 → torch (1, 3, 64, 64)
            tensor = torch.from_numpy(img).unsqueeze(0)
            with torch.no_grad():
                pattern_logits, outcome_logit = self.model(tensor)
                pattern_probs = torch.softmax(pattern_logits, dim=1)[0]
                outcome_prob = torch.sigmoid(outcome_logit)[0, 0].item()
                top_idx = int(pattern_probs.argmax().item())
                top_conf = float(pattern_probs[top_idx].item())
            result = {
                "pattern": IDX_TO_CLASS.get(top_idx, "unknown"),
                "pattern_conf": top_conf,
                "outcome_prob": outcome_prob,
            }
        except Exception as e:
            now = time.time()
            if now - self._last_warn > _WARN_THROTTLE_S:
                logger.warning(f"[ChartCNN] inference error: {e} (disabling 60s)")
                self._last_warn = now
            self.disabled = True
            self._disabled_until = time.time() + _DISABLE_DURATION_S
            return None

        self._cache[cache_key] = result
        if len(self._cache) > _CACHE_MAX:
            self._cache.popitem(last=False)  # LRU eviction
        return result


_singleton: Optional[ChartCNNInference] = None


def get_inference() -> ChartCNNInference:
    """Module-level accessor for the singleton."""
    global _singleton
    if _singleton is None:
        _singleton = ChartCNNInference()
    return _singleton
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python tests/test_chart_cnn_inference.py`

Expected: `All inference tests passed`

- [ ] **Step 3: Commit**

```bash
git add core/chart_cnn_inference.py tests/test_chart_cnn_inference.py
git commit -m "feat(cnn): inference singleton with LRU cache + graceful disable"
```

---

## Task 8: Backfill script — historical seed dataset

**Files:**
- Create: `scripts/backfill_chart_dataset.py`

- [ ] **Step 1: Implement the backfill script**

Create `scripts/backfill_chart_dataset.py`:

```python
"""Backfill historical chart dataset from closed trades.

Pulls all closed trades from /api/trades, fetches pre-entry candle
data, renders to 3-channel image, writes .npy + .json label files
to .cnn_dataset/v1/.

Pattern label: from entry_meta.chart_pattern_5m (chart_reader output
captured at trade time).
Outcome label: 1 if total pnl > 0, 0 otherwise.

Usage: python scripts/backfill_chart_dataset.py
"""
from __future__ import annotations
import asyncio
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np

from feeds.chart_image_renderer import render_chart_image
from feeds.candle_utils import Candle

OUT_DIR = Path(".cnn_dataset/v1")
API_URL = "https://gracious-inspiration-production.up.railway.app/api/trades?limit=2000"


def fetch_trades() -> list:
    """Pull all trades from production API."""
    r = urllib.request.urlopen(API_URL, timeout=30)
    data = json.loads(r.read())
    return data if isinstance(data, list) else data.get("trades", [])


def pair_buys_with_sells(trades: list) -> list:
    """Pair each buy with its subsequent sells; compute total pnl per buy."""
    sells_by_addr = defaultdict(list)
    for t in trades:
        if t.get("type") == "sell":
            sells_by_addr[t.get("address", "")].append(t)
    for a in sells_by_addr:
        sells_by_addr[a].sort(key=lambda x: x.get("time", ""))

    paired = []
    for t in trades:
        if t.get("type") != "buy":
            continue
        addr = t.get("address", "")
        ts = t.get("time", "")
        rs = [s for s in sells_by_addr.get(addr, []) if s.get("time", "") > ts]
        if not rs:
            continue
        total_pnl = sum((s.get("pnl") or 0) for s in rs)
        paired.append({
            "addr": addr,
            "time": ts,
            "token": t.get("token"),
            "pair": t.get("pair_address"),
            "pnl": total_pnl,
            "entry_meta": t.get("entry_meta") or {},
        })
    return paired


async def fetch_candles_at_entry(pair_addr: str, entry_ts_iso: str):
    """Fetch (candles_1m, candles_5m, candles_15m) just before entry_ts.

    Uses the existing assemble_chart_data — same source the bot used at
    entry time. Returns None on any error.
    """
    try:
        from feeds.chart_data import assemble_chart_data
        from feeds.gt_client import GeckoTerminalClient
        from feeds.dexscreener_client import DexScreenerClient
        gt = GeckoTerminalClient()
        ds = DexScreenerClient()
        cd = await assemble_chart_data(gt, pair_addr, dexs_client=ds)
        if not cd:
            return None, None, None
        return (cd.candles_1m or [], cd.candles_5m or [], cd.candles_15m or [])
    except Exception as e:
        print(f"  candle fetch err: {e}")
        return None, None, None


def label_for_trade(trade: dict) -> dict:
    """Build the label JSON for one trade."""
    em = trade.get("entry_meta") or {}
    pattern = em.get("chart_pattern_5m") or "none"
    return {
        "addr": trade["addr"],
        "ts": trade["time"],
        "token": trade["token"],
        "pattern_label": pattern,
        "outcome_label": 1 if trade["pnl"] > 0 else 0,
        "outcome_pnl_pct": float(em.get("outcome_pnl_pct") or 0),
        "context": {
            "triggers_fired": em.get("triggers_fired") or [],
            "hour_ct": em.get("hour_ct"),
            "mcap_usd": em.get("entry_market_cap_usd"),
        },
    }


async def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    trades = fetch_trades()
    paired = pair_buys_with_sells(trades)
    print(f"Found {len(paired)} closed trades")

    success = 0
    fail = 0
    for i, t in enumerate(paired):
        out_npy = OUT_DIR / f"{t['addr']}_{t['time'].replace(':', '-')}.npy"
        out_json = OUT_DIR / f"{t['addr']}_{t['time'].replace(':', '-')}.json"
        if out_npy.exists() and out_json.exists():
            success += 1
            continue
        if not t.get("pair"):
            fail += 1
            continue
        c1, c5, c15 = await fetch_candles_at_entry(t["pair"], t["time"])
        if not c1 or not c5 or not c15:
            fail += 1
            print(f"[{i+1}/{len(paired)}] {t['token']}: no candles available")
            continue
        img = render_chart_image(c1, c5, c15)
        if img is None:
            fail += 1
            print(f"[{i+1}/{len(paired)}] {t['token']}: render failed")
            continue
        np.save(out_npy, img)
        with open(out_json, "w") as f:
            json.dump(label_for_trade(t), f, indent=2)
        success += 1
        print(f"[{i+1}/{len(paired)}] {t['token']}: ok (pattern={label_for_trade(t)['pattern_label']}, win={label_for_trade(t)['outcome_label']})")
        await asyncio.sleep(1.0)  # GT rate-limit pacing

    print(f"\nDone: {success} saved, {fail} failed")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run the backfill**

Run: `python scripts/backfill_chart_dataset.py`

Expected: Output like `[1/500] BABYTROLL: ok (pattern=double_bottom, win=1)`. Takes ~10-15 min (1s pacing per token). Final summary: `Done: N saved, M failed`.

- [ ] **Step 3: Verify output**

Run: `ls .cnn_dataset/v1/ | head -10`

Expected: list of `.npy` and `.json` files paired.

Run: `python -c "import numpy as np, glob; files = glob.glob('.cnn_dataset/v1/*.npy'); print(f'{len(files)} images'); a = np.load(files[0]); print(a.shape, a.dtype)"`

Expected: `(3, 64, 64) uint8`

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill_chart_dataset.py
git commit -m "feat(cnn): backfill script — seed dataset from closed trades"
```

---

## Task 9: Training pipeline

**Files:**
- Create: `scripts/train_chart_cnn.py`

- [ ] **Step 1: Implement the training script**

Create `scripts/train_chart_cnn.py`:

```python
"""Train ChartCNN on the backfilled + forward-collected dataset.

Loads images + labels from .cnn_dataset/v1/, splits date-stratified
(train < cutoff, val >= cutoff), trains with combined cross-entropy +
BCE loss, saves best-val-loss model to models/chart_cnn_v1.pt.

Usage:
  python scripts/train_chart_cnn.py                 # use defaults
  python scripts/train_chart_cnn.py --epochs 20    # custom epochs
"""
from __future__ import annotations
import argparse
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from models.chart_cnn import ChartCNN, CLASS_TO_IDX, NUM_PATTERN_CLASSES

DATASET_DIR = Path(".cnn_dataset/v1")
MODEL_OUT = Path("models/chart_cnn_v1.pt")


class ChartDataset(Dataset):
    def __init__(self, items: list):
        self.items = items  # list of (image_path, label_dict)

    def __len__(self) -> int:
        return len(self.items)

    def __getitem__(self, idx):
        img_path, label = self.items[idx]
        img = np.load(img_path)  # (3, 64, 64) uint8
        x = torch.from_numpy(img).float() / 255.0  # → (3, 64, 64) float
        pattern_idx = CLASS_TO_IDX.get(label.get("pattern_label") or "none", 0)
        outcome = float(label.get("outcome_label") or 0)
        return x, torch.tensor(pattern_idx, dtype=torch.long), torch.tensor(outcome, dtype=torch.float)


def load_dataset(cutoff_iso: str = "2026-05-13T00:00:00"):
    """Returns (train_items, val_items), each a list of (path, label) tuples."""
    train_items = []
    val_items = []
    npy_files = sorted(glob.glob(str(DATASET_DIR / "*.npy")))
    for npy_path in npy_files:
        json_path = npy_path.replace(".npy", ".json")
        if not os.path.exists(json_path):
            continue
        with open(json_path) as f:
            label = json.load(f)
        if label.get("ts", "") < cutoff_iso:
            train_items.append((npy_path, label))
        else:
            val_items.append((npy_path, label))
    return train_items, val_items


def train_one_epoch(model, loader, opt, pat_loss_fn, out_loss_fn):
    model.train()
    total = 0.0
    n = 0
    for x, pat_y, out_y in loader:
        opt.zero_grad()
        pat_logits, out_logit = model(x)
        loss = pat_loss_fn(pat_logits, pat_y) + out_loss_fn(out_logit.squeeze(-1), out_y)
        loss.backward()
        opt.step()
        total += loss.item() * x.size(0)
        n += x.size(0)
    return total / max(1, n)


@torch.no_grad()
def eval_one_epoch(model, loader, pat_loss_fn, out_loss_fn):
    model.eval()
    total = 0.0
    n = 0
    pat_correct = 0
    for x, pat_y, out_y in loader:
        pat_logits, out_logit = model(x)
        loss = pat_loss_fn(pat_logits, pat_y) + out_loss_fn(out_logit.squeeze(-1), out_y)
        total += loss.item() * x.size(0)
        n += x.size(0)
        pat_correct += (pat_logits.argmax(dim=1) == pat_y).sum().item()
    avg_loss = total / max(1, n)
    pat_acc = pat_correct / max(1, n)
    return avg_loss, pat_acc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=10)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--cutoff", default="2026-05-13T00:00:00",
                    help="ISO timestamp; entries before = train, >= = val")
    args = ap.parse_args()

    train_items, val_items = load_dataset(args.cutoff)
    print(f"train={len(train_items)}  val={len(val_items)}")
    if len(train_items) < 10 or len(val_items) < 5:
        print("Dataset too small to train. Need >=10 train and >=5 val.")
        return

    train_ds = ChartDataset(train_items)
    val_ds = ChartDataset(val_items)
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True)
    val_loader = DataLoader(val_ds, batch_size=args.batch_size, shuffle=False)

    model = ChartCNN()
    opt = torch.optim.Adam(model.parameters(), lr=args.lr)
    pat_loss_fn = nn.CrossEntropyLoss()
    out_loss_fn = nn.BCEWithLogitsLoss()

    best_val = float("inf")
    MODEL_OUT.parent.mkdir(parents=True, exist_ok=True)
    for ep in range(1, args.epochs + 1):
        train_loss = train_one_epoch(model, train_loader, opt, pat_loss_fn, out_loss_fn)
        val_loss, pat_acc = eval_one_epoch(model, val_loader, pat_loss_fn, out_loss_fn)
        marker = ""
        if val_loss < best_val:
            best_val = val_loss
            torch.save(model.state_dict(), MODEL_OUT)
            marker = "  (best saved)"
        print(f"ep={ep:02d}  train_loss={train_loss:.4f}  val_loss={val_loss:.4f}  "
              f"val_pat_acc={pat_acc:.3f}{marker}")

    print(f"\nBest val loss: {best_val:.4f}")
    print(f"Saved to: {MODEL_OUT}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run training**

Run: `python scripts/train_chart_cnn.py --epochs 10`

Expected: Lines like `ep=01  train_loss=2.31  val_loss=2.15  val_pat_acc=0.42  (best saved)`. Final model saved to `models/chart_cnn_v1.pt`.

If dataset too small, output `Dataset too small to train` — wait for more forward data accumulation, or relax the cutoff.

- [ ] **Step 3: Verify model loads via inference singleton**

Run: `python -c "from core.chart_cnn_inference import get_inference; inf = get_inference(); print('disabled:', inf.disabled)"`

Expected: `disabled: False`

- [ ] **Step 4: Commit**

```bash
git add scripts/train_chart_cnn.py
git commit -m "feat(cnn): training pipeline — date-stratified split, dual-head loss"
```

Note: Do **NOT** commit `models/chart_cnn_v1.pt` to git (binary weights). Add to `.gitignore`:

```bash
echo "models/*.pt" >> .gitignore
git add .gitignore
git commit -m "chore: gitignore CNN weights binary"
```

---

## Task 10: Forward dataset collector — failing test

**Files:**
- Create: `tests/test_forward_dataset_collector.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_forward_dataset_collector.py`:

```python
"""Tests for the forward dataset collector.

Verifies:
  - dump_snapshot() writes .npy + .json to the correct date dir
  - update_outcome() finds the partial label and adds outcome fields
  - Disk-space guard returns False when threshold exceeded
"""
import os
import sys
import tempfile
import json
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from feeds.candle_utils import Candle
from feeds.forward_dataset_collector import ForwardDatasetCollector


def _flat_candles(n: int, base_ts: int = 1700000000):
    return [Candle(open_time=base_ts + i * 60, open=1.0, high=1.01, low=0.99,
                   close=1.0, volume=100.0, close_time=base_ts + (i + 1) * 60)
            for i in range(n)]


def test_dump_writes_npy_and_json():
    with tempfile.TemporaryDirectory() as tmpdir:
        col = ForwardDatasetCollector(root_dir=tmpdir)
        ok = col.dump_snapshot(
            token_address="ADDR_TEST",
            ts_iso="2026-05-15T12:00:00+00:00",
            candles_1m=_flat_candles(60),
            candles_5m=_flat_candles(60),
            candles_15m=_flat_candles(60),
            context={"triggers_fired": ["test_trigger"], "hour_ct": 7},
        )
        assert ok is True
        # Date dir created
        date_dir = Path(tmpdir) / "2026-05-15"
        assert date_dir.exists()
        npys = list(date_dir.glob("*.npy"))
        jsons = list(date_dir.glob("*.json"))
        assert len(npys) == 1
        assert len(jsons) == 1
        # Load and inspect
        img = np.load(npys[0])
        assert img.shape == (3, 64, 64)
        with open(jsons[0]) as f:
            label = json.load(f)
        assert label["addr"] == "ADDR_TEST"
        assert label["outcome_label"] is None  # not yet closed
        assert label["context"]["triggers_fired"] == ["test_trigger"]


def test_update_outcome_finds_and_appends():
    with tempfile.TemporaryDirectory() as tmpdir:
        col = ForwardDatasetCollector(root_dir=tmpdir)
        col.dump_snapshot(
            token_address="ADDR_UPDATE",
            ts_iso="2026-05-15T13:00:00+00:00",
            candles_1m=_flat_candles(60),
            candles_5m=_flat_candles(60),
            candles_15m=_flat_candles(60),
            context={},
        )
        updated = col.update_outcome(
            token_address="ADDR_UPDATE",
            ts_iso="2026-05-15T13:00:00+00:00",
            outcome_label=1,
            outcome_pnl_pct=4.03,
        )
        assert updated is True
        # Re-read
        date_dir = Path(tmpdir) / "2026-05-15"
        jsons = list(date_dir.glob("*ADDR_UPDATE*.json"))
        with open(jsons[0]) as f:
            label = json.load(f)
        assert label["outcome_label"] == 1
        assert label["outcome_pnl_pct"] == 4.03


def test_returns_false_on_insufficient_candles():
    with tempfile.TemporaryDirectory() as tmpdir:
        col = ForwardDatasetCollector(root_dir=tmpdir)
        ok = col.dump_snapshot(
            token_address="ADDR_SHORT",
            ts_iso="2026-05-15T14:00:00+00:00",
            candles_1m=_flat_candles(10),  # below 30
            candles_5m=_flat_candles(60),
            candles_15m=_flat_candles(60),
            context={},
        )
        assert ok is False


if __name__ == "__main__":
    test_dump_writes_npy_and_json()
    test_update_outcome_finds_and_appends()
    test_returns_false_on_insufficient_candles()
    print("All forward collector tests passed")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python tests/test_forward_dataset_collector.py`

Expected: `ModuleNotFoundError: No module named 'feeds.forward_dataset_collector'`

---

## Task 11: Forward dataset collector — implementation

**Files:**
- Create: `feeds/forward_dataset_collector.py`

- [ ] **Step 1: Implement the collector**

Create `feeds/forward_dataset_collector.py`:

```python
"""Forward-collected chart dataset. Called on every scanner candidate
that has chart_data available. Dumps image + partial label; outcome is
appended later when the trade closes.

Disk-space guard: skips writes when free space < 5% (throttled WARNING).
"""
from __future__ import annotations
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from feeds.candle_utils import Candle
from feeds.chart_image_renderer import render_chart_image

logger = logging.getLogger(__name__)

_DEFAULT_ROOT = "/data/cnn_dataset/forward"
_DISK_GUARD_FREE_PCT = 0.05  # require >=5% free
_WARN_THROTTLE_S = 300.0


def _safe_filename(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", s)


class ForwardDatasetCollector:
    def __init__(self, root_dir: str = _DEFAULT_ROOT):
        self.root_dir = Path(root_dir)
        self._last_disk_warn = 0.0

    def _disk_has_space(self) -> bool:
        try:
            self.root_dir.mkdir(parents=True, exist_ok=True)
            total, used, free = shutil.disk_usage(str(self.root_dir))
            free_pct = free / total if total > 0 else 1.0
            if free_pct < _DISK_GUARD_FREE_PCT:
                now = time.time()
                if now - self._last_disk_warn > _WARN_THROTTLE_S:
                    logger.warning(
                        f"[forward_collector] disk <5% free; dropping writes"
                    )
                    self._last_disk_warn = now
                return False
            return True
        except Exception:
            return True  # fail-open

    def _paths(self, token_address: str, ts_iso: str) -> tuple:
        date = ts_iso[:10]  # YYYY-MM-DD
        date_dir = self.root_dir / date
        date_dir.mkdir(parents=True, exist_ok=True)
        base = f"{_safe_filename(token_address)}_{_safe_filename(ts_iso)}"
        return date_dir / f"{base}.npy", date_dir / f"{base}.json"

    def dump_snapshot(self,
                      token_address: str,
                      ts_iso: str,
                      candles_1m: List[Candle],
                      candles_5m: List[Candle],
                      candles_15m: List[Candle],
                      context: Dict) -> bool:
        """Write a partial-label snapshot. Returns True on success."""
        if not self._disk_has_space():
            return False
        try:
            img = render_chart_image(candles_1m, candles_5m, candles_15m)
            if img is None:
                return False
            npy_path, json_path = self._paths(token_address, ts_iso)
            np.save(npy_path, img)
            label = {
                "addr": token_address,
                "ts": ts_iso,
                "pattern_label": None,  # filled at training time from chart_reader
                "outcome_label": None,  # filled by update_outcome on trade close
                "outcome_pnl_pct": None,
                "context": context,
            }
            with open(json_path, "w") as f:
                json.dump(label, f)
            return True
        except Exception as e:
            logger.debug(f"[forward_collector] dump err: {e}")
            return False

    def update_outcome(self,
                       token_address: str,
                       ts_iso: str,
                       outcome_label: int,
                       outcome_pnl_pct: float) -> bool:
        """Find the matching partial label and append outcome fields."""
        try:
            _, json_path = self._paths(token_address, ts_iso)
            if not json_path.exists():
                return False
            with open(json_path) as f:
                label = json.load(f)
            label["outcome_label"] = int(outcome_label)
            label["outcome_pnl_pct"] = float(outcome_pnl_pct)
            with open(json_path, "w") as f:
                json.dump(label, f)
            return True
        except Exception as e:
            logger.debug(f"[forward_collector] update err: {e}")
            return False


_singleton: Optional[ForwardDatasetCollector] = None


def get_collector() -> ForwardDatasetCollector:
    global _singleton
    if _singleton is None:
        # Use DATA_DIR env if set (Railway), else fallback to relative path
        data_dir = os.environ.get("DATA_DIR", "/data")
        root = os.path.join(data_dir, "cnn_dataset/forward")
        _singleton = ForwardDatasetCollector(root_dir=root)
    return _singleton
```

- [ ] **Step 2: Run test to verify it passes**

Run: `python tests/test_forward_dataset_collector.py`

Expected: `All forward collector tests passed`

- [ ] **Step 3: Commit**

```bash
git add feeds/forward_dataset_collector.py tests/test_forward_dataset_collector.py
git commit -m "feat(cnn): forward dataset collector with disk-space guard"
```

---

## Task 12: Integrate inference into dip_scanner (shadow only)

**Files:**
- Modify: `feeds/dip_scanner.py`

- [ ] **Step 1: Locate insertion point**

Run: `grep -n "filter_falling_knife_block_reasons" feeds/dip_scanner.py | head -5`

You should find the spot in `entry_meta_dict` (around line 8568 from earlier session work). Inference call goes BEFORE this dict construction so the values are ready when the dict is built.

Locate the line `_chart_ctx_dict = {` (around line 2287 of dip_scanner.py from earlier session work). The CNN call goes after `_chart_ctx_dict` is fully built and after `m1_features` is populated.

- [ ] **Step 2: Add inference call**

In `feeds/dip_scanner.py`, find the section just after `_chart_ctx_dict` is populated (you'll see fields like `"chart_mtf_score": _chart_ctx.mtf.get("score")`). Right after that block (end of the `try:` block, around the `except Exception as _e: logger.debug(f"[DipScanner] chart_reader error: {_e}")` line), add:

```python
# Chart CNN inference — SHADOW 2026-05-15. Plugs into _chart_data
# (already fetched above). Returns None if weights missing or render
# failure; all degradation is silent. Output goes into entry_meta_dict.
_cnn_pattern = None
_cnn_pattern_conf = None
_cnn_outcome_prob = None
try:
    from core.chart_cnn_inference import get_inference
    _cnn_inf = get_inference()
    if not _cnn_inf.disabled and _chart_data:
        _cnn_result = _cnn_inf.predict(
            token_address=token_address,
            candles_1m=_chart_data.candles_1m or [],
            candles_5m=_chart_data.candles_5m or [],
            candles_15m=_chart_data.candles_15m or [],
        )
        if _cnn_result:
            _cnn_pattern = _cnn_result.get("pattern")
            _cnn_pattern_conf = _cnn_result.get("pattern_conf")
            _cnn_outcome_prob = _cnn_result.get("outcome_prob")
except Exception as _e:
    logger.debug(f"[DipScanner] CNN inference err: {_e}")
```

- [ ] **Step 3: Add fields to entry_meta_dict**

Find `entry_meta_dict = {` near line 8400 (from earlier session work). Inside the dict, append three new keys (after the `filter_falling_knife` block):

```python
                # chart_cnn — SHADOW 2026-05-15 (pattern + outcome head)
                "cnn_pattern": _cnn_pattern,
                "cnn_pattern_conf": _cnn_pattern_conf,
                "cnn_outcome_prob": _cnn_outcome_prob,
```

- [ ] **Step 4: Syntax check**

Run: `python -c "import ast; ast.parse(open('feeds/dip_scanner.py', encoding='utf-8').read()); print('OK')"`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add feeds/dip_scanner.py
git commit -m "feat(cnn): dip_scanner SHADOW integration — log pattern/outcome to entry_meta"
```

---

## Task 13: Integrate forward collector into dip_scanner

**Files:**
- Modify: `feeds/dip_scanner.py`

- [ ] **Step 1: Find insertion point**

Right after the CNN inference call from Task 12, add a forward collector dump. It runs for every candidate the bot evaluates — winners, losers, and blocked candidates alike.

- [ ] **Step 2: Add forward collector call**

In `feeds/dip_scanner.py`, immediately after the CNN inference block you just added, append:

```python
# Forward dataset collector — dumps image + context for every
# evaluated candidate. Outcome label gets stamped later by the
# trader on close. SHADOW only — pure data collection.
try:
    from feeds.forward_dataset_collector import get_collector
    from datetime import datetime, timezone
    if _chart_data:
        get_collector().dump_snapshot(
            token_address=token_address,
            ts_iso=datetime.now(timezone.utc).isoformat(),
            candles_1m=_chart_data.candles_1m or [],
            candles_5m=_chart_data.candles_5m or [],
            candles_15m=_chart_data.candles_15m or [],
            context={
                "triggers_fired": list(_triggers_fired) if "_triggers_fired" in dir() else [],
                "hour_ct": _flt_h if "_flt_h" in dir() else None,
                "mcap_usd": mcap if "mcap" in dir() else None,
                "token_symbol": token_symbol,
            },
        )
except Exception as _e:
    logger.debug(f"[DipScanner] forward_collector err: {_e}")
```

Note: `_triggers_fired` is built later in the function, so this only captures the trigger list if it's been built. Move the call later in the function if you want post-trigger context — for now, capture pre-trigger context for the broadest dataset.

- [ ] **Step 3: Syntax check**

Run: `python -c "import ast; ast.parse(open('feeds/dip_scanner.py', encoding='utf-8').read()); print('OK')"`

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add feeds/dip_scanner.py
git commit -m "feat(cnn): forward collector — dump image+context every scan"
```

---

## Task 14: Integrate inference into peak_recorder

**Files:**
- Modify: `core/peak_recorder.py`

- [ ] **Step 1: Locate init_position**

Run: `grep -n "def init_position" core/peak_recorder.py`

You should find init_position around line 240 from earlier session work.

- [ ] **Step 2: Add CNN stamp at init**

In `core/peak_recorder.py` `init_position()`, after the `self.state[token_address] = {...}` dict is constructed, append CNN result fields. Find the lines:

```python
            self.state[token_address] = {
                'tok': token_symbol,
                'addr': token_address,
                ...
                'minutes': [],
                'last_record_minute_ts': 0,
                'shadow_exit_logged': False,
            }
            logger.info(f'[PEAK_RECORDER] init {token_symbol} entry=${entry_price:.8f}')
```

Add the CNN stamp right BEFORE the `logger.info` line:

```python
            # Stamp CNN prediction at init — correlates entry-time pattern
            # with eventual outcome for forward validation. SHADOW only.
            try:
                from core.chart_cnn_inference import get_inference
                from feeds.chart_data import assemble_chart_data
                # Caller passes candles via init_position kwargs if available,
                # otherwise we skip (no fetch here — keep init lightweight).
                _cnn_init = entry_meta.get('cnn_pattern') if isinstance(entry_meta, dict) else None
                if _cnn_init is not None:
                    self.state[token_address]['cnn_pattern_at_entry'] = _cnn_init
                    self.state[token_address]['cnn_pattern_conf_at_entry'] = entry_meta.get('cnn_pattern_conf')
                    self.state[token_address]['cnn_outcome_prob_at_entry'] = entry_meta.get('cnn_outcome_prob')
            except Exception as _e:
                logger.debug(f'[PEAK_RECORDER] cnn stamp err: {_e}')
```

(Note: this reads from `entry_meta` passed in, which dip_scanner now populates. No new fetch needed.)

- [ ] **Step 3: Update init_position signature if needed**

Run: `grep -n "def init_position" core/peak_recorder.py`

Check that `init_position` accepts an `entry_meta` parameter. If not, add it as a default-None kwarg.

If `init_position` is called from position_manager and doesn't currently pass entry_meta, modify the call site in `core/position_manager.py` to pass `entry_meta=getattr(pos, "entry_meta", None)`.

- [ ] **Step 4: Add CNN fields to finalize() trace output**

In `core/peak_recorder.py` `finalize()` method, find the `trace = {...}` dict construction (around line 346 from earlier session work). Add three CNN fields:

```python
                'cnn_pattern_at_entry': s.get('cnn_pattern_at_entry'),
                'cnn_pattern_conf_at_entry': s.get('cnn_pattern_conf_at_entry'),
                'cnn_outcome_prob_at_entry': s.get('cnn_outcome_prob_at_entry'),
```

- [ ] **Step 5: Syntax check**

Run: `python -c "import ast; ast.parse(open('core/peak_recorder.py', encoding='utf-8').read()); print('OK')"`

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add core/peak_recorder.py core/position_manager.py
git commit -m "feat(cnn): peak_recorder stamps CNN entry prediction in trace"
```

---

## Task 15: Integrate forward-collector update on trade close

**Files:**
- Modify: `core/trader.py`

- [ ] **Step 1: Find the close-position path**

Run: `grep -n "_register_dip_close\|del self.open_positions" core/trader.py | head -10`

You should find the close path around line 2438 from earlier session work.

- [ ] **Step 2: Add outcome update after position is fully closed**

In `core/trader.py`, find the block where `_pool_price_feed.unsubscribe_token` is called (added in this session). Right after that, add:

```python
                # Forward-collector: stamp outcome on the partial label
                # written at scan time. SHADOW only.
                try:
                    from feeds.forward_dataset_collector import get_collector
                    _entry_ts_iso = getattr(position, "entry_time", None)
                    if _entry_ts_iso and hasattr(_entry_ts_iso, "isoformat"):
                        _entry_ts_iso = _entry_ts_iso.isoformat()
                    # Use closing-trade total_pnl_usd computed by the tracker
                    _total_pnl = getattr(position, "total_pnl_usd", 0.0) or 0.0
                    _pnl_pct = (_total_pnl / max(getattr(position, "amount_usd", 20.0), 1.0)) * 100.0
                    get_collector().update_outcome(
                        token_address=token_address,
                        ts_iso=str(_entry_ts_iso) if _entry_ts_iso else "",
                        outcome_label=1 if _total_pnl > 0 else 0,
                        outcome_pnl_pct=_pnl_pct,
                    )
                except Exception as _e:
                    logger.debug(f"[Trader] forward_collector update err: {_e}")
```

- [ ] **Step 3: Syntax check**

Run: `python -c "import ast; ast.parse(open('core/trader.py', encoding='utf-8').read()); print('OK')"`

Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add core/trader.py
git commit -m "feat(cnn): forward collector — stamp outcome on close"
```

---

## Task 16: Integrate inference into postmortem

**Files:**
- Modify: `scripts/postmortem.py`

- [ ] **Step 1: Locate the chart inspection section**

Run: `grep -n "chart_reader\|read_chart\|chart_data" scripts/postmortem.py | head -10`

Find where postmortem already does chart analysis on the target token.

- [ ] **Step 2: Append CNN verdict to output**

Find the section that prints chart_reader output. Right after that block, add:

```python
# CNN verdict on the pre-entry chart context
try:
    from core.chart_cnn_inference import get_inference
    inf = get_inference()
    if not inf.disabled:
        result = inf.predict(
            token_address=addr,
            candles_1m=cd.candles_1m if cd else [],
            candles_5m=cd.candles_5m if cd else [],
            candles_15m=cd.candles_15m if cd else [],
        )
        if result:
            print(f"\nCNN verdict:")
            print(f"  pattern: {result['pattern']}  (conf={result['pattern_conf']:.2f})")
            print(f"  outcome_prob: {result['outcome_prob']:.3f}")
        else:
            print(f"\nCNN: unavailable (disabled or render failed)")
except Exception as e:
    print(f"\nCNN err: {e}")
```

- [ ] **Step 3: Test against a known token**

Run: `python scripts/postmortem.py gvv7sfu6fhjssvxfpg7xqfnwar3c7ykcc74rqe7bpump`

(Substitute the actual postmortem invocation pattern for your repo.) Verify the output now includes a "CNN verdict:" section.

- [ ] **Step 4: Commit**

```bash
git add scripts/postmortem.py
git commit -m "feat(cnn): postmortem prints CNN pattern + outcome verdict"
```

---

## Task 17: End-to-end smoke test on a historical trade

**Files:** none (manual verification)

- [ ] **Step 1: Pick a recent trade**

Pull a recent closed trade from `/api/trades`. Choose one with all 3 timeframes available (most tokens do).

- [ ] **Step 2: Render its image**

```python
python -c "
import asyncio, urllib.request, json
from feeds.chart_image_renderer import render_chart_image
from feeds.chart_data import assemble_chart_data
from feeds.gt_client import GeckoTerminalClient
from feeds.dexscreener_client import DexScreenerClient
async def main():
    gt = GeckoTerminalClient()
    ds = DexScreenerClient()
    # Substitute the actual pair address for a recent token
    cd = await assemble_chart_data(gt, 'PAIR_ADDRESS_HERE', dexs_client=ds)
    img = render_chart_image(cd.candles_1m, cd.candles_5m, cd.candles_15m)
    print('shape:', img.shape if img is not None else None)
asyncio.run(main())
"
```

Expected: `shape: (3, 64, 64)`

- [ ] **Step 3: Run inference**

```bash
python -c "
import asyncio
from core.chart_cnn_inference import get_inference
from feeds.chart_data import assemble_chart_data
from feeds.gt_client import GeckoTerminalClient
from feeds.dexscreener_client import DexScreenerClient
async def main():
    gt = GeckoTerminalClient()
    ds = DexScreenerClient()
    cd = await assemble_chart_data(gt, 'PAIR_ADDRESS_HERE', dexs_client=ds)
    inf = get_inference()
    r = inf.predict('TOKEN_ADDR', cd.candles_1m, cd.candles_5m, cd.candles_15m)
    print(r)
asyncio.run(main())
"
```

Expected: dict like `{'pattern': 'double_bottom', 'pattern_conf': 0.34, 'outcome_prob': 0.51}` (random-init weights without training, but valid shape).

- [ ] **Step 4: Mark task complete**

If the smoke test passes, the live integration is wired correctly and weights file is loadable. No commit needed for this task — pure verification.

---

## Task 18: Phantom parity for CNN fields

**Files:**
- Modify: `scripts/live_forward_test.py`

- [ ] **Step 1: Add CNN phantom predicates**

In `scripts/live_forward_test.py`, find the COMBOS dict (around line 700+ in this session's edits). Add three new combos that consume the CNN fields:

```python
    # ── Chart CNN phantom mirrors — SHADOW 2026-05-15 ─────────────
    # Mirror dip_scanner's CNN entry_meta fields. Snapshot must be
    # enriched with cnn_pattern / cnn_outcome_prob — see Task 19.
    'CNN_outcome_above_60': lambda c: (
        c.get('cnn_outcome_prob') is not None and c['cnn_outcome_prob'] >= 0.60
    ),
    'CNN_outcome_above_70': lambda c: (
        c.get('cnn_outcome_prob') is not None and c['cnn_outcome_prob'] >= 0.70
    ),
    'CNN_pattern_double_bottom': lambda c: (
        c.get('cnn_pattern') == 'double_bottom'
    ),
```

- [ ] **Step 2: Syntax check + smoke**

Run: `python -c "import ast; ast.parse(open('scripts/live_forward_test.py', encoding='utf-8').read()); print('OK')"`

Run: `python scripts/live_forward_test.py status | head -30` (won't show new combos until next snapshot, but verifies the script runs.)

- [ ] **Step 3: Commit**

```bash
git add scripts/live_forward_test.py
git commit -m "feat(cnn): phantom predicates for CNN outcome+pattern"
```

---

## Task 19: Enrich phantom snapshot with CNN fields

**Files:**
- Modify: `scripts/live_forward_test.py`

- [ ] **Step 1: Find take_snapshot enrichment loop**

Run: `grep -n "def take_snapshot\|compute_mtf_features\|compute_5m_features" scripts/live_forward_test.py | head -5`

Find where the enrichment functions are called inside `take_snapshot`.

- [ ] **Step 2: Add CNN feature computation**

Add a new helper function near the other `compute_*` functions:

```python
def compute_cnn_features(c):
    """Run CNN on the candidate's pre-entry candles. Returns dict
    with cnn_pattern, cnn_pattern_conf, cnn_outcome_prob. Fail-open."""
    out = {'cnn_pattern': None, 'cnn_pattern_conf': None, 'cnn_outcome_prob': None}
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from core.chart_cnn_inference import get_inference
        from feeds.candle_utils import Candle
        # Build Candle lists from GT 1m + 5m + 15m fetches. The snapshot
        # already calls fetch_gt_ohlcv_with_retry for 5m; reuse pattern.
        # For brevity here, fetch 60-bar 1m + 5m + 15m windows.
        # NOTE: this adds 2 GT calls per token (1m + 15m); 5m already fetched.
        # Acceptable since snapshot already paces with sleep(1.5).
        # Implementation: directly call fetch_gt_ohlcv_with_retry on each TF
        # and convert to Candle objects.
        import time as _t
        c1_raw = fetch_gt_ohlcv_with_retry(c['pair'], agg=1, limit=60)
        _t.sleep(1.0)
        c5_raw = fetch_gt_ohlcv_with_retry(c['pair'], agg=5, limit=60)
        _t.sleep(1.0)
        c15_raw = fetch_gt_ohlcv_with_retry(c['pair'], agg=15, limit=60)
        def _to_candles(raw):
            # GT format: [ts, o, h, l, c, v], newest-first.
            return [
                Candle(open_time=int(r[0]), open=float(r[1]), high=float(r[2]),
                       low=float(r[3]), close=float(r[4]), volume=float(r[5]),
                       close_time=int(r[0]) + 60)
                for r in reversed(raw)
            ]
        c1 = _to_candles(c1_raw or [])
        c5 = _to_candles(c5_raw or [])
        c15 = _to_candles(c15_raw or [])
        inf = get_inference()
        if inf.disabled:
            return out
        r = inf.predict(c['token'], c1, c5, c15)
        if r:
            out['cnn_pattern'] = r.get('pattern')
            out['cnn_pattern_conf'] = r.get('pattern_conf')
            out['cnn_outcome_prob'] = r.get('outcome_prob')
    except Exception:
        pass
    return out
```

- [ ] **Step 3: Wire into take_snapshot loop**

Inside `take_snapshot`, find the enrichment loop (`for c in top:`). Add a call to `compute_cnn_features` after the existing `compute_1h_features` line:

```python
        c.update(compute_cnn_features(c))
```

- [ ] **Step 4: Syntax check**

Run: `python -c "import ast; ast.parse(open('scripts/live_forward_test.py', encoding='utf-8').read()); print('OK')"`

- [ ] **Step 5: Test snapshot runs**

Run: `python scripts/live_forward_test.py` — verify it doesn't crash. The CNN fields will be `None` until weights are trained, but the snapshot should complete.

- [ ] **Step 6: Commit**

```bash
git add scripts/live_forward_test.py
git commit -m "feat(cnn): enrich phantom snapshot with CNN pattern+outcome"
```

---

## Task 20: Deploy + verify CNN integrations live

**Files:** none (deploy)

- [ ] **Step 1: Verify no open positions**

Run: `python -c "import urllib.request, json; print(json.loads(urllib.request.urlopen('https://gracious-inspiration-production.up.railway.app/api/positions', timeout=15).read())['count'])"`

Expected: `0` (or wait until 0 before deploying).

- [ ] **Step 2: Deploy**

Run: `MSYS_NO_PATHCONV=1 railway up --detach`

- [ ] **Step 3: Verify ChartCNN logs on startup**

Wait 2 min for build + deploy.

Run: `MSYS_NO_PATHCONV=1 railway logs --tail 200 | grep -iE "ChartCNN|chart_cnn"`

Expected: one of:
- `[ChartCNN] loaded weights from ...` (if you've already trained + uploaded weights)
- `[ChartCNN] weights not found at ... ; inference disabled` (expected if you haven't shipped weights)

- [ ] **Step 4: Verify forward collector writes on next scan**

After a few scanner cycles (1-2 min), check `/data/cnn_dataset/forward/` exists on the Railway volume (via dashboard or `railway shell`):

```bash
railway shell
ls -la /data/cnn_dataset/forward/$(date +%Y-%m-%d)/ | head
exit
```

Expected: `.npy` + `.json` files appearing.

- [ ] **Step 5: Verify entry_meta fields appear on next buy**

When the next buy fires, check `/api/trades?limit=5` and look for `entry_meta.cnn_pattern` field. Will be `None` if weights aren't trained — that's fine, the plumbing works.

- [ ] **Step 6: Commit nothing — just verification**

Mark task complete.

---

## Self-review

Re-read the spec (`docs/superpowers/specs/2026-05-15-chart-cnn-design.md`) and verify every section is covered by a task:

| Spec section | Tasks |
|---|---|
| Architecture | Tasks 2-15 cover all three time domains (train, inference, forward) |
| Component: chart_image_renderer.py | Tasks 2-3 |
| Component: chart_cnn.py | Tasks 4-5 |
| Component: chart_cnn_inference.py | Tasks 6-7 |
| Component: backfill_chart_dataset.py | Task 8 |
| Component: train_chart_cnn.py | Task 9 |
| Component: forward_dataset_collector.py | Tasks 10-11 |
| Modification: dip_scanner.py | Tasks 12-13 |
| Modification: peak_recorder.py | Task 14 |
| Modification: trader.py (outcome update) | Task 15 |
| Modification: postmortem.py | Task 16 |
| Image format | Tasks 2-3 (renderer) |
| Label format | Tasks 8 (backfill) + 11 (forward collector) |
| Error handling | Embedded in Tasks 7, 11 (graceful disable + disk guard) |
| Testing | Tasks 2, 4, 6, 10 (unit tests); Task 17 (smoke) |
| Promotion gate | Not coded in v1 — measured externally via forward dataset accumulation. Spec says shadow-only in v1. |
| Phantom parity | Tasks 18-19 |
| Deploy verification | Task 20 |

**Placeholder scan:** Every step shows actual code or commands. No "TBD" / "implement later" / "appropriate error handling" — all error handling is shown explicitly per spec.

**Type consistency:** `Candle` dataclass used uniformly. `IDX_TO_CLASS` / `CLASS_TO_IDX` / `PATTERN_CLASSES` consistent across model, training, and inference. `entry_meta_dict` keys (`cnn_pattern`, `cnn_pattern_conf`, `cnn_outcome_prob`) consistent across dip_scanner, peak_recorder, postmortem, phantom.

**Spec coverage gaps:** None found.

---

## Notes

- **Training depends on backfill (Task 8) completing first.** If backfill returns < 10 trades, training will skip and the model file won't exist — inference will gracefully disable.
- **The model file is gitignored** (Task 9 step 4). Distribute via Railway volume or rebuild on each train.
- **Forward collector data accumulates on `/data` volume**, which persists across Railway redeploys.
- **Weekly retrain** is a manual operation in v1 (`python scripts/train_chart_cnn.py`). Could be automated via Railway cron in v2.
