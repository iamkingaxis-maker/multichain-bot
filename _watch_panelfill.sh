# Poll Railway logs for the panel-refresher's first NON-EMPTY fill (added=[(...)]).
# 'added=[]' is an empty cycle (skip); 'added=[(' means it actually stocked a wallet.
for i in $(seq 1 10); do
  hit=$(railway logs 2>&1 | grep -E "\[PanelRefresh\].*added=\[\(" | tail -1)
  if [ -n "$hit" ]; then echo "FIRST FILL @ iter $i:"; echo "$hit"; exit 0; fi
  # also surface dry cycles so I know it's running (no fill yet)
  dry=$(railway logs 2>&1 | grep -E "\[PanelRefresh\]" | tail -1)
  echo "iter $i: ${dry:-no PanelRefresh log yet (deploy may still be building)}"
  sleep 180
done
echo "TIMEOUT: no fill in ~30min — relaunch"
