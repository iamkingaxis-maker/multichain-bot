# Watch for the FIRST real (sig-bearing) buy from the live bot, OR a live exec error.
for i in $(seq 1 10); do
  buy=$(curl -s --max-time 20 "https://gracious-inspiration-production.up.railway.app/api/trades?limit=60" 2>&1 | python -c "
import sys,json
try: d=json.load(sys.stdin)
except: print(''); sys.exit(0)
t=d if isinstance(d,list) else (d.get('trades') or d.get('data') or [])
for x in t:
    if x.get('bot_id')=='badday_flush_conviction_live' and x.get('type')=='buy':
        sig=x.get('sig') or x.get('signature') or x.get('tx_sig')
        if sig: print(f\"LIVE BUY: {x.get('token')} sig={str(sig)[:20]} \${x.get('size_usd') or x.get('usd')}\"); break
" 2>/dev/null)
  if [ -n "$buy" ]; then echo "@iter $i: $buy"; exit 0; fi
  err=$(railway logs 2>&1 | grep -iE "badday_flush_conviction_live|_execute_swap_ultra|insufficient|live.*fail|Sweep" | grep -iE "fail|insufficient|error" | tail -1)
  echo "iter $i: armed, no live buy yet${err:+ | EXEC NOTE: $err}"
  sleep 180
done
echo "no live buy in ~30min (badday lane is sparse) — still armed"
