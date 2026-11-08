"""NY opening-range breakout live signal bot — ETH by default (env CRYPTO_SYMBOL).

Every 30 s: pull closed M5 bars from the Binance mirror, build today's opening range
(RANGE_START + RANGE_MIN, anchored to NYORB_TZ — UTC by default) once it is complete,
and fire on the FIRST M5 close beyond it (one BUY and one SELL max per day, no new
entries at/after LAST_ENTRY). Stop = the other side of the range, target =
NYORB_TP_R x risk, and the signal carries `flat_at_utc` so the resolver closes it flat
at that time exactly like the backtest.

The day's breakouts are detected with the SAME `breakout_signals` the backtester
uses, so live == backtest: only the first close beyond the range counts, and a
first break that happened while the bot was down is marked as missed for the
day (never chased late, never replaced by a later re-break).

PAPER mode (env NYORB_PAPER=1, the default on the VPS since 2026-10-09): signals are
saved to Mongo (tagged paper) and scored by the resolver, but NOT sent to Telegram.
The 2026-10-09 re-validation (research/nyorb_dst_check.py) showed the strategy's
edge is confined to the US-winter weeks; in the current regime it is flat.

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
from zoneinfo import ZoneInfo

import pandas as pd
from dotenv import load_dotenv

from core.btc_fetcher import BtcFetcher, resolve_crypto
from core.heartbeat import beat as _heartbeat
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
    from core.mongo_store import configure_pending_queue as _configure_pending_queue
    _MONGO_OK = True
except Exception:  # pragma: no cover
    _MONGO_OK = False

    def _save_live_signal(**_):
        return None

    def _flush_pending():
        return 0

    def _configure_pending_queue(_tag):
        return None


ROOT = Path(__file__).resolve().parent
CRYPTO = resolve_crypto(os.getenv("CRYPTO_SYMBOL", "ETH"))
KEY = CRYPTO["key"]
DISPLAY_SYMBOL = CRYPTO["display"]
PAIR = CRYPTO["pair"]
STRATEGY_TAG = f"nyorb-{KEY.lower()}"
STATE_PATH = ROOT / f"state_nyorb_{KEY.lower()}.json"
POLL_SECS = 30
STALE_BAR_MAX_MIN = int(os.getenv("NYORB_STALE_BAR_MAX_MIN", "10"))


def _env_time(name: str, default: dt.time) -> dt.time:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    hh, mm = raw.split(":")[:2]
    return dt.time(int(hh), int(mm))


def _env_flag(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


TZ_NAME = os.getenv("NYORB_TZ", "UTC").strip() or "UTC"
TZ = ZoneInfo(TZ_NAME)
RANGE_START = _env_time("NYORB_RANGE_START", nyorb.RANGE_START)
LAST_ENTRY = _env_time("NYORB_LAST_ENTRY", nyorb.LAST_ENTRY)
FLAT_AT = _env_time("NYORB_FLAT_AT", nyorb.FLAT_AT)
TP_R = float(os.getenv("NYORB_TP_R", str(nyorb.TP_R)))
RANGE_MIN = int(os.getenv("NYORB_RANGE_MIN", str(nyorb.RANGE_MIN)))
LONG_ONLY = _env_flag("NYORB_LONG_ONLY")
PAPER = _env_flag("NYORB_PAPER")
MAX_CHASE = float(os.getenv("NYORB_MAX_CHASE", "0.25"))   # skip if live price ran > this x range beyond the signal close

LOG = logging.getLogger(f"nyorb-{KEY.lower()}.live")


# ── per-day state (which directions already fired) ────────────────────────────
def _load_state() -> dict:
    try:
        if STATE_PATH.exists():
            d = json.loads(STATE_PATH.read_text())
            if isinstance(d, dict):
                return d
            LOG.warning("state file %s is not a dict — starting fresh", STATE_PATH.name)
    except Exception as e:  # noqa: BLE001
        LOG.warning("state file %s unreadable (%s) — starting fresh; both directions re-enabled for today", STATE_PATH.name, e)
    return {}


def _save_state(state: dict) -> None:
    try:
        tmp = STATE_PATH.with_name(STATE_PATH.name + ".tmp")
        tmp.write_text(json.dumps(state))
        os.replace(tmp, STATE_PATH)
    except Exception as e:  # noqa: BLE001
        LOG.warning("state save failed: %s", e)


def _fmt_local(ts: pd.Timestamp) -> str:
    return f"{pd.Timestamp(ts).tz_convert(TZ):%H:%M} {TZ_NAME if TZ_NAME != 'UTC' else 'UTC'}"


def _format_message(direction: str, entry: float, sl: float, tp: float, rng: dict, ts: pd.Timestamp, flat_utc: pd.Timestamp, risk_pct: int) -> str:
    arrow = "🟢 BUY" if direction == "BUY" else "🔴 SELL"
    bd = (pd.Timestamp(ts).tz_convert("UTC") + pd.Timedelta(minutes=5, hours=6)).strftime("%Y-%m-%d %H:%M")
    flat_bd = (flat_utc + pd.Timedelta(hours=6)).strftime("%H:%M")
    tag = "🧪 PAPER · " if PAPER else ""
    return (
        f"📐 *{tag}NY Open Range Breakout — {PAIR}* — {arrow}\n"
        f"Symbol: {DISPLAY_SYMBOL}  ·  M5  ·  Binance\n"
        f"Range {_fmt_local(rng['start'])}-{_fmt_local(rng['end'])}: `{rng['low']:.2f}` – `{rng['high']:.2f}`\n"
        f"🕒 Signal (BD): `{bd}`  ·  enter now\n"
        f"Entry: `{entry:.2f}`\n"
        f"SL:    `{sl:.2f}`  (other side of the range)\n"
        f"TP:    `{tp:.2f}`  (RR 1:{TP_R:g})\n"
        f"⏰ FLAT at {flat_utc:%H:%M} UTC ({flat_bd} BD) if neither hits  ·  Risk {risk_pct}%"
    )


async def _cycle(fetcher: BtcFetcher, tg, state: dict, risk_pct: int, last_log: dict) -> None:
    now = pd.Timestamp.now(tz="UTC")
    now_l = now.tz_convert(TZ)
    today = now_l.normalize()                      # local midnight (tz-aware)
    key_day = str(today.date())

    def _log_once(msg: str, every_min: int = 15) -> None:
        last = last_log.get(msg)
        if last is None or (now - last) >= pd.Timedelta(minutes=every_min):
            LOG.info(msg)
            last_log[msg] = now
        # keep the throttle map small (keys embed bar times)
        for k in [k for k, v in last_log.items() if (now - v) > pd.Timedelta(days=1)]:
            last_log.pop(k, None)

    df = await asyncio.to_thread(fetcher.fetch_klines, "5m", 200, True)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)
    if df.empty:
        LOG.warning("no bars returned")
        return

    # Freshness guard: a lagging/partial mirror fetch must never make an old bar
    # look like "the latest closed bar".
    cur = df.iloc[-1]
    cur_ts = pd.Timestamp(cur["timestamp"])
    bar_age_min = (now - (cur_ts + pd.Timedelta(minutes=5))).total_seconds() / 60.0
    if bar_age_min > STALE_BAR_MAX_MIN:
        LOG.warning("stale data: newest closed bar %s is %.0f min old — not evaluating", str(cur_ts)[:16], bar_age_min)
        return
    _heartbeat(STRATEGY_TAG)

    bars = nyorb.localize_bars(df, TZ_NAME)        # session times are read on this clock
    rng = nyorb.day_range(bars, today, RANGE_START, RANGE_MIN)
    fired: list[str] = list(state.get(key_day, []))
    if rng is None:
        _log_once(f"range not complete yet ({RANGE_START:%H:%M}+{RANGE_MIN}min {TZ_NAME})", 30)
        return
    last_entry = today.replace(hour=LAST_ENTRY.hour, minute=LAST_ENTRY.minute)
    flat_ts = today.replace(hour=FLAT_AT.hour, minute=FLAT_AT.minute)
    flat_utc = flat_ts.tz_convert("UTC")

    # The day's breakouts, computed exactly like the backtester (first close beyond
    # the range per direction, previous close still inside, bars before LAST_ENTRY).
    day_bars = bars[(bars["timestamp"] >= today) & (bars["timestamp"] < today + pd.Timedelta(days=1))]
    sigs = nyorb.breakout_signals(day_bars, tp_r=TP_R, minutes=RANGE_MIN, long_only=LONG_ONLY,
                                  start=RANGE_START, last_entry=LAST_ENTRY, flat_at=FLAT_AT)
    cur_l = cur_ts.tz_convert(TZ)
    direction: str | None = None
    for s in sigs:
        d = s["dir"]
        if d in fired:
            continue
        s_ts = pd.Timestamp(s["timestamp"])
        if s_ts < cur_l:
            # The first break happened on an earlier bar (bot was down / data gap):
            # the backtest's trade for this direction is gone — never chase a later
            # re-break instead.
            LOG.info("missed the first %s break @ %s (range %.2f-%.2f) — %s is done for %s",
                     d, str(s_ts)[:16], rng["low"], rng["high"], d, key_day)
            fired.append(d)
            state[key_day] = fired
            _save_state(state)
        elif s_ts == cur_l:
            direction = d
            break

    if now_l >= last_entry and direction is None:
        _log_once(f"entry window closed for {key_day} (range {rng['low']:.2f}-{rng['high']:.2f}; fired {fired or 'none'})", 60)
        return
    if direction is None:
        c = float(cur["close"])
        _log_once(f"no break @ {cur_l:%H:%M} close {c:.2f} (range {rng['low']:.2f}-{rng['high']:.2f}; fired {fired or 'none'})", 15)
        return

    ts = cur_ts
    ts_str = str(ts)[:19]
    c = float(cur["close"])
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

    LOG.info("SIGNAL%s %s @ %s entry=%.2f sl=%.2f tp=%.2f range=%.2f-%.2f flat=%s UTC",
             " (paper)" if PAPER else "", direction, ts_str, entry, sl, tp, rng["low"], rng["high"], f"{flat_utc:%H:%M}")
    telegram_sent = False
    if tg is not None and not PAPER:
        try:
            telegram_sent = bool(await tg.send_message(_format_message(direction, entry, sl, tp, rng, ts, flat_utc, risk_pct)))
            LOG.info("telegram: %s", "sent" if telegram_sent else "not_sent")
        except Exception as e:  # noqa: BLE001
            LOG.warning("telegram failed: %s", e)
    elif PAPER:
        LOG.info("telegram: skipped (paper mode)")

    if _MONGO_OK:
        try:
            await asyncio.to_thread(
                _save_live_signal,
                symbol=DISPLAY_SYMBOL, direction=direction, entry_price=entry, stop_loss=sl, take_profit=tp,
                atr=height, score=1, score_threshold=1,
                session=f"{RANGE_START:%H:%M}-{FLAT_AT:%H:%M} {TZ_NAME}", market_regime="breakout",
                regime_confidence=1.0, trend_alignment=True, price_trigger=True, rsi_filter=None, atr_expansion=None,
                reason="ny_open_range_breakout", strategy=STRATEGY_TAG, strategyName=f"{KEY} NY Open Range",
                signal_kind="nyorb", telegram_sent=telegram_sent, candle_time_utc=ts_str,
                entry_time_utc=str(ts + pd.Timedelta(minutes=5))[:19],
                flat_at_utc=str(flat_utc)[:19],
                range_high=rng["high"], range_low=rng["low"], tp_r=TP_R,
                cost_pct=nyorb.COST, paper=PAPER, anchor_tz=TZ_NAME,
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
    # .env may define the paper flag / anchor; re-read after load_dotenv
    global PAPER, TZ_NAME, TZ, RANGE_START, LAST_ENTRY, FLAT_AT
    PAPER = _env_flag("NYORB_PAPER")
    TZ_NAME = os.getenv("NYORB_TZ", "UTC").strip() or "UTC"
    TZ = ZoneInfo(TZ_NAME)
    RANGE_START = _env_time("NYORB_RANGE_START", nyorb.RANGE_START)
    LAST_ENTRY = _env_time("NYORB_LAST_ENTRY", nyorb.LAST_ENTRY)
    FLAT_AT = _env_time("NYORB_FLAT_AT", nyorb.FLAT_AT)
    _configure_pending_queue(STRATEGY_TAG)

    risk_pct = int(float(os.getenv(f"{KEY}_RISK_PCT", os.getenv("BTC_RISK_PCT", "2"))))
    fetcher = BtcFetcher(KEY)
    tg = None
    if _TG_OK:
        try:
            tg = TelegramNotifier(token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(), chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip())
            LOG.info("telegram: ready")
        except Exception as e:  # noqa: BLE001
            LOG.warning("telegram init failed: %s -- running signal-silent", e)
    LOG.info("started; %s NY open-range breakout: range %s+%dmin, last entry %s, flat %s (%s), TP %.2gR, stop = far side of range, "
             "long_only=%s, paper=%s; poll %ds",
             DISPLAY_SYMBOL, RANGE_START.strftime("%H:%M"), RANGE_MIN, LAST_ENTRY.strftime("%H:%M"), FLAT_AT.strftime("%H:%M"),
             TZ_NAME, TP_R, LONG_ONLY, PAPER, POLL_SECS)
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
