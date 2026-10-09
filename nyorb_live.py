"""NY opening-range breakout live signal bot — ETH by default (env CRYPTO_SYMBOL).

Every 30 s: pull closed M5 bars from the Binance mirror, build today's 13:30-14:30 UTC
range once it is complete, and fire on the FIRST M5 close beyond it (one BUY and one
SELL max per UTC day, nothing new at/after 20:00 UTC). Stop = the other side of the
range, target = NYORB_TP_R x risk, and the signal carries `flat_at_utc` (21:00 UTC) so
the resolver closes it flat at that time exactly like the backtest.

Only the LATEST closed bar can fire, and only if the previous bar was still inside
the range — a breakout missed while the bot was down is never chased late.

Runs as the systemd template instance `nyorb@eth.service`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os
import signal as _signal
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from core.btc_fetcher import BtcFetcher, resolve_crypto
from core.signal_guard import check_signal
from strategies.nyorb import engine as nyorb

try:
    from core.telegram_bot import TelegramNotifier
    _TG_OK = True
except Exception:  # pragma: no cover
    _TG_OK = False
    TelegramNotifier = None  # type: ignore

try:
    from core.mongo_store import save_live_signal_queued as _save_live_signal
    from core.mongo_store import flush_pending_signals as _flush_pending
    _MONGO_OK = True
except Exception:  # pragma: no cover
    _MONGO_OK = False

    def _save_live_signal(**_):
        return None

    def _flush_pending():
        return 0


ROOT = Path(__file__).resolve().parent
CRYPTO = resolve_crypto(os.getenv("CRYPTO_SYMBOL", "ETH"))
KEY = CRYPTO["key"]
DISPLAY_SYMBOL = CRYPTO["display"]
PAIR = CRYPTO["pair"]
STRATEGY_TAG = f"nyorb-{KEY.lower()}"
STATE_PATH = ROOT / f"state_nyorb_{KEY.lower()}.json"
POLL_SECS = 30

TP_R = float(os.getenv("NYORB_TP_R", str(nyorb.TP_R)))
RANGE_MIN = int(os.getenv("NYORB_RANGE_MIN", str(nyorb.RANGE_MIN)))
LONG_ONLY = os.getenv("NYORB_LONG_ONLY", "0").strip().lower() in ("1", "true", "yes")
MAX_CHASE = float(os.getenv("NYORB_MAX_CHASE", "0.25"))   # skip if live price ran > this x range beyond the signal close

LOG = logging.getLogger(f"nyorb-{KEY.lower()}.live")


# ── per-day state (which directions already fired) ────────────────────────────
def _load_state() -> dict:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text())
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state))
    except Exception as e:  # noqa: BLE001
        LOG.warning("state save failed: %s", e)


def _format_message(direction: str, entry: float, sl: float, tp: float, rng: dict, ts: pd.Timestamp, risk_pct: int) -> str:
    arrow = "🟢 BUY" if direction == "BUY" else "🔴 SELL"
    bd = (pd.Timestamp(ts) + pd.Timedelta(minutes=5, hours=6)).strftime("%Y-%m-%d %H:%M")
    return (
        f"📐 *NY Open Range Breakout — {PAIR}* — {arrow}\n"
        f"Symbol: {DISPLAY_SYMBOL}  ·  M5  ·  Binance\n"
        f"Range 13:30-{(rng['end']).strftime('%H:%M')} UTC: `{rng['low']:.2f}` – `{rng['high']:.2f}`\n"
        f"🕒 Signal (BD): `{bd}`  ·  enter now\n"
        f"Entry: `{entry:.2f}`\n"
        f"SL:    `{sl:.2f}`  (other side of the range)\n"
        f"TP:    `{tp:.2f}`  (RR 1:{TP_R:g})\n"
        f"⏰ FLAT at 21:00 UTC (03:00 BD) if neither hits  ·  Risk {risk_pct}%"
    )


async def _cycle(fetcher: BtcFetcher, tg, state: dict, risk_pct: int, last_log: dict) -> None:
    now = pd.Timestamp.now(tz="UTC")
    today = now.normalize()
    df = await asyncio.to_thread(fetcher.fetch_klines, "5m", 200, True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    rng = nyorb.day_range(df, today, nyorb.RANGE_START, RANGE_MIN)
    key_day = str(today.date())
    fired: list[str] = state.get(key_day, [])

    def _log_once(msg: str, every_min: int = 15) -> None:
        last = last_log.get(msg)
        if last is None or (now - last) >= pd.Timedelta(minutes=every_min):
            LOG.info(msg)
            last_log[msg] = now

    if rng is None:
        _log_once(f"range not complete yet (13:30-{(pd.Timestamp(today) + pd.Timedelta(hours=13, minutes=30 + RANGE_MIN)):%H:%M} UTC)", 30)
        return
    last_entry = today + pd.Timedelta(hours=nyorb.LAST_ENTRY.hour, minutes=nyorb.LAST_ENTRY.minute)
    flat_ts = today + pd.Timedelta(hours=nyorb.FLAT_AT.hour, minutes=nyorb.FLAT_AT.minute)
    if now >= last_entry:
        _log_once(f"entry window closed for {key_day} (range {rng['low']:.2f}-{rng['high']:.2f}; fired {fired or 'none'})", 60)
        return

    # Only the latest closed bar may fire, and only as the FIRST close beyond the range.
    watch = df[(df["timestamp"] >= rng["end"]) & (df["timestamp"] < last_entry)]
    if len(watch) < 1:
        _log_once(f"range set {rng['low']:.2f}-{rng['high']:.2f} (height {rng['high']-rng['low']:.2f}); waiting for the first close beyond it", 15)
        return
    cur = watch.iloc[-1]
    prev_close = float(watch.iloc[-2]["close"]) if len(watch) >= 2 else None
    c = float(cur["close"])
    direction = None
    if "BUY" not in fired and c > rng["high"] and (prev_close is None or prev_close <= rng["high"]):
        direction = "BUY"
    elif (not LONG_ONLY) and "SELL" not in fired and c < rng["low"] and (prev_close is None or prev_close >= rng["low"]):
        direction = "SELL"
    if direction is None:
        _log_once(f"no break @ {cur['timestamp']:%H:%M} close {c:.2f} (range {rng['low']:.2f}-{rng['high']:.2f}; fired {fired or 'none'})", 15)
        return

    ts = pd.Timestamp(cur["timestamp"])
    ts_str = str(ts)[:19]
    height = rng["high"] - rng["low"]

    # Entry = live price now (the next bar's open in the backtest). Never chase a runaway.
    entry = c
    try:
        snap = await asyncio.to_thread(fetcher.fetch_live_price)
        if snap and snap.get("price"):
            entry = float(snap["price"])
    except Exception as e:  # noqa: BLE001
        LOG.warning("live price failed (%s); using the signal-bar close", e)
    chased = (entry - c) if direction == "BUY" else (c - entry)
    if chased > MAX_CHASE * height:
        LOG.info("skip %s @ %s: price already %.2f beyond the breakout close (> %.0f%% of the range)", direction, ts_str, chased, MAX_CHASE * 100)
        fired.append(direction); state[key_day] = fired; _save_state(state)
        return

    sl = rng["low"] if direction == "BUY" else rng["high"]
    risk = (entry - sl) if direction == "BUY" else (sl - entry)
    if risk <= 0:
        LOG.info("skip %s @ %s: live price back inside the range", direction, ts_str)
        return
    tp = entry + TP_R * risk if direction == "BUY" else entry - TP_R * risk

    ok, why = check_signal(direction=direction, entry=entry, stop_loss=sl, take_profit=tp, atr=height,
                           ref_price=c, recent_high=float(cur["high"]), recent_low=float(cur["low"]))
    if not ok:
        LOG.warning("signal REJECTED by sanity guard: %s (%s @ %s)", why, direction, ts_str)
        return

    LOG.info("SIGNAL %s @ %s entry=%.2f sl=%.2f tp=%.2f range=%.2f-%.2f", direction, ts_str, entry, sl, tp, rng["low"], rng["high"])
    telegram_sent = False
    if tg is not None:
        try:
            telegram_sent = bool(await tg.send_message(_format_message(direction, entry, sl, tp, rng, ts, risk_pct)))
            LOG.info("telegram: %s", "sent" if telegram_sent else "not_sent")
        except Exception as e:  # noqa: BLE001
            LOG.warning("telegram failed: %s", e)

    if _MONGO_OK:
        try:
            await asyncio.to_thread(
                _save_live_signal,
                symbol=DISPLAY_SYMBOL, direction=direction, entry_price=entry, stop_loss=sl, take_profit=tp,
                atr=height, score=1, score_threshold=1, session="13:30-21:00 UTC", market_regime="breakout",
                regime_confidence=1.0, trend_alignment=True, price_trigger=True, rsi_filter=None, atr_expansion=None,
                reason="ny_open_range_breakout", strategy=STRATEGY_TAG, strategyName=f"{KEY} NY Open Range",
                signal_kind="nyorb", telegram_sent=telegram_sent, candle_time_utc=ts_str,
                entry_time_utc=str(ts + pd.Timedelta(minutes=5))[:19],
                flat_at_utc=str(flat_ts)[:19],
                range_high=rng["high"], range_low=rng["low"], tp_r=TP_R,
                timestamp=int(dt.datetime.now(dt.timezone.utc).timestamp()),
            )
            LOG.info("mongo: saved")
        except Exception as e:  # noqa: BLE001
            LOG.warning("mongo save failed: %s", e)

    fired.append(direction)
    state[key_day] = fired
    # keep the state file small: today + yesterday only
    for k in [k for k in state if k not in (key_day, str((today - pd.Timedelta(days=1)).date()))]:
        state.pop(k, None)
    _save_state(state)


async def run() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_dotenv(ROOT / ".env")
    risk_pct = int(float(os.getenv(f"{KEY}_RISK_PCT", os.getenv("BTC_RISK_PCT", "2"))))
    fetcher = BtcFetcher(KEY)
    tg = None
    if _TG_OK:
        try:
            tg = TelegramNotifier(token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip())
            LOG.info("telegram: ready")
        except Exception as e:  # noqa: BLE001
            LOG.warning("telegram init failed: %s -- running signal-silent", e)
    LOG.info("started; %s NY open-range breakout: range 13:30+%dmin UTC, TP %.2gR, stop = far side of range, flat 21:00 UTC, long_only=%s; poll %ds",
             DISPLAY_SYMBOL, RANGE_MIN, TP_R, LONG_ONLY, POLL_SECS)
    state = _load_state()
    last_log: dict = {}
    stop = asyncio.Event()
    for s in (_signal.SIGINT, _signal.SIGTERM):
        try:
            asyncio.get_event_loop().add_signal_handler(s, stop.set)
        except NotImplementedError:
            _signal.signal(s, lambda *_: stop.set())
    while not stop.is_set():
        try:
            await _cycle(fetcher, tg, state, risk_pct, last_log)
            if _MONGO_OK:
                await asyncio.to_thread(_flush_pending)
        except Exception as e:  # noqa: BLE001
            LOG.exception("cycle error: %s", e)
        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_SECS)
        except asyncio.TimeoutError:
            pass
    LOG.info("stopped")


if __name__ == "__main__":
    asyncio.run(run())
