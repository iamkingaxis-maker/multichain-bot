"""Per-wallet flow-concentration feature tests (feeds/wallet_flow_features.py).
2026-06-03: true per-wallet seller HHI (the axis the coarse dollar-share proxy
could never compute)."""
from feeds.wallet_flow_features import wallet_concentration, wallet_flow_features


def _s(kind, usd, maker):
    return {"kind": kind, "volume_usd": usd, "maker": maker}


def test_single_wallet_hhi_is_one():
    swaps = [_s("sell", 100, "w1"), _s("sell", 50, "w1")]
    c = wallet_concentration(swaps, "sell")
    assert c["hhi"] == 1.0
    assert c["top1_share"] == 1.0
    assert c["n_wallets"] == 1
    assert c["total_usd"] == 150.0


def test_many_equal_wallets_low_hhi():
    swaps = [_s("sell", 10, f"w{i}") for i in range(10)]
    c = wallet_concentration(swaps, "sell")
    assert abs(c["hhi"] - 0.10) < 1e-9   # 10 equal wallets -> 10*(0.1^2)=0.1
    assert c["top1_share"] == 0.1
    assert c["n_wallets"] == 10


def test_one_whale_among_minnows():
    # one wallet 90%, nine wallets ~1.1% each
    swaps = [_s("sell", 900, "whale")] + [_s("sell", 100 / 9, f"m{i}") for i in range(9)]
    c = wallet_concentration(swaps, "sell")
    assert c["top1_share"] == 0.9
    assert c["hhi"] > 0.81   # dominated by the whale's 0.9^2
    assert c["n_wallets"] == 10


def test_side_isolation():
    swaps = [_s("sell", 100, "s1"), _s("buy", 200, "b1"), _s("buy", 200, "b2")]
    sell = wallet_concentration(swaps, "sell")
    buy = wallet_concentration(swaps, "buy")
    assert sell["n_wallets"] == 1 and sell["total_usd"] == 100.0
    assert buy["n_wallets"] == 2 and buy["total_usd"] == 400.0


def test_empty_side_fails_open():
    c = wallet_concentration([_s("buy", 100, "b1")], "sell")
    assert c["hhi"] is None
    assert c["n_wallets"] == 0


def test_ignores_zero_and_missing_maker():
    swaps = [_s("sell", 0, "w1"), _s("sell", 100, ""), _s("sell", 50, "w2")]
    c = wallet_concentration(swaps, "sell")
    assert c["n_wallets"] == 1   # only w2 counts (zero vol + blank maker dropped)
    assert c["total_usd"] == 50.0


def test_single_whale_seller_flag():
    # one wallet 60% of sells, only 3 sellers -> flagged; buys spread over 5 wallets
    swaps = [_s("sell", 60, "whale"), _s("sell", 20, "a"), _s("sell", 20, "b")] \
        + [_s("buy", 20, f"x{i}") for i in range(5)]
    f = wallet_flow_features(swaps)
    assert f["single_whale_seller"] is True
    assert f["seller_top1_share"] == 0.6
    # seller HHI (0.6^2+0.2^2+0.2^2=0.44) > buyer HHI (5 equal = 0.20)
    assert f["hhi_sell_minus_buy"] is not None
    assert f["hhi_sell_minus_buy"] > 0   # selling more concentrated than buying


def test_headtohead_baselines_on_same_window():
    # sells: whale 80 + a 20 (sell$=100); buys: b1 100 + b2 100 (buy$=200)
    swaps = [_s("sell", 80, "whale"), _s("sell", 20, "a"),
             _s("buy", 100, "b1"), _s("buy", 100, "b2")]
    f = wallet_flow_features(swaps)
    # net_imbalance = (200-100)/300
    assert abs(f["net_imbalance"] - round(100 / 300, 4)) < 1e-4
    # coarse proxy = max_sell/sell$ = 80/100
    assert f["coarse_sell_proxy"] == 0.8
    # and the per-wallet seller HHI (0.8^2+0.2^2=0.68) is a distinct number from the proxy
    assert f["seller_hhi"] == 0.68


def test_no_whale_when_distributed():
    swaps = [_s("sell", 10, f"w{i}") for i in range(8)] + [_s("buy", 10, f"b{i}") for i in range(8)]
    f = wallet_flow_features(swaps)
    assert f["single_whale_seller"] is False
    assert f["n_sellers"] == 8 and f["n_buyers"] == 8
    assert f["seller_buyer_wallet_ratio"] == 1.0
