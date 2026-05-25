"""SEAN0-ALGO · Candle Engine  (production)
==========================================

Reliable OHLCV pipeline for XAU/USD via OANDA:

  1. On startup – per timeframe:
     a) Load closed candles from MongoDB (last DAYS_HISTORY days)
     b) Detect gap: last DB candle timestamp → now
     c) Backfill gap from OANDA REST API (chunked, ≤5000 per request)
     d) Upsert all new candles to MongoDB (no duplicates)
     e) Seed in-memory ring buffer from merged dataset

  2. Live OANDA pricing stream:
     a) Every tick → update forming (live) candle for all 4 TFs
     b) On period close → finalize candle, upsert to MongoDB, broadcast
     c) No synthetic "flat" gap candles – gaps are filled from OANDA, not fabricated

  3. On stream reconnect:
     a) Detect gap since last known candle
     b) Backfill from OANDA REST
     c) Re-emit init event with full history + forming candle

MongoDB schema  (collection: candles)
  { symbol, timeframe, timestamp, open, high, low, close, volume, is_closed }
  Unique index: (symbol, timeframe, timestamp)
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import math
import ssl
import threading
import time
import urllib.request
import urllib.error
import urllib.parse
from collections import deque
from typing import Any

import pandas as pd

LOGGER = logging.getLogger("xau.candle_engine")

TIMEFRAMES: dict[str, int] = {   # label → period in seconds
    "M1":    60,
    "M5":   300,
    "M15":  900,
    "H1":  3600,
}

DAYS_HISTORY   = 7
OANDA_MAX_COUNT = 5_000
SYMBOL          = "XAU_USD"

TF_MAX_CANDLES: dict[str, int] = {
    "M1":  11_000,  # 7 days × 1 440  = 10 080 M1 candles
    "M5":   2_200,  # 7 days × 288    =  2 016 M5
    "M15":    800,  # 7 days × 96     =    672 M15
    "H1":     200,  # 7 days × 24     =    168 H1
}


# ── MongoDB Candle Persistence ────────────────────────────────────────────────

class CandleDB:
    """
    MongoDB-backed candle store.
    All operations are best-effort – failures are logged and silently ignored
    so the engine continues to work without a DB connection.
    """

    def __init__(self) -> None:
        self._col   = None
        self._ready = False
        self._connect()

    def _connect(self) -> None:
        import os
        uri     = os.getenv("MONGODB_URI", "").strip()
        db_name = os.getenv("MONGODB_DB",  "sean0_algo").strip()
        if not uri:
            LOGGER.warning("[CandleDB] MONGODB_URI not set — no persistence")
            return
        try:
            from pymongo import MongoClient, ASCENDING
            client = MongoClient(uri, serverSelectionTimeoutMS=5_000)
            client.server_info()          # raises if unreachable
            col = client[db_name]["candles"]
            col.create_index(
                [("symbol", ASCENDING), ("timeframe", ASCENDING), ("timestamp", ASCENDING)],
                unique=True, background=True, name="sym_tf_ts",
            )
            self._col   = col
            self._ready = True
            LOGGER.info("[CandleDB] connected  db=%s  col=candles", db_name)
        except Exception as exc:
            LOGGER.warning("[CandleDB] unavailable: %s", exc)

    @property
    def ready(self) -> bool:
        return self._ready

    # ── Write ─────────────────────────────────────────────────────────────────

    def upsert_many(self, symbol: str, tf: str, candles: list[dict]) -> int:
        """Bulk upsert; returns number of documents written (upserted + modified)."""
        if not self._ready or not candles:
            return 0
        from pymongo import UpdateOne
        ops = [
            UpdateOne(
                {"symbol": symbol, "timeframe": tf, "timestamp": c["timestamp"]},
                {"$set": {
                    "symbol":    symbol,
                    "timeframe": tf,
                    "timestamp": c["timestamp"],
                    "open":      c["open"],
                    "high":      c["high"],
                    "low":       c["low"],
                    "close":     c["close"],
                    "volume":    c.get("volume", 0),
                    "is_closed": c.get("is_closed", True),
                }},
                upsert=True,
            )
            for c in candles
        ]
        try:
            r = self._col.bulk_write(ops, ordered=False)
            return r.upserted_count + r.modified_count
        except Exception as exc:
            LOGGER.debug("[CandleDB] bulk_write: %s", exc)
            return 0

    def upsert_one(self, symbol: str, tf: str, candle: dict) -> None:
        if not self._ready:
            return
        try:
            self._col.update_one(
                {"symbol": symbol, "timeframe": tf, "timestamp": candle["timestamp"]},
                {"$set": {
                    "symbol":    symbol,
                    "timeframe": tf,
                    "timestamp": candle["timestamp"],
                    "open":      candle["open"],
                    "high":      candle["high"],
                    "low":       candle["low"],
                    "close":     candle["close"],
                    "volume":    candle.get("volume", 0),
                    "is_closed": candle.get("is_closed", True),
                }},
                upsert=True,
            )
        except Exception as exc:
            LOGGER.debug("[CandleDB] upsert_one: %s", exc)

    # ── Read ──────────────────────────────────────────────────────────────────

    def load_recent(
        self,
        symbol: str,
        tf: str,
        limit: int,
        since_unix: int = 0,
    ) -> list[dict]:
        """
        Load the most-recent *limit* CLOSED candles (newest last).
        Optionally filter to candles with timestamp >= since_unix.
        """
        if not self._ready:
            return []
        try:
            query: dict[str, Any] = {
                "symbol":    symbol,
                "timeframe": tf,
                "is_closed": True,
            }
            if since_unix:
                query["timestamp"] = {"$gte": since_unix}
            docs = list(self._col.find(
                query,
                {"_id": 0, "symbol": 0, "timeframe": 0},
                sort=[("timestamp", -1)],
                limit=limit,
            ))
            docs.reverse()
            return docs
        except Exception as exc:
            LOGGER.debug("[CandleDB] load_recent: %s", exc)
            return []

    def last_closed_ts(self, symbol: str, tf: str) -> int | None:
        """Return the timestamp of the most recent CLOSED candle, or None."""
        if not self._ready:
            return None
        try:
            doc = self._col.find_one(
                {"symbol": symbol, "timeframe": tf, "is_closed": True},
                sort=[("timestamp", -1)],
            )
            return int(doc["timestamp"]) if doc else None
        except Exception:
            return None


# ── CandleStore ────────────────────────────────────────────────────────────────

class CandleStore:
    """
    Thread-safe in-memory ring buffer.
    Holds closed candles (deque, capped at TF_MAX_CANDLES) and the
    currently forming candle per timeframe.
    """

    def __init__(self) -> None:
        self._completed: dict[str, deque] = {
            tf: deque(maxlen=TF_MAX_CANDLES[tf]) for tf in TIMEFRAMES
        }
        self._current: dict[str, dict | None] = {tf: None for tf in TIMEFRAMES}
        self._lock = threading.Lock()

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_candles(self, timeframe: str) -> list[dict]:
        """Closed candles only."""
        with self._lock:
            return list(self._completed[timeframe])

    def get_current(self, timeframe: str) -> dict | None:
        with self._lock:
            c = self._current[timeframe]
            return dict(c) if c else None

    def get_all(self, timeframe: str) -> list[dict]:
        """Closed candles + forming candle (for SSE init / API response)."""
        with self._lock:
            result = list(self._completed[timeframe])
            if self._current[timeframe]:
                result.append(dict(self._current[timeframe]))
            return result

    def last_completed_ts(self, timeframe: str) -> int | None:
        with self._lock:
            return self._completed[timeframe][-1]["time"] if self._completed[timeframe] else None

    def all_current(self) -> dict[str, dict | None]:
        with self._lock:
            return {tf: (dict(c) if c else None) for tf, c in self._current.items()}

    # ── Write ─────────────────────────────────────────────────────────────────

    def seed_historical(self, timeframe: str, candles: list[dict]) -> None:
        """Replace entire store for this TF (called at startup)."""
        with self._lock:
            self._completed[timeframe].clear()
            for c in candles:
                self._completed[timeframe].append(c)

    def merge_historical(self, timeframe: str, candles: list[dict]) -> int:
        """
        Append candles that don't already exist (by timestamp).
        Returns number of candles added.
        """
        added = 0
        with self._lock:
            existing_ts = {c["time"] for c in self._completed[timeframe]}
            for c in candles:
                if c["time"] not in existing_ts:
                    self._completed[timeframe].append(c)
                    existing_ts.add(c["time"])
                    added += 1
        return added

    def push_completed(self, timeframe: str, candle: dict) -> None:
        with self._lock:
            self._completed[timeframe].append(candle)

    def set_current(self, timeframe: str, candle: dict | None) -> None:
        with self._lock:
            self._current[timeframe] = candle


# ── CandleBuilder ──────────────────────────────────────────────────────────────

class CandleBuilder:
    """
    Consumes live price ticks and maintains one forming candle per TF.

    Key difference from old version:
      * Does NOT fabricate synthetic gap-filling candles.
        Gaps are backfilled from OANDA REST, not invented from the last close.
    """

    def __init__(self, store: CandleStore) -> None:
        self._store   = store
        self._current: dict[str, dict | None] = {tf: None for tf in TIMEFRAMES}
        self._lock    = threading.Lock()

    def process_tick(self, mid: float, tick_unix: float) -> list[dict]:
        """
        Process one price tick.
        Returns list of freshly COMPLETED candles (may be empty or multi-TF).
        """
        completed: list[dict] = []
        with self._lock:
            for tf, period in TIMEFRAMES.items():
                period_start = int(math.floor(tick_unix / period)) * period
                cur = self._current[tf]

                if cur is None:
                    # First tick for this TF
                    self._current[tf] = _new_candle(tf, period_start, mid)

                elif cur["time"] == period_start:
                    # Still in the same period — update OHLCV
                    cur["high"]   = max(cur["high"],  mid)
                    cur["low"]    = min(cur["low"],   mid)
                    cur["close"]  = mid
                    cur["volume"] += 1

                else:
                    # New period started — close the old candle
                    old = dict(cur)
                    old["complete"]  = True
                    old["is_closed"] = True
                    self._store.push_completed(tf, old)
                    completed.append(old)

                    # Start a fresh candle — no fake gap fill
                    self._current[tf] = _new_candle(tf, period_start, mid)

                # Always sync store's live candle view
                self._store.set_current(tf, dict(self._current[tf]))

        return completed

    def snapshot_current(self) -> dict[str, dict | None]:
        with self._lock:
            return {tf: (dict(c) if c else None) for tf, c in self._current.items()}


def _new_candle(tf: str, period_start: int, price: float) -> dict:
    return {
        "time":      period_start,
        "open":      price,
        "high":      price,
        "low":       price,
        "close":     price,
        "volume":    1,
        "complete":  False,
        "is_closed": False,
        "timeframe": tf,
    }


# ── OANDA REST helpers ─────────────────────────────────────────────────────────

def _ts_to_rfc3339(unix: float) -> str:
    return _dt.datetime.utcfromtimestamp(unix).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


def _fetch_oanda_candles(
    api_key:     str,
    base_url:    str,
    granularity: str,
    count:       int   | None = None,
    from_unix:   float | None = None,
    to_unix:     float | None = None,
) -> list[dict]:
    """
    Fetch CLOSED candles from OANDA REST API.
    Returns [] on any error instead of raising (caller decides).
    """
    params: list[str] = ["price=M", f"granularity={granularity}"]
    if from_unix is not None:
        params.append(f"from={urllib.parse.quote(_ts_to_rfc3339(from_unix))}")
    if to_unix is not None:
        params.append(f"to={urllib.parse.quote(_ts_to_rfc3339(to_unix))}")
    if count is not None:
        params.append(f"count={min(count, OANDA_MAX_COUNT)}")

    url = f"{base_url}/instruments/{SYMBOL}/candles?{'&'.join(params)}"
    headers = {
        "Authorization":          f"Bearer {api_key}",
        "Accept-Datetime-Format": "RFC3339",
        "User-Agent":             "SEAN0-ALGO-V1/1.0",
    }
    ssl_ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=headers, method="GET")

    try:
        with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read(300).decode("utf-8", errors="replace")
        LOGGER.warning("[OANDA REST] HTTP %s  gran=%s  %s", exc.code, granularity, body[:200])
        return []
    except Exception as exc:
        LOGGER.warning("[OANDA REST] error  gran=%s  %s", granularity, exc)
        return []

    candles: list[dict] = []
    for c in payload.get("candles", []):
        if not c.get("complete", True):
            continue        # skip the forming (incomplete) candle
        mid = c.get("mid") or c.get("bid") or c.get("ask") or {}
        if not mid:
            continue
        try:
            ts_unix = int(pd.Timestamp(c["time"]).timestamp())
            candles.append({
                "time":      ts_unix,
                "open":      float(mid.get("o", 0)),
                "high":      float(mid.get("h", 0)),
                "low":       float(mid.get("l", 0)),
                "close":     float(mid.get("c", 0)),
                "volume":    int(c.get("volume", 0)),
                "complete":  True,
                "is_closed": True,
            })
        except (ValueError, KeyError):
            continue
    return candles


def _fetch_range_chunked(
    api_key: str, base_url: str, granularity: str,
    from_unix: float, to_unix: float,
) -> list[dict]:
    """
    Recursively splits large date ranges into ≤ OANDA_MAX_COUNT chunks.
    Deduplicates and sorts the final result by timestamp.
    """
    period = TIMEFRAMES.get(granularity, 60)
    needed = int((to_unix - from_unix) / period) + 20

    if needed <= OANDA_MAX_COUNT:
        return _fetch_oanda_candles(
            api_key, base_url, granularity,
            from_unix=from_unix, to_unix=to_unix,
        )

    mid = from_unix + (to_unix - from_unix) / 2
    a   = _fetch_range_chunked(api_key, base_url, granularity, from_unix, mid)
    b   = _fetch_range_chunked(api_key, base_url, granularity, mid,       to_unix)

    seen: set[int] = set()
    merged: list[dict] = []
    for c in a + b:
        if c["time"] not in seen:
            seen.add(c["time"])
            merged.append(c)
    merged.sort(key=lambda x: x["time"])
    return merged


def _discover_account_id(api_key: str, base_url: str) -> str:
    url = f"{base_url}/accounts"
    headers = {
        "Authorization":          f"Bearer {api_key}",
        "Accept-Datetime-Format": "RFC3339",
        "User-Agent":             "SEAN0-ALGO-V1/1.0",
    }
    ssl_ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    accounts = data.get("accounts", [])
    if not accounts:
        raise RuntimeError("No OANDA accounts found for this API key.")
    return accounts[0]["id"]


# ── History Seeder ─────────────────────────────────────────────────────────────

def _seed_timeframe(
    tf:       str,
    store:    CandleStore,
    db:       CandleDB,
    api_key:  str,
    api_base: str,
) -> None:
    """
    Full startup / reconnect seed for one timeframe:

      1. Load last DAYS_HISTORY days of closed candles from MongoDB.
      2. Detect gap: (last stored timestamp + 1 period) → now.
      3. If gap > 2 periods → fetch from OANDA, upsert to MongoDB.
      4. Merge into in-memory store (no duplicates).
    """
    period    = TIMEFRAMES[tf]
    now_ts    = int(time.time())
    cutoff    = now_ts - DAYS_HISTORY * 86_400
    limit     = TF_MAX_CANDLES[tf]

    # ── 1. Load from MongoDB ─────────────────────────────────────────────────
    db_docs = db.load_recent(SYMBOL, tf, limit=limit, since_unix=cutoff)
    if db_docs:
        # Convert MongoDB doc format → store format
        store_candles = []
        for d in db_docs:
            store_candles.append({
                "time":      d["timestamp"],
                "open":      d["open"],
                "high":      d["high"],
                "low":       d["low"],
                "close":     d["close"],
                "volume":    d.get("volume", 0),
                "complete":  True,
                "is_closed": True,
            })
        store.seed_historical(tf, store_candles)
        LOGGER.info("[%s] loaded %d candles from MongoDB", tf, len(store_candles))

    # ── 2. Detect gap ────────────────────────────────────────────────────────
    last_ts = store.last_completed_ts(tf)

    if last_ts is None:
        fetch_from = float(cutoff)
        LOGGER.info("[%s] no history — fetching full %d-day window from OANDA", tf, DAYS_HISTORY)
    else:
        expected_next = last_ts + period
        gap_periods   = (now_ts - expected_next) / period

        if gap_periods < 2:
            LOGGER.info("[%s] up to date (last=%s)", tf,
                        _dt.datetime.utcfromtimestamp(last_ts).strftime("%H:%M UTC"))
            return   # nothing to do

        fetch_from = float(expected_next)
        LOGGER.info(
            "[%s] gap detected: last=%s  gap=%.0f periods — backfilling from OANDA",
            tf,
            _dt.datetime.utcfromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M UTC"),
            gap_periods,
        )

    # ── 3. Fetch from OANDA ──────────────────────────────────────────────────
    if not api_key:
        LOGGER.warning("[%s] no OANDA_API_KEY — cannot backfill", tf)
        return

    raw = _fetch_range_chunked(
        api_key, api_base, tf,
        from_unix = fetch_from,
        to_unix   = float(now_ts),
    )

    if not raw:
        # Expected on weekends when market is closed — suppress to debug level
        import datetime as _dtmod
        _now = _dtmod.datetime.now(_dtmod.timezone.utc)
        _wd = _now.weekday()
        _is_weekend = (_wd == 4 and _now.hour >= 22) or _wd == 5 or (_wd == 6 and _now.hour < 22)
        if _is_weekend:
            LOGGER.debug("[%s] No candles (market closed — weekend)", tf)
        else:
            LOGGER.warning("[%s] OANDA returned 0 candles for backfill window", tf)
        return

    LOGGER.info("[%s] OANDA returned %d candles", tf, len(raw))

    # ── 4. Upsert to MongoDB ─────────────────────────────────────────────────
    db_rows = [
        {
            "timestamp": c["time"],
            "open":      c["open"],
            "high":      c["high"],
            "low":       c["low"],
            "close":     c["close"],
            "volume":    c.get("volume", 0),
            "is_closed": True,
        }
        for c in raw
    ]
    n_written = db.upsert_many(SYMBOL, tf, db_rows)
    LOGGER.info("[%s] upserted %d/%d candles to MongoDB", tf, n_written, len(db_rows))

    # ── 5. Merge into memory ─────────────────────────────────────────────────
    added = store.merge_historical(tf, raw)
    total = len(store.get_candles(tf))
    LOGGER.info("[%s] store: +%d merged → %d total", tf, added, total)


# ── OandaStreamEngine ──────────────────────────────────────────────────────────

class OandaStreamEngine:
    """
    Singleton multi-TF candle engine.

    Startup flow:
      _fetch_and_seed (all TFs in parallel via asyncio.gather)
        → _seed_timeframe per TF
      _broadcast(init)
      _stream_loop (daemon thread)
        → _connect_and_read
        → on disconnect: _backfill_gaps + _broadcast(init) + reconnect

    External interface (unchanged from previous version):
      engine.store.get_candles(tf)   → list[dict]
      engine.subscribe()             → (sub_id, asyncio.Queue)
      engine.unsubscribe(sub_id)
      engine.stream_status           → str
      engine._history_loaded         → bool
    """

    def __init__(
        self,
        api_key:     str,
        account_id:  str,
        api_base:    str,
        stream_base: str,
    ) -> None:
        self.api_key     = api_key
        self.account_id  = account_id
        self.api_base    = api_base.rstrip("/")
        self.stream_base = stream_base.rstrip("/")

        self.store   = CandleStore()
        self.builder = CandleBuilder(self.store)
        self._db     = CandleDB()

        self._subscribers:   dict[int, asyncio.Queue] = {}
        self._sub_lock       = threading.Lock()
        self._sub_counter    = 0
        self._loop:          asyncio.AbstractEventLoop | None = None
        self._running        = False
        self._stream_thread: threading.Thread | None = None
        self._history_loaded = False
        self.stream_status   = "disconnected"

    # ── Factory ───────────────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "OandaStreamEngine":
        import os
        api_key = os.getenv("OANDA_API_KEY", "").strip()
        env     = os.getenv("OANDA_ENV",     "practice").strip().lower()
        raw_api = os.getenv("OANDA_API_URL", "").strip().rstrip("/")

        if raw_api:
            api_base = raw_api if "/v3" in raw_api else raw_api + "/v3"
        elif env == "live":
            api_base = "https://api-fxtrade.oanda.com/v3"
        else:
            api_base = "https://api-fxpractice.oanda.com/v3"

        stream_env = os.getenv("OANDA_STREAM_URL", "").strip().rstrip("/")
        if stream_env:
            stream_base = stream_env if "/v3" in stream_env else stream_env + "/v3"
        elif env == "live":
            stream_base = "https://stream-fxtrade.oanda.com/v3"
        else:
            stream_base = "https://stream-fxpractice.oanda.com/v3"

        account_id = os.getenv("OANDA_ACCOUNT_ID", "").strip()
        if not account_id and api_key:
            try:
                account_id = _discover_account_id(api_key, api_base)
                LOGGER.info("[ENGINE] account_id=%s", account_id)
            except Exception as exc:
                LOGGER.warning("[ENGINE] could not discover account_id: %s", exc)

        return cls(api_key=api_key, account_id=account_id,
                   api_base=api_base, stream_base=stream_base)

    # ── Startup ───────────────────────────────────────────────────────────────

    async def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop    = loop
        self._running = True

        LOGGER.info("[ENGINE] seeding history (M1/M5/M15/H1) …")
        results = await asyncio.gather(
            *[asyncio.to_thread(self._fetch_and_seed, tf) for tf in TIMEFRAMES],
            return_exceptions=True,
        )
        for tf, res in zip(TIMEFRAMES, results):
            if isinstance(res, Exception):
                LOGGER.error("[ENGINE] seed [%s] failed: %s", tf, res)

        self._history_loaded = True
        LOGGER.info("[ENGINE] history loaded — broadcasting init")
        self._broadcast(_init_event(self.store))

        self._stream_thread = threading.Thread(
            target=self._stream_loop, name="oanda-stream", daemon=True,
        )
        self._stream_thread.start()
        LOGGER.info("[ENGINE] stream thread started")

    # ── Public API ────────────────────────────────────────────────────────────

    def subscribe(self) -> tuple[int, asyncio.Queue]:
        q = asyncio.Queue(maxsize=500)
        with self._sub_lock:
            sid = self._sub_counter
            self._sub_counter += 1
            self._subscribers[sid] = q
        return sid, q

    def unsubscribe(self, sub_id: int) -> None:
        with self._sub_lock:
            self._subscribers.pop(sub_id, None)

    def stop(self) -> None:
        self._running = False

    # ── Seeding helpers ───────────────────────────────────────────────────────

    def _fetch_and_seed(self, tf: str) -> None:
        """Called in thread pool — delegates to module-level _seed_timeframe."""
        _seed_timeframe(tf, self.store, self._db, self.api_key, self.api_base)

    def _backfill_gaps(self) -> None:
        """Called after every stream reconnect to fill gaps created during downtime."""
        LOGGER.info("[ENGINE] checking / filling gaps after reconnect …")
        for tf in TIMEFRAMES:
            try:
                _seed_timeframe(tf, self.store, self._db, self.api_key, self.api_base)
            except Exception as exc:
                LOGGER.warning("[ENGINE] gap-fill [%s]: %s", tf, exc)

    # ── Stream loop ───────────────────────────────────────────────────────────

    def _stream_loop(self) -> None:
        """Runs forever in daemon thread.  Reconnects on any failure."""
        while self._running:
            try:
                self._connect_and_read()
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    LOGGER.error("[STREAM] 401 Invalid API Key — stopping stream")
                    self._broadcast({"type": "status", "status": "error", "detail": "Invalid API Key"})
                    self._running = False
                    return
                elif exc.code == 429:
                    LOGGER.warning("[STREAM] 429 rate-limited — waiting 60 s")
                    self._set_status("reconnecting")
                    time.sleep(60)
                else:
                    LOGGER.warning("[STREAM] HTTP %s — reconnect in 5 s", exc.code)
                    self._set_status("reconnecting")
                    time.sleep(5)
            except Exception as exc:
                LOGGER.warning("[STREAM] disconnected: %s — reconnect in 3 s", exc)
                self._set_status("reconnecting")
                time.sleep(3)

            if self._running:
                # Fill any gap created while the stream was down
                self._backfill_gaps()
                # Push full updated history to all SSE clients
                self._broadcast(_init_event(self.store))

    def _connect_and_read(self) -> None:
        if not self.api_key or not self.account_id:
            LOGGER.error("[STREAM] api_key or account_id missing — sleeping 10 s")
            time.sleep(10)
            return

        url = (
            f"{self.stream_base}/accounts/{self.account_id}"
            f"/pricing/stream?instruments={SYMBOL}"
        )
        headers = {
            "Authorization":          f"Bearer {self.api_key}",
            "Accept-Datetime-Format": "RFC3339",
            "User-Agent":             "SEAN0-ALGO-V1/1.0",
        }
        ssl_ctx = ssl.create_default_context()
        req = urllib.request.Request(url, headers=headers, method="GET")

        LOGGER.info("[STREAM] connecting → %s …", self.stream_base)
        with urllib.request.urlopen(req, timeout=30, context=ssl_ctx) as resp:
            self._set_status("connected")
            LOGGER.info("[STREAM] connected — reading ticks")

            for raw_line in resp:
                if not self._running:
                    break
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if msg.get("type") == "HEARTBEAT":
                    continue
                if msg.get("type") != "PRICE":
                    continue

                bids = msg.get("bids", [])
                asks = msg.get("asks", [])
                if not bids or not asks:
                    continue

                bid = float(bids[0]["price"])
                ask = float(asks[0]["price"])
                mid = (bid + ask) / 2.0

                tick_time_str = msg.get("time", "")
                try:
                    tick_unix = pd.Timestamp(tick_time_str).timestamp()
                except Exception:
                    tick_unix = time.time()

                # Feed tick → candle builder
                completed = self.builder.process_tick(mid, tick_unix)

                # Persist + broadcast newly completed candles
                for candle in completed:
                    try:
                        self._db.upsert_one(SYMBOL, candle["timeframe"], {
                            "timestamp": candle["time"],
                            "open":      candle["open"],
                            "high":      candle["high"],
                            "low":       candle["low"],
                            "close":     candle["close"],
                            "volume":    candle.get("volume", 0),
                            "is_closed": True,
                        })
                    except Exception:
                        pass
                    self._broadcast({
                        "type":      "candle",
                        "timeframe": candle["timeframe"],
                        "candle":    candle,
                    })

                # Broadcast live tick
                self._broadcast({
                    "type":    "tick",
                    "price":   round(mid, 3),
                    "time":    tick_unix,
                    "candles": self.builder.snapshot_current(),
                })

    # ── Broadcasting ─────────────────────────────────────────────────────────

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


# ── Helpers ────────────────────────────────────────────────────────────────────

def _safe_put(q: asyncio.Queue, payload: str) -> None:
    try:
        q.put_nowait(payload)
    except asyncio.QueueFull:
        pass


def _init_event(store: CandleStore) -> dict:
    """Init event includes CLOSED + FORMING candle for each TF."""
    return {
        "type":    "init",
        "candles": {tf: store.get_all(tf) for tf in TIMEFRAMES},
    }
