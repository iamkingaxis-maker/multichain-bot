# scripts/rh_make_wallet.py
"""Generate the RH LIVE FILL PROBE hot wallet (2026-07-12).

Creates a fresh eth_account keypair, writes the PRIVATE KEY to
rh_wallet_key.txt at the repo root (VERIFIED gitignored — this script FAILS
CLOSED if .gitignore does not cover the file), and prints ONLY the ADDRESS
plus funding instructions. The key is NEVER printed, logged, or echoed.

Usage:  python scripts/rh_make_wallet.py

If rh_wallet_key.txt already exists the script REFUSES to overwrite it and
just re-prints the existing wallet's address (derived locally).
"""
import os
import stat
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
sys.path.insert(0, _ROOT)

KEY_BASENAME = "rh_wallet_key.txt"
KEY_PATH = os.path.join(_ROOT, KEY_BASENAME)
GITIGNORE = os.path.join(_ROOT, ".gitignore")

FUNDING_NOTES = """
FUNDING (Robinhood Chain, chain_id 4663 — an Arbitrum Orbit L2, gas = ETH):
  1. Send ETH to the address above ON ROBINHOOD CHAIN (not mainnet/Arbitrum
     One). Robinhood app withdrawals to RH chain, or the chain's canonical
     bridge, land as native ETH.
  2. Suggested probe funding: ~$40-50 of ETH — 4 x $7.50 entries + $2 dust
     test + gas headroom (measured gas is ~$0.005/side; the executor's
     RH_LIVE_MAX_GAS_COST_ETH cap bounds runaways).
  3. Verify arrival (read-only, keyless):
       RH_WALLET_ADDRESS=<address> python -c "from core.rh_live_execution \\
           import rh_wallet_truth; print(rh_wallet_truth())"
  4. The key goes into the RH_PRIVATE_KEY env var of the session/service
     that runs the probe (env ONLY — never a committed file, never Railway
     unless AxiS says so). rh_wallet_key.txt is the local gitignored backup.
"""


def gitignore_covers(gitignore_path: str = GITIGNORE,
                     basename: str = KEY_BASENAME) -> bool:
    """True when .gitignore has an exact-line rule for the key file.
    FAIL-CLOSED: unreadable .gitignore counts as NOT covered."""
    try:
        with open(gitignore_path, encoding="utf-8") as f:
            lines = {ln.strip() for ln in f}
        return basename in lines or ("/" + basename) in lines
    except Exception:
        return False


def main() -> int:
    from eth_account import Account

    if not gitignore_covers():
        print(f"REFUSING to write {KEY_BASENAME}: .gitignore does not cover "
              f"it (add the exact line '{KEY_BASENAME}' first).")
        return 1

    if os.path.exists(KEY_PATH):
        try:
            with open(KEY_PATH, encoding="utf-8") as f:
                acct = Account.from_key(f.read().strip())
        except Exception as e:
            print(f"{KEY_BASENAME} exists but is unreadable as a key ({e}). "
                  f"Refusing to overwrite — move it aside manually first.")
            return 1
        print(f"{KEY_BASENAME} already exists — NOT overwritten.")
        print(f"Existing probe wallet address: {acct.address}")
        print(FUNDING_NOTES)
        return 0

    acct = Account.create()
    key_hex = acct.key.hex()
    if not key_hex.startswith("0x"):
        key_hex = "0x" + key_hex
    # write the key, then tighten perms best-effort (Windows: read-only bit)
    with open(KEY_PATH, "w", encoding="utf-8") as f:
        f.write(key_hex + "\n")
    try:
        os.chmod(KEY_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0o600 where honored
    except Exception:
        pass
    del key_hex  # never keep a printable copy around

    print("RH probe hot wallet CREATED.")
    print(f"  key file : {KEY_PATH}  (gitignored, never printed)")
    print(f"  address  : {acct.address}")
    print(FUNDING_NOTES)
    return 0


if __name__ == "__main__":
    sys.exit(main())
