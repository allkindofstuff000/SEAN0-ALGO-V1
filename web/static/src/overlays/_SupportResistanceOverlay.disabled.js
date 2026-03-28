(function attachSupportResistanceOverlay(global) {
  /**
   * SupportResistanceOverlay — Real-time S/R levels from current chart data.
   * Combines 3 methods: pivot clusters, touch-count levels, consolidation zones.
   * Draws price lines (solid/dashed) + zone rectangles on the chart.
   */
  class SupportResistanceOverlay {
    constructor(chart, series, config = {}) {
      this.chart = chart;
      this.series = series;
      this.config = config;
      this.visible = false;
      this.data = { markers: [], lines: [] };
      this._priceLines = [];
      this._levels = [];
    }

    setVisible(visible) {
      this.visible = visible;
      if (!visible) this._clearPriceLines();
    }

    calculate(store, context = {}) {
      const lookback = this.config.SR_LOOKBACK || 100;
      const candles = (store.M5 && store.M5.length > 30 ? store.M5 :
                       store.M15 && store.M15.length > 30 ? store.M15 :
                       store.M1 && store.M1.length > 30 ? store.M1 : []).slice(-lookback);
      if (candles.length < 15) {
        this.data = { markers: [], lines: [] };
        this._levels = [];
        return this.data;
      }
      this._levels = this._calculateLevels(candles);
      // Build canvas lines for zone rendering
      this.data.lines = [];
      this.data.markers = [];
      return this.data;
    }

    getMarkers() {
      return []; // S/R uses price lines, not markers
    }

    draw(ctx, helpers) {
      if (!this.visible) return;
      this._clearPriceLines();
      this._renderLevels(helpers);
      this._drawZones(ctx, helpers);
    }

    // ── LEVEL CALCULATION ─────────────────────────────────
    _calculateLevels(candles) {
      const pivotBars = this.config.SR_PIVOT_BARS || 3;
      const touchTol = this.config.SR_TOUCH_TOLERANCE || 0.30;
      const mergeTol = this.config.SR_MERGE_TOLERANCE || 0.50;
      const minTouches = this.config.SR_MIN_TOUCHES || 3;
      const maxLevels = this.config.SR_MAX_LEVELS || 8;

      // Method 1: Pivot clusters
      const pivots = this._findPivots(candles, pivotBars);

      // Method 2: Most-touched levels
      const touched = this._findTouched(candles, touchTol, minTouches);

      // Method 3: Consolidation zones
      const zones = this._findZones(candles);

      // Combine, merge, score
      const all = [...pivots, ...touched, ...zones];
      const merged = this._merge(all, mergeTol);
      const scored = merged.map(l => ({ ...l, score: this._score(l, candles) }));

      return scored
        .sort((a, b) => b.score - a.score)
        .slice(0, maxLevels);
    }

    // ── METHOD 1: PIVOT POINTS ────────────────────────────
    _findPivots(candles, bars) {
      const results = [];
      for (let i = bars; i < candles.length - bars; i++) {
        const c = candles[i];
        const neighbors = candles.slice(i - bars, i).concat(candles.slice(i + 1, i + bars + 1));

        if (neighbors.every(n => c.high >= n.high)) {
          results.push({ price: c.high, time: c.time, type: 'resistance', index: i, touches: 1, isPivot: true });
        }
        if (neighbors.every(n => c.low <= n.low)) {
          results.push({ price: c.low, time: c.time, type: 'support', index: i, touches: 1, isPivot: true });
        }
      }
      return results;
    }

    // ── METHOD 2: TOUCH COUNT ─────────────────────────────
    _findTouched(candles, tolerance, minTouches) {
      const results = [];
      // Sample every 3rd candle to avoid O(n^2) explosion
      for (let i = 0; i < candles.length; i += 3) {
        const c = candles[i];

        // Check high as resistance
        let ht = 0;
        for (let j = 0; j < candles.length; j++) {
          if (i === j) continue;
          if (Math.abs(candles[j].high - c.high) <= tolerance ||
              Math.abs(candles[j].low - c.high) <= tolerance) ht++;
        }
        if (ht >= minTouches) {
          results.push({ price: c.high, time: c.time, type: 'resistance', index: i, touches: ht });
        }

        // Check low as support
        let lt = 0;
        for (let j = 0; j < candles.length; j++) {
          if (i === j) continue;
          if (Math.abs(candles[j].low - c.low) <= tolerance ||
              Math.abs(candles[j].high - c.low) <= tolerance) lt++;
        }
        if (lt >= minTouches) {
          results.push({ price: c.low, time: c.time, type: 'support', index: i, touches: lt });
        }
      }
      return results;
    }

    // ── METHOD 3: CONSOLIDATION ZONES ─────────────────────
    _findZones(candles) {
      const avgRange = candles.slice(-20).reduce((s, c) => s + (c.high - c.low), 0) / 20;
      const results = [];
      const minBars = 3;

      for (let i = minBars; i < candles.length; i++) {
        const slice = candles.slice(i - minBars, i);
        const rH = Math.max(...slice.map(c => c.high));
        const rL = Math.min(...slice.map(c => c.low));
        const totalRange = rH - rL;

        if (totalRange < avgRange * 1.5 && totalRange > 0) {
          const mid = (rH + rL) / 2;
          const price = candles[i - 1].close;
          results.push({
            price: mid, time: slice[0].time,
            type: price > mid ? 'support' : 'resistance',
            touches: minBars, index: i, isZone: true,
            zoneHigh: rH, zoneLow: rL,
          });
        }
      }
      return results;
    }

    // ── MERGE NEARBY LEVELS ───────────────────────────────
    _merge(levels, tolerance) {
      const merged = [];
      const used = new Set();

      for (let i = 0; i < levels.length; i++) {
        if (used.has(i)) continue;
        const group = [levels[i]];
        for (let j = i + 1; j < levels.length; j++) {
          if (used.has(j)) continue;
          if (Math.abs(levels[i].price - levels[j].price) <= tolerance) {
            group.push(levels[j]);
            used.add(j);
          }
        }
        // Keep strongest from group
        const best = group.sort((a, b) => (b.touches || 0) - (a.touches || 0))[0];
        merged.push({
          ...best,
          touches: group.reduce((s, l) => s + (l.touches || 1), 0),
          isPivot: group.some(l => l.isPivot),
          isZone: group.some(l => l.isZone),
          zoneHigh: group.find(l => l.zoneHigh)?.zoneHigh,
          zoneLow: group.find(l => l.zoneLow)?.zoneLow,
        });
        used.add(i);
      }
      return merged;
    }

    // ── SCORE LEVEL ───────────────────────────────────────
    _score(level, candles) {
      let score = 0;
      const currentPrice = candles[candles.length - 1].close;

      // Touches
      score += (level.touches || 1) * 10;

      // Recency
      const recency = candles.length - (level.index || 0);
      score += Math.max(0, 50 - recency);

      // Proximity to current price
      const distPct = Math.abs(currentPrice - level.price) / currentPrice;
      if (distPct < 0.005) score += 30;
      else if (distPct < 0.010) score += 20;
      else if (distPct < 0.020) score += 10;
      if (distPct > 0.050) score -= 20;

      // Bonus for pivots and zones
      if (level.isPivot) score += 15;
      if (level.isZone) score += 20;

      return score;
    }

    // ── RENDER PRICE LINES ────────────────────────────────
    _renderLevels(helpers) {
      if (!this._levels.length) return;

      const currentPrice = this._levels.length > 0
        ? (helpers?.currentPrice || this._levels[0].price) : 0;

      this._levels.forEach(level => {
        const isSupport = level.type === 'support';
        const distPct = currentPrice > 0 ? Math.abs(currentPrice - level.price) / currentPrice : 0.1;
        const isCritical = distPct < 0.008;

        const strength = level.touches >= 6 ? 'STRONG' : level.touches >= 4 ? 'MEDIUM' : 'WEAK';

        const color = isSupport
          ? (isCritical ? '#00E676' : 'rgba(38,166,154,0.7)')
          : (isCritical ? '#FF1744' : 'rgba(239,83,80,0.7)');

        const lineWidth = strength === 'STRONG' ? 1.5 : strength === 'MEDIUM' ? 1.0 : 0.5;
        const lineStyle = strength === 'STRONG' ? 0 : 2; // 0=solid, 2=dashed

        const dots = strength === 'STRONG' ? '●●●' : strength === 'MEDIUM' ? '●●' : '●';
        const label = `${isSupport ? 'S' : 'R'} ${dots} (${level.touches}x)`;

        try {
          const pl = this.series.createPriceLine({
            price: level.price,
            color,
            lineWidth,
            lineStyle,
            axisLabelVisible: true,
            title: label,
          });
          this._priceLines.push(pl);
        } catch (e) { /* ignore */ }
      });
    }

    // ── DRAW ZONE RECTANGLES ON CANVAS ────────────────────
    _drawZones(ctx, helpers) {
      if (!this._levels.length) return;

      ctx.save();
      this._levels.forEach(level => {
        if (!level.isZone || !level.zoneHigh || !level.zoneLow) return;

        const y1 = helpers.priceToY(level.zoneHigh);
        const y2 = helpers.priceToY(level.zoneLow);
        if (!Number.isFinite(y1) || !Number.isFinite(y2)) return;

        const isSupport = level.type === 'support';
        ctx.fillStyle = isSupport
          ? 'rgba(38,166,154,0.04)'
          : 'rgba(239,83,80,0.04)';
        ctx.fillRect(0, Math.min(y1, y2), helpers.canvasWidth, Math.abs(y2 - y1));
      });
      ctx.restore();
    }

    _clearPriceLines() {
      this._priceLines.forEach(pl => {
        try { this.series.removePriceLine(pl); } catch (e) { /* ignore */ }
      });
      this._priceLines = [];
    }
  }

  global.SEANSupportResistanceOverlay = SupportResistanceOverlay;
})(window);
