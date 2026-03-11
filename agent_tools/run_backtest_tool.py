from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pandas as pd

try:
    from .analyze_results_tool import analyze_trades
except ImportError:
    from analyze_results_tool import analyze_trades


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKTEST_SCRIPT = PROJECT_ROOT / "backtest_xau_strategy.py"

_METRIC_PATTERNS = {
    "trades": re.compile(r"Total trades:\s*(?P<value>\d+)", re.IGNORECASE),
    "wins": re.compile(r"Wins:\s*(?P<value>\d+)", re.IGNORECASE),
    "losses": re.compile(r"Losses:\s*(?P<value>\d+)", re.IGNORECASE),
    "win_rate": re.compile(r"Win rate:\s*(?P<value>-?\d+(?:\.\d+)?)%", re.IGNORECASE),
    "profit_factor": re.compile(r"Profit factor:\s*(?P<value>-?\d+(?:\.\d+)?)", re.IGNORECASE),
    "avg_R": re.compile(r"Average R:\s*(?P<value>-?\d+(?:\.\d+)?)", re.IGNORECASE),
    "max_drawdown": re.compile(r"Max drawdown:\s*(?P<value>-?\d+(?:\.\d+)?)\s*R", re.IGNORECASE),
    "ending_balance": re.compile(r"Ending balance:\s*\$(?P<value>-?\d+(?:\.\d+)?)", re.IGNORECASE),
}


def _normalize_date(value: str) -> str:
    timestamp = pd.Timestamp(value)
    return timestamp.strftime("%Y-%m-%d")


def _parse_metric_output(stdout: str) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for key, pattern in _METRIC_PATTERNS.items():
        match = pattern.search(stdout)
        if not match:
            continue
        raw_value = match.group("value")
        summary[key] = int(raw_value) if key in {"trades", "wins", "losses"} else float(raw_value)

    if "trades" in summary:
        summary["total_trades"] = int(summary["trades"])
    return summary


def run_backtest(
    start_date: str,
    end_date: str,
    *,
    mode: str = "forex",
    max_hold: int = 12,
    timeout_seconds: int = 1800,
    python_executable: str | None = None,
) -> dict[str, Any]:
    """Run the existing XAU backtest script in a bounded subprocess."""

    normalized_start = _normalize_date(start_date)
    normalized_end = _normalize_date(end_date)
    command = [
        python_executable or sys.executable,
        str(BACKTEST_SCRIPT),
        "--start",
        normalized_start,
        "--end",
        normalized_end,
        "--mode",
        str(mode).strip().lower(),
        "--max_hold",
        str(max(1, int(max_hold))),
    ]

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=max(1, int(timeout_seconds)),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise TimeoutError(
            f"Backtest exceeded timeout after {timeout_seconds}s for {normalized_start} -> {normalized_end}."
        ) from error

    duration_seconds = round(time.perf_counter() - started, 2)
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()

    if completed.returncode != 0:
        details = "\n".join(part for part in (stdout, stderr) if part).strip()
        raise RuntimeError(
            f"Backtest failed with exit code {completed.returncode}: {details or 'No output received.'}"
        )

    summary = _parse_metric_output(stdout)
    if not summary:
        analysis = analyze_trades(PROJECT_ROOT / "trades.csv")
        summary = {
            "trades": analysis["trades"],
            "total_trades": analysis["total_trades"],
            "wins": analysis["wins"],
            "losses": analysis["losses"],
            "win_rate": analysis["win_rate"],
            "profit_factor": analysis["profit_factor"],
            "avg_R": analysis["avg_R"],
            "max_drawdown": analysis["max_drawdown_r"],
            "ending_balance": analysis["ending_balance"],
        }

    summary.update(
        {
            "start_date": normalized_start,
            "end_date": normalized_end,
            "mode": str(mode).strip().lower(),
            "duration_seconds": duration_seconds,
            "stdout_tail": stdout.splitlines()[-12:] if stdout else [],
        }
    )
    return summary
