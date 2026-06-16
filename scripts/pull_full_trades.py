# -*- coding: utf-8 -*-
"""Pull the FULL trade dataset ONCE to a local file — the egress chokepoint for mining workflows.

WHY: mining workflows fan out many agents. If each agent curls /api/trades?limit=5000&full=1
(~74MB raw) that's N x 74MB of Railway egress (the documented bill driver). Instead the MAIN LOOP
runs this ONCE before launching the workflow, and every agent READS the local file (zero egress).
~20x egress cut on a typical 20-agent mine, no loss of capability.

Idempotent + freshness-gated: if _full_trades.json exists and is younger than --max-age-min
(default 30), it SKIPS the pull (no egress). --force overrides.

USAGE (main loop, before a mining Workflow):
  python scripts/pull_full_trades.py            # pull once if stale -> _full_trades.json
  python scripts/pull_full_trades.py --force     # always re-pull

THEN agent prompts must say: "Load the pre-pulled local file _full_trades.json (json.load) and
filter to your bot. Do NOT curl the API / &full=1 per-agent (egress discipline)."
"""
import sys, os, time, json, gzip, io, urllib.request

URL = "https://gracious-inspiration-production.up.railway.app/api/trades?limit=5000&full=1"
OUT = "_full_trades.json"


def main():
    force = "--force" in sys.argv
    max_age_min = 30
    if "--max-age-min" in sys.argv:
        try:
            max_age_min = float(sys.argv[sys.argv.index("--max-age-min") + 1])
        except Exception:
            pass
    if not force and os.path.exists(OUT):
        age_min = (time.time() - os.path.getmtime(OUT)) / 60.0
        if age_min < max_age_min:
            try:
                n = len(json.load(open(OUT)))
            except Exception:
                n = "?"
            print(f"FRESH: {OUT} is {age_min:.0f}min old (<{max_age_min:.0f}), {n} records — SKIP pull (0 egress).")
            print("Agents: json.load('_full_trades.json'); do NOT curl &full=1 per-agent.")
            return
    req = urllib.request.Request(URL, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=300) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
    d = json.loads(raw)
    trades = d if isinstance(d, list) else d.get("trades", [])
    json.dump(trades, open(OUT, "w"))
    mb = os.path.getsize(OUT) / 1e6
    print(f"PULLED ONCE: {len(trades)} records -> {OUT} ({mb:.0f}MB). This is the SINGLE egress for the mine.")
    print("Agents: json.load('_full_trades.json'); do NOT curl &full=1 per-agent.")


if __name__ == "__main__":
    main()
