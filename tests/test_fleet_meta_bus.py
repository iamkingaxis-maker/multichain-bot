"""fleet_meta_bus (#436) — fleet-wide equal-weighted time-decayed $/trade per family, with
the consensus rails (>=2 distinct bots, >=MIN_N decayed trades) + per-bot cap that stop one
big-size or one clustered bot from defining the meta (the size-is-the-bleed + consensus lessons)."""
import core.fleet_meta_bus as fmb
import core.meta_allocator as ma


def _fam(bot_id):
    if bot_id.startswith("w"):
        return "winners"
    if bot_id.startswith("l"):
        return "losers"
    if bot_id.startswith("solo"):
        return "solo"
    return None


def test_picks_the_paying_family(monkeypatch):
    monkeypatch.setattr(ma, "family_of", _fam)
    fmb._ring.clear()
    now = 1_000_000.0
    for i in range(9):
        fmb.record(f"w{i % 3}", 5.0, ts=now - i * 60)    # winners: 3 bots, +5
        fmb.record(f"l{i % 3}", -4.0, ts=now - i * 60)   # losers: 3 bots, -4
    blf = fmb.best_live_family(now=now)
    assert blf is not None and blf[0] == "winners" and blf[1] > 0


def test_consensus_rail_excludes_single_bot(monkeypatch):
    monkeypatch.setattr(ma, "family_of", _fam)
    fmb._ring.clear()
    now = 1_000_000.0
    for i in range(12):                                  # one bot, big +, many trades
        fmb.record("solo0", 9.0, ts=now - i * 60)
    assert fmb.best_live_family(now=now) is None         # <2 distinct bots -> excluded


def test_min_n_rail(monkeypatch):
    monkeypatch.setattr(ma, "family_of", _fam)
    fmb._ring.clear()
    now = 1_000_000.0
    fmb.record("w0", 5.0, ts=now - 60)
    fmb.record("w1", 5.0, ts=now - 120)                  # only 2 trades (< MIN_N)
    assert fmb.best_live_family(now=now) is None


def test_per_bot_cap_clamps_a_whale(monkeypatch):
    monkeypatch.setattr(ma, "family_of", _fam)
    fmb._ring.clear()
    now = 1_000_000.0
    for i in range(9):
        fmb.record(f"w{i % 3}", 5000.0, ts=now - i * 60)  # $5000 net -> clamped to the cap
    blf = fmb.best_live_family(now=now)
    assert blf is not None and blf[1] <= fmb.PER_BOT_NET_CAP + 1.0   # not ~5000


def test_unknown_family_ignored(monkeypatch):
    monkeypatch.setattr(ma, "family_of", _fam)
    fmb._ring.clear()
    now = 1_000_000.0
    for i in range(9):
        fmb.record(f"unmapped{i}", 5.0, ts=now - i * 60)  # family_of -> None
    assert fmb.best_live_family(now=now) is None
