(function attachDisplacementOverlay(global) {
  class DisplacementOverlay {
    constructor(chart, series, config = {}) {
      this.chart = chart;
      this.series = series;
      this.config = config;
      this.visible = true;
      this.data = { markers: [], displacements: [] };
      this._lastDispIndex = -999; // cooldown tracker for double D markers
    }

    setVisible(visible) {
      this.visible = visible;
    }

    calculate(store, context = {}) {
      const candles = (store.M1 || []).slice(-120);
      const bias = context.bias || null;
      const displacements = [];

      for (let i = 21; i < candles.length - 1; i += 1) {
        const avgRange = candles.slice(i - 20, i).reduce((sum, candle) => sum + (candle.high - candle.low), 0) / 20;
        const candle = candles[i];
        const range = Math.max(0.0001, candle.high - candle.low);
        const body = Math.abs(candle.close - candle.open);
        const bodyPct = body / range;
        const bullishClosePct = (candle.close - candle.low) / range;
        const bearishClosePct = (candle.high - candle.close) / range;
        const isBull = candle.close > candle.open;
        const isBear = candle.close < candle.open;

        const bullValid = isBull
          && bodyPct >= (this.config.DISP_BODY_PCT || 0.75)
          && bullishClosePct >= (this.config.DISP_CLOSE_PCT || 0.85)
          && range >= (this.config.DISP_MIN_SIZE || 1)
          && range >= avgRange * (this.config.DISP_RANGE_MULTIPLIER || 2);
        const bearValid = isBear
          && bodyPct >= (this.config.DISP_BODY_PCT || 0.75)
          && bearishClosePct >= (this.config.DISP_CLOSE_PCT || 0.85)
          && range >= (this.config.DISP_MIN_SIZE || 1)
          && range >= avgRange * (this.config.DISP_RANGE_MULTIPLIER || 2);
        if (!bullValid && !bearValid) continue;

        // FIX: cooldown — no two D markers within 5 candles of each other
        const DISP_COOLDOWN = 5;
        if (displacements.length > 0) {
          const lastIdx = displacements[displacements.length - 1].candleIndex;
          if (i - lastIdx < DISP_COOLDOWN) continue;
        }

        const type = bullValid ? 'bullish' : 'bearish';
        if (bias && bias !== type) continue;
        const prev = candles[i - 1];
        const next = candles[i + 1];
        const fvgLow = type === 'bullish' ? prev.high : next.high;
        const fvgHigh = type === 'bullish' ? next.low : prev.low;
        if (fvgHigh <= fvgLow || (fvgHigh - fvgLow) < (this.config.FVG_MIN_SIZE || 0.3)) continue;

        displacements.push({
          type,
          candle,
          candleIndex: i,
          bodyPct,
          rangeVsAvg: range / Math.max(avgRange, 0.0001),
          fvg: {
            low: fvgLow,
            high: fvgHigh,
            midpoint: (fvgLow + fvgHigh) / 2,
            formed: candle.time,
          },
        });
      }

      displacements.sort((a, b) => b.candle.time - a.candle.time);
      const active = displacements.slice(0, 2);
      const currentPrice = context.currentPrice || (store.current.M1 && store.current.M1.close) || (store.M1.at(-1) && store.M1.at(-1).close) || 0;
      const currentTime = context.currentTime || (store.current.M1 && store.current.M1.time) || (store.M1.at(-1) && store.M1.at(-1).time);
      const markers = active.flatMap((item) => {
        const marker = {
          time: item.candle.time,
          position: item.type === 'bullish' ? 'belowBar' : 'aboveBar',
          color: item.type === 'bullish' ? '#00E676' : '#ff8a65',
          shape: 'square',
          text: this.config.MARKER_DISP || 'D',
          size: 1.2,
        };
        const inEntry = currentPrice >= item.fvg.low && currentPrice <= item.fvg.high && currentTime;
        return inEntry
          ? [marker, {
              time: currentTime,
              position: item.type === 'bullish' ? 'belowBar' : 'aboveBar',
              color: '#00E676',
              shape: item.type === 'bullish' ? 'arrowUp' : 'arrowDown',
              text: 'Entry',
              size: 2,
            }]
          : [marker];
      });

      this.data = { markers, displacements: active };
      context.displacement = this.data;
      return this.data;
    }

    getMarkers() {
      return this.visible ? this.data.markers : [];
    }

    draw(ctx, helpers) {
      if (!this.visible) return;
      const Rect = global.SEANRectanglePrimitive;
      (this.data.displacements || []).forEach((item) => {
        const candleWidth = Math.max(8, helpers.barSpacing * 0.9);
        const x = helpers.timeToX(item.candle.time);
        const y1 = helpers.clampY(helpers.priceToY(item.candle.high));
        const y2 = helpers.clampY(helpers.priceToY(item.candle.low));
        if ([x, y1, y2].every(Number.isFinite)) {
          ctx.save();
          ctx.strokeStyle = item.type === 'bullish' ? '#00E676' : '#ff8a65';
          ctx.lineWidth = 1.25;
          ctx.strokeRect(x - candleWidth / 2, Math.min(y1, y2), candleWidth, Math.max(1, Math.abs(y2 - y1)));
          ctx.restore();
        }

        new Rect({
          startTime: item.fvg.formed,
          endTime: helpers.currentTime,
          high: item.fvg.high,
          low: item.fvg.low,
          fill: this.config.ENTRY_FVG_FILL || 'rgba(0,230,118,0.15)',
          stroke: this.config.ENTRY_FVG_STROKE || '#00E676',
          lineWidth: 1.1,
          label: 'ENTRY',
          midpoint: item.fvg.midpoint,
          midpointColor: this.config.ENTRY_FVG_STROKE || '#00E676',
          midpointDash: [4, 3],
        }).draw(ctx, helpers);
      });
    }
  }

  global.SEANDisplacementOverlay = DisplacementOverlay;
})(window);
