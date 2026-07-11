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


def _rotate_days() -> float:
    """LEDGER_ROTATE_DAYS knob (default 21). Rows whose trade time is older than
    this many days are rotated OUT of the loaded base at boot compaction into
    trades_multi_archive.jsonl (append-only, NEVER loaded at boot) — the #1
    Railway RAM cut (memory re-audit #496: the parsed ledger IS the service's
    ~3GB RSS). 0 / off / no / false / empty disables rotation entirely.
    Fail-open: an unparseable value disables rotation (= load everything)."""
    raw = os.environ.get("LEDGER_ROTATE_DAYS", "21").strip().lower()
    if raw in ("off", "no", "false", ""):
        return 0.0
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _meta_keep_rows() -> int:
    """LEDGER_META_KEEP_ROWS knob (default 6000). In-memory cache rows older
    than the newest N keep every scalar field but have their ~15KB entry_meta
    dict slimmed to _META_TRIM_KEEP_KEYS (disk stays lossless) — the #2 RAM cut.
    N is aligned above /api/trades?full=1's 5000-newest cap so every row that
    endpoint can serve still carries full meta. <=0 / off disables trimming.
    Fail-open: an unparseable value disables trimming."""
    raw = os.environ.get("LEDGER_META_KEEP_ROWS", "6000").strip().lower()
    if raw in ("off", "no", "false", ""):
        return 0
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return 0


# entry_meta keys preserved by the in-memory trim. core/live_faithful_pnl.py
# reads these two booleans from EVERY closed buy (full-history), so dropping
# them would silently flip old would-block trades to "not blocked". Keeping
# two booleans costs ~100B/row vs the ~15KB full dict.
_META_TRIM_KEEP_KEYS = ("daily_halt_would_block", "reentry_cap_would_block")


def _parse_trade_time(ts):
    """ISO trade time -> aware UTC datetime, or None when missing/unparseable.
    Rotation treats None as NOT old (fail-safe: never archive a row whose age
    is unknown)."""
    from datetime import timezone as _tz
    from datetime import datetime as _dt
    if not ts:
        return None
    try:
        d = _dt.fromisoformat(str(ts).replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=_tz.utc)
        return d
    except (TypeError, ValueError):
        return None


def _trade_sig(t: dict) -> tuple:
    """Stable identity signature for a trade row (crash-recovery dedup between
    the base ledger and the rotation archive). Specific enough that two DISTINCT
    real fills can't collide: bot+token+type+microsecond time+prices+pnl."""
    return (
        t.get("bot_id"), t.get("time"), t.get("type"), t.get("token"),
        t.get("address"), t.get("pair_address"), repr(t.get("entry_price")),
        repr(t.get("pnl")), repr(t.get("pnl_pct")), repr(t.get("amount_usd")),
    )


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
        # Ledger rotation (memory re-audit #496): rows older than
        # LEDGER_ROTATE_DAYS live here, append-only, NEVER loaded at boot.
        self._trades_archive_path = self.data_dir / "trades_multi_archive.jsonl"
        # Per-bot aggregates of the archived rows (leaderboard fold source).
        self._rotation_stats_path = self.data_dir / "ledger_rotation_stats.json"
        self._trades_loaded = False   # append-mode: boot-compaction-done flag
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
        """Append-mode: perform one-time BOOT COMPACTION — fold any prior-session
        JSONL sidecar into the base array + truncate the sidecar so it stays
        bounded to this session. Idempotent; one-time O(n) work at boot (off the
        hot fill path).

        Does NOT retain the ledger in memory. Reads are served from disk via
        _read_disk_ledger (base + sidecar, both mtime-cached), so holding a full
        in-memory copy here was a dead duplicate (~0.8-1.2GB on a long session).
        The folded ledger lives only in the LOCAL `mem` here and is released when
        this method returns; afterwards a lightweight `_trades_loaded` flag marks
        boot compaction done."""
        if self._trades_loaded:
            return
        with self._lock:
            if self._trades_loaded:
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
            # Ledger ROTATION (memory re-audit #496, cut #1): fold rows older
            # than LEDGER_ROTATE_DAYS out of the base into the append-only
            # archive, so the resident _base_cache (and every boot re-parse)
            # holds only the active window. Fail-open: ANY error -> log loudly
            # and keep the full ledger (current behavior).
            rotated = False
            try:
                mem, rotated = self._rotate_ledger(mem)
            except Exception as e:
                import logging
                logging.getLogger(__name__).error(
                    "[LedgerRotation] rotation FAILED — fail-open, loading the "
                    "FULL ledger (%d rows): %s", len(mem), e)
            # Boot compaction: fold prior-session JSONL appends into the base
            # array + truncate the sidecar so it never grows across restarts.
            # STREAMED write (cut #3): per-row dumps into the temp file instead
            # of one whole-ledger json.dumps string (a ~file-size GIL-held
            # transient that glibc never returned to the OS).
            if had_jsonl or rotated:
                try:
                    self._atomic_write_stream(self._trades_path, mem)
                    self._trades_jsonl_path.write_text("")
                except Exception:
                    pass
            # Mark done and release the local ledger copy (do not retain).
            self._trades_loaded = True
            mem = None

    def _rotate_ledger(self, mem: list) -> tuple:
        """Boot-time ledger rotation (#496 cut #1). Returns (active_rows, changed).

        Rows older than LEDGER_ROTATE_DAYS move to trades_multi_archive.jsonl
        (append-only, never loaded at boot); per-bot aggregates of everything
        archived are re-derived and written to ledger_rotation_stats.json so
        stat readers (dashboard leaderboard via core/ledger_stats.sell_stats)
        report IDENTICAL since-inception totals before/after rotation.

        Safety properties:
          • NO-STRADDLE: a (bot_id, token) group is archived only when EVERY
            row of it is older than the cutoff — position joins (leaderboard
            (token, entry_price) groups, restore_positions, live_faithful lots)
            never split across base/archive. Open-position tokens (bot_state
            books = holdings truth) are additionally protected outright.
          • IDEMPOTENT/CRASH-SAFE: stats are ALWAYS re-derived from the archive
            file itself (streamed line-by-line, per-line dedup by row signature),
            and any base row whose signature already exists in the archive is a
            crash leftover — dropped from base, counted exactly once.
          • Daily circuit breakers unaffected: boot daily-pnl re-derivation
            needs only TODAY's rows, far inside any sane LEDGER_ROTATE_DAYS.

        Caller wraps in try/except (fail-open -> full ledger). Runs under
        self._lock at boot, before any reader is served."""
        from datetime import datetime, timedelta, timezone
        days = _rotate_days()
        if days <= 0 or not mem:
            return mem, False
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)

        def _is_old(t):
            d = _parse_trade_time(t.get("time"))
            return d is not None and d < cutoff_dt

        # (bot_id, token) groups that must STAY in the base: any group with a
        # recent/unparseable-time row, plus every open position (paranoia —
        # open positions are recent by construction, but bot_state is truth).
        keep_keys = set()
        for t in mem:
            if not _is_old(t):
                keep_keys.add((t.get("bot_id"), t.get("token")))
        try:
            for p in (self.data_dir / "bot_state").glob("*.json"):
                try:
                    st = json.loads(p.read_text())
                    bid = st.get("bot_id") or p.stem
                    for pos in (st.get("open_positions") or []):
                        keep_keys.add((bid, pos.get("token")))
                except Exception:
                    continue
        except Exception:
            pass

        # Leaderboard-mirror filters for the stats snapshot (must match
        # dashboard _build_bot_rows / core/ledger_stats.sell_stats population).
        try:
            import sys
            root = str(Path(__file__).resolve().parent.parent)
            if root not in sys.path:
                sys.path.insert(0, root)
            from scripts.sp4_common import MIN_TRADE_TIMESTAMP as _min_ts
        except Exception:
            _min_ts = ""

        group_pnl: dict = {}     # (bot, token, entry_price) -> summed sell pnl
        latest_by_bot: dict = {}  # bot -> newest archived row time (reset guard)

        def _count(t):
            bid = t.get("bot_id", "baseline_v1")
            tm = t.get("time") or ""
            if tm > latest_by_bot.get(bid, ""):
                latest_by_bot[bid] = tm
            if t.get("type") != "sell":
                return
            if _min_ts and tm < _min_ts:
                return
            if "cancelled on restart" in (t.get("reason") or ""):
                return
            k = (bid, t.get("token"), t.get("entry_price"))
            group_pnl[k] = group_pnl.get(k, 0.0) + float(t.get("pnl") or 0)

        # Stream the existing archive once: signature set (dedup) + aggregates.
        arch_sigs = set()
        if self._trades_archive_path.exists():
            with open(self._trades_archive_path, encoding="utf-8") as fh:
                for ln in fh:
                    ln = ln.strip()
                    if not ln:
                        continue
                    try:
                        t = json.loads(ln)
                    except Exception:
                        continue
                    if not isinstance(t, dict):
                        continue
                    sig = _trade_sig(t)
                    if sig in arch_sigs:
                        continue  # crash-duplicated line — count once
                    arch_sigs.add(sig)
                    _count(t)

        to_archive, active, changed = [], [], False
        for t in mem:
            if _trade_sig(t) in arch_sigs:
                changed = True  # crash leftover: already archived — drop it
                continue
            if _is_old(t) and (t.get("bot_id"), t.get("token")) not in keep_keys:
                to_archive.append(t)
                changed = True
            else:
                active.append(t)
        if not changed:
            return mem, False

        # 1) Append new archive lines durably (fsync — the base rewrite that
        #    follows must never outrun the archive of the rows it drops).
        if to_archive:
            with open(self._trades_archive_path, "a", encoding="utf-8") as fh:
                for t in to_archive:
                    fh.write(json.dumps(t) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            for t in to_archive:
                _count(t)

        # 2) Reduce to per-bot aggregates + atomic snapshot write.
        bots: dict = {}

        def _bot(bid):
            return bots.setdefault(
                bid, {"pnl": 0.0, "positions": 0, "wins": 0, "latest_time": ""})

        for (bid, _tok, _ep), pnl in group_pnl.items():
            b = _bot(bid)
            b["pnl"] = round(b["pnl"] + pnl, 6)
            b["positions"] += 1
            if pnl > 0:
                b["wins"] += 1
        for bid, tm in latest_by_bot.items():
            _bot(bid)["latest_time"] = tm
        snapshot = {
            "version": 1,
            "rotate_days": days,
            "rotated_at": datetime.now(timezone.utc).isoformat(),
            "note": ("per-bot aggregates of rows archived to "
                     "trades_multi_archive.jsonl; folded into leaderboard "
                     "stats via core/ledger_stats.sell_stats"),
            "bots": bots,
        }
        self._atomic_write(self._rotation_stats_path, json.dumps(snapshot))
        self._rotation_stats_cache = None  # invalidate reader cache
        import logging
        logging.getLogger(__name__).warning(
            "[LedgerRotation] archived %d rows older than %.0fd (base %d -> %d "
            "rows; archive+stats updated: %s bots)",
            len(to_archive), days, len(mem), len(active), len(bots))
        return active, True

    def load_rotation_stats(self) -> dict:
        """Read ledger_rotation_stats.json (per-bot aggregates of archived rows).
        {} when never rotated / unreadable. mtime-cached (dashboard polls)."""
        try:
            p = self._rotation_stats_path
            if not p.exists():
                return {}
            mt = p.stat().st_mtime
            c = getattr(self, "_rotation_stats_cache", None)
            if c is not None and c[0] == mt:
                return c[1]
            d = json.loads(p.read_text())
            d = d if isinstance(d, dict) else {}
            self._rotation_stats_cache = (mt, d)
            return d
        except Exception:
            return {}

    def record_trade(self, trade: dict, bot_id: str) -> None:
        record = dict(trade)
        record["bot_id"] = bot_id
        if _append_enabled():
            # O(1) durable append: a single JSONL line. No in-memory full-ledger
            # copy (reads come from disk via _read_disk_ledger, mtime-cached) and
            # no whole-file read/dump -> no GIL-holding json.dumps -> no loop freeze.
            self._ensure_trades_loaded()  # idempotent one-time boot compaction
            with self._lock:
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
                    # #496 cut #2: cache old rows entry_meta-slim (disk lossless;
                    # trades_multi.json is append-ordered so [:-keep] = oldest).
                    self._trim_entry_meta(base)
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
            # appends. A per-instance in-memory ledger made the reader stale —
            # it loaded once at boot and never replayed the JSONL sidecar the bot
            # appends to, so /api/trades froze at the last full base write
            # (2026-06-22 fix). It was also a dead RAM duplicate, now removed
            # (2026-06-28). base + sidecar are disjoint
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
            # #496 cut #2 (read-cache only; record_trade re-parses the file
            # directly before appending, so disk writes stay lossless).
            self._trim_entry_meta(data)
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

    @staticmethod
    def _atomic_write_stream(path, rows) -> None:
        """Atomic JSON-array write, STREAMED per row (#496 cut #3): the boot
        compaction's whole-ledger json.dumps built a ~file-size Python string
        (a 0.5-1.0GB transient on the deployed ledger) that fragmented the heap
        floor. Per-row dumps straight into the temp file keep the peak at one
        row. Same output file semantics (temp + os.replace) as _atomic_write;
        byte-diff only in separators (',' vs ', '), JSON-identical."""
        import os
        path = Path(path)
        tmp = path.with_name(path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("[")
            for i, r in enumerate(rows):
                if i:
                    f.write(",")
                f.write(json.dumps(r))
            f.write("]")
        os.replace(tmp, path)

    @staticmethod
    def _trim_entry_meta(rows: list) -> None:
        """#496 cut #2: slim the ~15KB entry_meta dict off IN-MEMORY cache rows
        older than the newest LEDGER_META_KEEP_ROWS (disk stays lossless — this
        runs only on freshly parsed read-cache rows, never on anything written
        back). The whitelist keys survive (live_faithful_pnl reads them across
        full history); '_meta_trimmed' marks the slim dict for consumers."""
        keep = _meta_keep_rows()
        if keep <= 0 or len(rows) <= keep:
            return
        for t in rows[:-keep]:
            if not isinstance(t, dict):
                continue
            em = t.get("entry_meta")
            if not isinstance(em, dict) or em.get("_meta_trimmed"):
                continue
            slim = {k: em[k] for k in _META_TRIM_KEEP_KEYS if k in em}
            slim["_meta_trimmed"] = True
            t["entry_meta"] = slim

    def save_bot_state(self, bot_id: str, state: dict) -> None:
        path = self.data_dir / "bot_state" / f"{bot_id}.json"
        with self._lock:
            # No-clobber guard (2026-06-29 deploy-amnesia fix): a capital-only
            # save (a dict that OMITS the "open_positions" key — e.g. the
            # new-bot seed at dip_scanner.py) must NOT erase a populated
            # open_positions book already on disk. Only an EXPLICIT
            # open_positions value (incl. an empty list = a real close-to-flat)
            # is honored. Fail-open: a read error falls through to the raw write.
            if isinstance(state, dict) and "open_positions" not in state:
                try:
                    if path.exists():
                        prior = json.loads(path.read_text())
                        prior_op = prior.get("open_positions") if isinstance(prior, dict) else None
                        if prior_op:
                            state = dict(state)
                            state["open_positions"] = prior_op
                            # Carry the sibling position-book keys too, so the
                            # preserved positions keep their cooldown / buy-count
                            # context across a capital-only save.
                            for _k in ("last_close_times", "token_buys"):
                                if _k not in state and _k in prior:
                                    state[_k] = prior[_k]
                except Exception:
                    pass
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
