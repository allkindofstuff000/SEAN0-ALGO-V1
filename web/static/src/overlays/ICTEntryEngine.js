(function attachICTEntryEngine(global) {
  /**
   * ICTEntryEngine — 15M bias gate for long/short entry signals.
   *
   * BUG FIX 2: Long entries were firing even when 15M structure was bearish
   * (Lower-Highs / Lower-Lows confirmed on 15M). This engine reads the last
   * confirmed structure break direction from StructureOverlay and blocks any
   * entry whose direction conflicts with the 15M bias.
   *
   * Usage (inside ChartOverlayEngine):
   *   this.entryEngine = new SEANICTEntryEngine(config);
   *   this.entryEngine.setStructureOverlay(this.overlays.structure);
   *   // then in recalculate():
   *   const result = this.entryEngine.evaluate(context.signal, this.store);
   *   if (!result.allowed) context.signal = null;
   */
  class ICTEntryEngine {
    constructor(config = {}) {
      this.config          = config;
      this.structureOverlay = null;
      this.lastBias         = 'neutral';
      this.lastEvalTime     = null;
    }

    /** Wire up after overlays are initialised. */
    setStructureOverlay(overlay) {
      this.structureOverlay = overlay;
    }

    /**
     * Derive the current 15M structural bias from the last confirmed
     * BOS / CHoCH recorded by StructureOverlay.
     *
     * Returns: 'bullish' | 'bearish' | 'neutral'
     */
    getCurrentBias() {
      if (!this.structureOverlay) return 'neutral';

      const lastBias = this.structureOverlay.data && this.structureOverlay.data.lastBias;
      if (!lastBias) return 'neutral';

      if (String(lastBias).includes('bullish')) return 'bullish';
      if (String(lastBias).includes('bearish')) return 'bearish';
      return 'neutral';
    }

    /**
     * Evaluate whether a signal should be allowed given the 15M bias.
     *
     * @param {object|null} signal  — latestSignal from strategyState
     * @param {object}      store   — candleStore (M1/M5/M15 arrays)
     * @returns {{ allowed: boolean, bias: string, reason?: string }}
     */
    evaluate(signal, store) {
      const bias = this.getCurrentBias();
      const now  = new Date().toISOString();

      // Log every evaluation so the terminal can show the bias state
      console.log(`[ICT] 15M bias: ${bias} at ${now}`);
      this.lastBias     = bias;
      this.lastEvalTime = now;

      // No signal to evaluate — always allow (nothing to block)
      if (!signal || !signal.type) {
        return { allowed: true, bias };
      }

      const type = String(signal.type).toUpperCase();

      if (type === 'LONG') {
        if (bias === 'bearish' || bias === 'neutral') {
          const reason = `Blocked — 15M bias is ${bias}. Need bullish for long entry.`;
          console.warn(`[ICT] ${reason}`);
          return { allowed: false, bias, reason };
        }
      }

      if (type === 'SHORT') {
        if (bias === 'bullish' || bias === 'neutral') {
          const reason = `Blocked — 15M bias is ${bias}. Need bearish for short entry.`;
          console.warn(`[ICT] ${reason}`);
          return { allowed: false, bias, reason };
        }
      }

      return { allowed: true, bias };
    }
  }

  global.SEANICTEntryEngine = ICTEntryEngine;
})(window);
