from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any, TextIO

import matplotlib
import pandas as pd
import requests

from core.data_fetcher import OANDA_BASE_URLS, OANDA_INSTRUMENT
from core.indicator_engine import IndicatorEngine
from core.trade_filters import run_trade_filters


matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
TRADES_CSV_PATH = ROOT / "trades.csv"
DECISION_TRACE_PATH = ROOT / "decision_trace.log"
EQUITY_CURVE_PATH = ROOT / "equity_curve.png"
LOG_DIR = ROOT / "logs"
BACKTEST_SUMMARY_PATH = LOG_DIR / "backtest_summary.json"
TIMESTAMP_FILENAME_FORMAT = "%Y-%m-%d_%H-%M-%S"

ENTRY_GRANULARITY = "M5"
DEFAULT_MAX_HOLD = 12
DEFAULT_STARTING_BALANCE = 5_000.0
DEFAULT_RISK_PER_TRADE = 0.05
DEFAULT_LOOKBACK_DAYS = 15
DEFAULT_WARMUP_DAYS = 30
MAX_CHUNK_DAYS = 14
REQUEST_TIMEOUT = 30
MAX_RETRIES = 5
RETRY_SECONDS = 12
BUY_RSI_THRESHOLD = 55.0
SELL_RSI_THRESHOLD = 45.0
STOP_LOSS_ATR_MULTIPLIER = 1.5
TAKE_PROFIT_ATR_MULTIPLIER = 1.5           # default TP (chop regime)
SLIPPAGE_POINTS = 0.05
COMMISSION_RATE = 0.000001

# ── Realistic execution model (bid/ask + intrabar resolution) ─────────────
# When True, backtests price fills the way a real broker fills you:
#   • BUY  enters at the ASK, its SL/TP are hit when the BID crosses them
#   • SELL enters at the BID, its SL/TP are hit when the ASK crosses them
# This embeds the true OANDA spread instead of the flat 5-cent slippage hack,
# so reported P&L matches what a live account would actually earn (usually a
# few % lower — that gap was optimism, not real edge). Signals still fire on
# MID prices, so the *signal set is unchanged*; only the fills differ.
USE_REALISTIC_FILLS = True

# When a single M5 bar's range straddles BOTH stop and target, OHLC alone
# can't say which was hit first. With this on, we drop to M1 candles for that
# one bar and replay the 5 sub-bars to find the real order. Sub-minute ties
# still fall back to "stop first" (honest — tick data would be needed to go
# finer). Fixes the handful of mislabeled straddle trades manual checks catch.
USE_M1_INTRABAR = True

# OANDA hard-caps a candle request at 5000 bars, so chunk by granularity.
_CHUNK_DAYS_BY_GRANULARITY = {"M1": 3, "M5": 14, "M15": 30, "H1": 60}

# ── Detection lag ─────────────────────────────────────────────────────────
# HONEST model (time-based, uses REAL M1 drift). The live bots poll every ~60s,
# so they detect a just-closed signal bar — and fill — up to one poll interval
# later, at whatever price the market has ACTUALLY drifted to by then. That
# drift can help OR hurt; this is not a worst-case hack. When >0, the entry
# fills at the correct side of the spread of the M1 bar covering
# (entry-bar-open + this many seconds), instead of the exact next-bar open.
# Requires M1 bid/ask data; falls back to the exact fill when unavailable.
# NOTE: M1 granularity floors sub-minute lags to the minute (sub-minute drift
# isn't observable at M1) — set 60 to see the realistic worst-case of one full
# poll interval of drift. 0 = off (exact next-bar open fill).
DETECTION_LAG_SECONDS = 0.0

# STRESS knob (points of ALWAYS-ADVERSE entry slippage) — a separate, blunter
# tool for spread-jitter / worst-case stress tests. Set >0 to worsen every fill
# by this many points (BUY higher / SELL lower). Stacks on top of the honest
# time-based lag above. 0 = off.
DETECTION_LAG_POINTS = 0.0

# ── Volatility Switch (built but DISABLED by default) ────────────────────
# Idea: when ADX(14) on the M15 trend candle is >= threshold, widen TP to
# TAKE_PROFIT_ATR_MULTIPLIER_HIGH_VOL to capture trending moves; else keep 1:1.
# Tested on 120-day Aug 2026 XAUUSD:
#   baseline 1:1  → +19.89% net, PF 1.19, MaxDD -5.12R
#   switch @ ADX 30 → +19.66% net, PF 1.18, MaxDD -7.12R  (SAME net, WORSE DD)
# Verdict: no material improvement on real complete data; the earlier isolated
# experiment that showed +3.6pp gain was an artifact of a fetch data gap that
# lowered the baseline. Left in-place (flag) for future regime tests. Flip to
# True to re-enable and A/B on live data before shipping.
USE_VOLATILITY_SWITCH = False
ADX_HIGH_VOL_THRESHOLD = 30.0
TAKE_PROFIT_ATR_MULTIPLIER_HIGH_VOL = 3.0  # 1:2 RR when switch is on


def add_adx14(df: "pd.DataFrame") -> "pd.DataFrame":
    """Add Wilder ADX(14) as 'adx14' column. Idempotent — returns df unchanged if already present."""
    if "adx14" in df.columns:
        return df
    import numpy as np  # local import — pandas already imported at module level
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    up = high.diff()
    dn = -low.diff()
    plus_dm = ((up > dn) & (up > 0)) * up.clip(lower=0)
    minus_dm = ((dn > up) & (dn > 0)) * dn.clip(lower=0)
    tr = pd.concat([high - low, (high - close.shift()).abs(), (low - close.shift()).abs()], axis=1).max(axis=1)
    atr_w = tr.ewm(alpha=1.0 / 14, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1.0 / 14, adjust=False).mean() / atr_w
    minus_di = 100 * minus_dm.ewm(alpha=1.0 / 14, adjust=False).mean() / atr_w
    denom = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / denom
    df = df.copy()
    df["adx14"] = dx.ewm(alpha=1.0 / 14, adjust=False).mean()
    return df


def load_local_env(env_path: Path = ENV_PATH) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def parse_date_utc(value: str, *, inclusive_end: bool = False) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    else:
        timestamp = timestamp.tz_convert("UTC")
    timestamp = timestamp.normalize()
    if inclusive_end:
        timestamp = timestamp + pd.Timedelta(days=1)
    return timestamp


def resolve_oanda_base_url() -> str:
    configured_url = os.getenv("OANDA_API_URL", "").strip()
    if configured_url:
        normalized = configured_url.rstrip("/")
        if normalized.endswith("/v3"):
            normalized = normalized[: -len("/v3")]
        return normalized
    environment = os.getenv("OANDA_ENV", "practice").strip().lower()
    return OANDA_BASE_URLS.get(environment, OANDA_BASE_URLS["practice"])


def build_request_windows(
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    chunk_days: int = MAX_CHUNK_DAYS,
) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    windows: list[tuple[pd.Timestamp, pd.Timestamp]] = []
    cursor = start_utc
    step = pd.Timedelta(days=max(1, chunk_days))
    while cursor < end_utc:
        window_end = min(cursor + step, end_utc)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def fetch_candles_mba(
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    granularity: str = ENTRY_GRANULARITY,
) -> pd.DataFrame:
    """Fetch OANDA candles with MID, BID and ASK buckets in one pass.

    Returns a frame with the usual mid columns (open/high/low/close) plus
    bid_o/h/l/c and ask_o/h/l/c so the realistic fill model can price
    entries at the ask/bid and resolve SL/TP against the correct side.
    Indicators keep running on the mid columns, unchanged.
    """
    api_key = os.getenv("OANDA_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OANDA_API_KEY is required to run the forex backtest.")

    base_url = resolve_oanda_base_url()
    chunk_days = _CHUNK_DAYS_BY_GRANULARITY.get(granularity, MAX_CHUNK_DAYS)

    frames: list[pd.DataFrame] = []
    for window_start, window_end in build_request_windows(start_utc, end_utc, chunk_days):
        try:
            frame = fetch_oanda_window(
                base_url=base_url,
                api_key=api_key,
                price_component="MBA",
                granularity=granularity,
                start_utc=window_start,
                end_utc=window_end,
            )
            frames.append(frame)
        except RuntimeError as fetch_err:
            # Skip windows that OANDA rejects (e.g. 400 for partial/future
            # dates, or empty weekend windows) — log and continue so the rest
            # of the backtest still runs with whatever data was fetched.
            print(
                f"[SKIP] {granularity} window {window_start.date()} -> {window_end.date()} "
                f"skipped: {fetch_err}"
            )
            continue

    if not frames:
        raise RuntimeError(f"No OANDA {granularity} windows were fetched for the selected date range.")

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    if combined.empty:
        raise RuntimeError(f"No complete OANDA {granularity} candles were returned for the requested window.")
    return combined


def fetch_historical_5m_candles(start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> pd.DataFrame:
    """Backwards-compatible M5 fetch — now returns mid + bid/ask columns.

    When OANDA_PRICE_COMPONENT forces a single stream (legacy behaviour), we
    fall back to the mid-only fetch so nothing breaks; otherwise we pull MBA.
    """
    forced = os.getenv("OANDA_PRICE_COMPONENT", "").strip().upper()
    if forced and forced != "MBA":
        # Legacy single-stream path (kept for explicit overrides / debugging).
        api_key = os.getenv("OANDA_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("OANDA_API_KEY is required to run the forex backtest.")
        base_url = resolve_oanda_base_url()
        frames: list[pd.DataFrame] = []
        for window_start, window_end in build_request_windows(start_utc, end_utc):
            try:
                frames.append(
                    fetch_oanda_window(
                        base_url=base_url,
                        api_key=api_key,
                        price_component=forced,
                        granularity=ENTRY_GRANULARITY,
                        start_utc=window_start,
                        end_utc=window_end,
                    )
                )
            except RuntimeError as fetch_err:
                print(f"[SKIP] window {window_start.date()} -> {window_end.date()} skipped: {fetch_err}")
                continue
        if not frames:
            raise RuntimeError("No OANDA windows were fetched for the selected date range.")
        combined = pd.concat(frames, ignore_index=True)
        return combined.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)

    return fetch_candles_mba(start_utc, end_utc, granularity=ENTRY_GRANULARITY)


def fetch_oanda_window(
    *,
    base_url: str,
    api_key: str,
    price_component: str,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    granularity: str = ENTRY_GRANULARITY,
) -> pd.DataFrame:
    url = f"{base_url}/v3/instruments/{OANDA_INSTRUMENT}/candles"
    params = {
        "granularity": granularity,
        "price": price_component,
        "from": start_utc.isoformat(),
        "to": end_utc.isoformat(),
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept-Datetime-Format": "RFC3339",
        "User-Agent": "SEAN0-ALGO-V1-forex-backtest/4.0",
    }

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            return candles_to_frame(payload.get("candles", []))
        except requests.HTTPError as error:
            last_error = error
            status_code = error.response.status_code if error.response is not None else 0
            # 400 Bad Request — invalid params (e.g. future/weekend date).
            # Retrying the exact same URL will always fail; break immediately.
            # 401/403 — auth failure; no point retrying.
            if status_code in (400, 401, 403):
                break
            if attempt >= MAX_RETRIES:
                break
            print(
                f"[FETCH] window={start_utc.date()}->{end_utc.date()} "
                f"attempt={attempt}/{MAX_RETRIES} status={status_code} retry_in={RETRY_SECONDS}s"
            )
            time.sleep(RETRY_SECONDS)
        except (requests.RequestException, ValueError) as error:
            last_error = error
            if attempt >= MAX_RETRIES:
                break
            print(
                f"[FETCH] window={start_utc.date()}->{end_utc.date()} "
                f"attempt={attempt}/{MAX_RETRIES} error={error} retry_in={RETRY_SECONDS}s"
            )
            time.sleep(RETRY_SECONDS)

    raise RuntimeError(f"Unable to fetch OANDA candles for {start_utc} -> {end_utc}: {last_error}")


def candles_to_frame(candles: list[dict[str, Any]]) -> pd.DataFrame:
    """Parse OANDA candles into a frame. Handles single-stream (mid/bid/ask)
    and MBA multi-stream payloads alike; missing streams mirror the mid so
    downstream code always finds bid_*/ask_* columns."""
    rows: list[dict[str, Any]] = []
    for candle in candles:
        if not candle.get("complete", False):
            continue
        mid = candle.get("mid")
        bid = candle.get("bid")
        ask = candle.get("ask")
        # Single-stream payload: whichever bucket exists becomes the mid.
        base = mid if isinstance(mid, dict) else (bid if isinstance(bid, dict) else ask)
        if not isinstance(base, dict):
            continue
        if not isinstance(mid, dict):
            mid = base
        if not isinstance(bid, dict):
            bid = mid
        if not isinstance(ask, dict):
            ask = mid
        rows.append(
            {
                "timestamp": pd.to_datetime(candle["time"], utc=True),
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
                "bid_o": float(bid["o"]),
                "bid_h": float(bid["h"]),
                "bid_l": float(bid["l"]),
                "bid_c": float(bid["c"]),
                "ask_o": float(ask["o"]),
                "ask_h": float(ask["h"]),
                "ask_l": float(ask["l"]),
                "ask_c": float(ask["c"]),
                "volume": float(candle.get("volume", 0.0)),
            }
        )

    columns = [
        "timestamp", "open", "high", "low", "close",
        "bid_o", "bid_h", "bid_l", "bid_c",
        "ask_o", "ask_h", "ask_l", "ask_c",
        "volume",
    ]
    frame = pd.DataFrame(rows, columns=columns)
    if frame.empty:
        return frame
    return frame.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)


def resample_to_15m(candles_5m: pd.DataFrame) -> pd.DataFrame:
    indexed = candles_5m.set_index("timestamp")
    resampled = indexed.resample("15min", label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    resampled = resampled.dropna(subset=["open", "high", "low", "close"]).reset_index()
    return resampled


def detect_session(close_time_utc: pd.Timestamp) -> str:
    hour = close_time_utc.hour
    if 12 <= hour < 16:
        return "OVERLAP"
    if 7 <= hour < 16:
        return "LONDON"
    if 16 <= hour < 21:
        return "NEW_YORK"
    return "ASIAN"


def session_allowed(close_time_utc: pd.Timestamp) -> bool:
    hour = close_time_utc.hour
    return 12 <= hour < 21  # London-NY Overlap (12-16) + New York (16-21); skip pure London + Asian


def indicators_ready(row: pd.Series, columns: tuple[str, ...]) -> bool:
    return not any(pd.isna(row[column]) for column in columns)


def trend_candle_timestamp(entry_timestamp: pd.Timestamp) -> pd.Timestamp:
    entry_close_time = pd.Timestamp(entry_timestamp)
    if entry_close_time.tzinfo is None:
        entry_close_time = entry_close_time.tz_localize("UTC")
    else:
        entry_close_time = entry_close_time.tz_convert("UTC")
    entry_close_time = entry_close_time + pd.Timedelta(minutes=5)
    return entry_close_time.floor("15min") - pd.Timedelta(minutes=15)


def effective_entry_price(open_price: float, direction: str) -> float:
    if direction == "BUY":
        return open_price + SLIPPAGE_POINTS
    return open_price - SLIPPAGE_POINTS


def effective_exit_price(exit_price: float, direction: str) -> float:
    if direction == "BUY":
        return exit_price - SLIPPAGE_POINTS
    return exit_price + SLIPPAGE_POINTS


# ── Realistic fill model (bid/ask + M1 intrabar resolution) ───────────────

def has_bidask(df: pd.DataFrame) -> bool:
    """True if the frame carries bid/ask columns (i.e. was fetched via MBA)."""
    return "ask_o" in df.columns and "bid_o" in df.columns


def realistic_entry_price(entry_candle: pd.Series, direction: str) -> float:
    """Fill a market entry at the correct side of the spread.

    BUY lifts the ASK, SELL hits the BID — matching how a broker fills you.
    """
    if direction == "BUY":
        return float(entry_candle["ask_o"])
    return float(entry_candle["bid_o"])


def _bar_hits(bar: pd.Series, direction: str, stop_loss: float, take_profit: float) -> tuple[bool, bool]:
    """Did this bar's exit-side stream touch SL / TP?

    Longs are closed on the BID, shorts on the ASK.
    """
    if direction == "BUY":
        return (float(bar["bid_l"]) <= stop_loss, float(bar["bid_h"]) >= take_profit)
    return (float(bar["ask_h"]) >= stop_loss, float(bar["ask_l"]) <= take_profit)


class M1Cache:
    """Lazily fetches and caches M1 bid/ask candles, one UTC day at a time.

    Straddle bars (where an M5 bar hits both SL and TP) are rare, so fetching
    M1 for a whole backtest window upfront is wasteful. This fetches only the
    day a straddle actually lands on, the first time it's needed."""

    def __init__(self) -> None:
        self._by_day: dict[pd.Timestamp, "pd.DataFrame | None"] = {}

    def day_frame(self, ts: pd.Timestamp) -> "pd.DataFrame | None":
        day = pd.Timestamp(ts).normalize()
        if day not in self._by_day:
            try:
                df = fetch_candles_mba(day, day + pd.Timedelta(days=1), granularity="M1")
            except Exception as exc:  # noqa: BLE001 — M1 is best-effort
                print(f"[M1] day {day.date()} fetch failed ({exc}); straddle falls back to stop-first")
                df = None
            if df is not None and not df.empty and has_bidask(df):
                self._by_day[day] = df.set_index("timestamp", drop=False).sort_index()
            else:
                self._by_day[day] = None
        return self._by_day[day]


def _m1_day_frame(m1: object, ts: pd.Timestamp) -> "pd.DataFrame | None":
    """Return an M1 frame (indexed by timestamp) covering `ts`. Accepts either
    an M1Cache (lazy) or a pre-indexed DataFrame (used by unit tests)."""
    if m1 is None:
        return None
    if hasattr(m1, "day_frame"):
        return m1.day_frame(ts)  # type: ignore[attr-defined]
    return m1  # already an indexed frame


def lagged_entry_price(
    entry_candle: pd.Series,
    direction: str,
    m1_lookup: object,
    lag_seconds: float,
    fallback_price: float,
) -> float:
    """Honest detection-lag fill. The live bot polls every ~60s, so it fills up
    to one poll interval after the signal bar closes — at whatever price the
    market has ACTUALLY drifted to by then (favourable or adverse; this reads
    the real M1 price, not a worst-case slippage).

    Returns the fill at the correct side of the spread (BUY = ask, SELL = bid)
    of the M1 bar covering (entry-bar open + lag_seconds). Falls back to
    `fallback_price` (the exact next-bar open) when lag <= 0, or when M1 bid/ask
    data for that minute isn't available. M1 granularity floors sub-minute lags
    to the minute (lag < 60 -> no observable drift; [60,120) -> one minute; ...).
    """
    if not lag_seconds or lag_seconds <= 0:
        return fallback_price
    entry_time = pd.Timestamp(entry_candle["timestamp"])
    target_minute = (entry_time + pd.Timedelta(seconds=float(lag_seconds))).floor("1min")
    frame = _m1_day_frame(m1_lookup, target_minute)
    if frame is None or getattr(frame, "empty", True) or not has_bidask(frame):
        return fallback_price
    if target_minute not in frame.index:
        return fallback_price  # market gap at that minute — keep the exact fill
    bar = frame.loc[target_minute]
    if isinstance(bar, pd.DataFrame):
        bar = bar.iloc[0]
    return float(bar["ask_o"]) if direction == "BUY" else float(bar["bid_o"])


def _resolve_straddle_m1(
    m5_timestamp: pd.Timestamp,
    direction: str,
    stop_loss: float,
    take_profit: float,
    m1_lookup: object,
) -> str | None:
    """When an M5 bar hits both SL and TP, replay its 5 M1 sub-bars to find
    which came first. Returns 'SL', 'TP', or None (no M1 data available).
    A sub-minute straddle still resolves to 'SL' (honest — finer than M1
    needs tick data we don't have)."""
    frame = _m1_day_frame(m1_lookup, m5_timestamp)
    if frame is None or frame.empty:
        return None
    t0 = pd.Timestamp(m5_timestamp)
    for k in range(5):
        t = t0 + pd.Timedelta(minutes=k)
        if t not in frame.index:
            continue
        bar = frame.loc[t]
        if isinstance(bar, pd.DataFrame):
            bar = bar.iloc[0]
        hit_sl, hit_tp = _bar_hits(bar, direction, stop_loss, take_profit)
        if hit_sl:
            return "SL"          # stop-first (covers the SL+TP-same-M1 tie too)
        if hit_tp:
            return "TP"
    return None


def resolve_realistic_exit(
    *,
    entry_df: pd.DataFrame,
    entry_index: int,
    last_index: int,
    direction: str,
    stop_loss: float,
    take_profit: float,
    m1_lookup: "pd.DataFrame | None" = None,
) -> tuple[int, float, str, str] | None:
    """Walk forward over bid/ask bars and return the first SL/TP hit as
    (bar_index, fill_level, result, exit_reason), or None if neither was hit
    within [entry_index, last_index] (caller then closes at max-hold)."""
    for fi in range(entry_index, last_index + 1):
        bar = entry_df.iloc[fi]
        hit_sl, hit_tp = _bar_hits(bar, direction, stop_loss, take_profit)
        if hit_sl and hit_tp:
            decided = _resolve_straddle_m1(
                pd.Timestamp(bar["timestamp"]), direction, stop_loss, take_profit, m1_lookup
            )
            if decided == "TP":
                return fi, take_profit, "WIN", "take_profit_hit_m1"
            if decided == "SL":
                return fi, stop_loss, "LOSS", "stop_loss_hit_m1"
            return fi, stop_loss, "LOSS", "sl_and_tp_same_bar_sl_first"
        if hit_sl:
            return fi, stop_loss, "LOSS", "stop_loss_hit"
        if hit_tp:
            return fi, take_profit, "WIN", "take_profit_hit"
    return None


def fetch_m1_lookup(start_utc: pd.Timestamp, end_utc: pd.Timestamp) -> "pd.DataFrame | None":
    """DEPRECATED eager M1 fetch — superseded by the lazy M1Cache. Kept for any
    external caller. Fetches all M1 bid/ask for the window, indexed by time."""
    if not USE_M1_INTRABAR:
        return None
    try:
        m1 = fetch_candles_mba(start_utc, end_utc, granularity="M1")
    except Exception as exc:  # noqa: BLE001 — M1 is best-effort, never fatal
        print(f"[M1] intrabar fetch failed ({exc}); falling back to stop-first on straddles")
        return None
    if m1 is None or m1.empty or not has_bidask(m1):
        return None
    return m1.set_index("timestamp", drop=False).sort_index()


def weak_candle_filter(candle: pd.Series) -> bool:
    candle_range = float(candle["high"]) - float(candle["low"])
    atr_value = float(candle["atr14"])
    return candle_range < atr_value * 0.5


def write_trace(handle: TextIO, lines: list[str]) -> None:
    handle.write("\n".join(lines))
    handle.write("\n\n")


def evaluate_signal(
    *,
    entry_df: pd.DataFrame,
    trend_lookup: pd.DataFrame,
    signal_index: int,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    trace_handle: TextIO,
) -> dict[str, Any]:
    candle = entry_df.iloc[signal_index]
    previous = entry_df.iloc[signal_index - 1]
    signal_timestamp = pd.Timestamp(candle["timestamp"])
    close_time = signal_timestamp + pd.Timedelta(minutes=5)
    session = detect_session(close_time)
    trace_lines: list[str] = [f"[TIME] {close_time.isoformat()}"]

    if signal_timestamp < start_utc or signal_timestamp >= end_utc:
        trace_lines.append("[RESULT] OUTSIDE WINDOW")
        write_trace(trace_handle, trace_lines)
        return {"signal": None, "reason": "outside_window"}

    trend_timestamp = trend_candle_timestamp(signal_timestamp)
    if trend_timestamp not in trend_lookup.index:
        trace_lines.append("[TREND] missing closed 15m candle")
        trace_lines.append("[RESULT] NO SIGNAL")
        write_trace(trace_handle, trace_lines)
        return {"signal": None, "reason": "missing_trend_candle"}

    trend_candle = trend_lookup.loc[trend_timestamp]
    if isinstance(trend_candle, pd.DataFrame):
        trend_candle = trend_candle.iloc[-1]

    if not indicators_ready(candle, ("rsi14", "atr14", "atr20_avg")):
        trace_lines.append("[INDICATORS] entry indicators not ready")
        trace_lines.append("[RESULT] NO SIGNAL")
        write_trace(trace_handle, trace_lines)
        return {"signal": None, "reason": "entry_indicators_not_ready"}

    if not indicators_ready(trend_candle, ("ema50", "ema200", "atr14")):
        trace_lines.append("[INDICATORS] trend indicators not ready")
        trace_lines.append("[RESULT] NO SIGNAL")
        write_trace(trace_handle, trace_lines)
        return {"signal": None, "reason": "trend_indicators_not_ready"}

    ema50 = float(trend_candle["ema50"])
    ema200 = float(trend_candle["ema200"])
    if ema50 > ema200:
        direction = "BUY"
        breakout_ok = float(candle["close"]) > float(previous["high"])
        rsi_ok = float(candle["rsi14"]) > BUY_RSI_THRESHOLD
        trace_lines.append(f"[TREND] EMA50 > EMA200 -> bullish ({ema50:.2f}/{ema200:.2f})")
        trace_lines.append(
            f"[BREAKOUT] close > prev high -> {breakout_ok} "
            f"({float(candle['close']):.2f}/{float(previous['high']):.2f})"
        )
    elif ema50 < ema200:
        direction = "SELL"
        breakout_ok = float(candle["close"]) < float(previous["low"])
        rsi_ok = float(candle["rsi14"]) < SELL_RSI_THRESHOLD
        trace_lines.append(f"[TREND] EMA50 < EMA200 -> bearish ({ema50:.2f}/{ema200:.2f})")
        trace_lines.append(
            f"[BREAKOUT] close < prev low -> {breakout_ok} "
            f"({float(candle['close']):.2f}/{float(previous['low']):.2f})"
        )
    else:
        trace_lines.append(f"[TREND] EMA50 == EMA200 -> neutral ({ema50:.2f}/{ema200:.2f})")
        trace_lines.append("[RESULT] NO SIGNAL")
        write_trace(trace_handle, trace_lines)
        return {"signal": None, "reason": "neutral_trend"}

    trace_lines.append(
        f"[MOMENTUM] RSI={float(candle['rsi14']):.2f} -> {'valid' if rsi_ok else 'invalid'}"
    )

    # Filter 1: Trend strength — EMA gap must exceed trend ATR
    trend_atr = float(trend_candle["atr14"])
    trend_strength_ok = abs(ema50 - ema200) > trend_atr
    trace_lines.append(
        f"[TREND_STRENGTH] |EMA50-EMA200|={abs(ema50 - ema200):.2f} > trend_ATR={trend_atr:.2f} -> {trend_strength_ok}"
    )

    atr_value = float(candle["atr14"])
    atr_avg = float(candle["atr20_avg"])

    # Filter 2: Volatility expansion — ATR must exceed 1.1× its average
    atr_ok = atr_value > atr_avg * 1.1
    trace_lines.append(
        f"[VOLATILITY] ATR > ATR_avg×1.1 -> {atr_ok} ({atr_value:.2f}/{atr_avg * 1.1:.2f})"
    )

    # Filter 3: Range avoidance — last 20 candles must span at least 3× ATR
    lookback_start = max(0, signal_index - 19)
    lookback = entry_df.iloc[lookback_start: signal_index + 1]
    range_20 = float(lookback["high"].max()) - float(lookback["low"].min())
    range_ok = range_20 >= atr_value * 3.0
    trace_lines.append(
        f"[RANGE] 20c_range={range_20:.2f} >= ATR×3={atr_value * 3.0:.2f} -> {range_ok}"
    )

    session_ok = session_allowed(close_time)
    trace_lines.append(f"[SESSION] {session} -> {'allowed' if session_ok else 'blocked'}")

    weak_candle = weak_candle_filter(candle)
    trace_lines.append(
        f"[FILTER] weak candle -> {'rejected' if weak_candle else 'ok'} "
        f"({float(candle['high']) - float(candle['low']):.2f}/{atr_value * 0.5:.2f})"
    )

    # run_trade_filters only inspects the last 20 bars + the current candle, so
    # pass a bounded tail instead of the whole history. Slicing entry_df[:i] on
    # every bar was O(n) per bar → O(n²) over the run (made large/long backtests
    # crawl and time out); this makes it O(1) per bar. Behaviour is identical.
    _flt_lb = max(0, signal_index - 24)
    filter_result = run_trade_filters(
        entry_df.iloc[_flt_lb: signal_index + 1],
        trend_ema50=ema50,
        trend_ema200=ema200,
        trend_atr=trend_atr,
    )
    trade_filter_ok = bool(filter_result.get("allowed", True))
    trace_lines.append(
        f"[FILTER] no-trade-zone -> {'ok' if trade_filter_ok else 'rejected'} "
        f"({filter_result.get('reason', '') or 'clear'})"
    )

    reason = "accepted"
    if not breakout_ok:
        reason = "no_breakout"
    elif not rsi_ok:
        reason = "momentum_invalid"
    elif not trend_strength_ok:
        reason = "weak_trend_strength"
    elif not range_ok:
        reason = "ranging_20c"
    elif not atr_ok:
        reason = "low_volatility"
    elif not session_ok:
        reason = "session_blocked"
    elif weak_candle:
        reason = "weak_candle"
    elif not trade_filter_ok:
        reason = str(filter_result.get("reason", "trade_filter_rejected")).replace(" ", "_").lower()

    if reason != "accepted":
        trace_lines.append(f"[RESULT] NO SIGNAL ({reason})")
        write_trace(trace_handle, trace_lines)
        return {"signal": None, "reason": reason}

    trace_lines.append(f"[RESULT] {direction} SIGNAL")
    write_trace(trace_handle, trace_lines)
    return {
        "signal": {
            "direction": direction,
            "trend_candle": trend_candle,
            "risk_distance": atr_value * STOP_LOSS_ATR_MULTIPLIER,
            "atr_value": atr_value,
            "reason": "trend_breakout_momentum_volatility",
        },
        "reason": "accepted",
    }


def build_trade_record(
    *,
    signal_timestamp: pd.Timestamp,
    entry_timestamp: pd.Timestamp,
    exit_timestamp: pd.Timestamp,
    direction: str,
    entry_price: float,
    exit_price: float,
    stop_loss: float,
    take_profit: float,
    result: str,
    position_size: float,
    balance_before: float,
    risk_amount: float,
    bars_held: int,
    ema50: float,
    ema200: float,
    rsi: float,
    atr: float,
    reason: str,
    exit_reason: str,
) -> dict[str, Any]:
    if direction == "BUY":
        gross_pnl = (exit_price - entry_price) * position_size
    else:
        gross_pnl = (entry_price - exit_price) * position_size

    entry_commission = abs(entry_price * position_size) * COMMISSION_RATE
    exit_commission = abs(exit_price * position_size) * COMMISSION_RATE
    commission = entry_commission + exit_commission
    pnl = gross_pnl - commission
    r_multiple = pnl / risk_amount if risk_amount > 0 else 0.0

    return {
        "timestamp": signal_timestamp,
        "entry_timestamp": entry_timestamp,
        "exit_timestamp": exit_timestamp,
        "direction": direction,
        "entry_price": round(entry_price, 4),
        "exit_price": round(exit_price, 4),
        "sl": round(stop_loss, 4),
        "tp": round(take_profit, 4),
        "result": result,
        "R_multiple": float(r_multiple),
        "position_size": float(position_size),
        "gross_pnl": float(gross_pnl),
        "commission": float(commission),
        "pnl": float(pnl),
        "equity_before": float(balance_before),
        "ema50": float(ema50),
        "ema200": float(ema200),
        "rsi": float(rsi),
        "atr": float(atr),
        "reason": reason,
        "exit_reason": exit_reason,
        "bars_held": int(bars_held),
    }


def simulate_forex_trade(
    *,
    entry_df: pd.DataFrame,
    signal_index: int,
    direction: str,
    trend_candle: pd.Series,
    signal_reason: str,
    risk_distance: float,
    balance_before: float,
    risk_amount: float,
    max_hold_bars: int,
    m1_lookup: "pd.DataFrame | None" = None,
    detection_lag_seconds: float = DETECTION_LAG_SECONDS,
) -> tuple[dict[str, Any] | None, int]:
    entry_index = signal_index + 1
    if entry_index >= len(entry_df):
        return None, signal_index

    if risk_distance <= 0:
        return None, signal_index

    signal_candle = entry_df.iloc[signal_index]
    entry_candle = entry_df.iloc[entry_index]

    # Realistic fills price the entry at the correct side of the spread and
    # resolve SL/TP against the opposite side (see USE_REALISTIC_FILLS).
    realistic = USE_REALISTIC_FILLS and has_bidask(entry_df)
    if realistic:
        entry_price = realistic_entry_price(entry_candle, direction)
    else:
        entry_price = effective_entry_price(float(entry_candle["open"]), direction)

    # Honest detection-lag: re-fill at the REAL M1 price one poll interval after
    # the signal bar closes. The live bot can't fill at the untraded next-bar
    # open — it polls every ~60s and fills at the drifted market price (which may
    # help or hurt). Needs M1 data; otherwise keeps the exact next-bar fill.
    if detection_lag_seconds and detection_lag_seconds > 0:
        entry_price = lagged_entry_price(
            entry_candle, direction, m1_lookup, detection_lag_seconds, entry_price
        )

    # Detection-lag STRESS (separate blunt knob): additionally worsen every fill
    # by a flat DETECTION_LAG_POINTS to stress spread jitter / worst case.
    if DETECTION_LAG_POINTS:
        entry_price = (entry_price + DETECTION_LAG_POINTS) if direction == "BUY" else (entry_price - DETECTION_LAG_POINTS)

    position_size = risk_amount / risk_distance
    # Volatility switch: use wider TP in trending regime (ADX >= threshold on M15).
    tp_mult = TAKE_PROFIT_ATR_MULTIPLIER
    if USE_VOLATILITY_SWITCH:
        try:
            adx = float(trend_candle.get("adx14", 0.0) or 0.0)
        except Exception:
            adx = 0.0
        if adx >= ADX_HIGH_VOL_THRESHOLD:
            tp_mult = TAKE_PROFIT_ATR_MULTIPLIER_HIGH_VOL
    take_profit_distance = float(signal_candle["atr14"]) * tp_mult

    if direction == "BUY":
        stop_loss = entry_price - risk_distance
        take_profit = entry_price + take_profit_distance
    else:
        stop_loss = entry_price + risk_distance
        take_profit = entry_price - take_profit_distance

    last_index = min(len(entry_df) - 1, entry_index + max_hold_bars - 1)

    if realistic:
        # Bid/ask-aware walk-forward with M1 straddle resolution.
        hit = resolve_realistic_exit(
            entry_df=entry_df,
            entry_index=entry_index,
            last_index=last_index,
            direction=direction,
            stop_loss=stop_loss,
            take_profit=take_profit,
            m1_lookup=m1_lookup,
        )
        if hit is not None:
            future_index, exit_price, result, exit_reason = hit
            future_candle = entry_df.iloc[future_index]
            exit_timestamp = pd.Timestamp(future_candle["timestamp"]) + pd.Timedelta(minutes=5)
            bars_held = future_index - entry_index + 1
            return (
                build_trade_record(
                    signal_timestamp=pd.Timestamp(signal_candle["timestamp"]),
                    entry_timestamp=pd.Timestamp(entry_candle["timestamp"]),
                    exit_timestamp=exit_timestamp,
                    direction=direction,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    result=result,
                    position_size=position_size,
                    balance_before=balance_before,
                    risk_amount=risk_amount,
                    bars_held=bars_held,
                    ema50=float(trend_candle["ema50"]),
                    ema200=float(trend_candle["ema200"]),
                    rsi=float(signal_candle["rsi14"]),
                    atr=float(signal_candle["atr14"]),
                    reason=signal_reason,
                    exit_reason=exit_reason,
                ),
                future_index,
            )
    else:
        for future_index in range(entry_index, last_index + 1):
            future_candle = entry_df.iloc[future_index]
            candle_high = float(future_candle["high"])
            candle_low = float(future_candle["low"])
            exit_timestamp = pd.Timestamp(future_candle["timestamp"]) + pd.Timedelta(minutes=5)
            bars_held = future_index - entry_index + 1

            if direction == "BUY":
                hit_sl = candle_low <= stop_loss
                hit_tp = candle_high >= take_profit
            else:
                hit_sl = candle_high >= stop_loss
                hit_tp = candle_low <= take_profit

            if hit_sl and hit_tp:
                raw_exit_price = stop_loss
                result = "LOSS"
                exit_reason = "sl_and_tp_same_candle_sl_first"
            elif hit_sl:
                raw_exit_price = stop_loss
                result = "LOSS"
                exit_reason = "stop_loss_hit"
            elif hit_tp:
                raw_exit_price = take_profit
                result = "WIN"
                exit_reason = "take_profit_hit"
            else:
                continue

            exit_price = effective_exit_price(raw_exit_price, direction)
            return (
                build_trade_record(
                    signal_timestamp=pd.Timestamp(signal_candle["timestamp"]),
                    entry_timestamp=pd.Timestamp(entry_candle["timestamp"]),
                    exit_timestamp=exit_timestamp,
                    direction=direction,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    result=result,
                    position_size=position_size,
                    balance_before=balance_before,
                    risk_amount=risk_amount,
                    bars_held=bars_held,
                    ema50=float(trend_candle["ema50"]),
                    ema200=float(trend_candle["ema200"]),
                    rsi=float(signal_candle["rsi14"]),
                    atr=float(signal_candle["atr14"]),
                    reason=signal_reason,
                    exit_reason=exit_reason,
                ),
                future_index,
            )

    final_candle = entry_df.iloc[last_index]
    # Time-stop closes at market: BUY exits on the bid, SELL on the ask.
    if realistic:
        final_raw = float(final_candle["bid_c"]) if direction == "BUY" else float(final_candle["ask_c"])
        final_exit_price = final_raw
    else:
        final_exit_price = effective_exit_price(float(final_candle["close"]), direction)
    final_exit_timestamp = pd.Timestamp(final_candle["timestamp"]) + pd.Timedelta(minutes=5)
    if direction == "BUY":
        result = "WIN" if final_exit_price > entry_price else "LOSS"
    else:
        result = "WIN" if final_exit_price < entry_price else "LOSS"

    return (
        build_trade_record(
            signal_timestamp=pd.Timestamp(signal_candle["timestamp"]),
            entry_timestamp=pd.Timestamp(entry_candle["timestamp"]),
            exit_timestamp=final_exit_timestamp,
            direction=direction,
            entry_price=entry_price,
            exit_price=final_exit_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            result=result,
            position_size=position_size,
            balance_before=balance_before,
            risk_amount=risk_amount,
            bars_held=last_index - entry_index + 1,
            ema50=float(trend_candle["ema50"]),
            ema200=float(trend_candle["ema200"]),
            rsi=float(signal_candle["rsi14"]),
            atr=float(signal_candle["atr14"]),
            reason=signal_reason,
            exit_reason=f"max_hold_{max_hold_bars}",
        ),
        last_index,
    )


def compute_metrics(trades_df: pd.DataFrame) -> dict[str, float]:
    if trades_df.empty:
        return {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "average_r": 0.0,
            "max_drawdown_r": 0.0,
            "ending_balance": DEFAULT_STARTING_BALANCE,
        }

    pnl_values = pd.to_numeric(trades_df["pnl"], errors="coerce").fillna(0.0)
    r_values = pd.to_numeric(trades_df["R_multiple"], errors="coerce").fillna(0.0)
    equity_values = pd.to_numeric(trades_df["equity_after"], errors="coerce").ffill().fillna(DEFAULT_STARTING_BALANCE)

    cumulative_r = r_values.cumsum()
    drawdown_r = cumulative_r - cumulative_r.cummax()
    gross_profit = float(pnl_values[pnl_values > 0].sum())
    gross_loss = float(abs(pnl_values[pnl_values < 0].sum()))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    wins = int((pnl_values > 0).sum())
    losses = int((pnl_values <= 0).sum())
    total_trades = int(len(trades_df))
    win_rate = (wins / total_trades) * 100.0 if total_trades else 0.0

    return {
        "total_trades": total_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "average_r": float(r_values.mean()),
        "max_drawdown_r": float(drawdown_r.min()) if not drawdown_r.empty else 0.0,
        "ending_balance": float(equity_values.iloc[-1]),
    }


def save_equity_curve(trades_df: pd.DataFrame) -> None:
    plt.figure(figsize=(12, 6))
    if trades_df.empty:
        plt.title("XAUUSD Equity Curve (no trades)")
        plt.xlabel("Trade")
        plt.ylabel("Balance ($)")
        plt.grid(alpha=0.2)
        plt.tight_layout()
        plt.savefig(EQUITY_CURVE_PATH, dpi=150, bbox_inches="tight")
        plt.close()
        return

    ordered = trades_df.sort_values("exit_timestamp").reset_index(drop=True)
    plt.plot(ordered["exit_timestamp"], ordered["equity_after"], color="#2c7be5", linewidth=1.8)
    plt.title("XAUUSD Forex Backtest Equity Curve")
    plt.xlabel("Exit Time")
    plt.ylabel("Balance ($)")
    plt.grid(alpha=0.2)
    plt.tight_layout()
    plt.savefig(EQUITY_CURVE_PATH, dpi=150, bbox_inches="tight")
    plt.close()


def ensure_runtime_paths() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def atomic_write_csv(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_suffix(destination.suffix + ".tmp")
    frame.to_csv(temp_path, index=False)
    temp_path.replace(destination)


def save_backtest_outputs(
    trades_df: pd.DataFrame,
    metrics: dict[str, float],
    *,
    start_label: str,
    end_label: str,
) -> dict[str, Path | str]:
    ensure_runtime_paths()
    completed_at_utc = pd.Timestamp.now(tz="UTC")
    stamped_path = ROOT / f"trades_{completed_at_utc.strftime(TIMESTAMP_FILENAME_FORMAT)}.csv"

    atomic_write_csv(trades_df, stamped_path)
    atomic_write_csv(trades_df, TRADES_CSV_PATH)
    save_equity_curve(trades_df)

    summary_payload = {
        "mode": "forex",
        "paper_mode": True,
        "completed_at_utc": completed_at_utc.isoformat(),
        "start_date": start_label,
        "end_date": end_label,
        "trades": int(metrics["total_trades"]),
        "win_rate": round(float(metrics["win_rate"]), 2),
        "avg_R": round(float(metrics["average_r"]), 4),
        "max_drawdown": round(float(metrics["max_drawdown_r"]), 4),
        "profit_factor": round(float(metrics["profit_factor"]), 4),
        "final_balance": round(float(metrics["ending_balance"]), 2),
        "latest_trades_csv": str(TRADES_CSV_PATH),
        "timestamped_trades_csv": str(stamped_path),
        "decision_trace": str(DECISION_TRACE_PATH),
        "equity_curve": str(EQUITY_CURVE_PATH),
    }
    BACKTEST_SUMMARY_PATH.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
    return {
        "latest": TRADES_CSV_PATH,
        "timestamped": stamped_path,
        "summary": BACKTEST_SUMMARY_PATH,
    }


def assess_data_integrity(
    df: pd.DataFrame,
    session_start_hour: int = 12,
    session_end_hour: int = 21,
) -> dict[str, Any]:
    """Estimate M5 data completeness inside the trading-session window.

    Only session hours matter (the strategy trades 12-21 UTC), and only days
    the market was open (>0 bars) are counted — so weekends and the nightly
    settlement break are ignored, not flagged as 'missing'. Each open day
    expects (session_end - session_start) * 12 five-minute bars.

    Returns completeness %, expected/actual/missing bar counts, and the worst
    gap days — so a run built on a data hole can't be silently trusted.
    """
    empty = {
        "completeness_pct": 100.0, "expected_bars": 0, "actual_bars": 0,
        "missing_bars": 0, "gap_days": [],
    }
    if df is None or df.empty or "timestamp" not in df.columns:
        return empty
    ts = pd.to_datetime(df["timestamp"], utc=True)
    hours = ts.dt.hour
    sess = ts[(hours >= session_start_hour) & (hours < session_end_hour)]
    if sess.empty:
        return empty

    per_day_expected = (session_end_hour - session_start_hour) * 12
    counts = sess.dt.floor("1D").value_counts()
    total_actual = int(counts.sum())
    total_missing = 0
    gap_days: list[dict[str, Any]] = []
    for day, cnt in counts.items():
        miss = per_day_expected - int(cnt)
        if miss > 0:
            total_missing += miss
            gap_days.append({"date": str(pd.Timestamp(day).date()), "actual": int(cnt), "missing": int(miss)})
    expected = total_actual + total_missing
    completeness = (total_actual / expected * 100.0) if expected else 100.0
    gap_days.sort(key=lambda d: d["missing"], reverse=True)
    return {
        "completeness_pct": round(completeness, 2),
        "expected_bars": expected,
        "actual_bars": total_actual,
        "missing_bars": total_missing,
        "gap_days": gap_days[:10],
    }


def run_backtest(
    *,
    start_utc: pd.Timestamp,
    end_utc: pd.Timestamp,
    max_hold_bars: int = DEFAULT_MAX_HOLD,
    starting_balance: float = DEFAULT_STARTING_BALANCE,
    risk_per_trade: float = DEFAULT_RISK_PER_TRADE,
    detection_lag_seconds: float = DETECTION_LAG_SECONDS,
) -> tuple[pd.DataFrame, dict[str, float]]:
    warmup_start = start_utc - pd.Timedelta(days=DEFAULT_WARMUP_DAYS)
    candles_5m = fetch_historical_5m_candles(warmup_start, end_utc)
    candles_15m = resample_to_15m(candles_5m)

    indicator_engine = IndicatorEngine()
    entry_df = indicator_engine.add_indicators(candles_5m)
    trend_df = indicator_engine.add_indicators(candles_15m)
    trend_df = add_adx14(trend_df)  # for volatility switch — dynamic TP based on ADX
    trend_lookup = trend_df.set_index("timestamp", drop=False).sort_index()

    # M1 bid/ask cache for intrabar SL/TP resolution AND honest detection-lag
    # fills — lazy: only fetches a day when a straddle bar or a lagged entry on
    # that day actually needs it (detection lag makes this ~1 fetch per traded
    # day; straddle-only stays 0-3 tiny fetches per run).
    m1_lookup = None
    _need_m1 = (USE_REALISTIC_FILLS and USE_M1_INTRABAR) or bool(detection_lag_seconds and detection_lag_seconds > 0)
    if _need_m1 and has_bidask(entry_df):
        m1_lookup = M1Cache()

    trades: list[dict[str, Any]] = []
    balance = starting_balance

    with DECISION_TRACE_PATH.open("w", encoding="utf-8") as trace_handle:
        entry_index = 1
        while entry_index < len(entry_df) - 1:
            signal_timestamp = pd.Timestamp(entry_df.iloc[entry_index]["timestamp"])
            if signal_timestamp >= end_utc:
                break

            evaluation = evaluate_signal(
                entry_df=entry_df,
                trend_lookup=trend_lookup,
                signal_index=entry_index,
                start_utc=start_utc,
                end_utc=end_utc,
                trace_handle=trace_handle,
            )
            signal = evaluation["signal"]
            if signal is None:
                entry_index += 1
                continue

            risk_amount = balance * risk_per_trade
            trade, exit_index = simulate_forex_trade(
                entry_df=entry_df,
                signal_index=entry_index,
                direction=str(signal["direction"]),
                trend_candle=signal["trend_candle"],
                signal_reason=str(signal["reason"]),
                risk_distance=float(signal["risk_distance"]),
                balance_before=float(balance),
                risk_amount=float(risk_amount),
                max_hold_bars=max(1, int(max_hold_bars)),
                m1_lookup=m1_lookup,
                detection_lag_seconds=detection_lag_seconds,
            )

            if trade is None:
                entry_index += 1
                continue

            balance += float(trade["pnl"])
            trade["equity_after"] = float(balance)
            trades.append(trade)
            entry_index = max(exit_index + 1, entry_index + 1)

    trades_df = pd.DataFrame(
        trades,
        columns=[
            "timestamp",
            "entry_timestamp",
            "exit_timestamp",
            "direction",
            "entry_price",
            "exit_price",
            "sl",
            "tp",
            "result",
            "R_multiple",
            "position_size",
            "gross_pnl",
            "commission",
            "pnl",
            "equity_before",
            "equity_after",
            "ema50",
            "ema200",
            "rsi",
            "atr",
            "reason",
            "exit_reason",
            "bars_held",
        ],
    )
    metrics = compute_metrics(trades_df)
    metrics["detection_lag_seconds"] = float(detection_lag_seconds or 0.0)

    # Data-integrity: how complete was the M5 feed over the traded window?
    try:
        in_window = candles_5m[(candles_5m["timestamp"] >= start_utc) & (candles_5m["timestamp"] < end_utc)]
        integ = assess_data_integrity(in_window)
        metrics["data_completeness_pct"] = integ["completeness_pct"]
        metrics["data_missing_bars"] = integ["missing_bars"]
        metrics["data_gap_days"] = integ["gap_days"]
    except Exception as exc:  # noqa: BLE001 — integrity is advisory, never fatal
        print(f"[INTEGRITY] check skipped: {exc}")

    return trades_df, metrics


def parse_args() -> argparse.Namespace:
    today_utc = pd.Timestamp.now(tz="UTC").normalize()
    default_end = today_utc - pd.Timedelta(days=1)
    default_start = default_end - pd.Timedelta(days=DEFAULT_LOOKBACK_DAYS)

    parser = argparse.ArgumentParser(description="Run the XAUUSD forex backtest.")
    parser.add_argument("--start", default=default_start.strftime("%Y-%m-%d"), help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=default_end.strftime("%Y-%m-%d"), help="End date YYYY-MM-DD")
    parser.add_argument(
        "--max_hold",
        type=int,
        default=DEFAULT_MAX_HOLD,
        help="Maximum number of 5m bars to hold a trade before closing at market",
    )
    return parser.parse_args()


def main() -> int:
    load_local_env()
    args = parse_args()

    start_utc = parse_date_utc(args.start)
    end_utc = parse_date_utc(args.end, inclusive_end=True)
    if end_utc <= start_utc:
        print("[BACKTEST] failed: --end must be after --start.")
        return 1

    try:
        trades_df, metrics = run_backtest(
            start_utc=start_utc,
            end_utc=end_utc,
            max_hold_bars=max(1, int(args.max_hold)),
        )
    except Exception as error:
        print(f"[BACKTEST] failed: {error}")
        return 1

    saved_paths = save_backtest_outputs(
        trades_df,
        metrics,
        start_label=args.start,
        end_label=args.end,
    )

    print("FOREX BACKTEST RESULTS")
    print("----------------------")
    print(f"Window: {args.start} -> {args.end}")
    print(f"Starting balance: ${DEFAULT_STARTING_BALANCE:.2f}")
    print(f"Risk per trade: {DEFAULT_RISK_PER_TRADE * 100:.2f}%")
    print(f"Trades: {metrics['total_trades']}")
    print(f"Wins: {metrics['wins']}")
    print(f"Losses: {metrics['losses']}")
    print(f"Win rate: {metrics['win_rate']:.2f}%")
    print(f"Average R: {metrics['average_r']:.4f}")
    print(f"Profit factor: {metrics['profit_factor']:.2f}")
    print(f"Max drawdown: {metrics['max_drawdown_r']:.2f} R")
    print(f"Final balance: ${metrics['ending_balance']:.2f}")
    print(f"Saved to {Path(saved_paths['timestamped']).name}")
    print(f"Latest trades CSV: {saved_paths['latest']}")
    print(f"Summary JSON: {saved_paths['summary']}")
    print(f"Equity curve: {EQUITY_CURVE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
