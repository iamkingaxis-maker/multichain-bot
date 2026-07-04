# tests/test_fleet_token_cap.py
"""Fleet per-token CONCURRENCY cap (go-live audit #4, 2026-07-04).

Caps how many DISTINCT badday_/young-probe bots may hold the SAME token at
once — the residual mirror pile-on (07-03 BongoCat first-entry wave = 7 bots
at once; June's -$5k live day = one token x 198 entries). Pure-logic tests
for the counting helper + env resolution; the dip_scanner block just feeds
it the per-bot books and honors mode. Style follows test_young_holder_guard.
"""
from core.fleet_token_cap import (
    DEFAULT_CAP,
    blocks,
    cap_mode,
    cap_n,
    other_holders,
)

MINT = "mintbongocat111111111111111111111111111111"


# ── counting: the pile-on core ─────────────────────────────────────────────

class TestCounting:
    def test_bongocat_wave_counts_all_other_holders(self):
        # 07-03 first-entry wave shape: siblings already in, one more asks.
        book = {"badday_%d" % i: {MINT} for i in range(7)}
        who = other_holders(book, "badday_new", {MINT})
        assert len(who) == 7
        assert who == sorted(book)  # deterministic, analyzable holder list

    def test_requesting_bot_never_counts_itself(self):
        book = {"badday_a": {MINT}, "badday_b": {MINT}}
        assert other_holders(book, "badday_a", {MINT}) == ["badday_b"]

    def test_unheld_token_counts_zero(self):
        book = {"badday_a": {MINT}, "badday_b": {"othermint"}}
        assert other_holders(book, "badday_c", {"freshmint"}) == []

    def test_one_bot_many_positions_counts_once(self):
        # Distinct BOTS, not distinct positions (a bot holds one pos per token
        # anyway, but the helper must not double-count a weird book).
        book = {"badday_a": [MINT, MINT, "other"], "badday_b": {"other"}}
        assert other_holders(book, "badday_new", {MINT}) == ["badday_a"]

    def test_empty_book_counts_zero(self):
        assert other_holders({}, "badday_a", {MINT}) == []


# ── keying: address when available, symbol fallback (streak-latch rule) ────

class TestKeying:
    def test_address_match_case_insensitive(self):
        book = {"badday_a": {MINT.lower()}}
        assert other_holders(book, "b", {MINT.upper()}) == ["badday_a"]

    def test_same_symbol_different_mint_not_conflated(self):
        # SPCX phantom: two mints share a symbol. Books keyed by address ->
        # a buy in mint_real must not count the mint_imposter holder.
        book = {"badday_a": {"mint_imposter"}}
        assert other_holders(book, "b", {"mint_real"}) == []

    def test_symbol_fallback_matches_addressless_position(self):
        # Holder's position had no address (keyed by symbol); the buy queries
        # with BOTH its address and symbol -> still matched.
        book = {"badday_a": {"bongo"}}
        assert other_holders(book, "b", {MINT, "bongo"}) == ["badday_a"]

    def test_query_key_whitespace_and_case_normalized(self):
        book = {"badday_a": {" Bongo "}}
        assert other_holders(book, "b", {"bongo"}) == ["badday_a"]


# ── fail-open on garbage: a counting bug must never block a buy ────────────

class TestFailOpen:
    def test_none_holdings_returns_empty(self):
        assert other_holders(None, "a", {MINT}) == []

    def test_non_mapping_holdings_returns_empty(self):
        assert other_holders(["not", "a", "mapping"], "a", {MINT}) == []

    def test_none_and_empty_token_keys_return_empty(self):
        assert other_holders({"badday_a": {MINT}}, "b", None) == []
        assert other_holders({"badday_a": {MINT}}, "b", set()) == []

    def test_garbage_token_keys_object_returns_empty(self):
        assert other_holders({"badday_a": {MINT}}, "b", 42) == []

    def test_one_bots_garbage_book_skipped_others_still_count(self):
        book = {"badday_bad": 3.14, "badday_ok": {MINT}, "badday_none": None}
        assert other_holders(book, "b", {MINT}) == ["badday_ok"]

    def test_blocks_garbage_inputs_fail_open(self):
        assert blocks("garbage", 3) is False
        assert blocks(None, 3) is False
        assert blocks(3, None) is False


# ── blocks(): the >= cap edge ──────────────────────────────────────────────

class TestBlocks:
    def test_at_cap_blocks(self):
        assert blocks(3, 3) is True

    def test_under_cap_allows(self):
        assert blocks(2, 3) is False

    def test_over_cap_blocks(self):
        assert blocks(7, 3) is True


# ── env resolution ─────────────────────────────────────────────────────────

class TestEnvDefaults:
    def test_default_mode_shadow(self, monkeypatch):
        monkeypatch.delenv("FLEET_TOKEN_CAP_MODE", raising=False)
        assert cap_mode() == "shadow"

    def test_mode_override(self, monkeypatch):
        monkeypatch.setenv("FLEET_TOKEN_CAP_MODE", "enforce")
        assert cap_mode() == "enforce"
        monkeypatch.setenv("FLEET_TOKEN_CAP_MODE", "off")
        assert cap_mode() == "off"

    def test_mode_case_and_whitespace_normalized(self, monkeypatch):
        monkeypatch.setenv("FLEET_TOKEN_CAP_MODE", "  ENFORCE ")
        assert cap_mode() == "enforce"

    def test_mode_garbage_falls_back_to_shadow(self, monkeypatch):
        # a typo must neither silently enforce nor silently vanish the gate
        monkeypatch.setenv("FLEET_TOKEN_CAP_MODE", "enforce!!")
        assert cap_mode() == "shadow"

    def test_default_cap_is_3(self, monkeypatch):
        monkeypatch.delenv("FLEET_TOKEN_CAP", raising=False)
        assert cap_n() == 3 == DEFAULT_CAP

    def test_cap_override(self, monkeypatch):
        monkeypatch.setenv("FLEET_TOKEN_CAP", "2")
        assert cap_n() == 2

    def test_cap_garbage_falls_back(self, monkeypatch):
        monkeypatch.setenv("FLEET_TOKEN_CAP", "not-a-number")
        assert cap_n() == DEFAULT_CAP

    def test_cap_nonpositive_falls_back(self, monkeypatch):
        # cap<=0 would block EVERY buy — never a parse accident
        monkeypatch.setenv("FLEET_TOKEN_CAP", "0")
        assert cap_n() == DEFAULT_CAP
        monkeypatch.setenv("FLEET_TOKEN_CAP", "-1")
        assert cap_n() == DEFAULT_CAP
