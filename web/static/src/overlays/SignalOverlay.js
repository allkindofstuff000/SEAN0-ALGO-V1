(function attachSignalOverlay(global) {
  class SignalOverlay {
    constructor(chart, series, config = {}) {
      this.chart = chart;
      this.series = series;
      this.config = config;
      this.visible = true;
      this.signal = null;
      this.priceLines = [];
      this.data = { markers: [] };
    }

    setVisible(visible) {
      this.visible = visible;
      this.syncPriceLines();
    }

    setSignal(signal) {
      this.signal = signal || null;
      this.syncPriceLines();
    }

    syncPriceLines(currentPrice = null) {
      this.priceLines.forEach((line) => {
        try { this.series.removePriceLine(line); } catch (_) {}
      });
      this.priceLines = [];

      if (!this.visible || !this.signal) return;
      const LS = global.LightweightCharts ? global.LightweightCharts.LineStyle : { Dashed: 2, Dotted: 1 };
      this.priceLines.push(this.series.createPriceLine({ price: this.signal.entryPrice, color: this.config.ENTRY_COLOR || '#26a69a', lineWidth: 1.5, lineStyle: LS.Solid || 0, title: 'ENTRY' }));
      this.priceLines.push(this.series.createPriceLine({ price: this.signal.stopLoss, color: this.config.SL_COLOR || '#ef5350', lineWidth: 1.0, lineStyle: LS.Solid || 0, title: 'SL' }));
      this.priceLines.push(this.series.createPriceLine({ price: this.signal.takeProfit1, color: this.config.TP_COLOR || '#FF6D00', lineWidth: 1.0, lineStyle: LS.Dashed || 2, title: 'TP1' }));
      this.priceLines.push(this.series.createPriceLine({ price: this.signal.takeProfit2, color: this.config.TP_COLOR || '#FF6D00', lineWidth: 1.5, lineStyle: LS.Dashed || 2, title: 'TP2' }));

      const price = currentPrice ?? this.signal.entryPrice;
      const showBE = this.signal.type === 'LONG'
        ? price >= this.signal.breakEvenAt
        : price <= this.signal.breakEvenAt;
      if (showBE) {
        this.priceLines.push(this.series.createPriceLine({ price: this.signal.breakEvenAt, color: this.config.BE_COLOR || 'rgba(255,255,255,0.5)', lineWidth: 1, lineStyle: LS.Dotted || 1, title: 'BE' }));
      }
    }

    calculate(store, context = {}) {
      const signal = context.signal || this.signal;
      this.signal = signal || null;
      this.syncPriceLines(context.currentPrice);
      if (!signal) {
        this.data = { markers: [] };
        return this.data;
      }
      this.data = {
        markers: [{
          time: Math.floor(signal.timestamp / 1000),
          position: signal.type === 'LONG' ? 'belowBar' : 'aboveBar',
          color: signal.type === 'LONG' ? '#00E676' : '#ff8a65',
          shape: signal.type === 'LONG' ? 'arrowUp' : 'arrowDown',
          text: signal.type === 'LONG' ? (this.config.MARKER_ENTRY_LONG || 'LONG') : (this.config.MARKER_ENTRY_SHORT || 'SHORT'),
          size: 2.5,
        }],
      };
      return this.data;
    }

    getMarkers() {
      return this.visible ? this.data.markers : [];
    }

    draw(ctx, helpers) {
      if (!this.visible || !this.signal) return;
      const Rect = global.SEANRectanglePrimitive;
      const startTime = Math.floor(this.signal.timestamp / 1000);
      const endTime = helpers.currentTime;
      const isLong = this.signal.type === 'LONG';
      const riskHigh = isLong ? this.signal.entryPrice : this.signal.stopLoss;
      const riskLow = isLong ? this.signal.stopLoss : this.signal.entryPrice;
      const rewardHigh = isLong ? this.signal.takeProfit2 : this.signal.entryPrice;
      const rewardLow = isLong ? this.signal.entryPrice : this.signal.takeProfit2;

      new Rect({
        startTime,
        endTime,
        high: rewardHigh,
        low: rewardLow,
        fill: 'rgba(38,166,154,0.10)',
        stroke: 'rgba(38,166,154,0.28)',
        lineWidth: 0.8,
        label: `R:R 1:${Number(this.signal.riskReward2 || 0).toFixed(1)}`,
        labelColor: '#26a69a',
      }).draw(ctx, helpers);

      new Rect({
        startTime,
        endTime,
        high: riskHigh,
        low: riskLow,
        fill: 'rgba(239,83,80,0.10)',
        stroke: 'rgba(239,83,80,0.28)',
        lineWidth: 0.8,
      }).draw(ctx, helpers);
    }
  }

  global.SEANSignalOverlay = SignalOverlay;
})(window);
