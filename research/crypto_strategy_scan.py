"""Crypto strategy scan — ETH / SOL (BTC as reference) over 3 NON-overlapping windows.

What it tests
  • RSI EMA (the live crypto bot's engine, `backtest_forex_engine.evaluate_signal`):
    signals generated ONCE per symbol/window/RSI-threshold, then replayed through a
    stop-first simulator for every combination of stop (ATR), target (ATR), UTC
    session window and max-hold bars.
  • MACD (the Gold MACD rules: MACD 12/26/9 signal-line cross in the EMA200 trend,
    entry next bar open): on H1 and M15 bars, several stop/target/hold settings.

Honesty rules
  • Entry on the NEXT bar's open (nothing is known at the signal bar's close).
  • A bar that touches both stop and target counts as a LOSS (stop first).
  • A round-trip cost (spread) is charged on every trade — crypto CFD-style
    ~0.06% of price, split half on entry and half on exit.
  • A config only PASSES if it is net positive in ALL THREE windows. The win-rate
    column is reported separately; "high win rate != profitable".

Usage:  python research/crypto_strategy_scan.py [--symbols ETH,SOL,BTC] [--days 45] [--cost 0.0006]
Writes: research/out/crypto_scan_<date>.csv + a ranked summary on stdout.
"""
from __future__ import annotations

import argparse
import io
import itertools
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OANDA_API_KEY", "not-used-by-this-scan")

from backtests import backtest_forex_engine as engine  # noqa: E402
from core.btc_fetcher import BtcFetcher  # noqa: E402
from core.indicator_engine import IndicatorEngine  # noqa: E402


class _NullTrace(io.StringIO):
    def write(self, *_a, **_k):  # the engine writes a trace per bar; discard it
        return 0


# ───────────────────────────── simulator ──────────────────────────────────────
def simulate(bars: pd.DataFrame, signals: list[dict], sl_mult: float, tp_mult: float,
             max_hold: int, cost_pct: float, hours: tuple[int, int] | None) -> list[float]:
    """Replay signals (index, direction, atr) → list of R multiples. One trade at a
    time (a new signal during an open trade is skipped, like the engine)."""
    o = bars["open"].to_numpy(); h = bars["high"].to_numpy(); l = bars["low"].to_numpy(); c = bars["close"].to_numpy()
    ts_hour = bars["timestamp"].dt.hour.to_numpy()
    n = len(bars)
    out: list[float] = []
    busy_until = -1
    for s in signals:
        i = s["i"]
        if i <= busy_until or i + 1 >= n:
            continue
        if hours is not None and not (hours[0] <= int(ts_hour[i]) < hours[1]):
            continue
        atr = s["atr"]
        if not np.isfinite(atr) or atr <= 0:
            continue
        d = s["dir"]
        entry = float(o[i + 1])
        half = entry * cost_pct / 2.0
        entry = entry + half if d == "BUY" else entry - half       # adverse entry
        risk, rew = atr * sl_mult, atr * tp_mult
        sl = entry - risk if d == "BUY" else entry + risk
        tp = entry + rew if d == "BUY" else entry - rew
        last = min(n - 1, i + 1 + max_hold - 1)
        exit_p = None; exit_j = last
        for j in range(i + 1, last + 1):
            hit_sl = (l[j] <= sl) if d == "BUY" else (h[j] >= sl)
            hit_tp = (h[j] >= tp) if d == "BUY" else (l[j] <= tp)
            if hit_sl:                      # stop first on a straddle
                exit_p, exit_j = sl, j; break
            if hit_tp:
                exit_p, exit_j = tp, j; break
        if exit_p is None:
            exit_p = float(c[last])
        exit_half = exit_p * cost_pct / 2.0
        exit_p = exit_p - exit_half if d == "BUY" else exit_p + exit_half  # adverse exit
        pnl = (exit_p - entry) if d == "BUY" else (entry - exit_p)
        out.append(pnl / risk)
        busy_until = exit_j
    return out


def metrics(rs: list[float]) -> dict:
    if not rs:
        return {"n": 0, "win": 0.0, "pf": 0.0, "netR": 0.0, "avgR": 0.0, "maxDD": 0.0}
    a = np.array(rs)
    wins = a[a > 0]; losses = a[a <= 0]
    pf = float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf")
    eq = np.cumsum(a); dd = float((np.maximum.accumulate(eq) - eq).max()) if len(eq) else 0.0
    return {"n": int(len(a)), "win": float((a > 0).mean() * 100), "pf": pf, "netR": float(a.sum()),
            "avgR": float(a.mean()), "maxDD": dd}


# ───────────────────────────── RSI EMA signals ────────────────────────────────
def rsi_ema_signals(m5: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, buy_rsi: float, sell_rsi: float) -> list[dict]:
    ind = IndicatorEngine()
    entry_df = ind.add_indicators(m5.copy())
    trend_df = engine.add_adx14(ind.add_indicators(engine.resample_to_15m(m5.copy())))
    trend_lookup = trend_df.set_index("timestamp", drop=False).sort_index()
    ob, os_ = engine.BUY_RSI_THRESHOLD, engine.SELL_RSI_THRESHOLD
    orig_session = engine.session_allowed
    engine.BUY_RSI_THRESHOLD, engine.SELL_RSI_THRESHOLD = buy_rsi, sell_rsi
    engine.session_allowed = lambda *_a, **_k: True
    sigs: list[dict] = []
    trace = _NullTrace()
    try:
        ts_all = entry_df["timestamp"]
        first = int(ts_all.searchsorted(start)); last = int(ts_all.searchsorted(end))
        for i in range(max(first, 1), min(last, len(entry_df) - 1)):
            ev = engine.evaluate_signal(entry_df=entry_df, trend_lookup=trend_lookup, signal_index=i,
                                        start_utc=start, end_utc=end, trace_handle=trace)
            s = ev.get("signal")
            if s is not None:
                sigs.append({"i": i, "dir": str(s["direction"]), "atr": float(s["atr_value"])})
    finally:
        engine.BUY_RSI_THRESHOLD, engine.SELL_RSI_THRESHOLD = ob, os_
        engine.session_allowed = orig_session
    return entry_df, sigs


# ───────────────────────────── MACD signals ───────────────────────────────────
def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def macd_signals(bars: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, trend_ema: int = 200) -> list[dict]:
    df = bars.copy()
    df["ema_t"] = _ema(df["close"], trend_ema)
    macd = _ema(df["close"], 12) - _ema(df["close"], 26)
    df["macd"], df["sig"] = macd, _ema(macd, 9)
    tr = pd.concat([df.high - df.low, (df.high - df.close.shift()).abs(), (df.low - df.close.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.rolling(14).mean()
    sigs = []
    m = df["macd"].to_numpy(); sg = df["sig"].to_numpy(); e = df["ema_t"].to_numpy(); c = df["close"].to_numpy(); a = df["atr"].to_numpy()
    ts = df["timestamp"]
    for i in range(max(trend_ema + 5, 30), len(df) - 1):
        if not (start <= ts.iloc[i] < end):
            continue
        if any(np.isnan(x) for x in (m[i], sg[i], m[i - 1], sg[i - 1], e[i], a[i])) or a[i] <= 0:
            continue
        cu = m[i - 1] <= sg[i - 1] and m[i] > sg[i]
        cd = m[i - 1] >= sg[i - 1] and m[i] < sg[i]
        d = "BUY" if (c[i] > e[i] and cu) else ("SELL" if (c[i] < e[i] and cd) else None)
        if d:
            sigs.append({"i": i, "dir": d, "atr": float(a[i])})
    return sigs


# ───────────────────────────── main ───────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="ETH,SOL,BTC")
    ap.add_argument("--days", type=int, default=45, help="window length (3 non-overlapping windows)")
    ap.add_argument("--cost", type=float, default=0.0006, help="round-trip cost as a fraction of price")
    ap.add_argument("--min-win", type=float, default=45.0, help="win-rate floor for the PASS flag")
    ap.add_argument("--end", default=None, help="end of the last window (UTC date); default = now. Use to test OLDER periods")
    args = ap.parse_args()

    now = pd.Timestamp(args.end, tz="UTC") if args.end else pd.Timestamp.now(tz="UTC").floor("5min")
    W = args.days
    windows = [(now - pd.Timedelta(days=W * (3 - k)), now - pd.Timedelta(days=W * (2 - k))) for k in range(3)]
    out_dir = ROOT / "research" / "out"; out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    t0 = time.time()

    for key in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        f = BtcFetcher(key)
        print(f"\n=== {key}: fetching {W*3} days M5 / M15 / H1 from the Binance mirror ...", flush=True)
        m5_all = f.fetch_range(windows[0][0] - pd.Timedelta(days=15), now, "5m")
        m15_all = f.fetch_range(windows[0][0] - pd.Timedelta(days=30), now, "15m")
        h1_all = f.fetch_range(windows[0][0] - pd.Timedelta(days=45), now, "1h")
        for d in (m5_all, m15_all, h1_all):
            d["timestamp"] = pd.to_datetime(d["timestamp"], utc=True)
        print(f"    m5 {len(m5_all)} bars, m15 {len(m15_all)}, h1 {len(h1_all)}  ({time.time()-t0:.0f}s)", flush=True)

        # RSI EMA: signals per window per RSI setting
        rsi_sets = {"rsi55/45": (55.0, 45.0), "rsi50/50": (50.0, 50.0)}
        for rsi_name, (b, s_) in rsi_sets.items():
            per_window = []
            for (ws, we) in windows:
                sub = m5_all[(m5_all["timestamp"] >= ws - pd.Timedelta(days=15)) & (m5_all["timestamp"] < we)].reset_index(drop=True)
                entry_df, sigs = rsi_ema_signals(sub, ws, we, b, s_)
                per_window.append((entry_df, sigs))
                print(f"    RSI EMA {rsi_name} window {ws:%m-%d}->{we:%m-%d}: {len(sigs)} raw signals ({time.time()-t0:.0f}s)", flush=True)
            sessions = {"24/7": None, "12-18": (12, 18), "07-21": (7, 21), "00-08": (0, 8), "13-21": (13, 21)}
            for (sl, tp), (sname, hrs), hold in itertools.product(
                [(1.5, 1.5), (1.5, 3.0), (1.0, 2.0), (2.0, 2.0), (1.0, 1.0), (2.0, 4.0)], sessions.items(), [12, 24, 48]
            ):
                wins = []
                for (entry_df, sigs) in per_window:
                    wins.append(metrics(simulate(entry_df, sigs, sl, tp, hold, args.cost, hrs)))
                rows.append(_row(key, f"RSI EMA {rsi_name}", f"SL{sl}/TP{tp} {sname} hold{hold}", wins, args.min_win))

        # MACD: H1 and M15
        for tf_name, bars in (("H1", h1_all), ("M15", m15_all)):
            sig_w = []
            for (ws, we) in windows:
                sig_w.append((bars.reset_index(drop=True), macd_signals(bars.reset_index(drop=True), ws, we)))
            for (sl, tp), hold in itertools.product([(1.5, 3.0), (1.5, 1.5), (1.0, 2.0), (2.0, 4.0), (1.0, 1.0)],
                                                    [24, 48] if tf_name == "H1" else [48, 96]):
                wins = [metrics(simulate(b, s, sl, tp, hold, args.cost, None)) for (b, s) in sig_w]
                rows.append(_row(key, f"MACD {tf_name}", f"SL{sl}/TP{tp} hold{hold}", wins, args.min_win))

    df = pd.DataFrame(rows)
    stamp = now.strftime("%Y-%m-%d") + ("_older" if args.end else "")
    csv = out_dir / f"crypto_scan_{stamp}.csv"
    df.to_csv(csv, index=False)
    print(f"\nsaved {csv}  ({len(df)} configs, {time.time()-t0:.0f}s)")

    for key in df["symbol"].unique():
        d = df[df["symbol"] == key].copy()
        d["score"] = d["all_pos"].astype(int) * 1000 + d["netR"]
        print(f"\n{'='*110}\n{key}: top configs (PASS = net positive in all 3 windows; WIN>= {args.min_win:.0f}% flag separate)\n{'='*110}")
        cols = ["strategy", "config", "n", "win", "pf", "netR", "maxDD", "w1", "w2", "w3", "all_pos", "pass"]
        top = d.sort_values(["pass", "all_pos", "netR"], ascending=False).head(14)
        print(top[cols].to_string(index=False, formatters={"win": "{:.1f}".format, "pf": "{:.2f}".format, "netR": "{:+.1f}".format, "maxDD": "{:.1f}".format}))


def _row(symbol: str, strategy: str, config: str, wins: list[dict], min_win: float) -> dict:
    n = sum(w["n"] for w in wins)
    tot = [w["netR"] for w in wins]
    all_pos = all(x > 0 for x in tot) and all(w["n"] >= 8 for w in wins)
    win = (sum(w["win"] * w["n"] for w in wins) / n) if n else 0.0
    gw = sum(max(w["netR"], 0) + 0 for w in wins)  # placeholder, PF recomputed below
    # profit factor across windows from avg stats: approximate via netR / drawdown-free sums isn't exact; recompute from per-window pf weighted by n
    pf = float(np.average([min(w["pf"], 9.99) for w in wins], weights=[max(w["n"], 1) for w in wins])) if n else 0.0
    return {
        "symbol": symbol, "strategy": strategy, "config": config, "n": n, "win": win, "pf": pf,
        "netR": sum(tot), "maxDD": max(w["maxDD"] for w in wins),
        "w1": f"{wins[0]['n']}t {wins[0]['netR']:+.1f}R", "w2": f"{wins[1]['n']}t {wins[1]['netR']:+.1f}R", "w3": f"{wins[2]['n']}t {wins[2]['netR']:+.1f}R",
        "all_pos": all_pos, "pass": bool(all_pos and win >= min_win),
    }


if __name__ == "__main__":
    main()
