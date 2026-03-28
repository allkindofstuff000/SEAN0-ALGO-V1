"""One-off backtest: RSI EMA ETH/USDT — $5000, 3% risk, 30 days, all trades."""
import sys, time, requests
sys.path.insert(0, ".")
from strategies.rsi_eth.optimizer import _ema, _rsi, _atr, _check_entry

# ── Fetch 30 days of 15M candles ─────────────────────────
print("Fetching 30 days of ETH/USDT 15M candles...")
all_candles = []
end_time = int(time.time() * 1000)
start_time = end_time - (30 * 24 * 60 * 60 * 1000)
cursor = start_time
while cursor < end_time:
    url = (f"https://api.binance.com/api/v3/klines?symbol=ETHUSDT"
           f"&interval=15m&limit=1000&startTime={cursor}")
    data = requests.get(url, timeout=15).json()
    if not data:
        break
    for k in data:
        all_candles.append({
            "time": k[0] / 1000, "open": float(k[1]), "high": float(k[2]),
            "low": float(k[3]), "close": float(k[4]), "volume": float(k[5]),
        })
    cursor = int(data[-1][0]) + 1
    time.sleep(0.15)

seen = set()
candles = []
for c in all_candles:
    if c["time"] not in seen:
        seen.add(c["time"])
        candles.append(c)
candles.sort(key=lambda c: c["time"])
print(f"Loaded {len(candles)} candles")
print(f"Range: {time.strftime('%Y-%m-%d', time.gmtime(candles[0]['time']))} "
      f"to {time.strftime('%Y-%m-%d', time.gmtime(candles[-1]['time']))}")

# ── Parameters ───────────────────────────────────────────
RSI_P = 7; RSI_OB = 70; RSI_OS = 35
EMA_F = 9; EMA_S = 50
ENTRY = "C"; ATR_M = 1.0; TP_R = 2.5
BALANCE_START = 5000.0; RISK_PCT = 0.03

# ── Indicators ───────────────────────────────────────────
closes = [c["close"] for c in candles]
rsi_v = _rsi(closes, RSI_P)
ema_fv = _ema(closes, EMA_F)
ema_sv = _ema(closes, EMA_S)
atr_v = _atr(candles, 14)

# ── Simulate ─────────────────────────────────────────────
balance = BALANCE_START
trades = []
open_t = None
start_i = max(RSI_P, EMA_S) + 1

for i in range(start_i, len(candles) - 1):
    c = candles[i]

    if open_t:
        is_long = open_t["dir"] == "LONG"
        hit_sl = (c["low"] <= open_t["sl"]) if is_long else (c["high"] >= open_t["sl"])
        hit_tp = (c["high"] >= open_t["tp"]) if is_long else (c["low"] <= open_t["tp"])

        if hit_sl:
            pnl = -open_t["risk_amt"]
            balance += pnl
            open_t.update(result="LOSS", pnl=pnl, exitPrice=open_t["sl"],
                          exitTime=c["time"], balance=balance)
            trades.append(open_t); open_t = None; continue
        if hit_tp:
            pnl = open_t["risk_amt"] * TP_R
            balance += pnl
            open_t.update(result="WIN", pnl=pnl, exitPrice=open_t["tp"],
                          exitTime=c["time"], balance=balance)
            trades.append(open_t); open_t = None; continue
        continue

    if any(v is None for v in [rsi_v[i], rsi_v[i-1], ema_fv[i], ema_sv[i], atr_v[i]]):
        continue

    sig = _check_entry(
        candles[i]["close"],
        rsi_v[i], rsi_v[i-1],
        ema_fv[i], ema_fv[i-1],
        ema_sv[i], ema_sv[i-1],
        ENTRY, RSI_OB, RSI_OS,
    )
    if not sig:
        continue

    price = c["close"]
    atr = atr_v[i]
    risk_amt = balance * RISK_PCT

    if sig == "LONG":
        sl = price - atr * ATR_M
        tp = price + (price - sl) * TP_R
    else:
        sl = price + atr * ATR_M
        tp = price - (sl - price) * TP_R

    open_t = dict(num=len(trades)+1, dir=sig, entry=price, sl=sl, tp=tp,
                  openTime=c["time"], risk_amt=risk_amt,
                  rsi=round(rsi_v[i], 1), ema_f=round(ema_fv[i], 2),
                  ema_s=round(ema_sv[i], 2))

# ── Metrics ──────────────────────────────────────────────
wins = [t for t in trades if t["result"] == "WIN"]
losses = [t for t in trades if t["result"] == "LOSS"]
gross_w = sum(t["pnl"] for t in wins)
gross_l = abs(sum(t["pnl"] for t in losses))
pf = round(gross_w / gross_l, 2) if gross_l > 0 else gross_w
total_pnl = sum(t["pnl"] for t in trades)

peak = BALANCE_START; max_dd = 0; bt = BALANCE_START
for t in trades:
    bt += t["pnl"]
    if bt > peak: peak = bt
    dd = peak - bt
    if dd > max_dd: max_dd = dd

# ── Print ────────────────────────────────────────────────
sep = "=" * 110
dash = "-" * 110
print()
print(sep)
print("  RSI EMA ETH/USDT — FULL BACKTEST REPORT")
print(f"  $5,000 starting | 3% risk/trade | 30 days | 15M")
print(f"  RSI({RSI_P}) OB/OS({RSI_OB}/{RSI_OS}) EMA({EMA_F}/{EMA_S}) ATR({ATR_M}x) TP({TP_R}R) Entry: {ENTRY}")
print(sep)
print(f"  Total Trades:    {len(trades)}")
print(f"  Winners:         {len(wins)}")
print(f"  Losers:          {len(losses)}")
print(f"  Win Rate:        {len(wins)/len(trades)*100:.1f}%")
print(f"  Profit Factor:   {pf}")
print(f"  Gross Profit:    ${gross_w:,.2f}")
print(f"  Gross Loss:      ${gross_l:,.2f}")
print(f"  Net P&L:         ${total_pnl:,.2f}")
print(f"  Final Balance:   ${BALANCE_START+total_pnl:,.2f}")
print(f"  Return:          {total_pnl/BALANCE_START*100:.1f}%")
print(f"  Max Drawdown:    ${max_dd:,.2f} ({max_dd/peak*100:.1f}%)")
if wins:
    print(f"  Avg Win:         ${gross_w/len(wins):,.2f}")
if losses:
    print(f"  Avg Loss:        ${gross_l/len(losses):,.2f}")
print(f"  Trades/Day:      {len(trades)/30:.1f}")
print(sep)

print()
print("  ALL TRADES:")
print(f"  {dash}")
print(f"  {'#':>3} {'Date':>14} {'Dir':<6} {'Entry':>9} {'SL':>9} {'TP':>9} "
      f"{'Exit':>9} {'Result':>7} {'P&L':>11} {'Balance':>11} {'RSI':>5}")
print(f"  {dash}")
for t in trades:
    d = time.strftime("%m/%d %H:%M", time.gmtime(t["openTime"]))
    print(f"  {t['num']:>3} {d:>14} {t['dir']:<6} {t['entry']:>9.2f} "
          f"{t['sl']:>9.2f} {t['tp']:>9.2f} {t['exitPrice']:>9.2f} "
          f"{t['result']:>7} {t['pnl']:>+11.2f} {t['balance']:>11.2f} "
          f"{t['rsi']:>5.1f}")
print(f"  {dash}")
print(f"  {'':>3} {'':>14} {'':6} {'':>9} {'':>9} {'':>9} {'':>9} "
      f"{'TOTAL':>7} {total_pnl:>+11.2f} {BALANCE_START+total_pnl:>11.2f}")

# ── Weekly breakdown ─────────────────────────────────────
print()
print("  WEEKLY BREAKDOWN:")
print(f"  {'-'*65}")
weeks = {}
for t in trades:
    wk_start = t["openTime"] - (time.gmtime(t["openTime"]).tm_wday * 86400)
    wk = time.strftime("%m/%d", time.gmtime(wk_start))
    if wk not in weeks:
        weeks[wk] = {"trades": 0, "wins": 0, "pnl": 0.0}
    weeks[wk]["trades"] += 1
    if t["result"] == "WIN":
        weeks[wk]["wins"] += 1
    weeks[wk]["pnl"] += t["pnl"]

for wk, d in weeks.items():
    wr = d["wins"] / d["trades"] * 100 if d["trades"] else 0
    l = d["trades"] - d["wins"]
    print(f"  Week {wk}:  {d['trades']} trades | {d['wins']}W {l}L | "
          f"WR {wr:.0f}% | P&L ${d['pnl']:+,.2f}")

print()
print("  Done.")
