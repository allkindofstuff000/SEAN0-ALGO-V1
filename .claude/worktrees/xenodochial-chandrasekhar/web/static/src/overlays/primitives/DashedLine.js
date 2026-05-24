(function attachDashedLinePrimitive(global) {
  class DashedLinePrimitive {
    constructor(options = {}) {
      this.options = { ...options };
    }

    setOptions(partial = {}) {
      this.options = { ...this.options, ...partial };
      return this;
    }

    draw(ctx, helpers) {
      const {
        startTime,
        endTime,
        price,
        color = '#ffffff',
        lineWidth = 1,
        dash = [],
        label = '',
        labelAlign = 'right',
      } = this.options;

      if (startTime == null || endTime == null || price == null) return;
      const x1 = helpers.clampX(helpers.timeToX(startTime));
      const x2 = helpers.clampX(helpers.timeToX(endTime));
      const y = helpers.clampY(helpers.priceToY(price));
      if (![x1, x2, y].every(Number.isFinite)) return;

      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = lineWidth;
      if (dash.length) ctx.setLineDash(dash);
      ctx.beginPath();
      ctx.moveTo(x1, y);
      ctx.lineTo(x2, y);
      ctx.stroke();
      ctx.setLineDash([]);

      if (label) {
        ctx.fillStyle = color;
        ctx.font = '10px system-ui, sans-serif';
        const labelX = labelAlign === 'left' ? x1 + 4 : Math.max(6, x2 - 56);
        ctx.fillText(label, labelX, y - 4);
      }
      ctx.restore();
    }
  }

  global.SEANDashedLinePrimitive = DashedLinePrimitive;
})(window);
