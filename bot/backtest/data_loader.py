from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from bot.data.data_fetcher import DataFetcher


LOGGER = logging.getLogger(__name__)


@dataclass
class DataLoader:
    """
    Load historical OHLCV data for backtests from CSV or an exchange API.
    """

    exchange_name: str = "binance"
    batch_limit: int = 1000

    def load(
        self,
        *,
        csv_path: str | Path | None = None,
        symbol: str,
        timeframe: str,
        start_date: str | datetime | None = None,
        end_date: str | datetime | None = None,
    ) -> pd.DataFrame:
        if csv_path:
            frame = self.load_csv(csv_path=csv_path, start_date=start_date, end_date=end_date)
        else:
            frame = self.load_exchange(
                symbol=symbol,
                timeframe=timeframe,
                start_date=start_date,
                end_date=end_date,
            )
        return self._finalize(frame)

    def load_csv(
        self,
        *,
        csv_path: str | Path,
        start_date: str | datetime | None = None,
        end_date: str | datetime | None = None,
    ) -> pd.DataFrame:
        path = Path(csv_path)
        if not path.exists():
            raise FileNotFoundError(f"Historical CSV not found: {path}")

        frame = pd.read_csv(path)
        frame = self._normalize_columns(frame)
        frame = self._apply_date_filters(frame, start_date=start_date, end_date=end_date)
        LOGGER.info("loaded_csv_data path=%s candles=%s", path, len(frame))
        return frame

    def load_exchange(
        self,
        *,
        symbol: str,
        timeframe: str,
        start_date: str | datetime | None = None,
        end_date: str | datetime | None = None,
    ) -> pd.DataFrame:
        fetcher = DataFetcher(exchange_name=self.exchange_name, default_limit=self.batch_limit)
        resolved_symbol = fetcher._resolve_symbol(symbol)
        timeframe_ms = int(fetcher.exchange.parse_timeframe(timeframe) * 1000)
        start_ts = self._coerce_timestamp(start_date) if start_date else None
        end_ts = self._coerce_timestamp(end_date) if end_date else datetime.now(timezone.utc)
        since_ms = int(start_ts.timestamp() * 1000) if start_ts else None
        end_ms = int(end_ts.timestamp() * 1000) if end_ts else None

        all_rows: list[list[float]] = []
        while True:
            rows = fetcher.exchange.fetch_ohlcv(
                resolved_symbol,
                timeframe=timeframe,
                since=since_ms,
                limit=self.batch_limit,
            )
            if not rows:
                break

            all_rows.extend(rows)
            last_open_ms = int(rows[-1][0])
            next_since_ms = last_open_ms + timeframe_ms

            if since_ms is not None and next_since_ms <= since_ms:
                break
            since_ms = next_since_ms

            if end_ms is not None and last_open_ms >= end_ms:
                break
            if len(rows) < self.batch_limit:
                break

            time.sleep(max(fetcher.exchange.rateLimit / 1000.0, 0.05))

        frame = fetcher._to_dataframe(all_rows)
        frame = fetcher._drop_unfinished_candle(frame, timeframe=timeframe)
        frame = self._normalize_columns(frame)
        frame = self._apply_date_filters(frame, start_date=start_date, end_date=end_date)
        LOGGER.info(
            "loaded_exchange_data symbol=%s resolved=%s timeframe=%s candles=%s start=%s end=%s",
            symbol,
            resolved_symbol,
            timeframe,
            len(frame),
            start_date,
            end_date,
        )
        return frame

    def _finalize(self, frame: pd.DataFrame) -> pd.DataFrame:
        frame = self._normalize_columns(frame)
        required = ["timestamp", "open", "high", "low", "close", "volume"]
        missing = [column for column in required if column not in frame.columns]
        if missing:
            raise ValueError(f"Historical data is missing required columns: {missing}")

        out = frame.loc[:, required].copy()
        out["timestamp"] = self._to_utc_timestamp(out["timestamp"])
        for column in ["open", "high", "low", "close", "volume"]:
            out[column] = pd.to_numeric(out[column], errors="coerce")

        out = out.dropna(subset=required)
        out = out.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
        return out

    def _normalize_columns(self, frame: pd.DataFrame) -> pd.DataFrame:
        out = frame.copy()
        out.columns = [str(column).strip().lower() for column in out.columns]
        return out.rename(columns={"date": "timestamp", "datetime": "timestamp"})

    def _apply_date_filters(
        self,
        frame: pd.DataFrame,
        *,
        start_date: str | datetime | None,
        end_date: str | datetime | None,
    ) -> pd.DataFrame:
        if "timestamp" not in frame.columns:
            return frame

        out = frame.copy()
        out["timestamp"] = self._to_utc_timestamp(out["timestamp"])

        if start_date is not None:
            start_ts = self._coerce_timestamp(start_date)
            out = out[out["timestamp"] >= start_ts]
        if end_date is not None:
            end_ts = self._coerce_timestamp(end_date)
            out = out[out["timestamp"] <= end_ts]

        return out.reset_index(drop=True)

    @staticmethod
    def _to_utc_timestamp(series: pd.Series) -> pd.Series:
        if pd.api.types.is_numeric_dtype(series):
            sample = float(series.dropna().iloc[0]) if not series.dropna().empty else 0.0
            unit = "ms" if abs(sample) >= 1e11 else "s"
            return pd.to_datetime(series, unit=unit, utc=True, errors="coerce")
        return pd.to_datetime(series, utc=True, errors="coerce")

    @staticmethod
    def _coerce_timestamp(value: str | datetime) -> datetime:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")
        return timestamp.to_pydatetime()
