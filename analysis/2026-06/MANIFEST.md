# Analysis archive

Sprint scripts and data dumps swept out of the repo root on 2026-06-10
(root had 213 one-off scripts + 174 artifacts; see project_bot_handoff.md).

Layout:
- `2026-06/` — May-31..Jun-10 sprint scripts (bleed decomposition, pond mine,
  sol-gate audit, flush-depth, sector/solgate/nf/ttp/flash investigations).
  Tracked provenance scripts: _bleed_decomp.py, _pond_mine.py, _pond_combo.py,
  _capacity*.py, _filter_redundancy.py, _sol_gate_audit.py, _sol_heldout.py.
- `2026-06/data/` — outputs/caches for the above (regenerable, not in git).
- `legacy_data/` — older root-level data dumps (pre-June pulls, backtests).
- `_prune_mine/`, `_research/`, `_verify_flk/`, `wallet_hhi/`, `winloss_8hr/`,
  `_archive/` — earlier self-contained sprint dirs, moved as-is.

Rules going forward:
1. New investigation scripts start in `analysis/<YYYY-MM>/`, not repo root.
2. A script gets promoted to `scripts/` when it becomes a reusable tool.
3. Outputs/caches stay next to their scripts; git ignores all of analysis/.
