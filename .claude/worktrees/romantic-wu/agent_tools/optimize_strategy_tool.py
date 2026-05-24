from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from itertools import product
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
import sys

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import backtest_xau_strategy as backtest
from indicator_engine import IndicatorEngine


DEFAULT_RSI_THRESHOLDS = (48.0, 50.0, 52.0, 55.0)
DEFAULT_ATR_MULTIPLIERS = (1.25, 1.5, 1.75)
DEFAULT_BREAKOUT_STRENGTHS = (0.0, 0.1, 0.2)


def _default_window() -> tuple[str, str]:
    today_utc = pd.Timestamp.now(tz="UTC").normalize()
    default_end = today_utc - pd.Timedelta(days=1)
    default_start = default_end - pd.Timedelta(days=365)
    return default_start.strftime("%Y-%m-%d"), default_end.strftime("%Y-%m-%d")


def _evaluate_signal_with_params(
    *,
    entry_df: pd.DataFrame,
    trend_lookup: pd.DataFrame,
    signal_index: int,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    rsi_threshold: float,
    atr_multiplier: float,
    breakout_strength: float,
) -> dict[str, Any]:
    candle = entry_df.iloc[signal_index]
    previous = entry_df.iloc[signal_index - 1]
    signal_timestamp = pd.Timestamp(candle["timestamp"])
    close_time = signal_timestamp + pd.Timedelta(minutes=5)

    if signal_timestamp < start_utc or signal_timestamp >= end_utc:
        return {"signal": None, "reason": "outside_window"}

    trend_timestamp = backtest.trend_candle_timestamp(signal_timestamp)
    if trend_timestamp not in trend_lookup.index:
        return {"signal": None, "reason": "missing_trend_candle"}

    trend_candle = trend_lookup.loc[trend_timestamp]
    if isinstance(trend_candle, pd.DataFrame):
        trend_candle = trend_candle.iloc[-1]

    if not backtest.indicators_ready(candle, ("rsi14", "atr14", "atr20_avg")):
        return {"signal": None, "reason": "entry_indicators_not_ready"}
    if not backtest.indicators_ready(trend_candle, ("ema50", "ema200")):
        return {"signal": None, "reason": "trend_indicators_not_ready"}

    ema50 = float(trend_candle["ema50"])
    ema200 = float(trend_candle["ema200"])
    atr_value = float(candle["atr14"])
    breakout_buffer = atr_value * float(breakout_strength)

    if ema50 > ema200:
        direction = "BUY"
        breakout_ok = float(candle["close"]) > (float(previous["high"]) + breakout_buffer)
        rsi_ok = float(candle["rsi14"]) > float(rsi_threshold)
    elif ema50 < ema200:
        direction = "SELL"
        breakout_ok = float(candle["close"]) < (float(previous["low"]) - breakout_buffer)
        rsi_ok = float(candle["rsi14"]) < float(rsi_threshold)
    else:
        return {"signal": None, "reason": "neutral_trend"}

    atr_avg = float(candle["atr20_avg"])
    atr_ok = atr_value > atr_avg
    session_ok = backtest.session_allowed(close_time)
    weak_candle = backtest.weak_candle_filter(candle)
    consolidation = backtest.consolidation_filter(entry_df.iloc[: signal_index + 1])

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
        return {"signal": None, "reason": reason}

    return {
        "signal": {
            "direction": direction,
            "trend_candle": trend_candle,
            "risk_distance": atr_value * float(atr_multiplier),
            "reason": (
                f"optimized:rsi={rsi_threshold},atr_mult={atr_multiplier},"
                f"breakout_strength={breakout_strength}"
            ),
        },
        "reason": "accepted",
    }


def _build_trades_frame(trades: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
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


def _run_candidate_backtest(
    *,
    entry_df: pd.DataFrame,
    trend_lookup: pd.DataFrame,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    mode: str,
    max_hold_bars: int,
    rsi_threshold: float,
    atr_multiplier: float,
    breakout_strength: float,
) -> pd.DataFrame:
    trades: list[dict[str, Any]] = []
    balance = float(backtest.DEFAULT_STARTING_BALANCE)
    entry_index = 1

    while entry_index < len(entry_df) - 1:
        signal_timestamp = pd.Timestamp(entry_df.iloc[entry_index]["timestamp"])
        if signal_timestamp >= end_utc:
            break

        evaluation = _evaluate_signal_with_params(
            entry_df=entry_df,
            trend_lookup=trend_lookup,
            signal_index=entry_index,
            start_utc=start_utc,
            end_utc=end_utc,
            rsi_threshold=rsi_threshold,
            atr_multiplier=atr_multiplier,
            breakout_strength=breakout_strength,
        )
        signal = evaluation["signal"]
        if signal is None:
            entry_index += 1
            continue

        if mode == "binary":
            trade, exit_index = backtest.simulate_binary_trade(
                entry_df=entry_df,
                signal_index=entry_index,
                direction=str(signal["direction"]),
                trend_candle=signal["trend_candle"],
                signal_reason=str(signal["reason"]),
                risk_distance=float(signal["risk_distance"]),
            )
        else:
            trade, exit_index = backtest.simulate_forex_trade(
                entry_df=entry_df,
                signal_index=entry_index,
                direction=str(signal["direction"]),
                trend_candle=signal["trend_candle"],
                signal_reason=str(signal["reason"]),
                risk_distance=float(signal["risk_distance"]),
                max_hold_bars=max(1, int(max_hold_bars)),
            )

        if trade is None:
            entry_index += 1
            continue

        risk_amount = balance * float(backtest.DEFAULT_RISK_PER_TRADE)
        trade["pnl"] = float(risk_amount * float(trade["R_multiple"]))
        trade["equity_before"] = float(balance)
        balance += float(trade["pnl"])
        trade["equity_after"] = float(balance)
        trades.append(trade)
        entry_index = max(exit_index + 1, entry_index + 1)

    return _build_trades_frame(trades)


def _sort_key(candidate: dict[str, Any]) -> tuple[float, float, float, int, float]:
    profit_factor = float(candidate["profit_factor"])
    profit_factor_score = profit_factor if math.isfinite(profit_factor) else 999999.0
    return (
        profit_factor_score,
        float(candidate["win_rate"]),
        float(candidate["avg_R"]),
        int(candidate["trades"]),
        float(candidate["ending_balance"]),
    )


def _normalize_iterable(values: Iterable[float]) -> tuple[float, ...]:
    return tuple(float(value) for value in values)


def _optimize_strategy_impl(
    *,
    start_date: str | None,
    end_date: str | None,
    mode: str,
    max_hold_bars: int,
    rsi_thresholds: Iterable[float],
    atr_multipliers: Iterable[float],
    breakout_strengths: Iterable[float],
) -> dict[str, Any]:
    backtest.load_local_env()

    default_start, default_end = _default_window()
    start_label = start_date or default_start
    end_label = end_date or default_end

    start_utc = backtest.parse_date_utc(start_label)
    end_utc = backtest.parse_date_utc(end_label, inclusive_end=True)
    if end_utc <= start_utc:
        raise ValueError("end_date must be after start_date")

    warmup_start = start_utc - pd.Timedelta(days=backtest.DEFAULT_WARMUP_DAYS)
    candles_5m = backtest.fetch_historical_5m_candles(warmup_start, end_utc)
    candles_15m = backtest.resample_to_15m(candles_5m)

    indicator_engine = IndicatorEngine()
    entry_df = indicator_engine.add_indicators(candles_5m)
    trend_df = indicator_engine.add_indicators(candles_15m)
    trend_lookup = trend_df.set_index("timestamp", drop=False).sort_index()

    original_sl = float(backtest.STOP_LOSS_ATR_MULTIPLIER)
    original_tp = float(backtest.TAKE_PROFIT_ATR_MULTIPLIER)
    reward_risk_ratio = original_tp / original_sl if original_sl else 1.3333333333

    candidates: list[dict[str, Any]] = []
    try:
        for rsi_threshold, atr_multiplier, breakout_strength in product(
            _normalize_iterable(rsi_thresholds),
            _normalize_iterable(atr_multipliers),
            _normalize_iterable(breakout_strengths),
        ):
            backtest.STOP_LOSS_ATR_MULTIPLIER = float(atr_multiplier)
            backtest.TAKE_PROFIT_ATR_MULTIPLIER = float(atr_multiplier) * reward_risk_ratio

            trades_df = _run_candidate_backtest(
                entry_df=entry_df,
                trend_lookup=trend_lookup,
                start_utc=start_utc,
                end_utc=end_utc,
                mode=mode,
                max_hold_bars=max_hold_bars,
                rsi_threshold=rsi_threshold,
                atr_multiplier=atr_multiplier,
                breakout_strength=breakout_strength,
            )
            metrics = backtest.compute_metrics(trades_df)
            candidates.append(
                {
                    "rsi_threshold": round(rsi_threshold, 4),
                    "atr_multiplier": round(atr_multiplier, 4),
                    "breakout_strength": round(breakout_strength, 4),
                    "trades": int(metrics["total_trades"]),
                    "win_rate": round(float(metrics["win_rate"]), 2),
                    "profit_factor": float(metrics["profit_factor"]),
                    "avg_R": round(float(metrics["average_r"]), 4),
                    "max_drawdown": round(float(metrics["max_drawdown_r"]), 4),
                    "ending_balance": round(float(metrics["ending_balance"]), 2),
                }
            )
    finally:
        backtest.STOP_LOSS_ATR_MULTIPLIER = original_sl
        backtest.TAKE_PROFIT_ATR_MULTIPLIER = original_tp

    if not candidates:
        raise RuntimeError("No optimization candidates were evaluated.")

    ranked = sorted(candidates, key=_sort_key, reverse=True)
    best = ranked[0]
    return {
        "start_date": start_label,
        "end_date": end_label,
        "mode": str(mode).strip().lower(),
        "tested_candidates": len(ranked),
        "best_parameters": {
            "rsi_threshold": best["rsi_threshold"],
            "atr_multiplier": best["atr_multiplier"],
            "breakout_strength": best["breakout_strength"],
        },
        "best_metrics": {
            "trades": best["trades"],
            "win_rate": best["win_rate"],
            "profit_factor": best["profit_factor"],
            "avg_R": best["avg_R"],
            "max_drawdown": best["max_drawdown"],
            "ending_balance": best["ending_balance"],
        },
        "top_candidates": ranked[:5],
    }


def optimize_strategy(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    mode: str = "forex",
    max_hold_bars: int = 12,
    rsi_thresholds: Iterable[float] = DEFAULT_RSI_THRESHOLDS,
    atr_multipliers: Iterable[float] = DEFAULT_ATR_MULTIPLIERS,
    breakout_strengths: Iterable[float] = DEFAULT_BREAKOUT_STRENGTHS,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    """Grid-search the strategy parameters with a hard timeout."""

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            _optimize_strategy_impl,
            start_date=start_date,
            end_date=end_date,
            mode=str(mode).strip().lower(),
            max_hold_bars=max(1, int(max_hold_bars)),
            rsi_thresholds=tuple(rsi_thresholds),
            atr_multipliers=tuple(atr_multipliers),
            breakout_strengths=tuple(breakout_strengths),
        )
        try:
            return future.result(timeout=max(1, int(timeout_seconds)))
        except FuturesTimeout as error:
            raise TimeoutError(
                f"Strategy optimization exceeded timeout after {timeout_seconds}s."
            ) from error
