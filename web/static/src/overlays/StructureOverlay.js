(function attachStructureOverlay(global) {
  class StructureOverlay {
    constructor(chart, series, config = {}) {
      this.chart = chart;
      this.series = series;
      this.config = config;
      this.visible = true;
      this.data = { markers: [], lines: [] };
    }

    setVisible(visible) {
      this.visible = visible;
    }

    calculate(store, context = {}) {
      const candles = (store.M15 || []).slice(-120);
      const pivotBars = this.config.SWING_PIVOT_BARS || 3;
      const swingHighs = [];
      const swingLows = [];

      for (let i = pivotBars; i < candles.length - pivotBars; i += 1) {
        const current = candles[i];
        const left = candles.slice(i - pivotBars, i);
        const right = candles.slice(i + 1, i + pivotBars + 1);
        if (left.every((c) => current.high > c.high) && right.every((c) => current.high > c.high)) {
          swingHighs.push({ price: current.high, time: current.time, index: i });
        }
        if (left.every((c) => current.low < c.low) && right.every((c) => current.low < c.low)) {
          swingLows.push({ price: current.low, time: current.time, index: i });
        }
      }

      const markers = [];
      const addMarkers = (swings, bullishLabel, bearishLabel, position, shape) => {
        let previous = null;
        swings.forEach((swing) => {
          let text = bullishLabel;
          let color = '#26a69a';
          if (previous) {
            const isBullish = swing.price > previous.price;
            text = isBullish ? bullishLabel : bearishLabel;
            color = isBullish ? '#26a69a' : '#ef5350';
          }
          markers.push({
            time: swing.time,
            position,
            color,
            shape,
            text,
            size: 1,
          });
          previous = swing;
        });
      };

      addMarkers(swingHighs, this.config.MARKER_HH || 'HH', this.config.MARKER_LH || 'LH', 'aboveBar', 'arrowDown');
      addMarkers(swingLows, this.config.MARKER_HL || 'HL', this.config.MARKER_LL || 'LL', 'belowBar', 'arrowUp');

      const lines = [];
      let lastBreakDirection = null;
      const brokenHighs = new Set();
      const brokenLows = new Set();

      candles.forEach((candle) => {
        const priorHighs = swingHighs.filter((swing) => swing.time < candle.time);
        const priorLows = swingLows.filter((swing) => swing.time < candle.time);
        const lastHigh = priorHighs[priorHighs.length - 1];
        const lastLow = priorLows[priorLows.length - 1];

        if (lastHigh && candle.close > lastHigh.price && !brokenHighs.has(lastHigh.time)) {
          const label = lastBreakDirection && !String(lastBreakDirection).startsWith('bullish')
            ? (this.config.MARKER_CHOCH || 'CHoCH')
            : (this.config.MARKER_BOS || 'BOS');
          lines.push({
            startTime: lastHigh.time,
            endTime: candle.time,
            price: lastHigh.price,
            color: label === (this.config.MARKER_CHOCH || 'CHoCH') ? '#f59e0b' : '#26a69a',
            dash: label === (this.config.MARKER_CHOCH || 'CHoCH') ? [5, 4] : [],
            label,
          });
          brokenHighs.add(lastHigh.time);
          lastBreakDirection = label === (this.config.MARKER_CHOCH || 'CHoCH') ? 'bullish_reversal' : 'bullish';
        }

        if (lastLow && candle.close < lastLow.price && !brokenLows.has(lastLow.time)) {
          const label = lastBreakDirection && !String(lastBreakDirection).startsWith('bearish')
            ? (this.config.MARKER_CHOCH || 'CHoCH')
            : (this.config.MARKER_BOS || 'BOS');
          lines.push({
            startTime: lastLow.time,
            endTime: candle.time,
            price: lastLow.price,
            color: label === (this.config.MARKER_CHOCH || 'CHoCH') ? '#f59e0b' : '#ef5350',
            dash: label === (this.config.MARKER_CHOCH || 'CHoCH') ? [5, 4] : [],
            label,
          });
          brokenLows.add(lastLow.time);
          lastBreakDirection = label === (this.config.MARKER_CHOCH || 'CHoCH') ? 'bearish_reversal' : 'bearish';
        }
      });

      this.data = {
        markers: markers.slice(-(this.config.MAX_SWING_MARKERS || 30)),
        lines: lines.slice(-12),
        // BUG 2 FIX: expose last confirmed structure direction for ICTEntryEngine
        lastBias: lastBreakDirection,
      };
      context.structure = this.data;
      return this.data;
    }

    getMarkers() {
      return this.visible ? this.data.markers : [];
    }

    draw(ctx, helpers) {
      if (!this.visible) return;
      const Line = global.SEANDashedLinePrimitive;
      (this.data.lines || []).forEach((line) => {
        new Line(line).draw(ctx, helpers);
      });
    }
  }

  global.SEANStructureOverlay = StructureOverlay;
})(window);
