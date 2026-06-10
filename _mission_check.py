"""One-command mission cycle check (9h mission 2026-06-09). Covers the protocol:
new trades since marker (with reasons), pond clone fires, smart_follow fires +
state records, daily goal meter, WS/diagnostics, paper mode."""
import json, sys, subprocess, collections
from datetime import datetime, timezone, timedelta

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "https://gracious-inspiration-production.up.railway.app"
MARKER = ".mission_trade_marker"
CAND = {"pond_settled_flow_thin","pond_settled_flow","pond_ugly_mtf","pond_settled_flow_solcap",
        "pond_bb_mtf","pond_flow_thin","pond_ugly_rsi","pond_sweep_flow","pond_sweep_deep_thin",
        "pool_c_post_peak","pool_c_tightexit","pool_a_candidate","momentum_shadow",
        "young_probe_light","young_probe_candidate","young_probe_stair","young_probe_baseflow",
        "badday_flush","badday_momo"}

def get(url, timeout=45):
    out = subprocess.run(["curl","-s","--compressed","--max-time",str(timeout),url],
                         capture_output=True, text=True, errors="replace").stdout
    try: return json.loads(out)
    except Exception: return None

# marker
try: since = open(MARKER).read().strip()
except Exception: since = "2026-06-09T19:44"

stats = get(f"{BASE}/api/stats", 20) or {}
print(f"paper={'OK' if stats.get('live_mode') is False else '!!CHECK!! '+str(stats.get('live_mode'))} "
      f"uptime={stats.get('uptime')} paused={stats.get('trading_paused')}")
diag = get(f"{BASE}/api/diagnostics", 20) or {}
ws = diag.get("dexscreener_ws") or {}
print(f"WS={ws.get('status')}(conn={ws.get('connected')}) open_pos={diag.get('open_positions')} "
      f"anomalies={str(diag.get('anomalies'))[:60]}")

d = get(f"{BASE}/api/trades?full=1&limit=400", 60)
trades = (d if isinstance(d, list) else (d or {}).get("trades", [])) or []
new = [t for t in trades if (t.get("time") or "") >= since]
buys = [t for t in new if t.get("type")=="buy"]
sells = [t for t in new if t.get("type")=="sell" and "cancelled on restart" not in (t.get("reason") or "").lower()]
print(f"\nsince {since}: buys={len(buys)} sells={len(sells)}")
tot=0
for t in sells:
    pnl=float(t.get("pnl") or 0); tot+=pnl
    print(f"  SELL {str(t.get('token'))[:10]:10s} {float(t.get('pnl_pct') or 0):+6.2f}% ${pnl:+7.2f} "
          f"peak={round(float(t.get('peak_pnl_pct') or 0),1):+5.1f} bot={str(t.get('bot_id'))[:18]} {str(t.get('reason'))[:34]}")
print(f"  net: ${tot:+.2f}")
for t in buys:
    print(f"  BUY  {str(t.get('token'))[:10]:10s} bot={str(t.get('bot_id'))[:20]} strat={t.get('strategy')} ${float(t.get('amount_usd') or 0):.0f}")

pond = [t for t in trades if (t.get("bot_id") or "").startswith("pond_")]
print(f"\npond fires (in last 400): {len(pond)}")
for t in pond[:6]:
    print(f"  {t.get('type')} {t.get('bot_id')} {str(t.get('token'))[:10]} {str(t.get('time'))[11:16]}")

# today's goal meter (CT day from 05:00 UTC) on this 400-pull + warn shallow
day_start = "2026-06-09T05:00"
cand_today = [t for t in trades if t.get("type")=="sell" and (t.get("time") or "")>=day_start
              and (t.get("bot_id") in CAND or (t.get("strategy") or "")=="smart_follow")
              and "cancelled on restart" not in (t.get("reason") or "").lower()]
oldest = min((t.get("time","") for t in trades), default="")
gm = sum(float(t.get("pnl") or 0) for t in cand_today)
print(f"\ngoal meter (this pull{' — SHALLOW, oldest '+oldest[11:16] if oldest>day_start else ''}): "
      f"candidate-set today ${gm:+.0f} vs $100")

# follow fires w/ state
fl = get(f"{BASE}/api/follow-logs", 30) or {}
sigs = fl.get("signals") or []
rec = [s for s in sigs if isinstance(s.get("state"), dict)]
print(f"follow signals total={len(sigs)} | with fire-state={len(rec)}")
for s in sigs[-2:]:
    st = s.get("state") or {}
    print(f"  fire {str(s.get('symbol'))[:8]} n={s.get('n')} pc_h1={st.get('pc_h1')}")

# advance marker
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
open(MARKER, "w").write(now)
print(f"\nmarker -> {now}")
