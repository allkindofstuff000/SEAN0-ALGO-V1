"""
VWAP + Supertrend live signal bot.

Polls OANDA XAUUSD M5 candles every 60s, evaluates the LAST CLOSED bar,
and fires a signal on a Supertrend flip that agrees with the daily-anchored VWAP.
Session-filtered (12-21 UTC). Signals go to Telegram + Mongo (visible in /signals).

Deduplicates: only one signal per candle timestamp per lifetime of the state file.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import signal
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from core.data_fetcher import DataFetcher
from core.indicator_engine import IndicatorEngine
from strategies.vwap_supertrend import config as cfg
from strategies.vwap_supertrend.backtester import add_supertrend, add_session_vwap

try:
    from core.telegram_bot import TelegramNotifier
    _TG_OK = True
except Exception as _tg_exc:
    _TG_OK = False
    TelegramNotifier = None  # type: ignore

try:
    from core.mongo_store import save_live_signal as _save_live_signal
    _MONGO_OK = True
except Exception:
    _MONGO_OK = False

    def _save_live_signal(**_):
        return None


ROOT = Path(__file__).resolve().parent
STATE_PATH = ROOT / "state_vwap_st.txt"
POLL_SECS = 60
CANDLE_COUNT = 300  # ~25h of M5, ample for Supertrend warmup + daily VWAP

# ── Risk guards (env-overridable) ───────────────────────────────────────────
MAX_SIGNALS_PER_DAY = 5   # cap fires per UTC day  (env: VWAP_MAX_SIGNALS_PER_DAY)
COOLDOWN_BARS = 3         # min M5 bars between fires (env: VWAP_COOLDOWN_BARS)
# In-memory risk state (resets on restart — acceptable for a signal bot).
_RISK: dict = {"day": None, "count": 0, "last_fired_ts": None}

LOG = logging.getLogger("vwap-st.live")


def _in_session(ts: pd.Timestamp) -> bool:
    return cfg.SESSION_START_HOUR <= ts.hour < cfg.SESSION_END_HOUR


def _load_last_signal_ts() -> str | None:
    if not STATE_PATH.exists():
        return None
    try:
        return STATE_PATH.read_text().strip() or None
    except Exception:
        return None


def _save_last_signal_ts(ts: str) -> None:
    try:
        STATE_PATH.write_text(ts)
    except Exception as e:
        LOG.warning("state save failed: %s", e)


def _format_message(direction: str, entry: float, sl: float, tp: float, atr: float, candle_ts: str) -> str:
    arrow = "🟢 BUY" if direction == "BUY" else "🔴 SELL"
    return (
        f"*VWAP + Supertrend* — {arrow}\n"
        f"Symbol: XAUUSD  ·  M5\n"
        f"Candle (UTC): `{candle_ts}`\n"
        f"Entry: `{entry:.2f}`\n"
        f"SL:    `{sl:.2f}`\n"
        f"TP:    `{tp:.2f}`\n"
        f"ATR:   {atr:.2f}  ·  RR 1:2  ·  Risk {int(cfg.DEFAULT_RISK_PER_TRADE * 100)}%"
    )


async def _cycle(
    fetcher: DataFetcher,
    ind_engine: IndicatorEngine,
    tg,
    last_signal_ts: str | None,
) -> str | None:
    df = await asyncio.to_thread(
        fetcher.fetch_oanda, "5m", CANDLE_COUNT
    )
    if df is None or len(df) < 30:
        LOG.warning("insufficient candles (%s)", 0 if df is None else len(df))
        return last_signal_ts

    df = ind_engine.add_indicators(df)
    df = add_supertrend(df, cfg.SUPERTREND_PERIOD, cfg.SUPERTREND_MULTIPLIER)
    df = add_session_vwap(df)

    # Evaluate the LAST CLOSED bar. fetch_oanda drops the in-progress candle,
    # so df[-1] IS the most recent completed bar — that's the signal bar.
    i = len(df) - 1
    row = df.iloc[i]
    ts = pd.Timestamp(row["timestamp"])
    ts_str = str(ts)[:19]

    if ts_str == last_signal_ts:
        return last_signal_ts  # already evaluated this bar

    if not _in_session(ts):
        LOG.info("out-of-session %s (hour %s), skip", ts_str, ts.hour)
        return ts_str

    atr = row.get("atr14")
    vwap = row.get("vwap")
    d_now = row.get("st_dir")
    d_prev = df.iloc[i - 1].get("st_dir")
    if pd.isna(atr) or pd.isna(vwap) or pd.isna(d_now) or pd.isna(d_prev):
        LOG.info("indicators not ready @ %s", ts_str)
        return last_signal_ts

    close = float(row["close"])  # signal-bar close — used only for the entry condition
    direction: str | None = None
    if d_now == 1 and d_prev == -1 and close > float(vwap):
        direction = "BUY"
    elif d_now == -1 and d_prev == 1 and close < float(vwap):
        direction = "SELL"

    if direction is None:
        LOG.info(
            "no flip @ %s close=%.2f vwap=%.2f st_dir=%s->%s",
            ts_str, close, float(vwap), int(d_prev), int(d_now),
        )
        return ts_str

    # ── Risk guards: daily cap + cooldown between fires ─────────────────────
    utc_day = ts.date()
    if _RISK["day"] != utc_day:
        _RISK["day"] = utc_day
        _RISK["count"] = 0
    if _RISK["count"] >= MAX_SIGNALS_PER_DAY:
        LOG.info("risk: daily cap %d reached, skip %s @ %s", MAX_SIGNALS_PER_DAY, direction, ts_str)
        return ts_str
    if _RISK["last_fired_ts"] is not None:
        gap_bars = (ts - _RISK["last_fired_ts"]) / pd.Timedelta(minutes=5)
        if gap_bars < COOLDOWN_BARS:
            LOG.info("risk: cooldown %.0f/%d bars, skip %s @ %s", gap_bars, COOLDOWN_BARS, direction, ts_str)
            return ts_str

    # ── Entry alignment with the backtest ──────────────────────────────────
    # The backtest fills at the NEXT bar's open — you can't fill at a close you
    # just watched print. Live, the honest equivalent is the current market
    # price the instant the signal confirms, so we price the entry off a fresh
    # live snapshot rather than the signal-bar close. Falls back to the close
    # if the snapshot call fails.
    entry = close
    try:
        snap = await asyncio.to_thread(fetcher.fetch_live_market_snapshot, "1m")
        if snap and snap.get("close"):
            entry = float(snap["close"])
    except Exception as e:
        LOG.warning("live snapshot failed (%s); using signal-bar close as entry", e)

    atr_val = float(atr)
    sl_dist = atr_val * cfg.STOP_LOSS_ATR_MULTIPLIER
    tp_dist = atr_val * cfg.TAKE_PROFIT_ATR_MULTIPLIER
    if direction == "BUY":
        sl = entry - sl_dist
        tp = entry + tp_dist
    else:
        sl = entry + sl_dist
        tp = entry - tp_dist

    LOG.info(
        "SIGNAL %s @ %s signalClose=%.2f entry=%.2f sl=%.2f tp=%.2f atr=%.4f",
        direction, ts_str, close, entry, sl, tp, atr_val,
    )

    telegram_sent = False
    if tg is not None:
        try:
            msg = _format_message(direction, entry, sl, tp, atr_val, ts_str)
            # send_message is a coroutine — await it directly. (Wrapping it in
            # asyncio.to_thread only schedules the coroutine object in a worker
            # thread and never awaits it, so the send silently no-ops.)
            ok = await tg.send_message(msg)
            telegram_sent = bool(ok)
            LOG.info("telegram: %s", "sent" if telegram_sent else "not_sent")
        except Exception as e:
            LOG.warning("telegram failed: %s", e)

    if _MONGO_OK:
        try:
            await asyncio.to_thread(
                _save_live_signal,
                symbol="XAUUSD",
                direction=direction,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                atr=atr_val,
                session=f"{cfg.SESSION_START_HOUR}-{cfg.SESSION_END_HOUR}UTC",
                market_regime="trend",
                trend_alignment=True,   # Supertrend flip agrees with daily VWAP
                price_trigger=True,     # the flip itself is the entry trigger
                reason="vwap_supertrend_flip",
                strategy="vwap-st",
                strategyName="VWAP+ST",
                signal_kind="vwap_supertrend",
                telegram_sent=telegram_sent,
                candle_time_utc=ts_str,
                timestamp=int(dt.datetime.utcnow().timestamp()),
            )
            LOG.info("mongo: saved")
        except Exception as e:
            LOG.warning("mongo save failed: %s", e)

    _RISK["count"] += 1
    _RISK["last_fired_ts"] = ts
    LOG.info("risk: fired %d/%d today", _RISK["count"], MAX_SIGNALS_PER_DAY)

    _save_last_signal_ts(ts_str)
    return ts_str


async def run() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    load_dotenv(ROOT / ".env")

    global MAX_SIGNALS_PER_DAY, COOLDOWN_BARS
    MAX_SIGNALS_PER_DAY = int(os.getenv("VWAP_MAX_SIGNALS_PER_DAY", str(MAX_SIGNALS_PER_DAY)))
    COOLDOWN_BARS = int(os.getenv("VWAP_COOLDOWN_BARS", str(COOLDOWN_BARS)))

    fetcher = DataFetcher(min_candles=CANDLE_COUNT)
    try:
        fetcher.startup_check()
    except Exception as e:
        LOG.warning("fetcher startup_check failed: %s", e)

    ind_engine = IndicatorEngine()

    tg = None
    if _TG_OK:
        try:
            tg = TelegramNotifier(
                token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
                chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
            )
            LOG.info("telegram: ready")
        except Exception as e:
            LOG.warning("telegram init failed: %s -- running signal-silent", e)
    else:
        LOG.warning("telegram module unavailable -- running signal-silent")

    LOG.info(
        "started; session %d-%d UTC; ST(%d, %.1f); SL %.1fx ATR / TP %.1fx ATR (RR 1:2); poll %ds",
        cfg.SESSION_START_HOUR, cfg.SESSION_END_HOUR,
        cfg.SUPERTREND_PERIOD, cfg.SUPERTREND_MULTIPLIER,
        cfg.STOP_LOSS_ATR_MULTIPLIER, cfg.TAKE_PROFIT_ATR_MULTIPLIER,
        POLL_SECS,
    )

    last = _load_last_signal_ts()
    if last:
        LOG.info("resuming; last signal ts %s", last)

    stop = asyncio.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(sig, stop.set)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop.set())

    while not stop.is_set():
        try:
            last = await _cycle(fetcher, ind_engine, tg, last)
        except Exception as e:
            LOG.exception("cycle error: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECS)
        except asyncio.TimeoutError:
            pass

    LOG.info("stopped")


if __name__ == "__main__":
    asyncio.run(run())
