"""SEAN0-ALGO Backtest Dashboard — 3-month XAUUSD strategy review with $3k equity curve."""
from __future__ import annotations

import traceback
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from .backtest_forex_engine import (
    DEFAULT_MAX_HOLD,
    DEFAULT_RISK_PER_TRADE,
    load_local_env,
    parse_date_utc,
    run_backtest,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STARTING_BALANCE = 3_000.0
DEFAULT_RR_LABEL = "2:1 (SL=1.5×ATR / TP=3.0×ATR)"

# ─── Page Config ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SEAN0-ALGO | Backtest",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Styles ─────────────────────────────────────────────────────────────────────
st.markdown(
    """
    <style>
    .metric-card {
        background: #1e1e2e;
        border-radius: 10px;
        padding: 16px 20px;
        border: 1px solid #2d2d3d;
        text-align: center;
    }
    .metric-label { color: #888; font-size: 12px; text-transform: uppercase; letter-spacing: 1px; }
    .metric-value { color: #f0f0f0; font-size: 26px; font-weight: 700; margin-top: 4px; }
    .metric-value.green { color: #26a65b; }
    .metric-value.red   { color: #e74c3c; }
    .metric-value.blue  { color: #3498db; }
    .win-row  { background-color: rgba(38,166,91,0.12) !important; }
    .loss-row { background-color: rgba(231,76,60,0.12) !important; }
    h1 { font-size: 2rem !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ─── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://img.icons8.com/fluency/48/combo-chart.png", width=48)
    st.title("Backtest Settings")
    st.markdown("---")

    today_utc = pd.Timestamp.now(tz="UTC").normalize()
    default_end = today_utc - pd.Timedelta(days=1)
    default_start = default_end - pd.Timedelta(days=90)

    start_date = st.date_input("Start Date", value=default_start.date())
    end_date = st.date_input("End Date", value=default_end.date())

    st.markdown("---")
    starting_balance = st.number_input(
        "Starting Balance ($)",
        min_value=100.0,
        max_value=1_000_000.0,
        value=DEFAULT_STARTING_BALANCE,
        step=500.0,
        format="%.0f",
    )
    risk_pct = st.slider(
        "Risk per Trade (%)",
        min_value=0.5,
        max_value=10.0,
        value=float(DEFAULT_RISK_PER_TRADE * 100),
        step=0.5,
    )
    max_hold_bars = st.slider(
        "Max Hold Bars (5m candles)",
        min_value=1,
        max_value=48,
        value=DEFAULT_MAX_HOLD,
        step=1,
    )

    st.markdown("---")
    st.markdown(f"**R:R Ratio** `{DEFAULT_RR_LABEL}`")
    st.markdown("**Symbol** `XAUUSD`")
    st.markdown("**Trend TF** `15m` | **Entry TF** `5m`")
    st.markdown("**Data** OANDA practice")
    st.markdown("---")

    run_button = st.button("🚀 Run Backtest", use_container_width=True, type="primary")
    load_button = st.button("📂 Load Last Results", use_container_width=True)


# ─── Header ─────────────────────────────────────────────────────────────────────
st.title("📊 SEAN0-ALGO | Backtest Dashboard")
st.caption(
    f"Strategy: XAU Regime-Adaptive Breakout  •  Symbol: XAUUSD  •  "
    f"R:R = {DEFAULT_RR_LABEL}  •  Starting Balance: ${starting_balance:,.0f}"
)


# ─── Helper: metric card ─────────────────────────────────────────────────────────
def _card(label: str, value: str, color: str = "") -> str:
    css_class = f"metric-value {color}".strip()
    return (
        f'<div class="metric-card">'
        f'<div class="metric-label">{label}</div>'
        f'<div class="{css_class}">{value}</div>'
        f"</div>"
    )


# ─── Helper: color-coded trades table ────────────────────────────────────────────
def _render_trades_table(df: pd.DataFrame) -> None:
    display = df.copy()

    # Format timestamps
    for col in ["timestamp", "entry_timestamp", "exit_timestamp"]:
        if col in display.columns:
            display[col] = pd.to_datetime(display[col], utc=True, errors="coerce")
            display[col] = display[col].dt.strftime("%Y-%m-%d %H:%M")

    # Round numerics
    for col in ["entry_price", "exit_price", "sl", "tp"]:
        if col in display.columns:
            display[col] = display[col].round(2)
    for col in ["R_multiple", "pnl", "gross_pnl", "commission"]:
        if col in display.columns:
            display[col] = display[col].round(4)
    for col in ["equity_before", "equity_after"]:
        if col in display.columns:
            display[col] = display[col].round(2)

    def _row_style(row: pd.Series) -> list[str]:
        colour = "rgba(38,166,91,0.15)" if row.get("result") == "WIN" else "rgba(231,76,60,0.15)"
        return [f"background-color: {colour}"] * len(row)

    styled = display.style.apply(_row_style, axis=1)
    st.dataframe(styled, use_container_width=True, height=400)


# ─── Load Last Results (instant, no OANDA fetch) ────────────────────────────────
if load_button:
    csv_path = ROOT / "trades.csv"
    if not csv_path.exists():
        st.error("No trades.csv found. Run a backtest first.")
        st.stop()
    trades_df = pd.read_csv(csv_path)
    if trades_df.empty:
        st.error("trades.csv is empty.")
        st.stop()
    from .backtest_forex_engine import compute_metrics, DEFAULT_STARTING_BALANCE as _DEFAULT_BAL
    trades_df["equity_after"] = pd.to_numeric(trades_df.get("equity_after", pd.Series(dtype=float)), errors="coerce")
    metrics = compute_metrics(trades_df)
    # Infer balance from first equity_before or fallback to default
    inferred_balance = float(trades_df["equity_before"].iloc[0]) if "equity_before" in trades_df.columns else starting_balance
    st.session_state["trades_df"] = trades_df
    st.session_state["metrics"] = metrics
    st.session_state["bt_start"] = str(trades_df["timestamp"].iloc[0])[:10] if "timestamp" in trades_df.columns else "—"
    st.session_state["bt_end"] = str(trades_df["timestamp"].iloc[-1])[:10] if "timestamp" in trades_df.columns else "—"
    st.session_state["bt_balance"] = inferred_balance
    st.rerun()


# ─── Run Backtest ────────────────────────────────────────────────────────────────
if run_button:
    load_local_env()
    start_utc = parse_date_utc(str(start_date))
    end_utc = parse_date_utc(str(end_date), inclusive_end=True)

    if end_utc <= start_utc:
        st.error("End date must be after start date.")
        st.stop()

    with st.spinner(f"Running backtest {start_date} → {end_date} …  (fetching OANDA candles + warming up 30 days of indicators)"):
        try:
            trades_df, metrics = run_backtest(
                start_utc=start_utc,
                end_utc=end_utc,
                max_hold_bars=max_hold_bars,
                starting_balance=starting_balance,
                risk_per_trade=risk_pct / 100.0,
            )
            st.session_state["trades_df"] = trades_df
            st.session_state["metrics"] = metrics
            st.session_state["bt_start"] = str(start_date)
            st.session_state["bt_end"] = str(end_date)
            st.session_state["bt_balance"] = starting_balance
        except Exception:
            st.error("Backtest failed — see details below.")
            st.code(traceback.format_exc())
            st.stop()


# ─── Results ────────────────────────────────────────────────────────────────────
if "trades_df" not in st.session_state:
    st.info("👈 Configure settings in the sidebar, then click **Run Backtest** to start.")
    st.markdown(
        """
        ### What this dashboard shows
        - **Last 3 months** of XAUUSD live candles from OANDA
        - Regime-adaptive breakout signals (EMA trend + RSI + ATR + session)
        - Forex trade simulation with **2:1 R:R** (SL = 1.5×ATR, TP = 3.0×ATR)
        - **$3,000** starting equity curve with **5% risk** per trade position sizing
        """
    )
    st.stop()

trades_df: pd.DataFrame = st.session_state["trades_df"]
metrics: dict = st.session_state["metrics"]
bt_start = st.session_state.get("bt_start", "")
bt_end = st.session_state.get("bt_end", "")
bt_balance = st.session_state.get("bt_balance", DEFAULT_STARTING_BALANCE)

st.success(f"✅ Backtest complete: **{bt_start}** → **{bt_end}**")

# ─── KPI Row ────────────────────────────────────────────────────────────────────
total = int(metrics.get("total_trades", 0))
wins = int(metrics.get("wins", 0))
losses = int(metrics.get("losses", 0))
win_rate = float(metrics.get("win_rate", 0.0))
profit_factor = float(metrics.get("profit_factor", 0.0))
avg_r = float(metrics.get("average_r", 0.0))
max_dd = float(metrics.get("max_drawdown_r", 0.0))
final_balance = float(metrics.get("ending_balance", bt_balance))
net_pnl = final_balance - bt_balance
net_return = (net_pnl / bt_balance) * 100.0 if bt_balance > 0 else 0.0

pnl_color = "green" if net_pnl >= 0 else "red"
wr_color = "green" if win_rate >= 50 else "red"
pf_color = "green" if profit_factor >= 1.0 else "red"

cols = st.columns(9)
cards = [
    ("Total Trades", str(total), "blue"),
    ("Wins", str(wins), "green"),
    ("Losses", str(losses), "red"),
    ("Win Rate", f"{win_rate:.1f}%", wr_color),
    ("Profit Factor", f"{profit_factor:.2f}", pf_color),
    ("Avg R", f"{avg_r:.3f}", "green" if avg_r >= 0 else "red"),
    ("Max Drawdown", f"{max_dd:.2f} R", "red" if max_dd < 0 else ""),
    ("Net P&L", f"${net_pnl:+,.2f}", pnl_color),
    ("Return", f"{net_return:+.2f}%", pnl_color),
]
for col, (label, value, color) in zip(cols, cards):
    col.markdown(_card(label, value, color), unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# Balance summary row
b1, b2, b3 = st.columns(3)
b1.metric("Starting Balance", f"${bt_balance:,.2f}")
b2.metric("Final Balance", f"${final_balance:,.2f}", delta=f"${net_pnl:+,.2f}")
b3.metric("R:R Ratio", "2 : 1", delta="SL=1.5×ATR  TP=3.0×ATR", delta_color="off")

st.markdown("---")

# ─── Equity Curve ────────────────────────────────────────────────────────────────
st.subheader("📈 Equity Curve")

if trades_df.empty:
    st.warning("No trades executed in the selected period — nothing to plot.")
else:
    eq_df = trades_df.sort_values("exit_timestamp").reset_index(drop=True)
    eq_df["trade_no"] = range(1, len(eq_df) + 1)

    # Prepend starting balance
    start_row = pd.DataFrame([{
        "exit_timestamp": eq_df["entry_timestamp"].iloc[0],
        "equity_after": bt_balance,
        "trade_no": 0,
        "result": "START",
    }])
    plot_df = pd.concat([start_row, eq_df[["exit_timestamp", "equity_after", "trade_no", "result"]]], ignore_index=True)

    # Color fills
    peak = plot_df["equity_after"].cummax()
    drawdown = plot_df["equity_after"] - peak

    fig_eq = go.Figure()

    # Drawdown shading
    fig_eq.add_trace(go.Scatter(
        x=plot_df["trade_no"],
        y=plot_df["equity_after"],
        mode="lines+markers",
        name="Equity",
        line=dict(color="#2ecc71", width=2.5),
        marker=dict(
            color=["#2ecc71" if r == "WIN" else "#e74c3c" if r == "LOSS" else "#888"
                   for r in plot_df["result"]],
            size=8,
        ),
        fill="tozeroy",
        fillcolor="rgba(46,204,113,0.07)",
        hovertemplate="Trade #%{x}<br>Balance: $%{y:,.2f}<extra></extra>",
    ))

    # Starting balance reference line
    fig_eq.add_hline(
        y=bt_balance,
        line_dash="dash",
        line_color="#888",
        annotation_text=f"Start ${bt_balance:,.0f}",
        annotation_position="bottom right",
    )

    fig_eq.update_layout(
        paper_bgcolor="#0e1117",
        plot_bgcolor="#161b22",
        font=dict(color="#c9d1d9"),
        xaxis=dict(title="Trade Number", gridcolor="#21262d", showgrid=True),
        yaxis=dict(title="Account Balance ($)", gridcolor="#21262d", showgrid=True),
        height=400,
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=False,
    )

    st.plotly_chart(fig_eq, use_container_width=True)

st.markdown("---")

# ─── Charts Row ─────────────────────────────────────────────────────────────────
col_a, col_b = st.columns(2)

with col_a:
    st.subheader("📊 P&L per Trade")
    if not trades_df.empty:
        pnl_df = trades_df.sort_values("entry_timestamp").reset_index(drop=True)
        pnl_df["trade_no"] = range(1, len(pnl_df) + 1)
        colors = ["#2ecc71" if v > 0 else "#e74c3c" for v in pnl_df["pnl"]]
        fig_pnl = go.Figure(go.Bar(
            x=pnl_df["trade_no"],
            y=pnl_df["pnl"].round(2),
            marker_color=colors,
            hovertemplate="Trade #%{x}<br>P&L: $%{y:,.2f}<extra></extra>",
        ))
        fig_pnl.update_layout(
            paper_bgcolor="#0e1117",
            plot_bgcolor="#161b22",
            font=dict(color="#c9d1d9"),
            xaxis=dict(title="Trade #", gridcolor="#21262d"),
            yaxis=dict(title="P&L ($)", gridcolor="#21262d"),
            height=300,
            margin=dict(l=10, r=10, t=10, b=10),
        )
        st.plotly_chart(fig_pnl, use_container_width=True)
    else:
        st.info("No trade data.")

with col_b:
    st.subheader("🥧 Win / Loss Split")
    if not trades_df.empty and total > 0:
        fig_pie = go.Figure(go.Pie(
            labels=["Wins", "Losses"],
            values=[wins, losses],
            marker_colors=["#2ecc71", "#e74c3c"],
            hole=0.45,
            textinfo="label+percent+value",
            hovertemplate="%{label}: %{value} (%{percent})<extra></extra>",
        ))
        fig_pie.update_layout(
            paper_bgcolor="#0e1117",
            font=dict(color="#c9d1d9"),
            height=300,
            margin=dict(l=10, r=10, t=10, b=10),
            showlegend=True,
            legend=dict(orientation="h", y=-0.1),
        )
        st.plotly_chart(fig_pie, use_container_width=True)
    else:
        st.info("No trade data.")

st.markdown("---")

# ─── Session & Direction Breakdown ──────────────────────────────────────────────
col_c, col_d = st.columns(2)

with col_c:
    st.subheader("🌍 Session Breakdown")
    if not trades_df.empty and "exit_timestamp" in trades_df.columns:
        def _session(ts: pd.Timestamp) -> str:
            try:
                h = pd.to_datetime(ts, utc=True).hour
                if 12 <= h < 16:
                    return "OVERLAP"
                if 7 <= h < 16:
                    return "LONDON"
                if 16 <= h < 21:
                    return "NEW_YORK"
                return "ASIAN"
            except Exception:
                return "UNKNOWN"

        trades_df["session"] = trades_df["exit_timestamp"].apply(_session)
        sess_grp = trades_df.groupby(["session", "result"]).size().reset_index(name="count")
        fig_sess = px.bar(
            sess_grp,
            x="session",
            y="count",
            color="result",
            color_discrete_map={"WIN": "#2ecc71", "LOSS": "#e74c3c"},
            barmode="group",
            labels={"count": "Trades", "session": "Session"},
        )
        fig_sess.update_layout(
            paper_bgcolor="#0e1117",
            plot_bgcolor="#161b22",
            font=dict(color="#c9d1d9"),
            height=300,
            margin=dict(l=10, r=10, t=10, b=10),
            legend=dict(orientation="h", y=1.1),
        )
        st.plotly_chart(fig_sess, use_container_width=True)
    else:
        st.info("No trade data.")

with col_d:
    st.subheader("📐 BUY vs SELL Performance")
    if not trades_df.empty and "direction" in trades_df.columns:
        dir_grp = trades_df.groupby(["direction", "result"]).size().reset_index(name="count")
        fig_dir = px.bar(
            dir_grp,
            x="direction",
            y="count",
            color="result",
            color_discrete_map={"WIN": "#2ecc71", "LOSS": "#e74c3c"},
            barmode="group",
            labels={"count": "Trades", "direction": "Direction"},
        )
        fig_dir.update_layout(
            paper_bgcolor="#0e1117",
            plot_bgcolor="#161b22",
            font=dict(color="#c9d1d9"),
            height=300,
            margin=dict(l=10, r=10, t=10, b=10),
            legend=dict(orientation="h", y=1.1),
        )
        st.plotly_chart(fig_dir, use_container_width=True)
    else:
        st.info("No trade data.")

st.markdown("---")

# ─── R Multiple Distribution ─────────────────────────────────────────────────────
st.subheader("📉 R-Multiple Distribution")
if not trades_df.empty and "R_multiple" in trades_df.columns:
    r_vals = trades_df["R_multiple"].dropna()
    fig_hist = px.histogram(
        r_vals,
        nbins=20,
        color_discrete_sequence=["#3498db"],
        labels={"value": "R Multiple", "count": "Frequency"},
    )
    fig_hist.add_vline(x=0, line_dash="dash", line_color="#e74c3c", annotation_text="Break-even")
    fig_hist.update_layout(
        paper_bgcolor="#0e1117",
        plot_bgcolor="#161b22",
        font=dict(color="#c9d1d9"),
        height=250,
        margin=dict(l=10, r=10, t=10, b=10),
        showlegend=False,
    )
    st.plotly_chart(fig_hist, use_container_width=True)

st.markdown("---")

# ─── Full Trades Table ───────────────────────────────────────────────────────────
st.subheader("📋 All Trades")
if trades_df.empty:
    st.info("No trades executed in the selected period.")
else:
    cols_show = [
        "timestamp", "direction", "entry_price", "exit_price",
        "sl", "tp", "result", "R_multiple", "pnl",
        "equity_before", "equity_after", "exit_reason", "bars_held",
    ]
    display_cols = [c for c in cols_show if c in trades_df.columns]
    _render_trades_table(trades_df[display_cols])

    csv_data = trades_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Download Trades CSV",
        data=csv_data,
        file_name=f"backtest_{bt_start}_{bt_end}.csv",
        mime="text/csv",
    )

# ─── Footer ─────────────────────────────────────────────────────────────────────
st.markdown("---")
st.caption(
    "SEAN0-ALGO — XAU Regime-Adaptive Breakout  •  Paper mode only  •  "
    "Past performance does not guarantee future results."
)
