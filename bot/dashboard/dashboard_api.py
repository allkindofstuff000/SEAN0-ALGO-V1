from __future__ import annotations

import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import Body, FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from bot.config.config import (
    DATA_REFRESH_SECONDS,
    ENGINE_STATE_PATH,
    PERFORMANCE_PATH,
    SIGNAL_HISTORY_PATH,
    SIGNAL_MODE,
    SYMBOL,
    THRESHOLD_HISTORY_PATH,
    THRESHOLD_STATE_PATH,
    TRADE_LOG_PATH,
)
from bot.learning.strategy_adapter import StrategyAdapter
from bot.learning.threshold_optimizer import ThresholdOptimizer

try:
    from storage import load_config, save_config
except Exception:  # pragma: no cover - fallback if legacy storage is unavailable
    load_config = None
    save_config = None


app = FastAPI(title="Trading Signal Dashboard API", version="2.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_DECISION_TRACE_PATH = Path("logs/decision_trace.log")
_RISK_STATE_PATH = Path("bot_runtime/risk_state.json")
_FALLBACK_CONTROL_PATH = Path("bot_runtime/dashboard_control.json")
_BACKTEST_REPORTS_PATH = Path("data/backtests")
_WFO_REPORTS_PATH = Path("data/wfo")


def _default_state() -> dict[str, Any]:
    return {
        "status": "unknown",
        "regime": "UNKNOWN",
        "session": "UNKNOWN",
        "volatility_state": "UNKNOWN",
        "liquidity_state": "UNKNOWN",
        "signal_score": 0,
        "dynamic_threshold": 70,
        "signal_confidence": "LOW",
        "last_cycle_utc": None,
        "last_signal_time_utc": None,
        "last_direction": None,
        "last_reason": None,
        "enabled": True,
        "route_mode": SIGNAL_MODE,
        "updated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(default)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            merged = dict(default)
            merged.update(payload)
            return merged
    except Exception:
        pass
    return dict(default)


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_json_lines(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                payload = json.loads(text)
            except Exception:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows[-limit:]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _load_runtime_config() -> dict[str, Any]:
    if callable(load_config):
        try:
            return dict(load_config())
        except Exception:
            pass
    return _read_json(_FALLBACK_CONTROL_PATH, {"enabled": True, "self_learning_enabled": True})


def _save_runtime_config(config: dict[str, Any]) -> None:
    if callable(save_config):
        save_config(config)
        return
    _FALLBACK_CONTROL_PATH.parent.mkdir(parents=True, exist_ok=True)
    _FALLBACK_CONTROL_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def _normalize_pair(value: Any) -> str:
    pair = str(value or SYMBOL)
    if pair.endswith("USDT"):
        return f"{pair[:-4]}USD"
    return pair


def _pair_label() -> str:
    return _normalize_pair(SYMBOL)


def _connection_status(updated_at_utc: str | None) -> str:
    if not updated_at_utc:
        return "DISCONNECTED"
    try:
        updated = datetime.fromisoformat(updated_at_utc)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - updated.astimezone(timezone.utc)
        return "ONLINE" if age <= timedelta(seconds=(DATA_REFRESH_SECONDS * 2) + 5) else "STALE"
    except Exception:
        return "DISCONNECTED"


def _latest_decision_log() -> dict[str, Any]:
    rows = _read_json_lines(_DECISION_TRACE_PATH, limit=1)
    return rows[-1] if rows else {}


def _confidence_label(score: int, threshold: int) -> str:
    if score >= threshold + 12:
        return "HIGH"
    if score >= threshold:
        return "MEDIUM"
    return "LOW"


def _latest_artifact_dir(root: Path) -> Path | None:
    if not root.exists():
        return None
    candidates = [path for path in root.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path.stat().st_mtime, path.name))


def _read_artifact_summary(root: Path, summary_name: str) -> tuple[Path | None, dict[str, Any]]:
    artifact_dir = _latest_artifact_dir(root)
    if artifact_dir is None:
        return None, {}
    return artifact_dir, _read_json(artifact_dir / summary_name, {})


def _score_distribution_from_mapping(mapping: dict[str, Any]) -> list[dict[str, Any]]:
    ordered_ranges = ["<60", "60-65", "65-70", "70-75", "75-80", "80+"]
    return [{"range": label, "count": _safe_int(mapping.get(label, 0))} for label in ordered_ranges]


def _session_performance_from_summary(summary: dict[str, Any]) -> list[dict[str, Any]]:
    counts = summary.get("signals_per_session", {}) or {}
    win_rates = summary.get("session_win_rate", {}) or {}
    session_order = ["ASIAN", "LONDON", "NEW_YORK", "OVERLAP"]
    sessions = [session for session in session_order if session in counts or session in win_rates]
    sessions.extend(session for session in counts.keys() if session not in sessions)
    return [
        {
            "session": session,
            "totalTrades": _safe_int(counts.get(session, 0)),
            "winRate": round(_safe_float(win_rates.get(session, 0.0)), 2),
        }
        for session in sessions
    ]


def _equity_curve(rows: list[dict[str, Any]], *, timestamp_field: str = "exit_timestamp") -> list[dict[str, Any]]:
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    if frame.empty or "equity_after" not in frame.columns:
        return []
    resolved_timestamp_field = timestamp_field if timestamp_field in frame.columns else "entry_timestamp"
    if resolved_timestamp_field not in frame.columns:
        return []
    frame[resolved_timestamp_field] = pd.to_datetime(frame[resolved_timestamp_field], errors="coerce", utc=True)
    frame["equity_after"] = pd.to_numeric(frame["equity_after"], errors="coerce")
    frame = frame.dropna(subset=[resolved_timestamp_field, "equity_after"]).sort_values(by=resolved_timestamp_field)
    return [
        {
            "timestamp": row[resolved_timestamp_field].isoformat(),
            "equity": round(float(row["equity_after"]), 2),
        }
        for _, row in frame.iterrows()
    ]


def _recent_signal_rows(limit: int = 20) -> list[dict[str, Any]]:
    decision_logs = list(reversed(_read_json_lines(_DECISION_TRACE_PATH, limit=max(limit, 80))))
    if decision_logs:
        return [
            {
                "timestamp": log.get("timestamp"),
                "pair": _normalize_pair(log.get("pair")),
                "direction": str(log.get("direction", "NONE")).upper(),
                "score": _safe_int(log.get("signal_score", log.get("score", 0))),
                "threshold": _safe_int(log.get("score_threshold", 70), 70),
                "confidence": _confidence_label(
                    _safe_int(log.get("signal_score", log.get("score", 0))),
                    _safe_int(log.get("score_threshold", 70), 70),
                ),
                "marketRegime": str(log.get("market_regime", log.get("regime", "UNKNOWN"))).upper(),
                "session": str(log.get("session", "UNKNOWN")).upper(),
                "status": "OPEN" if bool(log.get("signal_generated")) else "REJECTED",
                "signalGenerated": bool(log.get("signal_generated")),
                "reason": log.get("reason"),
            }
            for log in decision_logs[:limit]
        ]

    signal_rows = list(reversed(_read_csv(SIGNAL_HISTORY_PATH)))
    return [
        {
            "timestamp": row.get("timestamp_utc"),
            "pair": _normalize_pair(row.get("pair")),
            "direction": str(row.get("direction", "NONE")).upper(),
            "score": _safe_int(row.get("score", 0)),
            "threshold": _safe_int(row.get("score_threshold", 70), 70),
            "confidence": _confidence_label(_safe_int(row.get("score", 0)), _safe_int(row.get("score_threshold", 70), 70)),
            "marketRegime": str(row.get("regime", "UNKNOWN")).upper(),
            "session": str(row.get("session", "UNKNOWN")).upper(),
            "status": "ROUTED",
            "signalGenerated": True,
            "reason": row.get("reason"),
        }
        for row in signal_rows[:limit]
    ]


def _best_score_range(range_stats: list[dict[str, Any]]) -> str | None:
    eligible = [row for row in range_stats if int(row.get("total_trades", 0)) > 0]
    if not eligible:
        return None
    best = max(eligible, key=lambda row: (float(row.get("win_rate", 0.0)), int(row.get("total_trades", 0))))
    return str(best.get("range"))


def _session_performance(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    if frame.empty:
        return []
    frame["result"] = frame["result"].astype(str).str.upper()
    frame = frame[frame["result"].isin({"WIN", "LOSS"})]
    if frame.empty:
        return []
    metrics: list[dict[str, Any]] = []
    for session, group in frame.groupby("session"):
        wins = int((group["result"] == "WIN").sum())
        total = int(len(group))
        metrics.append(
            {
                "session": session,
                "totalTrades": total,
                "winRate": round((wins / total) * 100.0, 2) if total else 0.0,
            }
        )
    return metrics


def _score_distribution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = [(60, 65), (65, 70), (70, 75), (75, 80), (80, None)]
    frame = pd.DataFrame(rows)
    if frame.empty:
        return [{"range": f"{lower}+" if upper is None else f"{lower}-{upper}", "count": 0} for lower, upper in buckets]
    frame["score"] = pd.to_numeric(frame.get("score"), errors="coerce").fillna(0)
    distribution: list[dict[str, Any]] = []
    for lower, upper in buckets:
        if upper is None:
            subset = frame[frame["score"] >= lower]
            label = f"{lower}+"
        else:
            subset = frame[(frame["score"] >= lower) & (frame["score"] < upper)]
            label = f"{lower}-{upper}"
        distribution.append({"range": label, "count": int(len(subset))})
    return distribution


def _signals_over_time(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not rows:
        return []
    frame = pd.DataFrame(rows)
    timestamp_col = "timestamp_utc" if "timestamp_utc" in frame.columns else frame.columns[0]
    frame[timestamp_col] = pd.to_datetime(frame[timestamp_col], errors="coerce", utc=True)
    frame = frame.dropna(subset=[timestamp_col])
    if frame.empty:
        return []
    grouped = frame.groupby(frame[timestamp_col].dt.strftime("%Y-%m-%d %H:%M")).size()
    return [{"timestamp": index, "count": int(value)} for index, value in grouped.tail(30).items()]


def _threshold_evolution(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": row.get("timestamp_utc"),
            "threshold": int(float(row.get("new_threshold", 0) or 0)),
        }
        for row in rows[-30:]
    ]


def _best_and_worst_session(rows: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    metrics = _session_performance(rows)
    if not metrics:
        return None, None
    best = max(metrics, key=lambda row: float(row.get("winRate", 0.0)))
    worst = min(metrics, key=lambda row: float(row.get("winRate", 0.0)))
    return str(best.get("session")), str(worst.get("session"))


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    state = _read_json(ENGINE_STATE_PATH, _default_state())
    config = _load_runtime_config()
    return {
        "botStatus": str(state.get("status", "unknown")).upper(),
        "connectionStatus": _connection_status(state.get("updated_at_utc")),
        "pair": _pair_label(),
        "mode": str(state.get("route_mode", SIGNAL_MODE)).upper(),
        "enabled": bool(config.get("enabled", state.get("enabled", True))),
        "lastSignalTimestamp": state.get("last_signal_time_utc"),
        "learningEnabled": bool(config.get("self_learning_enabled", True)),
        "updatedAt": state.get("updated_at_utc"),
    }


@app.get("/api/market-state")
def get_market_state() -> dict[str, Any]:
    state = _read_json(ENGINE_STATE_PATH, _default_state())
    latest_log = _latest_decision_log()
    return {
        "session": state.get("session", latest_log.get("session", "UNKNOWN")),
        "regime": state.get("regime", latest_log.get("regime", "UNKNOWN")),
        "volatilityState": state.get("volatility_state") or ("EXPANDING" if float(latest_log.get("atr_ratio", 0.0)) >= 1.0 else "CONTRACTING"),
        "liquidityZones": state.get("liquidity_state") or ("Liquidity Sweep" if latest_log.get("liquidity_sweep") else "Range Pools"),
        "updatedAt": state.get("updated_at_utc"),
    }


@app.get("/api/signal")
def get_signal() -> dict[str, Any]:
    state = _read_json(ENGINE_STATE_PATH, _default_state())
    latest_log = _latest_decision_log()
    score = int(float(latest_log.get("signal_score", state.get("signal_score", 0)) or 0))
    threshold = int(float(latest_log.get("score_threshold", state.get("dynamic_threshold", 70)) or 70))
    direction = latest_log.get("direction") or state.get("last_direction") or "NONE"
    return {
        "score": score,
        "threshold": threshold,
        "confidence": state.get("signal_confidence") or _confidence_label(score, threshold),
        "direction": direction,
        "lastSignalTimestamp": state.get("last_signal_time_utc"),
        "scoreDelta": score - threshold,
        "breakdown": latest_log.get("breakdown", {}),
        "mode": state.get("route_mode", SIGNAL_MODE),
    }


@app.get("/api/signals")
def get_signals(limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    signals = _recent_signal_rows(limit=limit)
    return {"signals": signals, "count": len(signals)}


@app.get("/api/performance")
def get_performance() -> dict[str, Any]:
    trade_rows = _read_csv(TRADE_LOG_PATH)
    signal_rows = _read_csv(SIGNAL_HISTORY_PATH)
    risk_state = _read_json(_RISK_STATE_PATH, {"loss_streak": 0, "daily_counts": {}, "last_signal_utc": None})
    threshold_history = _read_csv(THRESHOLD_HISTORY_PATH)

    win_loss_frame = pd.DataFrame(trade_rows)
    overall_win_rate = 0.0
    if not win_loss_frame.empty:
        win_loss_frame["result"] = win_loss_frame["result"].astype(str).str.upper()
        decided = win_loss_frame[win_loss_frame["result"].isin({"WIN", "LOSS"})]
        if not decided.empty:
            wins = int((decided["result"] == "WIN").sum())
            overall_win_rate = round((wins / len(decided)) * 100.0, 2)

    best_session, worst_session = _best_and_worst_session(trade_rows)
    today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    signals_today = int(_read_json(_RISK_STATE_PATH, {"daily_counts": {}}).get("daily_counts", {}).get(today_key, 0))

    return {
        "signalsToday": signals_today,
        "winRate": overall_win_rate,
        "lossStreak": int(risk_state.get("loss_streak", 0)),
        "bestSession": best_session,
        "worstSession": worst_session,
        "sessionPerformance": _session_performance(trade_rows),
        "signalScoreDistribution": _score_distribution(trade_rows or signal_rows),
        "thresholdEvolution": _threshold_evolution(threshold_history),
        "signalsOverTime": _signals_over_time(signal_rows),
        "performanceSnapshot": _read_json(PERFORMANCE_PATH, {}),
    }


@app.get("/api/decision-logs")
def get_decision_logs(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    logs = list(reversed(_read_json_lines(_DECISION_TRACE_PATH, limit=limit)))
    return {"logs": logs, "count": len(logs)}


@app.get("/api/learning-state")
def get_learning_state() -> dict[str, Any]:
    config = _load_runtime_config()
    threshold_state = _read_json(
        THRESHOLD_STATE_PATH,
        {
            "enabled": True,
            "current_threshold": 70,
            "last_optimized_trade_count": 0,
            "updated_at_utc": None,
            "last_reason": None,
            "last_overall_win_rate": None,
        },
    )
    history = _read_csv(THRESHOLD_HISTORY_PATH)
    trade_rows = _read_csv(TRADE_LOG_PATH)
    optimizer = ThresholdOptimizer(lookback_trades=100)
    analysis = optimizer.analyze(trades=pd.DataFrame(trade_rows), current_threshold=int(threshold_state.get("current_threshold", 70)))
    return {
        "enabled": bool(config.get("self_learning_enabled", threshold_state.get("enabled", True))),
        "currentThreshold": int(threshold_state.get("current_threshold", 70)),
        "lastOptimizationTime": threshold_state.get("updated_at_utc"),
        "tradesAnalyzed": int(analysis.get("analyzed_trades", 0)),
        "bestScoreRange": _best_score_range(analysis.get("range_stats", [])),
        "lastReason": threshold_state.get("last_reason"),
        "overallWinRate": threshold_state.get("last_overall_win_rate", analysis.get("overall_win_rate", 0.0)),
        "history": history[-30:],
        "totalCompletedTrades": len(trade_rows),
    }


@app.get("/api/backtest")
def get_backtest() -> dict[str, Any]:
    artifact_dir, summary_payload = _read_artifact_summary(_BACKTEST_REPORTS_PATH, "performance_summary.json")
    if artifact_dir is None:
        return {
            "available": False,
            "summary": {},
            "scoreDistribution": [],
            "sessionPerformance": [],
            "equityCurve": [],
            "trades": [],
        }

    trade_rows = _read_csv(artifact_dir / "trade_log.csv")
    summary = summary_payload.get("summary") or summary_payload.get("metrics") or {}
    return {
        "available": True,
        "generatedAt": summary_payload.get("generated_at_utc"),
        "sourceDir": str(artifact_dir.resolve()),
        "config": summary_payload.get("config", {}),
        "summary": summary,
        "scoreDistribution": _score_distribution_from_mapping(summary.get("score_distribution", {}) or {}),
        "sessionPerformance": _session_performance_from_summary(summary),
        "equityCurve": _equity_curve(trade_rows),
        "trades": trade_rows[-25:],
    }


@app.get("/api/wfo")
def get_wfo() -> dict[str, Any]:
    artifact_dir, summary_payload = _read_artifact_summary(_WFO_REPORTS_PATH, "wfo_summary.json")
    if artifact_dir is None:
        return {
            "available": False,
            "overview": {},
            "tradeMetrics": {},
            "parameterStability": {},
            "performancePerWindow": [],
            "equityCurve": [],
            "trades": [],
        }

    trade_rows = _read_csv(artifact_dir / "wfo_trade_log.csv")
    return {
        "available": True,
        "generatedAt": summary_payload.get("generated_at_utc"),
        "sourceDir": str(artifact_dir.resolve()),
        "config": summary_payload.get("config", {}),
        "overview": summary_payload.get("overview", {}),
        "tradeMetrics": summary_payload.get("overall_trade_metrics", {}),
        "parameterStability": summary_payload.get("parameter_stability", {}),
        "performancePerWindow": summary_payload.get("performance_per_window", []),
        "skippedWindows": summary_payload.get("skipped_windows", []),
        "equityCurve": _equity_curve(trade_rows),
        "trades": trade_rows[-25:],
    }


@app.get("/api/risk")
def get_risk() -> dict[str, Any]:
    config = _load_runtime_config()
    risk_state = _read_json(_RISK_STATE_PATH, {"loss_streak": 0, "daily_counts": {}, "last_signal_utc": None})
    threshold_state = _read_json(THRESHOLD_STATE_PATH, {"current_threshold": 70})
    return {
        "maxSignalsPerDay": int(config.get("max_signals_per_day", 3)),
        "maxLossStreak": int(config.get("max_loss_streak", 2)),
        "cooldownCandles": int(config.get("cooldown_candles", 3)),
        "riskPercentage": float(config.get("risk_percentage", 1.0)),
        "signalScoreThreshold": int(threshold_state.get("current_threshold", config.get("signal_score_threshold", 70))),
        "lossStreak": int(risk_state.get("loss_streak", 0)),
        "dailyCounts": risk_state.get("daily_counts", {}),
    }


@app.post("/api/control")
def control_bot(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    config = _load_runtime_config()
    action = str(payload.get("action", "")).strip().lower()
    if action == "start":
        config["enabled"] = True
    elif action in {"stop", "emergency_stop"}:
        config["enabled"] = False
        config["emergency_stop"] = action == "emergency_stop"
    elif action == "restart":
        config["enabled"] = True
        config["restart_requested_at_utc"] = datetime.now(timezone.utc).isoformat()
    else:
        return {"ok": False, "error": "unsupported_action"}
    _save_runtime_config(config)
    return {"ok": True, "action": action, "config": config}


@app.post("/api/risk-config")
def update_risk_config(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    config = _load_runtime_config()
    config["max_signals_per_day"] = max(1, int(payload.get("maxSignalsPerDay", config.get("max_signals_per_day", 3))))
    config["max_loss_streak"] = max(1, int(payload.get("maxLossStreak", config.get("max_loss_streak", 2))))
    config["cooldown_candles"] = max(1, int(payload.get("cooldownCandles", config.get("cooldown_candles", 3))))
    config["risk_percentage"] = max(0.1, float(payload.get("riskPercentage", config.get("risk_percentage", 1.0))))
    threshold_state: dict[str, Any] | None = None
    requested_threshold = payload.get("signalScoreThreshold")
    if requested_threshold is not None:
        adapter = StrategyAdapter(
            state_path=THRESHOLD_STATE_PATH,
            history_path=THRESHOLD_HISTORY_PATH,
            default_threshold=int(_read_json(THRESHOLD_STATE_PATH, {"current_threshold": 70}).get("current_threshold", 70)),
        )
        threshold_state = adapter.set_threshold(int(requested_threshold), reason="dashboard_manual_update")
        config["signal_score_threshold"] = int(threshold_state.get("current_threshold", requested_threshold))
    _save_runtime_config(config)
    return {"ok": True, "config": config, "threshold": threshold_state}


@app.post("/api/learning-control")
def update_learning_state(payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    config = _load_runtime_config()
    adapter = StrategyAdapter(
        state_path=THRESHOLD_STATE_PATH,
        history_path=THRESHOLD_HISTORY_PATH,
        default_threshold=int(_read_json(THRESHOLD_STATE_PATH, {"current_threshold": 70}).get("current_threshold", 70)),
    )
    action = str(payload.get("action", "")).strip().lower()
    if action == "enable":
        config["self_learning_enabled"] = True
        adapter.set_enabled(True)
    elif action == "disable":
        config["self_learning_enabled"] = False
        adapter.set_enabled(False)
    elif action == "reset":
        config["self_learning_enabled"] = True
        adapter.reset()
    else:
        return {"ok": False, "error": "unsupported_action"}
    _save_runtime_config(config)
    return {"ok": True, "action": action, "learning": adapter.snapshot(), "config": config}
