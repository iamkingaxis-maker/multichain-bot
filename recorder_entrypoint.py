"""Railway side-service entrypoint for the universe dip recorder.

This is a thin wrapper so Railway can point its startCommand at the repo
root (where the trading bot already lives) and run the recorder instead
of main.py. The two services share the same code + volume but run
different processes.

Railway service setup:
  - New service in the same project as gracious-inspiration
  - Same GitHub repo
  - startCommand: python recorder_entrypoint.py
  - Volume mount: /data (so RECORDER_DATA_DIR=/data/universe_recorder persists)
  - Env vars:
      RECORDER_DATA_DIR=/data/universe_recorder
      RECORDER_CYCLE_S=120        (optional, default 120)
      RECORDER_OUTCOME_MIN=30     (optional, default 30)
"""
from __future__ import annotations
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scripts.universe_dip_recorder import main as recorder_main, parse_args
import asyncio


class _ArgsFromEnv:
    """Mimic argparse Namespace using env vars (Railway has no CLI args)."""
    def __init__(self):
        self.cycle_s = int(os.environ.get("RECORDER_CYCLE_S", "120"))
        self.outcome_min = int(os.environ.get("RECORDER_OUTCOME_MIN", "30"))


if __name__ == "__main__":
    # Allow CLI args to override env (handy for local testing).
    if len(sys.argv) > 1:
        args = parse_args()
    else:
        args = _ArgsFromEnv()
    print(f"Starting universe recorder: cycle_s={args.cycle_s} outcome_min={args.outcome_min}")
    try:
        asyncio.run(recorder_main(args))
    except KeyboardInterrupt:
        pass
