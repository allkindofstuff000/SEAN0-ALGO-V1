from __future__ import annotations

import os
from pathlib import Path
from typing import Any
import urllib.parse

import numpy as np
import pandas as pd

from data_fetcher import DataFetcher
from indicator_engine import IndicatorEngine
from data_fetcher import OANDA_INSTRUMENT


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
TRADES_CSV_PATH = ROOT / "backtest_trades.csv"
SYMBOL = "XAUUSDT"
ENTRY_TIMEFRAME = "5m"
TREND_TIMEFRAME = "15m"
MIN_HISTORY = 5000
STOP_LOSS_ATR_MULTIPLIER = 1.5
TAKE_PROFIT_ATR_MULTIPLIER = 3.0
MAX_OANDA_COUNT = 5000


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


def trend_candle_timestamp(entry_timestamp: pd.Timestamp) -> pd.Timestamp:
    """
    Map a 5m candle to the most recent fully closed 15m candle.

    Example:
    - evaluating a 5m candle that closes at 12:35 UTC
    - the latest closed 15m candle is the one that closed at 12:30 UTC
    - OANDA stores that 15m candle with a start timestamp of 12:15 UTC
    """

    entry_close_time = pd.Timestamp(entry_timestamp)
    if entry_close_time.tzinfo is None:
        entry_close_time = entry_close_time.tz_localize("UTC")
    else:
        entry_close_time = entry_close_time.tz_convert("UTC")
    entry_close_time = entry_close_time + pd.Timedelta(minutes=5)
    return entry_close_time.floor("15min") - pd.Timedelta(minutes=15)


def indicators_ready(row: pd.Series, columns: tuple[str, ...]) -> bool:
    return not any(pd.isna(row[column]) for column in columns)


def timeframe_delta(timeframe: str) -> pd.Timedelta:
    normalized = timeframe.strip().lower()
    if normalized.endswith("m"):
        return pd.Timedelta(minutes=int(normalized[:-1]))
    if normalized.endswith("h"):
        return pd.Timedelta(hours=int(normalized[:-1]))
    raise ValueError(f"Unsupported timeframe delta conversion: {timeframe}")


def candles_to_frame(candles: list[dict[str, Any]]) -> pd.DataFrame:
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
        return frame
    return frame.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)


def fetch_history(fetcher: DataFetcher, timeframe: str, target_count: int) -> pd.DataFrame:
    """
    Reuse the existing OANDA fetcher settings, but pull large history in chunks so
    we can safely reach 5000 candles without exceeding OANDA's hard count limit.
    """

    granularity = fetcher._oanda_granularity(timeframe)
    delta = timeframe_delta(timeframe)
    remaining = max(target_count, fetcher.min_candles)
    cursor_to: pd.Timestamp | None = None
    chunks: list[pd.DataFrame] = []

    while remaining > 0:
        request_count = min(remaining, MAX_OANDA_COUNT)
        params: dict[str, Any] = {
            "price": fetcher.oanda_price_component,
            "granularity": granularity,
            "count": request_count,
        }
        if cursor_to is not None:
            params["to"] = cursor_to.isoformat()

        url = (
            f"{fetcher._oanda_base_url}/v3/instruments/{OANDA_INSTRUMENT}/candles?"
            f"{urllib.parse.urlencode(params)}"
        )
        payload = fetcher._request_with_retry(url)
        candles = payload.get("candles", [])
        frame = candles_to_frame(candles)
        if frame.empty:
            break

        chunks.append(frame)
        remaining -= len(frame)
        cursor_to = pd.Timestamp(frame["timestamp"].iloc[0]) - delta

    if not chunks:
        raise RuntimeError(f"No historical candles fetched for timeframe {timeframe}.")

    combined = pd.concat(chunks, ignore_index=True)
    combined = combined.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    if len(combined) < target_count:
        raise RuntimeError(
            f"Not enough OANDA candles for {timeframe}. Expected at least {target_count}, got {len(combined)}."
        )
    return combined.tail(target_count).reset_index(drop=True)


def build_trade_record(
    *,
    entry_timestamp: pd.Timestamp,
    exit_timestamp: pd.Timestamp,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
    exit_price: float,
    result: str,
    bars_held: int,
    exit_reason: str,
) -> dict[str, Any]:
    risk_distance = abs(entry_price - stop_loss)
    if direction == "BUY":
        r_multiple = (exit_price - entry_price) / risk_distance
    else:
        r_multiple = (entry_price - exit_price) / risk_distance

    return {
        "timestamp": entry_timestamp,
        "exit_timestamp": exit_timestamp,
        "direction": direction,
        "entry": round(entry_price, 4),
        "sl": round(stop_loss, 4),
        "tp": round(take_profit, 4),
        "exit": round(exit_price, 4),
        "result": result,
        "R_multiple": float(r_multiple),
        "bars_held": int(bars_held),
        "exit_reason": exit_reason,
    }


def simulate_wick_trade(
    *,
    entry_df: pd.DataFrame,
    entry_index: int,
    direction: str,
    entry_price: float,
    stop_loss: float,
    take_profit: float,
) -> tuple[dict[str, Any] | None, int]:
    """
    Simulate a forex trade using real candle wicks.

    This is the key accuracy upgrade:
    we do not wait for candle closes to decide whether TP or SL was hit.
    Instead, we scan each future 5m candle and inspect its high/low range.

    Conservative tie-break:
    if both TP and SL are touched inside the same candle, we count it as SL first.
    That avoids overstating performance when the intrabar path is unknown.
    """

    for future_index in range(entry_index + 1, len(entry_df)):
        candle = entry_df.iloc[future_index]
        candle_high = float(candle["high"])
        candle_low = float(candle["low"])
        exit_timestamp = pd.Timestamp(candle["timestamp"]) + pd.Timedelta(minutes=5)
        bars_held = future_index - entry_index

        if direction == "BUY":
            hit_sl = candle_low <= stop_loss
            hit_tp = candle_high >= take_profit
            if hit_sl and hit_tp:
                return (
                    build_trade_record(
                        entry_timestamp=pd.Timestamp(entry_df.iloc[entry_index]["timestamp"]),
                        exit_timestamp=exit_timestamp,
                        direction=direction,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        exit_price=stop_loss,
                        result="LOSS",
                        bars_held=bars_held,
                        exit_reason="sl_and_tp_same_candle_sl_first",
                    ),
                    future_index,
                )
            if hit_sl:
                return (
                    build_trade_record(
                        entry_timestamp=pd.Timestamp(entry_df.iloc[entry_index]["timestamp"]),
                        exit_timestamp=exit_timestamp,
                        direction=direction,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        exit_price=stop_loss,
                        result="LOSS",
                        bars_held=bars_held,
                        exit_reason="stop_loss_hit",
                    ),
                    future_index,
                )
            if hit_tp:
                return (
                    build_trade_record(
                        entry_timestamp=pd.Timestamp(entry_df.iloc[entry_index]["timestamp"]),
                        exit_timestamp=exit_timestamp,
                        direction=direction,
                        entry_price=entry_price,
                        stop_loss=stop_loss,
                        take_profit=take_profit,
                        exit_price=take_profit,
                        result="WIN",
                        bars_held=bars_held,
                        exit_reason="take_profit_hit",
                    ),
                    future_index,
                )
            continue

        hit_sl = candle_high >= stop_loss
        hit_tp = candle_low <= take_profit
        if hit_sl and hit_tp:
            return (
                build_trade_record(
                    entry_timestamp=pd.Timestamp(entry_df.iloc[entry_index]["timestamp"]),
                    exit_timestamp=exit_timestamp,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    exit_price=stop_loss,
                    result="LOSS",
                    bars_held=bars_held,
                    exit_reason="sl_and_tp_same_candle_sl_first",
                ),
                future_index,
            )
        if hit_sl:
            return (
                build_trade_record(
                    entry_timestamp=pd.Timestamp(entry_df.iloc[entry_index]["timestamp"]),
                    exit_timestamp=exit_timestamp,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    exit_price=stop_loss,
                    result="LOSS",
                    bars_held=bars_held,
                    exit_reason="stop_loss_hit",
                ),
                future_index,
            )
        if hit_tp:
            return (
                build_trade_record(
                    entry_timestamp=pd.Timestamp(entry_df.iloc[entry_index]["timestamp"]),
                    exit_timestamp=exit_timestamp,
                    direction=direction,
                    entry_price=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    exit_price=take_profit,
                    result="WIN",
                    bars_held=bars_held,
                    exit_reason="take_profit_hit",
                ),
                future_index,
            )

    if len(entry_df) <= entry_index + 1:
        return None, entry_index

    final_candle = entry_df.iloc[-1]
    final_exit = float(final_candle["close"])
    final_exit_timestamp = pd.Timestamp(final_candle["timestamp"]) + pd.Timedelta(minutes=5)
    final_result = "WIN" if (final_exit > entry_price if direction == "BUY" else final_exit < entry_price) else "LOSS"
    return (
        build_trade_record(
            entry_timestamp=pd.Timestamp(entry_df.iloc[entry_index]["timestamp"]),
            exit_timestamp=final_exit_timestamp,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            exit_price=final_exit,
            result=final_result,
            bars_held=len(entry_df) - 1 - entry_index,
            exit_reason="end_of_data_close",
        ),
        len(entry_df) - 1,
    )


def compute_metrics(trades_df: pd.DataFrame) -> dict[str, float]:
    if trades_df.empty:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "avg_R_multiple": 0.0,
        }

    r_values = trades_df["R_multiple"].astype(float)
    wins = int((r_values > 0).sum())
    losses = int((r_values <= 0).sum())
    total_trades = int(len(trades_df))
    win_rate = (wins / total_trades) * 100.0 if total_trades else 0.0

    gross_profit = float(r_values[r_values > 0].sum())
    gross_loss = float(abs(r_values[r_values <= 0].sum()))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    equity_curve = r_values.cumsum()
    drawdown = equity_curve - equity_curve.cummax()
    max_drawdown = float(drawdown.min()) if not drawdown.empty else 0.0

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "max_drawdown": max_drawdown,
        "avg_R_multiple": float(r_values.mean()),
    }


def run_backtest() -> tuple[pd.DataFrame, dict[str, float]]:
    fetcher = DataFetcher(min_candles=MIN_HISTORY, request_limit=MIN_HISTORY)
    indicator_engine = IndicatorEngine()

    entry_candles = fetch_history(fetcher, ENTRY_TIMEFRAME, MIN_HISTORY)
    trend_candles = fetch_history(fetcher, TREND_TIMEFRAME, MIN_HISTORY)

    entry_indicators = indicator_engine.add_indicators(entry_candles)
    trend_indicators = indicator_engine.add_indicators(trend_candles)
    trend_lookup = trend_indicators.set_index("timestamp", drop=False).sort_index()

    required_entry_columns = ("rsi14", "atr14", "atr20_avg")
    required_trend_columns = ("ema50", "ema200")
    trades: list[dict[str, Any]] = []

    entry_index = 1
    while entry_index < len(entry_indicators) - 1:
        entry_candle = entry_indicators.iloc[entry_index]
        previous_candle = entry_indicators.iloc[entry_index - 1]
        entry_timestamp = pd.Timestamp(entry_candle["timestamp"])
        trend_timestamp = trend_candle_timestamp(entry_timestamp)

        if trend_timestamp not in trend_lookup.index:
            entry_index += 1
            continue

        trend_candle = trend_lookup.loc[trend_timestamp]
        if isinstance(trend_candle, pd.DataFrame):
            trend_candle = trend_candle.iloc[-1]

        if not indicators_ready(entry_candle, required_entry_columns):
            entry_index += 1
            continue
        if not indicators_ready(trend_candle, required_trend_columns):
            entry_index += 1
            continue

        bullish_bias = float(trend_candle["ema50"]) > float(trend_candle["ema200"])
        bearish_bias = float(trend_candle["ema50"]) < float(trend_candle["ema200"])
        if not bullish_bias and not bearish_bias:
            entry_index += 1
            continue

        if bullish_bias:
            direction = "BUY"
            breakout_ok = float(entry_candle["close"]) > float(previous_candle["high"])
            rsi_ok = float(entry_candle["rsi14"]) > 55.0
        else:
            direction = "SELL"
            breakout_ok = float(entry_candle["close"]) < float(previous_candle["low"])
            rsi_ok = float(entry_candle["rsi14"]) < 45.0

        atr_ok = float(entry_candle["atr14"]) > float(entry_candle["atr20_avg"])
        if not (breakout_ok and rsi_ok and atr_ok):
            entry_index += 1
            continue

        entry_price = float(entry_candle["close"])
        atr_value = float(entry_candle["atr14"])

        if direction == "BUY":
            stop_loss = entry_price - (atr_value * STOP_LOSS_ATR_MULTIPLIER)
            take_profit = entry_price + (atr_value * TAKE_PROFIT_ATR_MULTIPLIER)
        else:
            stop_loss = entry_price + (atr_value * STOP_LOSS_ATR_MULTIPLIER)
            take_profit = entry_price - (atr_value * TAKE_PROFIT_ATR_MULTIPLIER)

        trade, exit_index = simulate_wick_trade(
            entry_df=entry_indicators,
            entry_index=entry_index,
            direction=direction,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )
        if trade is not None:
            trades.append(trade)
            entry_index = max(exit_index + 1, entry_index + 1)
            continue

        entry_index += 1

    trades_df = pd.DataFrame(
        trades,
        columns=[
            "timestamp",
            "exit_timestamp",
            "direction",
            "entry",
            "sl",
            "tp",
            "exit",
            "result",
            "R_multiple",
            "bars_held",
            "exit_reason",
        ],
    )
    trades_df.to_csv(TRADES_CSV_PATH, index=False)
    return trades_df, compute_metrics(trades_df)


def main() -> int:
    load_local_env()
    trades_df, metrics = run_backtest()

    print("BACKTEST RESULTS")
    print("----------------")
    print(f"Total trades: {metrics['total_trades']}")
    print(f"Wins: {metrics['wins']}")
    print(f"Losses: {metrics['losses']}")
    print(f"Win rate: {metrics['win_rate']:.2f}%")
    print(f"Profit factor: {metrics['profit_factor']:.2f}")
    print(f"Max drawdown: {metrics['max_drawdown']:.2f} R")
    print(f"Average R multiple: {metrics['avg_R_multiple']:.2f}")
    print(f"Trade log saved to: {TRADES_CSV_PATH}")
    if trades_df.empty:
        print("No trades were generated in the current historical window.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
