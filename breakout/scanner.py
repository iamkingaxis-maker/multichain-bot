"""
BreakoutScanner — builds the top-5 watchlist every N minutes.

Filter cascade on /ticker/24hr → per-candidate klines fetch → composite scoring.
"""

import asyncio
import logging

from breakout.regime import BtcRegime, compute_btc_regime
from breakout.scoring import ema
from breakout.state import BreakoutState

logger = logging.getLogger(__name__)

_USD_SUFFIXES = ("USDT", "USD")


def _base_asset(symbol: str) -> str:
    for suf in _USD_SUFFIXES:
        if symbol.endswith(suf):
            return symbol[: -len(suf)]
    return symbol


class BreakoutScanner:
    def __init__(self, data_client, state: BreakoutState, config):
        self.client = data_client
        self.state = state
        self.config = config

    async def run(self):
        logger.info("[BreakoutScanner] Starting")
        while True:
            try:
                await self.scan_once()
            except Exception as e:
                logger.error(f"[BreakoutScanner] Scan cycle error: {e}")
            await asyncio.sleep(self.config.breakout_scan_interval_min * 60)

    async def scan_once(self) -> None:
        """Runs one pass: fetch tickers → filter → score → top-N → publish."""
        cfg = self.config

        try:
            btc_1h = await self.client.fetch_klines(cfg.breakout_regime_symbol, interval="1h", limit=210)
            btc_15m = await self.client.fetch_klines(cfg.breakout_regime_symbol, interval="15m", limit=25)
            regime = compute_btc_regime(
                btc_1h, btc_15m,
                risk_off_drop_pct=cfg.breakout_regime_risk_off_drop_pct,
                red_1h_pct=cfg.breakout_regime_red_1h_pct,
            )
        except Exception as e:
            logger.warning(f"[BreakoutScanner] BTC regime fetch failed ({e}); defaulting to green")
            regime = BtcRegime(label="green", btc_close=0.0, btc_ema50_1h=0.0,
                               btc_1h_pct=0.0, btc_15m_drop_pct=0.0)
        self.state.regime = regime

        tickers = await self.client.fetch_24h_tickers()

        stage1 = []
        for t in tickers:
            sym = t.get("symbol", "")
            if not sym.endswith(_USD_SUFFIXES):
                continue
            if _base_asset(sym) in cfg.breakout_excluded_bases:
                continue
            stage1.append(t)

        stage2 = []
        for t in stage1:
            try:
                qv = float(t.get("quoteVolume") or 0)
                pct = float(t.get("priceChangePercent") or 0)
            except (TypeError, ValueError):
                continue
            if qv < cfg.breakout_min_vol_24h_usd:
                continue
            if not (cfg.breakout_change_24h_min_pct <= pct <= cfg.breakout_change_24h_max_pct):
                continue
            stage2.append((t, qv, pct))

        scored = []
        for t, qv, pct24 in stage2:
            sym = t["symbol"]
            try:
                k15 = await self.client.fetch_klines(sym, interval="15m", limit=25)
                k1h = await self.client.fetch_klines(sym, interval="1h", limit=210)
            except Exception as e:
                logger.debug(f"[BreakoutScanner] {sym} klines fetch failed: {e}")
                continue
            if len(k15) < 21 or len(k1h) < 6:
                continue

            close_6h_ago = k1h[-7].close if len(k1h) >= 7 else k1h[0].close
            change_6h_pct = (k1h[-1].close - close_6h_ago) / close_6h_ago * 100 if close_6h_ago > 0 else 0
            if abs(change_6h_pct) > cfg.breakout_change_6h_max_pct:
                continue

            if len(k1h) >= 2:
                change_1h_pct = (k1h[-1].close - k1h[-2].close) / k1h[-2].close * 100 if k1h[-2].close > 0 else 0
            else:
                change_1h_pct = 0
            if change_1h_pct <= 0:
                continue
            # relative strength vs BTC: require candidate to outperform BTC on 1h
            if change_1h_pct <= regime.btc_1h_pct:
                continue

            if len(k15) >= 21:
                recent = k15[-1]
                avg_vol = sum(x.volume for x in k15[-21:-1]) / 20
                if recent.volume <= avg_vol:
                    continue
                vol_ratio = recent.volume / avg_vol if avg_vol > 0 else 0
            else:
                continue

            closes_1h = [k.close for k in k1h]
            ema50 = ema(closes_1h, 50)
            ema200 = ema(closes_1h, 200)
            if not (k1h[-1].close > ema50 > ema200):
                continue
            trend_sep = (k1h[-1].close - ema50) / ema50 if ema50 > 0 else 0

            composite = vol_ratio + change_1h_pct + (trend_sep * 100)
            scored.append((sym, composite, qv))

        best_by_base: dict[str, tuple[str, float, float]] = {}
        for sym, composite, qv in scored:
            base = _base_asset(sym)
            cur = best_by_base.get(base)
            if cur is None or qv > cur[2]:
                best_by_base[base] = (sym, composite, qv)

        ranked = sorted(best_by_base.values(), key=lambda x: x[1], reverse=True)
        watchlist_size = (
            cfg.breakout_red_watchlist_size
            if regime.label in ("red", "risk_off")
            else cfg.breakout_watchlist_size
        )
        top = [sym for sym, _, _ in ranked[:watchlist_size]]

        self.state.set_watchlist(top)
        logger.info(
            f"[BreakoutScanner] regime={regime.label} btc_1h={regime.btc_1h_pct:.2f}% "
            f"tickers={len(tickers)} → stage1={len(stage1)} stage2={len(stage2)} "
            f"scored={len(scored)} → watchlist={top}"
        )
