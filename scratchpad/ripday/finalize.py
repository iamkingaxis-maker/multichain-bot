"""LOCAL: final pipeline = compact tapes -> score wallets -> recon -> union."""
import runpy, sys
sys.path.insert(0, ".")
for script in ("compact_tapes", "score_wallets", "recon_local", "build_union"):
    print("== %s ==" % script)
    runpy.run_path("scratchpad/ripday/%s.py" % script, run_name="__main__")
