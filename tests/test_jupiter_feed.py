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
