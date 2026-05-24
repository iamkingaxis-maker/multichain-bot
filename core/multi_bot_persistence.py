from __future__ import annotations
import json
import threading
from pathlib import Path
from typing import Optional


class MultiBotTradeStore:
    """Bot-aware trade persistence.

    File layout under data_dir:
      trades_multi.json     — append-only list of multi-bot trade records
      trades.json           — legacy single-bot/baseline_v1 records (owned by
                              dashboard/tracker.py::PerformanceTracker)
      bot_state/{id}.json   — per-bot capital + daily P&L snapshot

    Option B split (2026-05-23): multi-bot writes were moved off of trades.json
    to eliminate a race with PerformanceTracker._save_trades. Each writer now
    owns exactly one file. A one-shot migration partitions any pre-split
    trades.json by bot_id on first boot.

    Legacy records lacking a 'bot_id' field are implicitly stamped
    'baseline_v1' on read (backfill-on-read). The migration script
    rewrites them on disk explicitly.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "bot_state").mkdir(exist_ok=True)
        self._trades_path = self.data_dir / "trades_multi.json"
        self._lock = threading.Lock()
        self._maybe_split_legacy()

    def _maybe_split_legacy(self) -> None:
        """One-shot: partition pre-split trades.json into legacy + multi files.

        Writes a sentinel `.trades_split_v1` to make this idempotent. Safe to
        run on every boot — short-circuits if sentinel exists or if
        trades_multi.json already exists (which implies split has happened).

        Splits records by bot_id: those with bot_id != "baseline_v1" move to
        trades_multi.json; those with bot_id == "baseline_v1" or missing stay
        in trades.json under PerformanceTracker's ownership.

        Crash-safe: writes trades_multi.json first; only rewrites trades.json
        if the multi-write succeeded.
        """
        sentinel = self.data_dir / ".trades_split_v1"
        if sentinel.exists() or self._trades_path.exists():
            return
        legacy = self.data_dir / "trades.json"
        if not legacy.exists():
            sentinel.write_text("no-legacy")
            return
        try:
            all_records = json.loads(legacy.read_text())
        except json.JSONDecodeError:
            sentinel.write_text("legacy-unreadable")
            return
        if not isinstance(all_records, list):
            sentinel.write_text("legacy-not-list")
            return
        multi = [r for r in all_records
                 if isinstance(r, dict) and r.get("bot_id") and r["bot_id"] != "baseline_v1"]
        legacy_only = [r for r in all_records
                       if not isinstance(r, dict) or not r.get("bot_id") or r["bot_id"] == "baseline_v1"]
        # Write multi-bot file first; only mutate legacy file if that succeeded
        self._trades_path.write_text(json.dumps(multi))
        legacy.write_text(json.dumps(legacy_only, indent=2))
        sentinel.write_text(
            f"split-at-{len(all_records)}-into-{len(legacy_only)}-legacy+{len(multi)}-multi"
        )

    def record_trade(self, trade: dict, bot_id: str) -> None:
        record = dict(trade)
        record["bot_id"] = bot_id
        with self._lock:
            existing = []
            if self._trades_path.exists():
                try:
                    existing = json.loads(self._trades_path.read_text())
                except json.JSONDecodeError:
                    existing = []
            existing.append(record)
            self._trades_path.write_text(json.dumps(existing))

    def load_trades(self, bot_id: Optional[str] = None) -> list[dict]:
        if not self._trades_path.exists():
            return []
        data = json.loads(self._trades_path.read_text())
        for t in data:
            if "bot_id" not in t:
                t["bot_id"] = "baseline_v1"
        if bot_id is None:
            return data
        return [t for t in data if t["bot_id"] == bot_id]

    def save_bot_state(self, bot_id: str, state: dict) -> None:
        path = self.data_dir / "bot_state" / f"{bot_id}.json"
        with self._lock:
            path.write_text(json.dumps(state, indent=2))

    def load_bot_state(self, bot_id: str) -> Optional[dict]:
        path = self.data_dir / "bot_state" / f"{bot_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())
