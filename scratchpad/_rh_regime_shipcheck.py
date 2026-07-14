"""Reproduce the projected net-$ saved using the SHIPPED functions
(core.rh_regime.expectancy_dial + regime_size) on the fleet-wide realized series,
strictly causally (dial only sees positions CLOSED before each entry)."""
import bisect
from datetime import timedelta
from collections import defaultdict
import importlib.util
spec = importlib.util.spec_from_file_location("sig", "scratchpad/_rh_regime_signal_0713.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
from core.rh_regime import expectancy_dial, regime_size

rows = m.load_rows(); trips, _ = m.scrub(m.build_trips(rows))
for t in trips:
    t["_close_ts"] = t["entry_ts"] + timedelta(seconds=t["hold_s"])
days = sorted({t["day"] for t in trips})
cs = sorted(trips, key=lambda t: t["_close_ts"]); cts=[t["_close_ts"] for t in cs]

base=defaultdict(float); gated=defaultdict(float); nd=defaultdict(int)
for t in trips:
    j = bisect.bisect_right(cts, t["entry_ts"])          # closed-before-entry
    fleet_realized = [x["pnl_usd"] for x in cs[:j]]       # close order
    size = regime_size(expectancy_dial(fleet_realized))  # SHIPPED path
    ws = size["would_size"]
    base[t["day"]] += t["pnl_usd"]
    gated[t["day"]] += t["pnl_usd"] * ws
    nd[t["day"]] += 1 if ws < 1.0 else 0
bt=sum(base.values()); gt=sum(gated.values())
print("SHIPPED-path shadow sim (regime_size default 0.3x on defense):")
for d in days:
    print(f"  {d}: base=${base[d]:>8.2f} -> would-size=${gated[d]:>8.2f}  "
          f"(delta ${gated[d]-base[d]:>+7.2f}; {nd[d]} downsized)")
print(f"  TOTAL: base=${bt:.2f} -> ${gt:.2f}  delta=${gt-bt:+.2f}")
