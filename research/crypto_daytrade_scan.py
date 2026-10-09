"""Crypto DAY-TRADING strategy scan — BTC / ETH / SOL, six non-overlapping 45-day windows.

Six intraday families (all positions are closed by a time stop, most by 21:00 UTC):
  ARB  Asia-range breakout: range = 00:00-07:00 UTC, first M5 close beyond it in
       07:00-20:00, stop = range midpoint, target = k x risk, flat at 21:00.
  NYORB New-York opening range (13:30 + 30/60 min): first close beyond, stop = other
       side of the range, target = k x risk, flat at 21:00.
  VPB  VWAP trend pullback (M5): H1 EMA50>EMA200 trend, price above the daily VWAP,
       a pullback that touches VWAP within the last 6 bars, then a bullish close back
       above it -> buy (mirror for sells). Stop 1.5 ATR, target 2/3 ATR, flat 21:00.
  VBF  VWAP band fade (M5): close beyond VWAP +/- 2 sigma against the H1 trend ->
       fade back to VWAP. Stop = same distance beyond, target = VWAP, 24-bar time stop.
  EPB  M15 EMA20 pullback in the H1 trend: previous M15 bar touched EMA20, this bar
       closes back on the trend side; stop under the pullback low, target 2/3 R,
       32-bar (8h) time stop.
  PDB  Previous-day high/low breakout (M15, 07:00-20:00): first close beyond
       yesterday's high/low, stop 1.5 ATR, target 2/3 R, flat 21:00.

Honesty rules: entry at the NEXT bar's open, stop-first on straddles, 0.06% round-trip
cost, one position at a time per strategy, PASS only if positive in >= 5 of 6 windows
with PF >= 1.15 and >= 60 trades.

Usage: python research/crypto_daytrade_scan.py [--symbols ETH,SOL,BTC] [--windows 6] [--days 45]
"""
from __future__ import annotations

import argparse
import itertools
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.btc_fetcher import BtcFetcher  # noqa: E402

COST = 0.0006


# ───────────────────────────── helpers ────────────────────────────────────────
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    tr = pd.concat([df.high - df.low, (df.high - df.close.shift()).abs(), (df.low - df.close.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def resample(df5: pd.DataFrame, rule: str) -> pd.DataFrame:
    idx = df5.set_index("timestamp")
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return idx.resample(rule, label="left", closed="left").agg(agg).dropna(subset=["open"]).reset_index()


def h1_trend_on(df5: pd.DataFrame) -> np.ndarray:
    """+1 / -1 / 0 trend from the last CLOSED H1 bar (EMA50 vs EMA200), mapped to M5 bars."""
    h1 = resample(df5, "1h")
    h1["e50"], h1["e200"] = ema(h1.close, 50), ema(h1.close, 200)
    h1["trend"] = np.where(h1.e50 > h1.e200, 1, np.where(h1.e50 < h1.e200, -1, 0))
    # the value of H1 bar t is known at t + 1h -> applies from the next hour's M5 bars
    h1["apply_from"] = h1["timestamp"] + pd.Timedelta(hours=1)
    m = pd.merge_asof(df5[["timestamp"]], h1[["apply_from", "trend"]], left_on="timestamp", right_on="apply_from", direction="backward")
    return m["trend"].fillna(0).to_numpy()


def add_daily_vwap(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["date"] = d["timestamp"].dt.floor("D")
    tp = (d.high + d.low + d.close) / 3
    pv = (tp * d.volume).groupby(d.date).cumsum()
    vv = d.volume.groupby(d.date).cumsum()
    d["vwap"] = pv / vv
    dev = ((tp - d.vwap) ** 2 * d.volume).groupby(d.date).cumsum() / vv
    d["vwap_sd"] = np.sqrt(dev)
    return d


# ───────────────────────────── simulator ──────────────────────────────────────
def simulate(bars: pd.DataFrame, signals: list[dict], cost: float = COST) -> list[dict]:
    """signals: {i, dir, sl, tp, exit_by} with sl/tp as PRICES, exit_by = last bar index.
    Entry at bar i+1 open. One position at a time. Returns per-trade dicts with R."""
    o = bars.open.to_numpy(); h = bars.high.to_numpy(); l = bars.low.to_numpy(); c = bars.close.to_numpy()
    n = len(bars); out = []; busy_until = -1
    for s in sorted(signals, key=lambda x: x["i"]):
        i = s["i"]
        if i <= busy_until or i + 1 >= n:
            continue
        d, sl, tp = s["dir"], float(s["sl"]), float(s["tp"])
        entry = float(o[i + 1]); half = entry * cost / 2
        entry = entry + half if d == "BUY" else entry - half
        risk = (entry - sl) if d == "BUY" else (sl - entry)
        if risk <= 0 or ((tp <= entry) if d == "BUY" else (tp >= entry)):
            continue
        last = min(n - 1, int(s["exit_by"]))
        if last < i + 1:
            continue
        exit_p, exit_j, reason = None, last, "time"
        for j in range(i + 1, last + 1):
            hit_sl = (l[j] <= sl) if d == "BUY" else (h[j] >= sl)
            hit_tp = (h[j] >= tp) if d == "BUY" else (l[j] <= tp)
            if hit_sl:
                exit_p, exit_j, reason = sl, j, "sl"; break
            if hit_tp:
                exit_p, exit_j, reason = tp, j, "tp"; break
        if exit_p is None:
            exit_p = float(c[last])
        eh = exit_p * cost / 2
        exit_p = exit_p - eh if d == "BUY" else exit_p + eh
        pnl = (exit_p - entry) if d == "BUY" else (entry - exit_p)
        out.append({"i": i, "t": bars.timestamp.iloc[i], "dir": d, "R": pnl / risk, "reason": reason})
        busy_until = exit_j
    return out


def metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0, "win": 0.0, "pf": 0.0, "netR": 0.0, "maxDD": 0.0}
    a = np.array([t["R"] for t in trades])
    w, lo = a[a > 0], a[a <= 0]
    pf = float(w.sum() / -lo.sum()) if lo.sum() < 0 else 9.99
    eq = np.cumsum(a)
    return {"n": len(a), "win": float((a > 0).mean() * 100), "pf": min(pf, 9.99), "netR": float(a.sum()),
            "maxDD": float((np.maximum.accumulate(eq) - eq).max())}


# ───────────────────────────── day-end helper ────────────────────────────────
def day_end_index(df: pd.DataFrame, end_hour: int = 21) -> np.ndarray:
    """For each bar: index of the last bar before end_hour UTC on the same day (time stop)."""
    ts = df.timestamp
    date = ts.dt.floor("D")
    cutoff = date + pd.Timedelta(hours=end_hour)
    # last index with timestamp < cutoff: searchsorted on the sorted timestamps
    arr = ts.to_numpy()
    idx = np.searchsorted(arr, cutoff.to_numpy(), side="left") - 1
    return np.maximum(idx, np.arange(len(df)))


# ───────────────────────────── strategies (signal generators) ────────────────
def strat_arb(d5: pd.DataFrame, k_tp: float, start_h: int = 7, end_h: int = 20, rng_end_h: int = 7) -> list[dict]:
    sig = []
    end_idx = day_end_index(d5, 21)
    hour = d5.timestamp.dt.hour.to_numpy()
    for date, g in d5.groupby("date"):
        rng = g[(g.timestamp.dt.hour < rng_end_h)]
        if len(rng) < 12:
            continue
        rh, rl = float(rng.high.max()), float(rng.low.min())
        mid = (rh + rl) / 2
        a0 = float(g.atr.iloc[min(len(g) - 1, rng_end_h * 12)]) if g.atr.notna().any() else np.nan
        if not np.isfinite(a0) or (rh - rl) < 1.0 * a0 or (rh - rl) > 10 * a0:
            continue
        trade_bars = g[(g.timestamp.dt.hour >= start_h) & (g.timestamp.dt.hour < end_h)]
        done_dir = set()
        for idx, r in trade_bars.iterrows():
            if "BUY" not in done_dir and r.close > rh:
                risk = r.close - mid
                sig.append({"i": idx, "dir": "BUY", "sl": mid, "tp": r.close + k_tp * risk, "exit_by": end_idx[idx]}); done_dir.add("BUY")
            elif "SELL" not in done_dir and r.close < rl:
                risk = mid - r.close
                sig.append({"i": idx, "dir": "SELL", "sl": mid, "tp": r.close - k_tp * risk, "exit_by": end_idx[idx]}); done_dir.add("SELL")
            if len(done_dir) == 2:
                break
    return sig


def strat_nyorb(d5: pd.DataFrame, k_tp: float, minutes: int = 30) -> list[dict]:
    sig = []
    end_idx = day_end_index(d5, 21)
    for date, g in d5.groupby("date"):
        t0 = date + pd.Timedelta(hours=13, minutes=30)
        t1 = t0 + pd.Timedelta(minutes=minutes)
        rng = g[(g.timestamp >= t0) & (g.timestamp < t1)]
        if len(rng) < minutes // 5:
            continue
        rh, rl = float(rng.high.max()), float(rng.low.min())
        if rh <= rl:
            continue
        trade = g[(g.timestamp >= t1) & (g.timestamp.dt.hour < 20)]
        done = set()
        for idx, r in trade.iterrows():
            if "BUY" not in done and r.close > rh:
                risk = r.close - rl
                sig.append({"i": idx, "dir": "BUY", "sl": rl, "tp": r.close + k_tp * risk, "exit_by": end_idx[idx]}); done.add("BUY")
            elif "SELL" not in done and r.close < rl:
                risk = rh - r.close
                sig.append({"i": idx, "dir": "SELL", "sl": rh, "tp": r.close - k_tp * risk, "exit_by": end_idx[idx]}); done.add("SELL")
            if len(done) == 2:
                break
    return sig


def strat_vpb(d5: pd.DataFrame, trend: np.ndarray, sl_atr: float, tp_atr: float, hours=(7, 21)) -> list[dict]:
    sig = []
    end_idx = day_end_index(d5, 21)
    c = d5.close.to_numpy(); o = d5.open.to_numpy(); lo = d5.low.to_numpy(); hi = d5.high.to_numpy()
    v = d5.vwap.to_numpy(); a = d5.atr.to_numpy(); hour = d5.timestamp.dt.hour.to_numpy()
    for i in range(10, len(d5) - 1):
        if not (hours[0] <= hour[i] < hours[1]) or not np.isfinite(a[i]) or not np.isfinite(v[i]):
            continue
        touched_dn = (lo[i - 6:i + 1] <= v[i - 6:i + 1]).any()
        touched_up = (hi[i - 6:i + 1] >= v[i - 6:i + 1]).any()
        if trend[i] == 1 and c[i] > v[i] and c[i] > o[i] and touched_dn and c[i - 1] <= v[i - 1] * 1.002:
            sig.append({"i": i, "dir": "BUY", "sl": c[i] - sl_atr * a[i], "tp": c[i] + tp_atr * a[i], "exit_by": end_idx[i]})
        elif trend[i] == -1 and c[i] < v[i] and c[i] < o[i] and touched_up and c[i - 1] >= v[i - 1] * 0.998:
            sig.append({"i": i, "dir": "SELL", "sl": c[i] + sl_atr * a[i], "tp": c[i] - tp_atr * a[i], "exit_by": end_idx[i]})
    return sig


def strat_vbf(d5: pd.DataFrame, trend: np.ndarray, sigma: float = 2.0, hold: int = 24) -> list[dict]:
    sig = []
    c = d5.close.to_numpy(); v = d5.vwap.to_numpy(); sd = d5.vwap_sd.to_numpy()
    for i in range(30, len(d5) - 1):
        if not np.isfinite(sd[i]) or sd[i] <= 0:
            continue
        up, dn = v[i] + sigma * sd[i], v[i] - sigma * sd[i]
        if c[i] > up and trend[i] != 1:
            dist = c[i] - v[i]
            sig.append({"i": i, "dir": "SELL", "sl": c[i] + dist, "tp": v[i], "exit_by": i + hold})
        elif c[i] < dn and trend[i] != -1:
            dist = v[i] - c[i]
            sig.append({"i": i, "dir": "BUY", "sl": c[i] - dist, "tp": v[i], "exit_by": i + hold})
    return sig


def strat_epb(d15: pd.DataFrame, trend15: np.ndarray, k_tp: float, hours=(0, 24), hold: int = 32) -> list[dict]:
    sig = []
    c = d15.close.to_numpy(); o = d15.open.to_numpy(); lo = d15.low.to_numpy(); hi = d15.high.to_numpy()
    e20 = d15.ema20.to_numpy(); a = d15.atr.to_numpy(); hour = d15.timestamp.dt.hour.to_numpy()
    for i in range(25, len(d15) - 1):
        if not (hours[0] <= hour[i] < hours[1]) or not np.isfinite(a[i]) or not np.isfinite(e20[i]):
            continue
        if trend15[i] == 1 and lo[i - 1] <= e20[i - 1] and c[i] > e20[i] and c[i] > o[i]:
            sl = min(lo[i - 3:i + 1]) - 0.1 * a[i]
            risk = c[i] - sl
            if risk > 0.3 * a[i]:
                sig.append({"i": i, "dir": "BUY", "sl": sl, "tp": c[i] + k_tp * risk, "exit_by": i + hold})
        elif trend15[i] == -1 and hi[i - 1] >= e20[i - 1] and c[i] < e20[i] and c[i] < o[i]:
            sl = max(hi[i - 3:i + 1]) + 0.1 * a[i]
            risk = sl - c[i]
            if risk > 0.3 * a[i]:
                sig.append({"i": i, "dir": "SELL", "sl": sl, "tp": c[i] - k_tp * risk, "exit_by": i + hold})
    return sig


def strat_pdb(d15: pd.DataFrame, k_tp: float, sl_atr: float = 1.5) -> list[dict]:
    sig = []
    d = d15.copy()
    d["date"] = d.timestamp.dt.floor("D")
    daily = d.groupby("date").agg(ph=("high", "max"), pl=("low", "min"))
    daily["pdh"], daily["pdl"] = daily.ph.shift(1), daily.pl.shift(1)
    d = d.join(daily[["pdh", "pdl"]], on="date")
    end_idx = day_end_index(d, 21)
    c = d.close.to_numpy(); a = d.atr.to_numpy(); pdh = d.pdh.to_numpy(); pdl = d.pdl.to_numpy(); hour = d.timestamp.dt.hour.to_numpy()
    done: dict = {}
    for i in range(1, len(d) - 1):
        if not (7 <= hour[i] < 20) or not np.isfinite(a[i]) or not np.isfinite(pdh[i]):
            continue
        key = d.date.iloc[i]
        dd = done.setdefault(key, set())
        if "BUY" not in dd and c[i] > pdh[i] and c[i - 1] <= pdh[i - 1]:
            sig.append({"i": i, "dir": "BUY", "sl": c[i] - sl_atr * a[i], "tp": c[i] + k_tp * sl_atr * a[i], "exit_by": end_idx[i]}); dd.add("BUY")
        elif "SELL" not in dd and c[i] < pdl[i] and c[i - 1] >= pdl[i - 1]:
            sig.append({"i": i, "dir": "SELL", "sl": c[i] + sl_atr * a[i], "tp": c[i] - k_tp * sl_atr * a[i], "exit_by": end_idx[i]}); dd.add("SELL")
    return sig


# ───────────────────────────── main ───────────────────────────────────────────
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="ETH,SOL,BTC")
    ap.add_argument("--windows", type=int, default=6)
    ap.add_argument("--days", type=int, default=45)
    args = ap.parse_args()

    now = pd.Timestamp.now(tz="UTC").floor("5min")
    W, NW = args.days, args.windows
    windows = [(now - pd.Timedelta(days=W * (NW - k)), now - pd.Timedelta(days=W * (NW - 1 - k))) for k in range(NW)]
    t0 = time.time(); rows = []
    out_dir = ROOT / "research" / "out"; out_dir.mkdir(parents=True, exist_ok=True)

    for key in [s.strip().upper() for s in args.symbols.split(",") if s.strip()]:
        f = BtcFetcher(key)
        print(f"\n=== {key}: fetching {W*NW + 20} days of M5 ...", flush=True)
        d5 = f.fetch_range(windows[0][0] - pd.Timedelta(days=20), now, "5m")
        d5["timestamp"] = pd.to_datetime(d5.timestamp, utc=True)
        d5 = d5.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
        d5["atr"] = atr(d5, 14)
        d5 = add_daily_vwap(d5)
        trend5 = h1_trend_on(d5)
        d15 = resample(d5, "15min"); d15["atr"] = atr(d15, 14); d15["ema20"] = ema(d15.close, 20)
        trend15 = h1_trend_on(d15)
        print(f"    {len(d5)} M5 bars, {len(d15)} M15 bars ({time.time()-t0:.0f}s)", flush=True)

        configs: list[tuple[str, str, str, list[dict]]] = []  # (strategy, config, tf, signals)
        for k_tp in (1.0, 1.5, 2.0):
            configs.append(("ARB", f"range00-07 tp{k_tp}R", "5", strat_arb(d5, k_tp)))
            configs.append(("ARB", f"range00-08 tp{k_tp}R", "5", strat_arb(d5, k_tp, start_h=8, rng_end_h=8)))
            for mins in (30, 60):
                configs.append(("NYORB", f"{mins}min tp{k_tp}R", "5", strat_nyorb(d5, k_tp, mins)))
            configs.append(("PDB", f"tp{k_tp}R sl1.5atr", "15", strat_pdb(d15, k_tp)))
            configs.append(("EPB", f"tp{k_tp}R 24h", "15", strat_epb(d15, trend15, k_tp)))
            configs.append(("EPB", f"tp{k_tp}R 07-21", "15", strat_epb(d15, trend15, k_tp, hours=(7, 21))))
        for sl_a, tp_a in ((1.5, 2.0), (1.5, 3.0), (1.0, 2.0), (2.0, 3.0)):
            configs.append(("VPB", f"sl{sl_a}/tp{tp_a}atr 07-21", "5", strat_vpb(d5, trend5, sl_a, tp_a)))
            configs.append(("VPB", f"sl{sl_a}/tp{tp_a}atr 24h", "5", strat_vpb(d5, trend5, sl_a, tp_a, hours=(0, 24))))
        for sg in (2.0, 2.5):
            for hold in (24, 48):
                configs.append(("VBF", f"{sg}sd hold{hold}", "5", strat_vbf(d5, trend5, sg, hold)))

        for strat, cfg, tf, sigs in configs:
            bars = d5 if tf == "5" else d15
            trades = simulate(bars, sigs)
            per = []
            for (ws, we) in windows:
                tw = [t for t in trades if ws <= t["t"] < we]
                per.append(metrics(tw))
            n = sum(p["n"] for p in per)
            pos = sum(1 for p in per if p["netR"] > 0)
            allm = metrics([t for t in trades if windows[0][0] <= t["t"] < windows[-1][1]])
            rows.append({"symbol": key, "strategy": strat, "config": cfg, "n": n, "win": allm["win"], "pf": allm["pf"],
                         "netR": allm["netR"], "maxDD": allm["maxDD"], "pos_windows": f"{pos}/{NW}",
                         "windows": " | ".join(f"{p['n']}t {p['netR']:+.1f}" for p in per),
                         "pass": bool(pos >= NW - 1 and allm["pf"] >= 1.15 and n >= 60)})
        print(f"    {key}: {len(configs)} configs scored ({time.time()-t0:.0f}s)", flush=True)

    df = pd.DataFrame(rows)
    csv = out_dir / f"crypto_daytrade_scan_{now:%Y-%m-%d}.csv"
    df.to_csv(csv, index=False)
    print(f"\nsaved {csv} ({len(df)} rows, {time.time()-t0:.0f}s)")
    pd.set_option("display.width", 220)
    for key in df.symbol.unique():
        d = df[df.symbol == key].copy()
        d["posn"] = d.pos_windows.str.split("/").str[0].astype(int)
        print(f"\n{'='*120}\n{key}: top 15 by (pass, positive windows, net R)   — windows = {W}d each, oldest first\n{'='*120}")
        top = d.sort_values(["pass", "posn", "netR"], ascending=False).head(15)
        print(top[["strategy", "config", "n", "win", "pf", "netR", "maxDD", "pos_windows", "windows", "pass"]].to_string(
            index=False, formatters={"win": "{:.1f}".format, "pf": "{:.2f}".format, "netR": "{:+.1f}".format, "maxDD": "{:.1f}".format}))


if __name__ == "__main__":
    main()
