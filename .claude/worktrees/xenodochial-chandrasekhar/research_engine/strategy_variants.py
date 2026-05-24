from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import product
from typing import Iterable


DEFAULT_RSI_THRESHOLDS = (45.0, 50.0, 55.0, 60.0)
DEFAULT_ATR_MULTIPLIERS = (1.5, 2.0, 2.5)
DEFAULT_BREAKOUT_STRENGTH_MULTIPLIERS = (1.0, 1.2, 1.5)


@dataclass(frozen=True, slots=True)
class StrategyVariant:
    rsi_threshold: float
    atr_multiplier: float
    breakout_strength_multiplier: float

    @property
    def variant_id(self) -> str:
        return (
            f"rsi_{self.rsi_threshold:g}_"
            f"atr_{self.atr_multiplier:g}_"
            f"breakout_{self.breakout_strength_multiplier:g}"
        )

    def to_dict(self) -> dict[str, float | str]:
        payload = asdict(self)
        payload["variant_id"] = self.variant_id
        return payload


def _normalize(values: Iterable[float]) -> tuple[float, ...]:
    return tuple(float(value) for value in values)


def generate_strategy_variants(
    *,
    rsi_thresholds: Iterable[float] = DEFAULT_RSI_THRESHOLDS,
    atr_multipliers: Iterable[float] = DEFAULT_ATR_MULTIPLIERS,
    breakout_strength_multipliers: Iterable[float] = DEFAULT_BREAKOUT_STRENGTH_MULTIPLIERS,
) -> list[StrategyVariant]:
    """Generate a deterministic grid of strategy parameter combinations."""

    variants = [
        StrategyVariant(
            rsi_threshold=rsi_threshold,
            atr_multiplier=atr_multiplier,
            breakout_strength_multiplier=breakout_strength_multiplier,
        )
        for rsi_threshold, atr_multiplier, breakout_strength_multiplier in product(
            _normalize(rsi_thresholds),
            _normalize(atr_multipliers),
            _normalize(breakout_strength_multipliers),
        )
    ]
    return sorted(variants, key=lambda variant: variant.variant_id)
