"""
Smart-money wallet detection.

Approach (v0): score wallets by how many of OUR closed winners they
appeared on. Wallets that show up on multiple wins are presumed to have
edge. At entry time, count how many smart wallets appear in
recent_trades makers within the last 60s.

The index file (data/smart_money_index.json) is built offline by
scripts/build_smart_money_index.py and refreshed daily.

Index schema:
{
  "version": 1,
  "generated_at": "2026-05-04T22:00:00Z",
  "smart_threshold": 3,            # min winners to be "smart"
  "wallets": {
    "<wallet_address>": {
      "winners": 5, "losers": 1,
      "win_rate": 0.83,
      "total_volume_usd": 12455.0,
      "avg_winner_volume_usd": 1245.50
    }
  }
}

Runtime (this module): load index lazily at first use. Score recent
trades by counting unique smart wallets appearing in the buy makers.

Fail-open: if the index file doesn't exist or is stale, return defaults
(smart_wallet_count_60s = 0). Bot still runs; signal is just absent.
"""
from __future__ import annotations
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)


class SmartMoneyIndex:
    """Lazy-loading wallet index. Thread-safe enough for our use (single
    scanner process, occasional re-loads on file mtime change)."""

    def __init__(self, index_path: Optional[str] = None):
        # Resolve index path with fallback chain:
        #   1. Explicit arg
        #   2. $DATA_DIR/smart_money_index.json (Railway persistent volume)
        #   3. ./assets/smart_money_index.json (committed in repo, deploys
        #      with code — used until offline rebuilds populate /data)
        #
        # Note: prior path was data/ but that directory is in .gitignore
        # and Railway upload skipped it; assets/ is unambiguously tracked.
        if index_path is None:
            candidates = []
            data_dir = os.environ.get("DATA_DIR", "/data")
            candidates.append(os.path.join(data_dir, "smart_money_index.json"))
            candidates.append(os.path.join("assets", "smart_money_index.json"))
            chosen = None
            for c in candidates:
                if os.path.exists(c):
                    chosen = c
                    break
            # If none exist yet, default to the repo-shipped location so
            # next rebuild lands somewhere predictable.
            index_path = chosen or candidates[-1]
            logger.info(
                f"[SmartMoney] Index path resolved to: {index_path} "
                f"(exists={os.path.exists(index_path)}, "
                f"cwd={os.getcwd()}, "
                f"candidates={candidates})"
            )
        self._index_path = index_path
        self._wallets: Dict[str, Dict[str, Any]] = {}
        self._smart_set: set = set()
        self._smart_threshold: int = 3
        self._loaded: bool = False
        self._mtime: float = 0.0

    def _load_if_needed(self) -> None:
        """Load index if file changed or never loaded."""
        try:
            if not os.path.exists(self._index_path):
                # First run, no index yet — leave empty.
                self._loaded = True
                return
            mt = os.path.getmtime(self._index_path)
            if self._loaded and mt == self._mtime:
                return
            with open(self._index_path) as f:
                data = json.load(f)
            self._wallets = data.get("wallets", {}) or {}
            self._smart_threshold = int(data.get("smart_threshold", 3))
            self._smart_set = {
                w for w, info in self._wallets.items()
                if int(info.get("winners", 0)) >= self._smart_threshold
            }
            self._mtime = mt
            self._loaded = True
            logger.info(
                f"[SmartMoney] Loaded index: {len(self._wallets)} wallets, "
                f"{len(self._smart_set)} above threshold (>={self._smart_threshold} winners)"
            )
        except Exception as e:
            logger.warning(f"[SmartMoney] Index load failed: {e}")
            self._loaded = True  # don't keep retrying

    @staticmethod
    def _parse_ts(ts: Any) -> Optional[float]:
        if not ts:
            return None
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

    def score_recent_trades(
        self, recent_trades: Sequence[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Compute smart-money features from recent_trades.

        Always returns the same key set (with defaults) so entry_meta
        stays consistent — the keys are present even when the index is
        empty.
        """
        self._load_if_needed()
        defaults = {
            "smart_wallet_count_60s": 0,
            "smart_wallet_count_total": 0,
            "smart_wallet_volume_usd": 0.0,
            "smart_wallet_volume_pct": 0.0,
            "smart_money_index_size": len(self._smart_set),
        }
        if not recent_trades or not self._smart_set:
            return defaults
        buys = [t for t in recent_trades if t.get("kind") == "buy" and t.get("maker")]
        if not buys:
            return defaults
        # Most recent buy timestamp = anchor
        anchor: Optional[float] = None
        for t in buys:
            ts = self._parse_ts(t.get("ts"))
            if ts is not None and (anchor is None or ts > anchor):
                anchor = ts

        smart_60s: set = set()
        smart_total: set = set()
        smart_volume = 0.0
        total_buy_volume = 0.0
        for t in buys:
            m = str(t.get("maker", ""))
            v = float(t.get("volume_usd") or 0)
            total_buy_volume += v
            if m in self._smart_set:
                smart_total.add(m)
                smart_volume += v
                if anchor is not None:
                    ts = self._parse_ts(t.get("ts"))
                    if ts is not None and (anchor - ts) <= 60.0:
                        smart_60s.add(m)
        return {
            "smart_wallet_count_60s": len(smart_60s),
            "smart_wallet_count_total": len(smart_total),
            "smart_wallet_volume_usd": round(smart_volume, 2),
            "smart_wallet_volume_pct": round(
                smart_volume / total_buy_volume, 3
            ) if total_buy_volume > 0 else 0.0,
            "smart_money_index_size": len(self._smart_set),
        }


def extract_top_makers(
    recent_trades: Sequence[Dict[str, Any]], top_n: int = 5
) -> Dict[str, Any]:
    """
    Capture top-N buy makers (by volume) into entry_meta so we can
    bootstrap the smart-money index from our own forward trade history
    without needing external maker fetches.

    Returns a dict with two keys:
      top_buy_makers       — list of {addr, volume_usd, n_buys}
      top_buy_makers_n     — total unique buyer count

    Fail-open: returns {"top_buy_makers": [], "top_buy_makers_n": None} on
    bad input.

    2026-07-02 FIX (missing-data-read-as-zero bug-class sweep): top_buy_makers_n
    is None (UNKNOWN), not 0, when no maker-tagged buys exist. An empty/maker-
    stripped trade log (io.dexscreener timeout / GT fallback strips maker) is a
    DATA GAP, not a measurement of buyer concentration — the fabricated 0 made
    the whale_concentrated_demand / whale_recent_burst / whale_p90_size triggers
    read "top_buy_makers_n=0 < 9 (concentrated!)" on missing data and half-fire
    on tokens whose buyer count was simply unknown. When maker data IS present,
    n >= 1 by construction, so None is unambiguous. All consumers are
    is-not-None guarded (dip_scanner whale triggers, FeatureBundle Optional,
    missed_reject_diagnosis).
    """
    if not recent_trades:
        return {"top_buy_makers": [], "top_buy_makers_n": None}
    buys = [t for t in recent_trades if t.get("kind") == "buy" and t.get("maker")]
    if not buys:
        return {"top_buy_makers": [], "top_buy_makers_n": None}
    per_maker_vol: Dict[str, float] = {}
    per_maker_count: Dict[str, int] = {}
    for t in buys:
        m = str(t.get("maker", ""))
        v = float(t.get("volume_usd") or 0)
        per_maker_vol[m] = per_maker_vol.get(m, 0.0) + v
        per_maker_count[m] = per_maker_count.get(m, 0) + 1
    sorted_makers = sorted(per_maker_vol.items(), key=lambda kv: -kv[1])[:top_n]
    return {
        "top_buy_makers": [
            {
                "addr": m,
                "volume_usd": round(v, 2),
                "n_buys": per_maker_count[m],
            }
            for m, v in sorted_makers
        ],
        "top_buy_makers_n": len(per_maker_vol),
    }
