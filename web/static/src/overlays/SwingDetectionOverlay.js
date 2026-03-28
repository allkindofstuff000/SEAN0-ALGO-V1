(function attachSwingDetectionOverlay(global) {
  /**
   * SwingDetectionOverlay — Real-time Market Swing Detection
   * Draws: swing markers (HH/HL/LH/LL), zigzag lines, range box,
   *        size labels, momentum arrows, and floating info panel.
   */
  class SwingDetectionOverlay {
    constructor(chart, series, config = {}) {
      this.chart = chart;
      this.series = series;
      this.config = config;
      this.visible = false;
      this.data = { markers: [], lines: [], swings: null };
      this._priceLines = [];
    }

    setVisible(visible) {
      this.visible = visible;
      if (!visible) this._clearPriceLines();
    }

    // ── MAIN CALCULATE ────────────────────────────────────
    calculate(store, context = {}) {
      const candles = (store.M5 && store.M5.length > 60 ? store.M5 :
                       store.M15 && store.M15.length > 60 ? store.M15 :
                       store.M1 && store.M1.length > 60 ? store.M1 : []).slice(-200);
      if (candles.length < 20) {
        this.data = { markers: [], lines: [], swings: null };
        return this.data;
      }
      const pivotBars = this.config.SWING_PIVOT_BARS || 5;  // FIX 1: was 3
      const swings = this._detectSwings(candles, pivotBars);
      this._buildVisuals(swings, candles);
      return this.data;
    }

    getMarkers() {
      return this.visible ? this.data.markers : [];
    }

    draw(ctx, helpers) {
      if (!this.visible) return;
      this._clearPriceLines();
      const Line = global.SEANDashedLinePrimitive;
      // Draw zigzag lines
      (this.data.lines || []).forEach(line => {
        if (Line) new Line(line).draw(ctx, helpers);
      });
      // Draw swing size labels
      this._drawSizeLabels(ctx, helpers);
      // Draw range zones
      this._drawRangeZones(ctx, helpers);
      // Draw equilibrium price line
      this._drawEqLine();
    }

    // ── SWING DETECTION ───────────────────────────────────
    _detectSwings(candles, pivotBars) {
      const highs = [];
      const lows = [];
      const currentPrice = candles[candles.length - 1]?.close || 1;
      const minSize = currentPrice * ((this.config.SWING_MIN_SIZE_PCT || 0.25) / 100);

      for (let i = pivotBars; i < candles.length - pivotBars; i++) {
        const c = candles[i];
        const left = candles.slice(i - pivotBars, i);
        const right = candles.slice(i + 1, i + pivotBars + 1);

        if (left.every(x => c.high > x.high) && right.every(x => c.high > x.high)) {
          // FIX 1: Skip micro-swings too close to previous
          const prevHigh = highs.length ? highs[highs.length - 1].price : 0;
          if (highs.length === 0 || Math.abs(c.high - prevHigh) >= minSize) {
            highs.push({ price: c.high, time: c.time, index: i, type: 'high', confirmed: true });
          }
        }
        if (left.every(x => c.low < x.low) && right.every(x => c.low < x.low)) {
          const prevLow = lows.length ? lows[lows.length - 1].price : Infinity;
          if (lows.length === 0 || Math.abs(c.low - prevLow) >= minSize) {
            lows.push({ price: c.low, time: c.time, index: i, type: 'low', confirmed: true });
          }
        }
      }

      // Forming (unconfirmed) swing at the end
      const last = candles.length - 1;
      const recentH = candles.slice(Math.max(0, last - pivotBars), last);
      const recentL = candles.slice(Math.max(0, last - pivotBars), last);
      if (recentH.length && recentH.every(x => candles[last].high > x.high)) {
        highs.push({ price: candles[last].high, time: candles[last].time, index: last, type: 'high', confirmed: false, forming: true });
      }
      if (recentL.length && recentL.every(x => candles[last].low < x.low)) {
        lows.push({ price: candles[last].low, time: candles[last].time, index: last, type: 'low', confirmed: false, forming: true });
      }

      // Classify
      const classifiedHighs = this._classify(highs, 'HH', 'LH', 'SH');
      const classifiedLows = this._classify(lows, 'HL', 'LL', 'SL');

      const all = [...classifiedHighs, ...classifiedLows].sort((a, b) => a.index - b.index);
      const metrics = this._calcMetrics(all, candles);

      return { highs: classifiedHighs, lows: classifiedLows, all, metrics };
    }

    _classify(points, higherLabel, lowerLabel, firstLabel) {
      return points.map((p, i) => {
        if (i === 0) return { ...p, classification: firstLabel };
        const prev = points[i - 1];
        const isHigher = p.price > prev.price;
        return {
          ...p,
          classification: isHigher ? higherLabel : lowerLabel,
          prevPrice: prev.price,
          swingSize: Math.abs(p.price - prev.price),
        };
      });
    }

    _calcMetrics(allSwings, candles) {
      if (allSwings.length < 2) return null;
      const current = candles[candles.length - 1].close;
      const recent = allSwings.slice(-8);
      const recentHigh = Math.max(...recent.map(s => s.price));
      const recentLow = Math.min(...recent.map(s => s.price));
      const range = recentHigh - recentLow || 1;
      const rangePos = ((current - recentLow) / range * 100);

      // Swing sizes
      const sizes = [];
      for (let i = 1; i < allSwings.length; i++) {
        sizes.push(Math.abs(allSwings[i].price - allSwings[i - 1].price));
      }
      const recentSizes = sizes.slice(-4);
      const momentum = recentSizes.length >= 2
        ? recentSizes[recentSizes.length - 1] > recentSizes[recentSizes.length - 2] ? 'expanding' : 'contracting'
        : 'neutral';

      // Trend
      const highs = allSwings.filter(s => s.type === 'high').slice(-3);
      const lows = allSwings.filter(s => s.type === 'low').slice(-3);
      const hTrend = highs.length >= 2 ? (highs.every((h, i) => i === 0 || h.price >= highs[i - 1].price) ? 'bullish' : 'bearish') : 'neutral';
      const lTrend = lows.length >= 2 ? (lows.every((l, i) => i === 0 || l.price >= lows[i - 1].price) ? 'bullish' : 'bearish') : 'neutral';
      const overallTrend = hTrend === lTrend ? hTrend : 'neutral';

      return {
        recentHigh, recentLow, range,
        rangePos: parseFloat(rangePos.toFixed(1)),
        momentum, overallTrend,
        avgSwingSize: sizes.length ? sizes.slice(-6).reduce((s, v) => s + v, 0) / Math.min(sizes.length, 6) : 0,
        lastSwingSize: sizes.length ? sizes[sizes.length - 1] : 0,
      };
    }

    // ── BUILD VISUALS ─────────────────────────────────────
    _buildVisuals(swings, candles) {
      const markers = [];
      const lines = [];

      if (!swings || !swings.all.length) {
        this.data = { markers: [], lines: [], swings: null };
        return;
      }

      // Markers for swing highs
      swings.highs.slice(-20).forEach(h => {
        const color = h.classification === 'HH' ? '#00E676'
          : h.classification === 'LH' ? '#FF6D00' : '#B0BEC5';
        markers.push({
          time: h.time,
          position: 'aboveBar',
          shape: h.forming ? 'circle' : 'arrowDown',
          color: h.confirmed ? color : color + '80',
          size: h.forming ? 1 : 2,
          text: h.forming ? '?' : `${h.classification} ${h.price.toFixed(2)}`,
        });
      });

      // Markers for swing lows
      swings.lows.slice(-20).forEach(l => {
        const color = l.classification === 'HL' ? '#00E676'
          : l.classification === 'LL' ? '#FF1744' : '#B0BEC5';
        markers.push({
          time: l.time,
          position: 'belowBar',
          shape: l.forming ? 'circle' : 'arrowUp',
          color: l.confirmed ? color : color + '80',
          size: l.forming ? 1 : 2,
          text: l.forming ? '?' : `${l.classification} ${l.price.toFixed(2)}`,
        });
      });

      // Momentum marker on last swing
      if (swings.metrics && swings.all.length) {
        const lastSwing = swings.all[swings.all.length - 1];
        if (swings.metrics.momentum === 'expanding') {
          const c = swings.metrics.overallTrend === 'bullish' ? '#00E676' : '#FF1744';
          markers.push({
            time: lastSwing.time,
            position: lastSwing.type === 'high' ? 'aboveBar' : 'belowBar',
            shape: 'square', color: c, size: 1, text: 'EXP',
          });
        } else if (swings.metrics.momentum === 'contracting') {
          markers.push({
            time: lastSwing.time,
            position: lastSwing.type === 'high' ? 'aboveBar' : 'belowBar',
            shape: 'square', color: '#FFC107', size: 1, text: 'CON',
          });
        }
      }

      // Zigzag lines connecting swings
      const points = swings.all.slice(-16);
      for (let i = 1; i < points.length; i++) {
        const from = points[i - 1];
        const to = points[i];
        const goingUp = to.price > from.price;
        const color = goingUp ? 'rgba(0,230,118,0.45)' : 'rgba(255,23,68,0.45)';
        const isMajor = swings.metrics && Math.abs(to.price - from.price) > swings.metrics.avgSwingSize * 1.3;
        // Draw as two horizontal segments at each price level connected by labels
        lines.push({
          startTime: from.time, endTime: to.time,
          price: from.price,
          color, lineWidth: isMajor ? 1.5 : 0.8,
          dash: isMajor ? [] : [4, 4], label: '',
        });
        lines.push({
          startTime: from.time, endTime: to.time,
          price: to.price,
          color, lineWidth: isMajor ? 1.5 : 0.8,
          dash: isMajor ? [] : [4, 4], label: '',
        });
      }

      this.data = { markers, lines, swings };
    }

    // ── DRAW SIZE LABELS ON CANVAS ────────────────────────
    _drawSizeLabels(ctx, helpers) {
      if (!this.data.swings || !this.data.swings.all.length) return;
      const points = this.data.swings.all.slice(-10);
      ctx.save();
      ctx.font = '9px monospace';
      ctx.textAlign = 'center';
      for (let i = 1; i < points.length; i++) {
        const from = points[i - 1];
        const to = points[i];
        const size = Math.abs(to.price - from.price);
        if (this.data.swings.metrics && size < this.data.swings.metrics.avgSwingSize * 0.3) continue;
        const pct = (size / from.price * 100).toFixed(2);
        const midPrice = (from.price + to.price) / 2;
        const midTime = Math.floor((from.time + to.time) / 2);
        const x = helpers.timeToX(midTime);
        const y = helpers.priceToY(midPrice);
        if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
        ctx.fillStyle = 'rgba(255,255,255,0.3)';
        ctx.fillText(`$${size.toFixed(2)} (${pct}%)`, x, y);
      }
      ctx.restore();
    }

    // ── DRAW RANGE ZONES (resistance/support shading) ─────
    _drawRangeZones(ctx, helpers) {
      if (!this.data.swings || !this.data.swings.metrics) return;
      const { recentHigh, recentLow, range } = this.data.swings.metrics;
      if (range < 0.01) return;
      const all = this.data.swings.all;
      if (!all.length) return;
      const oldest = all[Math.max(0, all.length - 8)];
      const newest = all[all.length - 1];

      const x1 = helpers.timeToX(oldest.time);
      const x2 = helpers.timeToX(newest.time);
      if (!Number.isFinite(x1) || !Number.isFinite(x2)) return;
      const w = Math.abs(x2 - x1) + 40;
      const left = Math.min(x1, x2);

      // Resistance zone (top 20%) — barely visible
      const resTop = helpers.priceToY(recentHigh);
      const resBot = helpers.priceToY(recentHigh - range * 0.20);
      if (Number.isFinite(resTop) && Number.isFinite(resBot)) {
        ctx.fillStyle = 'rgba(255,23,68,0.02)';
        ctx.fillRect(left, Math.min(resTop, resBot), w, Math.abs(resBot - resTop));
      }

      // Support zone (bottom 20%) — barely visible
      const supTop = helpers.priceToY(recentLow + range * 0.20);
      const supBot = helpers.priceToY(recentLow);
      if (Number.isFinite(supTop) && Number.isFinite(supBot)) {
        ctx.fillStyle = 'rgba(0,230,118,0.02)';
        ctx.fillRect(left, Math.min(supTop, supBot), w, Math.abs(supBot - supTop));
      }
    }

    // ── EQUILIBRIUM PRICE LINE ────────────────────────────
    _drawEqLine() {
      if (!this.data.swings || !this.data.swings.metrics) return;
      const { recentHigh, recentLow } = this.data.swings.metrics;
      const mid = (recentHigh + recentLow) / 2;
      try {
        const pl = this.series.createPriceLine({
          price: mid,
          color: 'rgba(255,255,255,0.05)',
          lineWidth: 0.5,
          lineStyle: 2,
          axisLabelVisible: false,
          title: `EQ ${mid.toFixed(2)}`,
        });
        this._priceLines.push(pl);
      } catch (e) { /* ignore */ }
    }

    _clearPriceLines() {
      this._priceLines.forEach(pl => {
        try { this.series.removePriceLine(pl); } catch (e) { /* ignore */ }
      });
      this._priceLines = [];
    }
  }

  global.SEANSwingDetectionOverlay = SwingDetectionOverlay;
})(window);
