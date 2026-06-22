from __future__ import annotations
import asyncio
import json
import os
import threading
from pathlib import Path
from typing import Optional


def _offload_enabled() -> bool:
    """LEDGER_WRITE_OFFLOAD gate (default ON). Set to off/0/false/no to restore
    the pure-synchronous write path without a redeploy."""
    return os.environ.get("LEDGER_WRITE_OFFLOAD", "on").strip().lower() not in (
        "off", "0", "false", "no", "")


def _append_enabled() -> bool:
    """LEDGER_APPEND_MODE gate (default OFF). When ON, record_trade does an O(1)
    append (one JSONL line + in-memory list) instead of re-reading + re-serializing
    the WHOLE trades_multi.json every fill. The old path's pure-Python json.dumps of
    the growing ledger held the GIL ~12s inside a to_thread, FREEZING the event loop
    (cgroup nr_throttled=0 proved GIL contention, not CPU quota) — the loop-lag root
    cause. Reversible: unset the env flag."""
    return os.environ.get("LEDGER_APPEND_MODE", "off").strip().lower() in (
        "on", "1", "true", "yes")


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
        self._trades_jsonl_path = self.data_dir / "trades_multi.jsonl"
        self._trades_mem = None   # append-mode in-memory ledger (lazy-loaded)
        self._lock = threading.Lock()
        self._maybe_split_legacy()
        self._maybe_scrub_giga_phantom()
        self._maybe_scrub_eurc_phantom()
        self._maybe_scrub_cdof_phantom()
        self._maybe_scrub_go_phantom()
        self._maybe_cleanup_phantom_backups()
        self._maybe_cleanup_cnn_dataset()
        self._maybe_reconcile_positions()
        self._maybe_reset_cap2k_pnl()
        self._maybe_retire_dashboard_bots()

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

    def _maybe_retire_dashboard_bots(self) -> None:
        """One-shot removal of 14 retired bots' state files from the dashboard.

        The bots were disabled in config; this clears their lingering bot_state
        snapshots so they vanish from /api/bots (which globs bot_state/).
        Sentinel-guarded inside retire(); never breaks boot.
        """
        try:
            import sys
            root = str(Path(__file__).resolve().parent.parent)
            if root not in sys.path:
                sys.path.insert(0, root)
            from scripts.retire_dashboard_bots import retire
            res = retire(self.data_dir)
            if "skipped" not in res and res.get("n"):
                import logging
                logging.getLogger(__name__).warning(
                    "Dashboard retire ran: removed %d state files (%s)",
                    res.get("n"), res.get("removed"),
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                "Dashboard retire skipped (non-fatal error): %s", e
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

    def _maybe_scrub_cdof_phantom(self) -> None:
        """One-shot reversal of the 2026-06-08 CDOF phantom-WIN profit.

        champion_defender_v3 + baseline_v1 booked +$2,456 across 4 sells when a
        deploy restart cold-started the price feed and the exit-guard SEED path
        blind-accepted a 62x phantom CDOF print (root cause patched in
        core/exit_price_guard.py). Repriced to breakeven here, before any bot reads
        its capital snapshot. Sentinel-guarded inside scrub(); wrapped so a
        migration error can NEVER break the trading boot.
        """
        try:
            import sys
            root = str(Path(__file__).resolve().parent.parent)  # repo root
            if root not in sys.path:
                sys.path.insert(0, root)
            from scripts.scrub_cdof_phantom import scrub
            res = scrub(self.data_dir)
            if "skipped" not in res:
                import logging
                logging.getLogger(__name__).warning(
                    "CDOF phantom scrub ran: delta $%s across %d bots, %s sells repriced; backup=%s",
                    res.get("total_delta"),
                    len(res.get("restored_per_bot", {})),
                    res.get("files"),
                    res.get("backup"),
                )
        except Exception as e:  # never break boot on a migration error
            import logging
            logging.getLogger(__name__).error(
                "CDOF phantom scrub skipped (non-fatal error): %s", e
            )

    def _maybe_scrub_go_phantom(self) -> None:
        """One-shot reversal of the 2026-06-08 GO phantom-LOSS prints (broken feed
        booked -99.9% 'rug' stops on the then-unguarded legacy path; GO did not rug —
        price recovered after every print). Repriced to breakeven here before bots load.
        Sentinel-guarded; never breaks boot."""
        try:
            import sys
            root = str(Path(__file__).resolve().parent.parent)
            if root not in sys.path:
                sys.path.insert(0, root)
            from scripts.scrub_go_phantom import scrub
            res = scrub(self.data_dir)
            if "skipped" not in res:
                import logging
                logging.getLogger(__name__).warning(
                    "GO phantom scrub ran: restored $%s across %d bots, %s sells repriced; backup=%s",
                    res.get("total_restored"),
                    len(res.get("restored_per_bot", {})),
                    res.get("files"),
                    res.get("backup"),
                )
        except Exception as e:
            import logging
            logging.getLogger(__name__).error(
                "GO phantom scrub skipped (non-fatal error): %s", e
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

    def _ensure_trades_loaded(self) -> None:
        """Append-mode: load the ledger into memory ONCE (base array + any JSONL
        appends), then compact (fold the JSONL into the base array + clear it) so
        the sidecar stays bounded to this session. Idempotent; one-time O(n) work
        at boot (off the hot fill path)."""
        if self._trades_mem is not None:
            return
        with self._lock:
            if self._trades_mem is not None:
                return
            mem = []
            if self._trades_path.exists():
                try:
                    mem = json.loads(self._trades_path.read_text())
                    for t in mem:
                        if "bot_id" not in t:
                            t["bot_id"] = "baseline_v1"
                except json.JSONDecodeError:
                    mem = []
            had_jsonl = False
            if self._trades_jsonl_path.exists():
                try:
                    for _ln in self._trades_jsonl_path.read_text().splitlines():
                        _ln = _ln.strip()
                        if _ln:
                            mem.append(json.loads(_ln))
                            had_jsonl = True
                except Exception:
                    pass
            self._trades_mem = mem
            # Boot compaction: fold prior-session JSONL appends into the base
            # array + truncate the sidecar so it never grows across restarts.
            if had_jsonl:
                try:
                    self._atomic_write(self._trades_path, json.dumps(mem))
                    self._trades_jsonl_path.write_text("")
                except Exception:
                    pass

    def record_trade(self, trade: dict, bot_id: str) -> None:
        record = dict(trade)
        record["bot_id"] = bot_id
        if _append_enabled():
            # O(1) durable append: one JSONL line + in-memory list. No whole-file
            # read/dump -> no GIL-holding json.dumps -> no event-loop freeze.
            self._ensure_trades_loaded()
            with self._lock:
                self._trades_mem.append(record)
                try:
                    with open(self._trades_jsonl_path, "a") as f:
                        f.write(json.dumps(record) + "\n")
                except Exception:
                    pass
            return
        with self._lock:
            existing = []
            if self._trades_path.exists():
                try:
                    existing = json.loads(self._trades_path.read_text())
                except json.JSONDecodeError:
                    existing = []
            existing.append(record)
            self._atomic_write(self._trades_path, json.dumps(existing))

    def _read_disk_ledger(self) -> list[dict]:
        """Append-mode read: base array (frozen post-boot) + JSONL sidecar (this
        session's appends), both mtime-cached. Any instance reading the same
        files gets the current union, so readers never go stale. base/sidecar
        are disjoint (compaction folds + truncates only at boot) -> no dup."""
        out: list[dict] = []
        try:
            if self._trades_path.exists():
                mt = self._trades_path.stat().st_mtime
                c = getattr(self, "_base_cache", None)
                if c is not None and c[0] == mt:
                    base = c[1]
                else:
                    base = json.loads(self._trades_path.read_text())
                    for t in base:
                        if "bot_id" not in t:
                            t["bot_id"] = "baseline_v1"
                    self._base_cache = (mt, base)
                out.extend(base)
        except Exception:
            pass
        try:
            if self._trades_jsonl_path.exists():
                mt = self._trades_jsonl_path.stat().st_mtime
                c = getattr(self, "_jsonl_cache", None)
                if c is not None and c[0] == mt:
                    side = c[1]
                else:
                    side = [json.loads(_ln) for _ln in
                            self._trades_jsonl_path.read_text().splitlines() if _ln.strip()]
                    self._jsonl_cache = (mt, side)
                out.extend(side)
        except Exception:
            pass
        return out

    def load_trades(self, bot_id: Optional[str] = None) -> list[dict]:
        if _append_enabled():
            # Disk-truth read so a READER instance (e.g. the dashboard's store,
            # a SEPARATE object from the bot's) stays current with the WRITER's
            # appends. Returning the per-instance in-memory `_trades_mem` made
            # the reader stale — it loaded once at boot and never replayed the
            # JSONL sidecar the bot appends to, so /api/trades froze at the last
            # full base write (2026-06-22 fix). base + sidecar are disjoint
            # (boot compaction folds + truncates only once), so no double-count.
            # Both reads are mtime-cached: the frozen base is a cache-hit after
            # boot; only the small growing sidecar re-parses on change.
            self._ensure_trades_loaded()
            data = self._read_disk_ledger()
            if bot_id is None:
                return data
            return [t for t in data if t.get("bot_id") == bot_id]
        if not self._trades_path.exists():
            return []
        # mtime-keyed cache (2026-06-02 cost fix): load_trades is called on every ~15s
        # dashboard poll across ~9 call sites and re-parsed the full (growing) trade DB
        # each time — the heaviest repeated CPU+disk path. Cache the parse keyed by file
        # mtime; record_trade rewrites the file (mtime changes) so the cache self-invalidates.
        try:
            mtime = self._trades_path.stat().st_mtime
        except OSError:
            mtime = None
        _cache = getattr(self, "_trades_cache", None)
        if _cache is not None and _cache[0] == mtime:
            data = _cache[1]
        else:
            data = json.loads(self._trades_path.read_text())
            for t in data:
                if "bot_id" not in t:
                    t["bot_id"] = "baseline_v1"
            self._trades_cache = (mtime, data)
        if bot_id is None:
            return data
        return [t for t in data if t.get("bot_id") == bot_id]

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

    # -- Loop-freeze fix (2026-06-19) ----------------------------------------
    # record_trade()/save_bot_state() do O(history) synchronous disk work under
    # self._lock. Called inline from the async buy/sell fill paths, a slow write
    # (the ledger grows unbounded) froze the event loop for tens of seconds on
    # fill clusters (57.5s on a 4-sell, 110.8s on a 7-bot BUY) — starving the
    # dashboard + fast-watch tick. These async wrappers push the blocking write
    # onto a worker thread (asyncio.to_thread) so it can't block the loop. The
    # sync methods are unchanged: the SAME self._lock (threading.Lock) is taken
    # INSIDE the worker thread, so the write stays atomic, durable, and
    # serialized. Order is preserved by awaiting in the caller.
    #
    # Gated behind LEDGER_WRITE_OFFLOAD (default on). Off => pure-sync (the old
    # behavior), instantly reversible without a redeploy.

    @staticmethod
    def _offload_write_sync(fn, *args, **kwargs):
        """Run a blocking write `fn(*args, **kwargs)` synchronously on the
        calling thread. The fallback path when there is no running event loop
        (non-async callers and tests)."""
        return fn(*args, **kwargs)

    async def _offload_write(self, fn, *args, **kwargs):
        """Await `fn(*args, **kwargs)` off the event loop via asyncio.to_thread
        when offload is enabled AND a loop is running; otherwise call it
        synchronously. Fail-safe: the write ALWAYS happens. The threading.Lock
        is acquired inside `fn` (in the worker thread), not here on the loop."""
        if not _offload_enabled():
            return self._offload_write_sync(fn, *args, **kwargs)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — fall back to a direct synchronous call.
            return self._offload_write_sync(fn, *args, **kwargs)
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def record_trade_async(self, trade: dict, bot_id: str) -> None:
        """Offloaded record_trade. Durable + lock-serialized; see _offload_write."""
        if _append_enabled():
            # Append-mode write is O(1) (one JSONL line) — GIL-cheap, no whole-file
            # dump — so run inline; no to_thread needed (to_thread wouldn't have
            # helped the old path anyway: json.dumps holds the GIL).
            return self.record_trade(trade, bot_id=bot_id)
        await self._offload_write(self.record_trade, trade, bot_id=bot_id)

    async def save_bot_state_async(self, bot_id: str, state: dict) -> None:
        """Offloaded save_bot_state. Durable + lock-serialized; see _offload_write."""
        await self._offload_write(self.save_bot_state, bot_id, state)

    def load_bot_state(self, bot_id: str) -> Optional[dict]:
        path = self.data_dir / "bot_state" / f"{bot_id}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text())
