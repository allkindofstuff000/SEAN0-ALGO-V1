(function attachOverlayConfig(global) {
  const config = {
    SWING_PIVOT_BARS: 3,
    MAX_SWING_MARKERS: 30,
    SHOW_BOS: true,
    SHOW_CHOCH: true,

    FVG_MIN_SIZE: 0.30,
    FVG_BULL_FILL: 'rgba(38,166,154,0.12)',
    FVG_BEAR_FILL: 'rgba(239,83,80,0.12)',
    FVG_BULL_STROKE: 'rgba(38,166,154,0.5)',
    FVG_BEAR_STROKE: 'rgba(239,83,80,0.5)',
    FVG_MID_DASH: [4, 3],
    FVG_MAX_DISPLAY: 5,
    FVG_MITIGATION_PCT: 0.75,

    OB_BULL_FILL: 'rgba(33,150,243,0.10)',
    OB_BEAR_FILL: 'rgba(255,109,0,0.10)',
    OB_BULL_STROKE: 'rgba(33,150,243,0.52)',
    OB_BEAR_STROKE: 'rgba(255,109,0,0.52)',
    OB_ACTIVE_FILL: 'rgba(33,150,243,0.22)',
    OB_ACTIVE_STROKE: 'rgba(33,150,243,0.92)',
    OB_ACTIVE_BEAR_FILL: 'rgba(255,109,0,0.20)',
    OB_ACTIVE_BEAR_STROKE: 'rgba(255,109,0,0.92)',
    OB_MIN_IMPULSE_PCT: 0.10,
    OB_VALID_CANDLE_BODY: 0.60,
    OB_MAX_DISPLAY: 5,
    OB_BUFFER_PRICE: 0.50,

    EQUAL_LEVEL_TOLERANCE: 0.20,
    SSL_COLOR: 'rgba(239,83,80,0.6)',
    BSL_COLOR: 'rgba(38,166,154,0.6)',
    LIQ_MAX_LEVELS: 6,
    SWEEP_MIN_PIPS: 1,
    SWEEP_MAX_PIPS: 15,
    PIP_SIZE: 0.10,

    DISP_BODY_PCT: 0.75,
    DISP_CLOSE_PCT: 0.85,
    DISP_MIN_SIZE: 1.00,
    DISP_RANGE_MULTIPLIER: 2.0,
    ENTRY_FVG_FILL: 'rgba(0,230,118,0.15)',
    ENTRY_FVG_STROKE: '#00E676',

    ENTRY_COLOR: '#26a69a',
    SL_COLOR: '#ef5350',
    TP_COLOR: '#FF6D00',
    BE_COLOR: 'rgba(255,255,255,0.5)',

    MARKER_HH: 'HH',
    MARKER_HL: 'HL',
    MARKER_LH: 'LH',
    MARKER_LL: 'LL',
    MARKER_BOS: 'BOS',
    MARKER_CHOCH: 'CHoCH',
    MARKER_SWEEP: 'Sweep',
    MARKER_DISP: 'D',
    MARKER_CISD: 'CISD',
    MARKER_ENTRY_LONG: 'LONG',
    MARKER_ENTRY_SHORT: 'SHORT',
  };

  global.SEANOverlayConfig = config;
})(window);
