"""
DexScreener internal chart-bars binary format parser.

The endpoint `https://io.dexscreener.com/dex/chart/amm/v3/{dex_slug}/bars/
solana/{pair}?res={1|5|15|60}&cb={count}&q={quote_mint}` returns a custom
length-prefix binary format that powers the dexscreener.com candlestick UI.
NOT documented; reverse-engineered from network traffic.

Header (8 bytes):
  0x0a  0x31 0x2e 0x30 0x2e 0x30   "1.0.0" version magic
  0x02  0x14                        section header (count marker)

Per bar (steady-state ~101 bytes for SOL prices, varies with ASCII width):
  8 bytes      Unix-ms timestamp as little-endian float64
  pricestring  open
  0x02         separator
  pricestring  open (duplicate when mc=0; mcap-form when mc=1)
  pricestring  high
  0x02
  pricestring  high  (dup or mcap)
  pricestring  low
  0x02
  pricestring  low (dup or mcap)
  pricestring  close
  0x02
  pricestring  close (dup or mcap)
  0x02         volume separator
  pricestring  volume_usd
  8 bytes      first block number in window  (LE float64)
  8 bytes      last  block number in window  (LE float64)

pricestring = <len_byte = ascii_len * 2> + <ascii_len ASCII chars>

The format is robust to occasional variable-size tail bars (live/forming
candles emit twice). The parser anchors on plausible Unix-ms timestamps and
skips malformed regions.
"""
from __future__ import annotations

import struct
from typing import Any, Dict, List


_HEADER_LEN = 8  # 0a 31 2e 30 2e 30 02 14


def parse_bar(raw: bytes, p: int) -> tuple[Dict[str, Any] | None, int]:
    """Parse a single bar starting at byte offset p. Returns (bar, next_p) or
    (None, p) on parse failure."""
    try:
        if p + 8 > len(raw):
            return None, p
        ts = struct.unpack("<d", raw[p:p + 8])[0]
        # Plausible Unix-ms range: 2024-2030
        if not (1.7e12 < ts < 1.9e12):
            return None, p
        q = p + 8
        prices: List[tuple[str, str]] = []
        for _ in range(4):  # open, high, low, close
            L = raw[q]; q += 1
            ascii_len = L // 2
            s1 = raw[q:q + ascii_len].decode("ascii"); q += ascii_len
            sep = raw[q]; q += 1
            if sep != 0x02:
                return None, p
            L2 = raw[q]; q += 1
            s2 = raw[q:q + L2 // 2].decode("ascii"); q += L2 // 2
            prices.append((s1, s2))
        # Volume: extra sep + lp string
        if raw[q] != 0x02:
            return None, p
        q += 1
        Lv = raw[q]; q += 1
        vol = raw[q:q + Lv // 2].decode("ascii"); q += Lv // 2
        # Two trailing 8-byte LE float64s (first/last block numbers in window)
        if q + 16 > len(raw):
            return None, p
        block_first = struct.unpack("<d", raw[q:q + 8])[0]; q += 8
        block_last = struct.unpack("<d", raw[q:q + 8])[0]; q += 8
        return {
            "ts_ms": int(ts),
            "open": float(prices[0][0]),
            "high": float(prices[1][0]),
            "low": float(prices[2][0]),
            "close": float(prices[3][0]),
            "volume_usd": float(vol),
            "block_first": int(block_first),
            "block_last": int(block_last),
        }, q
    except Exception:
        return None, p


def parse_chart_bars(raw: bytes) -> List[Dict[str, Any]]:
    """Parse a complete bars response into a list of bar dicts.

    Robust to:
      - Trailing duplicate "live" bars (live candle re-emit on each refresh)
      - Trailing partial bars
      - Inline garbage (bytes are skipped 1 at a time on parse failure)

    Returns bars sorted oldest-first and deduped by timestamp (last write wins).
    """
    if len(raw) < _HEADER_LEN + 8:
        return []
    p = _HEADER_LEN
    seen: Dict[int, Dict[str, Any]] = {}
    while p < len(raw) - 16:
        bar, np = parse_bar(raw, p)
        if bar is None:
            p += 1
            continue
        seen[bar["ts_ms"]] = bar
        p = np
    return sorted(seen.values(), key=lambda b: b["ts_ms"])


def rolling_high_from_bars(bars, window_secs, now_ms):
    """Max bar high over bars whose ts_ms is within window_secs of now_ms.

    Returns None if no qualifying bar with a positive high. Pure; never raises;
    skips malformed/non-numeric bars."""
    lo_ms = float(now_ms) - float(window_secs) * 1000.0
    best = None
    for b in bars or []:
        try:
            if float(b["ts_ms"]) < lo_ms:
                continue
            h = float(b["high"])
        except (KeyError, TypeError, ValueError):
            continue
        if h > 0 and (best is None or h > best):
            best = h
    return best


def rolling_low_from_bars(bars, window_secs, now_ms):
    """Min bar low over bars whose ts_ms is within window_secs of now_ms.

    Returns None if no qualifying bar with a positive low. Pure; never raises."""
    lo_ms = float(now_ms) - float(window_secs) * 1000.0
    best = None
    for b in bars or []:
        try:
            if float(b["ts_ms"]) < lo_ms:
                continue
            lw = float(b["low"])
        except (KeyError, TypeError, ValueError):
            continue
        if lw > 0 and (best is None or lw < best):
            best = lw
    return best
