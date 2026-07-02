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
                zone = ("rip" if h6 > 1.5 else "crash" if h6 < -3 or h1 < -2
                        else "green" if h6 > 0 else "red")
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
            our_tokens = {str(t.get("token")).lower() for t in buys[-120:]}

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
        save_state(state)
        time.sleep(CYCLE_SECS)


if __name__ == "__main__":
    main()
