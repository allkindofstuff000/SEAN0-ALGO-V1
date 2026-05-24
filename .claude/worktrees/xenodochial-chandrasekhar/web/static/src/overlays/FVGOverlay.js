(function attachFVGOverlay(global) {
  class FVGOverlay {
    constructor(chart, series, config = {}) {
      this.chart = chart;
      this.series = series;
      this.config = config;
      this.visible = true;
      this.data = { zones: [] };
    }

    setVisible(visible) {
      this.visible = visible;
    }

    calculate(store, context = {}) {
      const candles = (store.M5 || []).slice(-90);
      const zones = [];
      const currentTime = context.currentTime || (store.current.M1 && store.current.M1.time) || (store.M1.at(-1) && store.M1.at(-1).time);
      if (!candles.length || !currentTime) {
        this.data = { zones: [] };
        return this.data;
      }

      for (let i = 1; i < candles.length - 1; i += 1) {
        const prev = candles[i - 1];
        const mid = candles[i];
        const next = candles[i + 1];
        let type = null;
        let low = 0;
        let high = 0;

        const bullishGap = next.low - prev.high;
        const bearishGap = prev.low - next.high;
        if (bullishGap >= (this.config.FVG_MIN_SIZE || 0.3) && mid.close > mid.open) {
          type = 'bullish';
          low = prev.high;
          high = next.low;
        } else if (bearishGap >= (this.config.FVG_MIN_SIZE || 0.3) && mid.close < mid.open) {
          type = 'bearish';
          low = next.high;
          high = prev.low;
        } else {
          continue;
        }

        const future = candles.slice(i + 2);
        const size = Math.max(0.0001, high - low);
        let fillDepth = 0;
        future.forEach((candle) => {
          if (type === 'bullish' && candle.low < high) {
            fillDepth = Math.max(fillDepth, high - Math.max(candle.low, low));
          }
          if (type === 'bearish' && candle.high > low) {
            fillDepth = Math.max(fillDepth, Math.min(candle.high, high) - low);
          }
        });
        const mitPct = Math.max(0, Math.min(100, (fillDepth / size) * 100));
          const agePct = type === 'bullish'
          ? (fillDepth / size)
          : (fillDepth / size);
        // Skip if >75% filled (aggressive mitigation)
        if (mitPct >= 75) continue;

        zones.push({
          type,
          low,
          high,
          formedIndex: i,
          midpoint: (low + high) / 2,
          label: 'FVG',
          fill: type === 'bullish' ? this.config.FVG_BULL_FILL : this.config.FVG_BEAR_FILL,
          stroke: type === 'bullish' ? this.config.FVG_BULL_STROKE : this.config.FVG_BEAR_STROKE,
          formed: mid.time,
          startTime: prev.time,
          endTime: currentTime,
          mitigated: false,
          mitPct,
        });
      }

      const maxAge = 50;
      const lastIdx = candles.length - 1;
      // Remove FVGs older than 50 candles
      const fresh = zones.filter(z => (lastIdx - z.formedIndex) < maxAge);

      fresh.sort((a, b) => b.formed - a.formed);
      this.data = { zones: fresh.slice(0, this.config.FVG_MAX_DISPLAY || 5) };
      context.fvg = this.data;
      return this.data;
    }

    getMarkers() {
      return [];
    }

    draw(ctx, helpers) {
      if (!this.visible) return;
      const Rect = global.SEANRectanglePrimitive;
      (this.data.zones || []).forEach((zone) => {
        new Rect({
          startTime: zone.startTime,
          endTime: zone.endTime,
          high: zone.high,
          low: zone.low,
          fill: zone.fill,
          stroke: zone.stroke,
          lineWidth: zone.mitigated ? 0.75 : 1,
          label: zone.label,
          labelColor: zone.stroke,
          midpoint: zone.midpoint,
          midpointColor: zone.stroke,
          midpointDash: this.config.FVG_MID_DASH || [4, 3],
        }).draw(ctx, helpers);
      });
    }
  }

  global.SEANFVGOverlay = FVGOverlay;
})(window);
