"""
DexScreener internal trade-log binary format parser.

Endpoint:
    https://io.dexscreener.com/dex/log/amm/v4/{dex_slug}/all/solana/{pair}
        ?q={quote_mint}&c=1

Returns a custom length-prefix binary format with one or more trade
records per response. Each record represents a single on-chain swap.

Per-record layout (reverse-engineered):

    08 'swap'                     ascii tag, varint-len-prefixed (len*2)
    8 bytes  LE float64           block number (`block_first`)
    8 bytes  LE float64           timestamp in Unix milliseconds
    varint-len ascii              transaction signature (88 base58 chars)
    02                            separator
    varint-len ascii              maker / taker address (~44 base58 chars)
    02                            separator
    8 bytes  LE float64           pool reserve A before (or similar)
    8 bytes  LE float64           pool reserve A after
    8 bytes  LE float64           pool reserve B before
    8 bytes  LE float64           pool reserve B after
    varint-len ascii              reserve_A as decimal string
    02 30                         separator + literal '0'
    02
    varint-len ascii              reserve_B as decimal string
    02
    8 bytes  LE float64           ?
    8 bytes  LE float64           ts again (or block_last)
    8 bytes  LE float64           ?
    06 'buy' OR 08 'sell'         kind tag, varint-len-prefixed
    02
    varint-len ascii              price_USD       (e.g. "0.0001131")
    02
    varint-len ascii              price_QUOTE     (e.g. "0.000001342")
    02
    varint-len ascii              VOLUME_USD      (e.g. "10.58")     ← KEY
    varint-len ascii              volume_base     (e.g. "93560.39")
    varint-len ascii              volume_quote    (e.g. "0.1256")
    00 00                         optional record terminator

Parser strategy: anchor on the literal byte sequence `08 73 77 61 70`
(`swap` tag with len=8 prefix). Within each record window, scan for
`buy`/`sell` kind tag (single-byte varint = 6 or 8) and read the next
three varint-prefixed ASCII strings. The third is volume_USD.

Length prefix is **protobuf-style varint** (LSB-first, bit 7 = more).
For values < 128 this is one byte equal to ascii_len*2; for longer
strings (tx signatures at 88 chars, len*2 = 176 ≥ 128) it's 2 bytes.
"""
from __future__ import annotations

import struct
from typing import Any, Dict, List, Optional

# Marker byte sequences (varint-prefixed ASCII tags)
_SWAP_MARKER = b"\x08swap"   # len=8 (4 chars * 2), then "swap"
_BUY_MARKER = b"\x06buy"     # len=6
_SELL_MARKER = b"\x08sell"   # len=8


def _read_varint(raw: bytes, p: int) -> tuple[int, int]:
    """Read a protobuf-style varint at offset p. Returns (value, new_p).

    Raises IndexError on truncation; caller guards.
    """
    value = 0
    shift = 0
    while True:
        b = raw[p]
        p += 1
        value |= (b & 0x7F) << shift
        if (b & 0x80) == 0:
            return value, p
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def _read_lp_string(raw: bytes, p: int) -> tuple[str, int]:
    """Read a varint-len-prefixed ASCII string. Length is value/2."""
    L, p = _read_varint(raw, p)
    n = L // 2
    s = raw[p:p + n].decode("ascii", errors="replace")
    return s, p + n


def _find_next(raw: bytes, marker: bytes, start: int) -> int:
    """Find next occurrence of marker at byte offset >= start, or -1."""
    return raw.find(marker, start)


def parse_trades(raw: bytes, *, max_records: int = 200) -> List[Dict[str, Any]]:
    """Parse a complete trade-log response into a list of trade dicts.

    Each dict has keys: kind ("buy" or "sell"), volume_usd (float),
    ts (ISO-8601 string with timezone). Failure-tolerant: skips
    malformed records rather than raising. Returns trades sorted
    most-recent-first (matching gt_client.fetch_recent_trades order).
    """
    out: List[Dict[str, Any]] = []
    p = 0
    while p < len(raw) and len(out) < max_records:
        swap_at = _find_next(raw, _SWAP_MARKER, p)
        if swap_at < 0:
            break
        # Skip the "swap" tag itself
        rec_start = swap_at + len(_SWAP_MARKER)
        # Find the boundary of this record = start of NEXT swap (or EOF)
        next_swap = _find_next(raw, _SWAP_MARKER, rec_start)
        rec_end = next_swap if next_swap >= 0 else len(raw)
        # Advance outer pointer past this record
        p = rec_end

        rec = raw[rec_start:rec_end]
        try:
            # First 8 bytes = block_first (float64). We don't use it.
            # Next 8 bytes = ts_ms (Unix-ms LE float64).
            if len(rec) < 16:
                continue
            ts_ms = struct.unpack("<d", rec[8:16])[0]
            if not (1.7e12 < ts_ms < 1.9e12):
                # Implausible; skip this record
                continue

            # Extract maker address: layout after ts is
            #   varint-len + tx_signature (~88 chars)
            #   0x02 separator
            #   varint-len + maker_address (~44 chars)
            maker_address = ""
            try:
                q = 16
                # Skip tx signature
                _, q = _read_lp_string(rec, q)
                # Skip separator
                if q < len(rec) and rec[q] == 0x02:
                    q += 1
                    maker_address, q = _read_lp_string(rec, q)
            except Exception:
                maker_address = ""

            # Find buy or sell marker INSIDE this record
            buy_pos = rec.find(_BUY_MARKER)
            sell_pos = rec.find(_SELL_MARKER)
            if buy_pos < 0 and sell_pos < 0:
                continue
            if buy_pos < 0:
                kind, kind_pos, kind_marker_len = "sell", sell_pos, len(_SELL_MARKER)
            elif sell_pos < 0:
                kind, kind_pos, kind_marker_len = "buy", buy_pos, len(_BUY_MARKER)
            else:
                # Both substrings present (e.g. 'sell' contains 'sel...'
                # — but not 'buy'). Pick the earliest-occurring one.
                if buy_pos < sell_pos:
                    kind, kind_pos, kind_marker_len = "buy", buy_pos, len(_BUY_MARKER)
                else:
                    kind, kind_pos, kind_marker_len = "sell", sell_pos, len(_SELL_MARKER)

            # After kind tag: 02 sep, then 3 ASCII strings (price_USD,
            # price_QUOTE, volume_USD). 3rd string = volume_USD.
            q = kind_pos + kind_marker_len
            volume_usd = None
            for i in range(3):
                # Expect a 0x02 separator
                if q >= len(rec) or rec[q] != 0x02:
                    break
                q += 1
                try:
                    s, q = _read_lp_string(rec, q)
                except (IndexError, ValueError):
                    break
                if i == 2:
                    try:
                        volume_usd = float(s)
                    except ValueError:
                        volume_usd = None
            if volume_usd is None:
                continue

            # ts to ISO-8601 (UTC). Match gt_client.fetch_recent_trades shape.
            import datetime as _dt
            ts_iso = _dt.datetime.fromtimestamp(
                ts_ms / 1000.0, tz=_dt.timezone.utc
            ).isoformat()
            out.append({
                "kind": kind,
                "volume_usd": float(volume_usd),
                "ts": ts_iso,
                "maker": maker_address,
            })
        except Exception:
            continue

    # Sort newest-first to match GT client convention
    out.sort(key=lambda t: t["ts"], reverse=True)
    return out
