# Architecture

## 1. Project Overview

`SEAN0-ALGO-V1` is a modular algorithmic trading signal platform. The system ingests market data, enriches it with indicators, evaluates market structure, scores trade opportunities, applies risk controls, and produces trade signals for both binary and forex-style execution.

At a high level, the platform is designed to:

- analyze market data
- detect trading opportunities
- score signals with transparent weighted logic
- apply operational risk management
- generate signals for binary and forex markets
- expose runtime state, telemetry, and controls through an API and dashboard

The architecture already includes:

- market intelligence
- adaptive signal learning
- historical backtesting
- dashboard monitoring

The broader target architecture also includes walk-forward optimization as the next strategy-validation layer. That layer is described in this document as an architectural extension point, even though the current repository implements backtesting but does not yet contain dedicated walk-forward modules.

The current codebase has two important characteristics:

1. The active runtime is the modular `bot/` package, entered through [main.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/main.py) and [bot/main.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/main.py).
2. Some legacy dashboard/runtime files still exist for compatibility, but the main architectural direction is the modular backend plus the React frontend in `frontend/`.

## 2. High Level System Architecture

```text
Market Data Engine
        |
        v
Indicator Engine
        |
        v
Market Intelligence Layer
        |
        v
Signal Scoring Engine
        |
        v
Risk Manager
        |
        v
Signal Router
        |
        v
Output Systems
```

### Layer Purpose

#### Market Data Engine
Responsible for collecting closed OHLCV candles from exchange APIs or historical datasets. It provides the raw price/volume series consumed by the rest of the engine.

#### Indicator Engine
Calculates technical features such as EMA, ATR, MACD, and VWAP. This layer converts raw candles into feature-rich market state.

#### Market Intelligence Layer
Interprets structure and context. It identifies liquidity zones, sweeps, market regime, and active institutional session.

#### Signal Scoring Engine
Transforms market context into a weighted decision score. It produces a transparent score breakdown and signal direction.

#### Risk Manager
Filters otherwise-valid signals using operational constraints such as cooldown, max signals per day, and loss streak protection.

#### Signal Router
Converts an approved signal into venue-specific output payloads, such as binary signal instructions or forex trade parameters.

#### Output Systems
Delivers state and signals to downstream consumers:

- runtime state files
- dashboard API
- frontend dashboard
- logs
- Telegram or other notifications
- validation and analytics outputs

## 3. Core Backend Modules

### Repository-Level Runtime View

```text
main.py
  -> bot/main.py
      -> bot/config
      -> bot/data
      -> bot/indicators
      -> bot/market
      -> bot/signals
      -> bot/risk
      -> bot/execution
      -> bot/output
      -> bot/learning
      -> bot/debug
      -> bot/dashboard
      -> bot/backtest
```

### Data Layer

Files:

- [data_fetcher.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/data/data_fetcher.py)
- [data_cleaner.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/data/data_cleaner.py)
- [timeframe_manager.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/data/timeframe_manager.py)

Purpose:

- fetch and normalize OHLCV market data
- ensure candles are closed before evaluation
- coordinate multi-timeframe retrieval
- present pandas DataFrames to the rest of the engine

Notes:

- `DataFetcher` currently resolves symbols on Binance and filters out unfinished candles.
- `TimeframeManager` is the main bridge between fetcher logic and the live engine.

### Indicator Engine

File:

- [indicator_engine.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/indicators/indicator_engine.py)

Purpose:

- calculate EMA20
- calculate EMA50
- calculate ATR(14)
- calculate MACD(12,26,9)
- calculate VWAP

Output:

- enriched DataFrame with indicator columns

This layer is intentionally reusable across live trading and historical validation.

### Market Intelligence

Files:

- [liquidity_map.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/market/liquidity_map.py)
- [regime_detector.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/market/regime_detector.py)
- [session_engine.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/market/session_engine.py)

Purpose:

- analyze liquidity structure
- classify market regime
- determine the current trading session

Responsibilities:

- `LiquidityMapEngine`: equal highs, equal lows, swing highs/lows, bullish sweep, bearish sweep
- `RegimeDetector`: `TRENDING`, `RANGING`, `BREAKOUT`
- `SessionEngine`: `ASIAN`, `LONDON`, `OVERLAP`, `NEW_YORK`

This layer supplies the contextual inputs that make the scoring engine more selective than simple indicator-based strategies.

### Signal Engine

Files:

- [scoring_engine.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/signals/scoring_engine.py)
- [signal_logic.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/signals/signal_logic.py)

Purpose:

- evaluate trade opportunities using weighted scoring
- produce a consistent signal direction
- expose a transparent decision object

Current scoring components:

- `trend_alignment`
- `liquidity_sweep`
- `atr_expansion`
- `momentum_candle`
- `session_strength`
- `regime_alignment`

Important design detail:

`SignalGenerator` is the main architectural seam. It returns a `SignalEvaluation` object that is reused by:

- the live trading engine
- decision logging
- the backtesting engine

That reuse is what keeps live execution and historical simulation aligned.

### Risk Management

File:

- [risk_manager.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/risk/risk_manager.py)

Purpose:

- control trading exposure
- restrict signal frequency
- block signals under adverse operating conditions

Current controls:

- `max_signals_per_day`
- `cooldown_candles`
- `max_loss_streak`
- session window filter

The risk manager is stateful. It tracks daily counts, last signal time, and current loss streak through persisted runtime state.

### Execution Layer

Files:

- [signal_router.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/execution/signal_router.py)

Architectural roles:

- `binary_signal.py`
- `forex_signal.py`
- `signal_router.py`

Purpose:

- format signals for different downstream markets
- translate logical signals into execution payloads

Current repository status:

- `signal_router.py` is implemented and contains both binary and forex payload builders.
- Dedicated `binary_signal.py` and `forex_signal.py` modules are not currently present as standalone files; their responsibilities are embodied inside `SignalRouter`.

Output examples:

- binary payload: pair, direction, expiry, score
- forex payload: pair, BUY/SELL direction, entry, stop loss, take profit, score

### Output and Runtime Surface

Files:

- [signal_dispatcher.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/output/signal_dispatcher.py)
- [telegram_bot.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/output/telegram_bot.py)
- [dashboard_api.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/dashboard/dashboard_api.py)

Purpose:

- persist routed signals
- expose runtime telemetry to the dashboard
- publish notifications

The backend also writes operational state to:

- `bot_runtime/engine_state.json`
- `bot_runtime/risk_state.json`
- `bot_runtime/threshold_state.json`
- `logs/decision_trace.log`
- `data/trade_log.csv`
- `data/threshold_history.csv`

## 4. Strategy Validation System

The strategy validation layer exists to answer a different question than the live engine.

- Live engine question: should a signal be routed now?
- Validation question: does this logic remain profitable, stable, and explainable over time?

### Validation Pipeline

```text
Historical Data
    |
    v
Indicator Enrichment
    |
    v
Market Intelligence
    |
    v
Signal Evaluation
    |
    v
Risk Filtering
    |
    v
Trade Simulation
    |
    v
Performance Analysis
    |
    v
Reports / Research Artifacts
```

### Backtesting Engine

Purpose:

- simulate trades on historical candles
- reuse the same scoring and risk logic as live trading
- produce objective strategy metrics

Implemented modules:

- [backtest_runner.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/backtest/backtest_runner.py)
- [trade_simulator.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/backtest/trade_simulator.py)
- [performance_analyzer.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/backtest/performance_analyzer.py)
- [data_loader.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/backtest/data_loader.py)
- [report_generator.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/backtest/report_generator.py)
- [run_backtest.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/run_backtest.py)

Backtest responsibilities:

- load CSV or exchange history
- iterate candle by candle
- run indicator, intelligence, scoring, and risk layers
- simulate binary expiry or forex SL/TP settlement
- compute metrics such as win rate, profit factor, and drawdown
- emit trade logs, JSON summaries, and charts

### Walk-Forward Optimization

Architectural purpose:

- validate whether the strategy remains stable across rolling in-sample and out-of-sample windows
- reduce overfitting risk
- separate parameter discovery from parameter validation

Target modules:

- `wfo_engine.py`
- `parameter_optimizer.py`
- `window_generator.py`

Current repository status:

- these modules are not yet implemented in the current tree
- the architecture already has a natural place for them above the backtest layer

Expected role of each module:

- `window_generator.py`: create rolling training and test windows
- `parameter_optimizer.py`: search threshold, weights, or regime parameters inside the training window
- `wfo_engine.py`: orchestrate repeated optimization plus out-of-sample validation across many windows

## 5. Self Learning System

The self-learning system adapts the signal threshold based on realized trade outcomes. It does not rewrite the strategy; it adjusts the gate that decides whether a scored opportunity is strong enough to route.

Files:

- [performance_tracker.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/learning/performance_tracker.py)
- [threshold_optimizer.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/learning/threshold_optimizer.py)
- [strategy_adapter.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/learning/strategy_adapter.py)

Purpose:

- track completed trades
- analyze recent win rate by score range
- update `SIGNAL_SCORE_THRESHOLD` dynamically
- keep a historical threshold log

High-level flow:

```text
Completed Trades
    |
    v
PerformanceTracker
    |
    v
ThresholdOptimizer
    |
    v
StrategyAdapter
    |
    v
Dynamic Score Threshold
```

Operational effect:

- if recent performance deteriorates, the threshold can increase
- if recent performance is strong, the threshold can decrease slightly
- the live engine consumes the threshold through `SignalGenerator.threshold_provider`

## 6. Debugging and Monitoring

The debugging system exists to make signal behavior inspectable, not just executable.

File:

- [decision_logger.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/debug/decision_logger.py)

Purpose:

- log every signal evaluation step
- record rejected setups and rejection reasons
- support strategy auditing and debugging

Typical fields:

- timestamp
- price
- session
- trend alignment
- liquidity sweep
- ATR expansion
- regime
- score
- threshold
- signal generated
- rejection reason

Output location:

- `logs/decision_trace.log`

Design note:

The logger is asynchronous and queue-based so decision logging does not block the live trading loop.

Monitoring is also supported through:

- `engine_state.json`
- `risk_state.json`
- dashboard API endpoints
- frontend polling every 3 seconds

## 7. Frontend Dashboard

The frontend is a React-based trading dashboard in `frontend/`. It is a control and observability surface for the live engine, not just a passive chart page.

Primary page:

- [Dashboard.jsx](/C:/Users/ALGO/Desktop/QUOTEX_BOT/frontend/src/pages/Dashboard.jsx)

Main components:

- [TopNav.jsx](/C:/Users/ALGO/Desktop/QUOTEX_BOT/frontend/src/components/TopNav.jsx)
- [BotControls.jsx](/C:/Users/ALGO/Desktop/QUOTEX_BOT/frontend/src/components/BotControls.jsx)
- [MarketPanel.jsx](/C:/Users/ALGO/Desktop/QUOTEX_BOT/frontend/src/components/MarketPanel.jsx)
- [SignalPanel.jsx](/C:/Users/ALGO/Desktop/QUOTEX_BOT/frontend/src/components/SignalPanel.jsx)
- [LearningPanel.jsx](/C:/Users/ALGO/Desktop/QUOTEX_BOT/frontend/src/components/LearningPanel.jsx)
- [RiskPanel.jsx](/C:/Users/ALGO/Desktop/QUOTEX_BOT/frontend/src/components/RiskPanel.jsx)
- [PerformancePanel.jsx](/C:/Users/ALGO/Desktop/QUOTEX_BOT/frontend/src/components/PerformancePanel.jsx)
- [DecisionLogs.jsx](/C:/Users/ALGO/Desktop/QUOTEX_BOT/frontend/src/components/DecisionLogs.jsx)
- [ChartsPanel.jsx](/C:/Users/ALGO/Desktop/QUOTEX_BOT/frontend/src/components/ChartsPanel.jsx)

Dashboard responsibilities:

- show engine status
- show current pair and mode
- display market regime, session, volatility, and liquidity state
- display current score, threshold, and signal direction
- allow runtime control actions
- allow risk parameter updates
- show adaptive learning state
- show recent decision traces
- visualize score and performance analytics

### Backend Connectivity

The dashboard communicates with FastAPI endpoints in [dashboard_api.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/dashboard/dashboard_api.py), including:

- `/api/status`
- `/api/market-state`
- `/api/signal`
- `/api/performance`
- `/api/decision-logs`
- `/api/learning-state`
- `/api/risk`
- control endpoints for runtime actions and configuration

### Frontend Architecture

```text
React Dashboard
    |
    v
API Service Layer
    |
    v
FastAPI Dashboard API
    |
    v
Runtime State Files + Live Engine State
```

## 8. Data Flow

### Live Trading Data Flow

```text
Exchange OHLCV
    |
    v
DataFetcher / TimeframeManager
    |
    v
IndicatorEngine
    |
    v
LiquidityMapEngine + RegimeDetector + SessionEngine
    |
    v
SignalScoringEngine / SignalGenerator
    |
    v
RiskManager
    |
    v
SignalRouter
    |
    +------------------> SignalDispatcher / Telegram / Signal History
    |
    +------------------> Engine State / Risk State / Threshold State
    |
    +------------------> Dashboard API
    |
    +------------------> Frontend Dashboard
```

### Strategy Validation Data Flow

```text
Historical CSV / Exchange History
    |
    v
DataLoader
    |
    v
IndicatorEngine
    |
    v
Liquidity / Regime / Session
    |
    v
SignalGenerator
    |
    v
RiskManager
    |
    v
TradeSimulator
    |
    v
PerformanceAnalyzer
    |
    v
ReportGenerator
```

### Learning Data Flow

```text
Completed Trades
    |
    v
PerformanceTracker
    |
    v
ThresholdOptimizer
    |
    v
StrategyAdapter
    |
    v
SignalGenerator threshold_provider
```

## 9. Deployment Architecture

The current architecture is suitable for a small VPS or dedicated server deployment.

### Example Deployment

```text
VPS / Cloud VM
|
+-- Trading Engine Service
|     - main.py
|     - bot/main.py
|     - continuous market polling and signal evaluation
|
+-- API Server
|     - bot/dashboard/dashboard_api.py
|     - FastAPI runtime telemetry and control surface
|
+-- Frontend Dashboard
|     - frontend/ React app
|     - served via Vite in development or static build in production
|
+-- Persistent Storage
      - bot_runtime/*.json
      - data/*.csv
      - data/backtests/*
      - logs/decision_trace.log
```

### Production Responsibilities

#### Trading Engine Service

- runs continuously
- pulls market data on schedule
- evaluates signals
- enforces risk rules
- writes runtime state

#### API Server

- exposes live state and metrics
- receives dashboard control commands
- aggregates logs and performance data into API-friendly payloads

#### Frontend

- polls the API
- shows operational telemetry
- allows operators to start, stop, and tune the engine

#### Storage

The current system is file-based rather than database-first. This is adequate for a single-node deployment and simple research workflows, but larger deployments may later replace file persistence with:

- PostgreSQL
- TimescaleDB
- Redis
- object storage for reports and artifacts

## 10. Future Extensions

The architecture is modular enough to support substantial growth without rewriting the core pipeline.

### New Strategies

Additional strategies can be added by:

- introducing new scoring engines
- adding alternate `SignalGenerator` variants
- swapping or extending market-intelligence modules

### Additional Markets

The router boundary already separates logical signals from execution payloads. This allows extension into:

- CFDs
- futures
- spot crypto
- options-style structured signals

### Machine Learning Models

ML can be added as an additional feature or decision layer without replacing the deterministic core:

- feature augmentation before scoring
- model-assisted regime detection
- confidence overlays on top of existing score outputs

### Portfolio Management

The current architecture is mostly single-instrument oriented. A portfolio layer can be added above `RiskManager` to support:

- cross-asset exposure limits
- capital allocation rules
- correlation-aware throttling
- portfolio-level drawdown control

### Multi-Asset Trading

`TimeframeManager`, the signal engine, and the dashboard API can be extended to evaluate multiple symbols concurrently. The main additional requirements would be:

- symbol-level state partitioning
- portfolio-aware risk management
- multi-asset dashboard views
- more structured persistence

### Walk-Forward Optimization

The next natural validation extension is a formal WFO layer. The backtesting package already provides most of the execution primitives needed. The missing step is orchestration over rolling windows and parameter search.

## Summary For Developers and AI Agents

If you need to understand the project quickly, start here:

1. [main.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/main.py) for the runtime entrypoint.
2. [bot/main.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/main.py) for the live engine orchestration.
3. [signal_logic.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/signals/signal_logic.py) for the main signal decision seam.
4. [dashboard_api.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/dashboard/dashboard_api.py) for the monitoring and control surface.
5. [Dashboard.jsx](/C:/Users/ALGO/Desktop/QUOTEX_BOT/frontend/src/pages/Dashboard.jsx) for the frontend composition root.
6. [backtest_runner.py](/C:/Users/ALGO/Desktop/QUOTEX_BOT/bot/backtest/backtest_runner.py) for strategy validation reuse.

The key architectural principle is simple:

```text
One signal pipeline, many consumers.
```

The live engine, dashboard, learning system, debug logging, and backtester all derive value from the same core evaluation path. That shared pipeline is the main asset to preserve when extending the system.
