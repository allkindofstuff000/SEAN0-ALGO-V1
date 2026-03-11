from __future__ import annotations

import math
from typing import Any


def _profit_factor_score(result: dict[str, Any]) -> float:
    value = float(result["profit_factor"])
    return value if math.isfinite(value) else 999999.0


def _public_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "variant_id": result["variant_id"],
        "params": result["params"],
        "profit_factor": float(result["profit_factor"]),
        "max_drawdown": float(result["max_drawdown"]),
        "win_rate": float(result["win_rate"]),
        "avg_R": float(result["avg_R"]),
        "total_trades": int(result["total_trades"]),
        "ending_balance": float(result.get("ending_balance", 0.0)),
        "sharpe_ratio": float(result.get("sharpe_ratio", 0.0)),
        "risk_adjusted_return": float(result.get("risk_adjusted_return", 0.0)),
        "weaknesses": result.get("weaknesses", {}),
    }


def rank_strategies(results: list[dict[str, Any]], top_n: int = 5) -> list[dict[str, Any]]:
    """Sort strategy variants by profit factor, drawdown quality, and win rate."""

    ranked = sorted(
        results,
        key=lambda result: (
            _profit_factor_score(result),
            float(result["max_drawdown"]),
            float(result["win_rate"]),
            float(result.get("risk_adjusted_return", 0.0)),
            int(result["total_trades"]),
        ),
        reverse=True,
    )
    return [_public_summary(result) for result in ranked[: max(1, int(top_n))]]
