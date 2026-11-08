"""JSON state file for the single-file bots (VWAP+ST, Gold MACD, crypto RSI EMA).

Before 2026-10-09 these files held one line — the last evaluated bar — and the
risk counters (daily cap, cooldown) lived only in memory, so every restart reset
them (BTC hit 8/8 on 2026-10-08, restarted, and could have fired again). The file
now carries both. Reading the legacy one-line format still works.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd


def load_state(path: Path) -> dict[str, Any]:
    try:
        raw = Path(path).read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return {}
    except Exception:  # noqa: BLE001
        return {}
    if not raw:
        return {}
    if raw.startswith("{"):
        try:
            d = json.loads(raw)
            return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    return {"last_signal_ts": raw}  # legacy: just the bar timestamp


def save_state(path: Path, state: dict[str, Any]) -> None:
    """Atomic write (tmp + rename) so a crash mid-write never leaves a torn file."""
    p = Path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(state, default=str), encoding="utf-8")
    os.replace(tmp, p)


def risk_load(state: dict[str, Any]) -> dict[str, Any]:
    """Restore the in-memory risk dict {day, count, last_fired_ts} from a state dict."""
    day_raw = state.get("risk_day")
    ts_raw = state.get("risk_last_fired_ts")
    day = None
    if day_raw:
        try:
            day = dt.date.fromisoformat(str(day_raw)[:10])
        except Exception:  # noqa: BLE001
            day = None
    ts = None
    if ts_raw:
        try:
            ts = pd.Timestamp(ts_raw)
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        except Exception:  # noqa: BLE001
            ts = None
    try:
        count = int(state.get("risk_count") or 0)
    except Exception:  # noqa: BLE001
        count = 0
    return {"day": day, "count": count, "last_fired_ts": ts}


def risk_dump(risk: dict[str, Any]) -> dict[str, Any]:
    day = risk.get("day")
    ts = risk.get("last_fired_ts")
    return {
        "risk_day": day.isoformat() if day else None,
        "risk_count": int(risk.get("count") or 0),
        "risk_last_fired_ts": (str(pd.Timestamp(ts))[:19] if ts is not None else None),
    }
