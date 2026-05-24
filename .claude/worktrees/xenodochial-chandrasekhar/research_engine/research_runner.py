from __future__ import annotations

import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import backtest_xau_strategy as backtest
from indicator_engine import IndicatorEngine
from research_engine.strategy_variants import StrategyVariant


DEFAULT_BREAKOUT_BUFFER_ATR = 0.1


@dataclass(slots=True)
class ResearchDataset:
    start_label: str
    end_label: str
    start_utc: pd.Timestamp
    end_utc: pd.Timestamp
    entry_df: pd.DataFrame
    trend_lookup: pd.DataFrame


def default_window(months: int = 12) -> tuple[str, str]:
    today_utc = pd.Timestamp.now(tz="UTC").normalize()
    default_end = today_utc - pd.Timedelta(days=1)
    default_start = default_end - pd.DateOffset(months=max(1, int(months)))
    return default_start.strftime("%Y-%m-%d"), default_end.strftime("%Y-%m-%d")


def prepare_research_dataset(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    months: int = 12,
) -> ResearchDataset:
    """Load one historical dataset and indicator set for all research variants."""

    backtest.load_local_env()
    default_start, default_end = default_window(months=months)
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

    return ResearchDataset(
        start_label=start_label,
        end_label=end_label,
        start_utc=start_utc,
        end_utc=end_utc,
        entry_df=entry_df,
        trend_lookup=trend_lookup,
    )


def _rsi_thresholds(variant: StrategyVariant) -> tuple[float, float]:
    buy_threshold = float(variant.rsi_threshold)
    sell_threshold = max(0.0, min(100.0, 100.0 - buy_threshold))
    return buy_threshold, sell_threshold


def evaluate_variant_signal(
    *,
    dataset: ResearchDataset,
    signal_index: int,
    variant: StrategyVariant,
) -> dict[str, Any]:
    """Evaluate one candidate signal using a parameterized version of the base rules."""

    candle = dataset.entry_df.iloc[signal_index]
    previous = dataset.entry_df.iloc[signal_index - 1]
    signal_timestamp = pd.Timestamp(candle["timestamp"])
    close_time = signal_timestamp + pd.Timedelta(minutes=5)

    if signal_timestamp < dataset.start_utc or signal_timestamp >= dataset.end_utc:
        return {"signal": None, "reason": "outside_window"}

    trend_timestamp = backtest.trend_candle_timestamp(signal_timestamp)
    if trend_timestamp not in dataset.trend_lookup.index:
        return {"signal": None, "reason": "missing_trend_candle"}

    trend_candle = dataset.trend_lookup.loc[trend_timestamp]
    if isinstance(trend_candle, pd.DataFrame):
        trend_candle = trend_candle.iloc[-1]

    if not backtest.indicators_ready(candle, ("rsi14", "atr14", "atr20_avg")):
        return {"signal": None, "reason": "entry_indicators_not_ready"}
    if not backtest.indicators_ready(trend_candle, ("ema50", "ema200")):
        return {"signal": None, "reason": "trend_indicators_not_ready"}

    ema50 = float(trend_candle["ema50"])
    ema200 = float(trend_candle["ema200"])
    atr_value = float(candle["atr14"])
    buy_threshold, sell_threshold = _rsi_thresholds(variant)
    breakout_buffer = atr_value * DEFAULT_BREAKOUT_BUFFER_ATR * float(variant.breakout_strength_multiplier)

    if ema50 > ema200:
        direction = "BUY"
        breakout_ok = float(candle["close"]) > (float(previous["high"]) + breakout_buffer)
        rsi_ok = float(candle["rsi14"]) > buy_threshold
    elif ema50 < ema200:
        direction = "SELL"
        breakout_ok = float(candle["close"]) < (float(previous["low"]) - breakout_buffer)
        rsi_ok = float(candle["rsi14"]) < sell_threshold
    else:
        return {"signal": None, "reason": "neutral_trend"}

    atr_avg = float(candle["atr20_avg"])
    atr_ok = atr_value > atr_avg
    session_ok = backtest.session_allowed(close_time)
    weak_candle = backtest.weak_candle_filter(candle)
    consolidation = backtest.consolidation_filter(dataset.entry_df.iloc[: signal_index + 1])

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
            "risk_distance": atr_value * float(variant.atr_multiplier),
            "reason": f"research:{variant.variant_id}",
        },
        "reason": "accepted",
    }


def _simulate_variant_trades(
    *,
    dataset: ResearchDataset,
    variant: StrategyVariant,
    mode: str,
    max_hold_bars: int,
) -> pd.DataFrame:
    trades: list[dict[str, Any]] = []
    balance = float(backtest.DEFAULT_STARTING_BALANCE)
    entry_index = 1

    original_sl = float(backtest.STOP_LOSS_ATR_MULTIPLIER)
    original_tp = float(backtest.TAKE_PROFIT_ATR_MULTIPLIER)
    reward_risk_ratio = original_tp / original_sl if original_sl else 1.3333333333

    try:
        backtest.STOP_LOSS_ATR_MULTIPLIER = float(variant.atr_multiplier)
        backtest.TAKE_PROFIT_ATR_MULTIPLIER = float(variant.atr_multiplier) * reward_risk_ratio

        while entry_index < len(dataset.entry_df) - 1:
            signal_timestamp = pd.Timestamp(dataset.entry_df.iloc[entry_index]["timestamp"])
            if signal_timestamp >= dataset.end_utc:
                break

            evaluation = evaluate_variant_signal(
                dataset=dataset,
                signal_index=entry_index,
                variant=variant,
            )
            signal = evaluation["signal"]
            if signal is None:
                entry_index += 1
                continue

            if mode == "binary":
                trade, exit_index = backtest.simulate_binary_trade(
                    entry_df=dataset.entry_df,
                    signal_index=entry_index,
                    direction=str(signal["direction"]),
                    trend_candle=signal["trend_candle"],
                    signal_reason=str(signal["reason"]),
                    risk_distance=float(signal["risk_distance"]),
                )
            else:
                trade, exit_index = backtest.simulate_forex_trade(
                    entry_df=dataset.entry_df,
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
    finally:
        backtest.STOP_LOSS_ATR_MULTIPLIER = original_sl
        backtest.TAKE_PROFIT_ATR_MULTIPLIER = original_tp

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


def _finite_or_zero(value: float) -> float:
    return 0.0 if math.isnan(value) or math.isinf(value) else float(value)


def run_strategy_variant(
    params: StrategyVariant | dict[str, Any],
    *,
    dataset: ResearchDataset | None = None,
    mode: str = "forex",
    max_hold_bars: int = 12,
    start_date: str | None = None,
    end_date: str | None = None,
    months: int = 12,
) -> dict[str, Any]:
    """Run one strategy variant through the existing XAU backtest engine."""

    variant = params if isinstance(params, StrategyVariant) else StrategyVariant(
        rsi_threshold=float(params["rsi_threshold"]),
        atr_multiplier=float(params["atr_multiplier"]),
        breakout_strength_multiplier=float(params["breakout_strength_multiplier"]),
    )
    active_dataset = dataset or prepare_research_dataset(
        start_date=start_date,
        end_date=end_date,
        months=months,
    )

    trades_df = _simulate_variant_trades(
        dataset=active_dataset,
        variant=variant,
        mode=str(mode).strip().lower(),
        max_hold_bars=max_hold_bars,
    )
    metrics = backtest.compute_metrics(trades_df)

    losses = trades_df[trades_df["pnl"] < 0] if "pnl" in trades_df.columns else trades_df.iloc[0:0]
    exit_reason_counts = (
        trades_df["exit_reason"].fillna("unknown").value_counts().to_dict()
        if "exit_reason" in trades_df.columns and not trades_df.empty
        else {}
    )
    diagnostics = {
        "window": {
            "start_date": active_dataset.start_label,
            "end_date": active_dataset.end_label,
        },
        "avg_atr": round(float(trades_df["atr"].mean()), 4) if "atr" in trades_df.columns and not trades_df.empty else 0.0,
        "median_atr": round(float(trades_df["atr"].median()), 4) if "atr" in trades_df.columns and not trades_df.empty else 0.0,
        "buy_trades": int((trades_df["direction"] == "BUY").sum()) if "direction" in trades_df.columns else 0,
        "sell_trades": int((trades_df["direction"] == "SELL").sum()) if "direction" in trades_df.columns else 0,
        "loss_rate": round((len(losses) / len(trades_df)) * 100.0, 2) if len(trades_df) else 0.0,
        "exit_reason_counts": {str(key): int(value) for key, value in exit_reason_counts.items()},
    }

    return {
        "variant_id": variant.variant_id,
        "params": variant.to_dict(),
        "mode": str(mode).strip().lower(),
        "win_rate": round(float(metrics["win_rate"]), 2),
        "total_trades": int(metrics["total_trades"]),
        "profit_factor": float(metrics["profit_factor"]),
        "avg_R": round(float(metrics["average_r"]), 4),
        "max_drawdown": round(float(metrics["max_drawdown_r"]), 4),
        "ending_balance": round(float(metrics["ending_balance"]), 2),
        "diagnostics": diagnostics,
        "trades_frame": trades_df,
        "pnl_series": trades_df["pnl"].copy() if "pnl" in trades_df.columns else pd.Series(dtype="float64"),
        "r_multiple_series": trades_df["R_multiple"].copy() if "R_multiple" in trades_df.columns else pd.Series(dtype="float64"),
        "composite_return": round(_finite_or_zero(float(metrics["ending_balance"]) - backtest.DEFAULT_STARTING_BALANCE), 2),
    }
