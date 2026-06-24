"""CHAMELEON 13-HOUR WATCH (2026-06-13, AxiS: watch for bugs/anything off; run
the ENTIRE workflow each pass; don't be lazy). Runs as a Monitor: every ~20min
it walks every stage of the chameleon loop, prints a compact health line, and
emits a multi-line ALARM block if ANY process looks wrong. One pass = one
notification."""
import json, time, urllib.request, traceback, sys
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp1252 can't encode ⚠ → force utf-8
except Exception:
    pass
BASE = "https://gracious-inspiration-production.up.railway.app"

def _get_once(path, timeout):
    req = urllib.request.Request(BASE + path, headers={"Accept-Encoding": "gzip"})
    import gzip, io
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)

def get(path, timeout=45):
    # retry once on a transient stall (the intermittent >45s slow window clears
    # within seconds — a single retry turns a skipped 20-min pass into a 10s wait).
    try:
        return _get_once(path, timeout)
    except Exception:
        time.sleep(10)
        return _get_once(path, timeout)

def get_safe(path, timeout=45):
    """Sub-request that degrades gracefully — a timed-out pull returns None so
    one slow endpoint (boot blip) doesn't kill the whole audit pass."""
    try:
        return get(path, timeout)
    except Exception:
        return None

CLAMPS = {"time_stop_minutes": (10, 780), "tp1_pct": (8, 60), "hard_stop_pct": (-60, -10)}
LABELED = {"conviction","thesis_holder","time_boxer","surgical","swing","lottery"}
_seen_phantoms = set()  # (token, time) of big-move sells already flagged — alarm each ONCE, not every cycle

def audit():
    alarms, notes = [], []
    # ---- 1. PAPER SAFETY ----
    st = get("/api/stats")
    if st.get("live_mode") is True:
        alarms.append("CRITICAL: live_mode=TRUE (must be paper)")
    upt = st.get("uptime","?")
    if isinstance(upt,str) and upt.startswith("0h ") and upt[3:-1].isdigit() and int(upt[3:-1])<2:
        notes.append(f"recently restarted (uptime {upt}) — expect ~40s boot blip")
    # ---- 2. STREAM / CAPTURE ----
    ms = get("/api/meta-sensor")
    ing = ms.get("last_ingest_age_secs")
    opn = ms.get("open_episodes",0); scored = ms.get("scored_24h",0)
    if ing is None: notes.append("no ingest yet")
    elif ing > 1800: alarms.append(f"STREAM STALE: last ingest {ing}s ago (>1800)")
    if opn == 0: notes.append("0 open episodes")
    # ---- 3. BOARD ----
    w6 = (ms.get("windows") or {}).get("6h") or {}
    for arch,row in w6.items():
        if arch=="all": continue
        if row.get("unresolved",0) > row.get("n",0):
            alarms.append(f"BOARD: '{arch}' unresolved={row['unresolved']}>n={row['n']} (WR overstated)")
    # ---- 4. QUALIFY ----
    quals = [a for a,r in w6.items() if a!="all" and a in LABELED
             and r.get("n",0)>=8 and r.get("wr",0)>=0.60]
    # ---- 5. CHAMELEON TUNE ----
    ch = (ms.get("chameleon") or {}).get("meta_chameleon") or {}
    worn = ch.get("archetype"); tune = ch.get("tune") or {}
    geo = ch.get("geometry") or {}
    if worn and worn not in LABELED:
        alarms.append(f"TUNE: worn archetype '{worn}' not a known label (corruption)")
    for k,v in tune.items():
        if k in CLAMPS and isinstance(v,(int,float)):
            lo,hi = CLAMPS[k]
            if not (lo<=v<=hi): alarms.append(f"TUNE: {k}={v} OUTSIDE clamp [{lo},{hi}]")
    wal = geo.get("wallets") or {}
    if worn and geo:
        if geo.get("n_wallets",0) < 2:
            alarms.append(f"CONSENSUS: worn '{worn}' from {geo.get('n_wallets')} wallet(s) (<2)")
        if (geo.get("top_wallet_share") or 0) > 0.75:
            alarms.append(f"CONSENSUS: top wallet {geo.get('top_wallet_share'):.0%}>75% of '{worn}'")
    pend = ch.get("pending")
    if pend:
        age = time.time() - float(pend.get("queued_at") or time.time())
        if age > 7800:  # 2h force + slack
            alarms.append(f"QUIESCE STUCK: pending '{pend.get('archetype')}' queued {age/3600:.1f}h (>2h force should have applied)")
    # ---- 6/7. GATE + TRIPWIRES (inferred from state) ----
    rc = [c for c in (ch.get("recent_closes") or []) if c.get("archetype")==worn][-3:]
    losses = [c for c in rc if not c.get("win")]
    own_fills_should_pause = len(rc)>=3 and len(losses)>=2
    fresh = None  # not exposed; checked via board freshness proxy
    # ---- 8. TRADES ----  authoritative lifetime P&L from leaderboard (small/fast);
    # phantom check from a LIGHT recent window (trimmed; full=1 timed out under load).
    lb = get_safe("/api/leaderboard")
    lb_rows = (lb if isinstance(lb,list) else (lb or {}).get("leaderboard") or (lb or {}).get("bots") or []) if lb else []
    cham_lb = next((r for r in lb_rows if r.get("bot_id")=="meta_chameleon"), {})
    tb_lb = next((r for r in lb_rows if r.get("bot_id")=="timebox_probe"), {})
    net = float(cham_lb.get("realized_pnl_total_usd") or 0.0)       # lifetime, authoritative
    tb_net = float(tb_lb.get("realized_pnl_total_usd") or 0.0)
    n_buys = int(cham_lb.get("total_trades") or 0); n_wins = cham_lb.get("wins")
    tr = get_safe("/api/trades?limit=300")
    rows = tr if isinstance(tr,list) else (tr or {}).get("trades",[]) if tr else []
    cb = [t for t in rows if t.get("bot_id")=="meta_chameleon"]
    buys = [t for t in cb if t.get("type")=="buy"]
    sells = [t for t in cb if t.get("type")=="sell"]
    phantom = [t for t in sells if isinstance(t.get("pnl_pct"),(int,float)) and abs(t["pnl_pct"])>150]
    new_ph = [t for t in phantom if (t.get("token"), t.get("time")) not in _seen_phantoms]
    for t in new_ph: _seen_phantoms.add((t.get("token"), t.get("time")))
    if new_ph:
        toks = ", ".join("%s(%+.0f%%)" % (t.get("token"), t["pnl_pct"]) for t in new_ph)
        alarms.append(f"PHANTOM: {len(new_ph)} NEW sell(s) |pnl_pct|>150 [{toks}] — verify real-vs-guard (check OHLC high)")
    # signal-driven breakdown — AUTHORITATIVE source is the tune state's
    # recent_closes (the trades endpoint can trim the archetype field).
    rc_all = ch.get("recent_closes") or []
    conv_closes = [c for c in rc_all if (c.get("archetype") or "default")!="default"]
    default_n = len([c for c in rc_all if (c.get("archetype") or "default")=="default"])
    by_arch = {}
    for c in conv_closes:
        a=c.get("archetype"); d=by_arch.setdefault(a,[0,0.0,0])
        d[0]+=1; d[1]+=float(c.get("net") or 0); d[2]+= 1 if c.get("win") else 0
    sig_net = sum(float(c.get("net") or 0) for c in conv_closes)
    # ---- REPORT ---- (lifetime net + tb_net from leaderboard; signal-driven from recent_closes)
    sig_n = len(conv_closes)
    flag = "ALARM" if alarms else "ok"  # ASCII — no non-encodable chars in the status flag
    deg = "" if (lb is not None and tr is not None) else "  [DEGRADED: a sub-pull timed out]"
    line = (f"[{flag}] worn={worn} tune(ts={tune.get('time_stop_minutes')},tp1={tune.get('tp1_pct')},"
            f"stop={tune.get('hard_stop_pct')}) | qual={quals} | board6h={ {a:(r['n'],round(r['wr'],2)) for a,r in w6.items() if a!='all'} } | "
            f"chameleon trades={n_buys} wins={n_wins} lifetime_net=${net:.2f} | "
            f"conviction-experiment: signal-driven={sig_n} (net=${sig_net:.2f}) default={default_n} | "
            f"per-arch={ {a:(d[0],round(d[1],1),f'{d[2]}/{d[0]}W') for a,d in by_arch.items()} } | "
            f"ingest={ing}s open_ep={opn} scored={scored} uptime={upt} | tb_net=${tb_net:.0f}{deg}")
    print(line, flush=True)
    if own_fills_should_pause:
        print(f"  note: own-fills shows {len(losses)}/{len(rc)} recent '{worn}' closes lost — gate should be paused; verify next buy is blocked", flush=True)
    if alarms:
        for a in alarms: print("  "+a, flush=True)
    if notes:
        print("  notes: "+ "; ".join(notes), flush=True)

while True:
    try:
        audit()
    except Exception as e:
        print(f"[audit-error] {type(e).__name__}: {e}", flush=True)
    time.sleep(1200)  # 20 min
