# Free Real-Time Price Feeds — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement task-by-task with review. Steps use `- [ ]`.

**Goal:** Take fill latency from ~85s (main sweep) to ~8s for the WHOLE watchlist (free), and ~1-2s for the hot subset (free), with no paid feeds. Replace dead Axiom + the armed-subset compromise.

**Design basis:** the empirical free-feed bakeoff (workflow `wf_68c0695f-460`), adversarially verified. Key MEASURED facts:
- **Jupiter `lite-api.jup.ag/price/v3`** — free/keyless; HARD cap **50 ids/call** (silently truncates beyond — money-losing trap; chunk at 50 + serialize). 315 tokens = 7 calls. **5s cadence = 84 req/min, verified 84/84 live**, under the ~110/min CloudFront ceiling. Coverage 50/50 incl sub-$2k-liq pump.fun. Returns `usdPrice` + `blockId` (use vs getSlot for staleness). Strip `\r` (Windows CRLF → HTTP 000). Fill latency for a *moving* token ~8s.
- **DexScreener batch** — free, SEPARATE CDN. Response capped at **30 PAIRS** → memecoins have 2-3 pairs each → only ~8-10 mints/call usable (silent token drop beyond). ~300/min. Slow cross-CDN BACKSTOP only.
- **On-chain via free public RPC** — `accountSubscribe` WS = push, ~0.4-1s, zero slot lag; **~100 subs/connection** then code 1013 → ~4 connections for 315 (risky on public RPC). HTTP `getMultipleAccounts` batch=100 but a HARD **40-req-then-429** wall (~200/min). pump.fun price lives in the **bonding-curve PDA** (`seeds=['bonding-curve', mint]`, program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`); struct = 8B disc + `virtual_token_reserves`/`virtual_sol_reserves`/`real_token_reserves`/`real_sol_reserves`/`token_total_supply` (u64) + `complete` (bool); `price_sol = (vSOL/1e9)/(vTOK/1e6)`, ×SOL/USD. **`complete=1`/vtr=0 = MIGRATED** to pump-AMM (`pAMMBay6...`) or Raydium → different decoder + per-token pool discovery. So on-chain is the true 1-2s path but HIGH effort + reliability risk → reserve for the small hot subset.

**Tech:** Python, asyncio, aiohttp, websockets/websocket-client, pytest. All changes flag-gated default-off, paper (PAPER_MODE untouched).

---

## PART A — Jupiter-primary whole-watchlist feed (the workhorse, ~8s, low risk)

### Task A1: Jupiter batch fetch in `feeds/price_feed.py`

**Files:** Modify `feeds/price_feed.py`; Test `tests/test_jupiter_feed.py` (new).

- [ ] **Step 1: failing test** — `tests/test_jupiter_feed.py`:

```python
import asyncio, types
from feeds import price_feed as pf

def test_chunk_50_serialized():
    from feeds.price_feed import _jup_chunks
    ids = [f"m{i}" for i in range(120)]
    chunks = _jup_chunks(ids, 50)
    assert [len(c) for c in chunks] == [50, 50, 20]

def test_parse_jupiter_payload():
    from feeds.price_feed import _parse_jupiter
    payload = {"AAA": {"usdPrice": 0.0012, "blockId": 1000}, "BBB": {"usdPrice": None}, "CCC": {}}
    out = _parse_jupiter(payload)
    assert out["AAA"] == (0.0012, 1000)
    assert "BBB" not in out and "CCC" not in out   # null/missing price dropped

def test_strip_crlf_in_ids():
    from feeds.price_feed import _jup_clean_ids
    assert _jup_clean_ids(["AAA\r", " BBB ", "", "CCC\n"]) == ["AAA", "BBB", "CCC"]
```

- [ ] **Step 2:** `python -m pytest tests/test_jupiter_feed.py -q` → FAIL (no module fns).

- [ ] **Step 3:** Add to `feeds/price_feed.py` (module-level helpers + a method). Pure helpers first:

```python
def _jup_clean_ids(ids):
    """Strip CRLF/whitespace, drop empties (Windows CRLF breaks the URL -> HTTP 000)."""
    return [s.strip() for s in ids if s and s.strip()]

def _jup_chunks(ids, size=50):
    """Jupiter hard-caps at 50 ids/call and SILENTLY truncates beyond -> must chunk at 50."""
    return [ids[i:i+size] for i in range(0, len(ids), size)]

def _parse_jupiter(payload):
    """resp[mint] -> (usdPrice float, blockId|None); drop null/missing prices."""
    out = {}
    for mint, v in (payload or {}).items():
        if not isinstance(v, dict):
            continue
        p = v.get("usdPrice")
        if p is None:
            continue
        try:
            out[mint] = (float(p), v.get("blockId"))
        except (TypeError, ValueError):
            continue
    return out
```

Method on the feed class (mirror `_poll_batch`, serialize chunks):

```python
    async def _poll_batch_jupiter(self, addresses: list):
        """Jupiter lite price/v3 — keyless, 50 ids/call, SERIALIZED (parallel burst = 429 self-DoS).
        Writes price_cache via _process_jupiter_price. Returns count fetched."""
        ids = _jup_clean_ids(addresses)
        fetched = 0
        for chunk in _jup_chunks(ids, 50):
            url = "https://lite-api.jup.ag/price/v3?ids=" + ",".join(chunk)
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=8)) as resp:
                        if resp.status != 200:
                            if resp.status == 429:
                                self._jup_backoff_until = time.time() + 60.0  # no Retry-After; ~60-70s clear
                                logger.warning("[PriceFeed] Jupiter 429 — 60s backoff, failing over to DexScreener")
                            return fetched
                        data = await resp.json(content_type=None)
            except Exception as e:
                logger.debug("[PriceFeed] Jupiter fetch error: %s", e)
                return fetched
            for mint, (price, block) in _parse_jupiter(data).items():
                m = mint.lower()
                if m in self._watched:
                    await self._process_jupiter_price(m, price, block)
                    fetched += 1
        return fetched
```

Add `self._jup_backoff_until = 0.0` in `__init__`, and a minimal `_process_jupiter_price`:

```python
    async def _process_jupiter_price(self, addr_lower, price, block_id):
        """Feed a Jupiter quote into the same cache/sample path as poll updates."""
        if price <= 0:
            return
        self.price_cache[addr_lower] = price
        self._latest[addr_lower] = PriceTick(price=price, source="jupiter", ts=time.time())
```

(Match the actual `PriceTick`/cache field names in the file — read them first; adapt if different.)

- [ ] **Step 4:** `python -m pytest tests/test_jupiter_feed.py -q` → PASS. `python -c "import feeds.price_feed"` OK.
- [ ] **Step 5:** commit `feat(price-feed): Jupiter batch fetch (50/call serialized, keyless)`.

### Task A2: Make Jupiter primary with DexScreener backstop + fix dead-Axiom text

**Files:** Modify `feeds/price_feed.py` (the poll loop ~line 230-257 + the 429 warning at 288).

- [ ] **Step 1: test** — `tests/test_jupiter_feed.py`: a test that with `JUPITER_PRICE_PRIMARY=on` the loop calls `_poll_batch_jupiter` and only falls to `_poll_batch` (DexScreener) when `_jup_backoff_until` is in the future. Use monkeypatched stubs recording call order.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** In the poll loop, gate: when `os.environ.get("JUPITER_PRICE_PRIMARY","off")` is on AND `time.time() >= self._jup_backoff_until`, sweep via `_poll_batch_jupiter`; else `_poll_batch` (DexScreener). Set the loop sleep to `JUPITER_POLL_SECS` (default 5.0). Fix the `:288` warning text — remove "Helius/Axiom feeds carry Solana stops" (Axiom is dead), replace with "Jupiter/DexScreener cross-CDN failover active."
- [ ] **Step 4:** tests pass; import OK.
- [ ] **Step 5:** commit `feat(price-feed): Jupiter primary + DexScreener backstop (flag JUPITER_PRICE_PRIMARY, default off); drop dead-Axiom text`.

### Task A3: Repoint fast-watch to Jupiter + poll the WHOLE watchlist

**Files:** Modify `feeds/dip_scanner.py` (`_fast_batch_prices` → Jupiter; `_fast_arm_subset` → no cap, whole in-band watchlist when Jupiter primary). Test `tests/test_fast_watch.py`.

- [ ] **Step 1: test** — `_fast_batch_prices` uses Jupiter chunking (50) + parses usdPrice; with `JUPITER_PRICE_PRIMARY=on` the armed set is the whole in-band watchlist (no 30 cap). Add tests asserting batch builds 50-id Jupiter URLs and arm includes all in-band tokens.
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Change `_fast_batch_prices` to call Jupiter (`lite-api.jup.ag/price/v3`, chunk 50, serialize, parse `usdPrice`) when `JUPITER_PRICE_PRIMARY=on`, else the current DexScreener path. In `_fast_arm_subset`, when Jupiter is primary, set `armed_max` effectively to the whole in-band watchlist (still `|pc_h1|<=arm_band_pp` in-play filter is OPTIONAL — with whole-watchlist coverage we can arm all in-band; keep the in-play filter as a cheap reducer but raise the cap to e.g. 320). Keep the bidirectional `move_fires` trigger.
- [ ] **Step 4:** `python -m pytest tests/test_fast_watch.py -q` + regression suites pass; import OK; pre-live invariants OK.
- [ ] **Step 5:** commit `feat(fast-watch): poll whole watchlist via Jupiter when primary (drop armed cap)`.

### Task A4: Deploy + dry-run validate (runtime; paper)

- [ ] Push + `railway up`; set `JUPITER_PRICE_PRIMARY=on` after warm. Confirm `PAPER_MODE=true`.
- [ ] Validate: logs show Jupiter sweeps; coverage ≥99% of watchlist returns usdPrice; req/min < 110 over 5 min (no sustained 429); fast-watch `armed≈watchlist`, `polled≈armed`; fill latency on a moving token ~8s. Fix the dead-Axiom warning is gone.

---

## PART B — On-chain RPC WS hot-subset feed (the precision layer, ~1-2s, high effort, flag-gated/shadow)

### Task B1: Pure bonding-curve decoder `core/onchain_price.py`

**Files:** Create `core/onchain_price.py`; Test `tests/test_onchain_price.py`.

- [ ] **Step 1: failing tests** — decode the bonding-curve account struct + price math from known bytes:

```python
from core.onchain_price import decode_bonding_curve, bonding_curve_pda, price_sol_from_curve

def test_decode_struct():
    import struct
    disc = b"\x00"*8
    body = struct.pack("<QQQQQ", 1_000_000_000_000, 30_000_000_000, 800_000_000_000, 5_000_000_000, 1_000_000_000_000)
    flag = b"\x01"
    acct = disc + body + flag
    d = decode_bonding_curve(acct)
    assert d["virtual_token_reserves"] == 1_000_000_000_000
    assert d["virtual_sol_reserves"] == 30_000_000_000
    assert d["complete"] is True

def test_price_sol():
    # price_sol = (vSOL/1e9) / (vTOK/1e6)
    d = {"virtual_sol_reserves": 30_000_000_000, "virtual_token_reserves": 1_000_000_000_000_000, "complete": False}
    p = price_sol_from_curve(d)
    assert abs(p - ((30_000_000_000/1e9)/(1_000_000_000_000_000/1e6))) < 1e-18

def test_migrated_returns_none():
    d = {"virtual_token_reserves": 0, "complete": True, "virtual_sol_reserves": 0}
    assert price_sol_from_curve(d) is None   # migrated -> bonding curve dead

def test_pda_is_deterministic():
    a = bonding_curve_pda("9h66V2NiHU3PpviwceSg4KZ7xqStLTDej58o5pdhpump")
    b = bonding_curve_pda("9h66V2NiHU3PpviwceSg4KZ7xqStLTDej58o5pdhpump")
    assert a == b and isinstance(a, str)
```

- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** Implement with `solders`/`based58` (check what's installed; the RPC probe used solders). `decode_bonding_curve(bytes)` (struct `<8x QQQQQ ?`), `price_sol_from_curve` (None if `complete and vtr==0` or any reserve 0), `bonding_curve_pda(mint)` via `Pubkey.find_program_address([b"bonding-curve", mint_bytes], PUMP_PROGRAM)`.
- [ ] **Step 4:** tests pass.
- [ ] **Step 5:** commit `feat(onchain): pure pump.fun bonding-curve decoder + price math`.

### Task B2: Pool-type resolver + migrated-token handling

**Files:** `core/onchain_price.py`; Test `tests/test_onchain_price.py`.

- [ ] Add `resolve_price_account(mint, account_bytes_by_pubkey)` returning `{kind: 'bonding'|'migrated'|'unknown', account, decoder}`. For v1: decode bonding-curve; if `complete`/migrated, return kind='migrated' (handled later or skipped). Test bonding vs migrated classification. (Raydium/pump-AMM decoders are a documented follow-up — v1 covers live bonding-curve tokens and SKIPS migrated, logging the skip count so we know coverage.)
- [ ] commit `feat(onchain): pool-type resolver (bonding live; migrated skipped+counted)`.

### Task B3: accountSubscribe WS manager (hot subset only)

**Files:** Create `core/onchain_ws_feed.py`; Test `tests/test_onchain_ws_feed.py` (logic/parse tests with a fake ws).

- [ ] `OnchainWsFeed`: given a small set of mints (the armed/open subset, ≤~80), resolve each to its bonding-curve PDA, open ≤N connections (≤90 subs each) to `WS_RPC_URL` (env, default `wss://api.mainnet-beta.solana.com`), `accountSubscribe(commitment=processed)`, on notification decode → `price_sol × sol_usd` → write `self.price_cache[mint]=usd`, `self.ts[mint]`. Handle code 1013 (open new connection). SOL/USD from an existing source (reuse the scanner's cycle SOL price). Flag `ONCHAIN_WS_MODE` (off/shadow/on, default off). Tests: subscription chunking ≤90; notification→decode→cache; migrated/None skipped; reconnect on 1013; exception-safe.
- [ ] commit `feat(onchain): accountSubscribe WS feed for hot subset (flag default off)`.

### Task B4: Wire + shadow-validate against Jupiter

**Files:** `feeds/dip_scanner.py` (spawn `OnchainWsFeed` for the armed/open subset; in `_fast_watch_tick`, prefer on-chain price when fresher than Jupiter), Test + runtime.

- [ ] Spawn the WS feed scoped to `self._fast_armed` + open positions. In the tick, for an armed token, use the on-chain price if `ts` is within ~2s, else Jupiter. **Shadow first:** `ONCHAIN_WS_MODE=shadow` LOGS `[onchain] token=X onchain=$.. jupiter=$.. delta=..%` so we VALIDATE the on-chain decode matches Jupiter (within tolerance) before any bot reads it. Only `=on` lets the price drive decisions.
- [ ] Tests + import + pre-live invariants. Deploy shadow; confirm on-chain prices track Jupiter (low delta) + ~1-2s freshness before considering `=on`.
- [ ] commit `feat(onchain): wire WS hot-layer into fast-watch (shadow-validated vs Jupiter)`.

---

## Sequencing & safety
- Build **Part A fully first** (it's the high-value workhorse; gets every token to ~8s) → deploy + validate → THEN Part B (precision layer for the hot subset).
- Everything flag-gated default-off; PAPER_MODE never flipped by this work; on-chain prices shadow-validated vs Jupiter before they drive any decision.
- Reviews: Part A1-A3 + all of Part B get a code review (Part B is money-path-adjacent — adversarial review for wrong-decode → phantom price).

## Self-review
- Spec coverage: Jupiter primary whole-watchlist (A1-A3), DexScreener backstop + dead-Axiom fix (A2), validate (A4); on-chain decoder (B1), resolver/migration (B2), WS manager (B3), shadow-validated wiring (B4). Rate math from MEASURED limits (50/call, 5s; 90 subs/conn). Honest latency: A=~8s, B=~1-2s hot subset.
- Placeholders: none — code given for each step; Raydium/pump-AMM decoders explicitly deferred (v1 covers bonding-curve, skips+counts migrated).
- Types: `_jup_chunks/_jup_clean_ids/_parse_jupiter`, `_poll_batch_jupiter/_process_jupiter_price`, `decode_bonding_curve/price_sol_from_curve/bonding_curve_pda`, `OnchainWsFeed` used consistently.
