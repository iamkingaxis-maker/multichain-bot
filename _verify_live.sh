for i in $(seq 1 16); do
  s=$(railway deployment list 2>&1 | grep 2905c136 | grep -oE "BUILDING|DEPLOYING|SUCCESS|FAILED|CRASHED" | head -1)
  if [ "$s" = "SUCCESS" ]; then
    sleep 40  # full boot before checking live state
    echo "=== 2905c136 SUCCESS — live-state check ==="
    curl -s --max-time 25 "https://gracious-inspiration-production.up.railway.app/api/stats" 2>&1 | python -c "import sys,json; d=json.load(sys.stdin); print('live_mode:', d.get('live_mode'), '| uptime:', d.get('uptime') or d.get('uptime_human'))" 2>/dev/null
    exit 0
  fi
  if [ "$s" = "FAILED" ] || [ "$s" = "CRASHED" ]; then echo "DEPLOY $s — NOT LIVE"; exit 1; fi
  echo "poll $i: ${s:-?}"; sleep 30
done
echo "still building after ~8min"
