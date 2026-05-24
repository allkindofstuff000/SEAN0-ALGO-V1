(function attachRectanglePrimitive(global) {
  class RectanglePrimitive {
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
        high,
        low,
        fill,
        stroke,
        lineWidth = 1,
        label = '',
        labelColor,
        dash = [],
        midpoint = null,
        midpointColor = stroke,
        midpointDash = [4, 3],
      } = this.options;

      if (startTime == null || endTime == null || high == null || low == null) return;

      const leftX = helpers.clampX(helpers.timeToX(startTime));
      const rightX = helpers.clampX(helpers.timeToX(endTime));
      const topY = helpers.clampY(helpers.priceToY(Math.max(high, low)));
      const bottomY = helpers.clampY(helpers.priceToY(Math.min(high, low)));

      if (![leftX, rightX, topY, bottomY].every(Number.isFinite)) return;

      const x = Math.min(leftX, rightX);
      const y = Math.min(topY, bottomY);
      const width = Math.max(1, Math.abs(rightX - leftX));
      const height = Math.max(1, Math.abs(bottomY - topY));

      ctx.save();
      ctx.fillStyle = fill;
      ctx.strokeStyle = stroke;
      ctx.lineWidth = lineWidth;
      if (dash.length) ctx.setLineDash(dash);
      ctx.fillRect(x, y, width, height);
      ctx.strokeRect(x, y, width, height);
      ctx.setLineDash([]);

      if (midpoint != null) {
        const midY = helpers.clampY(helpers.priceToY(midpoint));
        if (Number.isFinite(midY)) {
          ctx.strokeStyle = midpointColor;
          ctx.lineWidth = 1;
          ctx.setLineDash(midpointDash);
          ctx.beginPath();
          ctx.moveTo(x, midY);
          ctx.lineTo(x + width, midY);
          ctx.stroke();
          ctx.setLineDash([]);
        }
      }

      if (label) {
        ctx.fillStyle = labelColor || stroke;
        ctx.font = '10px system-ui, sans-serif';
        ctx.fillText(label, x + 5, y + 12);
      }
      ctx.restore();
    }
  }

  global.SEANRectanglePrimitive = RectanglePrimitive;
})(window);
