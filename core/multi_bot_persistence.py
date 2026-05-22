from __future__ import annotations
import json
import threading
from pathlib import Path
from typing import Optional


class MultiBotTradeStore:
    """Bot-aware trade persistence.

    File layout under data_dir:
      trades.json           — append-only list of trade records (all bots)
      bot_state/{id}.json   — per-bot capital + daily P&L snapshot

    Legacy records lacking a 'bot_id' field are implicitly stamped
    'baseline_v1' on read (backfill-on-read). The migration script
    rewrites them on disk explicitly.
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "bot_state").mkdir(exist_ok=True)
        self._trades_path = self.data_dir / "trades.json"
        self._lock = threading.Lock()

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
