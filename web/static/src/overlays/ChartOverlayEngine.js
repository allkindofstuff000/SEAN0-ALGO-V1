(function attachChartOverlayEngine(global) {
  class ChartOverlayEngine {
    constructor(chartInstance, candleSeries, candleStore, config = {}) {
      this.chart = chartInstance;
      this.series = candleSeries;
      this.store = candleStore;
      this.config = { ...(global.SEANOverlayConfig || {}), ...(config || {}) };
      this.canvas = null;
      this.ctx = null;
      this.container = this.config.container || null;
      this.resizeObserver = null;
      this.recalcTimer = null;
      this.rafId = null;
      this.strategyState = null;
      this.overlays = {};
    }

    async initialize() {
      this.ensureCanvas();
      this.overlays = {
        structure:   new global.SEANStructureOverlay(this.chart, this.series, this.config),
        fvg:         new global.SEANFVGOverlay(this.chart, this.series, this.config),
        orderblock:  new global.SEANOrderBlockOverlay(this.chart, this.series, this.config),
        liquidity:   new global.SEANLiquidityOverlay(this.chart, this.series, this.config),
        displacement:new global.SEANDisplacementOverlay(this.chart, this.series, this.config),
        cisd:        new global.SEANCISDOverlay(this.chart, this.series, this.config),
        signal:      new global.SEANSignalOverlay(this.chart, this.series, this.config),
        swing:       global.SEANSwingDetectionOverlay ? new global.SEANSwingDetectionOverlay(this.chart, this.series, this.config) : null,
        sd:          global.SEANSupplyDemandOverlay ? new global.SEANSupplyDemandOverlay(this.chart, this.series, this.config) : null,
      };

      // BUG 2 FIX: wire up ICTEntryEngine after overlays are created so it can
      // read 15M bias from StructureOverlay and gate conflicting signals.
      if (global.SEANICTEntryEngine) {
        this.entryEngine = new global.SEANICTEntryEngine(this.config);
        this.entryEngine.setStructureOverlay(this.overlays.structure);
      } else {
        this.entryEngine = null;
      }

      this.chart.timeScale().subscribeVisibleLogicalRangeChange(() => this.requestDraw());
      window.addEventListener('resize', () => this.resizeCanvas());
      this.requestFullRecalc();
    }

    setCandles(timeframe, candles) {
      this.store[timeframe] = this.dedupe(candles);
      this.requestFullRecalc();
    }

    upsertClosedCandle(timeframe, candle) {
      this.store[timeframe] = this.dedupe([...(this.store[timeframe] || []), candle]);
      this.requestFullRecalc();
    }

    updateCurrentCandle(timeframe, candle) {
      this.store.current[timeframe] = candle ? { ...candle } : null;
      if (timeframe === 'M1' && candle) {
        this.store.livePrice = candle.close;
        this.store.liveTime = candle.time;
      }
      this.requestDraw();
    }

    setStrategyState(status) {
      this.strategyState = status || null;
      this.store.strategyState = status || null;
      this.store.livePrice = status?.livePrice?.price ?? this.store.livePrice;
      this.store.liveTime = status?.livePrice?.time ?? this.store.liveTime;
      this.overlays.signal?.setSignal(status?.latestSignal || null);
      this.requestDraw();
    }

    onCandleClose(candle, allCandles, timeframe = 'M1') {
      if (Array.isArray(allCandles)) {
        this.store[timeframe] = this.dedupe(allCandles);
      } else if (candle) {
        this.upsertClosedCandle(timeframe, candle);
        return;
      }
      if (timeframe === 'M1' && candle) {
        this.store.livePrice = candle.close;
        this.store.liveTime = candle.time;
      }
      this.requestFullRecalc();
    }

    onTick(tick, currentCandles = {}) {
      this.store.livePrice = tick?.price ?? this.store.livePrice;
      this.store.liveTime = tick?.time ?? this.store.liveTime;
      Object.entries(currentCandles || {}).forEach(([timeframe, candle]) => {
        if (candle) this.store.current[timeframe] = { ...candle };
      });
      this.requestDraw();
    }

    toggleOverlay(name, visible) {
      if (name === 'all') {
        Object.values(this.overlays).forEach((overlay) => overlay.setVisible(Boolean(visible)));
      } else if (this.overlays[name]) {
        this.overlays[name].setVisible(Boolean(visible));
      }
      this.requestDraw();
    }

    clearAll() {
      this.toggleOverlay('all', false);
    }

    requestFullRecalc() {
      clearTimeout(this.recalcTimer);
      this.recalcTimer = setTimeout(() => this.recalculate(), 100);
    }

    requestDraw() {
      if (this.rafId) return;
      this.rafId = requestAnimationFrame(() => {
        this.rafId = null;
        this.draw();
      });
    }

    ensureCanvas() {
      this.container = this.container || this.series?._internal__seriesPaneView?._internal__pane?.canvasBinding?.canvasElement?.parentElement || this.container;
      if (!this.container || this.canvas) return;
      this.container.style.position = this.container.style.position || 'relative';
      this.canvas = document.createElement('canvas');
      this.canvas.className = 'chart-overlay-canvas';
      this.canvas.style.position = 'absolute';
      this.canvas.style.inset = '0';
      this.canvas.style.pointerEvents = 'none';
      this.canvas.style.zIndex = '8';
      this.container.appendChild(this.canvas);
      this.ctx = this.canvas.getContext('2d');
      this.resizeObserver = new ResizeObserver(() => this.resizeCanvas());
      this.resizeObserver.observe(this.container);
      this.resizeCanvas();
    }

    resizeCanvas() {
      if (!this.canvas || !this.container) return;
      const rect = this.container.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      this.canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      this.canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      this.canvas.style.width = `${rect.width}px`;
      this.canvas.style.height = `${rect.height}px`;
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      this.requestDraw();
    }

    helpers() {
      const rect = this.container.getBoundingClientRect();
      return {
        chart: this.chart,
        series: this.series,
        canvasWidth: rect.width,
        canvasHeight: rect.height,
        currentTime: this.store.current.M1?.time || this.store.liveTime || this.store.M1.at(-1)?.time || 0,
        currentPrice: this.store.current.M1?.close || this.store.livePrice || this.store.M1.at(-1)?.close || 0,
        barSpacing: 6,
        timeToX: (time) => {
          const value = this.chart.timeScale().timeToCoordinate(time);
          return Number.isFinite(value) ? value : null;
        },
        priceToY: (price) => {
          const value = this.series.priceToCoordinate(price);
          return Number.isFinite(value) ? value : null;
        },
        clampX: (x) => {
          if (!Number.isFinite(x)) return null;
          return Math.max(-160, Math.min(rect.width + 160, x));
        },
        clampY: (y) => {
          if (!Number.isFinite(y)) return null;
          return Math.max(0, Math.min(rect.height, y));
        },
      };
    }

    recalculate() {
      const context = {
        currentPrice: this.store.current.M1?.close || this.store.livePrice || this.store.M1.at(-1)?.close || 0,
        currentTime:  this.store.current.M1?.time  || this.store.liveTime  || this.store.M1.at(-1)?.time  || 0,
        bias:   this.strategyState?.conditions?.bias15m?.bias || null,
        signal: this.strategyState?.latestSignal || null,
      };

      // Structure must run first — ICTEntryEngine reads its lastBias output
      this.overlays.structure.calculate(this.store, context);

      // BUG 2 FIX: evaluate signal through the 15M bias gate BEFORE rendering.
      // If the entry direction conflicts with 15M structure, suppress the signal
      // so no ENTRY box / price lines / arrow marker appear on the chart.
      if (this.entryEngine && context.signal) {
        const result = this.entryEngine.evaluate(context.signal, this.store);
        if (!result.allowed) {
          // Suppress from chart (server-side signal is NOT cancelled — only display)
          context.signal = null;
        }
      }

      this.overlays.fvg.calculate(this.store, context);
      this.overlays.orderblock.calculate(this.store, context);
      this.overlays.liquidity.calculate(this.store, context);
      this.overlays.displacement.calculate(this.store, context);
      this.overlays.cisd.calculate(this.store, context);
      this.overlays.signal.calculate(this.store, context);
      if (this.overlays.swing) this.overlays.swing.calculate(this.store, context);
      if (this.overlays.sd) this.overlays.sd.calculate(this.store, context);
      this.requestDraw();
    }

    draw() {
      if (!this.ctx || !this.canvas) return;
      const helpers = this.helpers();
      this.ctx.clearRect(0, 0, helpers.canvasWidth, helpers.canvasHeight);

      const markerGroups = [];
      ['structure', 'fvg', 'orderblock', 'liquidity', 'displacement', 'cisd', 'signal', 'swing', 'sd'].forEach((name) => {
        const overlay = this.overlays[name];
        if (!overlay) return;
        overlay.draw(this.ctx, helpers);
        const markers = overlay.getMarkers();
        if (markers && markers.length) markerGroups.push(...markers);
      });

      if (typeof this.series.setMarkers === 'function') {
        const markers = markerGroups.filter((marker) => marker && marker.time).sort((a, b) => a.time - b.time);
        this.series.setMarkers(markers);
      }
    }

    dedupe(candles) {
      const map = new Map();
      (candles || []).forEach((candle) => {
        if (candle && candle.time != null) map.set(Number(candle.time), { ...candle });
      });
      return [...map.values()].sort((a, b) => a.time - b.time);
    }
  }

  global.SEANChartOverlayEngine = ChartOverlayEngine;
})(window);
