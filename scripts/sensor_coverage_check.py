"""SENSOR COVERAGE CHECK — gap #2 of the 2026-06-12 airtight pass.

PumpPortal streams pump.fun/pumpswap activity; a panel wallet trading on
other venues (Raydium via Jupiter, etc.) is partially invisible to the
sensor. This compares each wallet's sensor episode rate (24h) against its
CHAIN rate measured by the RPC decode probe (2026-06-12 baseline) and flags
big shortfalls — those wallets' archetype boards are venue-biased.

Run in the morning ritual once the sensor has ~a day of data:
  python scripts/sensor_coverage_check.py
"""
from __future__ import annotations
import json
import sys
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# roundtrips/day measured on-chain by scripts/wallet_meta_sensor_probe.py
# (400 sigs, 2026-06-12). Update when the probe is re-run.
CHAIN_RATE_BASELINE = {
    "HmP3TxuV": 357, "2x99WSHD": 163, "1eveYYxZ": 720, "2qnHs8fZ": 565,
    "EGwERj1n": 298, "dmuXAmcX": 62, "7Gi3RNdV": 363, "AKprbkX7": 213,
    "2tYcXQCf": 350, "D1aDZDVX": 397, "udH4u5k3": 168, "V21GW8PG": 126,
    "HcLMmNx9": 287, "4jkL4dNk": 800, "45Sn4KL1": 650,
}

API = "https://gracious-inspiration-production.up.railway.app/api/meta-sensor"


def main():
    d = json.load(urllib.request.urlopen(API, timeout=60))
    got = d.get("wallet_episodes_24h") or {}
    age = d.get("last_score_age_secs")
    print(f"sensor: scored_24h={d.get('scored_24h')} open={d.get('open_episodes')} "
          f"last_score_age={age}s")
    if age is None:
        print("⚠ NO SCORES YET — stream warming or dead (check PumpPortal connected)")
        return
    if age > 3600:
        print(f"⚠ STALE: last score {age/60:.0f}min ago — stream may be down")
    print(f"\n{'wallet':10s}{'sensor/24h':>11s}{'chain/24h':>11s}{'coverage':>10s}")
    for w, chain in sorted(CHAIN_RATE_BASELINE.items(), key=lambda kv: -kv[1]):
        n = got.get(w, 0)
        cov = n / chain if chain else 0
        flag = "  ⚠ venue-blind" if cov < 0.3 else ("  (silent?)" if n == 0 else "")
        print(f"{w:10s}{n:11d}{chain:11d}{cov:10.0%}{flag}")
    extra = {w: n for w, n in got.items() if w not in CHAIN_RATE_BASELINE}
    if extra:
        print(f"\nwallets scoring but not in baseline: {extra}")


if __name__ == "__main__":
    main()
