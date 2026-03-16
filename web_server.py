"""SEAN0-ALGO Web Dashboard Server
Replaces the legacy Streamlit dashboard.py.

Run:
    python web_server.py

Then open:  http://localhost:8000
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Boot ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

LOG_PATH = ROOT / "logs" / "decision_trace.log"
TRADES_CSV_PATH = ROOT / "backtest_trades.csv"
STATIC_DIR = ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
LOGGER = logging.getLogger("dashboard")

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="SEAN0-ALGO Dashboard API", docs_url="/api/docs")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schemas ───────────────────────────────────────────────────────────────────
class BacktestRequest(BaseModel):
    start_date: str | None = None
    end_date: str | None = None
    # 5-20 candles → SL/TP multiplier = candles × 0.3
    # Default: sl=5 → 1.5×ATR, tp=10 → 3.0×ATR  (matches live engine defaults)
    sl_candles: int = 5
    tp_candles: int = 10


# ── Helpers ───────────────────────────────────────────────────────────────────
def _parse_log_line(raw: str) -> dict[str, str]:
    """Split 'timestamp | level | logger | message' log lines."""
    parts = raw.split(" | ", 3)
    if len(parts) >= 4:
        return {
            "timestamp": parts[0].strip(),
            "level": parts[1].strip(),
            "logger": parts[2].strip(),
            "message": parts[3].strip(),
            "raw": raw,
        }
    return {"timestamp": "", "level": "INFO", "logger": "", "message": raw, "raw": raw}


def _safe_float(v: Any) -> Any:
    """Return JSON-safe scalar."""
    if isinstance(v, float) and (v != v or v == float("inf") or v == float("-inf")):
        return None
    return v


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/logs")
def get_logs(limit: int = 20) -> dict[str, Any]:
    """Return the last *limit* log entries, newest first."""
    if not LOG_PATH.exists():
        return {"logs": [], "error": "Log file not found – start the bot first."}
    try:
        lines = [
            ln
            for ln in LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip()
        ]
        recent = list(reversed(lines[-limit:]))
        return {"logs": [_parse_log_line(ln) for ln in recent]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/trades")
def get_trades() -> dict[str, Any]:
    """Return all trades from the last backtest CSV, newest first."""
    if not TRADES_CSV_PATH.exists():
        return {"trades": [], "count": 0}
    try:
        df = pd.read_csv(TRADES_CSV_PATH).fillna("")
        if "timestamp" in df.columns:
            df = df.sort_values("timestamp", ascending=False)
        return {"trades": df.to_dict(orient="records"), "count": len(df)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/backtest")
def run_backtest(req: BacktestRequest) -> dict[str, Any]:
    """
    Fetch OANDA history, apply optional date filter, then simulate the
    XAUUSD strategy using the caller-supplied SL/TP candle counts.
    """
    try:
        from backtest_forex_engine import (  # noqa: PLC0415
            fetch_history,
            indicators_ready,
            simulate_wick_trade,
            compute_metrics,
            trend_candle_timestamp,
        )
        from data_fetcher import DataFetcher  # noqa: PLC0415
        from indicator_engine import IndicatorEngine  # noqa: PLC0415

        # Map slider value → ATR multiplier
        # 5  → 1.5 ×ATR   (tight – matches live engine default SL)
        # 10 → 3.0 ×ATR   (matches live engine default TP)
        # 20 → 6.0 ×ATR   (wide – swing trade)
        sl_mult = req.sl_candles * 0.3
        tp_mult = req.tp_candles * 0.3

        LOGGER.info(
            "Backtest start sl_candles=%s→%.2f×ATR  tp_candles=%s→%.2f×ATR  "
            "start=%s  end=%s",
            req.sl_candles, sl_mult,
            req.tp_candles, tp_mult,
            req.start_date, req.end_date,
        )

        fetcher = DataFetcher(min_candles=300, request_limit=5000)
        ind_engine = IndicatorEngine()

        # Fetch 5 000 candles → ~17 days of 5m data
        entry_raw = fetch_history(fetcher, "5m", 5000)
        trend_raw = fetch_history(fetcher, "15m", 5000)

        # Add indicators on full dataset (EMA200 needs 200+ candle warm-up)
        entry_ind = ind_engine.add_indicators(entry_raw)
        trend_ind = ind_engine.add_indicators(trend_raw)
        trend_lookup = trend_ind.set_index("timestamp", drop=False).sort_index()

        # Filter entry candles to requested date window AFTER indicators are ready
        filtered = entry_ind.copy()
        if req.start_date:
            start_ts = pd.Timestamp(req.start_date, tz="UTC")
            filtered = filtered[filtered["timestamp"] >= start_ts]
        if req.end_date:
            end_ts = pd.Timestamp(req.end_date, tz="UTC")
            filtered = filtered[filtered["timestamp"] <= end_ts]
        filtered = filtered.reset_index(drop=True)

        if len(filtered) < 50:
            return {
                "error": "Not enough data in the selected date range. "
                         "Try widening the range or check OANDA history availability.",
                "metrics": {},
                "trades": [],
                "equity_curve": [],
            }

        required_entry = ("rsi14", "atr14", "atr20_avg")
        required_trend = ("ema50", "ema200")
        trades: list[dict[str, Any]] = []
        idx = 1

        while idx < len(filtered) - 1:
            e_row = filtered.iloc[idx]
            p_row = filtered.iloc[idx - 1]
            e_ts = pd.Timestamp(e_row["timestamp"])
            t_ts = trend_candle_timestamp(e_ts)

            if t_ts not in trend_lookup.index:
                idx += 1
                continue

            t_row = trend_lookup.loc[t_ts]
            if isinstance(t_row, pd.DataFrame):
                t_row = t_row.iloc[-1]

            if not indicators_ready(e_row, required_entry) or not indicators_ready(t_row, required_trend):
                idx += 1
                continue

            bull = float(t_row["ema50"]) > float(t_row["ema200"])
            bear = float(t_row["ema50"]) < float(t_row["ema200"])
            if not bull and not bear:
                idx += 1
                continue

            if bull:
                direction = "BUY"
                signal_ok = (
                    float(e_row["close"]) > float(p_row["high"])
                    and float(e_row["rsi14"]) > 55.0
                )
            else:
                direction = "SELL"
                signal_ok = (
                    float(e_row["close"]) < float(p_row["low"])
                    and float(e_row["rsi14"]) < 45.0
                )

            atr_ok = float(e_row["atr14"]) > float(e_row["atr20_avg"])
            if not signal_ok or not atr_ok:
                idx += 1
                continue

            ep = float(e_row["close"])
            atr = float(e_row["atr14"])
            if direction == "BUY":
                sl = ep - atr * sl_mult
                tp = ep + atr * tp_mult
            else:
                sl = ep + atr * sl_mult
                tp = ep - atr * tp_mult

            trade, exit_idx = simulate_wick_trade(
                entry_df=filtered,
                entry_index=idx,
                direction=direction,
                entry_price=ep,
                stop_loss=sl,
                take_profit=tp,
            )
            if trade:
                trades.append(trade)
                idx = max(exit_idx + 1, idx + 1)
            else:
                idx += 1

        trades_df = pd.DataFrame(trades) if trades else pd.DataFrame()
        metrics = compute_metrics(trades_df)

        # Build equity curve list
        equity_curve: list[dict[str, Any]] = []
        if not trades_df.empty and "R_multiple" in trades_df.columns:
            cumulative = trades_df["R_multiple"].astype(float).cumsum().values
            tss = (
                trades_df["timestamp"].astype(str).str[:10].tolist()
                if "timestamp" in trades_df.columns
                else [str(i) for i in range(len(cumulative))]
            )
            for i, (ts, val) in enumerate(zip(tss, cumulative)):
                equity_curve.append({"trade": i + 1, "equity": round(float(val), 4), "ts": ts})

        # Serialise trades
        trades_out: list[dict[str, Any]] = []
        if not trades_df.empty:
            for row in trades_df.fillna("").to_dict(orient="records"):
                for k in ("timestamp", "exit_timestamp"):
                    if k in row and not isinstance(row[k], str):
                        row[k] = str(row[k])[:19]
                trades_out.append(row)

        # Serialise metrics (handle Inf / NaN)
        safe_metrics = {
            k: _safe_float(float(v)) if isinstance(v, (int, float)) else v
            for k, v in metrics.items()
        }

        LOGGER.info(
            "Backtest complete trades=%s win_rate=%.1f%%",
            safe_metrics.get("total_trades", 0),
            safe_metrics.get("win_rate", 0.0) or 0.0,
        )
        return {"metrics": safe_metrics, "trades": trades_out, "equity_curve": equity_curve}

    except Exception as exc:
        LOGGER.exception("Backtest failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ── Static files (React SPA) ──────────────────────────────────────────────────
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    LOGGER.info("Starting SEAN0-ALGO dashboard on http://localhost:8000")
    uvicorn.run("web_server:app", host="0.0.0.0", port=8000, reload=False)
