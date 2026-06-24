sleep 150
for i in 1 2 3 4 5; do
  lm=$(curl -s --max-time 20 "https://gracious-inspiration-production.up.railway.app/api/stats" 2>&1 | python -c "import sys,json;
try: print(json.load(sys.stdin).get('live_mode'))
except: print('booting')" 2>/dev/null)
  if [ "$lm" = "True" ]; then echo "LIVE BOT OK after deploy: live_mode=True"; exit 0; fi
  echo "check $i: live_mode=$lm"; sleep 30
done
echo "WARN: live_mode not True after deploy — CHECK"
