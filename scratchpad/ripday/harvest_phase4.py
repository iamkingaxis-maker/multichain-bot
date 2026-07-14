"""Final io re-sweep of ACTIVE pairs only (accumulate freshest tape + sell legs)."""
import asyncio, sys

sys.path.insert(0, ".")
sys.path.insert(0, "scratchpad/ripday")
import harvest_driver as hd

def main():
    act = hd.active_targets()
    hd.log("phase4: final active sweep %d pairs" % len(act))
    asyncio.run(hd.sweep(act, "sweepF"))
    hd.log("phase4: complete")

if __name__ == "__main__":
    main()
