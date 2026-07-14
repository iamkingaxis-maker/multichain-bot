# RH wallet-truth → dashboard sync (2026-07-14 03:43 UTC)

AxiS goal: the RH hot wallet (~$39) should ALWAYS show its current on-chain
balance on the dashboard, exactly like the SOL wallet does via
`/api/wallet-truth`. Built the end-to-end sync, mirroring the SOL pattern.

## The one env var to set
```
RH_WALLET_ADDRESS=0x<the RH hot-wallet address>
```
Set it on the **rh-paper-lane** Railway service. That is the ONLY switch — the
whole feature is a clean no-op until it is set (no private key needed; the read
is KEYLESS / read-only; no trading behavior changes). No SOL, no live-trading,
no `RH_PRIVATE_KEY` touched.

## Data flow (all env-driven + fail-open)
```
rh-paper-lane service                          gracious-inspiration (dashboard)
──────────────────                             ────────────────────────────────
scripts/rh_paper_lane.py                        dashboard/web_dashboard.py
  orchestrate() maintenance cadence, ~5 min:
   lane.refresh_wallet_truth()   ── keyless ──► (reads chain itself, no key)
     rh_live.rh_wallet_truth(eth_price=feed) 
       writes rh_wallet_truth.json (rh_state_dir
       = scratchpad/robinhood_tapes by default)

scripts/rh_paper_upload.py  (child loop, ~180s)
   _push_wallet_truth()  POST json ───────────► POST /api/rh-wallet-truth/ingest
                                                   persists DATA_DIR/bot_state/
                                                   rh_wallet_truth.json
browser (every 60s) ───────────────────────────► GET  /api/rh-wallet-truth
                                                   → rh_wallet_truth_view()
                                                   → RH WALLET card renders
```

## Which service each change deploys to
| File | Service | Change |
|---|---|---|
| `scripts/rh_paper_lane.py` | **rh-paper-lane** | `WALLET_TRUTH_REFRESH_S=300`; `PaperLane.refresh_wallet_truth()` (keyless, no-op unless `RH_WALLET_ADDRESS` set, fail-open); hooked into `orchestrate()` maintenance loop off the event loop (never blocks the strategy thread) |
| `scripts/rh_paper_upload.py` | **rh-paper-lane** (main.py child loop) | `_push_wallet_truth()` ships `rh_wallet_truth.json` → `/api/rh-wallet-truth/ingest`; `main()` now runs `_push_ledger()` + `_push_wallet_truth()`. No-op unless `RH_WALLET_ADDRESS` set AND snapshot exists |
| `core/rh_live_execution.py` | both (shared) | `rh_wallet_truth()` now stamps `eth_price_usd` + `total_usd` unconditionally when a price is passed (was baseline-gated) so the card shows **total USD in paper mode, pre-live**. Baseline/delta semantics unchanged |
| `dashboard/web_dashboard.py` | **gracious-inspiration** | `rh_wallet_truth_view()` (pure), `GET /api/rh-wallet-truth`, `POST /api/rh-wallet-truth/ingest`, `_rh_wallet_truth_path()`, the **RH WALLET** card + JS updater |

## Endpoint + card added (dashboard)
- `GET /api/rh-wallet-truth` — serves the last pushed snapshot; `{available:false}`
  when nothing pushed (dormant). Adds `available` + `total_usd`.
- `POST /api/rh-wallet-truth/ingest` — persists the snapshot to
  `DATA_DIR/bot_state/rh_wallet_truth.json` (Basic-auth via the app-wide
  middleware, same as `/api/rh-paper/ingest`).
- **RH WALLET** card (id `rh-wallet-panel`) — sits right under the SOL WALLET
  TRUTH card, `display:none` until a snapshot arrives. Shows: total (USD), Δ
  since baseline (USD + ETH), ETH, WETH, total (ETH), wallet mask. Mirrors the
  SOL card's look and 60s refresh.

## Behavior notes
- **Delta vs baseline** arms only in live (`RH_PAPER_MODE=false` + gate open),
  exactly like the SOL card shows "paper mode" for Δ until go-live. In paper
  mode the card shows the live **balance + total USD** (Δ blank) — that is the
  "always show the balance now" ask.
- **Fail-open everywhere**: unset `RH_WALLET_ADDRESS` → lane/uploader skip, card
  stays hidden; RPC read error → snapshot carries `ok:false`+`error`, card shows
  the error, NEVER a stale/zero number (2026-07-10 incident class).
- Path alignment verified: lane writes and uploader reads the same
  `rh_wallet_truth.json` under both the default dir and an `RH_LIVE_STATE_DIR`
  override.

## Verification
- `tests/test_rh_paper_endpoint.py` (+7 `rh_wallet_truth_view` tests),
  `tests/test_rh_live_execution.py` (+1 paper-mode `total_usd` test),
  `tests/test_rh_pre_live_invariants.py` — **100 passed**.
- Live keyless read against the RH public RPC (burn address) returned real
  eth_now/weth_now/total_eth and `total_usd` shaped through the view fn.

## NOT done (per instruction)
- No deploy / push. AxiS sets `RH_WALLET_ADDRESS` on rh-paper-lane and deploys.
