from dataclasses import dataclass
from breakout.scoring import ema


@dataclass
class BtcRegime:
    label: str   # "green" | "red" | "risk_off"
    btc_close: float
    btc_ema50_1h: float
    btc_1h_pct: float
    btc_15m_drop_pct: float


def compute_btc_regime(k1h, k15, *, risk_off_drop_pct: float, red_1h_pct: float) -> BtcRegime:
    btc_close = k1h[-1].close
    closes_1h = [k.close for k in k1h]
    ema50 = ema(closes_1h, 50)

    prev_1h = k1h[-2].close if len(k1h) >= 2 else btc_close
    btc_1h_pct = (btc_close - prev_1h) / prev_1h * 100 if prev_1h > 0 else 0.0

    if k15:
        last15 = k15[-1]
        btc_15m_drop_pct = (last15.close - last15.open) / last15.open * 100 if last15.open > 0 else 0.0
    else:
        btc_15m_drop_pct = 0.0

    if btc_15m_drop_pct <= -risk_off_drop_pct:
        label = "risk_off"
    elif btc_close < ema50 or btc_1h_pct < red_1h_pct:
        label = "red"
    else:
        label = "green"

    return BtcRegime(
        label=label,
        btc_close=btc_close,
        btc_ema50_1h=ema50,
        btc_1h_pct=btc_1h_pct,
        btc_15m_drop_pct=btc_15m_drop_pct,
    )
