"""Test 4 improvements + combinations against baseline on 30-day ETH/USDT 15M."""
import sys, time, requests, math
from datetime import datetime, timezone

# ── Indicators ──
def ema(vals, p):
    if len(vals) < p: return [None]*len(vals)
    m = 2.0/(p+1); r = [None]*(p-1); s = sum(vals[:p])/p; r.append(s)
    for i in range(p, len(vals)): s = vals[i]*m + r[-1]*(1-m); r.append(s)
    return r

def calc_rsi(cls, p=7):
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

def calc_adx(cands, p=14):
    n = len(cands)
    if n < p*2+2: return [None]*n
    r = [None]*n
    pdm = [0.0]*n; mdm = [0.0]*n; tr_l = [0.0]*n
    for i in range(1, n):
        h,l = cands[i]['high'], cands[i]['low']
        ph,pl = cands[i-1]['high'], cands[i-1]['low']
        pc = cands[i-1]['close']
        tr_l[i] = max(h-l, abs(h-pc), abs(l-pc))
        up = h - ph; dn = pl - l
        pdm[i] = up if up > dn and up > 0 else 0
        mdm[i] = dn if dn > up and dn > 0 else 0
    atr_s = sum(tr_l[1:p+1]); ps = sum(pdm[1:p+1]); ms = sum(mdm[1:p+1])
    dx_vals = []
    for i in range(p, n):
        if i == p:
            atr_s = sum(tr_l[1:p+1]); ps = sum(pdm[1:p+1]); ms = sum(mdm[1:p+1])
        else:
            atr_s = atr_s - atr_s/p + tr_l[i]
            ps = ps - ps/p + pdm[i]
            ms = ms - ms/p + mdm[i]
        pdi = 100*ps/atr_s if atr_s>0 else 0
        mdi = 100*ms/atr_s if atr_s>0 else 0
        ds = pdi+mdi
        dx = 100*abs(pdi-mdi)/ds if ds>0 else 0
        dx_vals.append(dx)
    if len(dx_vals) >= p:
        adx = sum(dx_vals[:p])/p
        for j in range(p, n):
            idx = j - p
            if idx < len(dx_vals):
                adx = (adx*(p-1) + dx_vals[idx])/p
                r[j] = adx
    return r

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

# ── IMP2: Nearest swing high/low check ──
def find_nearest_resistance(cands, i, direction, lookback=50):
    price = cands[i]['close']
    start = max(0, i - lookback)
    if direction == 'LONG':
        highs = [cands[j]['high'] for j in range(start, i) if cands[j]['high'] > price]
        if not highs: return None
        return min(highs, key=lambda h: h - price)
    else:
        lows = [cands[j]['low'] for j in range(start, i) if cands[j]['low'] < price]
        if not lows: return None
        return max(lows, key=lambda l: price - l)

# ── Backtest engine ──
def run_variant(cands, flags, balance=5000.0, risk_pct=0.02):
    """
    flags: dict with keys 'imp1','imp2','imp3','imp4' (bool)
    imp1 = two-stage exit
    imp2 = resistance check
    imp3 = ADX filter
    imp4 = candle close confirmation (enter on next bar open)
    """
    use_imp1 = flags.get('imp1', False)
    use_imp2 = flags.get('imp2', False)
    use_imp3 = flags.get('imp3', False)
    use_imp4 = flags.get('imp4', False)

    closes = [c['close'] for c in cands]
    rsi_v = calc_rsi(closes, 7)
    ema_f = ema(closes, 9)
    ema_s = ema(closes, 50)
    atr_v = calc_atr(cands, 14)
    adx_v = calc_adx(cands, 14) if use_imp3 else [None]*len(cands)

    trades = []; open_t = None; start = 52
    init_bal = balance
    skipped = {'resistance': 0, 'adx': 0}

    for i in range(start, len(cands)-1):
        c = cands[i]; price = c['close']

        # ── Manage open trade ──
        if open_t:
            is_long = open_t['dir'] == 'LONG'

            if use_imp1:
                # Two-stage exit
                # Check SL first
                hit_sl_price = open_t['sl']
                if is_long:
                    hit_sl = c['low'] <= hit_sl_price
                else:
                    hit_sl = c['high'] >= hit_sl_price

                if hit_sl:
                    if open_t.get('tp1_hit'):
                        # SL after TP1 = break-even on remaining 50%
                        pnl = open_t['tp1_pnl']  # already banked TP1 profit
                        balance += 0  # BE on remaining half
                    else:
                        pnl = -open_t['risk_amt']
                    balance += pnl if not open_t.get('tp1_hit') else 0
                    result = 'BE' if open_t.get('tp1_hit') else 'LOSS'
                    actual_pnl = open_t.get('tp1_pnl', 0) if open_t.get('tp1_hit') else -open_t['risk_amt']
                    trades.append({
                        'dir': open_t['dir'], 'entry': open_t['entry'],
                        'sl': open_t['sl'], 'tp': open_t['tp2'],
                        'result': result, 'pnl': round(actual_pnl, 2),
                        'balance': round(balance, 2),
                        'time': open_t['ts'], 'pnlR': round(actual_pnl / (open_t['risk_amt'] or 1), 2),
                    })
                    open_t = None
                    continue

                # Check TP1 (1.5R)
                if not open_t.get('tp1_hit'):
                    if is_long:
                        hit_tp1 = c['high'] >= open_t['tp1']
                    else:
                        hit_tp1 = c['low'] <= open_t['tp1']
                    if hit_tp1:
                        tp1_pnl = open_t['risk_amt'] * 0.5 * 1.5  # 50% at 1.5R = 0.75R
                        balance += tp1_pnl
                        open_t['tp1_hit'] = True
                        open_t['tp1_pnl'] = tp1_pnl
                        open_t['sl'] = open_t['entry']  # move SL to BE

                # Check TP2 (3.5R)
                if open_t and open_t.get('tp1_hit'):
                    if is_long:
                        hit_tp2 = c['high'] >= open_t['tp2']
                    else:
                        hit_tp2 = c['low'] <= open_t['tp2']
                    if hit_tp2:
                        tp2_pnl = open_t['risk_amt'] * 0.5 * 3.5  # 50% at 3.5R = 1.75R
                        balance += tp2_pnl
                        total_pnl = open_t['tp1_pnl'] + tp2_pnl
                        trades.append({
                            'dir': open_t['dir'], 'entry': open_t['entry'],
                            'sl': open_t['sl'], 'tp': open_t['tp2'],
                            'result': 'WIN', 'pnl': round(total_pnl, 2),
                            'balance': round(balance, 2),
                            'time': open_t['ts'], 'pnlR': round(total_pnl / (open_t['risk_amt'] or 1), 2),
                        })
                        open_t = None
            else:
                # Standard single TP exit (2.5R)
                if is_long:
                    hit_sl = c['low'] <= open_t['sl']
                    hit_tp = c['high'] >= open_t['tp']
                else:
                    hit_sl = c['high'] >= open_t['sl']
                    hit_tp = c['low'] <= open_t['tp']

                if hit_sl or hit_tp:
                    pnl = open_t['risk_amt'] * 2.5 if hit_tp else -open_t['risk_amt']
                    balance += pnl
                    trades.append({
                        'dir': open_t['dir'], 'entry': open_t['entry'],
                        'sl': open_t['sl'], 'tp': open_t['tp'],
                        'result': 'WIN' if hit_tp else 'LOSS',
                        'pnl': round(pnl, 2), 'balance': round(balance, 2),
                        'time': open_t['ts'], 'pnlR': 2.5 if hit_tp else -1.0,
                    })
                    open_t = None
            continue

        # ── Signal check ──
        if any(v is None for v in [rsi_v[i], rsi_v[i-1], ema_f[i], ema_s[i], atr_v[i]]): continue
        cr, pr = rsi_v[i], rsi_v[i-1]
        signal = None
        if pr <= 35 and cr > 35 and price > ema_s[i]: signal = 'LONG'
        elif pr >= 70 and cr < 70 and price < ema_s[i]: signal = 'SHORT'
        if not signal: continue
        if not filt_spread(c) or not filt_time(c): continue

        # IMP3: ADX filter
        if use_imp3:
            adx_val = adx_v[i]
            if adx_val is not None and adx_val < 20:
                skipped['adx'] += 1
                continue

        # IMP2: Resistance check
        if use_imp2:
            a = atr_v[i]
            risk_dist = a * 1.0
            min_room = risk_dist * 1.5
            nearest = find_nearest_resistance(cands, i, signal, 50)
            if nearest is not None:
                if signal == 'LONG' and (nearest - price) < min_room:
                    skipped['resistance'] += 1
                    continue
                elif signal == 'SHORT' and (price - nearest) < min_room:
                    skipped['resistance'] += 1
                    continue

        # IMP4: Candle close confirmation — enter on next bar open
        if use_imp4:
            if i + 1 >= len(cands): continue
            entry_price = cands[i+1]['open']
            entry_idx = i + 1
        else:
            entry_price = price
            entry_idx = i

        a = atr_v[i]
        risk_amt = balance * risk_pct

        if signal == 'LONG':
            sl = entry_price - a * 1.0
            if use_imp1:
                tp1 = entry_price + (entry_price - sl) * 1.5
                tp2 = entry_price + (entry_price - sl) * 3.5
                tp = tp2
            else:
                tp = entry_price + (entry_price - sl) * 2.5
                tp1 = tp; tp2 = tp
        else:
            sl = entry_price + a * 1.0
            if use_imp1:
                tp1 = entry_price - (sl - entry_price) * 1.5
                tp2 = entry_price - (sl - entry_price) * 3.5
                tp = tp2
            else:
                tp = entry_price - (sl - entry_price) * 2.5
                tp1 = tp; tp2 = tp

        ts = c.get('time', 0)
        ts_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime('%m/%d %H:%M') if isinstance(ts,(int,float)) and ts>1e9 else str(ts)

        open_t = {
            'dir': signal, 'entry': entry_price, 'sl': sl,
            'tp': tp, 'tp1': tp1, 'tp2': tp2,
            'risk_amt': risk_amt, 'ts': ts_str,
            'tp1_hit': False, 'tp1_pnl': 0,
        }

    # Metrics
    total = len(trades)
    wins = [t for t in trades if t['result'] == 'WIN']
    bes = [t for t in trades if t['result'] == 'BE']
    losses = [t for t in trades if t['result'] == 'LOSS']
    pnl_total = sum(t['pnl'] for t in trades)
    gw = sum(t['pnl'] for t in trades if t['pnl'] > 0)
    gl = abs(sum(t['pnl'] for t in trades if t['pnl'] < 0))
    pk = init_bal; mdd = 0; bl = init_bal
    for t in trades:
        bl += t['pnl']
        if bl > pk: pk = bl
        dd = pk - bl
        if dd > mdd: mdd = dd

    # Win rate counts BEs as non-losses
    non_loss = len(wins) + len(bes)
    wr = round(non_loss / total * 100, 1) if total else 0

    return {
        'trades': total, 'wins': len(wins), 'bes': len(bes), 'losses': len(losses),
        'winRate': wr,
        'pf': round(gw/gl, 2) if gl > 0 else round(gw, 2),
        'pnl': round(pnl_total, 2),
        'finalBal': round(init_bal + pnl_total, 2),
        'maxDD': round(mdd, 2),
        'returnPct': round(pnl_total / init_bal * 100, 1),
        'skipped': skipped,
        'trade_list': trades,
    }


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

    # Define all test variants
    variants = [
        ('BASE',   {'imp1':False,'imp2':False,'imp3':False,'imp4':False}),
        ('IMP1',   {'imp1':True, 'imp2':False,'imp3':False,'imp4':False}),
        ('IMP2',   {'imp1':False,'imp2':True, 'imp3':False,'imp4':False}),
        ('IMP3',   {'imp1':False,'imp2':False,'imp3':True, 'imp4':False}),
        ('IMP4',   {'imp1':False,'imp2':False,'imp3':False,'imp4':True}),
        ('IMP1+2', {'imp1':True, 'imp2':True, 'imp3':False,'imp4':False}),
        ('IMP1+3', {'imp1':True, 'imp2':False,'imp3':True, 'imp4':False}),
        ('IMP1+4', {'imp1':True, 'imp2':False,'imp3':False,'imp4':True}),
        ('IMP2+3', {'imp1':False,'imp2':True, 'imp3':True, 'imp4':False}),
        ('ALL',    {'imp1':True, 'imp2':True, 'imp3':True, 'imp4':True}),
    ]

    results = {}
    for name, flags in variants:
        print(f'  Running {name}...')
        results[name] = run_variant(candles, flags)

    base = results['BASE']

    print('\n' + '='*110)
    print('  RSI EMA ETH — 4 IMPROVEMENTS TEST  |  $5,000  |  2% risk  |  30 days  |  15M')
    print('  IMP1=Two-Stage Exit  IMP2=Resistance Check  IMP3=ADX Filter  IMP4=Next-Bar Entry')
    print('='*110)

    hdr = f"  {'Variant':<12} {'Trades':>7} {'W':>4} {'BE':>4} {'L':>4} {'Win%':>7} {'PF':>7} {'P&L':>11} {'Return':>9} {'MaxDD':>9} {'vs BASE':>10}"
    print(hdr)
    print('  ' + '-'*108)

    for name, _ in variants:
        v = results[name]
        diff = v['pnl'] - base['pnl']
        wr_diff = v['winRate'] - base['winRate']

        # Color coding
        if v['pf'] >= base['pf'] and v['pnl'] >= base['pnl'] and v['winRate'] >= base['winRate']:
            tag = ' *** GREEN'
        elif sum([v['pf']>=base['pf'], v['pnl']>=base['pnl'], v['winRate']>=base['winRate']]) >= 2:
            tag = '  ** YELLOW'
        elif name == 'BASE':
            tag = '          '
        else:
            tag = '   * RED'

        be_str = str(v['bes']) if v['bes'] > 0 else '-'
        print(f"  {name:<12} {v['trades']:>7} {v['wins']:>4} {be_str:>4} {v['losses']:>4} {str(v['winRate'])+'%':>7} {v['pf']:>7} {'$'+str(v['pnl']):>11} {str(v['returnPct'])+'%':>9} {'$'+str(v['maxDD']):>9} {tag}")

    # Find best
    print('\n  == BEST COMBINATION ==')
    scored = []
    for name, _ in variants:
        if name == 'BASE': continue
        v = results[name]
        if v['trades'] < 15: continue  # need enough trades
        score = v['winRate'] * v['pf']
        scored.append((name, score, v))
    scored.sort(key=lambda x: x[1], reverse=True)

    if scored:
        best_name, best_score, best = scored[0]
        print(f'\n  WINNER: {best_name}')
        print(f'  Trades:       {best["trades"]} vs {base["trades"]} baseline')
        print(f'  Win Rate:     {best["winRate"]}% vs {base["winRate"]}%  ({best["winRate"]-base["winRate"]:+.1f}%)')
        print(f'  Profit Factor:{best["pf"]} vs {base["pf"]}  ({best["pf"]-base["pf"]:+.2f})')
        print(f'  P&L:          ${best["pnl"]} vs ${base["pnl"]}  (${best["pnl"]-base["pnl"]:+.2f})')
        print(f'  Max Drawdown: ${best["maxDD"]} vs ${base["maxDD"]}')
        print(f'  Score (WR*PF):{best_score:.1f} vs {base["winRate"]*base["pf"]:.1f}')

    # Show trade details for best
    if scored:
        best_name = scored[0][0]
        best_v = results[best_name]
        print(f'\n  -- {best_name} TRADE LOG --')
        print(f"  {'#':>3} {'Date':>12} {'Dir':<6} {'Entry':>9} {'SL':>9} {'TP':>9} {'Result':>7} {'P&L':>10} {'Bal':>10} {'R':>6}")
        print('  ' + '-'*90)
        for idx, t in enumerate(best_v['trade_list']):
            print(f"  {idx+1:>3} {t['time']:>12} {t['dir']:<6} {t['entry']:>9.2f} {t['sl']:>9.2f} {t['tp']:>9.2f} {t['result']:>7} {t['pnl']:>+10.2f} {t['balance']:>10.2f} {t.get('pnlR',0):>+6.2f}")
