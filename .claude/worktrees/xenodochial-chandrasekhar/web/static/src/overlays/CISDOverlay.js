(function attachCISDOverlay(global) {
  class CISDOverlay {
    constructor(chart, series, config = {}) {
      this.chart = chart;
      this.series = series;
      this.config = config;
      this.visible = true;
      this.data = { markers: [], items: [] };
    }

    setVisible(visible) {
      this.visible = visible;
    }

    calculate(store, context = {}) {
      const candles = (store.M1 || []).slice(-120);
      const bias = context.bias || null;
      const liquidity = context.liquidity || {};
      const displacement = context.displacement || {};
      const sweep = bias === 'bearish' ? liquidity.latestBearSweep : liquidity.latestBullSweep;
      const items = [];

      if (sweep && candles.length) {
        const sweepIndex = sweep.candleIndex;
        if (bias === 'bullish') {
          const window = candles.slice(Math.max(0, sweepIndex - 5), sweepIndex);
          if (window.length) {
            const microHigh = window.reduce((best, candle) => candle.high > best.high ? candle : best, window[0]);
            for (let i = sweepIndex + 1; i < Math.min(candles.length, sweepIndex + 6); i += 1) {
              if (candles[i].close > microHigh.high) {
                items.push({ candle: candles[i], flippedLevel: microHigh.high, type: 'bullish' });
                break;
              }
            }
          }
        } else if (bias === 'bearish') {
          const window = candles.slice(Math.max(0, sweepIndex - 5), sweepIndex);
          if (window.length) {
            const microLow = window.reduce((best, candle) => candle.low < best.low ? candle : best, window[0]);
            for (let i = sweepIndex + 1; i < Math.min(candles.length, sweepIndex + 6); i += 1) {
              if (candles[i].close < microLow.low) {
                items.push({ candle: candles[i], flippedLevel: microLow.low, type: 'bearish' });
                break;
              }
            }
          }
        }
      }

      if (!items.length && displacement.displacements && displacement.displacements.length) {
        const item = displacement.displacements[0];
        items.push({
          candle: item.candle,
          flippedLevel: item.type === 'bullish' ? item.candle.high : item.candle.low,
          type: item.type,
        });
      }

      const markers = items.map((item) => ({
        time: item.candle.time,
        position: item.type === 'bullish' ? 'aboveBar' : 'belowBar',
        color: '#7b1fa2',
        shape: 'square',
        text: this.config.MARKER_CISD || 'CISD',
        size: 1.4,
      }));

      this.data = { items: items.slice(0, 2), markers };
      context.cisd = this.data;
      return this.data;
    }

    getMarkers() {
      return this.visible ? this.data.markers : [];
    }

    draw(ctx, helpers) {
      if (!this.visible) return;
      const Line = global.SEANDashedLinePrimitive;
      (this.data.items || []).forEach((item) => {
        const x = helpers.timeToX(item.candle.time);
        if (Number.isFinite(x)) {
          ctx.save();
          ctx.strokeStyle = 'rgba(123,31,162,0.6)';
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 4]);
          ctx.beginPath();
          ctx.moveTo(x, 0);
          ctx.lineTo(x, helpers.canvasHeight);
          ctx.stroke();
          ctx.restore();
        }
        new Line({
          startTime: item.candle.time,
          endTime: helpers.currentTime,
          price: item.flippedLevel,
          color: 'rgba(123,31,162,0.55)',
          dash: [2, 4],
          lineWidth: 1,
          label: 'CISD',
        }).draw(ctx, helpers);
      });
    }
  }

  global.SEANCISDOverlay = CISDOverlay;
})(window);
