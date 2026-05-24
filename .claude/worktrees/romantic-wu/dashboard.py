import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import time
from datetime import datetime, timedelta
from pathlib import Path
import requests
import threading
import pytz

LOG_FILE = Path("C:/Users/ALGO/Desktop/QUOTEX_BOT/logs/decision_trace.log")
TRADES_CSV = Path("C:/Users/ALGO/Desktop/QUOTEX_BOT/trades.csv")
BACKTEST_SUMMARY_CSV = Path("C:/Users/ALGO/Desktop/QUOTEX_BOT/performance.csv")

st.set_page_config(page_title="SEAN0-ALGO-V1 Trading Dashboard", layout="wide")

# Convert UTC to Bangladesh time
BD_TZ = pytz.timezone('Asia/Dhaka')

def convert_to_bd_time(ts):
    if isinstance(ts, pd.Timestamp):
        return ts.tz_localize('UTC').tz_convert(BD_TZ).strftime('%Y-%m-%d %H:%M:%S')
    return ts

# Load backtest summary csv
@st.cache_data
def load_backtest_summary():
    try:
        return pd.read_csv(BACKTEST_SUMMARY_CSV)
    except Exception:
        return pd.DataFrame()

# Load trades csv
@st.cache_data
def load_trades():
    try:
        df = pd.read_csv(TRADES_CSV)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        return df
    except Exception:
        return pd.DataFrame()

# Load tail of log file
@st.cache_data
 def tail_log(file_path, lines=50):
    try:
        with open(file_path, 'r') as f:
            return ''.join(f.readlines()[-lines:])
    except Exception as e:
        return f"Error reading log: {e}"

# Fetch live XAUUSD price from OANDA API
@st.cache_data(ttl=30)
 def fetch_live_price():
    url = 'https://api-fxpractice.oanda.com/v3/instruments/XAU_USD/price'
    headers = {'Authorization': 'Bearer YOUR_OANDA_API_KEY'}
    try:
        response = requests.get(url, headers=headers)
        price_json = response.json()
        price = float(price_json['price']['closeoutBid'])
        return price
    except Exception:
        return None

# Plot equity curve
 def plot_equity_curve(trades_df):
    if trades_df.empty:
        st.write("No trades for equity curve.")
        return
    eq = trades_df["equity_after"]
    plt.figure(figsize=(12, 4))
    plt.plot(eq, label='Equity Curve')
    plt.xlabel('Trade Number')
    plt.ylabel('Equity')
    plt.legend()
    st.pyplot(plt)
    plt.clf()

# Plot mini 5m chart with EMA, RSI, ATR
 def plot_mini_chart(df_5m):
    if df_5m.empty:
        st.write("No 5m candle data to plot.")
        return
    plt.figure(figsize=(10, 4))
    plt.plot(df_5m['close'], label='Close')
    if 'ema50' in df_5m.columns:
        plt.plot(df_5m['ema50'], label='EMA50')
    if 'ema200' in df_5m.columns:
        plt.plot(df_5m['ema200'], label='EMA200')
    if 'rsi14' in df_5m.columns:
        plt.plot(df_5m['rsi14'], label='RSI14')
    if 'atr' in df_5m.columns:
        plt.plot(df_5m['atr'], label='ATR')
    plt.legend()
    plt.tight_layout()
    st.pyplot(plt)
    plt.clf()

# Real-time signal feed
@st.cache_data(ttl=30)
 def load_recent_signals():
    df = load_trades()
    if df.empty:
        return pd.DataFrame()
    df = df.tail(20).copy()
    df['timestamp_bd'] = df['timestamp'].apply(convert_to_bd_time)
    return df[['timestamp_bd', 'direction', 'entry_price', 'exit_price', 'result']]

# Streamlit UI
st.title("SEAN0-ALGO-V1 Trading Dashboard")

# Tabs
tab_live, tab_backtest, tab_logs = st.tabs(["Live Data", "Backtest", "Logs"])

with tab_live:
    st.subheader("Live Market Data")
    price = fetch_live_price()
    if price:
        st.metric("XAUUSD Price", f"${price:.2f}")
    else:
        st.write("Price data unavailable")

    # Load recent 5m candles with indicators
    trades = load_trades()
    df_5m = trades.tail(50)
    plot_mini_chart(df_5m)

    st.subheader("Indicators")
    if not df_5m.empty:
        latest = df_5m.iloc[-1]
        ema_bias = "Bullish" if latest['ema50'] > latest['ema200'] else "Bearish"
        st.write(f"EMA Bias: {ema_bias}")
        st.write(f"RSI: {latest.get('rsi14', 'N/A')}")
        st.write(f"ATR vs Average: {latest.get('atr', 'N/A')}")

    st.subheader("Recent Signals")
    recent_signals = load_recent_signals()
    if recent_signals.empty:
        st.write("No recent signals.")
    else:
        st.dataframe(recent_signals)

with tab_backtest:
    st.subheader("Backtest Summary")
    summary_df = load_backtest_summary()
    st.dataframe(summary_df)

    st.subheader("Equity Curve")
    plot_equity_curve(load_trades())

    if st.button("Run Backtest Now"):
        with st.spinner("Running backtest..."):
            result = subprocess.run([
                "C:\\Users\\ALGO\\.openclaw\\workspace\\sean0_algo.cmd",
                "backtest",
                "--mode",
                "forex",
                "--months",
                "6"
            ], capture_output=True, text=True)
            if result.returncode == 0:
                st.success("Backtest completed.")
            else:
                st.error(f"Backtest failed:\n{result.stderr}")

with tab_logs:
    st.subheader("Live Paper Trading Log")
    st.text_area("", tail_log(LOG_FILE), height=300)


if __name__ == "__main__":
    pass
