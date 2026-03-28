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
      // BUG 1 FIX: include live candle price in fill depth check
      const currentPrice = context.currentPrice || (store.current.M1 && store.current.M1.close) || 0;

      if (!candles.length || !currentTime) {
        this.data = { zones: [] };
        return this.data;
      }

      // Colour constants — full opacity and faded (50–75% fill)
      const bullFill    = this.config.FVG_BULL_FILL   || 'rgba(38,166,154,0.12)';
      const bearFill    = this.config.FVG_BEAR_FILL   || 'rgba(239,83,80,0.12)';
      const bullStroke  = this.config.FVG_BULL_STROKE || 'rgba(38,166,154,0.5)';
      const bearStroke  = this.config.FVG_BEAR_STROKE || 'rgba(239,83,80,0.5)';
      const bullFillMit   = 'rgba(38,166,154,0.03)';
      const bearFillMit   = 'rgba(239,83,80,0.03)';
      const bullStrokeMit = 'rgba(38,166,154,0.12)';
      const bearStrokeMit = 'rgba(239,83,80,0.12)';

      for (let i = 1; i < candles.length - 1; i += 1) {
        const prev = candles[i - 1];
        const mid  = candles[i];
        const next = candles[i + 1];
        let type = null;
        let low  = 0;
        let high = 0;

        const bullishGap = next.low  - prev.high;
        const bearishGap = prev.low  - next.high;

        if (bullishGap >= (this.config.FVG_MIN_SIZE || 0.3) && mid.close > mid.open) {
          type = 'bullish';
          low  = prev.high;
          high = next.low;
        } else if (bearishGap >= (this.config.FVG_MIN_SIZE || 0.3) && mid.close < mid.open) {
          type = 'bearish';
          low  = next.high;
          high = prev.low;
        } else {
          continue;
        }

        const future = candles.slice(i + 2);
        const size   = Math.max(0.0001, high - low);
        let fillDepth = 0;

        // Check fill from every closed candle after the FVG formed
        future.forEach((candle) => {
          if (type === 'bullish' && candle.low < high) {
            fillDepth = Math.max(fillDepth, high - Math.max(candle.low, low));
          }
          if (type === 'bearish' && candle.high > low) {
            fillDepth = Math.max(fillDepth, Math.min(candle.high, high) - low);
          }
        });

        // BUG 1 FIX: also measure current live-candle close against the zone
        if (currentPrice > 0) {
          if (type === 'bullish' && currentPrice < high) {
            fillDepth = Math.max(fillDepth, high - Math.max(currentPrice, low));
          }
          if (type === 'bearish' && currentPrice > low) {
            fillDepth = Math.max(fillDepth, Math.min(currentPrice, high) - low);
          }
        }

        const mitPct = Math.max(0, Math.min(100, (fillDepth / size) * 100));

        // ≥ 75% filled → remove completely (skip this zone)
        if (mitPct >= 75) continue;

        // 50–74% filled → show faded ("mitigated but not yet removed")
        const isMitigated = mitPct >= 50;

        zones.push({
          type,
          low,
          high,
          formedIndex: i,
          midpoint: (low + high) / 2,
          // No label on faded zones — they are about to disappear
          label: isMitigated ? '' : 'FVG',
          fill:   type === 'bullish' ? (isMitigated ? bullFillMit   : bullFill)   : (isMitigated ? bearFillMit   : bearFill),
          stroke: type === 'bullish' ? (isMitigated ? bullStrokeMit : bullStroke) : (isMitigated ? bearStrokeMit : bearStroke),
          formed:    mid.time,
          startTime: prev.time,
          endTime:   currentTime,
          mitigated: isMitigated,
          mitPct,
        });
      }

      // Remove FVGs older than 50 M5 candles
      const maxAge  = 50;
      const lastIdx = candles.length - 1;
      const fresh   = zones.filter((z) => (lastIdx - z.formedIndex) < maxAge);

      // Newest first; cap at FVG_MAX_DISPLAY (5)
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
          endTime:   zone.endTime,
          high:      zone.high,
          low:       zone.low,
          fill:      zone.fill,
          stroke:    zone.stroke,
          lineWidth: zone.mitigated ? 0.5 : 1,
          label:     zone.label,
          labelColor: zone.stroke,
          // Hide midpoint dashes on faded zones
          midpoint:      zone.mitigated ? null : zone.midpoint,
          midpointColor: zone.stroke,
          midpointDash:  this.config.FVG_MID_DASH || [4, 3],
        }).draw(ctx, helpers);
      });
    }
  }

  global.SEANFVGOverlay = FVGOverlay;
})(window);
