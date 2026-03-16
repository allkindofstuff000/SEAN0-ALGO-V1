import pandas as pd

df = pd.read_csv('trades.csv')
df['entry_timestamp'] = pd.to_datetime(df['entry_timestamp'], utc=True)
df['exit_timestamp'] = pd.to_datetime(df['exit_timestamp'], utc=True)
wins = df[df['result'] == 'WIN']
losses = df[df['result'] == 'LOSS']
gp = wins['pnl'].sum()
gl = abs(losses['pnl'].sum())

print('=== IMPROVED STRATEGY — FULL BREAKDOWN ===')
print(f'Trades       : {len(df)} (was 212)')
print(f'Wins         : {len(wins)} | Losses: {len(losses)}')
print(f'Win Rate     : {len(wins)/len(df)*100:.2f}% (was 42.45%)')
print(f'Profit Factor: {gp/gl:.4f} (was 1.00)')
print(f'Avg Win R    : {wins["R_multiple"].mean():.4f}')
print(f'Avg Loss R   : {losses["R_multiple"].mean():.4f}')
print(f'Avg Win PnL  : ${wins["pnl"].mean():.2f}')
print(f'Avg Loss PnL : ${losses["pnl"].mean():.2f}')
print(f'Best Trade   : ${wins["pnl"].max():.2f}')
print(f'Worst Trade  : ${losses["pnl"].min():.2f}')

print()
print('--- Direction breakdown ---')
for d in ['BUY', 'SELL']:
    sub = df[df['direction'] == d]
    w = sub[sub['result'] == 'WIN']
    print(f'{d}: {len(sub)} trades | {len(w)/len(sub)*100:.1f}% WR | avg R={sub["R_multiple"].mean():.4f}')

print()
print('--- Month breakdown ---')
df['month'] = df['entry_timestamp'].dt.to_period('M').astype(str)
for m, g in df.groupby('month'):
    w = g[g['result'] == 'WIN']
    net = g['pnl'].sum()
    print(f'{m}: {len(g)} trades | {len(w)/len(g)*100:.1f}% WR | PnL=${net:.2f}')

print()
print('--- Exit reason ---')
print(df['exit_reason'].value_counts().to_string())
