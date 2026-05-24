from __future__ import annotations

import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from research_engine.parameter_optimizer import optimize_parameters
from research_engine.performance_analyzer import analyze_variant_performance
from research_engine.strategy_ranker import rank_strategies
from research_engine.strategy_variants import generate_strategy_variants


RESEARCH_LOG_PATH = PROJECT_ROOT / "logs" / "research_summary.json"


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
    return value


def _store_summary(payload: dict[str, Any]) -> None:
    RESEARCH_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESEARCH_LOG_PATH.write_text(json.dumps(_json_safe(payload), indent=2), encoding="utf-8")


def _run_research_impl(
    *,
    start_date: str | None,
    end_date: str | None,
    months: int,
    mode: str,
    max_hold_bars: int,
    max_variants: int | None,
    top_n: int,
) -> dict[str, Any]:
    variants = generate_strategy_variants()
    optimization = optimize_parameters(
        variants=variants,
        start_date=start_date,
        end_date=end_date,
        months=months,
        mode=mode,
        max_hold_bars=max_hold_bars,
        max_variants=max_variants,
    )
    analysis = analyze_variant_performance(optimization["results"])
    ranked = rank_strategies(analysis["variants"], top_n=top_n)
    best_strategy = ranked[0] if ranked else {}

    payload = {
        "research_window": optimization["dataset_window"],
        "tested_variants": optimization["tested_variants"],
        "best_strategy": best_strategy.get("params", {}),
        "win_rate": best_strategy.get("win_rate", 0.0),
        "profit_factor": best_strategy.get("profit_factor", 0.0),
        "avg_R": best_strategy.get("avg_R", 0.0),
        "max_drawdown": best_strategy.get("max_drawdown", 0.0),
        "sharpe_ratio": best_strategy.get("sharpe_ratio", 0.0),
        "risk_adjusted_return": best_strategy.get("risk_adjusted_return", 0.0),
        "top_5_strategies": ranked,
        "weakness_summary": analysis["weakness_summary"],
        "improvement_suggestions": analysis["improvement_suggestions"],
    }
    _store_summary(payload)
    return payload


def run_research(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    months: int = 12,
    mode: str = "forex",
    max_hold_bars: int = 12,
    max_variants: int | None = None,
    top_n: int = 5,
    timeout_seconds: int = 3600,
) -> dict[str, Any]:
    """Generate, test, analyze, and rank SEAN0-ALGO-V1 strategy variants."""

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            _run_research_impl,
            start_date=start_date,
            end_date=end_date,
            months=max(1, int(months)),
            mode=str(mode).strip().lower(),
            max_hold_bars=max(1, int(max_hold_bars)),
            max_variants=max_variants,
            top_n=max(1, int(top_n)),
        )
        try:
            return future.result(timeout=max(1, int(timeout_seconds)))
        except FuturesTimeout as error:
            raise TimeoutError(
                f"Research run exceeded timeout after {timeout_seconds}s."
            ) from error
