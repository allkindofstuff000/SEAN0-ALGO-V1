"""OpenClaw tool registry for the SEAN0-ALGO-V1 trading bot."""

from .analyze_results_tool import analyze_trades
from .market_regime_tool import get_market_regime, open_regime_visuals
from .monitor_logs_tool import monitor_logs
from .optimize_strategy_tool import optimize_strategy
from .research_tool import run_research
from .run_backtest_tool import run_backtest
from .run_live_strategy_tool import run_live_strategy


TOOLS = {
    "run_backtest": run_backtest,
    "run_live_strategy": run_live_strategy,
    "analyze_results": analyze_trades,
    "optimize_strategy": optimize_strategy,
    "monitor_logs": monitor_logs,
    "get_market_regime": get_market_regime,
    "open_regime_visuals": open_regime_visuals,
    "run_research": run_research,
}


__all__ = [
    "TOOLS",
    "analyze_trades",
    "get_market_regime",
    "open_regime_visuals",
    "monitor_logs",
    "optimize_strategy",
    "run_research",
    "run_backtest",
    "run_live_strategy",
]
