# Night watch: first real (sig) live buy OR any live-money risk signal. ~2h horizon.
for i in $(seq 1 24); do
  flag=$(curl -s --max-time 20 "https://gracious-inspiration-production.up.railway.app/api/stats" 2>&1 | python -c "import sys,json;
try: d=json.load(sys.stdin); print('LIVE_MODE_OFF' if d.get('live_mode') is not True else '')
except: print('')" 2>/dev/null)
  buy=$(curl -s --max-time 20 "https://gracious-inspiration-production.up.railway.app/api/trades?limit=50" 2>&1 | python -c "import sys,json;
try:
 d=json.load(sys.stdin); t=d if isinstance(d,list) else (d.get('trades') or d.get('data') or [])
 print(next((f\"REAL LIVE BUY {x.get('token')} sig={str(x.get('sig') or x.get('signature'))[:18]}\" for x in t if x.get('bot_id')=='badday_flush_conviction_live' and x.get('type')=='buy' and (x.get('sig') or x.get('signature'))), ''))
except: print('')" 2>/dev/null)
  err=$(railway logs 2>&1 | grep -iE "badday_flush_conviction_live|_execute_swap_ultra" | grep -iE "fail|insufficient|error|phantom|glitch" | tail -1)
  if [ -n "$buy" ]; then echo "@$i FIRST REAL BUY: $buy"; exit 0; fi
  if [ -n "$flag" ]; then echo "@$i ALERT: $flag (live_mode dropped!)"; exit 0; fi
  if [ -n "$err" ]; then echo "@$i EXEC ERROR: $err"; exit 0; fi
  sleep 300
done
echo "2h: live bot armed, no real buy yet (sparse badday lane), live_mode True, no errors"
