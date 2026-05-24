(function attachLiquidityOverlay(global) {
  class LiquidityOverlay {
    constructor(chart, series, config = {}) {
      this.chart = chart;
      this.series = series;
      this.config = config;
      this.visible = true;
      this.data = { levels: [], markers: [], latestBullSweep: null, latestBearSweep: null };
    }

    setVisible(visible) {
      this.visible = visible;
    }

    calculate(store, context = {}) {
      const candles = (store.M1 || []).slice(-120);
      const currentPrice = context.currentPrice || (store.current.M1 && store.current.M1.close) || (candles.at(-1) && candles.at(-1).close) || 0;
      const lows = this.findPools(candles, 'low');
      const highs = this.findPools(candles, 'high');
      const minBuffer = 2.0;
      const ssl = lows.filter((level) => level.price < currentPrice - minBuffer).sort((a, b) => b.price - a.price).slice(0, 3);
      const bsl = highs.filter((level) => level.price > currentPrice + minBuffer).sort((a, b) => a.price - b.price).slice(0, 3);

      const levels = [...ssl.map((item) => ({ ...item, side: 'ssl' })), ...bsl.map((item) => ({ ...item, side: 'bsl' }))];
      const markers = [];
      let latestBullSweep = null;
      let latestBearSweep = null;

      levels.forEach((level) => {
        const sweep = this.detectSweep(candles, level);
        if (sweep.swept) {
          markers.push({
            time: sweep.candle.time,
            position: level.side === 'ssl' ? 'belowBar' : 'aboveBar',
            color: level.side === 'ssl' ? '#ef5350' : '#26a69a',
            shape: level.side === 'ssl' ? 'arrowUp' : 'arrowDown',
            text: this.config.MARKER_SWEEP || 'Sweep',
            size: 1.5,
          });
          level.swept = true;
          level.sweep = sweep;
          if (level.side === 'ssl' && (!latestBullSweep || sweep.candle.time > latestBullSweep.candle.time)) latestBullSweep = sweep;
          if (level.side === 'bsl' && (!latestBearSweep || sweep.candle.time > latestBearSweep.candle.time)) latestBearSweep = sweep;
        }
      });

      this.data = { levels: levels.slice(0, this.config.LIQ_MAX_LEVELS || 6), markers, latestBullSweep, latestBearSweep };
      context.liquidity = this.data;
      return this.data;
    }

    findPools(candles, side) {
      const tolerance = this.config.EQUAL_LEVEL_TOLERANCE || 0.2;
      const groups = [];
      candles.forEach((candle, index) => {
        const price = side === 'low' ? candle.low : candle.high;
        const existing = groups.find((group) => Math.abs(group.price - price) <= tolerance);
        if (existing) {
          existing.tests += 1;
          existing.indices.push(index);
          existing.time = candle.time;
          existing.price = (existing.price + price) / 2;
        } else {
          groups.push({ price, time: candle.time, tests: 1, indices: [index], kind: side === 'low' ? 'SSL' : 'BSL' });
        }
      });

      const pivots = [];
      for (let i = 2; i < candles.length - 2; i += 1) {
        const value = side === 'low' ? candles[i].low : candles[i].high;
        const left = candles.slice(i - 2, i);
        const right = candles.slice(i + 1, i + 3);
        const isPivot = side === 'low'
          ? [...left, ...right].every((candle) => value < candle.low)
          : [...left, ...right].every((candle) => value > candle.high);
        if (isPivot) pivots.push({ price: value, time: candles[i].time, tests: 1, indices: [i], kind: side === 'low' ? 'SSL' : 'BSL' });
      }

      return [...groups.filter((group) => group.tests >= 2), ...pivots]
        .sort((a, b) => b.tests - a.tests || b.time - a.time);
    }

    detectSweep(candles, level) {
      const pipSize = this.config.PIP_SIZE || 0.1;
      for (let i = 0; i < candles.length; i += 1) {
        const candle = candles[i];
        if (level.kind === 'SSL') {
          const depth = (level.price - candle.low) / pipSize;
          if (candle.low < level.price && candle.close > level.price && depth >= (this.config.SWEEP_MIN_PIPS || 1) && depth <= (this.config.SWEEP_MAX_PIPS || 15)) {
            return { swept: true, candle, depth: Number(depth.toFixed(1)), candleIndex: i };
          }
        } else {
          const depth = (candle.high - level.price) / pipSize;
          if (candle.high > level.price && candle.close < level.price && depth >= (this.config.SWEEP_MIN_PIPS || 1) && depth <= (this.config.SWEEP_MAX_PIPS || 15)) {
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
          endTime: helpers.currentTime,
          price: level.price,
          color: level.side === 'ssl'
            ? (level.swept ? 'rgba(239,83,80,0.25)' : (this.config.SSL_COLOR || 'rgba(239,83,80,0.6)'))
            : (level.swept ? 'rgba(38,166,154,0.25)' : (this.config.BSL_COLOR || 'rgba(38,166,154,0.6)')),
          dash: [4, 4],
          lineWidth: level.tests >= 3 ? 1.1 : 0.8,
          label: `${level.kind}${level.swept ? ' ✓' : ''}`,
        }).draw(ctx, helpers);
      });
    }
  }

  global.SEANLiquidityOverlay = LiquidityOverlay;
})(window);
