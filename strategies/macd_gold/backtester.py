"""Gold MACD day-trade backtester (XAUUSD, H1) — matches the live macd_gold_live bot.

MACD(12,26,9) signal-line cross in the H1 EMA200 trend direction; SL 1.5xATR /
TP 3.0xATR (RR 1:2), hold <=24 H1 bars. Fetches gold M5 via the shared engine,
resamples to H1 (label='left', matching OANDA's native hour-open bars), simulates
on H1 OHLC (stop-first on a straddle, conservative) with an adverse spread cost.

Returns (trades_df, metrics, equity_curve) — the same shape as the RSI EMA / VWAP+ST
/ BTC backtesters, so the web layer + dashboard consume it identically.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from backtests import backtest_forex_engine as engine

MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
TREND_EMA, ATR_LEN = 200, 14
SL_ATR, TP_ATR, MAX_HOLD = 1.5, 3.0, 24
SPREAD = 0.30          # adverse spread/slippage on entry (gold)
WARMUP_DAYS = 20       # H1 EMA200 needs ~200 bars; 20d of H1 covers it


def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _atr(df: pd.DataFrame, n: int) -> pd.Series:
    h, l, c = df["high"], df["low"], df["close"]
    pc = c.shift(1)
    tr = pd.concat([h - l, (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1.0 / n, adjust=False).mean()


def _resample_h1(df: pd.DataFrame) -> pd.DataFrame:
    idx = df.set_index("timestamp")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return idx.resample("1h", label="left", closed="left").agg(agg).dropna(subset=["open", "high", "low", "close"]).reset_index()


def run_backtest(
    *,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    starting_balance: float = 10_000.0,
    risk_per_trade: float = 0.03,
    detection_lag_seconds: float = 0.0,  # accepted for signature parity; unused (H1)
    **_ignored: Any,
) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    warmup = pd.Timestamp(start_utc) - pd.Timedelta(days=WARMUP_DAYS)
    m5 = engine.fetch_historical_5m_candles(warmup, pd.Timestamp(end_utc))
    df = _resample_h1(m5)
    if df is None or len(df) < 210:
        return pd.DataFrame(), engine.compute_metrics(pd.DataFrame()), []
    df["ema200"] = _ema(df["close"], TREND_EMA)
    macd = _ema(df["close"], MACD_FAST) - _ema(df["close"], MACD_SLOW)
    df["macd"] = macd
    df["signal"] = _ema(macd, MACD_SIGNAL)
    df["atr"] = _atr(df, ATR_LEN)

    trades: list[dict[str, Any]] = []
    bal = float(starting_balance)
    n = len(df)
    s_utc, e_utc = pd.Timestamp(start_utc), pd.Timestamp(end_utc)
    i = 205
    while i < n - 1:
        r, p = df.iloc[i], df.iloc[i - 1]
        ts = pd.Timestamp(r["timestamp"])
        if ts < s_utc or ts >= e_utc:
            i += 1
            continue
        e200, a, c = r["ema200"], r["atr"], float(r["close"])
        m, sg, pm, ps = r["macd"], r["signal"], p["macd"], p["signal"]
        if any(pd.isna(x) for x in (e200, a, m, sg, pm, ps)) or float(a) <= 0:
            i += 1
            continue
        cu = (pm <= ps) and (m > sg)
        cd = (pm >= ps) and (m < sg)
        d = "BUY" if (c > float(e200) and cu) else ("SELL" if (c < float(e200) and cd) else None)
        if d is None:
            i += 1
            continue

        a = float(a)
        entry = float(df.iloc[i + 1]["open"])
        entry = entry + SPREAD if d == "BUY" else entry - SPREAD
        risk, rew = a * SL_ATR, a * TP_ATR
        sl = entry - risk if d == "BUY" else entry + risk
        tp = entry + rew if d == "BUY" else entry - rew
        last = min(n - 1, i + 1 + MAX_HOLD - 1)
        result, exit_p, exit_reason, exit_j = None, None, None, last
        for j in range(i + 1, last + 1):
            hi, lo = float(df.iloc[j]["high"]), float(df.iloc[j]["low"])
            hit_sl = (lo <= sl) if d == "BUY" else (hi >= sl)
            hit_tp = (hi >= tp) if d == "BUY" else (lo <= tp)
            if hit_sl:
                result, exit_p, exit_reason, exit_j = "LOSS", sl, "stop_loss_hit", j
                break
            if hit_tp:
                result, exit_p, exit_reason, exit_j = "WIN", tp, "take_profit_hit", j
                break
        if result is None:
            exit_p = float(df.iloc[last]["close"])
            exit_reason = f"max_hold_{MAX_HOLD}"

        size = (bal * risk_per_trade) / risk if risk > 0 else 0.0
        pnl = (exit_p - entry) * size if d == "BUY" else (entry - exit_p) * size
        r_mult = pnl / (bal * risk_per_trade) if bal > 0 else 0.0
        if result is None:
            result = "WIN" if pnl > 0 else "LOSS"
        bal += pnl
        trades.append({
            "timestamp": str(ts)[:19],
            "entry_timestamp": str(df.iloc[i + 1]["timestamp"])[:19],
            "exit_timestamp": str(df.iloc[exit_j]["timestamp"])[:19],
            "direction": d,
            "entry_price": round(entry, 2),
            "exit_price": round(float(exit_p), 2),
            "sl": round(sl, 2),
            "tp": round(tp, 2),
            "result": result,
            "R_multiple": round(r_mult, 4),
            "pnl": round(pnl, 2),
            "equity_after": round(bal, 2),
            "atr": round(a, 2),
            "exit_reason": exit_reason,
        })
        i = exit_j + 1

    trades_df = pd.DataFrame(trades)
    metrics = engine.compute_metrics(trades_df)
    metrics["detection_lag_seconds"] = 0.0
    equity_curve = [{"trade": k + 1, "equity": t["equity_after"], "ts": str(t["exit_timestamp"])[:10]} for k, t in enumerate(trades)]
    return trades_df, metrics, equity_curve
