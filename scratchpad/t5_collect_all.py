import json,subprocess,sys
sys.path.insert(0,'scratchpad')
import importlib.util
spec=importlib.util.spec_from_file_location("wb","scratchpad/t5_winbuys.py")
wb=importlib.util.module_from_spec(spec); spec.loader.exec_module(wb)
import datetime
W={"6FYgn2":"6FYgn2apNXSqNVyXHTV8CFHiRQhw5TPdDUEHrBMyR8og",
"DMdbb784":"DMdbb784EUaorYrC34LE4jbUM3ovYjJnNTwV7r4EH2eP",
"72QUWNCw":"72QUWNCwsmYUMwkCGqvagCKAmq7Ng5mfG7yCeUYSLzEc",
"3YFawWJV":"3YFawWJV2CCRdW8SvFFj9S1PmKLpZGUy3WT6pZmnaKRu",
"AgTM7bcP":"AgTM7bcPQo2kkru8o2igeiwbAjJY2bubEHHZUQPRhyqG"}
allb={}
for k,a in W.items():
    b=wb.buys(a,120)
    allb[k]=b
    if b:
        ts=[x["bt"] for x in b]
        lo=datetime.datetime.fromtimestamp(min(ts),datetime.UTC).strftime("%m-%d %H:%M")
        hi=datetime.datetime.fromtimestamp(max(ts),datetime.UTC).strftime("%m-%d %H:%M")
        print(f"{k}: {len(b)} buys, span {lo} -> {hi}")
    else:
        print(f"{k}: 0 buys (UNFOLLOWABLE/RPC)")
json.dump(allb,open("scratchpad/all_winner_buys.json","w"))
