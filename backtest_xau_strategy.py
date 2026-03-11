from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any, TextIO

import matplotlib
import numpy as np
import pandas as pd
import requests

from data_fetcher import OANDA_BASE_URLS, OANDA_INSTRUMENT
from indicator_engine import IndicatorEngine


matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
TRADES_CSV_PATH = ROOT / "trades.csv"
DECISION_TRACE_PATH = ROOT / "decision_trace.log"
EQUITY_CURVE_PATH = ROOT / "equity_curve.png"

ENTRY_TIMEFRAME = "5m"
TREND_TIMEFRAME = "15m"
ENTRY_GRANULARITY = "M5"
DEFAULT_MODE = "forex"
DEFAULT_MAX_HOLD = 12
DEFAULT_STARTING_BALANCE = 10_000.0
DEFAULT_RISK_PER_TRADE = 0.01
DEFAULT_WARMUP_DAYS = 30
MAX_CHUNK_DAYS = 14
REQUEST_TIMEOUT = 30
MAX_RETRIES = 5
RETRY_SECONDS = 12
SPREAD_POINTS = 0.30
SLIPPAGE_POINTS = 0.05
BUY_RSI_THRESHOLD = 50.0
SELL_RSI_THRESHOLD = 50.0
STOP_LOSS_ATR_MULTIPLIER = 1.5
TAKE_PROFIT_ATR_MULTIPLIER = 2.0
ENABLE_CONSOLIDATION_FILTER = False


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


def parse_date_utc(value: str, *, inclusive_end: bool = False) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    timestamp = timestamp.normalize()
    if inclusive_end:
        timestamp = timestamp + pd.Timedelta(days=1)
    return timestamp


def build_request_windows(start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    cursor = start_utc
    step = pd.Timedelta(days=MAX_CHUNK_DAYS)
    while cursor < end_utc:
        window_end = min(cursor + step, end_utc)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def fetch_historical_5m_candles(start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> pd.DataFrame:
    api_key = os.getenv("OANDA_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OANDA_API_KEY is required to backtest XAU_USD.")

    environment = os.getenv("OANDA_ENV", "practice").strip().lower()
    price_component = os.getenv("OANDA_PRICE_COMPONENT", "M").strip().upper() or "M"
    base_url = OANDA_BASE_URLS.get(environment, OANDA_BASE_URLS["practice"])

    frames: list[pd.DataFrame] = []
    for window_start, window_end in build_request_windows(start_utc, end_utc):
        frames.append(
            fetch_oanda_window(
                base_url=base_url,
                api_key=api_key,
                price_component=price_component,
                start_utc=window_start,
                end_utc=window_end,
            )
        )

    if not frames:
        raise RuntimeError("No historical windows were fetched from OANDA.")

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    if combined.empty:
        raise RuntimeError("No complete OANDA candles were returned for the requested window.")
    return combined


def fetch_oanda_window(
    *,
    base_url: str,
    api_key: str,
    price_component: str,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
) -> pd.DataFrame:
    url = f"{base_url}/v3/instruments/{OANDA_INSTRUMENT}/candles"
    params = {
        "granularity": ENTRY_GRANULARITY,
        "price": price_component,
        "from": start_utc.isoformat(),
        "to": end_utc.isoformat(),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept-Datetime-Format": "RFC3339",
        "User-Agent": "SEAN0-ALGO-V1-backtest/3.0",
    }

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            candles = payload.get("candles", [])
            return candles_to_frame(candles)
        except (requests.RequestException, ValueError) as error:
            last_error = error
            if attempt >= MAX_RETRIES:
                break
            print(
                f"[FETCH] OANDA request failed window={start_utc.date()}->{end_utc.date()} "
                f"attempt={attempt}/{MAX_RETRIES} error={error} retry_in={RETRY_SECONDS}s"
            )
            time.sleep(RETRY_SECONDS)

    raise RuntimeError(f"Unable to fetch OANDA candles for {start_utc} -> {end_utc}: {last_error}")


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
    return 7 <= close_time_utc.hour < 21


def indicators_ready(row: pd.Series, columns: tuple[str, ...]) -> bool:
    return not any(pd.isna(row[column]) for column in columns)


def trend_candle_timestamp(entry_timestamp: pd.Timestamp) -> pd.Timestamp:
    entry_close_time = pd.Timestamp(entry_timestamp)
    if entry_close_time.tzinfo is None:
        entry_close_time = entry_close_time.tz_localize("UTC")
    else:
        entry_close_time = entry_close_time.tz_convert("UTC")
    entry_close_time = entry_close_time + pd.Timedelta(minutes=5)
    return entry_close_time.floor("15min") - pd.Timedelta(minutes=15)


def effective_entry_price(open_price: float, direction: str) -> float:
    execution_cost = SPREAD_POINTS + SLIPPAGE_POINTS
    if direction == "BUY":
        return open_price + execution_cost
    return open_price - execution_cost


def weak_candle_filter(candle: pd.Series) -> bool:
    candle_range = float(candle["high"]) - float(candle["low"])
    atr_value = float(candle["atr14"])
    return candle_range < atr_value * 0.5


def consolidation_filter(entry_slice: pd.DataFrame) -> bool:
    if not ENABLE_CONSOLIDATION_FILTER:
        return False
    recent = entry_slice.tail(12)
    if len(recent) < 12:
        return False
    total_range = float(recent["high"].max()) - float(recent["low"].min())
    atr_avg = float(recent["atr20_avg"].iloc[-1])
    return total_range < atr_avg * 1.5


def write_trace(handle: TextIO, lines: list[str]) -> None:
    handle.write("\n".join(lines))
    handle.write("\n\n")


def simulate_binary_trade(
    *,
    entry_df: pd.DataFrame,
    signal_index: int,
    direction: str,
    trend_candle: pd.Series,
    signal_reason: str,
    risk_distance: float,
) -> tuple[dict[str, Any] | None, int]:
    entry_index = signal_index + 1
    if entry_index >= len(entry_df):
        return None, signal_index

    entry_candle = entry_df.iloc[entry_index]
    entry_price = effective_entry_price(float(entry_candle["open"]), direction)
    exit_price = float(entry_candle["close"])

    if direction == "BUY":
        win = exit_price > entry_price
    else:
        win = exit_price < entry_price

    r_multiple = 1.0 if win else -1.0
    return (
        {
            "timestamp": pd.Timestamp(entry_df.iloc[signal_index]["timestamp"]),
            "entry_timestamp": pd.Timestamp(entry_candle["timestamp"]),
            "exit_timestamp": pd.Timestamp(entry_candle["timestamp"]) + pd.Timedelta(minutes=5),
            "direction": direction,
            "entry_price": round(entry_price, 4),
            "exit_price": round(exit_price, 4),
            "sl": round(entry_price - risk_distance if direction == "BUY" else entry_price + risk_distance, 4),
            "tp": round(entry_price + (risk_distance * (TAKE_PROFIT_ATR_MULTIPLIER / STOP_LOSS_ATR_MULTIPLIER))
                        if direction == "BUY"
                        else entry_price - (risk_distance * (TAKE_PROFIT_ATR_MULTIPLIER / STOP_LOSS_ATR_MULTIPLIER)), 4),
            "result": "WIN" if win else "LOSS",
            "R_multiple": r_multiple,
            "ema50": float(trend_candle["ema50"]),
            "ema200": float(trend_candle["ema200"]),
            "rsi": float(entry_df.iloc[signal_index]["rsi14"]),
            "atr": float(entry_df.iloc[signal_index]["atr14"]),
            "reason": signal_reason,
            "exit_reason": "next_candle_close",
        },
        entry_index,
    )


def simulate_forex_trade(
    *,
    entry_df: pd.DataFrame,
    signal_index: int,
    direction: str,
    trend_candle: pd.Series,
    signal_reason: str,
    risk_distance: float,
    max_hold_bars: int,
) -> tuple[dict[str, Any] | None, int]:
    entry_index = signal_index + 1
    if entry_index >= len(entry_df):
        return None, signal_index

    entry_candle = entry_df.iloc[entry_index]
    entry_price = effective_entry_price(float(entry_candle["open"]), direction)

    if direction == "BUY":
        stop_loss = entry_price - risk_distance
        take_profit = entry_price + (float(entry_df.iloc[signal_index]["atr14"]) * TAKE_PROFIT_ATR_MULTIPLIER)
    else:
        stop_loss = entry_price + risk_distance
        take_profit = entry_price - (float(entry_df.iloc[signal_index]["atr14"]) * TAKE_PROFIT_ATR_MULTIPLIER)

    last_index = min(len(entry_df) - 1, entry_index + max_hold_bars - 1)
    for future_index in range(entry_index, last_index + 1):
        future_candle = entry_df.iloc[future_index]
        candle_high = float(future_candle["high"])
        candle_low = float(future_candle["low"])
        exit_timestamp = pd.Timestamp(future_candle["timestamp"]) + pd.Timedelta(minutes=5)

        if direction == "BUY":
            hit_sl = candle_low <= stop_loss
            hit_tp = candle_high >= take_profit
            if hit_sl and hit_tp:
                exit_price = stop_loss
                result = "LOSS"
                exit_reason = "sl_and_tp_same_candle_sl_first"
            elif hit_sl:
                exit_price = stop_loss
                result = "LOSS"
                exit_reason = "stop_loss_hit"
            elif hit_tp:
                exit_price = take_profit
                result = "WIN"
                exit_reason = "take_profit_hit"
            else:
                continue
        else:
            hit_sl = candle_high >= stop_loss
            hit_tp = candle_low <= take_profit
            if hit_sl and hit_tp:
                exit_price = stop_loss
                result = "LOSS"
                exit_reason = "sl_and_tp_same_candle_sl_first"
            elif hit_sl:
                exit_price = stop_loss
                result = "LOSS"
                exit_reason = "stop_loss_hit"
            elif hit_tp:
                exit_price = take_profit
                result = "WIN"
                exit_reason = "take_profit_hit"
            else:
                continue

        if direction == "BUY":
            r_multiple = (exit_price - entry_price) / risk_distance
        else:
            r_multiple = (entry_price - exit_price) / risk_distance

        return (
            {
                "timestamp": pd.Timestamp(entry_df.iloc[signal_index]["timestamp"]),
                "entry_timestamp": pd.Timestamp(entry_candle["timestamp"]),
                "exit_timestamp": exit_timestamp,
                "direction": direction,
                "entry_price": round(entry_price, 4),
                "exit_price": round(exit_price, 4),
                "sl": round(stop_loss, 4),
                "tp": round(take_profit, 4),
                "result": result,
                "R_multiple": float(r_multiple),
                "ema50": float(trend_candle["ema50"]),
                "ema200": float(trend_candle["ema200"]),
                "rsi": float(entry_df.iloc[signal_index]["rsi14"]),
                "atr": float(entry_df.iloc[signal_index]["atr14"]),
                "reason": signal_reason,
                "exit_reason": exit_reason,
            },
            future_index,
        )

    final_candle = entry_df.iloc[last_index]
    final_exit = float(final_candle["close"])
    if direction == "BUY":
        r_multiple = (final_exit - entry_price) / risk_distance
    else:
        r_multiple = (entry_price - final_exit) / risk_distance
    result = "WIN" if r_multiple > 0 else "LOSS"
    return (
        {
            "timestamp": pd.Timestamp(entry_df.iloc[signal_index]["timestamp"]),
            "entry_timestamp": pd.Timestamp(entry_candle["timestamp"]),
            "exit_timestamp": pd.Timestamp(final_candle["timestamp"]) + pd.Timedelta(minutes=5),
            "direction": direction,
            "entry_price": round(entry_price, 4),
            "exit_price": round(final_exit, 4),
            "sl": round(stop_loss, 4),
            "tp": round(take_profit, 4),
            "result": result,
            "R_multiple": float(r_multiple),
            "ema50": float(trend_candle["ema50"]),
            "ema200": float(trend_candle["ema200"]),
            "rsi": float(entry_df.iloc[signal_index]["rsi14"]),
            "atr": float(entry_df.iloc[signal_index]["atr14"]),
            "reason": signal_reason,
            "exit_reason": f"max_hold_{max_hold_bars}",
        },
        last_index,
    )


def evaluate_signal(
    *,
    entry_df: pd.DataFrame,
    trend_lookup: pd.DataFrame,
    signal_index: int,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    trace_handle: TextIO,
) -> dict[str, Any]:
    candle = entry_df.iloc[signal_index]
    previous = entry_df.iloc[signal_index - 1]
    signal_timestamp = pd.Timestamp(candle["timestamp"])
    close_time = signal_timestamp + pd.Timedelta(minutes=5)
    session = detect_session(close_time)
    trace_lines: list[str] = [f"[TIME] {close_time.isoformat()}"]

    if signal_timestamp < start_utc or signal_timestamp >= end_utc:
        trace_lines.append("[RESULT] OUTSIDE WINDOW")
        write_trace(trace_handle, trace_lines)
        return {"signal": None, "reason": "outside_window"}

    trend_timestamp = trend_candle_timestamp(signal_timestamp)
    if trend_timestamp not in trend_lookup.index:
        trace_lines.append("[TREND] missing closed 15m candle")
        trace_lines.append("[RESULT] NO SIGNAL")
        write_trace(trace_handle, trace_lines)
        return {"signal": None, "reason": "missing_trend_candle"}

    trend_candle = trend_lookup.loc[trend_timestamp]
    if isinstance(trend_candle, pd.DataFrame):
        trend_candle = trend_candle.iloc[-1]

    if not indicators_ready(candle, ("rsi14", "atr14", "atr20_avg")):
        trace_lines.append("[INDICATORS] entry indicators not ready")
        trace_lines.append("[RESULT] NO SIGNAL")
        write_trace(trace_handle, trace_lines)
        return {"signal": None, "reason": "entry_indicators_not_ready"}

    if not indicators_ready(trend_candle, ("ema50", "ema200")):
        trace_lines.append("[INDICATORS] trend indicators not ready")
        trace_lines.append("[RESULT] NO SIGNAL")
        write_trace(trace_handle, trace_lines)
        return {"signal": None, "reason": "trend_indicators_not_ready"}

    ema50 = float(trend_candle["ema50"])
    ema200 = float(trend_candle["ema200"])
    if ema50 > ema200:
        direction = "BUY"
        trace_lines.append(f"[TREND] EMA50 > EMA200 -> bullish ({ema50:.2f}/{ema200:.2f})")
        breakout_ok = float(candle["close"]) > float(previous["high"])
        rsi_ok = float(candle["rsi14"]) > BUY_RSI_THRESHOLD
    elif ema50 < ema200:
        direction = "SELL"
        trace_lines.append(f"[TREND] EMA50 < EMA200 -> bearish ({ema50:.2f}/{ema200:.2f})")
        breakout_ok = float(candle["close"]) < float(previous["low"])
        rsi_ok = float(candle["rsi14"]) < SELL_RSI_THRESHOLD
    else:
        trace_lines.append(f"[TREND] EMA50 == EMA200 -> neutral ({ema50:.2f}/{ema200:.2f})")
        trace_lines.append("[RESULT] NO SIGNAL")
        write_trace(trace_handle, trace_lines)
        return {"signal": None, "reason": "neutral_trend"}

    if direction == "BUY":
        trace_lines.append(
            f"[BREAKOUT] close > prev high -> {breakout_ok} "
            f"({float(candle['close']):.2f}/{float(previous['high']):.2f})"
        )
        trace_lines.append(
            f"[MOMENTUM] RSI = {float(candle['rsi14']):.2f} -> "
            f"{'valid' if rsi_ok else 'invalid'}"
        )
    else:
        trace_lines.append(
            f"[BREAKOUT] close < prev low -> {breakout_ok} "
            f"({float(candle['close']):.2f}/{float(previous['low']):.2f})"
        )
        trace_lines.append(
            f"[MOMENTUM] RSI = {float(candle['rsi14']):.2f} -> "
            f"{'valid' if rsi_ok else 'invalid'}"
        )

    atr_value = float(candle["atr14"])
    atr_avg = float(candle["atr20_avg"])
    atr_ok = atr_value > atr_avg
    trace_lines.append(
        f"[VOLATILITY] ATR > ATR_avg -> {atr_ok} ({atr_value:.2f}/{atr_avg:.2f})"
    )

    session_ok = session_allowed(close_time)
    trace_lines.append(
        f"[SESSION] {session} -> {'allowed' if session_ok else 'blocked'}"
    )

    weak_candle = weak_candle_filter(candle)
    trace_lines.append(
        f"[FILTER] weak candle -> {'rejected' if weak_candle else 'ok'} "
        f"({float(candle['high']) - float(candle['low']):.2f}/{atr_value * 0.5:.2f})"
    )

    consolidation = consolidation_filter(entry_df.iloc[: signal_index + 1])
    if ENABLE_CONSOLIDATION_FILTER:
        trace_lines.append(f"[FILTER] consolidation -> {'rejected' if consolidation else 'ok'}")

    reason = "accepted"
    if not breakout_ok:
        reason = "no_breakout"
    elif not rsi_ok:
        reason = "momentum_invalid"
    elif not atr_ok:
        reason = "low_volatility"
    elif not session_ok:
        reason = "session_blocked"
    elif weak_candle:
        reason = "weak_candle"
    elif consolidation:
        reason = "consolidation"

    if reason != "accepted":
        trace_lines.append(f"[RESULT] NO SIGNAL ({reason})")
        write_trace(trace_handle, trace_lines)
        return {"signal": None, "reason": reason}

    signal_reason = "trend_breakout_momentum_volatility"
    trace_lines.append(f"[RESULT] {direction} SIGNAL")
    write_trace(trace_handle, trace_lines)
    return {
        "signal": {
            "direction": direction,
            "trend_candle": trend_candle,
            "risk_distance": atr_value * STOP_LOSS_ATR_MULTIPLIER,
            "reason": signal_reason,
        },
        "reason": "accepted",
    }


def compute_metrics(trades_df: pd.DataFrame) -> dict[str, float]:
    if trades_df.empty:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "average_r": 0.0,
            "max_drawdown_r": 0.0,
            "ending_balance": DEFAULT_STARTING_BALANCE,
        }

    pnl_values = trades_df["pnl"].astype(float)
    r_values = trades_df["R_multiple"].astype(float)
    cumulative_r = r_values.cumsum()
    drawdown_r = cumulative_r - cumulative_r.cummax()

    gross_profit = float(pnl_values[pnl_values > 0].sum())
    gross_loss = float(abs(pnl_values[pnl_values < 0].sum()))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    wins = int((pnl_values > 0).sum())
    losses = int((pnl_values <= 0).sum())
    total_trades = int(len(trades_df))
    win_rate = (wins / total_trades) * 100.0 if total_trades else 0.0

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "average_r": float(r_values.mean()),
        "max_drawdown_r": float(drawdown_r.min()) if not drawdown_r.empty else 0.0,
        "ending_balance": float(trades_df["equity_after"].iloc[-1]),
    }


def save_equity_curve(trades_df: pd.DataFrame) -> None:
    plt.figure(figsize=(12, 6))
    if trades_df.empty:
        plt.title("XAUUSD Equity Curve (no trades)")
        plt.xlabel("Trade")
        plt.ylabel("Balance ($)")
        plt.grid(alpha=0.2)
        plt.tight_layout()
        plt.savefig(EQUITY_CURVE_PATH, dpi=150, bbox_inches="tight")
        plt.close()
        return

    ordered = trades_df.sort_values("exit_timestamp").reset_index(drop=True)
    plt.plot(ordered["exit_timestamp"], ordered["equity_after"], color="#d1a300", linewidth=1.8)
    plt.title("XAUUSD Strategy Equity Curve")
    plt.xlabel("Exit time")
    plt.ylabel("Balance ($)")
    plt.grid(alpha=0.2)
    plt.tight_layout()
    plt.savefig(EQUITY_CURVE_PATH, dpi=150, bbox_inches="tight")
    plt.close()


def run_backtest(
    *,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    mode: str,
    max_hold_bars: int,
) -> tuple[pd.DataFrame, dict[str, float]]:
    warmup_start = start_utc - pd.Timedelta(days=DEFAULT_WARMUP_DAYS)
    candles_5m = fetch_historical_5m_candles(warmup_start, end_utc)
    candles_15m = resample_to_15m(candles_5m)

    indicator_engine = IndicatorEngine()
    entry_df = indicator_engine.add_indicators(candles_5m)
    trend_df = indicator_engine.add_indicators(candles_15m)
    trend_lookup = trend_df.set_index("timestamp", drop=False).sort_index()

    trades: list[dict[str, Any]] = []
    balance = DEFAULT_STARTING_BALANCE

    with DECISION_TRACE_PATH.open("w", encoding="utf-8") as trace_handle:
        entry_index = 1
        while entry_index < len(entry_df) - 1:
            signal_timestamp = pd.Timestamp(entry_df.iloc[entry_index]["timestamp"])
            if signal_timestamp >= end_utc:
                break

            evaluation = evaluate_signal(
                entry_df=entry_df,
                trend_lookup=trend_lookup,
                signal_index=entry_index,
                start_utc=start_utc,
                end_utc=end_utc,
                trace_handle=trace_handle,
            )
            signal = evaluation["signal"]
            if signal is None:
                entry_index += 1
                continue

            if mode == "binary":
                trade, exit_index = simulate_binary_trade(
                    entry_df=entry_df,
                    signal_index=entry_index,
                    direction=str(signal["direction"]),
                    trend_candle=signal["trend_candle"],
                    signal_reason=str(signal["reason"]),
                    risk_distance=float(signal["risk_distance"]),
                )
            else:
                trade, exit_index = simulate_forex_trade(
                    entry_df=entry_df,
                    signal_index=entry_index,
                    direction=str(signal["direction"]),
                    trend_candle=signal["trend_candle"],
                    signal_reason=str(signal["reason"]),
                    risk_distance=float(signal["risk_distance"]),
                    max_hold_bars=max_hold_bars,
                )

            if trade is None:
                entry_index += 1
                continue

            risk_amount = balance * DEFAULT_RISK_PER_TRADE
            trade["pnl"] = float(risk_amount * float(trade["R_multiple"]))
            trade["equity_before"] = float(balance)
            balance += float(trade["pnl"])
            trade["equity_after"] = float(balance)
            trades.append(trade)
            entry_index = max(exit_index + 1, entry_index + 1)

    trades_df = pd.DataFrame(
        trades,
        columns=[
            "timestamp",
            "entry_timestamp",
            "exit_timestamp",
            "direction",
            "entry_price",
            "exit_price",
            "sl",
            "tp",
            "result",
            "R_multiple",
            "ema50",
            "ema200",
            "rsi",
            "atr",
            "reason",
            "exit_reason",
            "pnl",
            "equity_before",
            "equity_after",
        ],
    )
    metrics = compute_metrics(trades_df)
    return trades_df, metrics


def parse_args() -> argparse.Namespace:
    today_utc = pd.Timestamp.now(tz="UTC").normalize()
    default_end = today_utc - pd.Timedelta(days=1)
    default_start = default_end - pd.Timedelta(days=365)

    parser = argparse.ArgumentParser(description="Backtest the XAUUSD 15m/5m breakout strategy.")
    parser.add_argument("--start", default=default_start.strftime("%Y-%m-%d"), help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=default_end.strftime("%Y-%m-%d"), help="End date YYYY-MM-DD")
    parser.add_argument("--mode", default=DEFAULT_MODE, choices=("binary", "forex"), help="Backtest mode")
    parser.add_argument(
        "--max_hold",
        type=int,
        default=DEFAULT_MAX_HOLD,
        help="Maximum number of 5m bars to hold a forex trade",
    )
    return parser.parse_args()


def main() -> int:
    load_local_env()
    args = parse_args()

    start_utc = parse_date_utc(args.start)
    end_utc = parse_date_utc(args.end, inclusive_end=True)
    if end_utc <= start_utc:
        print("[BACKTEST] failed: --end must be after --start.")
        return 1

    try:
        trades_df, metrics = run_backtest(
            start_utc=start_utc,
            end_utc=end_utc,
            mode=args.mode,
            max_hold_bars=max(1, int(args.max_hold)),
        )
    except Exception as error:
        print(f"[BACKTEST] failed: {error}")
        return 1

    trades_df.to_csv(TRADES_CSV_PATH, index=False)
    save_equity_curve(trades_df)

    print("BACKTEST RESULTS")
    print("----------------")
    print(f"Mode: {args.mode}")
    print(f"Window: {args.start} -> {args.end}")
    print(f"Total trades: {metrics['total_trades']}")
    print(f"Wins: {metrics['wins']}")
    print(f"Losses: {metrics['losses']}")
    print(f"Win rate: {metrics['win_rate']:.2f}%")
    print(f"Profit factor: {metrics['profit_factor']:.2f}")
    print(f"Average R: {metrics['average_r']:.2f}")
    print(f"Max drawdown: {metrics['max_drawdown_r']:.2f} R")
    print(f"Ending balance: ${metrics['ending_balance']:.2f}")
    print(f"Trade log saved to: {TRADES_CSV_PATH}")
    print(f"Decision trace saved to: {DECISION_TRACE_PATH}")
    print(f"Equity curve saved to: {EQUITY_CURVE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
