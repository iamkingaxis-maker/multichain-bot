for i in $(seq 1 14); do
  s=$(railway deployment list 2>&1 | grep 3fe1425f | grep -oE "BUILDING|DEPLOYING|SUCCESS|FAILED|CRASHED" | head -1)
  if [ "$s" = "SUCCESS" ]; then
    sleep 25
    lm=$(curl -s --max-time 20 "https://gracious-inspiration-production.up.railway.app/api/stats" 2>&1 | python -c "import sys,json; print(json.load(sys.stdin).get('live_mode'))" 2>/dev/null)
    bots=$(curl -s --max-time 20 "https://gracious-inspiration-production.up.railway.app/api/bots" 2>&1 | python -c "import sys,json; d=json.load(sys.stdin); b=d if isinstance(d,list) else (d.get('bots') or d.get('data') or []); print('clone_loaded' if any((x.get('bot_id') or x.get('id'))=='badday_flush_conviction_live' for x in b) else 'clone_MISSING')" 2>/dev/null)
    echo "CLONE DEPLOY SUCCESS | live_mode=$lm (want False) | $bots"
    exit 0
  fi
  if [ "$s" = "FAILED" ] || [ "$s" = "CRASHED" ]; then echo "DEPLOY $s"; exit 1; fi
  echo "poll $i: ${s:-?}"; sleep 30
done
echo "still building after ~7min"
