import pandas as pd
df = pd.read_csv('trades.csv')
df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True, errors='coerce')
df['entry_timestamp'] = pd.to_datetime(df['entry_timestamp'], utc=True, errors='coerce')
df['exit_timestamp'] = pd.to_datetime(df['exit_timestamp'], utc=True, errors='coerce')
wins = df[(df['direction']=='SELL') & (df['result']=='WIN')].reset_index(drop=True)
wins.index += 1
for i, row in wins.iterrows():
    print(f"#{i} | Signal={str(row['timestamp'])[:19]} | Entry={str(row['entry_timestamp'])[:19]} | Exit={str(row['exit_timestamp'])[:19]}")
    print(f"     Entry={row['entry_price']:.2f}  SL={row['sl']:.2f}  TP={row['tp']:.2f}  ATR={row['atr']:.2f}  RSI={row['rsi']:.1f}")
    print(f"     Result=WIN  R={row['R_multiple']:.3f}  PnL=${row['pnl']:.2f}  bars_held={row['bars_held']}")
    print()
