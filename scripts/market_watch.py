#!/usr/bin/env python3
"""
market_watch.py — the automated 'AxiS eyes': periodic DexScreener/fleet check
that prints a line ONLY when something needs attention. Run under a Monitor so
each printed line becomes a notification.

Checks every CYCLE_SECS (default 720 = 12 min), all calls local (no Railway
egress except one tiny gzip /api/bots + /api/trades?limit=60 per cycle):
  MISSED-WINNER  token in our mcap band (50k-1B) up >=30%/h1 with vol, that we
                 hold no position in and haven't bought in 6h
  REGIME-FLIP    SOL h1/h6 crossing gate thresholds (rip open/close, crash)
  BUY-STALL      fleet buys in the last 2h < 25% of the trailing 12h/6 rate
                 (only during non-sleep hours UTC 08-03)
  FLEET-DARK     open positions unreadable / API down 2 cycles in a row
Quiet = healthy. State kept in scratchpad/market_watch_state.json.
"""
import json
import os
import time
import gzip
import io as _io
import urllib.request
from datetime import datetime, timezone

CYCLE_SECS = int(os.environ.get("MW_CYCLE_SECS", "720"))
DASH = "https://gracious-inspiration-production.up.railway.app"
STATE = os.path.join("scratchpad", "market_watch_state.json")
SOL_PAIR = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2"


def _dash_auth_header():
    """Basic-auth header for the bot's write endpoints (MW_DASH_USER/PASS env,
    injected at launch — never persisted)."""
    import base64
    u, pw = os.environ.get("MW_DASH_USER", ""), os.environ.get("MW_DASH_PASS", "")
    if not u or not pw:
        return {}
    return {"Authorization": "Basic " + base64.b64encode(f"{u}:{pw}".encode()).decode()}


def get(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Accept-Encoding": "gzip"})
    r = urllib.request.urlopen(req, timeout=timeout)
    raw = r.read()
    if r.headers.get("Content-Encoding") == "gzip":
        raw = gzip.GzipFile(fileobj=_io.BytesIO(raw)).read()
    return json.loads(raw)


def load_state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save_state(s):
    try:
        json.dump(s, open(STATE, "w"))
    except Exception:
        pass


def emit(tag, msg):
    print(f"[{tag}] {msg}", flush=True)


def inject_watchlist(state, addr, sym):
    """POST a qualified find into the bot's user-watchlist (force-include in
    every discovery/enrichment cycle — gates still decide entries). Client-side
    24h TTL: we remove our own injections after expiry so the set stays clean.
    Fail-open."""
    try:
        inj = state.setdefault("injected", {})
        if addr in inj:
            return
        body = json.dumps({"address": addr}).encode()
        req = urllib.request.Request(f"{DASH}/api/user-watchlist/add", data=body,
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "mw/1",
                                              **_dash_auth_header()})
        urllib.request.urlopen(req, timeout=15)
        inj[addr] = time.time()
        emit("INJECTED", f"{sym} -> scanner watchlist (24h pin)")
    except Exception:
        pass


def expire_injections(state):
    try:
        inj = state.get("injected") or {}
        for addr, ts in list(inj.items()):
            # TTL 24h -> 6h (2026-07-04): pin bloat (141 stale of 156) deepened
            # the fast-path tape-fetch queue past its 3s budget -> buyers=None
            # entries. A missed-dip pin is only interesting for hours, not a day.
            if time.time() - ts > 6 * 3600:
                body = json.dumps({"address": addr}).encode()
                req = urllib.request.Request(f"{DASH}/api/user-watchlist/remove",
                                             data=body,
                                             headers={"Content-Type": "application/json",
                                                      "User-Agent": "mw/1",
                                                      **_dash_auth_header()})
                try:
                    urllib.request.urlopen(req, timeout=15)
                except Exception:
                    pass
                del inj[addr]
    except Exception:
        pass


def chart_dip_check(pair_address):
    """GT minute bars -> (max_drawdown_pct, at_hhmm, n_bars). The recorder
    only samples tokens at runner moments, so dip existence MUST come from
    the chart (2026-07-02 lesson: 7/7 'pump-only' labels were wrong).
    Fail-open: any error -> (None, None, 0). Paced by the 6h alert dedup."""
    try:
        q = get(f"https://api.geckoterminal.com/api/v2/networks/solana/pools/"
                f"{pair_address}/ohlcv/minute?aggregate=1&limit=360")
        bars = sorted(((q.get("data") or {}).get("attributes") or {})
                      .get("ohlcv_list") or [])
        if len(bars) < 15:
            return None, None, len(bars)
        rollhi = 0.0
        maxdd = 0.0
        at = ""
        for b in bars:
            c = float(b[4])
            rollhi = max(rollhi, c)
            dd = (c / rollhi - 1) * 100 if rollhi > 0 else 0
            if dd < maxdd:
                maxdd = dd
                at = time.strftime("%H:%M", time.gmtime(b[0]))
        return maxdd, at, len(bars)
    except Exception:
        return None, None, 0


def launch_watch_tick(state):
    """WATCH-EARLIER v0 (2026-07-03 traction predictor, 4-window validated:
    n_bars_15>=8 & vol_15>=5k = 100% recall on every traction cohort incl
    110/110 production pools; pass ~15-25% of launches). Two-stage funnel:
    (1) each cycle, pull GT new_pools pages 1-3 and STAGE pools with
    reserve>=~$10k (the free prefilter); (2) once a staged pool is 12-25min
    old, one minute-bar call decides — participating pools get pinned into
    the scanner watchlist ~1.5h before discovery would see them (the
    Bullcoin-class miss). COVERAGE CAVEAT: GT polling sees only the newest
    ~60/cycle of ~130 launches/6min (~45%); the full-coverage feed
    (PumpPortal/io.dexscreener firehose) is the v1 upgrade. Fail-open."""
    try:
        staged = state.setdefault("lw_staged", {})
        seen = state.setdefault("lw_seen", {})
        now = time.time()
        # stage newest pools with real seed liquidity
        for page in (1, 2, 3):
            try:
                q = get("https://api.geckoterminal.com/api/v2/networks/solana/"
                        f"new_pools?page={page}")
            except Exception:
                break
            for it in (q.get("data") or []):
                try:
                    at = it.get("attributes") or {}
                    pa = (at.get("address") or "").strip()
                    if not pa or pa in staged or pa in seen:
                        continue
                    res = float(at.get("reserve_in_usd") or 0)
                    ts = at.get("pool_created_at")
                    import datetime as dt
                    cts = dt.datetime.fromisoformat(
                        str(ts).replace("Z", "+00:00")).timestamp() if ts else now
                    if res >= 10000 and now - cts < 12 * 60:
                        staged[pa] = {"t": cts, "sym": ((it.get("relationships") or {})
                                      .get("base_token") or {}).get("data", {}).get("id", pa[:8])}
                except Exception:
                    continue
            time.sleep(3.1)
        # decide staged pools that reached the 12-25min window
        for pa, info in list(staged.items()):
            age = now - info["t"]
            if age > 25 * 60:
                staged.pop(pa, None); seen[pa] = now
                continue
            if age < 12 * 60:
                continue
            try:
                q = get("https://api.geckoterminal.com/api/v2/networks/solana/"
                        f"pools/{pa}/ohlcv/minute?limit=25")
                bars = (((q.get("data") or {}).get("attributes") or {})
                        .get("ohlcv_list") or [])
                cut = now - 15 * 60
                printed = sum(1 for b in bars if float(b[0]) >= cut)
                vol15 = sum(float(b[5] or 0) for b in bars if float(b[0]) >= cut)
                if printed >= 8 and vol15 >= 5000:
                    sym = str(info.get("sym") or pa[:8]).split("_")[-1][:12]
                    inject_watchlist(state, pa, f"LW:{sym}")
                    try:
                        with open(os.path.join("scratchpad", "launch_watch.jsonl"), "a") as f:
                            f.write(json.dumps({"ts": now, "pair": pa, "sym": sym,
                                                "bars15": printed, "vol15": round(vol15)}) + "\n")
                    except Exception:
                        pass
                staged.pop(pa, None); seen[pa] = now
                time.sleep(3.1)
            except Exception:
                continue
        # bound state
        if len(seen) > 3000:
            for k in sorted(seen, key=seen.get)[:1500]:
                seen.pop(k, None)
    except Exception:
        pass


def greenday_forecast_tick(state):
    """Green-day forecaster HARNESS v1 (2026-07-03 study, n=17 days, NOT
    significant — this accrues the forward validation, one point/day).
    Candidate signal: median net_flow_60s_usd across the first-fill-per-token
    early entries (08:00-10:00 UTC) >= +$50 -> RED day call (modest positive
    early inflow = chase demand / pump-retrace day; capitulation days enter on
    flat/negative flow). Bar to act: >=30 forward days, both classes present,
    >=70% accuracy, binomial p<0.05, era-confound check. Records to
    scratchpad/greenday_forecast.jsonl; outcome backfilled next morning with
    the standard scrub + per-token bot-averaging. Fail-open everywhere."""
    import datetime as dt
    now = dt.datetime.now(dt.timezone.utc)
    today = now.strftime("%Y-%m-%d")
    path = os.path.join("scratchpad", "greenday_forecast.jsonl")
    excl = ("badday_young_absorb", "badday_fill_probe_live")
    # 1) the 10:00 UTC call (once per day)
    if now.hour >= 10 and state.get("gd_call_day") != today:
        try:
            first_by_tok = {}
            for bot in ("badday_flush", "badday_allday"):
                try:
                    arr = get(f"{DASH}/api/bots/{bot}/trades?limit=300")
                except Exception:
                    continue
                for t in arr or []:
                    tm = str(t.get("time", ""))
                    if (t.get("type") == "buy" and tm[:10] == today
                            and "08:00" <= tm[11:16] < "10:00"):
                        first_by_tok.setdefault(t.get("token"),
                                                t.get("entry_meta") or {})
            nf = [em.get("net_flow_60s_usd") for em in first_by_tok.values()]
            nf = sorted(float(x) for x in nf
                        if isinstance(x, (int, float)) and not isinstance(x, bool))
            med = nf[len(nf) // 2] if nf else None
            call = ("no_signal" if med is None else "red" if med >= 50 else "green")
            with open(path, "a") as f:
                f.write(json.dumps({"day": today, "ts": time.time(),
                                    "med_nf60": med, "n_nf60": len(nf),
                                    "n_early_tokens": len(first_by_tok),
                                    "call_v1": call}) + "\n")
            state["gd_call_day"] = today
            state["gd_pending_outcome"] = today
        except Exception:
            pass
    # 2) outcome backfill for the pending day (next morning, after 08:00)
    pend = state.get("gd_pending_outcome")
    if pend and pend != today and now.hour >= 8:
        try:
            arr = get(f"{DASH}/api/trades?limit=3000")
            arr = arr.get("trades", arr) if isinstance(arr, dict) else arr
            lo, hi = pend + "T10:00", today + "T08:00"
            tok, tb = {}, {}
            for t in arr:
                b = str(t.get("bot_id", ""))
                if (t.get("type") == "sell" and t.get("pnl_pct") is not None
                        and b.startswith("badday_") and b not in excl
                        and lo <= str(t.get("time", "")) < hi):
                    p = float(t["pnl_pct"]); h = t.get("hold_secs")
                    if p > 0 and isinstance(h, (int, float)) and h < 10:
                        continue  # scrub unrealizable spikes
                    tk = t.get("token")
                    tok[tk] = tok.get(tk, 0.0) + p * (t.get("sell_fraction") or 1.0)
                    tb.setdefault(tk, set()).add(b)
            vals = [tok[k] / max(1, len(tb[k])) for k in tok]
            mean = sum(vals) / len(vals) if vals else None
            with open(path, "a") as f:
                f.write(json.dumps({"day": pend, "ts": time.time(),
                                    "outcome_rest_mean_per_token": mean,
                                    "n_tokens": len(vals),
                                    "outcome": ("green" if (mean or 0) > 0 else
                                                "red" if mean is not None else
                                                "no_data")}) + "\n")
            state["gd_pending_outcome"] = None
        except Exception:
            pass


def main():
    state = load_state()
    fails = 0
    while True:
        now = datetime.now(timezone.utc)
        hr = now.hour
        try:
            # --- SOL regime ---
            try:
                d = get(f"https://api.dexscreener.com/latest/dex/pairs/solana/{SOL_PAIR}")
                pc = (d.get("pair") or (d.get("pairs") or [{}])[0]).get("priceChange") or {}
                h1, h6 = float(pc.get("h1") or 0), float(pc.get("h6") or 0)
                prev = state.get("sol_zone")
                # HYSTERESIS (2026-07-04): SOL oscillated across the 1.5 rip
                # line ~15x in 40min on 07-04 and the watcher alerted every
                # crossing. Enter rip above +1.8, leave below +1.2 — boundary
                # jitter inside the band keeps the previous zone.
                if h6 < -3 or h1 < -2:
                    zone = "crash"
                elif prev == "rip":
                    zone = "rip" if h6 > 1.2 else ("green" if h6 > 0 else "red")
                elif h6 > 1.8:
                    zone = "rip"
                else:
                    zone = "green" if h6 > 0 else "red"
                if prev and zone != prev and ("rip" in (zone, prev) or "crash" in (zone, prev)):
                    emit("REGIME-FLIP", f"SOL {prev} -> {zone} (h1={h1:+.2f} h6={h6:+.2f})")
                state["sol_zone"] = zone
            except Exception:
                pass

            # --- fleet state ---
            bots = get(f"{DASH}/api/bots")
            bots = bots.get("bots", bots) if isinstance(bots, dict) else bots
            open_n = sum((b.get("open_position_count") or 0) for b in bots or [])
            tr = get(f"{DASH}/api/trades?limit=200")
            arr = tr.get("trades", tr) if isinstance(tr, dict) else tr
            buys = [t for t in arr if t.get("type") == "buy"]
            fails = 0
            # buy-stall (only when awake: UTC 08-03 trading window)
            awake = not (3 <= hr < 8)
            if awake and buys:
                import datetime as dt
                nowts = time.time()
                def n_since(hours):
                    cut = (now - dt.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")
                    return sum(1 for t in buys if str(t.get("time", "")) > cut)
                last2, last12 = n_since(2), n_since(12)
                base_rate = last12 / 6.0
                if base_rate >= 2 and last2 < 0.25 * base_rate:
                    key = f"stall_{now.strftime('%Y%m%d%H')}"
                    if not state.get(key):
                        state[key] = 1
                        emit("BUY-STALL", f"buys last2h={last2} vs 12h-rate {base_rate:.1f}/2h "
                                          f"(open={open_n}) — check the funnel")
            our_tokens = {str(t.get("token")).lower() for t in buys[-300:]}

            # LOSS-EVENT (standing rule 2026-07-02: every loss gets analyzed):
            # per-token fleet net <= -40pp within the last 2h -> alert for
            # immediate root-cause analysis (gap-through? rebuy? new mode?)
            try:
                import datetime as _dt
                _cut2 = (now - _dt.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M")
                _tok_net = {}
                for t in arr:
                    if (t.get("type") == "sell" and t.get("pnl_pct") is not None
                            and str(t.get("time", "")) > _cut2):
                        _tok_net[t.get("token")] = _tok_net.get(t.get("token"), 0.0) +                             float(t["pnl_pct"]) * (t.get("sell_fraction") or 1.0)
                _seen_loss = state.setdefault("loss_seen", {})
                for tok, netpp in _tok_net.items():
                    if netpp <= -40:
                        k = f"loss_{str(tok).lower()}"
                        if time.time() - _seen_loss.get(k, 0) > 4 * 3600:
                            _seen_loss[k] = time.time()
                            emit("LOSS-EVENT", f"{tok} fleet net {netpp:+.0f}pp in 2h — "
                                               f"root-cause analysis required")
            except Exception:
                pass

            # --- missed winners in our band (source = the fleet's own broad
            # scan via universe-recorder; the DexScreener q=SOL search proved
            # near-blind for band movers 2026-07-02) ---
            try:
                d = get(f"{DASH}/api/universe-recorder?limit=800")
                events = d.get("events", d) if isinstance(d, dict) else d
                seen_alerts = state.setdefault("mw_seen", {})
                cut = time.time() - 6 * 3600
                for e in (events or []):
                    try:
                        pk = float(e.get("peak_pct") or 0)
                        ts_e = float(e.get("event_ts") or e.get("ts") or 0)
                        sym = str(e.get("symbol") or e.get("token_symbol") or "?")
                        if pk < 20 or ts_e < cut or sym.lower() in our_tokens:
                            continue
                        k = f"mw_{sym.lower()}"
                        if time.time() - seen_alerts.get(k, 0) > 6 * 3600:
                            dd, at, nb = chart_dip_check(e.get("pair_address") or "")
                            if dd is None:
                                # GT throttled — do NOT mark seen; re-verify next cycle
                                # (cap retries via a short 20-min soft mark)
                                if time.time() - seen_alerts.get(k + "_na", 0) < 1200:
                                    continue
                                seen_alerts[k + "_na"] = time.time()
                            else:
                                seen_alerts[k] = time.time()
                            if dd is None:
                                tag = "chart n/a"
                            elif dd <= -85:
                                tag = f"TERMINAL {dd:+.0f}% @{at} — avoided rug, not a miss"
                            elif dd <= -20:
                                tag = f"REAL DIP {dd:+.0f}% @{at} — in-thesis miss"
                                _tok_addr = e.get("token_address") or e.get("address") or ""
                                if _tok_addr:
                                    inject_watchlist(state, _tok_addr, sym)
                            else:
                                tag = f"shallow ({dd:+.0f}%) — momentum-only"
                            emit("MISSED-WINNER", f"{sym} peaked +{pk:.0f}% | {tag}")
                            time.sleep(3.2)
                    except Exception:
                        continue
            except Exception:
                pass
            # --- fresh listings + trending (outside our scanned universe) ---
            try:
                seen_new = state.setdefault("nl_seen", {})
                cand = []
                for src in ("token-profiles/latest/v1", "token-boosts/top/v1"):
                    try:
                        for tp in (get(f"https://api.dexscreener.com/{src}") or [])[:40]:
                            if tp.get("chainId") == "solana" and tp.get("tokenAddress"):
                                cand.append(tp["tokenAddress"])
                    except Exception:
                        continue
                fresh = [a for a in dict.fromkeys(cand)
                         if time.time() - seen_new.get(a, 0) > 12 * 3600][:30]
                if fresh:
                    d = get("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(fresh))
                    for pr in (d or []):
                        try:
                            base = (pr.get("baseToken") or {})
                            addr = base.get("address") or ""
                            sym = str(base.get("symbol") or "?")
                            liq = float(((pr.get("liquidity") or {}).get("usd")) or 0)
                            v1 = float((pr.get("volume") or {}).get("h1") or 0)
                            mc = float(pr.get("marketCap") or pr.get("fdv") or 0)
                            h1p = float((pr.get("priceChange") or {}).get("h1") or 0)
                            age_h = None
                            if pr.get("pairCreatedAt"):
                                age_h = (time.time() - pr["pairCreatedAt"] / 1000) / 3600
                            if addr:
                                seen_new[addr] = time.time()
                            # traction bar: real liq + real volume (not rug spam)
                            if liq >= 15_000 and v1 >= 10_000 and sym.lower() not in our_tokens:
                                kind = ("NEW-LISTING" if age_h is not None and age_h <= 24
                                        else "TRENDING")
                                inject_watchlist(state, addr, sym)
                                emit(kind, f"{sym} mc=${mc/1e6:.2f}M liq=${liq/1e3:.0f}k "
                                           f"vol1h=${v1/1e3:.0f}k h1={h1p:+.0f}% "
                                           f"age={age_h:.1f}h" if age_h is not None else
                                           f"{sym} mc=${mc/1e6:.2f}M liq=${liq/1e3:.0f}k "
                                           f"vol1h=${v1/1e3:.0f}k h1={h1p:+.0f}%")
                        except Exception:
                            continue
            except Exception:
                pass
        except Exception as e:
            fails += 1
            if fails == 2:
                emit("FLEET-DARK", f"API unreadable 2 cycles: {str(e)[:80]}")
        launch_watch_tick(state)
        greenday_forecast_tick(state)
        expire_injections(state)
        save_state(state)
        time.sleep(CYCLE_SECS)


if __name__ == "__main__":
    main()
