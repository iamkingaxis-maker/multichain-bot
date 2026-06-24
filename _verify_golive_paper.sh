for i in $(seq 1 12); do
  s=$(railway deployment list 2>&1 | grep 3cce0dea | grep -oE "BUILDING|DEPLOYING|SUCCESS|FAILED|CRASHED" | head -1)
  if [ "$s" = "SUCCESS" ]; then
    sleep 25  # let it boot
    lm=$(curl -s --max-time 20 "https://gracious-inspiration-production.up.railway.app/api/stats" 2>&1 | python -c "import sys,json; print(json.load(sys.stdin).get('live_mode'))" 2>/dev/null)
    echo "DEPLOY SUCCESS | live_mode=$lm (MUST be False/None = paper holds with live_probe set, no env)"
    exit 0
  fi
  if [ "$s" = "FAILED" ] || [ "$s" = "CRASHED" ]; then echo "DEPLOY $s"; exit 1; fi
  echo "poll $i: ${s:-?}"; sleep 30
done
echo "still building after ~6min"
