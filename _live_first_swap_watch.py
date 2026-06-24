"""CLOSE WATCH — the FIRST real swap by badday_flush_conviction_live (the live
exec path has NEVER fired a real swap; this is the validation). Silent until
swaps_attempted>0 (only a REAL swap increments it — cutover/paper-safe), then
reports the full exec breakdown + the live trade so we can verify it executed
clean: quote -> swap -> confirm -> real tokens (no phantom), at the $30 bound.
Polls /api/stats (small) every 60s; pulls /api/trades only when something changes."""
import time, json, urllib.request, gzip, io
BASE = "https://gracious-inspiration-production.up.railway.app"


def get(p, t=20):
    req = urllib.request.Request(BASE + p, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=t) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)


print("[swap-watch] armed — watching badday_flush_conviction_live for its first REAL swap", flush=True)
prev = None
seen_trades = set()
while True:
    try:
        st = get("/api/stats")
        ex = st.get("execution", {}) or {}
        att = ex.get("swaps_attempted") or 0
        cur = (att, ex.get("successful_swaps") or 0, ex.get("swap_failures") or 0,
               ex.get("quote_failures") or 0, ex.get("confirm_timeouts") or 0,
               ex.get("confirm_errors") or 0, ex.get("blocked_low_sol") or 0)
        if att > 0 and cur != prev:
            print(f"\n[SWAP EVENT] attempted={cur[0]} successful={cur[1]} swap_fail={cur[2]} "
                  f"quote_fail={cur[3]} confirm_timeout={cur[4]} confirm_err={cur[5]} "
                  f"blocked_low_sol={cur[6]} | wallet_sol={ex.get('wallet_sol_balance')} "
                  f"avg_slip={ex.get('avg_realized_slippage_pct')}% max_slip={ex.get('max_realized_slippage_pct')}% "
                  f"samples={ex.get('realized_samples')}", flush=True)
            prev = cur
            try:
                tr = get("/api/trades?limit=100")
                rows = tr if isinstance(tr, list) else tr.get("trades", [])
                for t in [x for x in rows if x.get("bot_id") == "badday_flush_conviction_live"]:
                    k = (t.get("type"), t.get("time"))
                    if k in seen_trades:
                        continue
                    seen_trades.add(k)
                    em = t.get("entry_meta") or {}
                    print(f"  [LIVE TRADE] {t.get('type')} {t.get('token')} addr={t.get('address')} "
                          f"entry={t.get('entry_price')} exit={t.get('exit_price')} pnl={t.get('pnl')} "
                          f"reason={t.get('reason')} sig={em.get('live_signature')} "
                          f"live_size_usd={em.get('live_size_usd')} entry_suspect={em.get('live_entry_suspect')} "
                          f"time={t.get('time')}", flush=True)
            except Exception as e:
                print(f"  [swap-watch] trade pull err: {e}", flush=True)
        time.sleep(60)
    except Exception as e:
        print(f"[swap-watch] poll err: {e}", flush=True)
        time.sleep(60)
