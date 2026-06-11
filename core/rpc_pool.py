"""Free-tier RPC pool (2026-06-11). Alchemy key (free signup, $0) unlocks the
wallet-scoring throughput the public endpoints throttle. Key sources, in order:
ALCHEMY_API_KEY env var, then the gitignored local file alchemy_key.txt.
NEVER commit the key; never print it."""
from __future__ import annotations
import os


def alchemy_rpc_url():
    key = (os.environ.get("ALCHEMY_API_KEY") or "").strip()
    if not key:
        try:
            with open(os.path.join(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))), "alchemy_key.txt")) as f:
                key = f.read().strip()
        except Exception:
            key = ""
    return f"https://solana-mainnet.g.alchemy.com/v2/{key}" if key else None


def rpc_pool():
    """Alchemy first (when key present), then the public fallbacks."""
    pool = []
    a = alchemy_rpc_url()
    if a:
        pool.append(a)
    pool += ["https://api.mainnet-beta.solana.com",
             "https://solana-rpc.publicnode.com",
             "https://solana.drpc.org",
             "https://solana.leorpc.com/?api_key=FREE"]
    return pool
