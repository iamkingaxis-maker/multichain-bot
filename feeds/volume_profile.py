"""
Phase 4 of chart-reading rebuild — volume-at-price profile.

Where the most volume traded is where real demand and supply lived.
Standard "volume profile" tool from professional trading platforms,
adapted for short-term memecoin signal generation.

Algorithm:
  1. Determine price range over input candles (high to low)
  2. Bin the range into N equal-width buckets (default 20)
  3. For each candle, attribute its volume to bins based on the
     candle's high-low range overlap with each bin
  4. Surface:
       - point_of_control (POC) — bin with highest volume = "fairest
         price" / heaviest historical interest
       - high-volume nodes (HVN) — bins with volume above 1.5x mean
       - low-volume nodes (LVN) — bins with volume below 0.5x mean
       - distance from current price to nearest HVN

Why this matters:
  - HVNs ABOVE current price → resistance ceiling (sellers parked
    there historically; expect rejection)
  - HVNs BELOW current price → demand floor (buyers stepped in
    there; high probability of bounce)
  - LVNs → "vacuum" — price moves through these quickly
  - At an HVN → consolidation or pivot
  - In an LVN → likely to keep moving

Volume attribution model:
  Each candle's volume is split proportionally across the bins its
  high-low range covers. A candle that traded entirely in one bin
  contributes all volume to that bin. A candle that spanned 5 bins
  contributes 1/5 volume per bin (uniform model — assumes time
  spent at each price level was roughly equal across the candle).

  This is a simplification; true VWAP-style attribution would use
  intra-candle ticks, but those aren't available from the OHLCV API.
  Uniform attribution is the standard approximation.
"""
from __future__ import annotations

from typing import List, Dict, Any

from feeds.candle_utils import Candle


def build_profile(candles: List[Candle], num_bins: int = 20) -> Dict[str, Any]:
    """Compute volume-at-price for the input candle series.

    Returns:
      bins              list of (price_low, price_high, volume) for each bin
      poc_price         price of the bin with maximum volume (mid-bin)
      poc_volume        volume at POC
      total_volume      sum of all bin volumes
      mean_bin_volume   mean volume per bin
      hvn_threshold     1.5x mean (cutoff for "high-volume node")
      lvn_threshold     0.5x mean (cutoff for "low-volume node")
      hvn_count         number of bins above hvn threshold
      lvn_count         number of bins below lvn threshold
    """
    if not candles or num_bins < 2:
        return {
            "bins": [],
            "poc_price": None, "poc_volume": 0,
            "total_volume": 0, "mean_bin_volume": 0,
            "hvn_threshold": 0, "lvn_threshold": 0,
            "hvn_count": 0, "lvn_count": 0,
        }

    overall_high = max(c.high for c in candles)
    overall_low = min(c.low for c in candles)
    if overall_high <= overall_low:
        return {
            "bins": [],
            "poc_price": overall_high, "poc_volume": 0,
            "total_volume": 0, "mean_bin_volume": 0,
            "hvn_threshold": 0, "lvn_threshold": 0,
            "hvn_count": 0, "lvn_count": 0,
        }

    bin_width = (overall_high - overall_low) / num_bins
    bin_lows = [overall_low + i * bin_width for i in range(num_bins)]
    bin_highs = [overall_low + (i + 1) * bin_width for i in range(num_bins)]
    bin_vols = [0.0] * num_bins

    # Distribute each candle's volume across the bins it spans
    for c in candles:
        c_low = c.low
        c_high = c.high
        if c_high <= c_low:
            continue
        c_range = c_high - c_low
        for i in range(num_bins):
            overlap = max(0.0, min(c_high, bin_highs[i]) - max(c_low, bin_lows[i]))
            if overlap > 0:
                bin_vols[i] += c.volume * (overlap / c_range)

    total_vol = sum(bin_vols)
    mean_vol = total_vol / num_bins if num_bins > 0 else 0
    hvn_threshold = mean_vol * 1.5
    lvn_threshold = mean_vol * 0.5

    poc_idx = bin_vols.index(max(bin_vols)) if total_vol > 0 else 0
    poc_price = (bin_lows[poc_idx] + bin_highs[poc_idx]) / 2

    bins = [
        (round(bin_lows[i], 8), round(bin_highs[i], 8), round(bin_vols[i], 2))
        for i in range(num_bins)
    ]
    hvn_count = sum(1 for v in bin_vols if v >= hvn_threshold)
    lvn_count = sum(1 for v in bin_vols if v <= lvn_threshold)

    return {
        "bins": bins,
        "poc_price": round(poc_price, 8),
        "poc_volume": round(bin_vols[poc_idx], 2),
        "total_volume": round(total_vol, 2),
        "mean_bin_volume": round(mean_vol, 2),
        "hvn_threshold": round(hvn_threshold, 2),
        "lvn_threshold": round(lvn_threshold, 2),
        "hvn_count": hvn_count,
        "lvn_count": lvn_count,
    }


def analyze(candles: List[Candle], current_price: float | None = None, num_bins: int = 20) -> Dict[str, Any]:
    """Build the profile, then position the current price within it.

    Returns the build_profile output PLUS:
      current_price
      poc_distance_pct           % distance from current to POC
      current_above_poc          bool
      nearest_hvn_above_price    nearest high-volume bin above current
      nearest_hvn_above_pct      % distance to it
      nearest_hvn_below_price    nearest high-volume bin below current
      nearest_hvn_below_pct      % distance to it
      at_hvn (bool)              within 1% of an HVN
      in_lvn (bool)              current price falls in a low-volume bin
                                 (= price is in a "vacuum"; likely to
                                 move quickly through)
    """
    profile = build_profile(candles, num_bins=num_bins)
    if not candles or not profile["bins"]:
        profile.update({
            "current_price": current_price,
            "poc_distance_pct": None,
            "current_above_poc": False,
            "nearest_hvn_above_price": None,
            "nearest_hvn_above_pct": None,
            "nearest_hvn_below_price": None,
            "nearest_hvn_below_pct": None,
            "at_hvn": False,
            "in_lvn": False,
        })
        return profile

    if current_price is None:
        current_price = candles[-1].close

    bins = profile["bins"]  # list of (low, high, vol)
    hvn_threshold = profile["hvn_threshold"]
    lvn_threshold = profile["lvn_threshold"]

    # Find nearest HVN above and below
    hvn_bins = [(low, high, vol) for low, high, vol in bins if vol >= hvn_threshold]
    above = [b for b in hvn_bins if (b[0] + b[1]) / 2 > current_price]
    below = [b for b in hvn_bins if (b[0] + b[1]) / 2 < current_price]

    nearest_above = min(above, key=lambda b: (b[0] + b[1]) / 2 - current_price) if above else None
    nearest_below = max(below, key=lambda b: (b[0] + b[1]) / 2 - current_price) if below else None

    nha_price = (nearest_above[0] + nearest_above[1]) / 2 if nearest_above else None
    nhb_price = (nearest_below[0] + nearest_below[1]) / 2 if nearest_below else None
    nha_pct = ((nha_price - current_price) / current_price * 100) if nha_price and current_price > 0 else None
    nhb_pct = ((current_price - nhb_price) / current_price * 100) if nhb_price and current_price > 0 else None

    at_hvn = bool(
        (nha_pct is not None and nha_pct <= 1.0)
        or (nhb_pct is not None and nhb_pct <= 1.0)
    )

    # Is current price in an LVN bucket?
    in_lvn = False
    for low, high, vol in bins:
        if low <= current_price <= high and vol <= lvn_threshold:
            in_lvn = True
            break

    poc_price = profile["poc_price"]
    poc_dist = ((current_price - poc_price) / poc_price * 100) if poc_price and poc_price > 0 else None

    profile.update({
        "current_price": round(current_price, 8),
        "poc_distance_pct": round(poc_dist, 3) if poc_dist is not None else None,
        "current_above_poc": (poc_price is not None and current_price > poc_price),
        "nearest_hvn_above_price": round(nha_price, 8) if nha_price else None,
        "nearest_hvn_above_pct": round(nha_pct, 3) if nha_pct is not None else None,
        "nearest_hvn_below_price": round(nhb_price, 8) if nhb_price else None,
        "nearest_hvn_below_pct": round(nhb_pct, 3) if nhb_pct is not None else None,
        "at_hvn": at_hvn,
        "in_lvn": in_lvn,
    })
    return profile
