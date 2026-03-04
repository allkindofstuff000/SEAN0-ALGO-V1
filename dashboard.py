from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates


BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "state.json"
SIGNALS_PATH = BASE_DIR / "signals.csv"
PERFORMANCE_PATH = BASE_DIR / "performance.csv"
CONFIG_PATH = BASE_DIR / "config.json"
LOSS_STREAK_PATH = BASE_DIR / "loss_streak.json"

DEFAULT_STATE: dict[str, Any] = {
    "status": "unknown",
    "last_cycle": None,
    "last_signal_time": None,
}
DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": True,
    "max_signals_per_day": 2,
    "cooldown_candles": 5,
    "strict_mode": True,
}
DEFAULT_LOSS: dict[str, Any] = {"consecutive_losses": 0, "updated_at_bdt": None}


class FileLock:
    """Simple cross-process lock using lock files."""

    def __init__(self, target: Path, timeout_seconds: float = 5.0, retry_seconds: float = 0.05) -> None:
        self.lock_path = target.with_suffix(target.suffix + ".lock")
        self.timeout_seconds = timeout_seconds
        self.retry_seconds = retry_seconds
        self._fd: int | None = None

    def __enter__(self) -> "FileLock":
        start = time.monotonic()
        while True:
            try:
                self._fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
                return self
            except FileExistsError:
                if time.monotonic() - start >= self.timeout_seconds:
                    raise TimeoutError(f"Timed out acquiring lock: {self.lock_path}")
                time.sleep(self.retry_seconds)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        if self.lock_path.exists():
            self.lock_path.unlink(missing_ok=True)


app = FastAPI(title="Trading Signal Dashboard", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def now_bdt_string() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def read_json_with_fallback(path: Path, default: dict[str, Any], warnings: list[str]) -> dict[str, Any]:
    if not path.exists():
        warnings.append(f"{path.name} missing. Using defaults.")
        return dict(default)

    try:
        with FileLock(path):
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        if not isinstance(data, dict):
            warnings.append(f"{path.name} is not a JSON object. Using defaults.")
            return dict(default)
        merged = dict(default)
        merged.update(data)
        return merged
    except Exception as error:
        warnings.append(f"Failed reading {path.name}: {error}")
        return dict(default)


def write_json_locked(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path):
        with NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
            json.dump(payload, tmp, indent=2)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)


def read_csv_rows(path: Path, limit: int | None = None) -> list[dict[str, str]]:
    if not path.exists():
        return []

    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    if limit is not None:
        return rows[-limit:]
    return rows


def read_overall_performance() -> dict[str, Any]:
    rows = read_csv_rows(PERFORMANCE_PATH)
    if not rows:
        return {
            "win_rate_percent": 0.0,
            "total_signals": 0,
            "wins": 0,
            "losses": 0,
            "consecutive_losses": 0,
        }

    overall = next((row for row in rows if row.get("scope") == "OVERALL"), rows[-1])
    return {
        "win_rate_percent": overall.get("win_rate_percent", 0),
        "total_signals": overall.get("total_signals", 0),
        "wins": overall.get("wins", 0),
        "losses": overall.get("losses", 0),
        "consecutive_losses": overall.get("consecutive_losses", 0),
    }


@app.get("/")
async def home(request: Request):
    warnings: list[str] = []
    state = read_json_with_fallback(STATE_PATH, DEFAULT_STATE, warnings)
    config = read_json_with_fallback(CONFIG_PATH, DEFAULT_CONFIG, warnings)
    loss = read_json_with_fallback(LOSS_STREAK_PATH, DEFAULT_LOSS, warnings)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "state": state,
            "config": config,
            "loss": loss,
            "warnings": warnings,
            "timestamp": now_bdt_string(),
        },
    )


@app.get("/performance")
async def performance(request: Request):
    warnings: list[str] = []
    perf = read_overall_performance()
    if not PERFORMANCE_PATH.exists():
        warnings.append("performance.csv missing. Showing defaults.")
    loss = read_json_with_fallback(LOSS_STREAK_PATH, DEFAULT_LOSS, warnings)
    return templates.TemplateResponse(
        "performance.html",
        {
            "request": request,
            "perf": perf,
            "loss": loss,
            "warnings": warnings,
            "timestamp": now_bdt_string(),
        },
    )


@app.get("/signals")
async def signals(request: Request):
    warnings: list[str] = []
    rows = read_csv_rows(SIGNALS_PATH, limit=20)
    if not SIGNALS_PATH.exists():
        warnings.append("signals.csv missing. No signals to display.")
    return templates.TemplateResponse(
        "signals.html",
        {"request": request, "signals": list(reversed(rows)), "warnings": warnings, "timestamp": now_bdt_string()},
    )


@app.post("/toggle")
async def toggle_bot():
    warnings: list[str] = []
    config = read_json_with_fallback(CONFIG_PATH, DEFAULT_CONFIG, warnings)
    config["enabled"] = not bool(config.get("enabled", True))
    write_json_locked(CONFIG_PATH, config)
    return RedirectResponse(url="/", status_code=303)


@app.post("/reset-loss")
async def reset_loss():
    warnings: list[str] = []
    loss = read_json_with_fallback(LOSS_STREAK_PATH, DEFAULT_LOSS, warnings)
    loss["consecutive_losses"] = 0
    loss["updated_at_bdt"] = now_bdt_string()
    write_json_locked(LOSS_STREAK_PATH, loss)
    return RedirectResponse(url="/performance", status_code=303)


@app.post("/update-config")
async def update_config(
    max_signals_per_day: int = Form(...),
    cooldown_candles: int = Form(...),
    strict_mode: str = Form(...),
):
    warnings: list[str] = []
    config = read_json_with_fallback(CONFIG_PATH, DEFAULT_CONFIG, warnings)

    config["max_signals_per_day"] = max(1, int(max_signals_per_day))
    config["cooldown_candles"] = max(1, int(cooldown_candles))
    config["strict_mode"] = strict_mode.strip().lower() == "true"
    write_json_locked(CONFIG_PATH, config)
    return RedirectResponse(url="/", status_code=303)
