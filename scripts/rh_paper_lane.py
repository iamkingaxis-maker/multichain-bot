# scripts/rh_paper_lane.py
"""Robinhood Chain PAPER lane v1 — the young-dip strategy on RH rails.

Wires the three shipped RH components into one per-session paper trader:
  detection  = sequencer firehose (scripts/rh_firehose_feed.py, ~0.9s lag)
  gates      = young-dip essence: quote-price dip trigger + demand turn +
               retrace-micro sell-distribution block (core/retrace_microstructure,
               same rip_tape schema) + honeypot (core/rh_honeypot, FAIL-CLOSED,
               mandatory before ANY entry) + liq floor
  exits      = core/per_bot_position_manager (the SAME exit engine as the
               Solana probe: tp1 6%/0.75, tp2 12%, trail, hard stop, bail)
  fills      = QuoterV2 quotes via core/rh_execution.RhExecutor (keyless
               eth_call: real pool state, fee + impact included) — the honest
               paper fill, and the exact call the live path would make.

PAPER ONLY: RhExecutor without RH_PRIVATE_KEY cannot sign (RhPaperModeError);
this script never calls the swap methods at all — quotes only.

Latency parity mandate (AxiS 2026-07-10): every paper fill records the full
chain detect->fill: trigger lag_secs (firehose) + decision + quote round-trip.

Ledger: scratchpad/robinhood_tapes/rh_paper_trades.jsonl (one JSON per event).
Usage: python scripts/rh_paper_lane.py [max_minutes]
"""
import asyncio
import json
import os
import queue
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

from rh_firehose_feed import (  # noqa: E402
    Firehose, WS_URL, RH_CHAIN_ID, RPC_DEFAULT, LOOKBACK_H, MAINT_SECS,
)
from rh_chain_feed import Feed, _append, iso_utc, pctl  # noqa: E402
from core.retrace_microstructure import retrace_micro_eval  # noqa: E402
from core.bot_config import BotConfig  # noqa: E402
from core.per_bot_position_manager import PerBotPositionManager  # noqa: E402

OUT_DIR = os.path.join("scratchpad", "robinhood_tapes")
LEDGER = os.path.join(OUT_DIR, "rh_paper_trades.jsonl")
STATE = os.path.join(OUT_DIR, "rh_lane_state.json")   # open positions survive restarts
# post-exit tail instrumentation (2026-07-10 trail-width analysis ask):
# every full close queues a +6h price check; results let the abandoned-tail
# question rerun from LOCAL data (no GT dependence). Pending file is durable —
# checks due after a session ends complete in the NEXT session.
POSTEXIT_PENDING = os.path.join(OUT_DIR, "rh_postexit_pending.jsonl")
POSTEXIT_RESULTS = os.path.join(OUT_DIR, "rh_postexit.jsonl")
POSTEXIT_DELAY_S = 6 * 3600.0
POSTEXIT_SWEEP_S = 600.0

# ── lane config (mirrors the Solana young probe where meaningful) ───────────
ENTRY_USD = 25.0            # probe sizing
MAX_CONCURRENT = 2          # probe cap
# PAPER = DATA (AxiS 2026-07-10: "whats the point of having a loss limit on
# paper? we need data"): a live-style daily stop starves the sample — any
# stop level can be applied to collected data retrospectively, but unsampled
# trades are gone forever. The remaining halt is a RUNAWAY-BUG backstop
# (broken loop machine-gunning losses), not a market-risk control.
DAILY_LOSS_STOP_USD = float(os.environ.get("RH_PAPER_DAILY_STOP", "-250"))
# Rug-guard port (2026-07-10 session-1 autopsy: Halp -90% + TREAT -17% = rugs
# that passed the unguarded v1 gates; the Solana probe's edge IS its guards):
MIN_LIQ_USD = 30_000.0      # PARITY with the live probe (was 10k -> rug pond)
MIN_POOL_AGE_H = 1.0        # dev-armed launch window: no fresh-pool entries
LP_DRAIN_WINDOW_S = 900.0   # liq-delta lookback (mirrors lp_delta_15m_pct)
LP_DRAIN_ENTRY_PCT = -15.0  # recent drain >= 15% -> no entry (RH v1: no data
                            # yet to refute a veto here, unlike Solana)
LP_DRAIN_EXIT_PCT = -30.0   # liq collapses while holding -> immediate full exit
DIP_TRIGGER_PCT = -12.0     # entry: price >=12% off the 10-min high
PRICE_WINDOW_S = 600.0
DEMAND_WINDOW_S = 30.0      # demand turn: net inflow over last 30s
DEMAND_MIN_BUY_USD = 50.0
HOT_TTL_S = 120.0           # pool is "hot" if traded within 2 min
MAX_HOT_QUOTES = 8          # quote budget per cycle (~130ms/call)
STRAT_TICK_S = 2.0
REENTRY_COOLDOWN_S = 300.0
GAS_USD_PER_SIDE = 0.01     # measured RH gas ~ $0.005; round up


# ── pure signal logic (unit-tested, no network) ─────────────────────────────
def price_from_quote(amount_in_wei: int, amount_out_atomic: int,
                     token_decimals: int) -> float:
    """RhQuote buy -> ETH per token, decimals-adjusted. 0.0 on empty quote."""
    if not amount_in_wei or not amount_out_atomic or token_decimals is None:
        return 0.0
    return (amount_in_wei / 1e18) / (amount_out_atomic / 10 ** token_decimals)


def dip_pct(series: list, now: float, window_s: float = PRICE_WINDOW_S):
    """[(ts, price)] -> pct off the window max, using the latest point.
    None when the window has <3 points (no basis for a dip claim)."""
    pts = [(t, p) for t, p in series if now - t <= window_s and p > 0]
    if len(pts) < 3:
        return None
    hi = max(p for _, p in pts)
    cur = pts[-1][1]
    if hi <= 0:
        return None
    return (cur - hi) / hi * 100.0


def demand_turn(rows: list, now: float, window_s: float = DEMAND_WINDOW_S,
                min_buy_usd: float = DEMAND_MIN_BUY_USD) -> bool:
    """Tape rows -> True when recent flow is net-positive AND buys are real
    dollars (a dip nobody is buying is a knife, not an entry)."""
    buys = sells = 0.0
    for r in rows:
        ts = r.get("_epoch")
        if ts is None or now - ts > window_s:
            continue
        v = float(r.get("volume_usd") or 0)
        if r.get("kind") == "buy":
            buys += v
        elif r.get("kind") == "sell":
            sells += v
    return buys >= min_buy_usd and (buys - sells) > 0


def sell_slice(remaining_frac: float, req_frac: float):
    """Exit-engine sell_fraction semantics: fraction of the ORIGINAL size,
    clamped to what's left. Returns (frac_of_original_sold, new_remaining).
    Cost basis MUST use the clamped fraction — booking the requested fraction
    overstates cost on post-TP1 exits (the BILLY -75% phantom, 2026-07-10)."""
    f = max(0.0, min(req_frac, remaining_frac))
    return f, remaining_frac - f


def lp_drain_pct(liq_series, now: float, window_s: float = LP_DRAIN_WINDOW_S):
    """[(ts, liq_usd)] -> pct change from the window's max liq to the latest
    sample (0 or negative = drain). None when <2 in-window samples (fail-OPEN
    for the entry stamp, but the entry gate then simply has no drain signal)."""
    pts = [(t, x) for t, x in liq_series if now - t <= window_s and x and x > 0]
    if len(pts) < 2:
        return None
    hi = max(x for _, x in pts)
    cur = pts[-1][1]
    if hi <= 0:
        return None
    return (cur - hi) / hi * 100.0


def entry_verdict(dip, demand, micro, liq_usd, honeypot_ok,
                  open_count, cooldown_ok, daily_pnl_usd,
                  age_h=None, drain_pct=None) -> dict:
    """Combine every gate -> {enter: bool, blocks: [..]} (all reasons kept
    so the ledger shows WHY, not just whether)."""
    blocks = []
    if dip is None or dip > DIP_TRIGGER_PCT:
        blocks.append("no_dip")
    if not demand:
        blocks.append("no_demand_turn")
    if micro and micro.get("avoid_block"):
        blocks.append("retrace_micro_avoid")
    if liq_usd < MIN_LIQ_USD:
        blocks.append("liq_floor")
    if age_h is not None and age_h < MIN_POOL_AGE_H:
        blocks.append("age_floor")
    if drain_pct is not None and drain_pct <= LP_DRAIN_ENTRY_PCT:
        blocks.append("lp_drain")
    if not honeypot_ok:
        blocks.append("honeypot")
    if open_count >= MAX_CONCURRENT:
        blocks.append("max_concurrent")
    if not cooldown_ok:
        blocks.append("cooldown")
    if daily_pnl_usd <= DAILY_LOSS_STOP_USD:
        blocks.append("daily_loss_stop")
    return {"enter": not blocks, "blocks": blocks}


# ── the lane ─────────────────────────────────────────────────────────────────
class PaperLane:
    def __init__(self, feed: Feed, executor=None, registry=None):
        self.feed = feed
        self.ex = executor          # RhExecutor (lazy if None)
        # pool -> {token, fee, ...}: feed.watch does NOT carry the token
        # (candidate dict is popped at promotion) — the Firehose registry does.
        self.registry = registry if registry is not None else {}
        self.q = queue.Queue()      # (pool, row) from the firehose hook
        self.tape = {}              # pool -> [rows] (rolling)
        self.prices = {}            # pool -> [(ts, price_eth)]
        self.liq_hist = {}          # pool -> [(ts, liq_usd)] (lp-drain guard)
        self.last_trade = {}        # pool -> ts (hot tracking)
        self.decimals = {}          # token -> int
        self.honeypot = {}          # token -> verdict dict
        self.last_exit = {}         # pool -> ts (re-entry cooldown)
        self.daily_pnl_usd = 0.0
        self.n_entries = 0
        self.n_exits = 0
        self.n_quotes = 0           # fire-evidence: quotes actually made
        self.n_evals = 0            # fire-evidence: entry gates actually run
        self.block_hist = {}        # block reason -> count (why we're not firing)
        cfg = BotConfig(bot_id="rh_paper_young", display_name="RH paper young",
                        tp1_pct=6.0, tp1_sell_fraction=0.75, tp2_pct=12.0)
        self.pm = PerBotPositionManager(cfg)
        self.pos_meta = {}          # pool -> {qty, token, sym, entry stamps}
        self.stop = threading.Event()

    # ── durable open positions (parity with the Solana bot_state stores:
    # a crash/restart mid-hold must never orphan a position) ────────────────
    def save_state(self):
        try:
            tmp = STATE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"pos_meta": self.pos_meta,
                           "daily_pnl_usd": self.daily_pnl_usd,
                           "day": time.strftime("%Y-%m-%d", time.gmtime()),
                           "pm_state": self.pm.to_state_list()}, f)
            os.replace(tmp, STATE)
        except Exception as e:
            print(f"[rh-paper] state save failed: {e}", flush=True)

    def restore_state(self):
        """Reload open positions from a prior session/crash. Same-day daily
        P&L carries over (it's a day counter, not a session counter)."""
        try:
            if not os.path.exists(STATE):
                return
            st = json.load(open(STATE, encoding="utf-8"))
            self.pos_meta = st.get("pos_meta") or {}
            if st.get("day") == time.strftime("%Y-%m-%d", time.gmtime()):
                self.daily_pnl_usd = float(st.get("daily_pnl_usd") or 0.0)
            n = self.pm.load_state_list(st.get("pm_state") or [])
            # drop meta whose pm twin didn't restore (and vice versa)
            self.pos_meta = {p: m for p, m in self.pos_meta.items()
                             if self.pm.get_position(p) is not None}
            if self.pos_meta or n:
                print(f"[rh-paper] restored {len(self.pos_meta)} open "
                      f"position(s), day_pnl={self.daily_pnl_usd:+.2f}",
                      flush=True)
        except Exception as e:
            print(f"[rh-paper] state restore failed (starting clean): {e}",
                  flush=True)

    # firehose hook (ws thread) — cheap, non-blocking
    def on_row(self, pool: str, row: dict):
        self.q.put((pool, row))

    def _token_for(self, pool: str):
        """Token address for a pool: firehose registry first (authoritative),
        then open-position meta, then feed.watch (future-proof fallback)."""
        return ((self.registry.get(pool) or {}).get("token")
                or (self.pos_meta.get(pool) or {}).get("token")
                or (self.feed.watch.get(pool) or {}).get("token"))

    def _executor(self):
        if self.ex is None:
            from core.rh_execution import RhExecutor
            self.ex = RhExecutor()
            self.ex.connect()
        return self.ex

    def _token_decimals(self, token: str) -> int:
        if token not in self.decimals:
            try:
                self.decimals[token] = self._executor().token_decimals(token)
            except Exception:
                self.decimals[token] = 18
        return self.decimals[token]

    def _honeypot_ok(self, token: str) -> bool:
        v = self.honeypot.get(token)
        if v is None:
            from core.rh_honeypot import simulate_sell
            v = simulate_sell(token, executor=self._executor())
            self.honeypot[token] = v
            if not v.get("sellable"):
                print(f"[rh-paper] HONEYPOT BLOCK {token[:10]} "
                      f"({v.get('reason','')[:60]})", flush=True)
        return bool(v.get("sellable"))

    def _drain(self, now: float):
        while True:
            try:
                pool, row = self.q.get_nowait()
            except queue.Empty:
                return
            row["_epoch"] = now  # seen time; good enough for 30s flow windows
            buf = self.tape.setdefault(pool, [])
            buf.append(row)
            if len(buf) > 400:
                del buf[:200]
            self.last_trade[pool] = now

    def _quote_hot(self, now: float):
        """Refresh quote-derived price: OPEN POSITIONS FIRST and unbudgeted
        (exit-blindness fix, 2026-07-10 trail-width analysis: positions were
        sorted into the shared budget by trade recency, so a quiet position
        could be crowded out of quotes exactly when its exit mattered —
        LOCKIN gapped through its trail to the hard stop). Entry candidates
        then fill the remaining budget."""
        hot = [p for p, t in self.last_trade.items()
               if now - t <= HOT_TTL_S and p in self.feed.watch
               and p not in self.pos_meta]
        hot.sort(key=lambda p: -(self.last_trade.get(p, 0)))
        budget = max(0, MAX_HOT_QUOTES - len(self.pos_meta))
        for pool in list(self.pos_meta) + hot[:budget]:
            token = self._token_for(pool)
            if not token:
                continue
            try:
                q = self._executor().quote_buy(token, int(ENTRY_USD / max(
                    self.feed.eth_price or 1e9, 1e-9) * 1e18))
                if q and q.amount_out:
                    self.n_quotes += 1
                    px = price_from_quote(q.amount_in, q.amount_out,
                                          self._token_decimals(token))
                    if px > 0:
                        s = self.prices.setdefault(pool, [])
                        s.append((now, px))
                        if len(s) > 600:
                            del s[:300]
            except Exception as e:
                print(f"[rh-paper] quote {pool[:10]} {type(e).__name__}",
                      flush=True)

    def _sample_liq(self, now: float):
        """Feed the lp-drain tracker from the maintenance liq refresher."""
        for pool in set(list(self.last_trade) + list(self.pos_meta)):
            w = self.feed.watch.get(pool)
            if not w:
                continue
            liq = float(w.get("liq") or 0)
            if liq <= 0:
                continue
            h = self.liq_hist.setdefault(pool, [])
            if not h or h[-1][1] != liq or now - h[-1][0] > 60:
                h.append((now, liq))
                if len(h) > 200:
                    del h[:100]

    def _pool_age_h(self, w):
        try:
            return self.feed.age_h(w["created_block"])
        except Exception:
            return None  # unknown age -> gate has no signal (fail-open)

    def _consider_entries(self, now: float):
        for pool, series in list(self.prices.items()):
            if pool in self.pos_meta:
                continue
            w = self.feed.watch.get(pool)
            if not w:
                continue
            rows = self.tape.get(pool, [])
            d = dip_pct(series, now)
            micro = retrace_micro_eval(
                [{"kind": r.get("kind"), "volume_usd": r.get("volume_usd"),
                  "ts": r.get("_epoch")} for r in rows], now)
            cooldown_ok = (now - self.last_exit.get(pool, 0)) > REENTRY_COOLDOWN_S
            liq = float(w.get("liq") or 0)
            # honeypot LAST (network call) and only when everything else passes
            v = entry_verdict(d, demand_turn(rows, now), micro, liq, True,
                              len(self.pos_meta), cooldown_ok, self.daily_pnl_usd,
                              age_h=self._pool_age_h(w),
                              drain_pct=lp_drain_pct(
                                  self.liq_hist.get(pool, []), now))
            self.n_evals += 1
            if not v["enter"]:
                for b in v["blocks"]:
                    self.block_hist[b] = self.block_hist.get(b, 0) + 1
                continue
            token = self._token_for(pool)
            if not token or not self._honeypot_ok(token):
                continue
            self._paper_buy(pool, token, w, d, micro, now, rows)

    def _paper_buy(self, pool, token, w, dip, micro, now, rows):
        t_decide = time.time()
        trigger_lag = rows[-1].get("lag_secs") if rows else None
        try:
            eth_in = ENTRY_USD / self.feed.eth_price
            q = self._executor().quote_buy(token, int(eth_in * 1e18))
        except Exception as e:
            print(f"[rh-paper] buy-quote failed {pool[:10]}: {e}", flush=True)
            return
        if not q or not q.amount_out:
            return
        t_fill = time.time()
        dec = self._token_decimals(token)
        px = price_from_quote(q.amount_in, q.amount_out, dec)
        qty = q.amount_out / 10 ** dec
        self.pm.open_position(token=pool, entry_price=px, size_usd=ENTRY_USD,
                              entry_time=now, address=token)
        self.pos_meta[pool] = {"qty_orig": qty, "remaining_frac": 1.0,
                               "token": token, "sym": w["sym"],
                               "entry_px": px, "entry_ts": now}
        self.n_entries += 1
        lat_total = (None if trigger_lag is None
                     else round(trigger_lag + (t_fill - t_decide), 2))
        rec = {"ev": "buy", "ts": iso_utc(now), "pool": pool, "token": token,
               "sym": w["sym"], "usd": ENTRY_USD, "price_eth": px, "qty": qty,
               "dip_pct": round(dip, 2), "liq": w.get("liq"),
               "micro": {k: micro.get(k) for k in ("avoid_block", "flow_confirm")},
               "lat_trigger_lag_s": trigger_lag,
               "lat_quote_s": round(t_fill - t_decide, 3),
               "lat_total_s": lat_total, "fee_tier": q.fee}
        _append(LEDGER, rec)
        self.save_state()
        print(f"[rh-paper] BUY  {w['sym']:<12} ${ENTRY_USD:.0f} dip={dip:.1f}% "
              f"lat_total={lat_total}s (trigger {trigger_lag}s + "
              f"quote {rec['lat_quote_s']}s)", flush=True)

    def _manage_exits(self, now: float):
        for pool, meta in list(self.pos_meta.items()):
            series = self.prices.get(pool) or []
            if not series:
                continue
            px = series[-1][1]
            rows = self.tape.get(pool, [])
            # LP-DRAIN EXIT (rug-guard port): pool liquidity collapsing under
            # us = get out NOW, don't wait for the price path to confirm.
            _drain = lp_drain_pct(self.liq_hist.get(pool, []), now)
            if _drain is not None and _drain <= LP_DRAIN_EXIT_PCT:
                from types import SimpleNamespace
                self._paper_sell(pool, meta, SimpleNamespace(
                    kind="LP_DRAIN", sell_fraction=1.0,
                    reason="lp drain %.1f%% in %ds (liq collapse)" % (
                        _drain, int(LP_DRAIN_WINDOW_S))), now)
                continue
            vol_m5 = sum(float(r.get("volume_usd") or 0) for r in rows
                         if now - (r.get("_epoch") or 0) <= 300)
            for d in self.pm.tick(token=pool, current_price=px, now=now,
                                  vol_m5_usd=vol_m5):
                self._paper_sell(pool, meta, d, now)
                if pool not in self.pos_meta:
                    break

    def _paper_sell(self, pool, meta, decision, now):
        token, dec = meta["token"], self._token_decimals(meta["token"])
        frac, new_remaining = sell_slice(meta["remaining_frac"],
                                         decision.sell_fraction)
        if frac <= 0:
            return
        sell_qty = meta["qty_orig"] * frac
        try:
            q = self._executor().quote_sell(token, int(sell_qty * 10 ** dec))
            eth_out = (q.amount_out / 1e18) if (q and q.amount_out) else 0.0
        except Exception:
            eth_out = 0.0
        if eth_out <= 0:  # unquotable at exit = rug/honeypot turned on: mark 0
            usd_out = 0.0
            exit_px = meta["entry_px"] * 1e-9
        else:
            usd_out = eth_out * self.feed.eth_price
            exit_px = (eth_out / sell_qty) if sell_qty else 0.0
        res = self.pm.close_position(pool, exit_price=max(exit_px, 1e-18),
                                     exit_time=now, reason=decision.reason,
                                     sell_fraction=decision.sell_fraction)
        cost = ENTRY_USD * frac
        pnl_usd = usd_out - cost - 2 * GAS_USD_PER_SIDE * frac
        pnl_pct = pnl_usd / cost * 100 if cost else 0.0
        self.daily_pnl_usd += pnl_usd
        meta["remaining_frac"] = new_remaining
        fully = getattr(res, "fully_closed", new_remaining <= 1e-9)
        if fully:
            self.pos_meta.pop(pool, None)
            self.last_exit[pool] = now
            self.n_exits += 1
            _append(POSTEXIT_PENDING, {
                "pool": pool, "token": token, "sym": meta["sym"],
                "exit_px_eth": exit_px, "exit_kind": decision.kind,
                "exit_pnl_pct": round(pnl_pct, 2), "close_ts": now,
                "due_ts": now + POSTEXIT_DELAY_S})
        self.save_state()
        _append(LEDGER, {"ev": "sell", "ts": iso_utc(now), "pool": pool,
                         "sym": meta["sym"], "kind": decision.kind,
                         "reason": decision.reason[:100], "frac": frac,
                         "usd_out": round(usd_out, 2),
                         "pnl_usd": round(pnl_usd, 2),
                         "pnl_pct": round(pnl_pct, 2), "fully": fully,
                         "daily_pnl_usd": round(self.daily_pnl_usd, 2)})
        print(f"[rh-paper] SELL {meta['sym']:<12} {decision.kind} "
              f"{frac*100:.0f}% pnl={pnl_pct:+.1f}% "
              f"(day {self.daily_pnl_usd:+.2f}) {decision.reason[:50]}",
              flush=True)

    def _check_postexit(self, now: float):
        """Complete due +6h post-exit checks: one quote per due token, result
        row written, pending rewritten. Unquotable at +6h = died (post_px 0)."""
        try:
            if not os.path.exists(POSTEXIT_PENDING):
                return
            rows = []
            with open(POSTEXIT_PENDING, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except ValueError:
                            pass
            keep = []
            for r in rows:
                if now < float(r.get("due_ts") or 0):
                    keep.append(r)
                    continue
                post_px = 0.0
                try:
                    q = self._executor().quote_buy(
                        r["token"], int(ENTRY_USD / max(
                            self.feed.eth_price or 1e9, 1e-9) * 1e18))
                    if q and q.amount_out:
                        post_px = price_from_quote(
                            q.amount_in, q.amount_out,
                            self._token_decimals(r["token"]))
                except Exception:
                    pass
                ex = float(r.get("exit_px_eth") or 0)
                vs = ((post_px - ex) / ex * 100.0) if (ex > 0 and post_px > 0) else None
                _append(POSTEXIT_RESULTS, {**r, "post6h_px_eth": post_px,
                                           "post6h_vs_exit_pct": (round(vs, 1)
                                                                  if vs is not None else None),
                                           "died": post_px <= 0,
                                           "checked_ts": now})
                print(f"[rh-paper] post-exit +6h {r['sym']}: "
                      f"{'DEAD' if post_px <= 0 else '%+.1f%% vs exit' % vs}",
                      flush=True)
            tmp = POSTEXIT_PENDING + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                for r in keep:
                    f.write(json.dumps(r, separators=(",", ":")) + "\n")
            os.replace(tmp, POSTEXIT_PENDING)
        except Exception as e:
            print(f"[rh-paper] post-exit sweep failed: {e}", flush=True)

    def strategy_loop(self):
        print(f"[rh-paper] lane armed: ${ENTRY_USD:.0f}/entry, max {MAX_CONCURRENT}, "
              f"dip<={DIP_TRIGGER_PCT}%, liq>=${MIN_LIQ_USD:,.0f}, "
              f"daily stop {DAILY_LOSS_STOP_USD}", flush=True)
        while not self.stop.is_set():
            t0 = time.time()
            try:
                now = self.feed.rpc.now()
                self._drain(now)
                self._sample_liq(now)
                self._quote_hot(now)
                self._manage_exits(now)
                self._consider_entries(now)
                if now - getattr(self, "_last_pe_sweep", 0) > POSTEXIT_SWEEP_S:
                    self._last_pe_sweep = now
                    self._check_postexit(now)
            except Exception as e:
                print(f"[rh-paper] loop {type(e).__name__}: {e}", flush=True)
            self.stop.wait(max(0.2, STRAT_TICK_S - (time.time() - t0)))

    def summary(self):
        top = sorted(self.block_hist.items(), key=lambda kv: -kv[1])[:4]
        blocks = " ".join(f"{k}={v}" for k, v in top) or "-"
        return (f"[rh-paper] session: entries={self.n_entries} "
                f"exits={self.n_exits} open={len(self.pos_meta)} "
                f"day_pnl=${self.daily_pnl_usd:+.2f} | quotes={self.n_quotes} "
                f"evals={self.n_evals} blocks: {blocks}")


async def orchestrate(fh: Firehose, lane: PaperLane, max_minutes: float):
    t_end = time.time() + max_minutes * 60
    ws_task = asyncio.create_task(fh.run_ws(t_end))
    strat = threading.Thread(target=lane.strategy_loop, daemon=True)
    strat.start()
    last_scanned = fh.feed.latest_block
    t0 = time.time()
    last_stats = t0
    try:
        while time.time() < t_end and not ws_task.done():
            last_scanned = await asyncio.to_thread(fh.maintenance, last_scanned)
            if time.time() - last_stats > 60.0:
                print(fh.stats_line(time.time() - t0), flush=True)
                print(lane.summary(), flush=True)
                last_stats = time.time()
            await asyncio.sleep(MAINT_SECS)
    finally:
        lane.stop.set()
        ws_task.cancel()
        try:
            await ws_task
        except (asyncio.CancelledError, Exception):
            pass


def main():
    max_minutes = float(sys.argv[1]) if len(sys.argv) > 1 else 240.0
    feed = Feed(os.environ.get("RH_FEED_RPC", RPC_DEFAULT))
    cid = int(feed.rpc.call("eth_chainId", []), 16)
    if cid != RH_CHAIN_ID:
        print(f"[rh-paper] FATAL: chain_id={cid} != {RH_CHAIN_ID}", flush=True)
        sys.exit(1)
    feed.sync_head()
    feed.refresh_eth_price()
    if feed.eth_price is None:
        print("[rh-paper] FATAL: no ETH/USD price", flush=True)
        sys.exit(1)
    lookback = int(LOOKBACK_H * 3600 / max(feed.spb, 0.02))
    feed.backfill_discovery(lookback)
    fh = Firehose(feed)
    lane = PaperLane(feed, registry=fh.registry)
    lane.restore_state()
    fh.on_row = lane.on_row
    print(f"[rh-paper] chain {cid} eth=${feed.eth_price:,.2f} "
          f"candidates={len(feed.cand)} PAPER-ONLY (no key, quotes only)",
          flush=True)
    asyncio.run(orchestrate(fh, lane, max_minutes))
    print(lane.summary(), flush=True)


if __name__ == "__main__":
    main()
