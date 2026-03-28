from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

PIP_SIZE = 0.10


def to_utc_timestamp(value: Any) -> pd.Timestamp:
    if isinstance(value, pd.Timestamp):
        ts = value
    elif isinstance(value, (int, float)):
        ts = pd.Timestamp(int(value), unit="s", tz="UTC")
    else:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        else:
            ts = ts.tz_convert("UTC")
    return ts


def candle_unix(candle: dict[str, Any]) -> int:
    raw = candle.get("time", candle.get("timestamp"))
    return int(to_utc_timestamp(raw).timestamp())


def normalize_candle(candle: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": candle_unix(candle),
        "open": float(candle.get("open", 0.0)),
        "high": float(candle.get("high", 0.0)),
        "low": float(candle.get("low", 0.0)),
        "close": float(candle.get("close", 0.0)),
        "volume": int(float(candle.get("volume", 0) or 0)),
        "complete": bool(candle.get("complete", candle.get("is_closed", True))),
        "is_closed": bool(candle.get("is_closed", candle.get("complete", True))),
        "timeframe": candle.get("timeframe"),
    }


def normalize_candles(candles: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: dict[int, dict[str, Any]] = {}
    for candle in candles or []:
        norm = normalize_candle(candle)
        deduped[norm["time"]] = norm
    return [deduped[key] for key in sorted(deduped)]


def timeframe_to_minutes(timeframe: str | int) -> int:
    if isinstance(timeframe, int):
        return timeframe
    label = str(timeframe).upper().strip()
    if label.startswith("M"):
        return int(label[1:])
    if label.startswith("H"):
        return int(label[1:]) * 60
    raise ValueError(f"Unsupported timeframe: {timeframe}")


def resample_candles(candles: Iterable[dict[str, Any]], timeframe: str | int) -> list[dict[str, Any]]:
    normalized = normalize_candles(candles)
    if not normalized:
        return []

    df = pd.DataFrame(normalized)
    df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True)
    df = df.set_index("dt").sort_index()

    minutes = timeframe_to_minutes(timeframe)
    agg = df.resample(f"{minutes}min", label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    ).dropna()

    out: list[dict[str, Any]] = []
    for idx, row in agg.iterrows():
        out.append(
            {
                "time": int(idx.timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row.get("volume", 0) or 0),
                "complete": True,
                "is_closed": True,
                "timeframe": f"M{minutes}" if minutes < 60 else f"H{minutes // 60}",
            }
        )
    return out


def candle_range(candle: dict[str, Any]) -> float:
    return float(candle["high"]) - float(candle["low"])


def candle_body(candle: dict[str, Any]) -> float:
    return abs(float(candle["close"]) - float(candle["open"]))


def upper_wick(candle: dict[str, Any]) -> float:
    return float(candle["high"]) - max(float(candle["open"]), float(candle["close"]))


def lower_wick(candle: dict[str, Any]) -> float:
    return min(float(candle["open"]), float(candle["close"])) - float(candle["low"])


def is_bullish(candle: dict[str, Any]) -> bool:
    return float(candle["close"]) > float(candle["open"])


def is_bearish(candle: dict[str, Any]) -> bool:
    return float(candle["close"]) < float(candle["open"])


def price_to_pips(price_delta: float) -> float:
    return round(float(price_delta) / PIP_SIZE, 1)


def safe_round(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None
