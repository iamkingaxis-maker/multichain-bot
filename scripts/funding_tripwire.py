"""Perp funding-arb tripwire (2026-05-27).

The delta-neutral funding-harvest build was SHELVED: the edge is real on a
multi-year window (BTC +11% net APR, 88% positive) but BULL-CONDITIONAL and
currently dormant (2026-Q2 BTC funding ~+0.7% APR). This re-checks the gate so we
revisit the build only when the bull-funding regime returns.

GATE: BTC trailing-30d annualized funding APR > THRESHOLD_APR (Hyperliquid, the
venue we'd trade + the only non-geo-blocked data source). Was +20% in the 2024-Q4
bull, ~+0.7% in 2026-Q2 chop.

Run:  python scripts/funding_tripwire.py
See:  [[reference_perp_funding_arb_shelved_2026_05_27]]
"""
import urllib.request, json, ssl, time

THRESHOLD_APR = 8.0
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE


def hl_funding_30d(coin):
    start = int((time.time() - 30 * 86400) * 1000); now = int(time.time() * 1000)
    out, cur, g = [], start, 0
    while cur < now and g < 10:
        g += 1
        req = urllib.request.Request(
            "https://api.hyperliquid.xyz/info",
            data=json.dumps({"type": "fundingHistory", "coin": coin,
                             "startTime": cur, "endTime": now}).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"})
        d = json.loads(urllib.request.urlopen(req, context=CTX, timeout=45).read())
        if not d:
            break
        for x in d:
            out.append((int(x["time"]), float(x["fundingRate"])))
        nxt = max(int(x["time"]) for x in d) + 1
        if nxt <= cur:
            break
        cur = nxt
        if len(d) < 500:
            break
        time.sleep(0.12)
    return out


def apr(rows):
    if not rows:
        return None
    days = (rows[-1][0] - rows[0][0]) / 86400000.0 or 1
    return (sum(r for _, r in rows) / days) * 365 * 100


if __name__ == "__main__":
    print(f"Perp funding tripwire — gate: BTC trailing-30d APR > {THRESHOLD_APR:.0f}%")
    fired = False
    for c in ("BTC", "ETH", "SOL"):
        try:
            a = apr(hl_funding_30d(c))
            if a is None:
                print(f"  {c}: n/a"); continue
            tag = "TRIGGERED" if a > THRESHOLD_APR else "dormant"
            if c == "BTC" and a > THRESHOLD_APR:
                fired = True
            print(f"  {c}: {a:+6.1f}% APR  [{tag}]")
        except Exception as e:
            print(f"  {c}: err {repr(e)[:60]}")
    print("\n>>> TRIPWIRE FIRED — bull-funding regime back; revisit the perp-arb build."
          if fired else
          "\n>>> dormant — funding premium out of season. Keep shelved.")
