from __future__ import annotations

from typing import Iterable

from research_engine.research_runner import ResearchDataset, prepare_research_dataset, run_strategy_variant
from research_engine.strategy_variants import StrategyVariant, generate_strategy_variants


def _optimizer_score(result: dict[str, object]) -> tuple[float, float, float, float, int]:
    return (
        float(result["profit_factor"]),
        float(result["avg_R"]),
        float(result["win_rate"]),
        float(result["max_drawdown"]),
        int(result["total_trades"]),
    )


def optimize_parameters(
    *,
    variants: Iterable[StrategyVariant] | None = None,
    dataset: ResearchDataset | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    months: int = 12,
    mode: str = "forex",
    max_hold_bars: int = 12,
    max_variants: int | None = None,
) -> dict[str, object]:
    """Run the research grid and return all results plus the optimizer winner."""

    active_dataset = dataset or prepare_research_dataset(
        start_date=start_date,
        end_date=end_date,
        months=months,
    )
    variant_list = list(variants or generate_strategy_variants())
    if max_variants is not None:
        variant_list = variant_list[: max(1, int(max_variants))]

    results: list[dict[str, object]] = []
    for variant in variant_list:
        results.append(
            run_strategy_variant(
                variant,
                dataset=active_dataset,
                mode=mode,
                max_hold_bars=max_hold_bars,
            )
        )

    if not results:
        raise RuntimeError("No strategy variants were evaluated.")

    best_result = max(results, key=_optimizer_score)
    return {
        "dataset_window": {
            "start_date": active_dataset.start_label,
            "end_date": active_dataset.end_label,
        },
        "tested_variants": len(results),
        "results": results,
        "best_configuration": best_result,
    }
