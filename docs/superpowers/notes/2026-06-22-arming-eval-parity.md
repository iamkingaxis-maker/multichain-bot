# Arming / per-bot eval tick parity — `badday_flush_nf15` vs `badday_flush_nf15_live`

**Task:** Task 9 of the paper↔live 1:1 fidelity plan. Determine whether the paper
twin and its live replica evaluate the SAME armed token set on the SAME tick, or
whether one can be silently dropped — fix ONLY if a real divergence exists.

**Verdict: NO code-level divergence on the eval/arming path.** The two replicas are
evaluated symmetrically. The documented 6.4h live silence is operational, not an
arming/eval asymmetry. One *deployment-config* asymmetry vector exists (the
fast-watch allowlist env string) and is flagged below as a residual to watch; it is
not a code bug, so no code fix was made. A findings doc is the deliverable.

---

## What was traced (file:line evidence)

### 1. The per-bot fan-out is one shared pass over ALL enabled evaluators
`feeds/dip_scanner.py:19177-19203` — every token's `_evaluate_pair` calls
`bot_manager.evaluate_all_async(bundle, ..., bot_allowlist=_fp_allow)` (falling
back to the byte-identical sync `evaluate_all` at :19200).

`core/bot_manager.py:39-55` (`evaluate_all`) and `:79-98` (`evaluate_all_async`):

```python
for ev in self.evaluators:
    if not ev.config.enabled:                                  # both nf15 + nf15_live are enabled:true
        continue
    if bot_allowlist is not None and ev.config.bot_id not in bot_allowlist:
        continue                                               # None on the MAIN scan -> no bot dropped
    d = ev.evaluate(bundle, realized_pnl_usd=...)              # SAME bundle, SAME tick
```

On the **main scan path** `_fp_allow is None` (`feeds/dip_scanner.py:19211` notes
"`_fp_allow` is None ... on the main path -> all bots, real fire"). So both
`badday_flush_nf15` and `badday_flush_nf15_live` receive `ev.evaluate(bundle)`
against the identical `FeatureBundle` in the same iteration of the same loop. The
only per-bot gate here is `enabled` (both `true`) and the allowlist (`None`).

### 2. The two configs are entry-symmetric
`config/bots/badday_flush_nf15.json` vs `config/bots/badday_flush_nf15_live.json`:
identical `entry_gate` (6 clauses, incl. `net_flow_15s_imbalance >= 0`), identical
mcap/age/vol/stop/TP bounds, both `enabled:true`. Differences are downstream-only:
`_live` has `live_probe:true`; each has its OWN `exclusion_pool`
(`badday_flush_nf15` vs `badday_flush_nf15_live`) — separate pools, so they cannot
cross-block each other.

### 3. Live vs paper diverge DOWNSTREAM of eval, not at arming
The live/paper split is at the buy-routing layer, not the decision layer:
`core/probe_instrument.py:71-76` `should_route_live(live_probe, ultra_enabled,
has_private_key)` decides real-money routing AFTER the decision exists. The eval
itself is identical for both replicas.

### 4. Token-level filters are fleet-wide (cannot drop one replica)
The `_filters_block` chain (e.g. `filter_terminal_collapse`
`feeds/dip_scanner.py:16717-16753`, post-pump corpse, etc.) runs once per token
UPSTREAM of the per-bot fan-out and gates the token for ALL bots equally. It can
remove a token from both replicas, never from one.

### 5. Decision routing iterates every returned decision
`feeds/dip_scanner.py:3690-3718` `_fast_route_decisions` loops over every decision
`evaluate_all` produced and routes each via `_execute_bot_buy`. If both replicas
pass the gate, both decisions are in the list and both are routed. Per-bot
capital / exclusion-pool / open-position dedup happens inside `_execute_bot_buy`
under `_buy_fire_lock` — per-bot and config-symmetric.

### 6. Both bots are actually loaded as evaluators
`BotRegistry.from_directory(config/bots)` loads every `*.json`; there is no
`_live`-suffix exclusion at load time. (`tests/test_bot_catalog.py:25-40` excludes
the `badday_*` family only from its *count* assertions, not from loading.)

`tests/test_bot_evaluator.py` — 68 passed (unchanged by this task).

---

## The ONE residual asymmetry vector (deployment config, not code)

The **fast-watch** path scopes evaluation to a per-config allowlist:
`feeds/dip_scanner.py:4303` `"_fast_path_allowlist": cfg.bot_allowlist`, read at
`:4400` and passed to `evaluate_all` as `bot_allowlist`. That allowlist is a flat
env string with NO replica-pairing guard:

`core/fast_watch.py:56-58`:
```python
raw = os.environ.get("FAST_WATCH_BOT_ALLOWLIST", "").strip()
allow = (frozenset(b.strip() for b in raw.split(",") if b.strip())
         if raw else _DEFAULT_ALLOWLIST)
```

- **Default (env unset):** `_DEFAULT_ALLOWLIST` (`core/fast_watch.py:16-20`)
  contains `badday_flush*`, `deepflush_timebox*`, `timebox_probe_5mgreen*` and
  their `_live` twins — but does **NOT** contain `badday_flush_nf15` OR
  `badday_flush_nf15_live`. So by default **neither** nf15 replica fires on the
  fast path → symmetric (both only catch the dip on the slow ~150s main sweep).
- **Risk case:** if `FAST_WATCH_BOT_ALLOWLIST` is set in the Railway dashboard to
  include exactly one of the pair (e.g. `badday_flush_nf15` but not
  `..._live`, or only the `_live`), the fast-watch loop would escalate a dip tick
  for one replica seconds ahead of the other, which only catches it on the next
  slow sweep. That is a per-replica timing/selection asymmetry — but it is a
  DEPLOY-CONFIG choice, not a code defect, and is invisible from the repo (env is
  set in Railway, not committed — `railway.toml` carries no env).

This vector cannot be confirmed or refuted from the repo. It is the most plausible
*code-adjacent* explanation IF the env happens to be set asymmetrically; verify the
deployed `FAST_WATCH_BOT_ALLOWLIST` value directly. No code change was made because
(a) the default is symmetric and (b) inventing an auto-pairing guard would be
speculative for a config that is correct when set as a matched pair.

---

## Most likely cause of the documented 6.4h live silence (given the code)

The no-fast-price gate was in shadow during that window, so it was NOT the cause
(stated in the brief). Given the symmetric eval above, the residual (NON-code)
candidates, in rough priority:

1. **Live wallet drained / capital floor** — live routing requires balance; a
   drained wallet or a hit daily-loss / capital floor stops live fires while paper
   (separate paper capital, `paper_capital_usd:2000`) keeps trading. This is the
   already-known live-paused condition (MEMORY: "Live PAUSED 2026-06-21",
   plan Global Constraints: "Live wallet is drained / live paused").
2. **Deploy / restart gap** — a redeploy mid-window drops in-memory armed state and
   any open live position becomes untracked (the documented deploy-amnesia /
   "drops to untracked → /api/sell 404" hazard); paper is unaffected on the same
   process if it re-armed first.
3. **`should_route_live` preconditions** — live needs `live_probe AND
   USE_JUPITER_ULTRA AND has_private_key` (`core/probe_instrument.py:71-76`); if the
   private key / Ultra flag was absent for the window, the SAME decisions were made
   but force-papered, so "live silent / paper traded" with identical entries.
4. **Fast-watch allowlist asymmetry** (the §residual vector) — only if the env was
   set to include one replica and not the other.

**Inconclusive on which of 1–4 actually fired** — confirming requires the live
process logs for the window (capital balance, deploy timestamps, `live_mode`
flag, and the deployed `FAST_WATCH_BOT_ALLOWLIST` value), not the repo. Per the
task's no-guessing constraint, this is stated as a ranked hypothesis set, not a
verdict.

---

## Conclusion

The arming → per-bot evaluation path is genuinely symmetric for the two replicas
(shared `evaluate_all` over both enabled evaluators against the same bundle each
tick; no per-replica break/cap/cooldown/exclusion difference in code). No code fix
is warranted. The only thing to actively verify in the live deployment is that
`FAST_WATCH_BOT_ALLOWLIST`, if set, lists the nf15 paper and live bots as a matched
pair (or omits both); the rest of the divergence is operational (capital / restart
/ live-routing preconditions).
