from __future__ import annotations

import datetime as dt
import importlib
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from market_regime_engine import detect_market_regime, open_regime_reference_tabs


def _load_trading_main():
    return importlib.import_module("main")


def _get_market_regime_impl() -> dict[str, Any]:
    trading_main = _load_trading_main()
    now_utc = dt.datetime.now(dt.timezone.utc)
    fetcher, _, _, _, _ = trading_main._build_components()

    trend_candles = fetcher.fetch_market_data(
        trading_main.SYMBOL,
        trading_main.TREND_TIMEFRAME,
        trading_main.CANDLE_LIMIT,
    )
    entry_candles = fetcher.fetch_market_data(
        trading_main.SYMBOL,
        trading_main.ENTRY_TIMEFRAME,
        trading_main.CANDLE_LIMIT,
    )

    regime = detect_market_regime(trend_candles, entry_candles)
    trend_regime = str(regime.get("trend_regime", "neutral"))
    volatility_regime = str(regime.get("volatility_regime", "normal_volatility"))
    market_open = bool(trading_main.is_market_open(now_utc))
    strategy_behavior = str(regime.get("strategy_behavior", "breakout"))

    return {
        "symbol": "XAUUSD",
        "regime": regime["regime"],
        "confidence": float(regime["confidence"]),
        "trend_regime": trend_regime,
        "volatility_regime": volatility_regime,
        "strategy_behavior": strategy_behavior,
        "should_trade": bool(market_open and volatility_regime != "low_volatility"),
        "market_open": market_open,
        "timestamp_utc": regime.get("timestamp"),
        "trend_bias": regime.get("trend_bias"),
        "ema50": regime.get("ema50"),
        "ema200": regime.get("ema200"),
        "adx14": regime.get("adx14"),
        "atr14": regime.get("atr14"),
        "atr_sma20": regime.get("atr_sma20"),
        "atr_std20": regime.get("atr_std20"),
        "price_range_avg": regime.get("price_range_avg"),
        "paper_mode_only": True,
    }


def get_market_regime(timeout_seconds: int = 90) -> dict[str, Any]:
    """Load fresh candles, calculate indicators, and return the active market regime."""

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_get_market_regime_impl)
        try:
            return future.result(timeout=max(1, int(timeout_seconds)))
        except FuturesTimeout as error:
            raise TimeoutError(
                f"Market regime evaluation exceeded timeout after {timeout_seconds}s."
            ) from error


def open_regime_visuals() -> dict[str, Any]:
    """Open four TradingView reference tabs for manual paper-mode review."""

    urls = open_regime_reference_tabs()
    return {
        "status": "opened",
        "opened_tabs": len(urls),
        "paper_mode_only": True,
        "urls": urls,
    }
