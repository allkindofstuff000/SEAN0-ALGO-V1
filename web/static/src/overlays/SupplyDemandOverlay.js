(function attachSupplyDemandOverlay(global) {
  /**
   * SupplyDemandOverlay — Real-time Supply/Demand zone detection.
   * Demand = consolidation → bullish impulse (green zones below price).
   * Supply = consolidation → bearish impulse (red zones above price).
   * Dynamic reclassification: demand above price → broken demand (BD).
   * Zones tracked as fresh/tested/mitigated with auto-cleanup.
   */
  class SupplyDemandOverlay {
    constructor(chart, series, config = {}) {
      this.chart = chart;
      this.series = series;
      this.config = config;
      this.visible = false;
      this.data = { markers: [], lines: [] };
      this._priceLines = [];
      this._zones = { demand: [], supply: [] };
    }

    setVisible(visible) {
      this.visible = visible;
      if (!visible) this._clear();
    }

    calculate(store, context = {}) {
      const lookback = this.config.SD_LOOKBACK || 100;
      const candles = (store.M5 && store.M5.length > 30 ? store.M5 :
                       store.M15 && store.M15.length > 30 ? store.M15 :
                       store.M1 && store.M1.length > 30 ? store.M1 : []).slice(-lookback);
      if (candles.length < 20) {
        this._zones = { demand: [], supply: [] };
        this.data = { markers: [], lines: [] };
        return this.data;
      }
      this._zones = this._detectAllZones(candles);
      this.data = { markers: [], lines: [] };
      return this.data;
    }

    getMarkers() { return []; }

    draw(ctx, helpers) {
      if (!this.visible) return;
      this._clear();
      this._renderZones(ctx, helpers);
    }

    // ── ZONE DETECTION ────────────────────────────────────
    _detectAllZones(candles) {
      const demand = this._findZones(candles, 'demand');
      const supply = this._findZones(candles, 'supply');

      // Check mitigation
      demand.forEach(z => this._checkMitigation(z, candles));
      supply.forEach(z => this._checkMitigation(z, candles));

      const price = candles[candles.length - 1].close;
      const maxD = this.config.SD_MAX_DEMAND_ZONES || 3;
      const maxS = this.config.SD_MAX_SUPPLY_ZONES || 3;

      // ── DYNAMIC RECLASSIFICATION ──────────────────────
      // Demand zone above price = broken demand (resistance)
      // Supply zone below price = broken supply (support)
      const allZones = [...demand, ...supply].filter(z => !z.mitigated);
      allZones.forEach(z => {
        z.originalType = z.originalType || z.type;
        if (z.originalType === 'demand') {
          if (price < z.zoneLow) {
            z.displayType = 'broken_demand'; // price fell below demand = now resistance
          } else {
            z.displayType = 'demand'; // valid demand below price
          }
        } else {
          if (price > z.zoneHigh) {
            z.displayType = 'broken_supply'; // price rose above supply = now support
          } else {
            z.displayType = 'supply'; // valid supply above price
          }
        }
      });

      // Sort all zones by proximity to price
      const activeDemand = allZones
        .filter(z => z.displayType === 'demand' || z.displayType === 'broken_supply')
        .sort((a, b) => Math.abs(a.mid - price) - Math.abs(b.mid - price))
        .slice(0, maxD);
      const activeSupply = allZones
        .filter(z => z.displayType === 'supply' || z.displayType === 'broken_demand')
        .sort((a, b) => Math.abs(a.mid - price) - Math.abs(b.mid - price))
        .slice(0, maxS);

      return { demand: activeDemand, supply: activeSupply };
    }

    _findZones(candles, type) {
      const zones = [];
      const minBase = this.config.SD_MIN_BASE_CANDLES || 2;
      const maxBase = this.config.SD_MAX_BASE_CANDLES || 6;
      const baseRangeMult = this.config.SD_MAX_BASE_RANGE_MULT || 2.5;
      const impulseBodyPct = this.config.SD_MIN_IMPULSE_BODY_PCT || 0.45;
      const impulseMult = this.config.SD_MIN_IMPULSE_MULT || 1.0;
      const avgRange = this._avgRange(candles, 20);
      const isBullish = type === 'demand';

      // ── PATTERN 1: Base → Impulse (classic) ──────────────
      for (let i = minBase; i < candles.length - 1; i++) {
        const imp = candles[i];
        const body = isBullish ? (imp.close - imp.open) : (imp.open - imp.close);
        const range = imp.high - imp.low;
        const bodyPct = range > 0 ? body / range : 0;

        if (body <= 0 || bodyPct < impulseBodyPct || range < avgRange * impulseMult) continue;

        for (let bLen = minBase; bLen <= maxBase; bLen++) {
          const bStart = i - bLen;
          if (bStart < 0) break;
          const base = candles.slice(bStart, i);
          const bH = Math.max(...base.map(c => c.high));
          const bL = Math.min(...base.map(c => c.low));
          const bRange = bH - bL;

          if (bRange > avgRange * baseRangeMult) continue;

          const ratio = range / (bRange || 0.01);
          const strength = ratio >= 2.5 ? 'strong'
                         : ratio >= 1.5 ? 'medium' : 'weak';

          zones.push({
            type, originalType: type, zoneHigh: bH, zoneLow: bL,
            mid: (bH + bL) / 2,
            formed: base[0].time, formedIndex: bStart,
            impulseSize: range, baseCandles: bLen,
            mitigated: false, mitPct: 0, tested: 0, strength,
          });
          break;
        }
      }

      // ── PATTERN 2: Impulse → Base (drop-base-rally / rally-base-drop) ──
      for (let i = minBase + 1; i < candles.length - 2; i++) {
        const preImp = candles[i - 1];
        const preBody = isBullish ? (preImp.open - preImp.close) : (preImp.close - preImp.open);
        const preRange = preImp.high - preImp.low;
        if (preBody <= 0 || preRange < avgRange * impulseMult) continue;

        for (let bLen = minBase; bLen <= maxBase; bLen++) {
          if (i + bLen >= candles.length) break;
          const base = candles.slice(i, i + bLen);
          const bH = Math.max(...base.map(c => c.high));
          const bL = Math.min(...base.map(c => c.low));
          const bRange = bH - bL;

          if (bRange > avgRange * baseRangeMult) break;

          const afterIdx = i + bLen;
          if (afterIdx >= candles.length) break;
          const afterC = candles[afterIdx];
          const afterBody = isBullish ? (afterC.close - afterC.open) : (afterC.open - afterC.close);
          if (afterBody <= 0) continue;

          const ratio = preRange / (bRange || 0.01);
          const strength = ratio >= 2.5 ? 'strong'
                         : ratio >= 1.5 ? 'medium' : 'weak';

          zones.push({
            type, originalType: type, zoneHigh: bH, zoneLow: bL,
            mid: (bH + bL) / 2,
            formed: base[0].time, formedIndex: i,
            impulseSize: preRange, baseCandles: bLen,
            mitigated: false, mitPct: 0, tested: 0, strength,
          });
          break;
        }
      }

      // ── PATTERN 3: Swing low/high zones (recent pivots) ──
      const pivotBars = 3;
      for (let i = pivotBars; i < candles.length - pivotBars; i++) {
        const c = candles[i];
        if (isBullish) {
          const left = candles.slice(i - pivotBars, i);
          const right = candles.slice(i + 1, i + pivotBars + 1);
          const isSwingLow = left.every(n => c.low <= n.low) &&
                             right.every(n => c.low <= n.low);
          if (!isSwingLow) continue;

          const zH = Math.max(c.open, c.close);
          const zL = c.low;
          const zRange = zH - zL;
          if (zRange < avgRange * 0.3) continue;

          zones.push({
            type, originalType: type, zoneHigh: zH, zoneLow: zL,
            mid: (zH + zL) / 2,
            formed: c.time, formedIndex: i,
            impulseSize: zRange, baseCandles: 1,
            mitigated: false, mitPct: 0, tested: 0,
            strength: zRange >= avgRange * 1.5 ? 'strong' : 'medium',
          });
        } else {
          const left = candles.slice(i - pivotBars, i);
          const right = candles.slice(i + 1, i + pivotBars + 1);
          const isSwingHigh = left.every(n => c.high >= n.high) &&
                              right.every(n => c.high >= n.high);
          if (!isSwingHigh) continue;

          const zH = c.high;
          const zL = Math.min(c.open, c.close);
          const zRange = zH - zL;
          if (zRange < avgRange * 0.3) continue;

          zones.push({
            type, originalType: type, zoneHigh: zH, zoneLow: zL,
            mid: (zH + zL) / 2,
            formed: c.time, formedIndex: i,
            impulseSize: zRange, baseCandles: 1,
            mitigated: false, mitPct: 0, tested: 0,
            strength: zRange >= avgRange * 1.5 ? 'strong' : 'medium',
          });
        }
      }

      // Cap zone size — zones wider than 3x avg candle range are too big
      const maxZoneWidth = avgRange * 3;
      zones.forEach(z => {
        const w = z.zoneHigh - z.zoneLow;
        if (w > maxZoneWidth) {
          // Shrink zone from the center
          const mid = z.mid;
          z.zoneHigh = mid + maxZoneWidth / 2;
          z.zoneLow = mid - maxZoneWidth / 2;
        }
      });

      return this._mergeZones(zones);
    }

    _checkMitigation(zone, candles) {
      const after = candles.filter(c => c.time > zone.formed);
      for (const c of after) {
        if (zone.originalType === 'demand' && c.close < zone.zoneLow) {
          // Don't mitigate — reclassify as broken_demand instead
          // Only truly mitigate if price closed through by more than 1 ATR
          const depth = zone.zoneLow - c.close;
          const zoneSize = zone.zoneHigh - zone.zoneLow;
          if (depth > zoneSize * 3) {
            zone.mitigated = true; return;
          }
        }
        if (zone.originalType === 'supply' && c.close > zone.zoneHigh) {
          const depth = c.close - zone.zoneHigh;
          const zoneSize = zone.zoneHigh - zone.zoneLow;
          if (depth > zoneSize * 3) {
            zone.mitigated = true; return;
          }
        }
        // Track tests — only count distinct touches (not every candle inside zone)
        if (zone.originalType === 'demand' && c.low <= zone.zoneHigh && c.low >= zone.zoneLow) {
          if (!zone._lastTestTime || (c.time - zone._lastTestTime) >= 300) { // 5 min gap between tests
            zone.tested++;
            zone._lastTestTime = c.time;
          }
        }
        if (zone.originalType === 'supply' && c.high >= zone.zoneLow && c.high <= zone.zoneHigh) {
          if (!zone._lastTestTime || (c.time - zone._lastTestTime) >= 300) {
            zone.tested++;
            zone._lastTestTime = c.time;
          }
        }
      }
    }

    _mergeZones(zones) {
      // Merge only by midpoint proximity — NOT by edge overlap (prevents snowball merging)
      const tol = this.config.SD_ZONE_MERGE || 1.50;
      const merged = [];
      const used = new Set();
      for (let i = 0; i < zones.length; i++) {
        if (used.has(i)) continue;
        const group = [zones[i]];
        for (let j = i + 1; j < zones.length; j++) {
          if (used.has(j)) continue;
          if (Math.abs(zones[i].mid - zones[j].mid) <= tol) {
            group.push(zones[j]); used.add(j);
          }
        }
        const sMap = { strong: 3, medium: 2, weak: 1 };
        const best = group.sort((a, b) => (sMap[b.strength] || 0) - (sMap[a.strength] || 0))[0];
        // Keep the best zone's original edges — don't expand
        merged.push(best); used.add(i);
      }
      return merged;
    }

    _avgRange(candles, n) {
      const sl = candles.slice(-n);
      return sl.reduce((s, c) => s + (c.high - c.low), 0) / (sl.length || 1);
    }

    // ── RENDERING ─────────────────────────────────────────
    _renderZones(ctx, helpers) {
      const all = [...this._zones.demand, ...this._zones.supply];
      if (!all.length) return;

      all.forEach(zone => {
        const dt = zone.displayType || zone.type;
        const isSupportSide = dt === 'demand' || dt === 'broken_supply';
        const isBroken = dt === 'broken_demand' || dt === 'broken_supply';

        // Color: green for support-side, red for resistance-side
        // Broken zones get opposite color with different label
        let baseColor;
        if (isSupportSide) {
          baseColor = '38,166,154'; // green/teal
        } else {
          baseColor = '239,83,80'; // red
        }

        // Opacity by strength — broken zones are slightly more transparent
        let opacity = zone.strength === 'strong' ? 0.16
                    : zone.strength === 'medium' ? 0.10 : 0.06;
        if (isBroken) opacity *= 0.7;

        // Reduce if heavily tested
        const testFade = zone.tested > 5 ? 0.5 : zone.tested > 2 ? 0.75 : 1.0;
        const fillOpacity = opacity * testFade;
        const strokeOpacity = Math.min(opacity * 3, 0.5) * testFade;

        // Draw zone rectangle
        const y1 = helpers.priceToY(zone.zoneHigh);
        const y2 = helpers.priceToY(zone.zoneLow);
        const x1 = helpers.timeToX(zone.formed);
        const x2 = helpers.canvasWidth;
        if ([y1, y2, x1].every(Number.isFinite)) {
          ctx.save();
          ctx.fillStyle = `rgba(${baseColor},${fillOpacity})`;
          ctx.fillRect(Math.max(0, x1), Math.min(y1, y2), x2 - Math.max(0, x1), Math.abs(y2 - y1));
          // Border lines
          ctx.strokeStyle = `rgba(${baseColor},${strokeOpacity})`;
          ctx.lineWidth = zone.strength === 'strong' ? 1.0 : 0.5;
          if (isBroken) ctx.setLineDash([3, 3]); // dashed for broken zones
          ctx.beginPath();
          ctx.moveTo(Math.max(0, x1), y1); ctx.lineTo(x2, y1);
          ctx.moveTo(Math.max(0, x1), y2); ctx.lineTo(x2, y2);
          ctx.stroke();
          ctx.restore();
        }

        // Label
        const dots = zone.strength === 'strong' ? '●●●' : zone.strength === 'medium' ? '●●' : '●';
        let label;
        if (dt === 'demand') label = 'D';
        else if (dt === 'supply') label = 'S';
        else if (dt === 'broken_demand') label = 'BD';
        else if (dt === 'broken_supply') label = 'BS';
        else label = dt.charAt(0).toUpperCase();

        const testedText = zone.tested > 0 ? ` ${zone.tested}x` : ' fresh';

        try {
          const pl = this.series.createPriceLine({
            price: zone.mid,
            color: `rgba(${baseColor},${strokeOpacity})`,
            lineWidth: 0.5,
            lineStyle: isBroken ? 1 : 2, // dotted for broken, dashed for active
            axisLabelVisible: true,
            title: `${label} ${dots}${testedText}`,
          });
          this._priceLines.push(pl);
        } catch (e) { /* ignore */ }
      });
    }

    _clear() {
      this._priceLines.forEach(pl => {
        try { this.series.removePriceLine(pl); } catch (e) { /* ignore */ }
      });
      this._priceLines = [];
    }
  }

  global.SEANSupplyDemandOverlay = SupplyDemandOverlay;
})(window);
