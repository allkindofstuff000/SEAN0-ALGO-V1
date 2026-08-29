"""
VWAP + Supertrend backtester (XAUUSD, M5 entries, session-filtered).

Signal: Supertrend flips (from bearish to bullish or vice versa) AND close is
on the correct side of the daily-anchored VWAP.

Exits: ATR-based fixed SL and TP (defaults 1:2 RR).

Uses the same OANDA data-fetch helpers as backtests/backtest_forex_engine.py
so the two strategies share cost model, session filter (12-21 UTC), position
sizing (risk-% of balance / SL distance), slippage, and commission.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from backtests import backtest_forex_engine as engine
from core.indicator_engine import IndicatorEngine
from strategies.vwap_supertrend import config as cfg


# ── Indicators ────────────────────────────────────────────────────────────

def add_supertrend(df: pd.DataFrame, period: int, mult: float) -> pd.DataFrame:
    """Add Wilder-ATR-based Supertrend. New columns: 'st', 'st_dir' (1 bull, -1 bear)."""
    df = df.copy()
    h = df["high"].astype(float).values
    l = df["low"].astype(float).values
    c = df["close"].astype(float).values
    n = len(df)
    hl2 = (h + l) / 2.0
    prev_c = np.concatenate([[c[0]], c[:-1]])
    tr = np.maximum.reduce([h - l, np.abs(h - prev_c), np.abs(l - prev_c)])
    atr = pd.Series(tr).ewm(alpha=1.0 / period, adjust=False).mean().values
    ub = hl2 + mult * atr
    lb = hl2 - mult * atr
    upper = np.zeros(n)
    lower = np.zeros(n)
    trend = np.ones(n, dtype=int)
    upper[0] = ub[0]
    lower[0] = lb[0]
    for i in range(1, n):
        upper[i] = ub[i] if (ub[i] < upper[i - 1] or c[i - 1] > upper[i - 1]) else upper[i - 1]
        lower[i] = lb[i] if (lb[i] > lower[i - 1] or c[i - 1] < lower[i - 1]) else lower[i - 1]
        if trend[i - 1] == 1:
            trend[i] = -1 if c[i] < lower[i] else 1
        else:
            trend[i] = 1 if c[i] > upper[i] else -1
    df["st"] = np.where(trend == 1, lower, upper)
    df["st_dir"] = trend
    return df


def add_session_vwap(df: pd.DataFrame) -> pd.DataFrame:
    """Daily-anchored VWAP that resets at each 00:00 UTC. New column: 'vwap'."""
    df = df.copy()
    ts = pd.to_datetime(df["timestamp"])
    day = ts.dt.floor("1D")
    tp = (df["high"] + df["low"] + df["close"]) / 3.0
    vol = df["volume"].where(df["volume"] > 0, 1.0)
    tpv = tp * vol
    df["_day"] = day
    df["_tpv"] = tpv
    df["_vol"] = vol
    df["_cumtpv"] = df.groupby("_day")["_tpv"].cumsum()
    df["_cumvol"] = df.groupby("_day")["_vol"].cumsum().replace(0, np.nan)
    df["vwap"] = df["_cumtpv"] / df["_cumvol"]
    df.drop(columns=["_day", "_tpv", "_vol", "_cumtpv", "_cumvol"], inplace=True)
    return df


def _in_session(ts: pd.Timestamp, start_h: int, end_h: int) -> bool:
    return start_h <= ts.hour < end_h


# ── Trade simulator (mirrors engine's cost model) ────────────────────────

def _simulate_trade(
    df: pd.DataFrame,
    signal_index: int,
    direction: str,
    atr: float,
    sl_mult: float,
    tp_mult: float,
    max_hold: int,
    balance: float,
    risk_pct: float,
) -> dict[str, Any] | None:
    entry_index = signal_index + 1
    if entry_index >= len(df):
        return None
    slippage = cfg.SLIPPAGE_POINTS
    comm_rate = cfg.COMMISSION_RATE
    open_price = float(df["open"].iat[entry_index])
    entry = open_price + slippage if direction == "BUY" else open_price - slippage
    risk_dist = atr * sl_mult
    tp_dist = atr * tp_mult
    if direction == "BUY":
        sl = entry - risk_dist
        tp = entry + tp_dist
    else:
        sl = entry + risk_dist
        tp = entry - tp_dist
    size = (balance * risk_pct) / risk_dist if risk_dist > 0 else 0.0
    if size <= 0:
        return None
    last_index = min(len(df) - 1, entry_index + max_hold - 1)
    for j in range(entry_index, last_index + 1):
        high = float(df["high"].iat[j])
        low = float(df["low"].iat[j])
        if direction == "BUY":
            hit_sl = low <= sl
            hit_tp = high >= tp
        else:
            hit_sl = high >= sl
            hit_tp = low <= tp
        raw_exit = None
        result = None
        exit_reason = None
        if hit_sl and hit_tp:
            raw_exit = sl
            result = "LOSS"
            exit_reason = "sl_and_tp_same_bar_sl_first"
        elif hit_sl:
            raw_exit = sl
            result = "LOSS"
            exit_reason = "stop_loss_hit"
        elif hit_tp:
            raw_exit = tp
            result = "WIN"
            exit_reason = "take_profit_hit"
        if raw_exit is not None:
            exit_p = raw_exit - slippage if direction == "BUY" else raw_exit + slippage
            gross = (exit_p - entry) * size if direction == "BUY" else (entry - exit_p) * size
            comm = (abs(entry * size) + abs(exit_p * size)) * comm_rate
            pnl = gross - comm
            return {
                "timestamp": pd.Timestamp(df["timestamp"].iat[signal_index]),
                "entry_timestamp": pd.Timestamp(df["timestamp"].iat[entry_index]),
                "exit_timestamp": pd.Timestamp(df["timestamp"].iat[j]),
                "direction": direction,
                "entry_price": round(entry, 4),
                "exit_price": round(exit_p, 4),
                "sl": round(sl, 4),
                "tp": round(tp, 4),
                "result": result,
                "R_multiple": pnl / (balance * risk_pct) if balance > 0 else 0.0,
                "position_size": float(size),
                "gross_pnl": float(gross),
                "commission": float(comm),
                "pnl": float(pnl),
                "equity_before": float(balance),
                "atr": float(atr),
                "reason": "vwap_supertrend_flip",
                "exit_reason": exit_reason,
                "bars_held": j - entry_index + 1,
                "exit_index": j,
            }
    # timeout
    close = float(df["close"].iat[last_index])
    exit_p = close - slippage if direction == "BUY" else close + slippage
    gross = (exit_p - entry) * size if direction == "BUY" else (entry - exit_p) * size
    comm = (abs(entry * size) + abs(exit_p * size)) * comm_rate
    pnl = gross - comm
    return {
        "timestamp": pd.Timestamp(df["timestamp"].iat[signal_index]),
        "entry_timestamp": pd.Timestamp(df["timestamp"].iat[entry_index]),
        "exit_timestamp": pd.Timestamp(df["timestamp"].iat[last_index]),
        "direction": direction,
        "entry_price": round(entry, 4),
        "exit_price": round(exit_p, 4),
        "sl": round(sl, 4),
        "tp": round(tp, 4),
        "result": "WIN" if pnl > 0 else "LOSS",
        "R_multiple": pnl / (balance * risk_pct) if balance > 0 else 0.0,
        "position_size": float(size),
        "gross_pnl": float(gross),
        "commission": float(comm),
        "pnl": float(pnl),
        "equity_before": float(balance),
        "atr": float(atr),
        "reason": "vwap_supertrend_flip",
        "exit_reason": f"max_hold_{max_hold}",
        "bars_held": last_index - entry_index + 1,
        "exit_index": last_index,
    }


# ── Metrics ──────────────────────────────────────────────────────────────

def _compute_metrics(trades: list[dict[str, Any]], starting_balance: float) -> dict[str, Any]:
    if not trades:
        return {
            "total_trades": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "profit_factor": 0.0,
            "average_r": 0.0, "max_drawdown_r": 0.0,
            "ending_balance": starting_balance,
        }
    n = len(trades)
    pnls = [float(t["pnl"]) for t in trades]
    rs = [float(t["R_multiple"]) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    losses = n - wins
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    pf = gp / gl if gl > 0 else float("inf")
    # Drawdown in R (matches engine's convention)
    cum_r = np.cumsum(rs)
    peak_r = np.maximum.accumulate(cum_r) if len(cum_r) else np.array([0])
    max_dd_r = float((cum_r - peak_r).min()) if len(cum_r) else 0.0
    ending = starting_balance + sum(pnls)
    return {
        "total_trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / n) * 100.0,
        "profit_factor": pf,
        "average_r": float(np.mean(rs)) if rs else 0.0,
        "max_drawdown_r": max_dd_r,
        "ending_balance": float(ending),
    }


# ── Public API ───────────────────────────────────────────────────────────

def run_backtest(
    *,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    starting_balance: float = cfg.DEFAULT_STARTING_BALANCE,
    risk_per_trade: float = cfg.DEFAULT_RISK_PER_TRADE,
    st_period: int = cfg.SUPERTREND_PERIOD,
    st_mult: float = cfg.SUPERTREND_MULTIPLIER,
    sl_atr_multiplier: float = cfg.STOP_LOSS_ATR_MULTIPLIER,
    tp_atr_multiplier: float = cfg.TAKE_PROFIT_ATR_MULTIPLIER,
    max_hold_bars: int = cfg.DEFAULT_MAX_HOLD_BARS,
    session_start_hour: int = cfg.SESSION_START_HOUR,
    session_end_hour: int = cfg.SESSION_END_HOUR,
) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    """Run the strategy over M5 XAUUSD candles. Returns (trades_df, metrics, equity_curve)."""
    warmup_start = start_utc - pd.Timedelta(days=cfg.DEFAULT_WARMUP_DAYS)
    df = engine.fetch_historical_5m_candles(warmup_start, end_utc)

    indicator_engine = IndicatorEngine()
    df = indicator_engine.add_indicators(df)  # brings atr14, rsi14, etc.
    df = add_supertrend(df, st_period, st_mult)
    df = add_session_vwap(df)

    trades: list[dict[str, Any]] = []
    balance = float(starting_balance)
    i = 30  # skip warmup
    while i < len(df) - 1:
        row = df.iloc[i]
        ts = pd.Timestamp(row["timestamp"])
        # Only fire signals inside the requested backtest window
        if ts < start_utc or ts >= end_utc:
            i += 1
            continue
        if not _in_session(ts, session_start_hour, session_end_hour):
            i += 1
            continue
        atr = row.get("atr14")
        vwap = row.get("vwap")
        d_now = row.get("st_dir")
        d_prev = df.iloc[i - 1].get("st_dir") if i > 0 else np.nan
        if pd.isna(atr) or pd.isna(vwap) or pd.isna(d_now) or pd.isna(d_prev):
            i += 1
            continue
        close = float(row["close"])
        direction: str | None = None
        if d_now == 1 and d_prev == -1 and close > vwap:
            direction = "BUY"
        elif d_now == -1 and d_prev == 1 and close < vwap:
            direction = "SELL"
        if direction is None:
            i += 1
            continue
        trade = _simulate_trade(
            df=df,
            signal_index=i,
            direction=direction,
            atr=float(atr),
            sl_mult=sl_atr_multiplier,
            tp_mult=tp_atr_multiplier,
            max_hold=max_hold_bars,
            balance=balance,
            risk_pct=risk_per_trade,
        )
        if trade is None:
            i += 1
            continue
        balance += float(trade["pnl"])
        trade["equity_after"] = float(balance)
        trades.append(trade)
        i = int(trade["exit_index"]) + 1

    # Serialise trades
    for t in trades:
        for k in ("timestamp", "entry_timestamp", "exit_timestamp"):
            if k in t and not isinstance(t[k], str):
                t[k] = str(t[k])[:19]
        t.pop("exit_index", None)

    metrics = _compute_metrics(trades, starting_balance)

    equity_curve: list[dict[str, Any]] = []
    for i2, t in enumerate(trades):
        equity_curve.append({
            "trade": i2 + 1,
            "equity": round(float(t["equity_after"]), 2),
            "ts": str(t.get("exit_timestamp") or "")[:10],
        })

    trades_df = pd.DataFrame(trades)
    return trades_df, metrics, equity_curve
