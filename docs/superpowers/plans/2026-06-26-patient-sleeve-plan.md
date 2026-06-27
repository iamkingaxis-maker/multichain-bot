# Patient Sleeve Implementation Plan

> **For agentic workers:** execute task-by-task; each task ends test-green + committed.

**Goal:** A paper `patient_sleeve` bot that takes ONLY winner-selection-qualified entries and holds them with winner-like exits (−22% stop, 240-min max hold, partial-TP1-then-trail), as a clean A/B vs the time-box fleet.

**Architecture:** Additive. New BotConfig flag + a pure entry-gate helper wired at the existing winner-selection site in `dip_scanner._execute_bot_buy`, a new paper config, and an offline A/B script. No change to existing bots.

**Tech Stack:** Python; existing BotConfig/PerBotPositionManager/dip_scanner; pytest.

## Global Constraints
- Paper only. Existing bots UNCHANGED. No live, no PAPER_MODE flip.
- `BotConfig.from_json` RAISES on unknown fields → any new field MUST be declared before any config uses it (C1 deploy-breaker history).
- `patient_sleeve` bot_id is NON-`badday_` → auto-skips the badday-scoped `IN_FLIGHT_FLOOR` (−7) + entry stack; the winner-selection gate IS its entry filter. `microcap_mandate: true` keeps it in the lane for sub-floor tokens.
- Fat-tail: judge on MEAN + tail-capture, never median. No conviction sizing. Rug guards ON (no `antirug_floor_exempt`).
- Entry gate fails CLOSED for this bot (missing `median_buy_size_usd` → skip): the point is to hold only qualified +tail entries.

---

### Task 1: BotConfig `winner_select_entry` flag
**Files:** Modify `core/bot_config.py` (field block ~line 87); Test `tests/test_bot_config.py` (or new `tests/test_winner_select_entry_flag.py`).
**Interfaces:** Produces `BotConfig.winner_select_entry: bool` (default False), accepted by `from_json`.

- [ ] **Step 1: failing test** — a config dict with `winner_select_entry: true` loads and the attr is True; default is False.
```python
from core.bot_config import BotConfig
def test_winner_select_entry_defaults_false():
    assert BotConfig(bot_id="b", display_name="B").winner_select_entry is False
def test_winner_select_entry_from_json_accepted():
    c = BotConfig.from_json({"bot_id":"b","display_name":"B","winner_select_entry":True})
    assert c.winner_select_entry is True
```
- [ ] **Step 2:** run → FAIL (unknown field raises / attr missing).
- [ ] **Step 3:** add field near other bool flags (~line 147 with entry_stack_exempt):
```python
    winner_select_entry: bool = False
```
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** commit `feat(bot-config): winner_select_entry flag`.

### Task 2: Winner-selection entry gate (helper + wire)
**Files:** Modify `core/bot_evaluator.py` (after `winner_demand_selected`); Modify `feeds/dip_scanner.py` (~1899, the winner-size shadow block); Test `tests/test_winner_select_entry_gate.py`.
**Interfaces:** Consumes `winner_demand_selected`. Produces `winner_select_entry_blocks(median_buy_size_usd, gate_on, threshold=None) -> (bool, str)`.

- [ ] **Step 1: failing test** for the helper:
```python
from core.bot_evaluator import winner_select_entry_blocks as wseb
def test_no_block_when_gate_off():
    assert wseb(10.0, gate_on=False)[0] is False        # gate disabled -> never blocks
def test_block_when_gated_and_not_selected():
    assert wseb(10.0, gate_on=True)[0] is True           # below 34.3 -> block
def test_pass_when_gated_and_selected():
    assert wseb(50.0, gate_on=True)[0] is False
def test_fail_closed_on_missing_signal():
    assert wseb(None, gate_on=True)[0] is True           # no signal -> block (hold only qualified)
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement helper (reuses `winner_demand_selected`):
```python
def winner_select_entry_blocks(median_buy_size_usd, gate_on, threshold=None) -> tuple[bool, str]:
    """Entry gate for the patient sleeve: when gate_on, ALLOW only winner-selected
    entries (median_buy_size_usd >= threshold). FAIL-CLOSED: missing/garbage signal
    while gated -> BLOCK (hold only qualified +tail entries). gate_on False -> never block."""
    if not gate_on:
        return False, ""
    sel, why = winner_demand_selected(median_buy_size_usd, threshold=threshold)
    if sel:
        return False, why
    return True, "winner_select_entry: not a qualified +tail entry"
```
- [ ] **Step 4:** run helper test → PASS.
- [ ] **Step 5: wire** in `dip_scanner._execute_bot_buy`, immediately AFTER the winner-size shadow block (after line ~1935, before the buy proceeds). Uses `pm.config`:
```python
        # Patient-sleeve ENTRY GATE: hold only winner-selected +tail entries.
        if bool(getattr(pm.config, "winner_select_entry", False)):
            from core.bot_evaluator import winner_select_entry_blocks as _wseb
            _wse_block, _wse_why = _wseb(_ar_meta.get("median_buy_size_usd"), gate_on=True)
            if _wse_block:
                logger.info("[DipScanner] bot=%s WINNER-SELECT-ENTRY skip: %s %s",
                            bot_id, _wse_why, decision.token)
                return
```
- [ ] **Step 6:** verify `python -c "import ast; ast.parse(open('feeds/dip_scanner.py',encoding='utf-8').read())"` OK; run helper tests → PASS.
- [ ] **Step 7:** commit `feat(patient-sleeve): winner-selection entry gate (helper + wire)`.

### Task 3: `patient_sleeve` paper config
**Files:** Create `config/bots/patient_sleeve.json`; Test `tests/test_patient_sleeve_config.py`.
**Interfaces:** Consumes all Task 1/2 fields. Produces a loadable paper bot.

- [ ] **Step 1: failing test** — config loads via `BotConfig.from_json` with the patient params:
```python
import json
from core.bot_config import BotConfig
def test_patient_sleeve_loads_with_patient_params():
    c = BotConfig.from_json(json.load(open("config/bots/patient_sleeve.json")))
    assert c.bot_id == "patient_sleeve" and not c.bot_id.startswith("badday_")  # skips -7 floor
    assert c.winner_select_entry is True
    assert c.hard_stop_pct == -22.0
    assert c.time_stop_minutes == 240
    assert c.tp1_pct == 15.0 and c.tp1_sell_fraction == 0.25   # partial-then-ride
    assert c.max_concurrent_positions >= 20
    assert c.microcap_mandate is True and c.antirug_floor_exempt is False
```
- [ ] **Step 2:** run → FAIL (file missing).
- [ ] **Step 3:** create config (clone a working badday config's REQUIRED fields, then override). Exit-side fast bails OFF; patient params set:
```json
{
  "bot_id": "patient_sleeve",
  "display_name": "Patient Sleeve (winner-selection A/B)",
  "enabled": true,
  "winner_select_entry": true,
  "microcap_mandate": true,
  "base_position_usd": 100.0,
  "max_concurrent_positions": 20,
  "hard_stop_pct": -22.0,
  "time_stop_minutes": 240,
  "tp1_pct": 15.0,
  "tp1_sell_fraction": 0.25,
  "trail_pp": 15.0,
  "pre_stop_bail_pnl_pct": -50.0,
  "fast_bail_pnl_pct": null,
  "giveback_floor_peak_min": null,
  "giveback_floor_pnl_pct": null,
  "slow_bleed_minutes": 600,
  "slow_bleed_pnl_threshold": -50.0,
  "flat_exit_minutes": null,
  "stall_exit_minutes": null,
  "daily_loss_limit_usd": 300.0,
  "max_token_buys_per_day": 1
}
```
  (If `from_json` rejects any key as unknown, that field doesn't exist on BotConfig — remove it; do NOT invent fields. Verify each key against `core/bot_config.py`.)
- [ ] **Step 4:** run → PASS.
- [ ] **Step 5:** commit `feat(patient-sleeve): paper bot config`.

### Task 4: Dry-run routing verification (pipeline-trace gate)
**Files:** `scripts/verify_patient_sleeve.py` (throwaway-style verification, kept for re-run).
**Interfaces:** None (read-only check).

- [ ] **Step 1:** write a script that loads ALL bot configs the way the app does, asserts `patient_sleeve` is registered + enabled, prints its resolved exit params, and confirms its bot_id does NOT start with `badday_` (so IFF is skipped) AND `microcap_mandate` is True (so the lane admits sub-floor). If the loader exposes a registry, assert membership.
- [ ] **Step 2:** run it; confirm registration + params. If it is NOT picked up by the loader, STOP and report (routing problem — adjust config dir/name before proceeding).
- [ ] **Step 3:** commit `chore(patient-sleeve): dry-run routing verification`.

### Task 5: A/B analysis script
**Files:** Create `scripts/patient_sleeve_ab.py`; Test `tests/test_patient_sleeve_ab.py` (pure-function smoke test on synthetic records).
**Interfaces:** Reads the local trades file; pairs `patient_sleeve` vs `badday_*` on shared tokens.

- [ ] **Step 1: failing test** for the pure pairing/compare function:
```python
from scripts.patient_sleeve_ab import compare_arms
def test_compare_pairs_same_token_and_reports_means():
    recs = [
      {"bot_id":"patient_sleeve","address":"A","fully_closed":True,"pnl_pct":40.0},
      {"bot_id":"badday_flush","address":"A","fully_closed":True,"pnl_pct":3.0},
      {"bot_id":"patient_sleeve","address":"B","fully_closed":True,"pnl_pct":-22.0},
      {"bot_id":"badday_flush","address":"B","fully_closed":True,"pnl_pct":-6.0},
    ]
    out = compare_arms(recs)
    assert out["paired_tokens"] == 2
    assert round(out["patient_mean"],1) == 9.0 and round(out["timebox_mean"],1) == -1.5
```
- [ ] **Step 2:** run → FAIL.
- [ ] **Step 3:** implement `compare_arms(records)` → dict with paired_tokens, patient_mean, timebox_mean, patient_median, timebox_median, patient_tail_rate (share >+25%), timebox_tail_rate, n_distinct; plus a `__main__` that loads `_full_trades.json` and prints the report. Pair on `address` where BOTH a `patient_sleeve` and a `badday_*` fully_closed record exist; mean of each arm on the paired set.
- [ ] **Step 4:** run test → PASS.
- [ ] **Step 5:** commit `feat(patient-sleeve): A/B analysis script`.

## Self-review
- Coverage: flag (T1) → gate (T2) → config (T3) → routing proof (T4) → measurement (T5). All spec components mapped.
- No placeholders: real field names verified against `core/bot_config.py`; gate wiring matches the existing `return`-to-abort pattern at the winner-size site.
- Type consistency: `winner_select_entry` bool used identically in T1/T2/T3; helper signature stable.
- Risk gate: T4 is the pipeline-trace checkpoint — if the bot isn't routed candidates, stop before shipping.
