"""New-York opening-range breakout (NYORB) — shared engine for the live bot, the
backtest endpoint and the research scan.

Rules (validated 2026-10-09 on ETH, see research/crypto_daytrade_scan.py):
  • Range = high/low of the M5 bars from RANGE_START (13:30 UTC) for RANGE_MIN minutes.
  • After the range closes, the FIRST M5 close above the range high -> BUY, the first
    close below the range low -> SELL. One trade per direction per UTC day. No new
    entries at or after LAST_ENTRY (20:00 UTC).
  • Entry = next bar open (live: the price at alert time). Stop = the other side of
    the range. Target = TP_R x risk. Everything still open is closed flat at
    FLAT_AT (21:00 UTC) — the resolver enforces that via `flat_at_utc` on the signal.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd

RANGE_START = dt.time(13, 30)   # New York open (UTC)
RANGE_MIN = 60                  # range length in minutes
LAST_ENTRY = dt.time(20, 0)     # no new entries at/after this UTC time
FLAT_AT = dt.time(21, 0)        # time exit (UTC)
TP_R = 1.5                      # target as a multiple of risk
COST = 0.0006                   # round-trip cost used by the backtest


def day_range(bars: pd.DataFrame, day: pd.Timestamp, start: dt.time = RANGE_START, minutes: int = RANGE_MIN) -> dict[str, Any] | None:
    """High/low of the opening range for one UTC day, or None if it is not complete yet."""
    t0 = pd.Timestamp(day).normalize().replace(hour=start.hour, minute=start.minute)
    if t0.tzinfo is None:
        t0 = t0.tz_localize("UTC")
    t1 = t0 + pd.Timedelta(minutes=minutes)
    sub = bars[(bars["timestamp"] >= t0) & (bars["timestamp"] < t1)]
    if len(sub) < minutes // 5:
        return None
    return {"high": float(sub["high"].max()), "low": float(sub["low"].min()), "start": t0, "end": t1}


def breakout_signals(bars: pd.DataFrame, tp_r: float = TP_R, minutes: int = RANGE_MIN, long_only: bool = False,
                     start: dt.time = RANGE_START, last_entry: dt.time = LAST_ENTRY, flat_at: dt.time = FLAT_AT) -> list[dict[str, Any]]:
    """All historical signals in `bars` (closed M5 bars, tz-aware UTC timestamps).
    Each: {i, timestamp, dir, range_high, range_low, sl, tp_from_close, exit_by, flat_at}."""
    df = bars.reset_index(drop=True)
    ts = df["timestamp"]
    dates = ts.dt.floor("D")
    out: list[dict[str, Any]] = []
    for day, g in df.groupby(dates):
        rng = day_range(g, day, start, minutes)
        if rng is None:
            continue
        rh, rl = rng["high"], rng["low"]
        if rh <= rl:
            continue
        flat_ts = pd.Timestamp(day).replace(hour=flat_at.hour, minute=flat_at.minute)
        last_ts = pd.Timestamp(day).replace(hour=last_entry.hour, minute=last_entry.minute)
        watch = g[(g["timestamp"] >= rng["end"]) & (g["timestamp"] < last_ts)]
        # index of the last bar strictly before the flat time (the time exit bar)
        before_flat = g[g["timestamp"] < flat_ts]
        exit_by = int(before_flat.index[-1]) if len(before_flat) else int(g.index[-1])
        done: set[str] = set()
        prev_close = None
        for idx, r in watch.iterrows():
            c = float(r["close"])
            if "BUY" not in done and c > rh and (prev_close is None or prev_close <= rh):
                out.append({"i": int(idx), "timestamp": r["timestamp"], "dir": "BUY", "range_high": rh, "range_low": rl,
                            "sl": rl, "tp_from_close": c + tp_r * (c - rl), "exit_by": exit_by, "flat_at": flat_ts})
                done.add("BUY")
            elif (not long_only) and "SELL" not in done and c < rl and (prev_close is None or prev_close >= rl):
                out.append({"i": int(idx), "timestamp": r["timestamp"], "dir": "SELL", "range_high": rh, "range_low": rl,
                            "sl": rh, "tp_from_close": c - tp_r * (rh - c), "exit_by": exit_by, "flat_at": flat_ts})
                done.add("SELL")
            prev_close = c
            if len(done) == 2:
                break
    return out


def simulate(bars: pd.DataFrame, signals: list[dict[str, Any]], tp_r: float = TP_R, cost: float = COST,
             starting_balance: float = 10_000.0, risk_per_trade: float = 0.02) -> tuple[pd.DataFrame, dict[str, Any], list[dict[str, Any]]]:
    """Entry at the next bar's open (+ half the cost), stop-first on straddles, flat at
    the time-exit bar's close. One position at a time. Returns (trades_df, metrics, equity_curve)
    in the dashboard's backtest shape."""
    df = bars.reset_index(drop=True)
    o = df["open"].to_numpy(); h = df["high"].to_numpy(); l = df["low"].to_numpy(); c = df["close"].to_numpy()
    n = len(df)
    bal = float(starting_balance)
    trades: list[dict[str, Any]] = []
    busy_until = -1
    for s in sorted(signals, key=lambda x: x["i"]):
        i = s["i"]
        if i <= busy_until or i + 1 >= n:
            continue
        d = s["dir"]
        entry = float(o[i + 1])
        half = entry * cost / 2
        entry = entry + half if d == "BUY" else entry - half
        sl = float(s["sl"])
        risk = (entry - sl) if d == "BUY" else (sl - entry)
        if risk <= 0:
            continue
        tp = entry + tp_r * risk if d == "BUY" else entry - tp_r * risk
        last = min(n - 1, int(s["exit_by"]))
        if last < i + 1:
            continue
        exit_p, exit_j, reason = None, last, "flat_at_time_exit"
        for j in range(i + 1, last + 1):
            hit_sl = (l[j] <= sl) if d == "BUY" else (h[j] >= sl)
            hit_tp = (h[j] >= tp) if d == "BUY" else (l[j] <= tp)
            if hit_sl:
                exit_p, exit_j, reason = sl, j, "stop_loss_hit"; break
            if hit_tp:
                exit_p, exit_j, reason = tp, j, "take_profit_hit"; break
        if exit_p is None:
            exit_p = float(c[last])
        eh = exit_p * cost / 2
        exit_p = exit_p - eh if d == "BUY" else exit_p + eh
        risk_amount = bal * risk_per_trade
        size = risk_amount / risk
        pnl = (exit_p - entry) * size if d == "BUY" else (entry - exit_p) * size
        r_mult = pnl / risk_amount if risk_amount > 0 else 0.0
        before = bal
        bal += pnl
        trades.append({
            "timestamp": str(s["timestamp"])[:19],
            "entry_timestamp": str(df["timestamp"].iloc[i + 1])[:19],
            "exit_timestamp": str(df["timestamp"].iloc[exit_j])[:19],
            "direction": d,
            "entry_price": round(entry, 4), "exit_price": round(float(exit_p), 4),
            "sl": round(sl, 4), "tp": round(tp, 4),
            "range_high": round(float(s["range_high"]), 4), "range_low": round(float(s["range_low"]), 4),
            "result": "WIN" if pnl > 0 else "LOSS",
            "exit_reason": reason,
            "R_multiple": round(r_mult, 4),
            "position_size": round(size, 6),
            "pnl": round(pnl, 2),
            "equity_before": round(before, 2), "equity_after": round(bal, 2),
            "rsi": 0.0, "atr": round(float(s["range_high"]) - float(s["range_low"]), 4),
        })
        busy_until = exit_j

    tdf = pd.DataFrame(trades)
    metrics = compute_metrics(tdf, starting_balance)
    curve = [{"trade": k + 1, "equity": float(t["equity_after"]), "ts": t["exit_timestamp"][:10]} for k, t in enumerate(trades)]
    return tdf, metrics, curve


def compute_metrics(tdf: pd.DataFrame, starting_balance: float) -> dict[str, Any]:
    if tdf.empty:
        return {"total_trades": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "profit_factor": 0.0, "average_r": 0.0,
                "max_drawdown_r": 0.0, "ending_balance": float(starting_balance), "net_r": 0.0}
    r = tdf["R_multiple"].to_numpy()
    wins = int((r > 0).sum()); losses = int(len(r) - wins)
    gw = float(r[r > 0].sum()); gl = float(-r[r <= 0].sum())
    eq = np.cumsum(r)
    return {
        "total_trades": int(len(r)), "wins": wins, "losses": losses,
        "win_rate": round(wins / len(r) * 100, 2),
        "profit_factor": round(gw / gl, 3) if gl > 0 else 99.0,
        "average_r": round(float(r.mean()), 4),
        "max_drawdown_r": round(float((np.maximum.accumulate(eq) - eq).max()), 3),
        "ending_balance": round(float(tdf["equity_after"].iloc[-1]), 2),
        "net_r": round(float(r.sum()), 3),
        "time_exits": int((tdf["exit_reason"] == "flat_at_time_exit").sum()),
    }


def backtest(symbol: str, start_utc: pd.Timestamp, end_utc: pd.Timestamp, *, tp_r: float = TP_R, minutes: int = RANGE_MIN,
             long_only: bool = False, cost: float = COST, starting_balance: float = 10_000.0, risk_per_trade: float = 0.02):
    """Fetch M5 history from the Binance mirror and run the strategy over [start, end]."""
    from core.btc_fetcher import BtcFetcher  # noqa: PLC0415

    bars = BtcFetcher(symbol).fetch_range(pd.Timestamp(start_utc) - pd.Timedelta(days=1), pd.Timestamp(end_utc), "5m")
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
    bars = bars.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    sigs = [s for s in breakout_signals(bars, tp_r, minutes, long_only) if pd.Timestamp(start_utc) <= s["timestamp"] < pd.Timestamp(end_utc)]
    return simulate(bars, sigs, tp_r, cost, starting_balance, risk_per_trade)
