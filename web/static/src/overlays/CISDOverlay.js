(function attachCISDOverlay(global) {
  class CISDOverlay {
    constructor(chart, series, config = {}) {
      this.chart = chart;
      this.series = series;
      this.config = config;
      this.visible = true;
      this.data = { markers: [], items: [] };
      // Track last CISD to prevent duplicates
      this._lastCISDTime = 0;
    }

    setVisible(visible) {
      this.visible = visible;
    }

    calculate(store, context = {}) {
      const candles = (store.M1 || []).slice(-120);
      const bias = context.bias || null;
      const liquidity = context.liquidity || {};
      const displacement = context.displacement || {};
      const items = [];

      // ── STRICT SEQUENCE: Sweep → Displacement → CISD ──────
      // CISD only fires when BOTH a sweep AND displacement exist
      // in the correct chronological order.
      const sweep = bias === 'bearish' ? liquidity.latestBearSweep : liquidity.latestBullSweep;
      const hasDisplacement = displacement.displacements && displacement.displacements.length > 0;

      if (sweep && hasDisplacement && candles.length) {
        const sweepTime = sweep.candle ? sweep.candle.time : 0;
        const dispTime = displacement.displacements[0].candle ? displacement.displacements[0].candle.time : 0;

        // Displacement must happen AFTER the sweep (correct sequence)
        if (dispTime >= sweepTime) {
          const sweepIndex = sweep.candleIndex;
          if (bias === 'bullish') {
            const window = candles.slice(Math.max(0, sweepIndex - 5), sweepIndex);
            if (window.length) {
              const microHigh = window.reduce((best, candle) => candle.high > best.high ? candle : best, window[0]);
              for (let i = sweepIndex + 1; i < Math.min(candles.length, sweepIndex + 6); i += 1) {
                if (candles[i].close > microHigh.high) {
                  // Cooldown: don't fire another CISD within 5 candles of the last one
                  if (candles[i].time - this._lastCISDTime < 300) break; // 5 min cooldown
                  this._lastCISDTime = candles[i].time;
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
                  if (candles[i].time - this._lastCISDTime < 300) break;
                  this._lastCISDTime = candles[i].time;
                  items.push({ candle: candles[i], flippedLevel: microLow.low, type: 'bearish' });
                  break;
                }
              }
            }
          }
        }
      }

      // NO FALLBACK to displacement-only CISD — removed the independent firing.
      // CISD must always be part of Sweep → Displacement → CISD sequence.

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
