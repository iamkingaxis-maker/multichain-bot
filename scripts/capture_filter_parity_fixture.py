"""Captures recent production trades + their entry_meta as a parity fixture.

A 'candidate' for parity testing is the full set of features the scanner
had when it decided buy/skip on that token at that moment. We use
entry_meta from production trades as a proxy.

Output: tests/fixtures/filter_parity_candidates.json
"""
import json
from pathlib import Path

import requests


PROD_URL = "https://gracious-inspiration-production.up.railway.app/api/trades"


def fetch_buys(n: int = 50) -> list[dict]:
    """Fetch the last N buy records with full entry_meta from production."""
    resp = requests.get(f"{PROD_URL}?full=1&limit=500")
    resp.raise_for_status()
    trades = resp.json()
    buys = [t for t in trades if t.get("type") == "buy"]
    # Take the most recent N (trades are ordered chronologically)
    return buys[-n:]


def main():
    out_path = Path(__file__).parent.parent / "tests" / "fixtures" / "filter_parity_candidates.json"
    out_path.parent.mkdir(exist_ok=True)

    candidates = []
    for buy in fetch_buys(50):
        candidates.append({
            "token": buy.get("token", "?"),
            "entry_meta": buy.get("entry_meta", {}),
            "expected_outcome": "BUY",
            "expected_skip_reason": None,
        })

    out = {"candidates": candidates}
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Wrote {len(candidates)} candidates to {out_path}")


if __name__ == "__main__":
    main()
