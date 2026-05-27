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
        self._maybe_scrub_giga_phantom()
        self._maybe_scrub_eurc_phantom()
        self._maybe_cleanup_phantom_backups()
        self._maybe_cleanup_cnn_dataset()
        self._maybe_reconcile_positions()
        self._maybe_reset_cap2k_pnl()

    def _maybe_scrub_giga_phantom(self) -> None:
        """One-shot reversal of the 2026-05-27 GIGA phantom-stop losses.

        Runs here (store construction, before any bot loads its capital snapshot)
        so bots read the corrected balances — no race. Sentinel-guarded inside
        scrub(); wrapped so a migration error can NEVER break the trading boot.
        """
        try:
            import sys
            root = str(Path(__file__).resolve().parent.parent)  # repo root
            if root not in sys.path:
                sys.path.insert(0, root)
            from scripts.scrub_giga_phantom import scrub
            res = scrub(self.data_dir)
            if "skipped" not in res:
                import logging
                logging.getLogger(__name__).warning(
                    "GIGA phantom scrub ran: restored $%s across %d bots, %s sells repriced; backup=%s",
                    res.get("total_restored"),
                    len(res.get("restored_per_bot", {})),
                    res.get("files"),
                    res.get("backup"),
                )
        except Exception as e:  # never break boot on a migration error
            import logging
            logging.getLogger(__name__).error(
                "GIGA phantom scrub skipped (non-fatal error): %s", e
            )

    def _maybe_scrub_eurc_phantom(self) -> None:
        """One-shot reversal of the 2026-05-27 EURC phantom-WIN profit.

        no_filters booked +$106,334 of phantom profit on a $6,199 bad-tick exit of
        a $1.16 stablecoin. Repriced to the real exit here, before any bot reads
        its capital snapshot. Sentinel-guarded inside scrub(); wrapped so a
        migration error can NEVER break the trading boot.
        """
        try:
            import sys
            root = str(Path(__file__).resolve().parent.parent)  # repo root
            if root not in sys.path:
                sys.path.insert(0, root)
            from scripts.scrub_eurc_phantom import scrub
            res = scrub(self.data_dir)
            if "skipped" not in res:
                import logging
                logging.getLogger(__name__).warning(
                    "EURC phantom scrub ran: delta $%s across %d bots, %s sells repriced; backup=%s",
                    res.get("total_delta"),
                    len(res.get("restored_per_bot", {})),
                    res.get("files"),
                    res.get("backup"),
                )
        except Exception as e:  # never break boot on a migration error
            import logging
            logging.getLogger(__name__).error(
                "EURC phantom scrub skipped (non-fatal error): %s", e
            )

    def _maybe_cleanup_phantom_backups(self) -> None:
        """One-shot removal of the verified-good GIGA/EURC scrub backups that
        pushed the Railway volume to 80% (2026-05-27). Sentinel-guarded; only
        touches the two known backup prefixes, never a future scrub's backup.
        Wrapped so a cleanup error can NEVER break the trading boot."""
        try:
            import sys
            root = str(Path(__file__).resolve().parent.parent)  # repo root
            if root not in sys.path:
                sys.path.insert(0, root)
            from scripts.cleanup_phantom_backups import cleanup
            res = cleanup(self.data_dir)
            if "skipped" not in res and res.get("count"):
                import logging
                logging.getLogger(__name__).warning(
                    "Phantom-backup cleanup ran: removed %d dir(s) %s",
                    res.get("count"), res.get("removed"),
                )
        except Exception as e:  # never break boot on a housekeeping error
            import logging
            logging.getLogger(__name__).error(
                "Phantom-backup cleanup skipped (non-fatal error): %s", e
            )

    def _maybe_cleanup_cnn_dataset(self) -> None:
        """One-shot removal of /data/cnn_dataset (~2.5GB of ChartCNN forward
        training data) that pushed the Railway volume to 80% (2026-05-27). The
        CNN is not gated on in live trading and the collector is now disabled by
        default, so this data is dead weight. Sentinel-guarded; the enforced rug
        filter loads its model from the in-repo models/ dir, not /data, so this
        is trading-safe. Wrapped so a cleanup error can NEVER break the boot."""
        try:
            import sys
            root = str(Path(__file__).resolve().parent.parent)  # repo root
            if root not in sys.path:
                sys.path.insert(0, root)
            from scripts.cleanup_cnn_dataset import cleanup
            res = cleanup(self.data_dir)
            if "skipped" not in res and res.get("existed"):
                import logging
                logging.getLogger(__name__).warning(
                    "CNN-dataset cleanup ran: removed %s", res.get("target"),
                )
        except Exception as e:  # never break boot on a housekeeping error
            import logging
            logging.getLogger(__name__).error(
                "CNN-dataset cleanup skipped (non-fatal error): %s", e
            )

    def _maybe_reconcile_positions(self) -> None:
        """One-shot capital reconcile for the position-persistence fix
        (2026-05-27). Returns the stuck in_flight from restart-orphaned positions
        to balance and clears the (now authoritative) open_positions book, so bots
        start flat and the fixed persistence keeps the book correct going forward.
        Runs before any bot loads its capital snapshot. Sentinel-guarded; backs up
        bot_state; never breaks boot."""
        try:
            import sys
            root = str(Path(__file__).resolve().parent.parent)  # repo root
            if root not in sys.path:
                sys.path.insert(0, root)
            from scripts.reconcile_positions import reconcile
            res = reconcile(self.data_dir)
            if "skipped" not in res and res.get("bots_reconciled"):
                import logging
                logging.getLogger(__name__).warning(
                    "Position reconcile ran: released $%s of stuck in_flight across %d bots",
                    res.get("total_released"), res.get("bots_reconciled"),
                )
        except Exception as e:  # never break boot on a migration error
            import logging
            logging.getLogger(__name__).error(
                "Position reconcile skipped (non-fatal error): %s", e
            )

    def _maybe_reset_cap2k_pnl(self) -> None:
        """One-shot P&L reset for the cap2k_* bots after their entry logic was
        changed (broad -> dip-family triggers + filter relaxations, 2026-05-27).
        Drops their old-config trades and resets state to fresh $2000 so the
        sizing/exit experiment measures the new entry only. Sentinel-guarded;
        backs up; never breaks boot."""
        try:
            import sys
            root = str(Path(__file__).resolve().parent.parent)  # repo root
            if root not in sys.path:
                sys.path.insert(0, root)
            from scripts.reset_cap2k_pnl import reset
            res = reset(self.data_dir)
            if "skipped" not in res and res.get("reset_bots"):
                import logging
                logging.getLogger(__name__).warning(
                    "cap2k P&L reset ran: reset %d bots %s, dropped trades %s",
                    len(res.get("reset_bots", [])), res.get("reset_bots"), res.get("dropped"),
                )
        except Exception as e:  # never break boot on a migration error
            import logging
            logging.getLogger(__name__).error(
                "cap2k P&L reset skipped (non-fatal error): %s", e
            )

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
            self._atomic_write(self._trades_path, json.dumps(existing))

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

    @staticmethod
    def _atomic_write(path, text: str) -> None:
        """Write via temp + os.replace so a crash mid-write can't truncate the file
        (a direct write_text that crashes leaves a corrupt JSON → next boot falls back
        to [] and overwrites the whole ledger empty). 2026-05-27 audit."""
        import os
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)

    def save_bot_state(self, bot_id: str, state: dict) -> None:
        path = self.data_dir / "bot_state" / f"{bot_id}.json"
        with self._lock:
            self._atomic_write(path, json.dumps(state, indent=2))

    def load_bot_state(self, bot_id: str) -> Optional[dict]:
        path = self.data_dir / "bot_state" / f"{bot_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())
