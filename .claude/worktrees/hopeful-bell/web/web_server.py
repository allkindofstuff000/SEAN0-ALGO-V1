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
from fastapi.responses import StreamingResponse
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
        load_live_signals,
        update_signal_outcome,
    )
    _MONGO_AVAILABLE = True
except Exception:
    _MONGO_AVAILABLE = False
    def save_backtest_report(**_):       return None
    def load_backtest_history(**_):      return []
    def load_backtest_report(_):         return None
    def save_bot_state(_):               return False
    def load_bot_state():                return None
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

# Prevent two backtest runs overlapping
_backtest_lock = threading.Lock()

# ── Bot process management ────────────────────────────────────────────────────
_bot_process: subprocess.Popen | None = None
_bot_lock = threading.Lock()
_bot_start_time: float | None = None
BOT_SERVICE_NAME = os.getenv("BOT_SERVICE_NAME", "").strip()
SYSTEMCTL_PATH = shutil.which("systemctl") or "/bin/systemctl"

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


def _use_systemd_bot() -> bool:
    return bool(BOT_SERVICE_NAME and SYSTEMCTL_PATH)


def _run_systemctl(action: str, extra_args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    if not BOT_SERVICE_NAME:
        raise RuntimeError("BOT_SERVICE_NAME is not configured.")

    base_cmd = [SYSTEMCTL_PATH, action]
    if extra_args:
        base_cmd.extend(extra_args)
    base_cmd.append(BOT_SERVICE_NAME)

    if os.geteuid() == 0:
        command = base_cmd
    elif shutil.which("sudo"):
        command = ["sudo", *base_cmd]
    else:
        command = base_cmd

    return subprocess.run(command, capture_output=True, text=True, check=False)


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
    count = min(max(count, 10), 500)
    gran = granularity.upper()

    # Serve from engine cache if available and populated
    if _stream_engine is not None:
        cached = _stream_engine.store.get_candles(gran)
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
                if tf == "ALL":
                    init_payload = {t: _stream_engine.store.get_candles(t) for t in _TF_MAP}
                else:
                    init_payload = {tf: _stream_engine.store.get_candles(tf)}
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
    port = int(os.getenv("PORT", "8000"))
    LOGGER.info("Starting SEAN0-ALGO dashboard on http://0.0.0.0:%s", port)
    uvicorn.run("web.web_server:app", host="0.0.0.0", port=port, reload=False)
