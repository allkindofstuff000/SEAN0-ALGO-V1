# SEAN-ALGO — Project Guide

XAUUSD (gold) algorithmic **trading-signal** engine for the forex market. It does
**not** place live orders — it detects setups, sends Telegram alerts, and logs
them to MongoDB. Three parts:

1. **Dashboard** — React/Vite in `frontend/` (Tailwind v4 + wouter + shadcn/ui),
   built into `web/static/`.
2. **Backend** — FastAPI in `web/web_server.py`, serves the dashboard + JSON APIs.
3. **Live signal bots** — poll OANDA price data; on a trigger they alert Telegram
   and save to Mongo.

Data source: OANDA **practice** API (`XAU_USD`). Everything runs in **UTC**
internally; the dashboard **displays** times in **UTC+6 (Asia/Dhaka)**.

## Repo / branch
- GitHub: `https://github.com/allkindofstuff000/SEAN0-ALGO-V1`
- Active branch: **`feature/react-web-dashboard`** (the current work is here, not `main`).

## Strategies
- **RSI EMA** (primary): M15 EMA50/200 trend + RSI(14) 55/45 + M5 breakout of the
  prior candle. RR 1:1, ~55.8% backtest win. Live process `main.py`
  (`sean-algo.service`); started/stopped via `POST /api/bot/rsi-ema/{start|stop}`.
- **VWAP + Supertrend**: Supertrend(10, 3.0) direction **flip** confirmed by daily
  session VWAP. SL 1.5×ATR / TP 3.0×ATR (RR 1:2), ~50.5% win, PF 1.80. Trades only
  **12:00–21:00 UTC** (18:00–03:00 Dhaka). Selective → fewer signals than RSI EMA.
  Live process `vwap_st_live.py` (`vwap-st.service`).
- **BTC RSI EMA**: the *same* RSI EMA strategy on **BTCUSD**, 24/7 (gold session
  filter bypassed). Data from the Binance public mirror `data-api.binance.vision`
  (`api.binance.com` is HTTP-451 geo-blocked on the VPS) via `core/btc_fetcher.py`
  (parallel paginated fetch); backtester `strategies/rsi_btc/backtester.py` reuses
  the XAU engine's signal/simulate/metrics on BTC klines (no bid/ask → mid fills).
  Endpoints `/api/btc/{price,candles/{tf},stream/{tf},backtest}`; backtests tagged
  `strategy:"rsi-btc"`. **Live DISPLAY** (chart + header price + SSE stream) uses
  **Coinbase** spot instead (`fetch_spot_price` / `fetch_display_candles`, paginated)
  — fast (~0.1s), real-time, and matches TradingView; the Binance mirror is
  CDN-cached (in-progress candle lags), so the stream overlays the live Coinbase
  `/ticker` onto the forming candle's close. Binance mirror stays for backtests +
  the bot (deeper history). Frontend page `/btc-rsi-ema` (`BtcRsiEma.tsx`) clones the RSI
  EMA page. **Live bot `btc_rsi_ema_live.py` (`btc-rsi-ema.service`)** polls BTC M5
  every 60s, evaluates the last closed bar with `engine.evaluate_signal` (session
  bypassed, RR 1:1 SL/TP 1.5×ATR), re-anchors entry to live price, and fires to
  Telegram + Mongo. Log `/var/log/btc-rsi-ema.log`.

## Production VPS
- Host `45.132.242.134` (Hostinger, Ubuntu 24.04, hostname `srv1935826`, root).
- App dir `/opt/sean0algo`; dashboard `http://45.132.242.134/` (nginx `:80 → :8000`).
- Config `/opt/sean0algo/.env` (OANDA practice, local Mongo `:27017`, Telegram).
- VPS Python: `/opt/sean0algo/.venv/bin/python` (3.12).
- systemd units (all normally `active`):
  - `sean0algo` → `web/web_server.py` (dashboard + API, `:8000`)
  - `sean-algo` → `main.py` (RSI EMA bot)
  - `vwap-st` → `vwap_st_live.py` (VWAP+ST bot); log `/var/log/vwap-st.log`
  - `signal-resolver` → `resolve_outcomes.py` (marks fired signals WIN/LOSS; daily
    Telegram heartbeat + stale-feed watchdog)
- Mongo collections: `live_signals`, `backtest_reports`, `bot_state`.

## Key API endpoints
- `GET /api/live/price` → `{price, time, initialized}`
- `GET /api/bot/status` → `{rsiEma, vwapSt, anyRunning, market}`
- `POST /backtest` → RSI EMA backtest · `POST /api/vwap-st/backtest` → VWAP+ST
- `GET /backtest/history` → saved runs · `GET /signals` → fired live signals
  (Signal History reads this)

## Frontend layout
Routes: `/` & `/live-bot` → `LiveBot`; `/rsi-ema` → `RsiEma`; `/vwap-st` → `VwapSt`.
Both strategy pages share the same shape: a **Backtest** tab (config sliders +
results + a nested Backtest History table) and a **Signal History** tab (fired
signals with win/loss summary, P&L per row, click-to-expand detail). Timezone
display helper: `frontend/src/lib/tz.ts` (`toUtcDate` / `fmtLocal`, UTC+6).

## Deployment
Passwordless SSH key at `~/.ssh/sean_vps_key` **on the original dev machine only**
(a different machine can edit code + push to GitHub but cannot deploy until a key
is installed there). Command shape:
```
ssh -o BatchMode=yes -o IdentitiesOnly=yes -i ~/.ssh/sean_vps_key root@45.132.242.134
```
- **Frontend**: `npm run build` in `frontend/`, tar `dist/` contents, scp to
  `/opt/sean0algo/`, extract with an **atomic `web/static` swap** (no restart —
  FastAPI serves `web/static/` directly).
- **Backend**: the VPS can drift from local — **pull the VPS copy first**, edit,
  `py_compile`-check under the VPS venv, back up as `*.bak_TS`, swap in, then
  restart **only** the owning service.

## Gotchas
- `core/telegram_bot.py` `send_message` is **async** — always `await` it, or
  `asyncio.run()` from a sync worker thread. Never `asyncio.to_thread(send_message)`
  (it builds a coroutine that's never awaited → the send silently no-ops).
- `save_live_signal()` (`core/mongo_store.py`) takes optional RSI-specific fields
  + `**extra`, so any strategy can persist a signal; tag with `strategy` /
  `strategyName` / `signal_kind`.
- Running a script from `/tmp` on the VPS needs `PYTHONPATH=/opt/sean0algo` for the
  `core` package to import.
- Windows dev host: PowerShell 5.1 breaks on non-ASCII in `.ps1`; base64-encode
  remote bash sent over ssh to avoid quote mangling.
- Weekend (Fri 22:00 → Sun 22:00 UTC) and the 21:00–22:00 UTC gold settlement break
  are normal quiet periods, not bugs.

## First steps for a new session
Read `git log --oneline -10`, confirm the 4 VPS services are `active` (if you have
VPS access) and `http://45.132.242.134/` loads, then summarize the state before
editing.
