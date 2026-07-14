import json
d=json.load(open('_trades_cache.json'))
bd=[r for r in d if r.get('bot_id','').startswith('badday_')]
sell=next(r for r in bd if r['type']=='sell')
print('=== SELL ROW keys ===')
for k,v in sell.items():
    if k=='entry_meta':
        print('  entry_meta present:', bool(v))
    else:
        print(f'  {k}: {v}')
