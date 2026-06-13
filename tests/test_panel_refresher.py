import json
import core.panel_refresher as pr


def test_classify_archetype_labels():
    assert pr.classify_archetype(2000, 0.70, 60, -40, 10, 12) == "thesis_holder"   # multi-day
    assert pr.classify_archetype(6, 0.50, 10, -8, 10, 14) == "time_boxer"          # min + tight
    assert pr.classify_archetype(120, 0.30, 200, -30, 12, 20) == "lottery"         # low-WR tail
    assert pr.classify_archetype(200, 0.73, 12, -16, 18, 30) == "surgical"         # hi-WR scalp
    assert pr.classify_archetype(100, 0.55, 22, -13, 12, 20) == "swing"            # mid-hold
    assert pr.classify_archetype(800, 0.50, 40, -30, 10, 12) == "conviction"       # catch-all


def test_classify_rejects_thin_and_mm_bots():
    assert pr.classify_archetype(6, 0.9, 50, -8, 3, 14) is None    # n_closed < 4
    assert pr.classify_archetype(6, 0.9, 50, -8, 20, 5) is None    # n_tokens < 8 = MM/churn bot


class _FakeSensor:
    def __init__(self, geos):
        self._g = geos

    def archetype_geometry(self, arch, now=None, min_n=1):
        return self._g.get(arch)


def test_thin_archetypes_detects_count_and_top_share():
    s = _FakeSensor({
        "conviction": {"n_wallets": 8, "top_wallet_share": 0.30},   # healthy
        "time_boxer": {"n_wallets": 3, "top_wallet_share": 0.98},   # one wallet dominates
        "surgical":   {"n_wallets": 1, "top_wallet_share": 0.50},   # below 2-wallet consensus
        # swing / lottery / thesis_holder: no geometry -> "no_episodes"
    })
    thin = pr.thin_archetypes(s, now=1000.0)
    assert "conviction" not in thin
    assert "time_boxer" in thin and "top_share" in thin["time_boxer"]
    assert "surgical" in thin and "n_wallets" in thin["surgical"]
    assert thin.get("swing") == "no_episodes"


def test_overlay_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(pr, "_OVERLAY", str(tmp_path / "overlay.json"))
    pr.save_overlay({"W1": {"archetype": "time_boxer", "source": "panel-refresher"}})
    assert pr.load_overlay()["W1"]["archetype"] == "time_boxer"


def test_load_panel_merges_overlay(tmp_path, monkeypatch):
    import core.meta_sensor as ms
    repo = tmp_path / "panel.json"
    repo.write_text(json.dumps({"BASE": {"archetype": "conviction", "status": "active"}}))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    (tmp_path / "sensor_panel_runtime.json").write_text(json.dumps(
        {"ADDED": {"archetype": "time_boxer", "status": "active", "source": "panel-refresher"}}))
    panel = ms.load_panel(str(repo))
    assert "BASE" in panel and "ADDED" in panel               # repo base + overlay merged
    assert panel["ADDED"]["archetype"] == "time_boxer"
