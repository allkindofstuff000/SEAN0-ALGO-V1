from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from indicator_engine import IndicatorEngine
from trade_filters import run_trade_filters


# NEW: Simple OANDA XAU backtest using the live 15m/5m strategy.
ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
TRADES_CSV_PATH = ROOT / "trades.csv"
OANDA_INSTRUMENT = "XAU_USD"
OANDA_BASE_URLS = {
    "practice": "https://api-fxpractice.oanda.com",
    "live": "https://api-fxtrade.oanda.com",
}
ENTRY_TIMEFRAME = "5m"
TREND_TIMEFRAME = "15m"
ENTRY_GRANULARITY = "M5"
DEFAULT_COUNT = 5000
DEFAULT_MODE = "binary"
REQUEST_TIMEOUT = 30
MAX_RETRIES = 5
RETRY_SECONDS = 12


def load_local_env(env_path: Path = ENV_PATH) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def fetch_historical_5m_candles(count: int = DEFAULT_COUNT) -> pd.DataFrame:
    api_key = os.getenv("OANDA_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OANDA_API_KEY is required to backtest XAU_USD.")

    environment = os.getenv("OANDA_ENV", "practice").strip().lower()
    price_component = os.getenv("OANDA_PRICE_COMPONENT", "M").strip().upper() or "M"
    base_url = OANDA_BASE_URLS.get(environment, OANDA_BASE_URLS["practice"])

    url = f"{base_url}/v3/instruments/{OANDA_INSTRUMENT}/candles"
    params = {
        "granularity": ENTRY_GRANULARITY,
        "count": int(count),
        "price": price_component,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept-Datetime-Format": "RFC3339",
        "User-Agent": "SEAN0-ALGO-V1-backtest/1.0",
    }

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            candles = payload.get("candles", [])
            if not candles:
                raise RuntimeError("OANDA returned no candle data.")
            return _candles_to_frame(candles)
        except (requests.RequestException, ValueError, RuntimeError) as error:
            last_error = error
            if attempt >= MAX_RETRIES:
                break
            print(
                f"[FETCH] OANDA request failed attempt={attempt}/{MAX_RETRIES} "
                f"error={error} retry_in={RETRY_SECONDS}s"
            )
            time.sleep(RETRY_SECONDS)

    raise RuntimeError(f"Unable to fetch historical XAU_USD candles: {last_error}")


def _candles_to_frame(candles: list[dict[str, Any]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for candle in candles:
        if not candle.get("complete", False):
            continue
        price_bucket = candle.get("mid") or candle.get("bid") or candle.get("ask")
        if not isinstance(price_bucket, dict):
            continue
        rows.append(
            {
                "timestamp": pd.to_datetime(candle["time"], utc=True),
                "open": float(price_bucket["o"]),
                "high": float(price_bucket["h"]),
                "low": float(price_bucket["l"]),
                "close": float(price_bucket["c"]),
                "volume": float(candle.get("volume", 0.0)),
            }
        )

    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    if frame.empty:
        raise RuntimeError("No complete OANDA candles were available for backtesting.")

    return frame.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)


def resample_to_15m(candles_5m: pd.DataFrame) -> pd.DataFrame:
    indexed = candles_5m.set_index("timestamp")
    resampled = indexed.resample("15min", label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    resampled = resampled.dropna(subset=["open", "high", "low", "close"]).reset_index()
    return resampled


def detect_session(close_time_utc: pd.Timestamp) -> str:
    hour = close_time_utc.hour
    if 12 <= hour < 16:
        return "OVERLAP"
    if 7 <= hour < 16:
        return "LONDON"
    if 16 <= hour < 21:
        return "NEW_YORK"
    return "ASIAN"


def session_allowed(close_time_utc: pd.Timestamp) -> bool:
    return detect_session(close_time_utc) in {"LONDON", "OVERLAP", "NEW_YORK"}


def indicators_ready(row: pd.Series, columns: tuple[str, ...]) -> bool:
    return not any(pd.isna(row[column]) for column in columns)


def trend_candle_timestamp(entry_timestamp: pd.Timestamp) -> pd.Timestamp:
    entry_close_time = entry_timestamp + pd.Timedelta(minutes=5)
    return entry_close_time.floor("15min") - pd.Timedelta(minutes=15)


def simulate_binary_trade(entry_df: pd.DataFrame, entry_index: int, direction: str) -> dict[str, Any] | None:
    if entry_index + 1 >= len(entry_df):
        return None

    entry_candle = entry_df.iloc[entry_index]
    exit_candle = entry_df.iloc[entry_index + 1]
    entry_price = float(entry_candle["close"])
    exit_price = float(exit_candle["close"])

    if direction == "BUY":
        win = exit_price > entry_price
    else:
        win = exit_price < entry_price

    return {
        "timestamp": entry_candle["timestamp"],
        "direction": direction,
        "entry": entry_price,
        "exit": exit_price,
        "result": "WIN" if win else "LOSS",
        "R_multiple": 1.0 if win else -1.0,
    }


def simulate_forex_trade(entry_df: pd.DataFrame, entry_index: int, direction: str) -> dict[str, Any] | None:
    entry_candle = entry_df.iloc[entry_index]
    atr_value = float(entry_candle["atr14"])
    if atr_value <= 0:
        return None

    entry_price = float(entry_candle["close"])
    risk_distance = atr_value * 1.5
    reward_distance = atr_value * 3.0

    if direction == "BUY":
        stop_loss = entry_price - risk_distance
        take_profit = entry_price + reward_distance
    else:
        stop_loss = entry_price + risk_distance
        take_profit = entry_price - reward_distance

    for future_index in range(entry_index + 1, len(entry_df)):
        future_candle = entry_df.iloc[future_index]
        high = float(future_candle["high"])
        low = float(future_candle["low"])

        if direction == "BUY":
            hit_sl = low <= stop_loss
            hit_tp = high >= take_profit
            if hit_sl and hit_tp:
                return build_forex_trade(entry_candle, direction, stop_loss, -1.0)
            if hit_sl:
                return build_forex_trade(entry_candle, direction, stop_loss, -1.0)
            if hit_tp:
                return build_forex_trade(entry_candle, direction, take_profit, 2.0)
        else:
            hit_sl = high >= stop_loss
            hit_tp = low <= take_profit
            if hit_sl and hit_tp:
                return build_forex_trade(entry_candle, direction, stop_loss, -1.0)
            if hit_sl:
                return build_forex_trade(entry_candle, direction, stop_loss, -1.0)
            if hit_tp:
                return build_forex_trade(entry_candle, direction, take_profit, 2.0)

    final_close = float(entry_df.iloc[-1]["close"])
    if direction == "BUY":
        r_multiple = (final_close - entry_price) / risk_distance
    else:
        r_multiple = (entry_price - final_close) / risk_distance
    return build_forex_trade(entry_candle, direction, final_close, r_multiple)


def build_forex_trade(entry_candle: pd.Series, direction: str, exit_price: float, r_multiple: float) -> dict[str, Any]:
    return {
        "timestamp": entry_candle["timestamp"],
        "direction": direction,
        "entry": float(entry_candle["close"]),
        "exit": float(exit_price),
        "result": "WIN" if r_multiple > 0 else "LOSS",
        "R_multiple": float(r_multiple),
    }


def compute_metrics(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_R": 0.0,
            "max_drawdown": 0.0,
        }

    r_values = trades["R_multiple"].astype(float)
    equity_curve = r_values.cumsum()
    running_peak = equity_curve.cummax()
    drawdown = equity_curve - running_peak

    wins = int((r_values > 0).sum())
    losses = int((r_values <= 0).sum())
    total_trades = int(len(trades))
    win_rate = (wins / total_trades) * 100 if total_trades else 0.0
    avg_r = float(r_values.mean()) if total_trades else 0.0
    max_drawdown = float(drawdown.min()) if total_trades else 0.0

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "avg_R": avg_r,
        "max_drawdown": max_drawdown,
    }


def run_backtest(mode: str, count: int) -> tuple[pd.DataFrame, dict[str, float]]:
    candles_5m = fetch_historical_5m_candles(count=count)
    candles_15m = resample_to_15m(candles_5m)

    indicator_engine = IndicatorEngine()
    entry_df = indicator_engine.add_indicators(candles_5m)
    trend_df = indicator_engine.add_indicators(candles_15m)
    trend_lookup = trend_df.set_index("timestamp")

    required_indicator_columns = ("ema50", "ema200", "rsi14", "atr14", "atr20_avg")
    trades: list[dict[str, Any]] = []

    for entry_index in range(1, len(entry_df)):
        entry_candle = entry_df.iloc[entry_index]
        previous_candle = entry_df.iloc[entry_index - 1]
        entry_timestamp = pd.Timestamp(entry_candle["timestamp"])
        current_trend_timestamp = trend_candle_timestamp(entry_timestamp)

        if current_trend_timestamp not in trend_lookup.index:
            continue

        trend_candle = trend_lookup.loc[current_trend_timestamp]
        if isinstance(trend_candle, pd.DataFrame):
            trend_candle = trend_candle.iloc[-1]

        if not indicators_ready(entry_candle, required_indicator_columns):
            continue
        if not indicators_ready(trend_candle, ("ema50", "ema200", "atr14")):
            continue

        candle_close_time = entry_timestamp + pd.Timedelta(minutes=5)
        if not session_allowed(candle_close_time):
            continue

        bullish_trend = float(trend_candle["ema50"]) > float(trend_candle["ema200"])
        bearish_trend = float(trend_candle["ema50"]) < float(trend_candle["ema200"])
        if not bullish_trend and not bearish_trend:
            continue

        if bullish_trend:
            direction = "BUY"
            breakout_ok = float(entry_candle["close"]) > float(previous_candle["high"])
            rsi_ok = float(entry_candle["rsi14"]) > 55.0
        else:
            direction = "SELL"
            breakout_ok = float(entry_candle["close"]) < float(previous_candle["low"])
            rsi_ok = float(entry_candle["rsi14"]) < 45.0

        atr_ok = float(entry_candle["atr14"]) > float(entry_candle["atr20_avg"])
        if not (breakout_ok and rsi_ok and atr_ok):
            continue

        filter_result = run_trade_filters(
            entry_df.iloc[: entry_index + 1],
            trend_ema50=float(trend_candle["ema50"]),
            trend_ema200=float(trend_candle["ema200"]),
            trend_atr=float(trend_candle["atr14"]),
        )
        if not filter_result["allowed"]:
            continue

        if mode == "binary":
            trade = simulate_binary_trade(entry_df, entry_index, direction)
        else:
            trade = simulate_forex_trade(entry_df, entry_index, direction)

        if trade is not None:
            trades.append(trade)

    trades_df = pd.DataFrame(trades, columns=["timestamp", "direction", "entry", "exit", "result", "R_multiple"])
    metrics = compute_metrics(trades_df)
    return trades_df, metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest the XAU_USD 15m/5m breakout strategy.")
    parser.add_argument(
        "mode",
        nargs="?",
        default=DEFAULT_MODE,
        choices=("binary", "forex"),
        help="Backtest mode: binary or forex. Defaults to binary.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=DEFAULT_COUNT,
        help="Number of historical 5m candles to fetch from OANDA. Defaults to 5000.",
    )
    return parser.parse_args()


def main() -> int:
    load_local_env()
    args = parse_args()

    try:
        trades_df, metrics = run_backtest(mode=args.mode, count=args.count)
    except Exception as error:
        print(f"[BACKTEST] failed: {error}")
        return 1

    trades_df.to_csv(TRADES_CSV_PATH, index=False)

    print("BACKTEST RESULTS")
    print("----------------")
    print(f"Mode: {args.mode}")
    print(f"Total trades: {metrics['total_trades']}")
    print(f"Wins: {metrics['wins']}")
    print(f"Losses: {metrics['losses']}")
    print(f"Win rate: {metrics['win_rate']:.1f}%")
    print(f"Average R: {metrics['avg_R']:.2f}")
    print(f"Max drawdown: {metrics['max_drawdown']:.2f}R")
    print(f"Trade log saved to: {TRADES_CSV_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
