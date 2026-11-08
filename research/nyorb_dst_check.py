"""NYORB re-validation split by US DST regime + NY-anchored variants (2026-10-09).

Two audit agents found the fixed-13:30-UTC NYORB edge concentrated in the EST weeks
(13:30 UTC = 08:30 ET). This script compares, on the same ETH M5 tape:
  A. fixed 13:30 UTC (live config)           B. fixed 12:30 UTC
  C. 09:30 America/New_York anchored         D. 08:30 America/New_York anchored
each reported in total, per DST regime (EST / EDT) and per 45-day window.
Usage: python research/nyorb_dst_check.py [--days 270] [--symbol ETH]
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OANDA_API_KEY", "x")

from core.btc_fetcher import BtcFetcher  # noqa: E402
from strategies.nyorb import engine as E  # noqa: E402

DST_START = pd.Timestamp("2026-03-08 07:00", tz="UTC")   # 2nd Sunday of March, 02:00 EST
DST_END = pd.Timestamp("2026-11-01 06:00", tz="UTC")     # 1st Sunday of November, 02:00 EDT


def regime(ts: pd.Timestamp) -> str:
    t = pd.Timestamp(ts)
    t = t.tz_convert("UTC") if t.tzinfo else t.tz_localize("UTC")
    return "EDT" if DST_START <= t < DST_END else "EST"


def run_variant(bars_utc: pd.DataFrame, *, tz: str | None, start: dt.time, last_entry: dt.time, flat_at: dt.time, tp_r: float = 1.5):
    bars = bars_utc.copy()
    if tz:
        bars["timestamp"] = bars["timestamp"].dt.tz_convert(tz)
    sigs = E.breakout_signals(bars, tp_r=tp_r, start=start, last_entry=last_entry, flat_at=flat_at)
    tdf, m, _ = E.simulate(bars, sigs, tp_r=tp_r)
    if tdf.empty:
        return tdf, m
    # back to UTC for the regime split
    ts_utc = pd.to_datetime(tdf["timestamp"]).dt.tz_localize(tz or "UTC", ambiguous="NaT", nonexistent="shift_forward").dt.tz_convert("UTC")
    tdf = tdf.assign(ts_utc=ts_utc, regime=[regime(t) for t in ts_utc])
    return tdf, m


def summarize(tdf: pd.DataFrame) -> str:
    if tdf is None or tdf.empty:
        return "no trades"
    r = tdf["R_multiple"]
    wins = int((r > 0).sum()); n = len(r)
    gw = float(r[r > 0].sum()); gl = float(-r[r <= 0].sum())
    pf = gw / gl if gl > 0 else 99.0
    eq = r.cumsum(); dd = float((eq.cummax() - eq).max())
    return f"n={n:3d} win={wins / n * 100:4.1f}% PF={pf:4.2f} netR={r.sum():+6.1f} avgR={r.mean():+.3f} maxDD={dd:4.1f}R"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=270)
    ap.add_argument("--symbol", default="ETH")
    args = ap.parse_args()

    end = pd.Timestamp.now(tz="UTC").floor("D")
    start = end - pd.Timedelta(days=args.days)
    print(f"fetching {args.symbol} M5 {start:%Y-%m-%d} -> {end:%Y-%m-%d} ...", flush=True)
    bars = BtcFetcher(args.symbol).fetch_range(start - pd.Timedelta(days=1), end, "5m")
    bars["timestamp"] = pd.to_datetime(bars["timestamp"], utc=True)
    bars = bars.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    expected = int((end - start + pd.Timedelta(days=1)) / pd.Timedelta(minutes=5))
    print(f"bars={len(bars)} expected~{expected} coverage={len(bars) / expected * 100:.1f}%  first={bars['timestamp'].iloc[0]} last={bars['timestamp'].iloc[-1]}", flush=True)

    variants = {
        "A fixed 13:30 UTC (LIVE)": dict(tz=None, start=dt.time(13, 30), last_entry=dt.time(20, 0), flat_at=dt.time(21, 0)),
        "B fixed 12:30 UTC": dict(tz=None, start=dt.time(12, 30), last_entry=dt.time(19, 0), flat_at=dt.time(20, 0)),
        "C NY 09:30 ET anchored": dict(tz="America/New_York", start=dt.time(9, 30), last_entry=dt.time(16, 0), flat_at=dt.time(17, 0)),
        "D NY 08:30 ET anchored": dict(tz="America/New_York", start=dt.time(8, 30), last_entry=dt.time(15, 0), flat_at=dt.time(16, 0)),
        "E NY 09:30 ET, flat 16:00 ET": dict(tz="America/New_York", start=dt.time(9, 30), last_entry=dt.time(15, 0), flat_at=dt.time(16, 0)),
    }
    # 45-day windows, oldest first
    edges = [start + pd.Timedelta(days=45 * k) for k in range(args.days // 45 + 1)]
    for name, kw in variants.items():
        tdf, _ = run_variant(bars, **kw)
        print(f"\n== {name}")
        print(f"   ALL  {summarize(tdf)}")
        if tdf is not None and not tdf.empty:
            for rg in ("EST", "EDT"):
                print(f"   {rg}  {summarize(tdf[tdf['regime'] == rg])}")
            parts = []
            for a, b in zip(edges[:-1], edges[1:]):
                sub = tdf[(tdf["ts_utc"] >= a) & (tdf["ts_utc"] < b)]
                parts.append(f"{sub['R_multiple'].sum():+5.1f}({len(sub)})" if len(sub) else "   --")
            print("   windows (45d, oldest first): " + " | ".join(parts))
            # EDT-only windows that matter for the live bot right now
            edt = tdf[tdf["regime"] == "EDT"]
            if len(edt):
                m = edt.groupby(edt["ts_utc"].dt.to_period("M"))["R_multiple"].agg(["sum", "count"])
                print("   EDT by month: " + ", ".join(f"{str(k)}:{v['sum']:+.1f}({int(v['count'])})" for k, v in m.iterrows()))


if __name__ == "__main__":
    main()
