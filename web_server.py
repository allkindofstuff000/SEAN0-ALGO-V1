"""SEAN0-ALGO Web Dashboard Server
Replaces the legacy Streamlit dashboard.py.

Run:
    python web_server.py

Then open:  http://localhost:8000
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# ── Boot ──────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

# Live-bot decision log (written by main.py)
LOG_PATH = ROOT / "logs" / "decision_trace.log"
# Backtest trades CSV (written by backtest_forex_engine.run_backtest)
TRADES_CSV_PATH = ROOT / "trades.csv"
STATIC_DIR = ROOT / "static"
STATIC_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
LOGGER = logging.getLogger("dashboard")

# Prevent two backtest runs overlapping
_backtest_lock = threading.Lock()

# ── Bot process management ────────────────────────────────────────────────────
_bot_process: subprocess.Popen | None = None
_bot_lock = threading.Lock()
_bot_start_time: float | None = None

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
    # 5-20 candles → ATR multiplier = candles × 0.3
    # sl=5 → 1.5×ATR  (live engine default SL)
    # tp=10 → 3.0×ATR (live engine default TP)
    sl_candles: int = 5
    tp_candles: int = 10
    starting_balance: float = 5000.0
    # 1-10 (%) → fraction sent to engine: 0.01–0.10
    risk_per_trade_pct: float = 5.0


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


def _safe_num(v: Any) -> Any:
    """Convert to JSON-safe scalar (handle Inf / NaN)."""
    if not isinstance(v, (int, float)):
        return v
    f = float(v)
    if f != f or f == float("inf") or f == float("-inf"):
        return None
    return round(f, 6)


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/logs")
def get_logs(limit: int = 20) -> dict[str, Any]:
    """Return the last *limit* log entries, newest first."""
    if not LOG_PATH.exists():
        return {"logs": [], "error": "Log file not found – start the bot first."}
    try:
        lines = [
            ln for ln in
            LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip()
        ]
        recent = list(reversed(lines[-limit:]))
        mtime  = os.path.getmtime(LOG_PATH)
        return {
            "logs":        [_parse_log_line(ln) for ln in recent],
            "total_lines": len(lines),
            "file_mtime":  mtime,           # Unix timestamp of last bot write
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/logs/stream")
async def stream_logs():
    """SSE endpoint — pushes new log lines to the browser the instant they appear.
    The frontend connects once; new lines are streamed with zero polling delay.
    """
    async def generator():
        # ── send keepalive so browser knows we're alive ──
        yield "retry: 3000\n\n"          # tell browser: reconnect after 3s on drop

        if not LOG_PATH.exists():
            yield f"data: {_json.dumps({'status': 'no_file'})}\n\n"
            return

        # Seek to END of file — we only push NEW lines from here on
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as fh:
            fh.seek(0, 2)
            while True:
                line = fh.readline()
                if line and line.strip():
                    parsed = _parse_log_line(line.strip())
                    yield f"data: {_json.dumps(parsed)}\n\n"
                else:
                    # No new line yet — yield a comment ping and wait
                    yield ": ping\n\n"
                    await asyncio.sleep(0.5)   # check file twice per second

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering": "no",    # disable nginx buffering if proxied
            "Connection":       "keep-alive",
        },
    )


@app.get("/trades")
def get_trades() -> dict[str, Any]:
    """Return all trades from the last backtest CSV, newest first."""
    if not TRADES_CSV_PATH.exists():
        return {"trades": [], "count": 0}
    try:
        df = pd.read_csv(TRADES_CSV_PATH).fillna("")
        sort_col = next((c for c in ("exit_timestamp", "entry_timestamp", "timestamp") if c in df.columns), None)
        if sort_col:
            df = df.sort_values(sort_col, ascending=False)
        # Stringify timestamp columns
        for col in ("timestamp", "entry_timestamp", "exit_timestamp"):
            if col in df.columns:
                df[col] = df[col].astype(str).str[:19]
        return {"trades": df.to_dict(orient="records"), "count": len(df)}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/backtest")
def run_backtest_endpoint(req: BacktestRequest) -> dict[str, Any]:
    """
    Run the XAUUSD backtest via the existing engine.

    SL/TP candle sliders (5-20) map to ATR multipliers:
      candles × 0.3  →  5→1.5×ATR, 10→3.0×ATR, 20→6.0×ATR
    """
    if not _backtest_lock.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="A backtest is already running. Please wait.")

    try:
        import backtest_forex_engine as engine  # noqa: PLC0415

        # ── Map slider → ATR multiplier ──────────────────────────────────────
        sl_mult = req.sl_candles * 0.3   # 5→1.5,  10→3.0,  20→6.0
        tp_mult = req.tp_candles * 0.3   # 5→1.5,  10→3.0,  20→6.0

        LOGGER.info(
            "Backtest start  sl=%s→%.2f×ATR  tp=%s→%.2f×ATR  range=[%s → %s]",
            req.sl_candles, sl_mult, req.tp_candles, tp_mult,
            req.start_date, req.end_date,
        )

        # ── Resolve date range ────────────────────────────────────────────────
        now_utc = pd.Timestamp.now(tz="UTC")
        today   = now_utc.normalize()
        start_utc = engine.parse_date_utc(req.start_date) if req.start_date else (today - pd.Timedelta(days=180))
        end_utc   = engine.parse_date_utc(req.end_date, inclusive_end=True) if req.end_date else today
        # Cap end_utc to yesterday 23:59:59 UTC.
        # OANDA rejects any "to" timestamp that falls on today's date (even
        # midnight-of-today) because the current trading day is still open.
        # Using yesterday's final second guarantees every requested window
        # contains only fully-closed candles and the API never returns 400.
        yesterday_end = today.normalize() - pd.Timedelta(seconds=1)
        end_utc = min(end_utc, yesterday_end)

        if end_utc <= start_utc:
            return {"error": "End date must be after start date.", "metrics": {}, "trades": [], "equity_curve": []}

        # ── Temporarily patch module-level SL / TP constants ─────────────────
        orig_sl = engine.STOP_LOSS_ATR_MULTIPLIER
        orig_tp = engine.TAKE_PROFIT_ATR_MULTIPLIER
        engine.STOP_LOSS_ATR_MULTIPLIER = sl_mult
        engine.TAKE_PROFIT_ATR_MULTIPLIER = tp_mult

        risk_fraction = max(0.01, min(0.10, req.risk_per_trade_pct / 100.0))

        try:
            trades_df, metrics = engine.run_backtest(
                start_utc=start_utc,
                end_utc=end_utc,
                starting_balance=req.starting_balance,
                risk_per_trade=risk_fraction,
            )
        finally:
            # Always restore originals even if backtest throws
            engine.STOP_LOSS_ATR_MULTIPLIER = orig_sl
            engine.TAKE_PROFIT_ATR_MULTIPLIER = orig_tp

        # ── Build equity curve from balance column ────────────────────────────
        equity_curve: list[dict[str, Any]] = []
        if not trades_df.empty and "equity_after" in trades_df.columns:
            ts_col = next((c for c in ("exit_timestamp", "entry_timestamp", "timestamp") if c in trades_df.columns), None)
            for i, row in trades_df.reset_index(drop=True).iterrows():
                equity_curve.append({
                    "trade": int(i) + 1,
                    "equity": round(float(row["equity_after"]), 2),
                    "ts": str(row[ts_col])[:10] if ts_col else str(i),
                })

        # ── Serialise trades ─────────────────────────────────────────────────
        trades_out: list[dict[str, Any]] = []
        if not trades_df.empty:
            for row in trades_df.fillna("").to_dict(orient="records"):
                for k in ("timestamp", "entry_timestamp", "exit_timestamp"):
                    if k in row and not isinstance(row[k], str):
                        row[k] = str(row[k])[:19]
                trades_out.append(row)

        # ── Serialise metrics ────────────────────────────────────────────────
        safe_metrics = {k: _safe_num(v) for k, v in metrics.items()}

        LOGGER.info(
            "Backtest complete  trades=%s  win_rate=%.1f%%  ending_balance=$%.2f",
            safe_metrics.get("total_trades", 0),
            safe_metrics.get("win_rate", 0.0) or 0.0,
            safe_metrics.get("ending_balance", 0.0) or 0.0,
        )
        return {"metrics": safe_metrics, "trades": trades_out, "equity_curve": equity_curve}

    except Exception as exc:
        LOGGER.exception("Backtest failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        _backtest_lock.release()


# ── Bot Control Endpoints ─────────────────────────────────────────────────────
@app.post("/bot/start")
def bot_start() -> dict[str, Any]:
    """Launch main.py as a subprocess. Idempotent — does nothing if already running."""
    global _bot_process, _bot_start_time
    with _bot_lock:
        # Check if already alive
        if _bot_process is not None and _bot_process.poll() is None:
            return {"status": "already_running", "pid": _bot_process.pid}
        main_py = ROOT / "main.py"
        if not main_py.exists():
            raise HTTPException(status_code=500, detail="main.py not found in bot directory.")
        try:
            _bot_process = subprocess.Popen(
                [sys.executable, str(main_py)],
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            _bot_start_time = time.time()
            LOGGER.info("[BOT] started pid=%s", _bot_process.pid)
            return {"status": "started", "pid": _bot_process.pid}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to start bot: {exc}") from exc


@app.post("/bot/stop")
def bot_stop() -> dict[str, Any]:
    """Gracefully terminate the bot subprocess."""
    global _bot_process, _bot_start_time
    with _bot_lock:
        if _bot_process is None or _bot_process.poll() is not None:
            _bot_process = None
            _bot_start_time = None
            return {"status": "not_running"}
        pid = _bot_process.pid
        try:
            _bot_process.terminate()
            try:
                _bot_process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                _bot_process.kill()
                _bot_process.wait(timeout=3)
        except Exception as exc:
            LOGGER.warning("[BOT] stop error pid=%s: %s", pid, exc)
        _bot_process = None
        _bot_start_time = None
        LOGGER.info("[BOT] stopped pid=%s", pid)
        return {"status": "stopped", "pid": pid}


@app.get("/bot/status")
def bot_status() -> dict[str, Any]:
    """Return bot running state + log freshness for the dashboard indicator."""
    global _bot_process, _bot_start_time
    with _bot_lock:
        running = _bot_process is not None and _bot_process.poll() is None
        pid = _bot_process.pid if running else None
        # If process was started but exited by itself, clean up
        if _bot_process is not None and not running:
            exit_code = _bot_process.poll()
            _bot_process = None
            _bot_start_time = None
            LOGGER.info("[BOT] process exited exit_code=%s", exit_code)

    log_age_seconds: float | None = None
    log_mtime: float | None = None
    if LOG_PATH.exists():
        log_mtime = os.path.getmtime(LOG_PATH)
        log_age_seconds = round(time.time() - log_mtime, 1)

    uptime_seconds: float | None = None
    if running and _bot_start_time is not None:
        uptime_seconds = round(time.time() - _bot_start_time, 0)

    return {
        "running": running,
        "pid": pid,
        "uptime_seconds": uptime_seconds,
        "log_age_seconds": log_age_seconds,
        "log_mtime": log_mtime,
    }


# ── Static files (React SPA) ──────────────────────────────────────────────────
# Serve index.html with no-cache headers so browser always gets the latest build
from fastapi.responses import FileResponse

@app.get("/", include_in_schema=False)
def serve_index():
    return FileResponse(
        str(STATIC_DIR / "index.html"),
        media_type="text/html",
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
        },
    )

app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    LOGGER.info("Starting SEAN0-ALGO dashboard on http://localhost:8000")
    uvicorn.run("web_server:app", host="0.0.0.0", port=8000, reload=False)
