import json
d=json.load(open('_vf_trades.json'))
mint='J9fVUSrsGYpuyXggAFJpf8WGkVsjqjCpBzyBxh6spump'
bs=[t for t in d if t.get('address')==mint and t.get('type')=='buy']
print('DONALD buys:',len(bs))
for b in sorted(bs,key=lambda x:x['time']):
    em=b.get('entry_meta') or {}
    print('  time',b['time'][:19],'bot',b['bot_id'],'bsl',em.get('1s_bars_since_low_60s'),
          'close_pos',em.get('1s_close_pos_60s'),'nf15',em.get('net_flow_15s_usd'),
          'ub',em.get('unique_buyers_n'),'hl',em.get('hl_confirm_state'),'slip',b.get('entry_slip_pct'))
# their sells
ss=[t for t in d if t.get('address')==mint and t.get('type')=='sell']
for s in sorted(ss,key=lambda x:x['time']):
    print('  SELL',s['time'][:19],s['bot_id'],'pnl%',round(s.get('pnl_pct') or 0,1),'hold',round(s.get('hold_secs') or 0),'peak',round(s.get('peak_pnl_pct') or 0,1),'mae',s.get('mae_pct'))

# reachability: does bsl require future data? check signal_ts vs fill. Confirm field is at signal time.
print()
print('signal_ts present sample:', bs[0]['entry_meta'].get('signal_ts_ms') if bs else None, 'fill time', bs[0]['time'] if bs else None)
