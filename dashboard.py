from __future__ import annotations

import csv
from typing import Any

from fastapi import FastAPI, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from storage import (
    BASE_DIR,
    CONFIG_PATH,
    DEFAULT_CONFIG,
    DEFAULT_LOSS,
    DEFAULT_STATE,
    LOSS_STREAK_PATH,
    PERFORMANCE_PATH,
    SIGNALS_PATH,
    STATE_PATH,
    load_config,
    now_bdt_string,
    read_json_with_fallback,
    save_config,
    write_json_locked,
)


app = FastAPI(title="Trading Signal Dashboard", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def read_csv_rows(path, limit: int | None = None) -> list[dict[str, str]]:
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


def _toggle_enabled_config() -> dict[str, Any]:
    config = load_config()
    config["enabled"] = not bool(config.get("enabled", True))
    save_config(config)
    return config


def _apply_config_updates(
    *,
    max_signals_per_day: int,
    cooldown_candles: int,
    strict_mode: str,
) -> dict[str, Any]:
    config = load_config()
    config["max_signals_per_day"] = max(1, int(max_signals_per_day))
    config["cooldown_candles"] = max(1, int(cooldown_candles))
    config["strict_mode"] = strict_mode.strip().lower() == "true"
    save_config(config)
    return config


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
    config = read_json_with_fallback(CONFIG_PATH, DEFAULT_CONFIG, warnings)
    if not PERFORMANCE_PATH.exists():
        warnings.append("performance.csv missing. Showing defaults.")
    loss = read_json_with_fallback(LOSS_STREAK_PATH, DEFAULT_LOSS, warnings)
    return templates.TemplateResponse(
        "performance.html",
        {
            "request": request,
            "perf": perf,
            "config": config,
            "loss": loss,
            "warnings": warnings,
            "timestamp": now_bdt_string(),
        },
    )


@app.get("/signals")
async def signals(request: Request):
    warnings: list[str] = []
    rows = read_csv_rows(SIGNALS_PATH, limit=20)
    config = read_json_with_fallback(CONFIG_PATH, DEFAULT_CONFIG, warnings)
    if not SIGNALS_PATH.exists():
        warnings.append("signals.csv missing. No signals to display.")
    return templates.TemplateResponse(
        "signals.html",
        {
            "request": request,
            "signals": list(reversed(rows)),
            "config": config,
            "warnings": warnings,
            "timestamp": now_bdt_string(),
        },
    )


@app.post("/toggle")
async def toggle_bot():
    _toggle_enabled_config()
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
    _apply_config_updates(
        max_signals_per_day=max_signals_per_day,
        cooldown_candles=cooldown_candles,
        strict_mode=strict_mode,
    )
    return RedirectResponse(url="/", status_code=303)


@app.get("/api/state")
async def api_state():
    warnings: list[str] = []
    state = read_json_with_fallback(STATE_PATH, DEFAULT_STATE, warnings)
    config = read_json_with_fallback(CONFIG_PATH, DEFAULT_CONFIG, warnings)
    loss = read_json_with_fallback(LOSS_STREAK_PATH, DEFAULT_LOSS, warnings)
    return {
        "state": state,
        "config": config,
        "loss": loss,
        "timestamp": now_bdt_string(),
        "warnings": warnings,
    }


@app.get("/api/signals")
async def api_signals(limit: int = Query(20, ge=1, le=200)):
    rows = read_csv_rows(SIGNALS_PATH, limit=limit)
    return {
        "signals": list(reversed(rows)),
        "limit": limit,
        "timestamp": now_bdt_string(),
    }


@app.post("/api/toggle")
async def api_toggle():
    config = _toggle_enabled_config()
    return {
        "ok": True,
        "config": config,
        "timestamp": now_bdt_string(),
    }


@app.post("/api/update-config")
async def api_update_config(
    max_signals_per_day: int = Form(...),
    cooldown_candles: int = Form(...),
    strict_mode: str = Form(...),
):
    config = _apply_config_updates(
        max_signals_per_day=max_signals_per_day,
        cooldown_candles=cooldown_candles,
        strict_mode=strict_mode,
    )
    return {
        "ok": True,
        "config": config,
        "timestamp": now_bdt_string(),
    }
