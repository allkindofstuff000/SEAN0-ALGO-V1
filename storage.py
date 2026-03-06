from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from zoneinfo import ZoneInfo


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
    "updated_at_bdt": None,
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


def now_bdt_string() -> str:
    return datetime.now(ZoneInfo("Asia/Dhaka")).strftime("%Y-%m-%d %H:%M:%S")


def read_json_with_fallback(
    path: Path,
    default: dict[str, Any],
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    if not path.exists():
        if warnings is not None:
            warnings.append(f"{path.name} missing. Using defaults.")
        return dict(default)

    try:
        with FileLock(path):
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        if not isinstance(data, dict):
            if warnings is not None:
                warnings.append(f"{path.name} is not a JSON object. Using defaults.")
            return dict(default)
        merged = dict(default)
        merged.update(data)
        return merged
    except Exception as error:
        if warnings is not None:
            warnings.append(f"Failed reading {path.name}: {error}")
        return dict(default)


def write_json_locked(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(path):
        with NamedTemporaryFile("w", delete=False, dir=str(path.parent), encoding="utf-8") as tmp:
            json.dump(payload, tmp, indent=2)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, path)


def _safe_int(value: object, fallback: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(value))
    except (TypeError, ValueError):
        return max(minimum, int(fallback))


def load_config() -> dict[str, Any]:
    config = read_json_with_fallback(CONFIG_PATH, DEFAULT_CONFIG)
    config["enabled"] = bool(config.get("enabled", True))
    config["max_signals_per_day"] = _safe_int(
        config.get("max_signals_per_day"),
        int(DEFAULT_CONFIG["max_signals_per_day"]),
        minimum=1,
    )
    config["cooldown_candles"] = _safe_int(
        config.get("cooldown_candles"),
        int(DEFAULT_CONFIG["cooldown_candles"]),
        minimum=1,
    )
    config["strict_mode"] = bool(config.get("strict_mode", DEFAULT_CONFIG["strict_mode"]))
    return config


def save_config(config: dict[str, Any]) -> None:
    merged = dict(DEFAULT_CONFIG)
    merged.update(config)
    merged["enabled"] = bool(merged.get("enabled", True))
    merged["max_signals_per_day"] = _safe_int(
        merged.get("max_signals_per_day"),
        int(DEFAULT_CONFIG["max_signals_per_day"]),
        minimum=1,
    )
    merged["cooldown_candles"] = _safe_int(
        merged.get("cooldown_candles"),
        int(DEFAULT_CONFIG["cooldown_candles"]),
        minimum=1,
    )
    merged["strict_mode"] = bool(merged.get("strict_mode", DEFAULT_CONFIG["strict_mode"]))
    write_json_locked(CONFIG_PATH, merged)


def update_state(patch: dict[str, Any]) -> None:
    state = read_json_with_fallback(STATE_PATH, DEFAULT_STATE)
    state.update(patch)
    state["updated_at_bdt"] = now_bdt_string()
    write_json_locked(STATE_PATH, state)


def set_state_status(status: str) -> None:
    update_state({"status": status})


def record_cycle(last_cycle: str, ok: bool, skipped: bool, error: str | None) -> None:
    if error:
        status = "error"
    elif skipped:
        status = "idle"
    elif ok:
        status = "running"
    else:
        status = "blocked"
    update_state(
        {
            "last_cycle": last_cycle,
            "status": status,
            "last_error": error,
        }
    )


def record_last_signal_time(ts: str) -> None:
    update_state({"last_signal_time": ts})
