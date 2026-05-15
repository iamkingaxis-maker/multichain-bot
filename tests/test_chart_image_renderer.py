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
