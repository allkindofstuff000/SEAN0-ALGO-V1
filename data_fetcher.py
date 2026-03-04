from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

import ccxt
import pandas as pd


@dataclass
class DataFetcher:
    """
    Fetches Binance futures candles for both 15m and 1h and returns only CLOSED candles.
    This class uses ccxt sync methods through asyncio.to_thread so the event loop is not blocked.
    """

    symbol: str = "XAUUSDT"
    min_candles: int = 300
    request_limit: int = 500
    max_retries: int = 5
    retry_base_seconds: float = 1.5

    def __post_init__(self) -> None:
        self.exchange = ccxt.binance(
            {
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            }
        )
        self._markets_loaded = False
        self._resolved_symbol: str | None = None

    async def fetch_dual_timeframes(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        candles_15m, candles_1h = await asyncio.gather(
            self.fetch_closed_candles("15m"),
            self.fetch_closed_candles("1h"),
        )
        return candles_15m, candles_1h

    async def fetch_closed_candles(self, timeframe: str) -> pd.DataFrame:
        await self._ensure_ready()
        ohlcv = await self._fetch_ohlcv_with_retry(timeframe=timeframe, limit=self.request_limit)
        frame = self._to_dataframe(ohlcv, timeframe)
        if len(frame) < self.min_candles:
            raise RuntimeError(
                f"Not enough closed candles on {timeframe}. "
                f"Expected >= {self.min_candles}, got {len(frame)}."
            )
        return frame.tail(self.min_candles)

    async def _ensure_ready(self) -> None:
        if self._markets_loaded:
            return
        await asyncio.to_thread(self.exchange.load_markets)
        self._resolved_symbol = self._resolve_symbol()
        self._markets_loaded = True
        print(f"[DataFetcher] Using Binance futures symbol: {self._resolved_symbol}")

    def _resolve_symbol(self) -> str:
        markets = self.exchange.markets or {}
        if self.symbol in markets:
            return self.symbol

        target = self.symbol.replace("/", "").replace(":", "").upper()
        for market in markets.values():
            if not market.get("contract", False):
                continue
            market_id = str(market.get("id", "")).replace("/", "").replace(":", "").upper()
            market_symbol = str(market.get("symbol", "")).replace("/", "").replace(":", "").upper()
            if target == market_id or target == market_symbol:
                return str(market["symbol"])

        raise ValueError(
            f"Symbol '{self.symbol}' was not found on Binance futures. "
            "Set SYMBOL in .env to a valid Binance futures market."
        )

    async def _fetch_ohlcv_with_retry(self, timeframe: str, limit: int) -> list[list[float]]:
        if self._resolved_symbol is None:
            raise RuntimeError("Exchange symbol is not initialized.")

        for attempt in range(1, self.max_retries + 1):
            try:
                data = await asyncio.to_thread(
                    self.exchange.fetch_ohlcv,
                    self._resolved_symbol,
                    timeframe,
                    None,
                    limit,
                )
                if not data:
                    raise RuntimeError(f"No OHLCV data returned for timeframe {timeframe}.")
                return data
            except (ccxt.NetworkError, ccxt.ExchangeError, RuntimeError) as error:
                wait_seconds = min(20.0, self.retry_base_seconds * (2 ** (attempt - 1)))
                print(
                    f"[DataFetcher] Fetch failed ({timeframe}, attempt {attempt}/{self.max_retries}): "
                    f"{error}"
                )
                if attempt >= self.max_retries:
                    raise
                await asyncio.sleep(wait_seconds)

        raise RuntimeError("Retry loop exited unexpectedly.")

    def _to_dataframe(self, ohlcv: list[list[float]], timeframe: str) -> pd.DataFrame:
        frame = pd.DataFrame(
            ohlcv,
            columns=["timestamp", "open", "high", "low", "close", "volume"],
        )
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        numeric_columns = ["open", "high", "low", "close", "volume"]
        frame[numeric_columns] = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
        frame = frame.dropna(subset=numeric_columns).sort_values("timestamp")
        frame = frame.drop_duplicates(subset=["timestamp"]).set_index("timestamp")

        timeframe_ms = int(self.exchange.parse_timeframe(timeframe) * 1000)
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        close_ms = (frame.index.view("int64") // 10**6) + timeframe_ms
        frame = frame[close_ms <= now_ms]
        return frame
