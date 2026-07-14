#!/usr/bin/env bash
# Sequential wallet decode of the discovery candidate pool (rate-limit safe).
# Swaps in a slim overlap cache (our current traded tokens), restores the big one after.
set -u
cd "$(dirname "$0")"

# 1. slim overlap cache from our current trades (back up the big one)
if [ -f _trades_cache.json ] && [ ! -f _trades_cache.json.bak ]; then
  mv _trades_cache.json _trades_cache.json.bak
fi
python -c "
import json
recs=json.load(open('_full_trades.json'))
slim=[{'address':r.get('address'),'type':'buy'} for r in recs if r.get('address')]
json.dump(slim, open('_trades_cache.json','w'))
print('slim cache:', len(slim), 'rows')
"

# 2. decode each candidate sequentially
i=0
while IFS= read -r addr; do
  [ -z "$addr" ] && continue
  i=$((i+1))
  short="${addr:0:8}"
  echo "=== [$i] decoding $short ($addr) ==="
  timeout 150 python scripts/wallet_decode.py "$addr" 150 > "_decode_${short}.txt" 2> "_decode_${short}.err" \
    && echo "  ok -> _decode_${short}.txt ($(wc -l < _decode_${short}.txt) lines)" \
    || echo "  FAIL/timeout (exit $?) — see _decode_${short}.err"
  sleep 4
done < _decode_targets.txt

# 3. restore the big cache
if [ -f _trades_cache.json.bak ]; then
  rm -f _trades_cache.json
  mv _trades_cache.json.bak _trades_cache.json
fi
echo "=== DECODE POOL COMPLETE ($i wallets) ==="
