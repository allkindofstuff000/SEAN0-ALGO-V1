"""Run 4 variant backtests: Baseline, Level 1 (FVG), Level 2 (FVG+OB), Level 3 (FVG+OB+SSL)."""
import sys, time, requests
from datetime import datetime, timezone

# ── Indicators ──
def ema(vals, p):
    if len(vals) < p: return [None]*len(vals)
    m = 2.0/(p+1); r = [None]*(p-1); s = sum(vals[:p])/p; r.append(s)
    for i in range(p, len(vals)): s = vals[i]*m + r[-1]*(1-m); r.append(s)
    return r

def calc_rsi(cls, p=14):
    if len(cls) < p+1: return [None]*len(cls)
    r = [None]*p; g,l = [],[]
    for i in range(1,p+1): d=cls[i]-cls[i-1]; g.append(max(d,0)); l.append(max(-d,0))
    ag=sum(g)/p; al=sum(l)/p; rs=ag/al if al>0 else 100; r.append(100-100/(1+rs))
    for i in range(p+1,len(cls)):
        d=cls[i]-cls[i-1]; ag=(ag*(p-1)+max(d,0))/p; al=(al*(p-1)+max(-d,0))/p
        rs=ag/al if al>0 else 100; r.append(100-100/(1+rs))
    return r

def calc_atr(cands, p=14):
    if len(cands)<2: return [None]*len(cands)
    trs=[None]
    for i in range(1,len(cands)):
        h,l,pc=cands[i]['high'],cands[i]['low'],cands[i-1]['close']
        trs.append(max(h-l,abs(h-pc),abs(l-pc)))
    r=[None]*p
    if len(trs)>p:
        a=sum(t for t in trs[1:p+1])/p; r.append(a)
        for i in range(p+1,len(trs)): a=(a*(p-1)+trs[i])/p; r.append(a)
    return r

# ── ICT detections ──
def find_fvgs(cands, i, direction, lookback=20):
    fvgs = []
    start = max(2, i-lookback)
    for j in range(start, i-1):
        prev_c, curr_c, next_c = cands[j-1], cands[j], cands[j+1]
        if direction == 'LONG':
            gap_low, gap_high = prev_c['high'], next_c['low']
        else:
            gap_low, gap_high = next_c['high'], prev_c['low']
        if gap_high > gap_low and (gap_high - gap_low) >= 0.30:
            fvgs.append({'low': gap_low, 'high': gap_high, 'mid': (gap_low+gap_high)/2, 'idx': j})
    return fvgs

def find_obs(cands, i, direction, lookback=30):
    obs = []
    start = max(0, i-lookback)
    for j in range(start+1, min(i-2, len(cands)-3)):
        c = cands[j]
        if direction == 'LONG' and c['close'] < c['open']:
            bull_after = sum(1 for k in range(j+1, min(j+4, len(cands))) if cands[k]['close'] > cands[k]['open'])
            if bull_after >= 2:
                obs.append({'low': c['low'], 'high': c['high'], 'mid': (c['low']+c['high'])/2, 'idx': j})
        elif direction == 'SHORT' and c['close'] > c['open']:
            bear_after = sum(1 for k in range(j+1, min(j+4, len(cands))) if cands[k]['close'] < cands[k]['open'])
            if bear_after >= 2:
                obs.append({'low': c['low'], 'high': c['high'], 'mid': (c['low']+c['high'])/2, 'idx': j})
    return obs

def check_ssl_sweep(cands, i, lookback=25):
    if i < lookback + 5: return False
    tol = 0.20
    lows = [(j, cands[j]['low']) for j in range(max(0,i-lookback), i-1)]
    for a in range(len(lows)):
        for b in range(a+1, len(lows)):
            if abs(lows[a][1] - lows[b][1]) <= tol:
                eq = (lows[a][1] + lows[b][1]) / 2
                for k in range(max(0,i-5), i+1):
                    c = cands[k]
                    if c['low'] < eq and c['close'] > eq:
                        depth = eq - c['low']
                        if 0.10 <= depth <= 0.80:
                            return True
    return False

# ── Filters ──
def filt_spread(c):
    body = abs(c['close']-c['open']); rng = c['high']-c['low']
    return rng > 0 and body/rng >= 0.30

def filt_time(c):
    ts = c.get('time',0)
    if isinstance(ts,(int,float)) and ts > 1e9:
        h = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if 2 <= h < 5: return False
        if 20 <= h < 22: return False
    return True

# ── Backtest ──
def run_variant(cands, variant, balance=5000.0, risk_pct=0.02):
    closes = [c['close'] for c in cands]
    rsi_v = calc_rsi(closes, 7)
    ema_f = ema(closes, 9)
    ema_s = ema(closes, 50)
    atr_v = calc_atr(cands, 14)
    trades = []; open_t = None; tp_ratio = 2.5; start = 51

    for i in range(start, len(cands)-1):
        c = cands[i]; price = c['close']
        if open_t:
            is_long = open_t['dir'] == 'LONG'
            hit_sl = (c['low'] <= open_t['sl']) if is_long else (c['high'] >= open_t['sl'])
            hit_tp = (c['high'] >= open_t['tp']) if is_long else (c['low'] <= open_t['tp'])
            if hit_sl or hit_tp:
                pnl = open_t['risk_amt'] * tp_ratio if hit_tp else -open_t['risk_amt']
                balance += pnl
                trades.append({'dir':open_t['dir'],'entry':open_t['entry'],'sl':open_t['sl'],'tp':open_t['tp'],
                    'result':'WIN' if hit_tp else 'LOSS','pnl':round(pnl,2),'balance':round(balance,2),
                    'time':open_t['ts'],'strength':open_t.get('strength','STD')})
                open_t = None
            continue

        if any(v is None for v in [rsi_v[i],rsi_v[i-1],ema_f[i],ema_s[i],atr_v[i]]): continue
        cr, pr = rsi_v[i], rsi_v[i-1]
        signal = None
        if pr <= 35 and cr > 35 and price > ema_s[i]: signal = 'LONG'
        elif pr >= 70 and cr < 70 and price < ema_s[i]: signal = 'SHORT'
        if not signal: continue
        if not filt_spread(c) or not filt_time(c): continue

        strength = 'STD'
        if variant == 'LEVEL1':
            fvgs = find_fvgs(cands, i, signal, 20)
            near = [f for f in fvgs if abs(f['mid'] - price) <= 3.0]
            if not near: continue
            strength = 'FVG'
        elif variant == 'LEVEL2':
            fvgs = find_fvgs(cands, i, signal, 20)
            obs_l = find_obs(cands, i, signal, 30)
            near_fvg = [f for f in fvgs if abs(f['mid'] - price) <= 3.0]
            near_ob = [o for o in obs_l if price >= o['low']-0.50 and price <= o['high']+0.50]
            if not near_fvg and not near_ob: continue
            strength = 'FVG+OB' if (near_fvg and near_ob) else ('FVG' if near_fvg else 'OB')
        elif variant == 'LEVEL3':
            fvgs = find_fvgs(cands, i, signal, 20)
            obs_l = find_obs(cands, i, signal, 30)
            near_fvg = [f for f in fvgs if abs(f['mid'] - price) <= 3.0]
            near_ob = [o for o in obs_l if price >= o['low']-0.50 and price <= o['high']+0.50]
            if not near_fvg and not near_ob: continue
            has_sweep = check_ssl_sweep(cands, i, 25)
            strength = 'SWEEP+ZONE' if has_sweep else 'ZONE_ONLY'

        ts = c.get('time',0)
        ts_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%m/%d %H:%M') if isinstance(ts,(int,float)) and ts>1e9 else str(ts)
        a = atr_v[i]; risk_amt = balance * risk_pct
        if signal == 'LONG': sl = price - a; tp = price + (price-sl)*tp_ratio
        else: sl = price + a; tp = price - (sl-price)*tp_ratio
        open_t = {'dir':signal,'entry':price,'sl':sl,'tp':tp,'risk_amt':risk_amt,'ts':ts_str,'strength':strength}

    total = len(trades)
    wins = [t for t in trades if t['result']=='WIN']
    loss = [t for t in trades if t['result']=='LOSS']
    gw = sum(t['pnl'] for t in wins); gl = abs(sum(t['pnl'] for t in loss))
    pnl = sum(t['pnl'] for t in trades)
    pk=5000.0; mdd=0; bl=5000.0
    for t in trades:
        bl+=t['pnl']
        if bl>pk: pk=bl
        dd=pk-bl;
        if dd>mdd: mdd=dd
    return {'variant':variant,'trades':total,'wins':len(wins),'losses':len(loss),
        'winRate':round(len(wins)/total*100,1) if total else 0,
        'pf':round(gw/gl,2) if gl>0 else round(gw,2),
        'pnl':round(pnl,2),'finalBal':round(5000+pnl,2),'maxDD':round(mdd,2),
        'returnPct':round(pnl/5000*100,1),'trade_list':trades}


if __name__ == '__main__':
    print('Fetching 30 days ETH/USDT 15M...')
    all_c = []; end_t = int(time.time()*1000); start_t = end_t - (30*24*60*60*1000); cursor = start_t
    while cursor < end_t:
        url = f'https://api.binance.com/api/v3/klines?symbol=ETHUSDT&interval=15m&limit=1000&startTime={cursor}'
        r = requests.get(url, timeout=15); data = r.json()
        if not data: break
        for k in data: all_c.append({'time':k[0]/1000,'open':float(k[1]),'high':float(k[2]),'low':float(k[3]),'close':float(k[4]),'volume':float(k[5])})
        cursor = int(data[-1][0]) + 1; time.sleep(0.15)
    seen = set(); candles = []
    for c in all_c:
        if c['time'] not in seen: seen.add(c['time']); candles.append(c)
    candles.sort(key=lambda c: c['time'])
    print(f'Loaded {len(candles)} candles\n')

    base = run_variant(candles, 'BASELINE')
    lv1 = run_variant(candles, 'LEVEL1')
    lv2 = run_variant(candles, 'LEVEL2')
    lv3 = run_variant(candles, 'LEVEL3')

    print('='*100)
    print('  RSI EMA + ICT VARIANTS  |  ETH/USDT 15M  |  30 DAYS  |  $5,000  |  2% risk  |  2.5R TP')
    print('='*100)
    hdr = f"  {'Variant':<35} {'Trades':>7} {'Wins':>6} {'Loss':>6} {'Win%':>7} {'PF':>7} {'P&L':>12} {'Return':>9} {'MaxDD':>10}"
    print(hdr)
    print('  '+'-'*98)
    for v in [base, lv1, lv2, lv3]:
        n = v['variant']
        if n == 'BASELINE': n = 'BASELINE (RSI+EMA+filters)'
        elif n == 'LEVEL1': n = 'LV1 - + FVG within $3'
        elif n == 'LEVEL2': n = 'LV2 - + FVG or OB zone'
        elif n == 'LEVEL3': n = 'LV3 - + FVG/OB + SSL sweep'
        print(f"  {n:<35} {v['trades']:>7} {v['wins']:>6} {v['losses']:>6} {str(v['winRate'])+'%':>7} {v['pf']:>7} {'$'+str(v['pnl']):>12} {str(v['returnPct'])+'%':>9} {'$'+str(v['maxDD']):>10}")

    print()
    for v in [base, lv1, lv2, lv3]:
        print(f"  -- {v['variant']} TRADES --")
        print(f"  {'#':>3} {'Date':>12} {'Dir':<6} {'Entry':>9} {'SL':>9} {'TP':>9} {'Result':>7} {'P&L':>10} {'Bal':>10} {'Type':>10}")
        print('  '+'-'*90)
        for idx, t in enumerate(v['trade_list']):
            print(f"  {idx+1:>3} {t['time']:>12} {t['dir']:<6} {t['entry']:>9.2f} {t['sl']:>9.2f} {t['tp']:>9.2f} {t['result']:>7} {t['pnl']:>+10.2f} {t['balance']:>10.2f} {t['strength']:>10}")
        print()

    # Summary
    print('  == IMPACT SUMMARY ==')
    for v in [lv1, lv2, lv3]:
        wd = v['winRate'] - base['winRate']
        td = v['trades'] - base['trades']
        pd = v['pnl'] - base['pnl']
        print(f"  {v['variant']:<12} WR: {wd:+.1f}%  |  Trades: {td:+d}  |  P&L: ${pd:+.2f}")

    # Level 3 breakdown
    sw = [t for t in lv3['trade_list'] if t['strength']=='SWEEP+ZONE']
    zo = [t for t in lv3['trade_list'] if t['strength']=='ZONE_ONLY']
    if sw:
        w = sum(1 for t in sw if t['result']=='WIN')
        print(f"  LV3 SWEEP+ZONE: {len(sw)} trades | {w}W {len(sw)-w}L | WR {w/len(sw)*100:.0f}%")
    if zo:
        w = sum(1 for t in zo if t['result']=='WIN')
        print(f"  LV3 ZONE_ONLY:  {len(zo)} trades | {w}W {len(zo)-w}L | WR {w/len(zo)*100:.0f}%")
