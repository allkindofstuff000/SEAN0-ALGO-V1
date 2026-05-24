from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import random
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

import pandas as pd
import pytz

from agent_tools.market_regime_tool import get_market_regime, open_regime_visuals
from agent_tools.research_tool import run_research as run_research_workflow
import backtest_xau_strategy as xbt
ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"
TRADES_PATH = ROOT / "trades.csv"
STATE_PATH = ROOT / "state.json"
DECISION_LOG_PATH = LOG_DIR / "decision_trace.log"
BACKTEST_SUMMARY_PATH = LOG_DIR / "openclaw_backtest_summary.json"
AGENT_LOG_PATH = LOG_DIR / "openclaw_backtest_agent.log"
PAPER_SERVICE_PID_PATH = LOG_DIR / "openclaw_paper_service.pid"
PAPER_ENGINE_PID_PATH = LOG_DIR / "openclaw_paper_engine.pid"
PAPER_ENGINE_LOG_PATH = LOG_DIR / "openclaw_paper_engine.log"

BD_TZ = pytz.timezone("Asia/Dhaka")
DEFAULT_PAPER_MODE = True
DEFAULT_BACKTEST_MONTHS = 6
DEFAULT_VALIDATE_TRADES = 5
PAPER_STATUS_INTERVAL_SECONDS = 300


def _ensure_runtime_dirs() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _to_bd_timestamp(value: str | pd.Timestamp | dt.datetime | None) -> str | None:
    if value is None:
        return None
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    return timestamp.tz_convert(BD_TZ).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, pd.Timestamp)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return value


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(_json_safe(payload), indent=2), encoding="utf-8")


def _append_agent_log(message: str) -> None:
    _ensure_runtime_dirs()
    with AGENT_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{_now_utc().isoformat()}] {message}\n")


def _normalize_date_window(
    *,
    months: int = DEFAULT_BACKTEST_MONTHS,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[str, str]:
    today_utc = pd.Timestamp.now(tz="UTC").normalize()
    default_end = today_utc - pd.Timedelta(days=1)
    default_start = default_end - pd.DateOffset(months=max(1, int(months)))
    resolved_start = xbt.parse_date_utc(start_date).strftime("%Y-%m-%d") if start_date else default_start.strftime("%Y-%m-%d")
    resolved_end = xbt.parse_date_utc(end_date).strftime("%Y-%m-%d") if end_date else default_end.strftime("%Y-%m-%d")
    return resolved_start, resolved_end


def _load_trades_frame(trades_path: Path = TRADES_PATH) -> pd.DataFrame:
    if not trades_path.exists():
        raise FileNotFoundError(f"Trade file not found: {trades_path}")
    trades = pd.read_csv(trades_path)
    if trades.empty:
        return trades
    for column in ("timestamp", "entry_timestamp", "exit_timestamp"):
        if column in trades.columns:
            trades[column] = pd.to_datetime(trades[column], utc=True, errors="coerce")
    return trades


def _tail_decision_logs(limit: int = 20) -> list[str]:
    if not DECISION_LOG_PATH.exists():
        return []

    recent_lines: deque[str] = deque(maxlen=max(1, int(limit)))
    with DECISION_LOG_PATH.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if line:
                recent_lines.append(line)

    formatted: list[str] = []
    for line in recent_lines:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            formatted.append(line)
            continue

        if not isinstance(payload, dict):
            formatted.append(str(payload))
            continue

        timestamp = str(payload.get("timestamp", "unknown-time"))
        direction = str(payload.get("direction", "NONE"))
        score = payload.get("signal_score", payload.get("score", 0))
        threshold = payload.get("score_threshold", "?")
        reason = str(payload.get("reason", "n/a"))
        decision = str(payload.get("decision", "unknown"))
        formatted.append(
            f"[{timestamp}] direction={direction} score={score}/{threshold} "
            f"decision={decision} reason={reason}"
        )
    return formatted


def _generate_replay_validation_examples(
    trades: pd.DataFrame,
    *,
    count: int = DEFAULT_VALIDATE_TRADES,
    seed: int = 42,
) -> list[dict[str, Any]]:
    if trades.empty:
        return []

    selected = trades.sample(n=min(int(count), len(trades)), random_state=seed)
    examples: list[dict[str, Any]] = []
    for row in selected.to_dict(orient="records"):
        signal_ts = pd.Timestamp(row["timestamp"])
        entry_ts = pd.Timestamp(row["entry_timestamp"])
        exit_ts = pd.Timestamp(row["exit_timestamp"])
        direction = str(row["direction"])
        trend_bias = "EMA50 > EMA200" if float(row["ema50"]) > float(row["ema200"]) else "EMA50 < EMA200"
        breakout_rule = "close > previous 5m high" if direction == "BUY" else "close < previous 5m low"

        examples.append(
            {
                "trade_timestamp_utc": signal_ts.isoformat(),
                "trade_timestamp_bd": _to_bd_timestamp(signal_ts),
                "entry_timestamp_utc": entry_ts.isoformat(),
                "entry_timestamp_bd": _to_bd_timestamp(entry_ts),
                "exit_timestamp_utc": exit_ts.isoformat(),
                "exit_timestamp_bd": _to_bd_timestamp(exit_ts),
                "direction": direction,
                "entry": round(float(row["entry_price"]), 4),
                "sl": round(float(row["sl"]), 4),
                "tp": round(float(row["tp"]), 4),
                "result": str(row["result"]),
                "exit_reason": str(row["exit_reason"]),
                "verification_steps": [
                    "Open TradingView Replay on OANDA:XAUUSD.",
                    f"Jump to the 15m trend candle before {signal_ts.isoformat()} and confirm {trend_bias}.",
                    f"On the 5m chart, confirm the signal candle at {signal_ts.isoformat()} satisfies {breakout_rule}.",
                    (
                        f"Check the signal candle RSI ({float(row['rsi']):.2f}) and ATR ({float(row['atr']):.2f}) "
                        "match the strategy acceptance filters."
                    ),
                    (
                        f"Advance from entry at {entry_ts.isoformat()} candle by candle and confirm "
                        f"{row['exit_reason']} caused the exit at {exit_ts.isoformat()}."
                    ),
                ],
            }
        )
    return examples


def _print_validation_examples(examples: list[dict[str, Any]]) -> None:
    if not examples:
        print("\nValidation examples: no trades available.\n")
        return

    print("\nTradingView Replay Validation")
    print("-----------------------------")
    for index, example in enumerate(examples, start=1):
        print(f"{index}. {example['direction']} trade @ {example['trade_timestamp_utc']}")
        print(f"   Entry: {example['entry']} | SL: {example['sl']} | TP: {example['tp']}")
        print(f"   Expected exit: {example['result']} via {example['exit_reason']}")
        for step_number, step in enumerate(example["verification_steps"], start=1):
            print(f"   Step {step_number}: {step}")


def run_backtest_agent(
    *,
    mode: str = "forex",
    months: int = DEFAULT_BACKTEST_MONTHS,
    start_date: str | None = None,
    end_date: str | None = None,
    max_hold: int = xbt.DEFAULT_MAX_HOLD,
    validation_samples: int = DEFAULT_VALIDATE_TRADES,
) -> dict[str, Any]:
    """Run the standalone OpenClaw backtest workflow."""

    _ensure_runtime_dirs()
    xbt.load_local_env()
    start_label, end_label = _normalize_date_window(months=months, start_date=start_date, end_date=end_date)
    start_utc = xbt.parse_date_utc(start_label)
    end_utc = xbt.parse_date_utc(end_label, inclusive_end=True)

    started = time.perf_counter()
    trades_df, metrics = xbt.run_backtest(
        start_utc=start_utc,
        end_utc=end_utc,
        mode=str(mode).strip().lower(),
        max_hold_bars=max(1, int(max_hold)),
    )
    duration_seconds = round(time.perf_counter() - started, 2)

    trades_df.to_csv(TRADES_PATH, index=False)
    xbt.save_equity_curve(trades_df)

    validation_examples = _generate_replay_validation_examples(
        trades_df,
        count=validation_samples,
    )

    summary = {
        "mode": str(mode).strip().lower(),
        "paper_mode": DEFAULT_PAPER_MODE,
        "start_date": start_label,
        "end_date": end_label,
        "duration_seconds": duration_seconds,
        "trades": int(metrics["total_trades"]),
        "win_rate": round(float(metrics["win_rate"]), 2),
        "avg_R": round(float(metrics["average_r"]), 4),
        "max_drawdown": round(float(metrics["max_drawdown_r"]), 4),
        "profit_factor": round(float(metrics["profit_factor"]), 4) if math.isfinite(float(metrics["profit_factor"])) else math.inf,
        "ending_balance": round(float(metrics["ending_balance"]), 2),
        "trades_csv": str(TRADES_PATH),
        "equity_curve": str(xbt.EQUITY_CURVE_PATH),
        "decision_trace": str(xbt.DECISION_TRACE_PATH),
        "validation_examples": validation_examples,
    }
    _write_json(BACKTEST_SUMMARY_PATH, summary)
    _append_agent_log(
        f"backtest completed mode={summary['mode']} window={start_label}->{end_label} "
        f"trades={summary['trades']} win_rate={summary['win_rate']}"
    )
    return summary


def analyze_last_trades(limit: int = 5) -> dict[str, Any]:
    """Return a small recent-trade snapshot for OpenClaw chat."""

    trades = _load_trades_frame()
    recent = trades.sort_values("exit_timestamp").tail(max(1, int(limit)))
    pnl_values = pd.to_numeric(recent.get("pnl", pd.Series(dtype="float64")), errors="coerce").fillna(0.0)
    r_values = pd.to_numeric(recent.get("R_multiple", pd.Series(dtype="float64")), errors="coerce").fillna(0.0)

    payload = {
        "limit": len(recent),
        "net_pnl": round(float(pnl_values.sum()), 2),
        "avg_R": round(float(r_values.mean()), 4) if not r_values.empty else 0.0,
        "wins": int((pnl_values > 0).sum()),
        "losses": int((pnl_values < 0).sum()),
        "trades": [],
    }

    for row in recent.to_dict(orient="records"):
        payload["trades"].append(
            {
                "timestamp_utc": _json_safe(row.get("timestamp")),
                "timestamp_bd": _to_bd_timestamp(row.get("timestamp")),
                "direction": str(row.get("direction", "")),
                "entry": round(float(row.get("entry_price", 0.0)), 4),
                "exit": round(float(row.get("exit_price", 0.0)), 4),
                "result": str(row.get("result", "")),
                "R_multiple": round(float(row.get("R_multiple", 0.0)), 4),
                "pnl": round(float(row.get("pnl", 0.0)), 2),
                "exit_reason": str(row.get("exit_reason", "")),
            }
        )
    return payload


def _read_pid(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (TypeError, ValueError):
        return None


def _pid_running(pid: int | None) -> bool:
    if pid is None:
        return False
    completed = subprocess.run(
        ["tasklist", "/FI", f"PID eq {int(pid)}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return str(pid) in (completed.stdout or "")


def paper_status(log_limit: int = 20) -> dict[str, Any]:
    """Return current paper-mode service and engine status."""

    service_pid = _read_pid(PAPER_SERVICE_PID_PATH)
    engine_pid = _read_pid(PAPER_ENGINE_PID_PATH)
    state = {}
    if STATE_PATH.exists():
        try:
            state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}

    return {
        "paper_mode": DEFAULT_PAPER_MODE,
        "service_pid": service_pid,
        "service_running": _pid_running(service_pid),
        "engine_pid": engine_pid,
        "engine_running": _pid_running(engine_pid),
        "state_path": str(STATE_PATH),
        "state": state,
        "recent_logs": _tail_decision_logs(limit=log_limit),
        "last_checked_utc": _now_utc().isoformat(),
        "last_checked_bd": _to_bd_timestamp(_now_utc()),
    }


def _run_paper_service() -> int:
    _ensure_runtime_dirs()
    PAPER_SERVICE_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")
    xbt.load_local_env()

    engine_log_handle = PAPER_ENGINE_LOG_PATH.open("a", encoding="utf-8")
    env = os.environ.copy()
    env["PAPER_MODE"] = "true"
    env["TEST_MODE"] = "True"
    env["MAX_CYCLES"] = "0"
    env["TELEGRAM_BOT_TOKEN"] = ""
    env["TELEGRAM_CHAT_ID"] = ""

    try:
        engine = subprocess.Popen(
            [sys.executable, str(ROOT / "main.py")],
            cwd=ROOT,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=engine_log_handle,
            stderr=engine_log_handle,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
        )
        PAPER_ENGINE_PID_PATH.write_text(str(engine.pid), encoding="utf-8")
        _append_agent_log(f"paper service started engine_pid={engine.pid}")

        while True:
            exit_code = engine.poll()
            status = paper_status(log_limit=5)
            _append_agent_log(
                f"paper status running={status['engine_running']} "
                f"last_state={status['state'].get('status', 'unknown')}"
            )
            if exit_code is not None:
                _append_agent_log(f"paper engine exited code={exit_code}")
                return int(exit_code)
            time.sleep(PAPER_STATUS_INTERVAL_SECONDS)
    finally:
        if PAPER_ENGINE_PID_PATH.exists():
            PAPER_ENGINE_PID_PATH.unlink()
        if PAPER_SERVICE_PID_PATH.exists():
            PAPER_SERVICE_PID_PATH.unlink()
        engine_log_handle.close()


def start_paper_mode() -> dict[str, Any]:
    """Start the live engine in background paper mode with 5-minute status logging."""

    _ensure_runtime_dirs()
    existing_pid = _read_pid(PAPER_SERVICE_PID_PATH)
    if _pid_running(existing_pid):
        return {
            "status": "already_running",
            "service_pid": existing_pid,
            "paper_mode": DEFAULT_PAPER_MODE,
        }

    launcher_log = AGENT_LOG_PATH.open("a", encoding="utf-8")
    creation_flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creation_flags |= subprocess.CREATE_NO_WINDOW
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP

    process = subprocess.Popen(
        [sys.executable, str(__file__), "paper-service"],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=launcher_log,
        stderr=launcher_log,
        creationflags=creation_flags,
    )
    launcher_log.close()
    time.sleep(2)
    service_pid = _read_pid(PAPER_SERVICE_PID_PATH) or process.pid
    return {
        "status": "started",
        "service_pid": service_pid,
        "paper_mode": DEFAULT_PAPER_MODE,
        "status_log": str(AGENT_LOG_PATH),
    }


def stop_paper_mode() -> dict[str, Any]:
    """Stop the background paper-mode service and its child engine."""

    service_pid = _read_pid(PAPER_SERVICE_PID_PATH)
    if not _pid_running(service_pid):
        if PAPER_SERVICE_PID_PATH.exists():
            PAPER_SERVICE_PID_PATH.unlink()
        if PAPER_ENGINE_PID_PATH.exists():
            PAPER_ENGINE_PID_PATH.unlink()
        return {
            "status": "not_running",
            "paper_mode": DEFAULT_PAPER_MODE,
        }

    completed = subprocess.run(
        ["taskkill", "/PID", str(int(service_pid)), "/T", "/F"],
        capture_output=True,
        text=True,
        check=False,
    )
    for path in (PAPER_SERVICE_PID_PATH, PAPER_ENGINE_PID_PATH):
        if path.exists():
            path.unlink()
    _append_agent_log(f"paper service stop requested pid={service_pid} rc={completed.returncode}")
    return {
        "status": "stopped" if completed.returncode == 0 else "failed",
        "service_pid": int(service_pid),
        "stdout": (completed.stdout or "").strip(),
        "stderr": (completed.stderr or "").strip(),
    }


def launch_dashboard(port: int = 8501) -> dict[str, Any]:
    """Launch the optional Streamlit dashboard in the background."""

    _ensure_runtime_dirs()
    creation_flags = 0
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        creation_flags |= subprocess.CREATE_NO_WINDOW
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creation_flags |= subprocess.CREATE_NEW_PROCESS_GROUP

    dashboard_log = (LOG_DIR / "openclaw_backtest_dashboard.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "streamlit",
            "run",
            "--server.headless",
            "true",
            "--server.port",
            str(int(port)),
            str(__file__),
            "--",
            "ui",
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=dashboard_log,
        stderr=dashboard_log,
        creationflags=creation_flags,
    )
    return {
        "status": "started",
        "pid": process.pid,
        "url": f"http://127.0.0.1:{int(port)}",
    }


def render_streamlit_dashboard() -> None:
    try:
        import streamlit as st
    except ImportError as error:
        raise RuntimeError("Streamlit is not installed. Run: pip install streamlit") from error

    from dashboard import render_regime_dashboard_section

    st.set_page_config(page_title="OpenClaw Backtest Agent", layout="wide")
    st.title("OpenClaw Backtest Agent")
    st.caption("SEAN0-ALGO-V1 XAUUSD backtest, logs, and paper-mode status")

    summary: dict[str, Any] = {}
    if BACKTEST_SUMMARY_PATH.exists():
        try:
            summary = json.loads(BACKTEST_SUMMARY_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = {}

    status = paper_status(log_limit=20)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Trades", summary.get("trades", 0))
    col2.metric("Win Rate %", summary.get("win_rate", 0.0))
    col3.metric("Avg R", summary.get("avg_R", 0.0))
    col4.metric("Profit Factor", summary.get("profit_factor", 0.0))

    st.subheader("Paper Mode Status")
    st.json(status)

    st.subheader("Recent Trades")
    if TRADES_PATH.exists():
        trades = _load_trades_frame().sort_values("exit_timestamp").tail(20)
        st.dataframe(trades, use_container_width=True)
    else:
        st.info("No trades.csv found yet.")

    st.subheader("Last 20 Decision Logs")
    logs = _tail_decision_logs(limit=20)
    if logs:
        st.code("\n".join(logs))
    else:
        st.info("No decision logs found yet.")

    render_regime_dashboard_section(st)


TOOLS = {
    "backtest_strategy": run_backtest_agent,
    "analyze_last_trades": analyze_last_trades,
    "get_market_regime": get_market_regime,
    "open_regime_visuals": open_regime_visuals,
    "run_research": run_research_workflow,
    "start_paper_mode": start_paper_mode,
    "stop_paper_mode": stop_paper_mode,
    "paper_status": paper_status,
    "launch_dashboard": launch_dashboard,
}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OpenClaw standalone backtest agent for SEAN0-ALGO-V1.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    backtest_parser = subparsers.add_parser("backtest", help="Run a historical backtest.")
    backtest_parser.add_argument("--mode", choices=("binary", "forex"), default="forex")
    backtest_parser.add_argument("--months", type=int, default=DEFAULT_BACKTEST_MONTHS)
    backtest_parser.add_argument("--start")
    backtest_parser.add_argument("--end")
    backtest_parser.add_argument("--max-hold", type=int, default=xbt.DEFAULT_MAX_HOLD)
    backtest_parser.add_argument("--validation-samples", type=int, default=DEFAULT_VALIDATE_TRADES)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze the last N trades.")
    analyze_parser.add_argument("--limit", type=int, default=5)

    subparsers.add_parser("regime", help="Show the latest detected market regime.")
    subparsers.add_parser("regime-tabs", help="Open TradingView paper-mode regime reference tabs.")

    research_parser = subparsers.add_parser("research", help="Run the strategy research workflow.")
    research_parser.add_argument("--mode", choices=("binary", "forex"), default="forex")
    research_parser.add_argument("--months", type=int, default=12)
    research_parser.add_argument("--start")
    research_parser.add_argument("--end")
    research_parser.add_argument("--max-hold", type=int, default=xbt.DEFAULT_MAX_HOLD)
    research_parser.add_argument("--max-variants", type=int, default=None)
    research_parser.add_argument("--top-n", type=int, default=5)

    subparsers.add_parser("paper-start", help="Start live paper mode in the background.")
    subparsers.add_parser("paper-stop", help="Stop the background paper mode service.")
    paper_status_parser = subparsers.add_parser("paper-status", help="Show paper mode status.")
    paper_status_parser.add_argument("--log-limit", type=int, default=20)

    logs_parser = subparsers.add_parser("logs", help="Show recent decision logs.")
    logs_parser.add_argument("--limit", type=int, default=20)

    dashboard_parser = subparsers.add_parser("dashboard", help="Launch the optional Streamlit dashboard.")
    dashboard_parser.add_argument("--port", type=int, default=8501)

    subparsers.add_parser("paper-service", help=argparse.SUPPRESS)
    subparsers.add_parser("ui", help=argparse.SUPPRESS)
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()

    try:
        if args.command == "backtest":
            summary = run_backtest_agent(
                mode=args.mode,
                months=args.months,
                start_date=args.start,
                end_date=args.end,
                max_hold=args.max_hold,
                validation_samples=args.validation_samples,
            )
            print(json.dumps(_json_safe(summary), indent=2))
            _print_validation_examples(summary["validation_examples"])
            return 0

        if args.command == "analyze":
            print(json.dumps(_json_safe(analyze_last_trades(limit=args.limit)), indent=2))
            return 0

        if args.command == "regime":
            print(json.dumps(_json_safe(get_market_regime()), indent=2))
            return 0

        if args.command == "regime-tabs":
            print(json.dumps(_json_safe(open_regime_visuals()), indent=2))
            return 0

        if args.command == "research":
            payload = run_research_workflow(
                start_date=args.start,
                end_date=args.end,
                months=args.months,
                mode=args.mode,
                max_hold_bars=args.max_hold,
                max_variants=args.max_variants,
                top_n=args.top_n,
            )
            print(json.dumps(_json_safe(payload), indent=2))
            return 0

        if args.command == "paper-start":
            print(json.dumps(_json_safe(start_paper_mode()), indent=2))
            return 0

        if args.command == "paper-stop":
            print(json.dumps(_json_safe(stop_paper_mode()), indent=2))
            return 0

        if args.command == "paper-status":
            print(json.dumps(_json_safe(paper_status(log_limit=args.log_limit)), indent=2))
            return 0

        if args.command == "logs":
            print(json.dumps({"entries": _tail_decision_logs(limit=args.limit)}, indent=2))
            return 0

        if args.command == "dashboard":
            print(json.dumps(_json_safe(launch_dashboard(port=args.port)), indent=2))
            return 0

        if args.command == "paper-service":
            return _run_paper_service()

        if args.command == "ui":
            render_streamlit_dashboard()
            return 0
    except Exception as error:
        print(json.dumps({"status": "error", "message": str(error)}, indent=2))
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
