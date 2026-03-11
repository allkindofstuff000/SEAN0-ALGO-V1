from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRADES_PATH = PROJECT_ROOT / "trades.csv"


def _resolve_trades_path(trades_path: str | Path | None) -> Path:
    if trades_path is None:
        return DEFAULT_TRADES_PATH
    return Path(trades_path).expanduser().resolve()


def _load_trades(trades_path: Path) -> pd.DataFrame:
    if not trades_path.exists():
        raise FileNotFoundError(f"Trade log not found: {trades_path}")

    trades = pd.read_csv(trades_path)
    if trades.empty:
        return trades

    for column in ("pnl", "R_multiple", "equity_before", "equity_after"):
        if column in trades.columns:
            trades[column] = pd.to_numeric(trades[column], errors="coerce")

    if "exit_timestamp" in trades.columns:
        trades["exit_timestamp"] = pd.to_datetime(trades["exit_timestamp"], utc=True, errors="coerce")

    return trades


def _safe_profit_factor(gross_profit: float, gross_loss: float) -> float:
    if gross_loss > 0:
        return gross_profit / gross_loss
    if gross_profit > 0:
        return math.inf
    return 0.0


def _build_equity_curve(trades: pd.DataFrame, pnl_values: pd.Series) -> pd.Series:
    if "equity_after" in trades.columns and trades["equity_after"].notna().any():
        return trades["equity_after"].ffill().fillna(0.0)

    starting_balance = 0.0
    if "equity_before" in trades.columns:
        starting_values = trades["equity_before"].dropna()
        if not starting_values.empty:
            starting_balance = float(starting_values.iloc[0])

    return pnl_values.cumsum() + starting_balance


def analyze_trades(trades_path: str | Path | None = None) -> dict[str, Any]:
    """Analyze the latest completed trade log for OpenClaw."""

    resolved_path = _resolve_trades_path(trades_path)
    trades = _load_trades(resolved_path)

    if trades.empty:
        return {
            "source_file": str(resolved_path),
            "trades": 0,
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_R": 0.0,
            "drawdown": 0.0,
            "max_drawdown_r": 0.0,
            "net_pnl": 0.0,
            "avg_pnl": 0.0,
            "ending_balance": 0.0,
        }

    pnl_values = (
        trades["pnl"].fillna(0.0)
        if "pnl" in trades.columns
        else pd.Series([0.0] * len(trades), dtype="float64")
    )
    r_values = (
        trades["R_multiple"].fillna(0.0)
        if "R_multiple" in trades.columns
        else pd.Series([0.0] * len(trades), dtype="float64")
    )

    if "pnl" in trades.columns:
        wins = int((pnl_values > 0).sum())
        losses = int((pnl_values < 0).sum())
    else:
        wins = int((r_values > 0).sum())
        losses = int((r_values < 0).sum())

    total_trades = int(len(trades))
    win_rate = (wins / total_trades) * 100.0 if total_trades else 0.0

    gross_profit = float(pnl_values[pnl_values > 0].sum())
    gross_loss = float(abs(pnl_values[pnl_values < 0].sum()))
    profit_factor = _safe_profit_factor(gross_profit, gross_loss)

    equity_curve = _build_equity_curve(trades, pnl_values)
    equity_peak = equity_curve.cummax()
    drawdown = ((equity_curve - equity_peak) / equity_peak.replace(0, pd.NA)).fillna(0.0) * 100.0

    cumulative_r = r_values.cumsum()
    drawdown_r = cumulative_r - cumulative_r.cummax()

    ending_balance = float(equity_curve.iloc[-1]) if not equity_curve.empty else 0.0

    return {
        "source_file": str(resolved_path),
        "trades": total_trades,
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 2),
        "profit_factor": round(profit_factor, 4) if math.isfinite(profit_factor) else math.inf,
        "avg_R": round(float(r_values.mean()), 4) if not r_values.empty else 0.0,
        "drawdown": round(float(drawdown.min()), 4) if not drawdown.empty else 0.0,
        "max_drawdown_r": round(float(drawdown_r.min()), 4) if not drawdown_r.empty else 0.0,
        "net_pnl": round(float(pnl_values.sum()), 2),
        "avg_pnl": round(float(pnl_values.mean()), 4) if not pnl_values.empty else 0.0,
        "ending_balance": round(ending_balance, 2),
    }
