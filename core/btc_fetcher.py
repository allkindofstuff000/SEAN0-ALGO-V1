"""BTCUSD market-data fetcher for the RSI EMA BTC strategy.

Uses the Binance **public data mirror** ``data-api.binance.vision`` (symbol
``BTCUSDT``) rather than ``api.binance.com`` because the production VPS is in a
Binance-restricted region (api.binance.com returns HTTP 451 there). The mirror
serves market data only (no auth, no trading) and is not geo-blocked.

Returns candle frames in the SAME shape the rest of the engine expects
(``timestamp, open, high, low, close, volume`` — timestamps tz-aware UTC), so
the RSI EMA indicator engine / signal logic / simulator run on BTC unchanged.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import pandas as pd

LOGGER = logging.getLogger(__name__)

BTC_BASE = "https://data-api.binance.vision"
SYMBOL = "BTCUSDT"
DISPLAY_SYMBOL = "BTCUSD"

# Coinbase spot — fast (~0.1s) and real-time, and it matches TradingView's default
# BTC feed. Used for LIVE DISPLAY only (chart + header price + SSE stream). The
# Binance mirror above is CDN-cached (the in-progress candle lags), so it stays
# for backtests + the live bot (deeper history) but not the live chart.
COINBASE_BASE = "https://api.exchange.coinbase.com"
COINBASE_PRODUCT = "BTC-USD"
_CB_GRAN = {"1m": 60, "5m": 300, "15m": 900, "30m": 1800, "1h": 3600}

# dashboard timeframe -> Binance interval
_INTERVALS = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "1h": "1h", "4h": "4h"}
# interval -> milliseconds per bar (for range pagination)
_STEP_MS = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "4h": 14_400_000}
# OANDA/dashboard-style aliases (M5, M15, H1, …) -> canonical Binance interval keys
_TF_ALIAS = {"m1": "1m", "m5": "5m", "m15": "15m", "m30": "30m", "h1": "1h", "h4": "4h"}


def _norm_tf(tf: str) -> str:
    """Normalise a timeframe (accepts both '5m' and dashboard-style 'M5') -> '5m'."""
    key = str(tf).strip().lower()
    key = _TF_ALIAS.get(key, key)
    if key not in _INTERVALS:
        raise ValueError(f"Unsupported BTC timeframe: {tf}")
    return key


class BtcFetcher:
    """Thin, dependency-free BTCUSDT fetcher over the Binance data mirror."""

    def __init__(self, max_retries: int = 4, retry_base_seconds: float = 3.0, timeout: int = 20) -> None:
        self.max_retries = max_retries
        self.retry_base_seconds = retry_base_seconds
        self.timeout = timeout

    # ── HTTP ────────────────────────────────────────────────────────────────
    def _get(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{BTC_BASE}{path}?{urllib.parse.urlencode(params)}"
        headers = {"User-Agent": "SEAN0-ALGO-V1/1.0", "Accept": "application/json"}
        for attempt in range(1, self.max_retries + 1):
            try:
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError) as exc:
                LOGGER.warning("btc_fetch_failed attempt=%s/%s path=%s err=%s", attempt, self.max_retries, path, exc)
                if attempt >= self.max_retries:
                    raise RuntimeError(f"Binance mirror request failed ({path}): {exc}") from exc
                time.sleep(self.retry_base_seconds)
        raise RuntimeError("unexpected_btc_retry_exit")

    @staticmethod
    def _interval(tf: str) -> str:
        return _INTERVALS[_norm_tf(tf)]

    @staticmethod
    def _to_frame(raw: list[list[Any]]) -> pd.DataFrame:
        rows = [
            {
                "timestamp": pd.to_datetime(int(k[0]), unit="ms", utc=True),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
                "close_time": int(k[6]),
            }
            for k in raw
        ]
        return pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume", "close_time"])

    # ── Public API ──────────────────────────────────────────────────────────
    def fetch_klines(self, interval: str = "5m", limit: int = 300, closed_only: bool = True) -> pd.DataFrame:
        """Most-recent candles. Drops the still-forming last bar when closed_only."""
        iv = self._interval(interval)
        raw = self._get("/api/v3/klines", {"symbol": SYMBOL, "interval": iv, "limit": min(1000, int(limit) + 2)})
        df = self._to_frame(raw)
        if df.empty:
            raise RuntimeError("empty_btc_klines")
        if closed_only:
            now_ms = int(time.time() * 1000)
            df = df[df["close_time"] <= now_ms]
        df = (
            df.drop(columns=["close_time"])
            .sort_values("timestamp")
            .drop_duplicates(subset=["timestamp"])
            .reset_index(drop=True)
        )
        return df.tail(int(limit)).reset_index(drop=True)

    def fetch_live_price(self) -> dict[str, Any]:
        data = self._get("/api/v3/ticker/price", {"symbol": SYMBOL})
        return {
            "price": float(data["price"]),
            "time": pd.Timestamp.now(tz="UTC").isoformat(),
            "symbol": DISPLAY_SYMBOL,
            "initialized": True,
        }

    # ── Coinbase live display (fast + real-time; matches TradingView) ─────────
    def _coinbase_get(self, path: str) -> Any:
        url = f"{COINBASE_BASE}{path}"
        req = urllib.request.Request(url, headers={"User-Agent": "SEAN0-ALGO-V1/1.0", "Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(req, timeout=6) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def fetch_spot_price(self) -> dict[str, Any]:
        """Fast, real-time BTC spot from Coinbase (matches TradingView). Falls back
        to the slower Binance mirror ticker if Coinbase is unreachable."""
        try:
            data = self._coinbase_get(f"/products/{COINBASE_PRODUCT}/ticker")
            return {
                "price": float(data["price"]),
                "time": pd.Timestamp.now(tz="UTC").isoformat(),
                "symbol": DISPLAY_SYMBOL,
                "initialized": True,
                "source": "coinbase",
            }
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("coinbase ticker failed (%s); using binance mirror", exc)
            return self.fetch_live_price()

    def fetch_display_candles(self, interval: str = "5m", limit: int = 300) -> pd.DataFrame:
        """Recent candles from Coinbase (fast) for the live chart. Coinbase caps
        each /candles request at ~300 bars, so page back in parallel to cover
        `limit`. Falls back to the Binance mirror on failure / unsupported tf."""
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

        key = _norm_tf(interval)
        gran = _CB_GRAN.get(key)
        if gran is None:
            return self.fetch_klines(interval, limit, closed_only=False)

        pages = max(1, min(6, (int(limit) + 299) // 300))
        now = int(pd.Timestamp.now(tz="UTC").timestamp())
        span = 300 * gran  # seconds covered by one 300-bar page
        windows: list[tuple[int, int]] = []
        end = now
        for _ in range(pages):
            windows.append((end - span, end))
            end -= span

        def _page(win: tuple[int, int]) -> list[list[Any]]:
            s, e = win
            s_iso = pd.to_datetime(s, unit="s", utc=True).isoformat()
            e_iso = pd.to_datetime(e, unit="s", utc=True).isoformat()
            try:
                return self._coinbase_get(f"/products/{COINBASE_PRODUCT}/candles?granularity={gran}&start={s_iso}&end={e_iso}")
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("coinbase page failed (%s)", exc)
                return []

        raw_all: list[list[Any]] = []
        with ThreadPoolExecutor(max_workers=6) as ex:
            for r in ex.map(_page, windows):
                if r:
                    raw_all.extend(r)

        if not raw_all:
            LOGGER.warning("coinbase candles empty; using binance mirror")
            return self.fetch_klines(interval, limit, closed_only=False)
        # Coinbase rows: [time, low, high, open, close, volume], newest-first.
        rows = [
            {
                "timestamp": pd.to_datetime(int(c[0]), unit="s", utc=True),
                "open": float(c[3]),
                "high": float(c[2]),
                "low": float(c[1]),
                "close": float(c[4]),
                "volume": float(c[5]),
            }
            for c in raw_all
            if isinstance(c, list) and len(c) >= 6
        ]
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
        return df.tail(int(limit)).reset_index(drop=True)

    def fetch_range(self, start_utc: pd.Timestamp, end_utc: pd.Timestamp, interval: str = "5m") -> pd.DataFrame:
        """Historical candles over [start_utc, end_utc] for backtests.

        Pages are 1000 bars wide and their start times are computable up front
        (the grid is regular), so we fetch them CONCURRENTLY instead of walking a
        cursor serially. On the public mirror each request is ~1-3s, so a 50-day
        pull drops from ~50s (16 serial requests) to a few seconds.
        """
        from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

        iv = self._interval(interval)
        step = _STEP_MS[_norm_tf(interval)]
        start_ms = int(pd.Timestamp(start_utc).timestamp() * 1000)
        end_ms = int(pd.Timestamp(end_utc).timestamp() * 1000)
        page_span = 1000 * step  # ms covered by one 1000-bar page

        starts: list[int] = []
        cur = start_ms
        while cur < end_ms and len(starts) < 800:
            starts.append(cur)
            cur += page_span

        def _page(ps: int) -> list[list[Any]]:
            pe = min(ps + page_span, end_ms)
            try:
                return self._get(
                    "/api/v3/klines",
                    {"symbol": SYMBOL, "interval": iv, "startTime": ps, "endTime": pe, "limit": 1000},
                )
            except Exception as exc:  # noqa: BLE001 — one bad page shouldn't kill the run
                LOGGER.warning("btc page fetch failed (start=%s): %s", ps, exc)
                return []

        all_raw: list[list[Any]] = []
        with ThreadPoolExecutor(max_workers=8) as ex:
            for raw in ex.map(_page, starts):
                if raw:
                    all_raw.extend(raw)

        if not all_raw:
            raise RuntimeError("no_btc_candles_in_range")
        df = self._to_frame(all_raw).drop(columns=["close_time"])
        df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
        df = df[(df["timestamp"] >= pd.Timestamp(start_utc)) & (df["timestamp"] <= pd.Timestamp(end_utc))]
        return df.reset_index(drop=True)


if __name__ == "__main__":
    f = BtcFetcher()
    px = f.fetch_live_price()
    print("live:", px)
    recent = f.fetch_klines("5m", 5)
    print("recent 5 bars:\n", recent.tail())
