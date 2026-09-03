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
import shutil
import signal
import ssl as _ssl
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

import pandas as pd
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# MongoDB persistence (non-fatal if unavailable)
try:
    from core.mongo_store import (
        save_backtest_report,
        load_backtest_history,
        load_backtest_report,
        save_bot_state,
        load_bot_state,
        save_strategy_state,
        load_strategy_state,
        load_strategy_started_at,
        load_live_signals,
        update_signal_outcome,
    )
    _MONGO_AVAILABLE = True
except Exception as _mongo_exc:
    _MONGO_AVAILABLE = False
    import logging as _log; _log.getLogger("dashboard").warning("[MONGO] import failed: %s", _mongo_exc)
    def save_backtest_report(**_):       return None
    def load_backtest_history(**_):      return []
    def load_backtest_report(_):         return None
    def save_bot_state(_):               return False
    def load_bot_state():                return None
    def save_strategy_state(*_):         return False
    def load_strategy_state(_):          return None
    def load_strategy_started_at(_):     return None
    def load_live_signals(**_):          return []
    def update_signal_outcome(*_, **__): return False

# ── Boot ──────────────────────────────────────────────────────────────────────
WEB_DIR = Path(__file__).resolve().parent
ROOT = WEB_DIR.parent
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

# Candle engine (non-fatal if import fails)
try:
    from core.candle_engine import OandaStreamEngine, TIMEFRAMES as _TF_MAP
    _ENGINE_AVAILABLE = True
except Exception as _eng_exc:
    LOGGER = logging.getLogger("dashboard")
    logging.basicConfig(level=logging.INFO)
    LOGGER.warning("[ENGINE] import failed: %s", _eng_exc)
    _ENGINE_AVAILABLE = False

_stream_engine: "OandaStreamEngine | None" = None

# ── VWAP + Supertrend strategy (backtest-only for now; live bot deploys later) ─
try:
    from strategies.vwap_supertrend import run_backtest as _vwap_st_run_backtest
    _VWAP_ST_AVAILABLE = True
except Exception as _vwap_st_exc:
    _VWAP_ST_AVAILABLE = False
    logging.getLogger("dashboard").warning("[VWAP-ST] import failed: %s", _vwap_st_exc)

# ── BTC RSI EMA strategy (Binance data mirror; reuses the XAU RSI EMA engine) ──
try:
    from strategies.rsi_btc.backtester import run_backtest as _btc_run_backtest
    from core.btc_fetcher import BtcFetcher as _BtcFetcher
    _btc_fetcher = _BtcFetcher()
    _BTC_AVAILABLE = True
except Exception as _btc_exc:
    _BTC_AVAILABLE = False
    _btc_fetcher = None
    logging.getLogger("dashboard").warning("[BTC] import failed: %s", _btc_exc)

# ── Binance ETH engine (isolated — does NOT touch OANDA code) ─────────────────
try:
    from core.binance_engine import BinanceCandleEngine
    _BINANCE_AVAILABLE = True
except Exception as _bin_exc:
    _BINANCE_AVAILABLE = False
    logging.getLogger("dashboard").warning("[BINANCE] import failed: %s", _bin_exc)

_binance_engine: "BinanceCandleEngine | None" = None

# ── ETH strategies ────────────────────────────────────────────────────────────
try:
    from strategies.rsi_eth import RsiEthSignal as _RsiEthSignal
    _RSI_ETH_AVAILABLE = True
except Exception:
    _RSI_ETH_AVAILABLE = False

_rsi_eth: "_RsiEthSignal | None" = None

# Live-bot decision log (written by main.py)
LOG_PATH = ROOT / "logs" / "decision_trace.log"
# Backtest trades CSV (written by backtest_forex_engine.run_backtest)
TRADES_CSV_PATH = ROOT / "trades.csv"
STATIC_DIR = WEB_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
LOGGER = logging.getLogger("dashboard")


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


ENABLE_BINANCE_ETH = _env_flag("ENABLE_BINANCE_ETH", default=False)


# ── Forex market hours ────────────────────────────────────────────────────────
import datetime as _dt

def is_market_open(now_utc: _dt.datetime | None = None) -> dict[str, Any]:
    """Check if forex market is open. Closed Fri 22:00 UTC → Sun 22:00 UTC."""
    if now_utc is None:
        now_utc = _dt.datetime.now(_dt.timezone.utc)
    wd = now_utc.weekday()  # Mon=0 … Sun=6
    h, m = now_utc.hour, now_utc.minute
    t = h * 60 + m  # minutes since midnight

    # Closed: Friday 22:00 UTC → Sunday 22:00 UTC
    closed = False
    if wd == 4 and t >= 22 * 60:        # Friday after 22:00
        closed = True
    elif wd == 5:                        # Saturday (all day)
        closed = True
    elif wd == 6 and t < 22 * 60:        # Sunday before 22:00
        closed = True

    # Calculate next open time
    next_open = None
    if closed:
        # Next Sunday 22:00 UTC
        days_until_sunday = (6 - wd) % 7
        if days_until_sunday == 0 and t >= 22 * 60:
            days_until_sunday = 7
        next_open_dt = (now_utc + _dt.timedelta(days=days_until_sunday)).replace(
            hour=22, minute=0, second=0, microsecond=0
        )
        next_open = next_open_dt.isoformat()

    return {
        "open": not closed,
        "closed": closed,
        "reason": "Weekend — Forex market closed (Fri 22:00 → Sun 22:00 UTC)" if closed else None,
        "nextOpen": next_open,
    }


# Prevent two backtest runs overlapping
# Single shared lock for ALL XAU backtests (RSI EMA + VWAP+ST). Both endpoints
# temporarily patch the shared engine global DETECTION_LAG_POINTS, so they must
# never run concurrently — otherwise they race on it and can even leak a non-zero
# value into every later backtest. Serialising on one lock removes that entirely.
_backtest_lock = threading.Lock()

# ── Bot process management ────────────────────────────────────────────────────
_bot_process: subprocess.Popen | None = None
_bot_lock = threading.Lock()
_bot_start_time: float | None = None
BOT_SERVICE_NAME = os.getenv("BOT_SERVICE_NAME", "").strip()
VWAP_ST_SERVICE_NAME = os.getenv("VWAP_ST_SERVICE_NAME", "vwap-st").strip()
BTC_RSI_EMA_SERVICE_NAME = os.getenv("BTC_RSI_EMA_SERVICE_NAME", "btc-rsi-ema").strip()
SYSTEMCTL_PATH = shutil.which("systemctl")  # None on Windows / non-systemd hosts

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
    # Detection-lag stress: points of adverse entry slippage (0 = off)
    detection_lag_points: float = 0.0
    # Detection lag (seconds): honest M1-drift fill this many seconds after the
    # signal bar closes (models the 60s live poll). 0 = off (exact next-bar open).
    detection_lag_seconds: float = 0.0


class VwapStBacktestRequest(BaseModel):
    start_date: str | None = None
    end_date: str | None = None
    starting_balance: float = 5_000.0
    risk_per_trade_pct: float = 2.0   # 0.5–5 %
    st_period: int = 10
    st_mult: float = 3.0
    sl_atr: float = 1.5
    tp_atr: float = 3.0
    max_hold_bars: int = 12
    # Detection-lag stress: points of adverse entry slippage (0 = off)
    detection_lag_points: float = 0.0
    # Detection lag (seconds): honest M1-drift fill this many seconds after the
    # signal bar closes (models the 60s live poll). 0 = off (exact next-bar open).
    detection_lag_seconds: float = 0.0


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


def _use_systemd_bot() -> bool:
    return bool(BOT_SERVICE_NAME and SYSTEMCTL_PATH)


def _run_systemctl(action: str, extra_args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    if not BOT_SERVICE_NAME:
        raise RuntimeError("BOT_SERVICE_NAME is not configured.")

    base_cmd = [SYSTEMCTL_PATH, action]
    if extra_args:
        base_cmd.extend(extra_args)
    base_cmd.append(BOT_SERVICE_NAME)

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        command = base_cmd
    elif shutil.which("sudo"):
        command = ["sudo", *base_cmd]
    else:
        command = base_cmd

    return subprocess.run(command, capture_output=True, text=True, check=False)


def _run_systemctl_named(service: str, action: str) -> subprocess.CompletedProcess[str]:
    """systemctl <action> <service> — parallel to _run_systemctl but per-service."""
    if not SYSTEMCTL_PATH or not service:
        raise RuntimeError("systemctl not available or service name blank")
    base_cmd = [SYSTEMCTL_PATH, action, service]
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        command = base_cmd
    elif shutil.which("sudo"):
        command = ["sudo", *base_cmd]
    else:
        command = base_cmd
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _service_status_named(service: str) -> dict[str, Any]:
    """Query systemd show output for <service>. Parallel to _service_status."""
    show = _run_systemctl_named(service, "show")
    if show.returncode != 0:
        detail = (show.stderr or show.stdout).strip()
        raise RuntimeError(detail or f"Unable to read {service} status.")
    values: dict[str, str] = {}
    for line in show.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            values[k] = v.strip()
    active_state = values.get("ActiveState", "inactive")
    pid_raw = values.get("MainPID", "0")
    pid = int(pid_raw) if pid_raw.isdigit() and int(pid_raw) > 0 else None
    running = active_state == "active" and pid is not None
    return {"running": running, "pid": pid, "active_state": active_state}


def _service_status() -> dict[str, Any]:
    show = _run_systemctl("show")
    if show.returncode != 0:
        detail = (show.stderr or show.stdout).strip()
        raise RuntimeError(detail or "Unable to read bot service status.")

    values: dict[str, str] = {}
    for line in show.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip()

    active_state = values.get("ActiveState", "inactive")
    pid_raw = values.get("MainPID", "0")
    pid = int(pid_raw) if pid_raw.isdigit() and int(pid_raw) > 0 else None
    running = active_state == "active" and pid is not None

    uptime_seconds: float | None = None
    if pid is not None:
        elapsed = subprocess.run(
            ["ps", "-o", "etimes=", "-p", str(pid)],
            capture_output=True,
            text=True,
            check=False,
        )
        elapsed_text = elapsed.stdout.strip()
        if elapsed.returncode == 0 and elapsed_text.isdigit():
            uptime_seconds = float(elapsed_text)

    return {
        "running": running,
        "pid": pid,
        "uptime_seconds": uptime_seconds,
    }


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
        from backtests import backtest_forex_engine as engine  # noqa: PLC0415

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

        # ── Temporarily patch module-level SL / TP / detection-lag constants ──
        orig_sl = engine.STOP_LOSS_ATR_MULTIPLIER
        orig_tp = engine.TAKE_PROFIT_ATR_MULTIPLIER
        orig_lag = getattr(engine, "DETECTION_LAG_POINTS", 0.0)
        engine.STOP_LOSS_ATR_MULTIPLIER = sl_mult
        engine.TAKE_PROFIT_ATR_MULTIPLIER = tp_mult
        engine.DETECTION_LAG_POINTS = max(0.0, float(getattr(req, "detection_lag_points", 0.0) or 0.0))

        risk_fraction = max(0.01, min(0.10, req.risk_per_trade_pct / 100.0))
        lag_seconds = max(0.0, min(300.0, float(req.detection_lag_seconds or 0.0)))
        if lag_seconds:
            LOGGER.info("Backtest detection-lag ON: %.0fs (honest M1 fill)", lag_seconds)

        try:
            trades_df, metrics = engine.run_backtest(
                start_utc=start_utc,
                end_utc=end_utc,
                starting_balance=req.starting_balance,
                risk_per_trade=risk_fraction,
                detection_lag_seconds=lag_seconds,
            )
        finally:
            # Always restore originals even if backtest throws
            engine.STOP_LOSS_ATR_MULTIPLIER = orig_sl
            engine.TAKE_PROFIT_ATR_MULTIPLIER = orig_tp
            engine.DETECTION_LAG_POINTS = orig_lag

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

        # ── Persist to MongoDB ────────────────────────────────────────────────
        mongo_id = save_backtest_report(
            metrics=safe_metrics,
            trades=trades_out,
            equity_curve=equity_curve,
            params={
                "start_date":        req.start_date,
                "end_date":          req.end_date,
                "sl_candles":        req.sl_candles,
                "tp_candles":        req.tp_candles,
                "starting_balance":  req.starting_balance,
                "risk_per_trade_pct": req.risk_per_trade_pct,
                "sl_atr_multiplier": sl_mult,
                "tp_atr_multiplier": tp_mult,
                "detection_lag_seconds": lag_seconds,
            },
        )

        return {
            "metrics":      safe_metrics,
            "trades":       trades_out,
            "equity_curve": equity_curve,
            "mongo_id":     mongo_id,
        }

    except Exception as exc:
        LOGGER.exception("Backtest failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        _backtest_lock.release()


# ── Backtest History (MongoDB) ────────────────────────────────────────────────
@app.get("/backtest/history")
def get_backtest_history(limit: int = 50) -> dict[str, Any]:
    """Return the last *limit* backtest summaries from MongoDB (no trade list)."""
    docs = load_backtest_history(limit=min(limit, 50))
    return {"reports": docs, "count": len(docs), "mongo_available": _MONGO_AVAILABLE}


@app.get("/backtest/history/{report_id}")
def get_backtest_report(report_id: str) -> dict[str, Any]:
    """Return a single full backtest report including all trades."""
    doc = load_backtest_report(report_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Report not found.")
    return doc


# ── Live Signal Endpoints ─────────────────────────────────────────────────────
@app.get("/signals")
def get_live_signals(limit: int = 100) -> dict[str, Any]:
    """Return most recent live signals, newest first."""
    docs = load_live_signals(limit=min(limit, 500))
    return {"signals": docs, "count": len(docs)}


class OutcomeRequest(BaseModel):
    outcome: str          # WIN | LOSS | BREAKEVEN
    exit_price: float | None = None
    note: str | None = None


@app.patch("/signals/{signal_id}/outcome")
def mark_signal_outcome(signal_id: str, req: OutcomeRequest) -> dict[str, Any]:
    """Mark a live signal as WIN / LOSS / BREAKEVEN."""
    valid = {"WIN", "LOSS", "BREAKEVEN"}
    outcome = req.outcome.strip().upper()
    if outcome not in valid:
        raise HTTPException(status_code=400, detail=f"outcome must be one of {valid}")
    ok = update_signal_outcome(signal_id, outcome, exit_price=req.exit_price, note=req.note)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to update signal outcome.")
    return {"status": "updated", "signal_id": signal_id, "outcome": outcome}


# ── Bot Control Endpoints ─────────────────────────────────────────────────────
@app.post("/bot/start")
def bot_start() -> dict[str, Any]:
    """Launch main.py as a subprocess. Idempotent — does nothing if already running."""
    global _bot_process, _bot_start_time
    if _use_systemd_bot():
        with _bot_lock:
            status = _service_status()
            if status["running"]:
                return {"status": "already_running", "pid": status["pid"]}
            result = _run_systemctl("start")
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise HTTPException(status_code=500, detail=f"Failed to start bot service: {detail}")
            status = _service_status()
            save_bot_state("running")
            LOGGER.info("[BOT] started via systemd service=%s pid=%s", BOT_SERVICE_NAME, status["pid"])
            return {"status": "started", "pid": status["pid"]}

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
            save_bot_state("running")
            LOGGER.info("[BOT] started pid=%s", _bot_process.pid)
            return {"status": "started", "pid": _bot_process.pid}
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Failed to start bot: {exc}") from exc


@app.post("/bot/stop")
def bot_stop() -> dict[str, Any]:
    """Gracefully terminate the bot subprocess."""
    global _bot_process, _bot_start_time
    if _use_systemd_bot():
        with _bot_lock:
            status = _service_status()
            if not status["running"]:
                return {"status": "not_running"}
            result = _run_systemctl("stop")
            if result.returncode != 0:
                detail = (result.stderr or result.stdout).strip()
                raise HTTPException(status_code=500, detail=f"Failed to stop bot service: {detail}")
            save_bot_state("stopped")
            LOGGER.info("[BOT] stopped via systemd service=%s pid=%s", BOT_SERVICE_NAME, status["pid"])
            return {"status": "stopped", "pid": status["pid"]}

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
        save_bot_state("stopped")
        LOGGER.info("[BOT] stopped pid=%s", pid)
        return {"status": "stopped", "pid": pid}


@app.get("/bot/status")
def bot_status() -> dict[str, Any]:
    """Return bot running state + log freshness for the dashboard indicator."""
    global _bot_process, _bot_start_time
    if _use_systemd_bot():
        with _bot_lock:
            status = _service_status()
            running = status["running"]
            pid = status["pid"]
            uptime_seconds = status["uptime_seconds"]
    else:
        with _bot_lock:
            running = _bot_process is not None and _bot_process.poll() is None
            pid = _bot_process.pid if running else None
            uptime_seconds = None
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

    if not _use_systemd_bot() and running and _bot_start_time is not None:
        uptime_seconds = round(time.time() - _bot_start_time, 0)

    return {
        "running": running,
        "pid": pid,
        "uptime_seconds": uptime_seconds,
        "log_age_seconds": log_age_seconds,
        "log_mtime": log_mtime,
    }


# ── Startup: auto-resume bot if it was running before ────────────────────────
@app.on_event("startup")
async def _init_candle_engine() -> None:
    """Start the multi-TF candle engine on server startup."""
    global _stream_engine
    if not _ENGINE_AVAILABLE:
        LOGGER.warning("[ENGINE] candle engine not available — chart stream disabled")
        return
    try:
        engine = OandaStreamEngine.from_env()
        if not engine.api_key:
            LOGGER.warning("[ENGINE] OANDA_API_KEY not set — stream disabled")
            return
        loop = asyncio.get_event_loop()
        await engine.start(loop)
        _stream_engine = engine
        LOGGER.info("[ENGINE] candle engine started")
    except Exception as exc:
        LOGGER.error("[ENGINE] startup failed: %s", exc)

    # ── Start Binance ETH engine only when explicitly enabled ──
    global _binance_engine, _rsi_eth
    if not ENABLE_BINANCE_ETH:
        LOGGER.info("[BINANCE] ETH engine disabled by config (set ENABLE_BINANCE_ETH=true to enable)")
        _binance_engine = None
        _rsi_eth = None
    elif _BINANCE_AVAILABLE:
        try:
            _binance_engine = BinanceCandleEngine.from_env()
            loop = asyncio.get_event_loop()
            await _binance_engine.start(loop)
            LOGGER.info("[BINANCE] ETH candle engine started")

            # Start RSI EMA ETH strategy
            if _RSI_ETH_AVAILABLE and _binance_engine:
                _rsi_eth = _RsiEthSignal(_binance_engine)
                await _rsi_eth.initialize()
                LOGGER.info("[RSI-ETH] strategy started")

        except Exception as exc:
            LOGGER.error("[BINANCE] startup failed: %s", exc)
            _binance_engine = None


@app.on_event("startup")
async def _auto_resume_bot() -> None:
    """If MongoDB says the bot should be running, start it automatically."""
    try:
        intent = load_bot_state()
        if intent != "running":
            LOGGER.info("[BOT] startup: last intent=%s — not auto-starting", intent)
            return
        LOGGER.info("[BOT] startup: MongoDB intent=running — auto-starting bot")
        if _use_systemd_bot():
            status = _service_status()
            if status["running"]:
                LOGGER.info("[BOT] startup: already running pid=%s", status["pid"])
                return
            result = _run_systemctl("start")
            if result.returncode == 0:
                LOGGER.info("[BOT] startup: auto-start via systemd OK")
            else:
                LOGGER.warning("[BOT] startup: systemctl start failed: %s", (result.stderr or result.stdout).strip())
        else:
            global _bot_process, _bot_start_time
            with _bot_lock:
                if _bot_process is not None and _bot_process.poll() is None:
                    LOGGER.info("[BOT] startup: subprocess already running pid=%s", _bot_process.pid)
                    return
                main_py = ROOT / "main.py"
                if main_py.exists():
                    _bot_process = subprocess.Popen(
                        [sys.executable, str(main_py)],
                        cwd=str(ROOT),
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                    _bot_start_time = time.time()
                    LOGGER.info("[BOT] startup: auto-start subprocess pid=%s", _bot_process.pid)
    except Exception as exc:
        LOGGER.warning("[BOT] startup auto-resume failed: %s", exc)

    # Auto-resume ETH strategies from MongoDB
    try:
        if _rsi_eth is not None:
            rsi_eth_intent = load_strategy_state("rsi-eth")
            if rsi_eth_intent == "running":
                if _rsi_eth._paused:
                    await _rsi_eth.resume()
                    LOGGER.info("[RSI-ETH] auto-resumed from MongoDB state")
            else:
                # Default: start paused — user must click Start
                if _rsi_eth._initialized and not _rsi_eth._paused:
                    _rsi_eth.stop()
                    LOGGER.info("[RSI-ETH] stopped on startup (last intent=%s)", rsi_eth_intent)
    except Exception as exc:
        LOGGER.warning("[ETH] startup auto-resume failed: %s", exc)


# ── Chart API ────────────────────────────────────────────────────────────────
_CHART_GRAN_MAP = {"M1": "M1", "M5": "M5", "M15": "M15", "M30": "M30", "H1": "H1"}


def _chart_oanda_config() -> tuple[str, str]:
    """Return (api_key, base_url_no_slash) for chart OANDA calls."""
    api_key = os.getenv("OANDA_API_KEY", "").strip()
    raw_url = os.getenv("OANDA_API_URL", "").strip()
    env = os.getenv("OANDA_ENV", "practice").strip().lower()
    if raw_url:
        base = raw_url.rstrip("/")
        if "/v3" not in base:
            base += "/v3"
    elif env == "live":
        base = "https://api-fxtrade.oanda.com/v3"
    else:
        base = "https://api-fxpractice.oanda.com/v3"
    return api_key, base


def _fetch_chart_candles(granularity: str, count: int) -> list[dict[str, Any]]:
    """
    Fetch OANDA candles (including the latest incomplete one) for chart display.
    Returns list of {time (unix seconds), open, high, low, close, complete}.
    """
    api_key, base_url = _chart_oanda_config()
    if not api_key:
        raise HTTPException(status_code=500, detail="OANDA_API_KEY not configured.")
    gran = _CHART_GRAN_MAP.get(granularity.upper(), "M5")
    url = f"{base_url}/instruments/XAU_USD/candles?price=M&granularity={gran}&count={count}"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept-Datetime-Format": "RFC3339",
        "User-Agent": "SEAN0-ALGO-V1/1.0",
    }
    ssl_ctx = _ssl.create_default_context()
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise HTTPException(status_code=401, detail="Invalid OANDA API Key.")
        raise HTTPException(status_code=502, detail=f"OANDA error: {exc.code}")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"OANDA unreachable: {exc}")

    candles: list[dict[str, Any]] = []
    for c in payload.get("candles", []):
        mid = c.get("mid") or c.get("bid") or c.get("ask") or {}
        if not mid:
            continue
        ts_unix = int(pd.Timestamp(c["time"]).timestamp())
        candles.append({
            "time":     ts_unix,
            "open":     float(mid.get("o", 0)),
            "high":     float(mid.get("h", 0)),
            "low":      float(mid.get("l", 0)),
            "close":    float(mid.get("c", 0)),
            "complete": bool(c.get("complete", False)),
        })
    return candles


@app.get("/api/candles")
def get_chart_candles(granularity: str = "M5", count: int = 200) -> dict[str, Any]:
    """Historical candles — tries engine cache first, falls back to direct OANDA fetch."""
    count = min(max(count, 10), 12000)
    gran = granularity.upper()

    # Serve from engine cache if available and populated
    if _stream_engine is not None:
        cached = _stream_engine.store.get_all(gran)
        if cached:
            return {"candles": cached[-count:], "granularity": gran, "source": "engine"}

    # Fallback: direct OANDA fetch
    all_candles = _fetch_chart_candles(granularity, count + 2)
    complete = [c for c in all_candles if c["complete"]]
    return {"candles": complete[-count:], "granularity": gran, "source": "direct"}


@app.get("/api/candles/{timeframe}")
def get_chart_candles_tf(timeframe: str, count: int = 200) -> dict[str, Any]:
    """Historical candles for a specific timeframe (M1/M5/M15/H1)."""
    return get_chart_candles(granularity=timeframe, count=count)


@app.get("/api/stream/{timeframe}")
async def stream_candle_updates(timeframe: str) -> StreamingResponse:
    """
    SSE endpoint for real-time candle updates.
    timeframe: M1 | M5 | M15 | H1 | all
    Events: init, tick, candle, status, heartbeat
    """
    tf = timeframe.upper()
    valid = {"M1", "M5", "M15", "H1", "ALL"}
    if tf not in valid:
        raise HTTPException(status_code=400, detail=f"timeframe must be one of {valid}")

    if _stream_engine is None:
        # Engine not available — fall back to polling SSE (original behaviour)
        return await _polling_sse_fallback(timeframe)

    sub_id, queue = _stream_engine.subscribe()

    async def event_gen():
        try:
            # Send init with stored historical candles immediately
            if _stream_engine._history_loaded:
                # Cap the init to the last ~800 bars per TF — the full store is
                # ~2000 bars (7d M5) = a heavy first payload; 800 is plenty and
                # matches the chart's initial fetch.
                if tf == "ALL":
                    init_payload = {t: _stream_engine.store.get_all(t)[-800:] for t in _TF_MAP}
                else:
                    init_payload = {tf: _stream_engine.store.get_all(tf)[-800:]}
                yield f"data: {_json.dumps({'type': 'init', 'candles': init_payload})}\n\n"

            # Also send current stream status
            yield f"data: {_json.dumps({'type': 'status', 'status': _stream_engine.stream_status})}\n\n"

            heartbeat_at = time.time()
            while True:
                try:
                    payload_str = await asyncio.wait_for(queue.get(), timeout=15.0)
                    msg = _json.loads(payload_str)

                    # Filter by requested timeframe
                    if tf != "ALL":
                        msg_tf = msg.get("timeframe")
                        if msg_tf and msg_tf != tf:
                            continue  # skip other TF candle events
                        if msg.get("type") == "tick" and "candles" in msg:
                            # Keep only the requested TF's current candle
                            msg = dict(msg)
                            msg["candles"] = {tf: msg["candles"].get(tf)}

                    yield f"data: {payload_str if tf == 'ALL' else _json.dumps(msg)}\n\n"

                    # Heartbeat every 30s to keep connection alive
                    if time.time() - heartbeat_at > 30:
                        yield f"data: {_json.dumps({'type': 'heartbeat', 'time': time.time()})}\n\n"
                        heartbeat_at = time.time()

                except asyncio.TimeoutError:
                    yield f"data: {_json.dumps({'type': 'heartbeat', 'time': time.time()})}\n\n"
                    heartbeat_at = time.time()

        finally:
            _stream_engine.unsubscribe(sub_id)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


async def _polling_sse_fallback(granularity: str) -> StreamingResponse:
    """Original polling-based SSE when engine is unavailable."""
    gran = _CHART_GRAN_MAP.get(granularity.upper(), "M5")

    async def event_gen():
        while True:
            try:
                candles = await asyncio.to_thread(_fetch_chart_candles, gran, 3)
                for c in candles[-2:]:
                    yield f"data: {_json.dumps({'type': 'tick', 'timeframe': gran, 'candles': {gran: c}})}\n\n"
            except Exception as exc:
                yield f"data: {_json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/stream")
async def stream_chart_candles(granularity: str = "M5") -> StreamingResponse:
    """
    SSE endpoint — pushes the latest 2 candles every 3 seconds so the frontend
    can update the current in-progress candle in real time.
    """
    gran = _CHART_GRAN_MAP.get(granularity.upper(), "M5")

    async def event_gen():
        while True:
            try:
                candles = await asyncio.to_thread(_fetch_chart_candles, gran, 3)
                # Send last complete + latest (possibly incomplete) candle
                for c in candles[-2:]:
                    yield f"data: {_json.dumps(c)}\n\n"
            except Exception as exc:
                yield f"data: {_json.dumps({'error': str(exc)})}\n\n"
            await asyncio.sleep(3)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# LIVE PRICE — lightweight snapshot from the OANDA stream engine
# (replaces the deprecated /api/xau-scalp/status endpoint that page headers
#  used to poll for live XAU/USD price)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/live/price", tags=["live"])
def api_live_price() -> dict[str, Any]:
    """Latest XAU/USD price + timestamp (from the M1 candle stream)."""
    if _stream_engine is None:
        return {"price": None, "time": None, "initialized": False}
    try:
        candles = _stream_engine.store.get_all("M1") or []
        if not candles:
            return {"price": None, "time": None, "initialized": False}
        last = candles[-1]
        return {
            "price": float(last.get("close", 0.0)),
            "time": int(last.get("time", 0)),
            "initialized": True,
        }
    except Exception as exc:
        LOGGER.warning("[LIVE_PRICE] error: %s", exc)
        return {"price": None, "time": None, "initialized": False}


# ─────────────────────────────────────────────────────────────────────────────
# VWAP + SUPERTREND STRATEGY — API Routes
# Backtest only for now. Live bot deploys in a follow-up (separate systemd svc).
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/vwap-st/backtest", tags=["vwap-st"])
def vwap_st_backtest(req: VwapStBacktestRequest) -> dict[str, Any]:
    """Run the VWAP + Supertrend strategy over historical M5 XAUUSD candles."""
    if not _VWAP_ST_AVAILABLE:
        raise HTTPException(status_code=503, detail="VWAP+ST strategy module not available.")
    if not _backtest_lock.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="A backtest is already running. Please wait.")
    try:
        now_utc       = pd.Timestamp.now(tz="UTC")
        today         = now_utc.normalize()
        yesterday_end = today - pd.Timedelta(seconds=1)

        if req.start_date:
            start_utc = pd.Timestamp(req.start_date).tz_localize("UTC")
        else:
            start_utc = today - pd.Timedelta(days=60)

        if req.end_date:
            end_utc = pd.Timestamp(req.end_date).tz_localize("UTC") + pd.Timedelta(days=1)
        else:
            end_utc = yesterday_end
        end_utc = min(end_utc, yesterday_end)

        if end_utc <= start_utc:
            return {"error": "End date must be after start date.", "metrics": {}, "trades": [], "equity_curve": []}

        risk_frac = max(0.005, min(0.05, req.risk_per_trade_pct / 100.0))
        lag_seconds = max(0.0, min(300.0, float(req.detection_lag_seconds or 0.0)))
        if lag_seconds:
            LOGGER.info("VWAP+ST backtest detection-lag ON: %.0fs (honest M1 fill)", lag_seconds)

        # Detection-lag stress (shared engine global, restored after the run)
        from backtests import backtest_forex_engine as _bt_engine  # noqa: PLC0415
        _orig_lag = getattr(_bt_engine, "DETECTION_LAG_POINTS", 0.0)
        _bt_engine.DETECTION_LAG_POINTS = max(0.0, float(getattr(req, "detection_lag_points", 0.0) or 0.0))
        try:
            trades_df, metrics, equity_curve = _vwap_st_run_backtest(
                start_utc         = start_utc,
                end_utc           = end_utc,
                starting_balance  = req.starting_balance,
                risk_per_trade    = risk_frac,
                st_period         = req.st_period,
                st_mult           = req.st_mult,
                sl_atr_multiplier = req.sl_atr,
                tp_atr_multiplier = req.tp_atr,
                max_hold_bars     = req.max_hold_bars,
                detection_lag_seconds = lag_seconds,
            )
        finally:
            _bt_engine.DETECTION_LAG_POINTS = _orig_lag

        trades_out: list[dict[str, Any]] = []
        if not trades_df.empty:
            for row in trades_df.fillna("").to_dict(orient="records"):
                for k in ("timestamp", "entry_timestamp", "exit_timestamp"):
                    if k in row and not isinstance(row[k], str):
                        row[k] = str(row[k])[:19]
                trades_out.append({k: _safe_num(v) if not isinstance(v, str) else v for k, v in row.items()})

        metrics_out = {k: _safe_num(v) for k, v in metrics.items()}

        LOGGER.info(
            "VWAP+ST backtest complete  trades=%s  win_rate=%.1f%%  balance=$%.2f",
            metrics_out.get("total_trades", 0),
            metrics_out.get("win_rate", 0.0) or 0.0,
            metrics_out.get("ending_balance", 0.0) or 0.0,
        )

        mongo_id = save_backtest_report(
            metrics=metrics_out,
            trades=trades_out,
            equity_curve=equity_curve,
            params={
                "strategy":           "vwap-st",
                "start_date":         req.start_date,
                "end_date":           req.end_date,
                "starting_balance":   req.starting_balance,
                "risk_per_trade_pct": req.risk_per_trade_pct,
                "st_period":          req.st_period,
                "st_mult":            req.st_mult,
                "sl_atr":             req.sl_atr,
                "tp_atr":             req.tp_atr,
                "max_hold_bars":      req.max_hold_bars,
                "detection_lag_seconds": lag_seconds,
            },
        )

        return {
            "metrics":      metrics_out,
            "trades":       trades_out,
            "equity_curve": equity_curve,
            "mongo_id":     mongo_id,
        }

    except Exception as exc:
        LOGGER.error("VWAP+ST backtest error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        _backtest_lock.release()


# ── BTC RSI EMA (Binance data mirror) ─────────────────────────────────────────
def _btc_candles_payload(timeframe: str, count: int, *, live_last: bool = True) -> dict[str, Any]:
    count = min(max(int(count), 10), 1500)
    # Live chart display uses Coinbase candles (fast). The newest /candles bar
    # still lags a little, so overlay the real-time /ticker onto the in-progress
    # (last) candle's close/high/low — that's what makes the chart tick live.
    df = _btc_fetcher.fetch_display_candles(timeframe, count + 2)
    candles = [
        {
            "time": int(pd.Timestamp(r["timestamp"]).timestamp()),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "close": float(r["close"]),
            "volume": float(r["volume"]),
            "complete": True,
        }
        for _, r in df.iterrows()
    ]
    if live_last and candles:
        try:
            spot = float(_btc_fetcher.fetch_spot_price().get("price") or 0)
            if spot > 0:
                last = candles[-1]
                last["close"] = spot
                last["high"] = max(last["high"], spot)
                last["low"] = min(last["low"], spot)
        except Exception:  # noqa: BLE001
            pass
    return {"candles": candles[-count:], "granularity": timeframe.upper(), "source": "coinbase"}


@app.get("/api/btc/price", tags=["btc"])
def api_btc_price() -> dict[str, Any]:
    if not _BTC_AVAILABLE or _btc_fetcher is None:
        raise HTTPException(status_code=503, detail="BTC data source unavailable.")
    try:
        snap = _btc_fetcher.fetch_spot_price()  # Coinbase (fast, matches TradingView)
        return {
            "price": float(snap["price"]),
            "time": int(pd.Timestamp(snap["time"]).timestamp()),
            "initialized": True,
            "symbol": "BTCUSD",
        }
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"BTC price fetch failed: {exc}") from exc


@app.get("/api/btc/candles/{timeframe}", tags=["btc"])
def api_btc_candles(timeframe: str, count: int = 300) -> dict[str, Any]:
    if not _BTC_AVAILABLE or _btc_fetcher is None:
        raise HTTPException(status_code=503, detail="BTC data source unavailable.")
    try:
        return _btc_candles_payload(timeframe, count)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"BTC candles fetch failed: {exc}") from exc


@app.get("/api/btc/stream/{timeframe}", tags=["btc"])
async def api_btc_stream(timeframe: str) -> StreamingResponse:
    async def event_gen():
        while True:
            try:
                payload = await asyncio.to_thread(_btc_candles_payload, timeframe, 3)
                for c in payload["candles"][-2:]:
                    yield f"data: {_json.dumps({'type': 'candle', 'candle': c})}\n\n"
            except Exception as exc:  # noqa: BLE001
                yield f"data: {_json.dumps({'type': 'error', 'detail': str(exc)})}\n\n"
            await asyncio.sleep(2)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.post("/api/btc/backtest", tags=["btc"])
def api_btc_backtest(req: BacktestRequest) -> dict[str, Any]:
    """RSI EMA strategy over BTCUSDT M5 (24/7 — the gold session filter is bypassed)."""
    if not _BTC_AVAILABLE:
        raise HTTPException(status_code=503, detail="BTC strategy module not available.")
    if not _backtest_lock.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="A backtest is already running. Please wait.")
    try:
        from backtests import backtest_forex_engine as engine  # noqa: PLC0415

        sl_mult = req.sl_candles * 0.3
        tp_mult = req.tp_candles * 0.3

        now_utc = pd.Timestamp.now(tz="UTC")
        today = now_utc.normalize()
        start_utc = engine.parse_date_utc(req.start_date) if req.start_date else (today - pd.Timedelta(days=30))
        end_utc = engine.parse_date_utc(req.end_date, inclusive_end=True) if req.end_date else today
        end_utc = min(end_utc, now_utc.floor("5min"))  # BTC trades 'today' too (24/7)
        if end_utc <= start_utc:
            return {"error": "End date must be after start date.", "metrics": {}, "trades": [], "equity_curve": []}

        orig_sl = engine.STOP_LOSS_ATR_MULTIPLIER
        orig_tp = engine.TAKE_PROFIT_ATR_MULTIPLIER
        orig_lag = getattr(engine, "DETECTION_LAG_POINTS", 0.0)
        engine.STOP_LOSS_ATR_MULTIPLIER = sl_mult
        engine.TAKE_PROFIT_ATR_MULTIPLIER = tp_mult
        engine.DETECTION_LAG_POINTS = max(0.0, float(getattr(req, "detection_lag_points", 0.0) or 0.0))

        risk_fraction = max(0.01, min(0.10, req.risk_per_trade_pct / 100.0))
        lag_seconds = max(0.0, min(300.0, float(req.detection_lag_seconds or 0.0)))
        try:
            trades_df, metrics, equity_curve = _btc_run_backtest(
                start_utc=start_utc,
                end_utc=end_utc,
                starting_balance=req.starting_balance,
                risk_per_trade=risk_fraction,
                detection_lag_seconds=lag_seconds,
            )
        finally:
            engine.STOP_LOSS_ATR_MULTIPLIER = orig_sl
            engine.TAKE_PROFIT_ATR_MULTIPLIER = orig_tp
            engine.DETECTION_LAG_POINTS = orig_lag

        trades_out: list[dict[str, Any]] = []
        if not trades_df.empty:
            for row in trades_df.fillna("").to_dict(orient="records"):
                for k in ("timestamp", "entry_timestamp", "exit_timestamp"):
                    if k in row and not isinstance(row[k], str):
                        row[k] = str(row[k])[:19]
                trades_out.append({k: _safe_num(v) if not isinstance(v, str) else v for k, v in row.items()})
        metrics_out = {k: _safe_num(v) for k, v in metrics.items()}

        LOGGER.info(
            "BTC backtest complete  trades=%s  win_rate=%.1f%%  balance=$%.2f",
            metrics_out.get("total_trades", 0),
            metrics_out.get("win_rate", 0.0) or 0.0,
            metrics_out.get("ending_balance", 0.0) or 0.0,
        )

        mongo_id = save_backtest_report(
            metrics=metrics_out,
            trades=trades_out,
            equity_curve=equity_curve,
            params={
                "strategy": "rsi-btc",
                "start_date": req.start_date,
                "end_date": req.end_date,
                "sl_candles": req.sl_candles,
                "tp_candles": req.tp_candles,
                "starting_balance": req.starting_balance,
                "risk_per_trade_pct": req.risk_per_trade_pct,
                "sl_atr_multiplier": sl_mult,
                "tp_atr_multiplier": tp_mult,
                "detection_lag_seconds": lag_seconds,
            },
        )

        return {"metrics": metrics_out, "trades": trades_out, "equity_curve": equity_curve, "mongo_id": mongo_id}

    except HTTPException:
        raise
    except Exception as exc:
        LOGGER.error("BTC backtest error: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    finally:
        _backtest_lock.release()


@app.post("/api/bot/btc-rsi-ema/start", tags=["bot"])
def api_btc_bot_start() -> dict[str, Any]:
    """Start the BTC RSI EMA live signal bot (systemctl start btc-rsi-ema.service)."""
    if not SYSTEMCTL_PATH:
        raise HTTPException(status_code=500, detail="systemctl not available on this host")
    result = _run_systemctl_named(BTC_RSI_EMA_SERVICE_NAME, "start")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "systemctl start failed"
        raise HTTPException(status_code=500, detail=detail)
    save_strategy_state("btc-rsi-ema", "running")
    LOGGER.info("[BOT] btc-rsi-ema started")
    return {"status": "started", "message": "BTC RSI EMA live bot started"}


@app.post("/api/bot/btc-rsi-ema/stop", tags=["bot"])
def api_btc_bot_stop() -> dict[str, Any]:
    """Stop the BTC RSI EMA live signal bot (systemctl stop btc-rsi-ema.service)."""
    if not SYSTEMCTL_PATH:
        raise HTTPException(status_code=500, detail="systemctl not available on this host")
    result = _run_systemctl_named(BTC_RSI_EMA_SERVICE_NAME, "stop")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "systemctl stop failed"
        raise HTTPException(status_code=500, detail=detail)
    save_strategy_state("btc-rsi-ema", "stopped")
    LOGGER.info("[BOT] btc-rsi-ema stopped")
    return {"status": "stopped", "message": "BTC RSI EMA live bot stopped"}


# ── Static files (React SPA) ──────────────────────────────────────────────────
# Serve index.html with no-cache headers so browser always gets the latest build
from fastapi.responses import FileResponse

# ── Unified bot status ─────────────────────────────────────────────────────
@app.get("/api/bot/status", tags=["bot"])
def api_bot_status() -> dict[str, Any]:
    """Unified status for all strategy bots."""
    # RSI EMA bot status
    rsi_running = False
    rsi_pid = None
    if _use_systemd_bot():
        try:
            svc = _service_status()
            rsi_running = bool(svc.get("running"))
            rsi_pid = svc.get("pid")
        except Exception as exc:
            LOGGER.warning("[BOT] systemd status lookup failed: %s", exc)
    elif _bot_process is not None and _bot_process.poll() is None:
        rsi_running = True
        rsi_pid = _bot_process.pid

    market = is_market_open()

    # ETH strategies
    rsi_eth_running = _rsi_eth is not None and getattr(_rsi_eth, "_initialized", False) and not getattr(_rsi_eth, "_paused", False)
    rsi_eth_status = _rsi_eth.get_status() if _rsi_eth is not None else {}

    # VWAP + Supertrend live signal bot status
    vwap_running = False
    vwap_pid = None
    if SYSTEMCTL_PATH and VWAP_ST_SERVICE_NAME:
        try:
            svc = _service_status_named(VWAP_ST_SERVICE_NAME)
            vwap_running = bool(svc.get("running"))
            vwap_pid = svc.get("pid")
        except Exception as exc:
            LOGGER.warning("[BOT] vwap-st status lookup failed: %s", exc)

    # BTC RSI EMA live signal bot status
    btc_running = False
    btc_pid = None
    if SYSTEMCTL_PATH and BTC_RSI_EMA_SERVICE_NAME:
        try:
            svc = _service_status_named(BTC_RSI_EMA_SERVICE_NAME)
            btc_running = bool(svc.get("running"))
            btc_pid = svc.get("pid")
        except Exception as exc:
            LOGGER.warning("[BOT] btc-rsi-ema status lookup failed: %s", exc)

    # Load uptime started_at from MongoDB
    rsi_started = load_strategy_started_at("rsi-ema") if rsi_running else None
    eth_started = load_strategy_started_at("rsi-eth") if rsi_eth_running else None
    vwap_started = load_strategy_started_at("vwap-st") if vwap_running else None
    btc_started = load_strategy_started_at("btc-rsi-ema") if btc_running else None

    return {
        "rsiEma": {"running": rsi_running, "pid": rsi_pid, "startedAt": rsi_started},
        "vwapSt": {"running": vwap_running, "pid": vwap_pid, "startedAt": vwap_started},
        "btcRsiEma": {"running": btc_running, "pid": btc_pid, "startedAt": btc_started},
        "rsiEth": {
            "running": rsi_eth_running,
            "paused": getattr(_rsi_eth, "_paused", False) if _rsi_eth else False,
            "startedAt": eth_started,
            "session": rsi_eth_status.get("session"),
            "market_regime": rsi_eth_status.get("market_regime"),
            "regime_details": rsi_eth_status.get("regime_details", {}),
            "strategy_behavior": rsi_eth_status.get("strategy_behavior"),
            "market_open": rsi_eth_status.get("market_open", True),
        },
        "anyRunning": rsi_running or rsi_eth_running or vwap_running or btc_running,
        "market": market,
    }


@app.get("/api/market/status", tags=["market"])
def api_market_status() -> dict[str, Any]:
    """Check if forex market is currently open."""
    return is_market_open()


@app.post("/api/bot/rsi-ema/start", tags=["bot"])
def api_rsi_start() -> dict[str, Any]:
    """Start the RSI EMA bot (alias to /bot/start)."""
    result = bot_start()
    save_strategy_state("rsi-ema", "running")
    return result


@app.post("/api/bot/rsi-ema/stop", tags=["bot"])
def api_rsi_stop() -> dict[str, Any]:
    """Stop the RSI EMA bot (alias to /bot/stop)."""
    result = bot_stop()
    save_strategy_state("rsi-ema", "stopped")
    return result


@app.post("/api/bot/vwap-st/start", tags=["bot"])
def api_vwap_st_start() -> dict[str, Any]:
    """Start the VWAP + Supertrend live signal bot (systemctl start vwap-st.service)."""
    if not SYSTEMCTL_PATH:
        raise HTTPException(status_code=500, detail="systemctl not available on this host")
    result = _run_systemctl_named(VWAP_ST_SERVICE_NAME, "start")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "systemctl start failed"
        raise HTTPException(status_code=500, detail=detail)
    save_strategy_state("vwap-st", "running")
    LOGGER.info("[BOT] vwap-st started")
    return {"status": "started", "message": "VWAP + Supertrend live bot started"}


@app.post("/api/bot/vwap-st/stop", tags=["bot"])
def api_vwap_st_stop() -> dict[str, Any]:
    """Stop the VWAP + Supertrend live signal bot (systemctl stop vwap-st.service)."""
    if not SYSTEMCTL_PATH:
        raise HTTPException(status_code=500, detail="systemctl not available on this host")
    result = _run_systemctl_named(VWAP_ST_SERVICE_NAME, "stop")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or "systemctl stop failed"
        raise HTTPException(status_code=500, detail=detail)
    save_strategy_state("vwap-st", "stopped")
    LOGGER.info("[BOT] vwap-st stopped")
    return {"status": "stopped", "message": "VWAP + Supertrend live bot stopped"}


@app.get("/api/bot/log-stream", tags=["bot"])
async def api_bot_log_stream():
    """Unified SSE log stream merging RSI EMA logs + XAU Scalp decision log."""
    async def event_generator():
        # FIX: Start from end of file — only show NEW logs, not the entire history
        last_log_line = 0
        last_rsi_eth_ts = 0
        first_poll = True
        yield f"data: {_json.dumps({'type':'connected','message':'Unified log stream connected'})}\n\n"
        while True:
            events = []
            # RSI EMA logs from file — only send NEW lines since last poll
            try:
                if LOG_PATH.exists():
                    lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
                    if first_poll:
                        last_log_line = max(0, len(lines) - 5)
                        first_poll = False
                    for line in lines[last_log_line:]:
                        if line.strip():
                            parsed = _parse_log_line(line)
                            msg = parsed.get("message", line)
                            # Try to parse JSON from the message to extract structured fields
                            raw_data = {}
                            try:
                                import json as _jj
                                jd = _jj.loads(msg) if msg.strip().startswith("{") else None
                                if jd:
                                    # RSI EMA logs have two formats:
                                    # 1. Strategy eval: signal_score, direction, session, breakdown, regime_details
                                    # 2. Market snapshot: event_type=market_snapshot, live_price
                                    is_snapshot = jd.get("event_type") == "market_snapshot"
                                    brkdwn = jd.get("breakdown", {})
                                    regime_d = jd.get("regime_details", {})
                                    price = jd.get("live_price") or jd.get("latest_closed_entry_close_price") or jd.get("latest_closed_trend_close_price") or ""
                                    raw_data = {
                                        "decision": jd.get("decision", ""),
                                        "direction": jd.get("direction", ""),
                                        "reason": jd.get("reason", jd.get("event_type", "")),
                                        "live_price": price,
                                        "signal_score": jd.get("signal_score"),
                                        "score_threshold": jd.get("score_threshold", 80),
                                        "symbol": jd.get("symbol", "XAUUSD"),
                                        "session": jd.get("session", ""),
                                        "market_regime": jd.get("market_regime", regime_d.get("regime", "")),
                                        "strategy_behavior": jd.get("strategy_behavior", regime_d.get("strategy_behavior", "")),
                                        "event_type": jd.get("event_type", ""),
                                        "rsi_ok": jd.get("rsi_filter"),
                                        "ema_ok": jd.get("trend_alignment"),
                                        "atr_ok": jd.get("atr_expansion"),
                                        "trend_bias": regime_d.get("trend_bias", jd.get("direction", "")),
                                        "conditions": {
                                            "rsi_ok": jd.get("rsi_filter"),
                                            "trend_alignment": jd.get("trend_alignment"),
                                            "atr_expansion": jd.get("atr_expansion"),
                                            "price_trigger": jd.get("price_trigger"),
                                            "session_filter": jd.get("session_filter"),
                                            "trend_bias": regime_d.get("trend_bias", ""),
                                        },
                                    }
                                    # Build readable message
                                    if jd.get("decision") == "SIGNAL":
                                        msg = f"SIGNAL {jd.get('direction','')} @ {price}"
                                    elif is_snapshot:
                                        msg = f"@ {price}"
                                    else:
                                        score_v = jd.get("signal_score", 0)
                                        thresh = jd.get("score_threshold", 80)
                                        sess = jd.get("session", "")
                                        reason = jd.get("reason", "")
                                        msg = f"{jd.get('decision','')} @ {price} — {reason}" if reason else f"@ {price} score:{score_v}/{thresh}"
                            except Exception:
                                raw_data = {"message": msg}
                            t_str = parsed.get("timestamp", "")
                            if t_str and len(t_str) > 10:
                                t_str = t_str[11:19]  # extract HH:MM:SS
                            events.append({
                                "strategy": "RSI-EMA",
                                "level": "SIGNAL" if raw_data.get("decision") == "SIGNAL" else parsed.get("level", "INFO"),
                                "message": msg,
                                "time": t_str,
                                "raw": raw_data,
                            })
                    last_log_line = len(lines)
            except Exception:
                pass
            # RSI-ETH decision log
            try:
                if _rsi_eth is not None:
                    import datetime as _dt
                    for entry in _rsi_eth.get_decision_log(limit=5):
                        ts = entry.get("ts", 0)
                        if ts > last_rsi_eth_ts:
                            last_rsi_eth_ts = ts
                            t_str = _dt.datetime.utcfromtimestamp(ts / 1000).strftime("%H:%M:%S") if ts else ""
                            conds = entry.get("conditions", {})
                            events.append({
                                "strategy": "ETH-RSI-15",
                                "level": "SIGNAL" if entry.get("decision") == "SIGNAL" else "INFO",
                                "message": f"{entry.get('decision','')} @ {entry.get('price','')} — {entry.get('reason','')}",
                                "time": t_str,
                                "raw": {
                                    "decision": entry.get("decision", ""),
                                    "direction": entry.get("direction", ""),
                                    "reason": entry.get("reason", ""),
                                    "live_price": entry.get("price"),
                                    "signal_score": entry.get("score"),
                                    "score_threshold": 80,
                                    "symbol": "ETHUSDT",
                                    "session": "24/7",
                                    "market_regime": "",
                                    "strategy_behavior": "",
                                    "conditions": conds,
                                },
                            })
            except Exception:
                pass
            for evt in events:
                yield f"data: {_json.dumps(evt)}\n\n"
            await asyncio.sleep(3)
    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/dashboard/live-bot", include_in_schema=False)
def serve_live_bot_dashboard():
    """Live Bot control + unified log page."""
    return FileResponse(
        str(STATIC_DIR / "live_bot.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/dashboard/rsi-ema", include_in_schema=False)
def serve_rsi_ema_dashboard():
    """Dedicated RSI EMA dashboard page."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    scalp_marker = "XAU SCALP STRATEGY WORKSPACE"
    registry_marker = "Strategy Registry"
    scalp_idx = html.find(scalp_marker)
    registry_idx = html.find(registry_marker, scalp_idx if scalp_idx != -1 else 0)
    if scalp_idx != -1 and registry_idx != -1 and scalp_idx < registry_idx:
        block_start = html.rfind("/*", 0, scalp_idx)
        block_end = html.rfind("/*", 0, registry_idx)
        if block_start != -1 and block_end != -1 and block_start < block_end:
            html = html[:block_start] + html[block_end:]

    bootstrap = """
<script>
try {
  localStorage.setItem('activeStrategy', 'rsi-ema');
  if (!localStorage.getItem('strategy:rsi-ema:tab')) {
    localStorage.setItem('strategy:rsi-ema:tab', 'chart');
  }
  localStorage.removeItem('strategy:xau-scalp:tab');
} catch (e) {}
</script>
""".strip()
    html = html.replace("<body>", f"<body>\n{bootstrap}", 1)

    return HTMLResponse(
        content=html,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/dashboard", include_in_schema=False)
def dashboard_redirect():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/dashboard/rsi-ema", status_code=307)


# ── RSI ETH Strategy API ──────────────────────────────────────────────────────

@app.get("/api/rsi-eth/status", tags=["rsi-eth"])
def rsi_eth_status() -> dict[str, Any]:
    if _rsi_eth is None:
        return {"initialized": False, "error": "RSI ETH not available"}
    return _rsi_eth.get_status()

@app.get("/api/rsi-eth/signal/latest", tags=["rsi-eth"])
def rsi_eth_latest():
    if _rsi_eth is None:
        return {"signal": None}
    return {"signal": _rsi_eth.get_latest_signal()}

@app.get("/api/rsi-eth/signals/history", tags=["rsi-eth"])
def rsi_eth_history(limit: int = 20):
    if _rsi_eth is None:
        return {"signals": []}
    return {"signals": _rsi_eth.get_signal_history(min(limit, 100))}

@app.get("/api/rsi-eth/log", tags=["rsi-eth"])
def rsi_eth_log(limit: int = 100):
    if _rsi_eth is None:
        return {"log": []}
    return {"log": _rsi_eth.get_decision_log(min(limit, 100))}

@app.post("/api/rsi-eth/start", tags=["rsi-eth"])
async def rsi_eth_start():
    if _rsi_eth is None:
        raise HTTPException(503, "RSI ETH not available")
    if _rsi_eth._initialized and not _rsi_eth._paused:
        return {"status": "already_running"}
    await _rsi_eth.resume()
    save_strategy_state("rsi-eth", "running")
    return {"status": "started"}

@app.post("/api/rsi-eth/stop", tags=["rsi-eth"])
def rsi_eth_stop():
    if _rsi_eth is None:
        raise HTTPException(503, "RSI ETH not available")
    if _rsi_eth._paused:
        return {"status": "already_stopped"}
    _rsi_eth.stop()
    save_strategy_state("rsi-eth", "stopped")
    return {"status": "stopped"}


# ── ETH Backtesting ───────────────────────────────────────────────────────────

class EthBacktestRequest(BaseModel):
    strategy: str = "rsi-eth"
    start_date: str | None = None
    end_date: str | None = None
    starting_balance: float = 10_000.0
    risk_per_trade_pct: float = 0.75

_eth_backtest_lock = threading.Lock()

@app.post("/api/eth/backtest", tags=["eth"])
def eth_backtest(req: EthBacktestRequest) -> dict[str, Any]:
    """Run RSI-ETH backtest using Binance historical data."""
    if not _eth_backtest_lock.acquire(blocking=False):
        raise HTTPException(429, "ETH backtest already running.")
    try:
        from core.binance_client import fetch_history

        now_utc = pd.Timestamp.now(tz="UTC")
        today = now_utc.normalize()

        if req.start_date:
            start_utc = pd.Timestamp(req.start_date).tz_localize("UTC")
        else:
            start_utc = today - pd.Timedelta(days=30)

        if req.end_date:
            end_utc = pd.Timestamp(req.end_date).tz_localize("UTC") + pd.Timedelta(days=1)
        else:
            end_utc = today

        if end_utc <= start_utc:
            return {"error": "End date must be after start date.", "metrics": {}, "trades": [], "equity_curve": []}

        from_ms = int(start_utc.timestamp() * 1000)
        to_ms = int(end_utc.timestamp() * 1000)
        symbol = os.getenv("ETH_SYMBOL", "ETHUSDT")

        LOGGER.info("[ETH-BT] Fetching %s M15 candles %s → %s", symbol, req.start_date, req.end_date)
        candles = fetch_history(symbol=symbol, interval="15m", from_ms=from_ms, to_ms=to_ms)

        if not candles:
            return {"error": "No candle data returned from Binance.", "metrics": {}, "trades": [], "equity_curve": []}

        risk_frac = max(0.005, min(0.05, req.risk_per_trade_pct / 100.0))

        from strategies.rsi_eth.backtester import run_backtest

        trades, metrics, equity_curve = run_backtest(
            candles=candles,
            starting_balance=req.starting_balance,
            risk_per_trade=risk_frac,
        )

        # Sanitise
        trades_out = [{k: _safe_num(v) for k, v in t.items()} for t in trades]
        metrics_out = {}
        for k, v in metrics.items():
            if isinstance(v, dict):
                metrics_out[k] = v
            else:
                metrics_out[k] = _safe_num(v)

        LOGGER.info("[ETH-BT] %s complete — %d trades, %.1f%% win rate",
                     req.strategy, metrics_out.get("totalTrades", 0), metrics_out.get("winRate", 0))

        mongo_id = save_backtest_report(
            metrics=metrics_out, trades=trades_out, equity_curve=equity_curve,
            params={"strategy": req.strategy, "start_date": req.start_date, "end_date": req.end_date,
                    "starting_balance": req.starting_balance, "risk_per_trade_pct": req.risk_per_trade_pct},
        )

        return {"metrics": metrics_out, "trades": trades_out, "equity_curve": equity_curve, "mongo_id": mongo_id}

    except Exception as exc:
        LOGGER.error("[ETH-BT] error: %s", exc, exc_info=True)
        raise HTTPException(500, str(exc)) from exc
    finally:
        _eth_backtest_lock.release()


# ── ICT Minimal Backtest ──────────────────────────────────────────────────────

_ict_backtest_lock = threading.Lock()
_ict_backtest_cache: dict[str, Any] = {}

@app.post("/api/backtest/eth-ict-minimal/run", tags=["ict-backtest"])
def ict_minimal_run(days: int = 30) -> dict[str, Any]:
    """Run all 6 ICT variants on last N days of ETH/USDT 5M data."""
    if not _ict_backtest_lock.acquire(blocking=False):
        raise HTTPException(429, "ICT backtest already running.")
    try:
        from core.binance_client import fetch_history
        from strategies.ict_eth_minimal.backtester import run_all_variants

        now_ms = int(time.time() * 1000)
        from_ms = now_ms - days * 86400 * 1000

        LOGGER.info("[ICT-BT] Fetching %d days of ETH 5M + 15M data…", days)
        m5 = fetch_history(symbol="ETHUSDT", interval="5m", from_ms=from_ms, to_ms=now_ms)
        m15 = fetch_history(symbol="ETHUSDT", interval="15m", from_ms=from_ms, to_ms=now_ms)
        LOGGER.info("[ICT-BT] Fetched %d 5M + %d 15M candles", len(m5), len(m15))

        if len(m5) < 300:
            return {"error": f"Only {len(m5)} candles — need 300+", "variants": {}, "best": None}

        result = run_all_variants(m5, m15)
        _ict_backtest_cache["last"] = result
        _ict_backtest_cache["ts"] = time.time()

        if result.get("best"):
            b = result["best"]
            LOGGER.info("[ICT-BT] Best: %s (%s) — %.1f%% WR, %.1f trades/day, PF %.2f",
                        b["id"], b["name"], b["winRate"], b["tradesPerDay"], b["profitFactor"])

        # Save to MongoDB
        try:
            save_backtest_report(
                metrics=result.get("best", {}),
                trades=result["best"]["trades"] if result.get("best") else [],
                equity_curve=result["best"]["equityCurve"] if result.get("best") else [],
                params={"strategy": "ict-eth-minimal", "days": days,
                        "bestVariant": result["best"]["id"] if result.get("best") else None},
            )
        except Exception:
            pass

        return result
    except Exception as exc:
        LOGGER.error("[ICT-BT] error: %s", exc, exc_info=True)
        raise HTTPException(500, str(exc)) from exc
    finally:
        _ict_backtest_lock.release()

@app.get("/api/backtest/eth-ict-minimal/last", tags=["ict-backtest"])
def ict_minimal_last() -> dict[str, Any]:
    """Get last cached ICT backtest result."""
    if "last" not in _ict_backtest_cache:
        return {"error": "No results yet — run a backtest first", "variants": {}, "best": None}
    return _ict_backtest_cache["last"]

@app.get("/dashboard/backtest-eth-ict", include_in_schema=False)
def serve_ict_backtest():
    return FileResponse(
        str(STATIC_DIR / "ict_backtest.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )

# ── XAU ICT Minimal Backtest ──────────────────────────────────────────────────

_xau_ict_backtest_lock = threading.Lock()
_xau_ict_backtest_cache: dict[str, Any] = {}

@app.post("/api/backtest/xau-ict-minimal/run", tags=["ict-backtest"])
def xau_ict_minimal_run(days: int = 30) -> dict[str, Any]:
    """Run all 6 ICT variants on last N days of XAU/USD M5 data (OANDA)."""
    if not _xau_ict_backtest_lock.acquire(blocking=False):
        raise HTTPException(429, "XAU ICT backtest already running.")
    try:
        from strategies.ict_xau_minimal.backtester import run_xau_ict_backtest

        LOGGER.info("[XAU-ICT-BT] Running %d-day ICT backtest on XAU/USD…", days)
        result = run_xau_ict_backtest(days=days)
        _xau_ict_backtest_cache["last"] = result
        _xau_ict_backtest_cache["ts"] = time.time()

        if result.get("best"):
            b = result["best"]
            LOGGER.info("[XAU-ICT-BT] Best: %s (%s) — %.1f%% WR, %.1f trades/day",
                        b["id"], b["name"], b["winRate"], b["tradesPerDay"])

        try:
            save_backtest_report(
                metrics=result.get("best", {}),
                trades=result["best"]["trades"] if result.get("best") else [],
                equity_curve=result["best"]["equityCurve"] if result.get("best") else [],
                params={"strategy": "ict-xau-minimal", "days": days,
                        "bestVariant": result["best"]["id"] if result.get("best") else None},
            )
        except Exception:
            pass

        return result
    except Exception as exc:
        LOGGER.error("[XAU-ICT-BT] error: %s", exc, exc_info=True)
        raise HTTPException(500, str(exc)) from exc
    finally:
        _xau_ict_backtest_lock.release()

@app.get("/api/backtest/xau-ict-minimal/last", tags=["ict-backtest"])
def xau_ict_minimal_last() -> dict[str, Any]:
    """Get last cached XAU ICT backtest result."""
    if "last" not in _xau_ict_backtest_cache:
        return {"error": "No results yet — run a backtest first", "variants": {}, "best": None}
    return _xau_ict_backtest_cache["last"]

@app.get("/dashboard/backtest-xau-ict", include_in_schema=False)
def serve_xau_ict_backtest():
    return FileResponse(
        str(STATIC_DIR / "ict_backtest_xau.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )

# ── ETH / Binance API routes ──────────────────────────────────────────────────

@app.get("/api/eth/status", tags=["eth"])
def eth_status() -> dict[str, Any]:
    """ETH engine status."""
    if _binance_engine is None:
        return {"available": False, "error": "Binance engine not running"}
    return {**_binance_engine.get_status(), "available": True}


@app.get("/api/eth/price", tags=["eth"])
def eth_price() -> dict[str, Any]:
    """Current ETH price."""
    if _binance_engine is None:
        return {"price": 0, "time": 0}
    return _binance_engine.get_price()


@app.get("/api/eth/candles/{timeframe}", tags=["eth"])
def eth_candles(timeframe: str, count: int = 200) -> dict[str, Any]:
    """Get ETH candles for a specific timeframe (M1/M5/M15/H1)."""
    tf = timeframe.upper()
    if tf not in ("M1", "M5", "M15", "H1"):
        raise HTTPException(400, "Invalid timeframe. Use: M1, M5, M15, H1")
    if _binance_engine is None:
        return {"candles": [], "timeframe": tf, "error": "Binance engine not running"}
    candles = _binance_engine.store.get_all(tf)
    if count:
        candles = candles[-count:]
    return {"candles": candles, "timeframe": tf, "count": len(candles)}


@app.get("/api/eth/stream/{timeframe}", tags=["eth"])
async def eth_stream(timeframe: str):
    """SSE endpoint for real-time ETH candle updates (same pattern as OANDA stream)."""
    tf = timeframe.upper()
    if tf not in ("M1", "M5", "M15", "H1", "ALL"):
        raise HTTPException(400, "Invalid timeframe")

    if _binance_engine is None:
        async def _error_gen():
            yield f"data: {_json.dumps({'type': 'error', 'detail': 'Binance engine not running'})}\n\n"
        return StreamingResponse(_error_gen(), media_type="text/event-stream")

    sub_id, queue = _binance_engine.subscribe()

    async def event_gen():
        try:
            # Send init with all candle data
            init_payload = {
                "type": "init",
                "symbol": _binance_engine.symbol,
                "candles": {
                    t: _binance_engine.store.get_all(t)
                    for t in ("M1", "M5", "M15", "H1")
                },
            }
            yield f"data: {_json.dumps(init_payload, default=str)}\n\n"

            while True:
                try:
                    raw = await asyncio.wait_for(queue.get(), timeout=30.0)
                    evt = _json.loads(raw)
                    evt_tf = evt.get("timeframe", "")
                    # Filter by requested timeframe (or pass all if ALL)
                    if tf == "ALL" or evt_tf == tf or evt.get("type") in ("init", "status"):
                        yield f"data: {raw}\n\n"
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            _binance_engine.unsubscribe(sub_id)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.get("/dashboard/crypto", include_in_schema=False)
def serve_crypto_dashboard():
    """Crypto (ETH/USDT) dashboard page."""
    return FileResponse(
        str(STATIC_DIR / "crypto.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )


@app.get("/", include_in_schema=False)
def serve_index():
    return FileResponse(
        str(STATIC_DIR / "index.html"),
        media_type="text/html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )

# ── RSI ETH Optimizer (read-only parameter testing) ───────────────────────────
_optimizer_running = False
_optimizer_result = None

@app.post("/api/rsi-eth/optimizer/run")
async def rsi_eth_optimizer_run():
    """Run RSI EMA parameter optimizer on historical ETH data."""
    global _optimizer_running, _optimizer_result
    if _optimizer_running:
        return {"error": "Optimizer already running", "status": "running"}

    _optimizer_running = True
    _optimizer_result = None

    try:
        import asyncio
        loop = asyncio.get_event_loop()

        def _run():
            global _optimizer_result, _optimizer_running
            try:
                from core.binance_client import fetch_history

                LOGGER.info("[OPTIMIZER] Fetching 30 days of ETH candles...")

                now_ms = int(time.time() * 1000)
                days_30_ms = 30 * 24 * 60 * 60 * 1000
                from_ms = now_ms - days_30_ms

                candles_5m = fetch_history("ETHUSDT", "5m", from_ms, now_ms)
                candles_15m = fetch_history("ETHUSDT", "15m", from_ms, now_ms)

                LOGGER.info("[OPTIMIZER] Fetched %d 5M + %d 15M candles. Starting optimization...",
                           len(candles_5m), len(candles_15m))

                from strategies.rsi_eth.optimizer import run_optimizer
                result = run_optimizer(candles_5m, candles_15m)

                _optimizer_result = result
                LOGGER.info("[OPTIMIZER] Complete — %d combos tested in %.1fs, %d meet target",
                           result["totalCombinations"], result["elapsedSeconds"], result["meetsTarget"])
            except Exception as exc:
                LOGGER.error("[OPTIMIZER] error: %s", exc, exc_info=True)
                _optimizer_result = {"error": str(exc)}
            finally:
                _optimizer_running = False

        import threading
        t = threading.Thread(target=_run, daemon=True)
        t.start()

        return {"status": "started", "message": "Optimizer started — fetching 30 days of data..."}

    except Exception as exc:
        _optimizer_running = False
        return {"error": str(exc)}

@app.get("/api/rsi-eth/optimizer/status")
async def rsi_eth_optimizer_status():
    """Get optimizer status and results."""
    if _optimizer_running:
        return {"status": "running"}
    if _optimizer_result is not None:
        return {"status": "complete", "result": _optimizer_result}
    return {"status": "idle"}


# Serve compiled React/Vite assets
app.mount("/assets", StaticFiles(directory=str(STATIC_DIR / "assets")), name="assets")

@app.get("/favicon.svg", include_in_schema=False)
def serve_favicon():
    return FileResponse(str(STATIC_DIR / "favicon.svg"))

# SPA catch-all - serve React index.html for every unmatched route
@app.get("/{full_path:path}", include_in_schema=False)
async def serve_spa(full_path: str):
    return FileResponse(str(STATIC_DIR / "index.html"))


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    LOGGER.info("Starting SEAN0-ALGO dashboard on http://0.0.0.0:%s", port)
    uvicorn.run("web.web_server:app", host="0.0.0.0", port=port, reload=False)
