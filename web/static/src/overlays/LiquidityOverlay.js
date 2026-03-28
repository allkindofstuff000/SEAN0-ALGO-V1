(function attachLiquidityOverlay(global) {
  class LiquidityOverlay {
    constructor(chart, series, config = {}) {
      this.chart   = chart;
      this.series  = series;
      this.config  = config;
      this.visible = true;
      this.data    = { levels: [], markers: [], latestBullSweep: null, latestBearSweep: null };
      // BUG 3 FIX: persistent pool state — survives re-calculations so each pool
      // fires exactly ONE sweep marker, then fades for 10 candles, then disappears.
      this.poolState = new Map(); // key → { swept, sweptAt }
    }

    setVisible(visible) {
      this.visible = visible;
    }

    // Stable key for a pool — rounded to 0.1 pip so minor averaging doesn't break identity
    _key(side, price) {
      return `${side}_${Math.round(price * 10)}`;
    }

    calculate(store, context = {}) {
      const candles      = (store.M1 || []).slice(-120);
      const currentPrice = context.currentPrice || (store.current.M1 && store.current.M1.close) || (candles.at(-1) && candles.at(-1).close) || 0;
      const currentTime  = context.currentTime  || (store.current.M1 && store.current.M1.time)  || (candles.at(-1) && candles.at(-1).time)  || 0;

      const lows  = this.findPools(candles, 'low');
      const highs = this.findPools(candles, 'high');

      const minBuffer = 2.0;

      // BUG 3 FIX: keep only the 3 STRONGEST pools on each side
      const ssl = lows
        .filter((l) => l.price < currentPrice - minBuffer)
        .sort((a, b) => b.tests - a.tests || b.time - a.time)
        .slice(0, 3);
      const bsl = highs
        .filter((l) => l.price > currentPrice + minBuffer)
        .sort((a, b) => b.tests - a.tests || b.time - a.time)
        .slice(0, 3);

      const levels = [
        ...ssl.map((item) => ({ ...item, side: 'ssl' })),
        ...bsl.map((item) => ({ ...item, side: 'bsl' })),
      ];

      // Restore persistent swept state onto each level
      levels.forEach((level) => {
        const cached = this.poolState.get(this._key(level.side, level.price));
        if (cached) {
          level.swept   = cached.swept;
          level.sweptAt = cached.sweptAt;
        } else {
          level.swept   = false;
          level.sweptAt = null;
        }
      });

      // Remove levels swept more than 10 M1 candles (~600 s) ago
      const EXPIRE_SEC = 10 * 60;
      const activeLevels = levels.filter((level) => {
        if (level.swept && level.sweptAt && currentTime > 0) {
          return (currentTime - level.sweptAt) <= EXPIRE_SEC;
        }
        return true;
      });

      const markers = [];
      let latestBullSweep = null;
      let latestBearSweep = null;

      activeLevels.forEach((level) => {
        if (level.swept) {
          // Already swept — emit the stored marker (no re-detection)
          if (level.sweptAt) {
            markers.push({
              time:     level.sweptAt,
              position: level.side === 'ssl' ? 'belowBar'  : 'aboveBar',
              color:    level.side === 'ssl' ? '#ef5350'   : '#26a69a',
              shape:    level.side === 'ssl' ? 'arrowUp'   : 'arrowDown',
              text:     this.config.MARKER_SWEEP || 'Sweep',
              size: 1.5,
            });
            level.sweep = { candle: { time: level.sweptAt }, depth: 0, candleIndex: -1 };
            if (level.side === 'ssl' && (!latestBullSweep || level.sweptAt > latestBullSweep.candle.time)) {
              latestBullSweep = level.sweep;
            }
            if (level.side === 'bsl' && (!latestBearSweep || level.sweptAt > latestBearSweep.candle.time)) {
              latestBearSweep = level.sweep;
            }
          }
        } else {
          // BUG 3 FIX: detectSweep() now checks level.swept first — won't fire twice
          const sweep = this.detectSweep(candles, level);
          if (sweep.swept) {
            level.swept   = true;
            level.sweptAt = sweep.candle.time;
            level.sweep   = sweep;

            // Persist so future recalcs know this pool is already swept
            this.poolState.set(this._key(level.side, level.price), {
              swept: true, sweptAt: sweep.candle.time,
            });

            markers.push({
              time:     sweep.candle.time,
              position: level.side === 'ssl' ? 'belowBar'  : 'aboveBar',
              color:    level.side === 'ssl' ? '#ef5350'   : '#26a69a',
              shape:    level.side === 'ssl' ? 'arrowUp'   : 'arrowDown',
              text:     this.config.MARKER_SWEEP || 'Sweep',
              size: 1.5,
            });
            if (level.side === 'ssl' && (!latestBullSweep || sweep.candle.time > latestBullSweep.candle.time)) latestBullSweep = sweep;
            if (level.side === 'bsl' && (!latestBearSweep || sweep.candle.time > latestBearSweep.candle.time)) latestBearSweep = sweep;
          }
        }
      });

      // Prune expired entries from poolState to avoid memory growth
      for (const [key, cached] of this.poolState.entries()) {
        if (cached.swept && currentTime > 0 && cached.sweptAt) {
          if ((currentTime - cached.sweptAt) > EXPIRE_SEC + 60) {
            this.poolState.delete(key);
          }
        }
      }

      this.data = {
        levels:          activeLevels.slice(0, this.config.LIQ_MAX_LEVELS || 6),
        markers,
        latestBullSweep,
        latestBearSweep,
      };
      context.liquidity = this.data;
      return this.data;
    }

    findPools(candles, side) {
      const tolerance = this.config.EQUAL_LEVEL_TOLERANCE || 0.2;
      const groups = [];
      candles.forEach((candle, index) => {
        const price    = side === 'low' ? candle.low : candle.high;
        const existing = groups.find((g) => Math.abs(g.price - price) <= tolerance);
        if (existing) {
          existing.tests += 1;
          existing.indices.push(index);
          existing.time  = candle.time;
          existing.price = (existing.price + price) / 2;
        } else {
          groups.push({ price, time: candle.time, tests: 1, indices: [index], kind: side === 'low' ? 'SSL' : 'BSL' });
        }
      });

      // BUG 3 FIX: merge pivot points into groups instead of creating separate pools,
      // which was the root cause of duplicate pools near the same price level.
      for (let i = 2; i < candles.length - 2; i += 1) {
        const value = side === 'low' ? candles[i].low : candles[i].high;
        const left  = candles.slice(i - 2, i);
        const right = candles.slice(i + 1, i + 3);
        const isPivot = side === 'low'
          ? [...left, ...right].every((c) => value < c.low)
          : [...left, ...right].every((c) => value > c.high);
        if (isPivot) {
          // Merge into existing group if within tolerance, otherwise add new
          const near = groups.find((g) => Math.abs(g.price - value) <= tolerance);
          if (near) {
            near.tests += 1;
          } else {
            groups.push({ price: value, time: candles[i].time, tests: 1, indices: [i], kind: side === 'low' ? 'SSL' : 'BSL' });
          }
        }
      }

      return groups
        .filter((g) => g.tests >= 2)
        .sort((a, b) => b.tests - a.tests || b.time - a.time);
    }

    detectSweep(candles, level) {
      // BUG 3 FIX: guard — already swept pools must never trigger another marker
      if (level.swept === true) {
        return { swept: false, reason: 'already swept' };
      }
      const pipSize = this.config.PIP_SIZE || 0.1;
      for (let i = 0; i < candles.length; i += 1) {
        const candle = candles[i];
        if (level.kind === 'SSL') {
          const depth = (level.price - candle.low) / pipSize;
          if (
            candle.low   < level.price &&
            candle.close > level.price &&
            depth >= (this.config.SWEEP_MIN_PIPS || 1) &&
            depth <= (this.config.SWEEP_MAX_PIPS || 15)
          ) {
            return { swept: true, candle, depth: Number(depth.toFixed(1)), candleIndex: i };
          }
        } else {
          const depth = (candle.high - level.price) / pipSize;
          if (
            candle.high  > level.price &&
            candle.close < level.price &&
            depth >= (this.config.SWEEP_MIN_PIPS || 1) &&
            depth <= (this.config.SWEEP_MAX_PIPS || 15)
          ) {
            return { swept: true, candle, depth: Number(depth.toFixed(1)), candleIndex: i };
          }
        }
      }
      return { swept: false };
    }

    getMarkers() {
      return this.visible ? this.data.markers : [];
    }

    draw(ctx, helpers) {
      if (!this.visible) return;
      const Line = global.SEANDashedLinePrimitive;
      (this.data.levels || []).forEach((level) => {
        new Line({
          startTime: level.time,
          endTime:   helpers.currentTime,
          price:     level.price,
          // Swept pools fade to 20% opacity (visible but clearly used)
          color: level.side === 'ssl'
            ? (level.swept ? 'rgba(239,83,80,0.20)' : (this.config.SSL_COLOR || 'rgba(239,83,80,0.6)'))
            : (level.swept ? 'rgba(38,166,154,0.20)' : (this.config.BSL_COLOR || 'rgba(38,166,154,0.6)')),
          dash:      [4, 4],
          lineWidth: level.tests >= 3 ? 1.1 : 0.8,
          label:     `${level.kind}${level.swept ? ' ✓' : ''}`,
        }).draw(ctx, helpers);
      });
    }
  }

  global.SEANLiquidityOverlay = LiquidityOverlay;
})(window);
