import bisect, statistics as st
from datetime import timedelta
from collections import defaultdict
import importlib.util
spec = importlib.util.spec_from_file_location("sig", "scratchpad/_rh_regime_signal_0713.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import core.rh_regime as R

rows = m.load_rows(); trips, _ = m.scrub(m.build_trips(rows))
for t in trips:
    t["_close_ts"] = t["entry_ts"] + timedelta(seconds=t["hold_s"])
days = sorted({t["day"] for t in trips})

# hold-time stats — does the dial refresh fast enough intraday?
holds = sorted(t["hold_s"] for t in trips)
print(f"hold_s: med={st.median(holds):.0f}s p75={holds[int(.75*len(holds))]:.0f}s "
      f"p90={holds[int(.90*len(holds))]:.0f}s max={max(holds):.0f}s")

# Causal dial via EXISTING expectancy_dial(window=20, min=10)
cs = sorted(trips, key=lambda t: t["_close_ts"]); cts=[t["_close_ts"] for t in cs]
base=defaultdict(float); g=defaultdict(float); nd=defaultdict(int); warm=0
deffrac=defaultdict(list)
for t in trips:
    j = bisect.bisect_right(cts, t["entry_ts"])
    prior = [x["pnl_usd"] for x in cs[:j]]        # all closed-before-entry
    dial = R.expectancy_dial(prior)               # window=20,min=10 defaults
    score = dial["exp_usd"]; state = dial["state"]
    would = 1.0 if (score is None or score >= 0) else 0.3
    if score is None: warm += 1
    base[t["day"]] += t["pnl_usd"]
    g[t["day"]] += t["pnl_usd"] * would
    nd[t["day"]] += 1 if would < 1.0 else 0
    deffrac[t["day"]].append(1 if (score is not None and score < 0) else 0)
bt=sum(base.values()); gt=sum(g.values())
print(f"\nEXISTING dial (window20/min10), defense=0.3x when exp_usd<0  [warmup={warm}]")
for d in days:
    df = sum(deffrac[d])/len(deffrac[d])
    print(f"  {d}: base=${base[d]:>8.2f} -> gated=${g[d]:>8.2f}  "
          f"(save ${g[d]-base[d]:>7.2f}; {nd[d]:>3} downsized; defense-frac={df:.2f})")
print(f"  TOTAL: base=${bt:.2f} -> gated=${gt:.2f}  delta=${gt-bt:+.2f}")
