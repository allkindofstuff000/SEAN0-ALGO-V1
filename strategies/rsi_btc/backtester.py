"""RSI EMA backtester for BTCUSD (24/7 crypto).

Reuses the EXACT XAU RSI EMA signal engine (`backtests.backtest_forex_engine`):
same EMA50/200 M15 trend, RSI 55/45 momentum, M5 breakout of the prior candle,
trend-strength / volatility / range filters, next-bar-open entry and ATR-based
SL/TP. Only two things differ for BTC:

  1. Candles come from BTCUSDT via `core.btc_fetcher` (Binance data mirror).
  2. The gold **session filter is bypassed** — crypto trades 24/7, so
     `engine.session_allowed` is temporarily forced True for the run.

BTC klines carry no bid/ask, so the engine automatically uses its mid-price +
flat-slippage fill path (the realistic bid/ask + M1 path only engages when
bid/ask columns are present).

Backtests are serialised on the web layer's single `_backtest_lock`, so the
temporary monkeypatch of `engine.session_allowed` can never be seen by a
concurrent XAU run.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from backtests import backtest_forex_engine as engine
from core.btc_fetcher import BtcFetcher
from core.indicator_engine import IndicatorEngine


def run_backtest(
    *,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    starting_balance: float = 10_000.0,
    risk_per_trade: float = 0.02,
    max_hold_bars: int | None = None,
    detection_lag_seconds: float = 0.0,
) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    """Run the RSI EMA strategy over BTCUSDT M5 candles. Returns (trades_df, metrics, equity_curve)."""
    max_hold = engine.DEFAULT_MAX_HOLD if max_hold_bars is None else int(max_hold_bars)
    warmup_start = pd.Timestamp(start_utc) - pd.Timedelta(days=engine.DEFAULT_WARMUP_DAYS)

    fetcher = BtcFetcher()
    candles_5m = fetcher.fetch_range(warmup_start, pd.Timestamp(end_utc), "5m")
    if candles_5m is None or candles_5m.empty or len(candles_5m) < 250:
        raise RuntimeError(f"insufficient BTC candles ({0 if candles_5m is None else len(candles_5m)})")
    candles_15m = engine.resample_to_15m(candles_5m)

    ind = IndicatorEngine()
    entry_df = ind.add_indicators(candles_5m)
    trend_df = ind.add_indicators(candles_15m)
    trend_df = engine.add_adx14(trend_df)  # only used if the volatility switch is on
    trend_lookup = trend_df.set_index("timestamp", drop=False).sort_index()

    trades: list[dict[str, Any]] = []
    balance = float(starting_balance)

    _orig_session = engine.session_allowed
    engine.session_allowed = lambda *_a, **_k: True  # crypto is 24/7
    try:
        with engine.DECISION_TRACE_PATH.open("w", encoding="utf-8") as trace_handle:
            entry_index = 1
            while entry_index < len(entry_df) - 1:
                signal_timestamp = pd.Timestamp(entry_df.iloc[entry_index]["timestamp"])
                if signal_timestamp >= pd.Timestamp(end_utc):
                    break
                evaluation = engine.evaluate_signal(
                    entry_df=entry_df,
                    trend_lookup=trend_lookup,
                    signal_index=entry_index,
                    start_utc=pd.Timestamp(start_utc),
                    end_utc=pd.Timestamp(end_utc),
                    trace_handle=trace_handle,
                )
                signal = evaluation["signal"]
                if signal is None:
                    entry_index += 1
                    continue

                risk_amount = balance * risk_per_trade
                trade, exit_index = engine.simulate_forex_trade(
                    entry_df=entry_df,
                    signal_index=entry_index,
                    direction=str(signal["direction"]),
                    trend_candle=signal["trend_candle"],
                    signal_reason=str(signal["reason"]),
                    risk_distance=float(signal["risk_distance"]),
                    balance_before=float(balance),
                    risk_amount=float(risk_amount),
                    max_hold_bars=max(1, int(max_hold)),
                    m1_lookup=None,
                    detection_lag_seconds=detection_lag_seconds,
                )
                if trade is None:
                    entry_index += 1
                    continue

                balance += float(trade["pnl"])
                trade["equity_after"] = float(balance)
                trades.append(trade)
                entry_index = max(exit_index + 1, entry_index + 1)
    finally:
        engine.session_allowed = _orig_session

    trades_df = pd.DataFrame(trades)
    metrics = engine.compute_metrics(trades_df)
    metrics["detection_lag_seconds"] = float(detection_lag_seconds or 0.0)

    equity_curve: list[dict[str, Any]] = []
    if not trades_df.empty and "equity_after" in trades_df.columns:
        ts_col = next((c for c in ("exit_timestamp", "entry_timestamp", "timestamp") if c in trades_df.columns), None)
        for i, row in trades_df.reset_index(drop=True).iterrows():
            equity_curve.append({
                "trade": int(i) + 1,
                "equity": round(float(row["equity_after"]), 2),
                "ts": str(row[ts_col])[:10] if ts_col else str(i),
            })

    return trades_df, metrics, equity_curve
