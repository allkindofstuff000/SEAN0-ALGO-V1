from __future__ import annotations

import logging
from dataclasses import dataclass, field

import pandas as pd

from bot.data.data_cleaner import clean_dataframe
from bot.data.data_fetcher import DataFetcher


LOGGER = logging.getLogger(__name__)


@dataclass
class TimeframeManager:
    """
    Coordinates multi-timeframe requests and cleaning.
    """

    fetcher: DataFetcher = field(default_factory=DataFetcher)
    minimum_candles: int = 300

    def fetch_timeframes(
        self,
        symbol: str,
        timeframes: list[str],
        limit: int,
    ) -> dict[str, pd.DataFrame]:
        datasets: dict[str, pd.DataFrame] = {}

        for timeframe in timeframes:
            LOGGER.info("fetching timeframe=%s symbol=%s limit=%s", timeframe, symbol, limit)
            raw = self.fetcher.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
            cleaned = clean_dataframe(raw)

            if len(cleaned) < self.minimum_candles:
                raise RuntimeError(
                    f"Not enough clean candles for {timeframe}. "
                    f"Need >= {self.minimum_candles}, got {len(cleaned)}."
                )

            datasets[timeframe] = cleaned.tail(limit).reset_index(drop=True)

        return datasets
