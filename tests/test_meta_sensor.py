"""Meta sensor — wallet-panel day-meta reader (2026-06-12). Measure-only."""
import importlib

import core.meta_sensor as ms_mod
from core.meta_sensor import MetaSensor


def _mk(panel=None, tmp_path=None, monkeypatch=None):
    if tmp_path is not None and monkeypatch is not None:
        monkeypatch.setattr(ms_mod, "_STATE_FILE", str(tmp_path / "state.json"))
    s = MetaSensor.__new__(MetaSensor)
    s.panel = panel or {"W1": {"archetype": "surgical"},
                        "W2": {"archetype": None}}
    s._episodes = {}
    from collections import deque
    s._scores = deque()
    s._unresolved = deque()
    s._last_persist = 0.0
    return s


def test_episode_scores_after_idle():
    s = _mk()
    s.ingest("W1", "MintA", "buy", sol=1.0, ts=1000.0)
    s.ingest("W1", "MintA", "sell", sol=1.5, ts=1100.0)     # +50%, not yet closed
    assert s.scoreboard(now=1200.0)["windows"].get("6h", {}) == {}
    # idle past EPISODE_IDLE_SECS -> scored under its archetype + 'all'
    board = s.scoreboard(now=1100.0 + ms_mod.EPISODE_IDLE_SECS + 1)
    w6 = board["windows"]["6h"]
    assert w6["surgical"]["n"] == 1 and w6["surgical"]["wr"] == 1.0
    assert w6["all"]["n"] == 1
    assert abs(w6["surgical"]["med_ret_pct"] - 50.0) < 0.1


def test_unlabeled_wallet_buckets_as_unlabeled():
    s = _mk()
    s.ingest("W2", "MintB", "buy", sol=2.0, ts=1000.0)
    s.ingest("W2", "MintB", "sell", sol=1.0, ts=1100.0)     # -50%
    w6 = s.scoreboard(now=1100.0 + ms_mod.EPISODE_IDLE_SECS + 1)["windows"]["6h"]
    assert w6["unlabeled"]["n"] == 1 and w6["unlabeled"]["wr"] == 0.0


def test_non_panel_wallet_and_orphan_sell_ignored():
    s = _mk()
    s.ingest("STRANGER", "MintA", "buy", sol=1.0, ts=1000.0)
    s.ingest("W1", "MintC", "sell", sol=9.0, ts=1000.0)     # sell w/o buy = pre-existing pos
    assert s._episodes == {}


def test_rebuy_extends_episode():
    s = _mk()
    s.ingest("W1", "MintA", "buy", sol=1.0, ts=1000.0)
    s.ingest("W1", "MintA", "sell", sol=0.6, ts=1100.0)
    s.ingest("W1", "MintA", "buy", sol=1.0, ts=1200.0)      # re-entry keeps it open
    assert len(s._episodes) == 1
    s.ingest("W1", "MintA", "sell", sol=1.8, ts=1300.0)
    w6 = s.scoreboard(now=1300.0 + ms_mod.EPISODE_IDLE_SECS + 1)["windows"]["6h"]
    # 2.4 recv / 2.0 spent = +20% -> one winning episode
    assert w6["surgical"]["n"] == 1 and w6["surgical"]["wr"] == 1.0


def test_ingest_never_raises():
    s = _mk()
    s.ingest(None, None, "buy", sol=None, ts=None)
    s.ingest("W1", "M", "weird", sol="x", ts=0)


def test_load_panel_bootstraps_from_configs():
    panel = ms_mod.load_panel(path="__definitely_missing__.json")
    assert len(panel) >= 8     # roster at minimum
    assert all("status" in m for m in panel.values())
