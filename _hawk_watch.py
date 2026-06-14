"""HAWK WATCH (AxiS 2026-06-14): close dual-monitor of the two PRIORITIES —
the LIVE bot (badday_flush_conviction_live, REAL money) and the CHAMELEON
(meta_chameleon, the meta-rotation engine AxiS sees the most potential in:
"rotate into successful setups at the right time").

~3-min cycle. Prints on any MATERIAL change (live swap / error / halt-approach /
real-money P&L move; chameleon rotation / red-mode / >=$5 P&L move) plus a
~30-min all-normal heartbeat. Shows worn-vs-best-board each print so rotation
timing is visible at a glance. ASCII + utf-8 (no cp1252 crash). Supersedes
_chameleon_audit.py."""
import sys, time, json, urllib.request, gzip, io
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass
BASE = "https://gracious-inspiration-production.up.railway.app"


def get(p, t=25):
    req = urllib.request.Request(BASE + p, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=t) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    return json.loads(raw)


def lb_row(rows, bid):
    r = next((x for x in rows if x.get("bot_id") == bid), None)
    return (float(r.get("realized_pnl_total_usd") or 0), int(r.get("total_trades") or 0)) if r else (0.0, 0)


print("[hawk] armed — dual watch: LIVE bot + CHAMELEON (3min, change-driven + IDLE watchdog)", flush=True)
prev = {"att": 0, "succ": 0, "fail": 0, "blocked": 0, "worn": "__init__", "live_net": 0.0,
        "cham_net": None, "cham_tr": None}
# IDLE WATCHDOG (AxiS 2026-06-14: "you should have caught this in your watches"). A
# change-driven watch is BLIND to inactivity — a standing-down chameleon makes no
# rotation and no P&L move, so it never alarmed. Track time since the last NEW trade
# and flag a bot that's armed-but-not-firing (or in standby) as a first-class alarm.
cham_last_trade_ts = None
CHAM_IDLE_SECS = 7200        # 2h armed-but-no-new-trade -> ALARM
cyc = 0
while True:
    try:
        cyc += 1
        st = get("/api/stats"); ex = st.get("execution", {}) or {}
        lb = get("/api/leaderboard")
        rows = lb if isinstance(lb, list) else lb.get("leaderboard") or lb.get("bots") or []
        ms = get("/api/meta-sensor")

        live_mode = st.get("live_mode")
        att = ex.get("swaps_attempted") or 0
        succ = ex.get("successful_swaps") or 0
        fail = (ex.get("swap_failures") or 0) + (ex.get("quote_failures") or 0) + (ex.get("confirm_errors") or 0)
        blocked = ex.get("blocked_low_sol") or 0
        live_net, live_tr = lb_row(rows, "badday_flush_conviction_live")
        cham_net, cham_tr = lb_row(rows, "meta_chameleon")
        tbm_net, tbm_tr = lb_row(rows, "timebox_probe_mcap")   # live-seat swap candidate

        nowts = time.time()
        if cham_last_trade_ts is None:
            cham_last_trade_ts = nowts
        if prev["cham_tr"] is not None and cham_tr > prev["cham_tr"]:
            cham_last_trade_ts = nowts          # it traded -> reset the idle clock
        cham_idle_h = (nowts - cham_last_trade_ts) / 3600.0

        ch = (ms.get("chameleon") or {}).get("meta_chameleon") or {}
        worn = ch.get("archetype"); tune = ch.get("tune") or {}
        w6 = (ms.get("windows") or {}).get("6h") or {}
        quals = sorted([(a, r.get("n", 0), round(r.get("wr", 0), 2)) for a, r in w6.items()
                        if a != "all" and r.get("n", 0) >= 8 and r.get("wr", 0) >= 0.60], key=lambda x: -x[2])
        best = f"{quals[0][0]}(wr{quals[0][2]})" if quals else None
        rcw = [c for c in (ch.get("recent_closes") or []) if c.get("archetype") == worn][-3:]
        worn_recent = (f"{sum(1 for c in rcw if c.get('win'))}/{len(rcw)}W" if rcw else "n/a")

        alarms = []
        if live_mode is not True:
            alarms.append(f"LIVE_MODE not True ({live_mode}) — live bot not routing real")
        if att != prev["att"]:
            alarms.append(f"LIVE SWAP attempted {prev['att']}->{att} (successful={succ} fail={fail})")
        if fail > prev["fail"]:
            alarms.append(f"LIVE SWAP FAILED total_fail {prev['fail']}->{fail}")
        if blocked > prev["blocked"]:
            alarms.append(f"LIVE blocked_low_sol {prev['blocked']}->{blocked} (top up wallet SOL)")
        if live_net != prev["live_net"]:
            alarms.append(f"LIVE P&L (REAL) ${prev['live_net']:.2f} -> ${live_net:.2f}")
        if live_net <= -18:
            alarms.append(f"LIVE near -$20 daily halt (net=${live_net:.2f})")
        if worn != prev["worn"] and prev["worn"] != "__init__":
            alarms.append(f"CHAMELEON ROTATED {prev['worn']} -> {worn}")
        if prev["cham_net"] is not None and abs(cham_net - prev["cham_net"]) >= 5:
            alarms.append(f"CHAMELEON P&L ${prev['cham_net']:.2f} -> ${cham_net:.2f}")
        # IDLE WATCHDOG (AxiS 2026-06-14) — inactivity is now a first-class alarm.
        if not worn:
            alarms.append("CHAMELEON STANDBY — worn=None (idle; should be on green-momentum default)")
        elif cham_idle_h >= CHAM_IDLE_SECS / 3600.0:
            alarms.append(f"CHAMELEON IDLE — worn={worn} but NO new trade in {cham_idle_h:.1f}h (armed, not firing)")
        # SWAP TRIGGER — timebox_mcap crossing n>=30 is the cue to validate + plan the live-seat swap.
        if tbm_tr >= 30 and (prev.get("tbm_tr") or 0) < 30:
            alarms.append(f"SWAP-TRIGGER: timebox_mcap n={tbm_tr} (>=30) net=${tbm_net:.2f} — validate + plan live-seat swap")

        red = worn == "deepflush_red"; green = worn == "momentum_green"
        if bool(alarms) or cyc == 1 or cyc % 10 == 0:
            flag = "ALARM" if alarms else "ok"
            _mode = " [RED]" if red else (" [GREEN-MOM]" if green else "")
            print(f"[{flag}] LIVE mode={live_mode} swaps={att}a/{succ}s/{fail}f blk={blocked} "
                  f"net=${live_net:.2f}/{live_tr}tr || CHAM worn={worn}{_mode} ({worn_recent}) "
                  f"idle={cham_idle_h:.1f}h net=${cham_net:.2f}/{cham_tr}tr best_board={best} "
                  f"|| TBM(swap) n={tbm_tr}/30 net=${tbm_net:.2f}", flush=True)
            for a in alarms:
                print("  [!] " + a, flush=True)
            if att != prev["att"] or any("LIVE" in a for a in alarms):
                try:
                    tr = get("/api/trades?limit=80")
                    trows = tr if isinstance(tr, list) else tr.get("trades", [])
                    for t in [x for x in trows if x.get("bot_id") == "badday_flush_conviction_live"][:3]:
                        em = t.get("entry_meta") or {}
                        print(f"    LIVE TRADE {t.get('type')} {t.get('token')} entry={t.get('entry_price')} "
                              f"pnl={t.get('pnl')} sig={em.get('live_signature')} size={em.get('live_size_usd')} "
                              f"{t.get('time')}", flush=True)
                except Exception:
                    pass

        prev = {"att": att, "succ": succ, "fail": fail, "blocked": blocked,
                "worn": worn, "live_net": live_net, "cham_net": cham_net,
                "cham_tr": cham_tr, "tbm_tr": tbm_tr}
        time.sleep(180)
    except Exception as e:
        print(f"[hawk] err {e}", flush=True)
        time.sleep(180)
