"""
Binance Candle Engine — ETH/USDT multi-timeframe streaming.
Follows the same architecture as OandaStreamEngine:
  - CandleStore for thread-safe candle storage
  - Subscriber pattern (asyncio.Queue) for event broadcasting
  - Same event types: init, tick, candle, status
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from core.binance_client import (
    BinanceStreamThread,
    fetch_klines,
    fetch_klines_paged,
    fetch_price,
    fetch_24hr,
)
from core.candle_engine import CandleStore

LOGGER = logging.getLogger("binance-engine")
ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "logs" / "eth_seed_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Binance interval strings → internal TF labels
INTERVAL_MAP = {
    "1m": "M1",
    "5m": "M5",
    "15m": "M15",
    "1h": "H1",
}

TF_TO_INTERVAL = {v: k for k, v in INTERVAL_MAP.items()}

# How many candles to seed per timeframe on startup (3 days of history)
SEED_LIMITS = {
    "M1": 4320,   # 3 days × 1440 min/day ÷ 1 min = 4320
    "M5": 864,    # 3 days × 1440 ÷ 5 = 864
    "M15": 288,   # 3 days × 1440 ÷ 15 = 288
    "H1": 72,     # 3 days × 24 = 72
}


def _safe_put(q: asyncio.Queue, payload: str) -> None:
    try:
        q.put_nowait(payload)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            q.put_nowait(payload)
        except asyncio.QueueFull:
            pass


class BinanceCandleEngine:
    """
    Multi-timeframe candle engine for Binance (ETH/USDT).
    Same interface as OandaStreamEngine — drop-in compatible for strategies.
    """

    def __init__(self, symbol: str = "ETHUSDT"):
        self.symbol = symbol
        self.store = CandleStore()

        # Subscriber pattern (same as OandaStreamEngine)
        self._subscribers: dict[int, asyncio.Queue] = {}
        self._sub_lock = threading.Lock()
        self._sub_counter = 0

        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._stream_thread: BinanceStreamThread | None = None
        self._history_loaded = False
        self.stream_status = "disconnected"

        # Live price tracking
        self._live_price: float = 0.0
        self._live_time: int = 0

    @classmethod
    def from_env(cls) -> "BinanceCandleEngine":
        symbol = os.getenv("ETH_SYMBOL", "ETHUSDT").strip()
        return cls(symbol=symbol)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """Seed historical candles, then start WebSocket stream."""
        self._loop = loop
        self._running = True

        LOGGER.info("[ETH] seeding history for %s (M1/M5/M15/H1)…", self.symbol)

        # Fetch all timeframes in parallel using thread pool
        results = await asyncio.gather(
            *[
                asyncio.to_thread(self._seed_timeframe, tf)
                for tf in SEED_LIMITS
            ],
            return_exceptions=True,
        )

        for tf, result in zip(SEED_LIMITS.keys(), results):
            if isinstance(result, Exception):
                LOGGER.error("[ETH] seed %s failed: %s", tf, result)

        self._history_loaded = True
        LOGGER.info("[ETH] history loaded — broadcasting init")

        # Broadcast init event
        self._broadcast(self._build_init_event())

        # Start WebSocket stream
        symbol_lower = self.symbol.lower()
        streams = [
            f"{symbol_lower}@kline_1m",
            f"{symbol_lower}@kline_5m",
            f"{symbol_lower}@kline_15m",
            f"{symbol_lower}@kline_1h",
        ]

        self._stream_thread = BinanceStreamThread(
            streams=streams,
            on_message=self._on_ws_kline,
            symbol=self.symbol,
        )
        self._stream_thread.start()
        self._set_status("connected")

        LOGGER.info("[ETH] candle engine started — %s", self.symbol)

    def _seed_timeframe(self, tf: str) -> None:
        """Fetch historical candles for one timeframe (3 days)."""
        interval = TF_TO_INTERVAL[tf]
        total = SEED_LIMITS[tf]
        candles: list[dict[str, Any]] = []
        try:
            candles = fetch_klines_paged(
                symbol=self.symbol,
                interval=interval,
                total=total,
            )
        except Exception as exc:
            LOGGER.warning("[ETH] %s remote seed failed: %s", tf, exc)

        if not candles:
            candles = self._load_seed_cache(tf)
            if candles:
                LOGGER.info("[ETH] %s restored from local cache: %d candles", tf, len(candles))

        if candles:
            self.store.seed_historical(tf, candles)
            self._save_seed_cache(tf, candles)
            LOGGER.info("[ETH] %s loaded: %d candles (3 days)", tf, len(candles))
        else:
            LOGGER.warning("[ETH] no candles returned for %s", tf)

    # ── WebSocket handler ─────────────────────────────────────────────────────

    def _on_ws_kline(self, stream: str, data: dict[str, Any]) -> None:
        """Called by BinanceStreamThread on each kline message."""
        k = data.get("k", {})
        if not k:
            return

        # Determine timeframe
        interval = k.get("i", "")
        tf = INTERVAL_MAP.get(interval)
        if not tf:
            return

        candle = {
            "time": int(k["t"]) // 1000,  # open time ms → seconds
            "open": float(k["o"]),
            "high": float(k["h"]),
            "low": float(k["l"]),
            "close": float(k["c"]),
            "volume": float(k["v"]),
        }
        is_closed = k.get("x", False)

        # Update live price
        self._live_price = candle["close"]
        self._live_time = int(time.time())

        if is_closed:
            # Candle completed
            self.store.push_completed(tf, candle)
            self.store.set_current(tf, None)
            self._save_seed_cache(tf, self.store.get_all(tf))
            self._broadcast({
                "type": "candle",
                "timeframe": tf,
                "candle": candle,
            })
        else:
            # Forming candle (tick update)
            self.store.set_current(tf, candle)

        # Always broadcast tick with current price
        self._broadcast({
            "type": "tick",
            "price": candle["close"],
            "time": candle["time"],
            "timeframe": tf,
            "candle": candle,
            "isClosed": is_closed,
        })

    # ── Subscriber pattern (identical to OandaStreamEngine) ───────────────────

    def subscribe(self) -> tuple[int, asyncio.Queue]:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        with self._sub_lock:
            sid = self._sub_counter
            self._sub_counter += 1
            self._subscribers[sid] = q
        return sid, q

    def unsubscribe(self, sub_id: int) -> None:
        with self._sub_lock:
            self._subscribers.pop(sub_id, None)

    def _broadcast(self, event: dict) -> None:
        if self._loop is None or self._loop.is_closed():
            return
        payload = json.dumps(event, default=str)
        with self._sub_lock:
            subs = list(self._subscribers.values())
        for q in subs:
            try:
                self._loop.call_soon_threadsafe(_safe_put, q, payload)
            except RuntimeError:
                pass

    def _set_status(self, status: str) -> None:
        self.stream_status = status
        self._broadcast({"type": "status", "status": status})

    # ── Event builders ────────────────────────────────────────────────────────

    def _build_init_event(self) -> dict[str, Any]:
        """Build the init event with all candle data (same format as OANDA)."""
        candles = {}
        for tf in SEED_LIMITS:
            candles[tf] = self.store.get_all(tf)
        return {
            "type": "init",
            "symbol": self.symbol,
            "candles": candles,
        }

    # ── Public accessors ──────────────────────────────────────────────────────

    def get_price(self) -> dict[str, Any]:
        return {"price": self._live_price, "time": self._live_time}

    def get_status(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "status": self.stream_status,
            "history_loaded": self._history_loaded,
            "running": self._running,
            "price": self._live_price,
            "candle_counts": {
                tf: len(self.store.get_candles(tf)) for tf in SEED_LIMITS
            },
        }

    # ── Seed cache ───────────────────────────────────────────────────────────

    def _cache_path(self, tf: str) -> Path:
        return CACHE_DIR / f"{self.symbol.lower()}_{tf.lower()}.json"

    def _load_seed_cache(self, tf: str) -> list[dict[str, Any]]:
        path = self._cache_path(tf)
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                return []
            candles = [c for c in raw if isinstance(c, dict) and "time" in c]
            candles.sort(key=lambda c: c["time"])
            return candles[-SEED_LIMITS[tf]:]
        except Exception as exc:
            LOGGER.debug("[ETH] failed reading cache %s: %s", path.name, exc)
            return []

    def _save_seed_cache(self, tf: str, candles: list[dict[str, Any]]) -> None:
        path = self._cache_path(tf)
        try:
            trimmed = candles[-SEED_LIMITS[tf]:]
            path.write_text(json.dumps(trimmed, separators=(",", ":")), encoding="utf-8")
        except Exception as exc:
            LOGGER.debug("[ETH] failed writing cache %s: %s", path.name, exc)
