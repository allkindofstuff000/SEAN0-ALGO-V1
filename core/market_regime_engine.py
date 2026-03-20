from __future__ import annotations

import webbrowser
from typing import Any
from urllib.parse import quote_plus

import pandas as pd


ADX_LENGTH = 14
ATR_LENGTH = 14
ATR_STAT_WINDOW = 20
PRICE_RANGE_WINDOW = 12
ADX_TREND_THRESHOLD = 25.0
RANGE_ATR_MULTIPLIER = 1.2
PAPER_MODE_ONLY = True
TRADINGVIEW_SYMBOL = "OANDA:XAUUSD"
REGIME_PRIORITY = ("low_volatility", "high_volatility", "trend", "range")


def prepare_regime_history(
    trend_candles: pd.DataFrame,
    entry_candles: pd.DataFrame,
) -> pd.DataFrame:
    """
    Merge 15m EMA state with 5m ADX/ATR state so each 5m row has a regime label.

    The resulting frame is used by the live signal engine and the backtest tool,
    and the Streamlit dashboard.
    """

    trend_frame = _prepare_trend_frame(trend_candles)
    entry_frame = _prepare_entry_frame(entry_candles)
    merged = pd.merge_asof(
        entry_frame.sort_values("timestamp"),
        trend_frame[["timestamp", "ema50", "ema200"]].sort_values("timestamp"),
        on="timestamp",
        direction="backward",
    )

    regime_rows = [_classify_row(row) for _, row in merged.iterrows()]
    regime_frame = pd.DataFrame(regime_rows)
    out = pd.concat([merged.reset_index(drop=True), regime_frame], axis=1)
    out["paper_mode_only"] = PAPER_MODE_ONLY
    return out


def detect_market_regime(
    trend_candles: pd.DataFrame,
    entry_candles: pd.DataFrame,
) -> dict[str, Any]:
    history = prepare_regime_history(trend_candles, entry_candles)
    if history.empty:
        raise ValueError("Not enough candle data to detect the market regime.")

    row = history.iloc[-1]
    return {
        "timestamp": pd.Timestamp(row["timestamp"]).isoformat(),
        "regime": str(row["regime"]),
        "confidence": round(float(row["confidence"]), 4),
        "trend_regime": str(row["trend_regime"]),
        "volatility_regime": str(row["volatility_regime"]),
        "trend_bias": str(row["trend_bias"]),
        "strategy_behavior": str(row["strategy_behavior"]),
        "should_trade": bool(row["should_trade"]),
        "paper_mode_only": True,
        "ema50": round(float(row["ema50"]), 6),
        "ema200": round(float(row["ema200"]), 6),
        "adx14": round(float(row["adx14"]), 4),
        "atr14": round(float(row["atr14"]), 6),
        "atr_sma20": round(float(row["atr_sma20"]), 6),
        "atr_std20": round(float(row["atr_std20"]), 6),
        "price_range_avg": round(float(row["price_range_avg"]), 6),
        "price_range_threshold": round(float(row["price_range_threshold"]), 6),
    }


def build_tradingview_regime_urls(symbol: str = TRADINGVIEW_SYMBOL) -> dict[str, str]:
    labels = {
        "trend": "XAUUSD trending market chart",
        "range": "XAUUSD ranging market chart",
        "high_volatility": "XAUUSD high volatility chart",
        "low_volatility": "XAUUSD low volatility chart",
    }
    urls: dict[str, str] = {}
    for regime, query in labels.items():
        urls[regime] = (
            "https://www.tradingview.com/chart/"
            f"?symbol={quote_plus(symbol)}&interval=5&query={quote_plus(query)}"
        )
    return urls


def open_regime_reference_tabs(symbol: str = TRADINGVIEW_SYMBOL) -> list[str]:
    """
    Open TradingView reference tabs on demand.

    This is only for local paper-mode visualization. It never places live orders.
    """

    urls = build_tradingview_regime_urls(symbol=symbol)
    for url in urls.values():
        webbrowser.open_new_tab(url)
    return list(urls.values())


def _prepare_trend_frame(candles: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "close"}
    missing = required - set(candles.columns)
    if missing:
        raise ValueError(f"Trend candles missing columns: {sorted(missing)}")

    frame = candles.copy()
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    if "ema50" not in frame.columns:
        frame["ema50"] = frame["close"].ewm(span=50, adjust=False).mean()
    if "ema200" not in frame.columns:
        frame["ema200"] = frame["close"].ewm(span=200, adjust=False).mean()
    return frame.sort_values("timestamp").reset_index(drop=True)


def _prepare_entry_frame(candles: pd.DataFrame) -> pd.DataFrame:
    required = {"timestamp", "high", "low", "close"}
    missing = required - set(candles.columns)
    if missing:
        raise ValueError(f"Entry candles missing columns: {sorted(missing)}")

    frame = candles.copy()
    overlap_columns = [column for column in ("ema50", "ema200") if column in frame.columns]
    if overlap_columns:
        frame = frame.drop(columns=overlap_columns)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], utc=True)
    frame = frame.sort_values("timestamp").reset_index(drop=True)
    frame["atr14"] = frame["atr14"] if "atr14" in frame.columns else _manual_atr(frame, ATR_LENGTH)
    frame["adx14"] = frame["adx14"] if "adx14" in frame.columns else _manual_adx(frame, ADX_LENGTH)
    frame["atr_sma20"] = frame["atr14"].rolling(ATR_STAT_WINDOW, min_periods=ATR_STAT_WINDOW).mean()
    frame["atr_std20"] = (
        frame["atr14"].rolling(ATR_STAT_WINDOW, min_periods=ATR_STAT_WINDOW).std(ddof=0).fillna(0.0)
    )
    frame["price_range_avg"] = (
        (frame["high"] - frame["low"]).rolling(PRICE_RANGE_WINDOW, min_periods=PRICE_RANGE_WINDOW).mean()
    )
    return frame


def _classify_row(row: pd.Series) -> dict[str, Any]:
    ema50 = _safe_float(row.get("ema50"))
    ema200 = _safe_float(row.get("ema200"))
    adx14 = _safe_float(row.get("adx14"))
    atr14 = _safe_float(row.get("atr14"))
    atr_sma20 = _safe_float(row.get("atr_sma20"))
    atr_std20 = _safe_float(row.get("atr_std20"))
    price_range_avg = _safe_float(row.get("price_range_avg"))

    price_range_threshold = atr_sma20 * RANGE_ATR_MULTIPLIER
    trend_bias = "bull" if ema50 > ema200 else "bear" if ema50 < ema200 else "flat"
    trend_regime = "trend" if trend_bias != "flat" and adx14 > ADX_TREND_THRESHOLD else "range"

    high_volatility = atr_sma20 > 0 and atr14 > (atr_sma20 + atr_std20)
    low_volatility = atr_sma20 > 0 and atr14 < (atr_sma20 - atr_std20)
    volatility_regime = "normal_volatility"
    if high_volatility:
        volatility_regime = "high_volatility"
    elif low_volatility:
        volatility_regime = "low_volatility"

    trend_condition = trend_bias != "flat" and adx14 > ADX_TREND_THRESHOLD
    range_condition = adx14 < ADX_TREND_THRESHOLD and price_range_avg > 0 and price_range_avg < price_range_threshold
    high_volatility_condition = high_volatility
    low_volatility_condition = low_volatility

    confidence_map = {
        "trend": _trend_confidence(ema50, ema200, adx14, atr14),
        "range": _range_confidence(adx14, price_range_avg, price_range_threshold),
        "high_volatility": _volatility_confidence(atr14, atr_sma20 + atr_std20, positive=True),
        "low_volatility": _volatility_confidence(atr14, max(atr_sma20 - atr_std20, 0.0), positive=False),
    }
    active_map = {
        "trend": trend_condition,
        "range": range_condition,
        "high_volatility": high_volatility_condition,
        "low_volatility": low_volatility_condition,
    }

    regime = next((name for name in REGIME_PRIORITY if active_map[name]), "trend" if trend_bias != "flat" else "range")
    confidence = confidence_map[regime] if active_map.get(regime, False) else _fallback_confidence(
        trend_bias=trend_bias,
        adx14=adx14,
        atr14=atr14,
        atr_sma20=atr_sma20,
    )
    strategy_behavior = _resolve_strategy_behavior(regime)
    should_trade = regime not in {"range", "low_volatility"}

    return {
        "regime": regime,
        "confidence": round(float(confidence), 4),
        "trend_regime": trend_regime,
        "volatility_regime": volatility_regime,
        "trend_bias": trend_bias,
        "strategy_behavior": strategy_behavior,
        "should_trade": should_trade,
        "price_range_threshold": price_range_threshold,
    }


def _resolve_strategy_behavior(regime: str) -> str:
    if regime == "trend":
        return "breakout"
    if regime == "high_volatility":
        return "breakout_cautious"
    return "standby"


def _trend_confidence(ema50: float, ema200: float, adx14: float, atr14: float) -> float:
    ema_strength = min(1.0, abs(ema50 - ema200) / max(atr14, 0.01) / 3.0)
    adx_strength = min(1.0, max(0.0, (adx14 - ADX_TREND_THRESHOLD) / 20.0))
    return min(0.99, 0.55 + (ema_strength * 0.2) + (adx_strength * 0.24))


def _range_confidence(adx14: float, price_range_avg: float, threshold: float) -> float:
    if threshold <= 0:
        return 0.35
    adx_component = min(1.0, max(0.0, (ADX_TREND_THRESHOLD - adx14) / ADX_TREND_THRESHOLD))
    compression_component = min(1.0, max(0.0, 1.0 - (price_range_avg / threshold)))
    return min(0.99, 0.52 + (adx_component * 0.22) + (compression_component * 0.22))


def _volatility_confidence(value: float, threshold: float, *, positive: bool) -> float:
    if threshold <= 0:
        return 0.35
    if positive:
        margin = max(0.0, value - threshold)
    else:
        margin = max(0.0, threshold - value)
    strength = min(1.0, margin / max(abs(threshold), 0.01))
    return min(0.99, 0.58 + (strength * 0.28))


def _fallback_confidence(*, trend_bias: str, adx14: float, atr14: float, atr_sma20: float) -> float:
    base = 0.36 if trend_bias == "flat" else 0.44
    adx_adjustment = min(0.1, abs(adx14 - ADX_TREND_THRESHOLD) / 100.0)
    vol_adjustment = min(0.08, abs(atr14 - atr_sma20) / max(atr_sma20, 1.0) / 4.0)
    return min(0.6, base + adx_adjustment + vol_adjustment)


def _manual_atr(candles: pd.DataFrame, length: int) -> pd.Series:
    previous_close = candles["close"].shift(1)
    true_range = pd.concat(
        [
            candles["high"] - candles["low"],
            (candles["high"] - previous_close).abs(),
            (candles["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()


def _manual_adx(candles: pd.DataFrame, length: int) -> pd.Series:
    high = candles["high"]
    low = candles["low"]
    close = candles["close"]

    up_move = high.diff()
    down_move = low.shift(1) - low
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    previous_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    plus_di = 100.0 * plus_dm.ewm(alpha=1 / length, adjust=False, min_periods=length).mean() / atr.replace(0, pd.NA)
    minus_di = 100.0 * minus_dm.ewm(alpha=1 / length, adjust=False, min_periods=length).mean() / atr.replace(0, pd.NA)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, pd.NA)
    return dx.ewm(alpha=1 / length, adjust=False, min_periods=length).mean().fillna(0.0)


def _safe_float(value: Any) -> float:
    return float(value if value is not None else 0.0)
