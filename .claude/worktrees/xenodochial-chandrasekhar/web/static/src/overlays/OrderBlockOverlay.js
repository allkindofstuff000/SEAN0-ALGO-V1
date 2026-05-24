(function attachOrderBlockOverlay(global) {
  class OrderBlockOverlay {
    constructor(chart, series, config = {}) {
      this.chart = chart;
      this.series = series;
      this.config = config;
      this.visible = true;
      this.data = { zones: [], markers: [] };
    }

    setVisible(visible) {
      this.visible = visible;
      if (!visible) this.data = { ...this.data, markers: [] };
    }

    calculate(store, context = {}) {
      const candles = (store.M5 || []).slice(-100);
      const currentPrice = context.currentPrice || (store.current.M1 && store.current.M1.close) || (store.M1.at(-1) && store.M1.at(-1).close) || 0;
      const currentTime = context.currentTime || (store.current.M1 && store.current.M1.time) || (store.M1.at(-1) && store.M1.at(-1).time);
      const zones = [];
      if (!candles.length || !currentTime) {
        this.data = { zones: [], markers: [] };
        return this.data;
      }

      const start = Math.max(1, candles.length - 60);
      for (let i = start; i < candles.length - 3; i += 1) {
        const obCandle = candles[i];
        const future = candles.slice(i + 1, i + 4);
        const bullishBars = future.filter((c) => c.close > c.open).length;
        const bearishBars = future.filter((c) => c.close < c.open).length;
        const bodyPct = Math.abs(obCandle.close - obCandle.open) / Math.max(0.0001, obCandle.high - obCandle.low);

        if (obCandle.close < obCandle.open && bullishBars >= 2 && bodyPct >= (this.config.OB_VALID_CANDLE_BODY || 0.6)) {
          const impulseMove = ((Math.max(...future.map((c) => c.high)) - obCandle.close) / obCandle.close) * 100;
          if (impulseMove >= (this.config.OB_MIN_IMPULSE_PCT || 0.1)) {
            zones.push(this.buildZone('bullish', obCandle, candles.slice(i + 4), currentTime, currentPrice));
          }
        }

        if (obCandle.close > obCandle.open && bearishBars >= 2 && bodyPct >= (this.config.OB_VALID_CANDLE_BODY || 0.6)) {
          const impulseMove = ((obCandle.close - Math.min(...future.map((c) => c.low))) / obCandle.close) * 100;
          if (impulseMove >= (this.config.OB_MIN_IMPULSE_PCT || 0.1)) {
            zones.push(this.buildZone('bearish', obCandle, candles.slice(i + 4), currentTime, currentPrice));
          }
        }
      }

      const filtered = zones.filter((zone) => !zone.mitigated).sort((a, b) => b.formed - a.formed);
      const bullish = filtered.filter((zone) => zone.type === 'bullish').slice(0, this.config.OB_MAX_DISPLAY || 5);
      const bearish = filtered.filter((zone) => zone.type === 'bearish').slice(0, this.config.OB_MAX_DISPLAY || 5);
      const activeZone = [...bullish, ...bearish].find((zone) => zone.active);
      const markers = activeZone && currentTime ? [{
        time: currentTime,
        position: activeZone.type === 'bullish' ? 'belowBar' : 'aboveBar',
        color: activeZone.type === 'bullish' ? '#2196F3' : '#FF6D00',
        shape: 'circle',
        text: 'In OB',
        size: 1,
      }] : [];

      this.data = { zones: [...bullish, ...bearish], markers };
      context.orderblock = this.data;
      return this.data;
    }

    buildZone(type, obCandle, future, currentTime, currentPrice) {
      let tests = 0;
      let mitigated = false;
      future.forEach((candle) => {
        if (candle.high >= obCandle.low && candle.low <= obCandle.high) tests += 1;
        if (type === 'bullish' && candle.close < obCandle.low) mitigated = true;
        if (type === 'bearish' && candle.close > obCandle.high) mitigated = true;
      });
      if (tests > 2) mitigated = true;
      const active = !mitigated
        && currentPrice >= obCandle.low - (this.config.OB_BUFFER_PRICE || 0.5)
        && currentPrice <= obCandle.high + (this.config.OB_BUFFER_PRICE || 0.5);
      return {
        type,
        high: obCandle.high,
        low: obCandle.low,
        midpoint: (obCandle.high + obCandle.low) / 2,
        formed: obCandle.time,
        startTime: obCandle.time,
        endTime: currentTime,
        mitigated,
        active,
        label: type === 'bullish' ? 'Bull OB' : 'Bear OB',
      };
    }

    getMarkers() {
      return this.visible ? this.data.markers : [];
    }

    draw(ctx, helpers) {
      if (!this.visible) return;
      const Rect = global.SEANRectanglePrimitive;
      (this.data.zones || []).forEach((zone) => {
        const bullish = zone.type === 'bullish';
        const fill = bullish
          ? (zone.active ? (this.config.OB_ACTIVE_FILL || 'rgba(33,150,243,0.22)') : (this.config.OB_BULL_FILL || 'rgba(33,150,243,0.1)'))
          : (zone.active ? (this.config.OB_ACTIVE_BEAR_FILL || 'rgba(255,109,0,0.2)') : (this.config.OB_BEAR_FILL || 'rgba(255,109,0,0.1)'));
        const stroke = bullish
          ? (zone.active ? (this.config.OB_ACTIVE_STROKE || 'rgba(33,150,243,0.92)') : (this.config.OB_BULL_STROKE || 'rgba(33,150,243,0.52)'))
          : (zone.active ? (this.config.OB_ACTIVE_BEAR_STROKE || 'rgba(255,109,0,0.92)') : (this.config.OB_BEAR_STROKE || 'rgba(255,109,0,0.52)'));
        new Rect({
          startTime: zone.startTime,
          endTime: zone.endTime,
          high: zone.high,
          low: zone.low,
          fill,
          stroke,
          lineWidth: zone.active ? 1.2 : 0.9,
          label: zone.label,
          labelColor: stroke,
        }).draw(ctx, helpers);
      });
    }
  }

  global.SEANOrderBlockOverlay = OrderBlockOverlay;
})(window);
