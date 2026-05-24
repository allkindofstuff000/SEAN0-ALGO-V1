from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRIMARY_LOG_PATH = PROJECT_ROOT / "logs" / "decision_trace.log"
FALLBACK_LOG_PATH = PROJECT_ROOT / "decision_trace.log"


def _resolve_log_path() -> Path:
    if PRIMARY_LOG_PATH.exists():
        return PRIMARY_LOG_PATH
    if FALLBACK_LOG_PATH.exists():
        return FALLBACK_LOG_PATH
    raise FileNotFoundError(
        f"Decision trace log not found at {PRIMARY_LOG_PATH} or {FALLBACK_LOG_PATH}"
    )


def _format_json_entry(payload: dict[str, Any]) -> str:
    timestamp = str(payload.get("timestamp", "unknown-time"))
    symbol = str(payload.get("symbol", "UNKNOWN"))
    session = str(payload.get("session", "UNKNOWN"))
    direction = str(payload.get("direction", "NONE"))
    score = payload.get("signal_score", payload.get("score", 0))
    threshold = payload.get("score_threshold", "?")
    decision = str(payload.get("decision", "unknown"))
    reason = str(payload.get("reason", "n/a"))
    modes = ",".join(str(item) for item in payload.get("signal_modes", []) if item) or "-"
    return (
        f"[{timestamp}] {symbol} session={session} direction={direction} "
        f"score={score}/{threshold} decision={decision} modes={modes} reason={reason}"
    )


def monitor_logs(limit: int = 20) -> dict[str, Any]:
    """Return the last decision log entries in a human-readable format."""

    resolved_path = _resolve_log_path()
    recent_lines: deque[str] = deque(maxlen=max(1, int(limit)))

    with resolved_path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line:
                recent_lines.append(line)

    entries: list[str] = []
    for line in recent_lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            entries.append(line)
            continue

        if isinstance(payload, dict):
            entries.append(_format_json_entry(payload))
        else:
            entries.append(str(payload))

    return {
        "log_path": str(resolved_path),
        "count": len(entries),
        "entries": entries,
    }
