"""Dry-run routing verification for the patient_sleeve bot (2026-06-26).

Pipeline-trace gate (per the "trace before build" rule): confirm the fleet loader
(core.bot_registry, the same glob the app uses) actually picks up patient_sleeve,
that it's enabled, NON-badday (so it skips the -7 IN_FLIGHT_FLOOR), winner-gated, has
microcap_mandate (so the badday-lane admits sub-floor tokens to it), and resolves the
patient exit params. Read-only. Exit non-zero if anything is off.

Usage: python scripts/verify_patient_sleeve.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.bot_registry import BotRegistry

reg = BotRegistry.from_directory("config/bots")
by_id = {c.bot_id: c for c in reg.configs}
print(f"loaded {len(reg.configs)} bot configs; enabled: "
      f"{sum(1 for c in reg.configs if c.enabled)}")

ps = by_id.get("patient_sleeve")
checks = []
checks.append(("registered", ps is not None))
if ps is not None:
    checks += [
        ("enabled", ps.enabled is True),
        ("non-badday id (skips -7 IN_FLIGHT_FLOOR)", not ps.bot_id.startswith("badday_")),
        ("winner_select_entry gate ON", ps.winner_select_entry is True),
        ("microcap_mandate (lane admits sub-floor)", ps.microcap_mandate is True),
        ("rug guards on (antirug NOT exempt)", ps.antirug_floor_exempt is False),
        ("hard_stop -22", ps.hard_stop_pct == -22.0),
        ("time_stop 240", ps.time_stop_minutes == 240),
        ("wide slots >=20", ps.max_concurrent_positions >= 20),
        ("partial TP1 + ride", ps.tp1_sell_fraction + ps.tp2_sell_fraction < 1.0),
    ]
    print(f"\npatient_sleeve resolved exit params: hard_stop={ps.hard_stop_pct} "
          f"time_stop={ps.time_stop_minutes}min slots={ps.max_concurrent_positions} "
          f"tp1={ps.tp1_pct}@{ps.tp1_sell_fraction} tp2={ps.tp2_pct}@{ps.tp2_sell_fraction} "
          f"trail={ps.trail_pp}pp size=${ps.base_position_usd}")

print()
ok = True
for name, passed in checks:
    print(f"  [{'OK' if passed else 'FAIL'}] {name}")
    ok = ok and passed

if not ok:
    print("\nROUTING PROBLEM — patient_sleeve will NOT behave as designed. Fix before shipping.")
    sys.exit(1)
print("\nALL CHECKS PASS — patient_sleeve is registered and routed as designed.")
