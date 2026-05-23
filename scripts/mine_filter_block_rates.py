"""Mine production block rates per ENFORCED filter.

Strategy: for each recent buy in /api/trades, count how many candidates
had filter_X_verdict=BLOCK in the entry_meta. This proxies the production
block rate without log scraping.

Note: this counts SHADOW verdicts too. A filter that records BLOCK but
isn't enforced still shows up. The output flags which are ENFORCED vs
SHADOW by cross-referencing the filter inventory.
"""
import json
from collections import Counter
from pathlib import Path

import requests


PROD_URL = "https://gracious-inspiration-production.up.railway.app/api/trades"
INVENTORY_PATH = (
    Path(__file__).parent.parent
    / "docs" / "superpowers" / "notes"
    / "2026-05-23-filter-chain-inventory.md"
)


def load_enforced_filter_names() -> set[str]:
    """Parse the SP2 inventory file for the canonical list of ENFORCED filter names."""
    if not INVENTORY_PATH.exists():
        return set()
    names: set[str] = set()
    for line in INVENTORY_PATH.read_text().splitlines():
        # Lines look like: "| filter_fake_bounce | 2237 | ~2240 |"
        if line.startswith("|") and "filter_" in line:
            cells = [c.strip() for c in line.split("|")]
            for cell in cells:
                if cell.startswith("filter_") and " " not in cell:
                    names.add(cell)
    return names


def fetch_trades(n: int = 500) -> list[dict]:
    resp = requests.get(f"{PROD_URL}?full=1&limit={n}")
    resp.raise_for_status()
    return resp.json()


def count_block_verdicts(trades: list[dict]) -> Counter:
    block_counts: Counter = Counter()
    for t in trades:
        meta = t.get("entry_meta") or {}
        for key, val in meta.items():
            if not key.endswith("_verdict"):
                continue
            if val != "BLOCK":
                continue
            # Strip the "_verdict" suffix to get the filter name
            filter_name = key[: -len("_verdict")]
            if not filter_name.startswith("filter_"):
                filter_name = f"filter_{filter_name}"
            block_counts[filter_name] += 1
    return block_counts


def main():
    enforced = load_enforced_filter_names()
    print(f"Loaded {len(enforced)} ENFORCED filter names from SP2 inventory")

    trades = fetch_trades(500)
    print(f"Fetched {len(trades)} trades from production")

    buys = [t for t in trades if t.get("type") == "buy"]
    print(f"Analyzing {len(buys)} buys")

    blocks = count_block_verdicts(buys)
    print(f"Found {len(blocks)} distinct filter verdicts across all buys")

    out_path = (
        Path(__file__).parent.parent / "docs" / "superpowers" / "notes"
        / "2026-05-23-filter-block-rates.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    lines = ["# Filter block rates - mined 2026-05-23", "", ""]
    lines.append(f"Sample: {len(buys)} buys from production /api/trades.")
    lines.append("")
    lines.append("BLOCK count = number of buy candidates where this filter's verdict was BLOCK")
    lines.append("(SHADOW filters block but don't enforce; ENFORCED filters are marked).")
    lines.append("")
    lines.append("| Rank | Filter name | BLOCK count | ENFORCED? |")
    lines.append("|---:|---|---:|:---:|")

    ranked = blocks.most_common()
    for i, (name, count) in enumerate(ranked, start=1):
        is_enforced = "yes" if name in enforced else "shadow"
        lines.append(f"| {i} | {name} | {count} | {is_enforced} |")

    lines.append("")
    lines.append("## Top 10 ENFORCED filters (use for SP3 Block 2 ablations)")
    lines.append("")
    enforced_only = [(n, c) for n, c in ranked if n in enforced][:10]
    for i, (name, count) in enumerate(enforced_only, start=1):
        lines.append(f"{i}. `{name}` - {count} blocks observed")

    out_path.write_text("\n".join(lines))
    print(f"Wrote ranking to {out_path}")
    print("\nTop 10 ENFORCED:")
    for i, (name, count) in enumerate(enforced_only, start=1):
        print(f"  {i}. {name}: {count}")


if __name__ == "__main__":
    main()
