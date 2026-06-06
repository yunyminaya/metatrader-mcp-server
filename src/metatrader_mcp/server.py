#!/usr/bin/env python3
import os
import argparse
import logging
from dotenv import load_dotenv

from mcp.server.fastmcp import FastMCP, Context
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Optional, Union

from metatrader_mcp.utils import init, get_client

# ────────────────────────────────────────────────────────────────────────────────
# 1) Lifespan context definition
# ────────────────────────────────────────────────────────────────────────────────
@dataclass
class AppContext:
	client: str

@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:

	try:
		client = init(
			os.getenv("login"),
			os.getenv("password"),
			os.getenv("server"),
			os.getenv("MT5_PATH")
		)
		yield AppContext(client=client)
	finally:
		client.disconnect()

# ────────────────────────────────────────────────────────────────────────────────
# 2) Instantiate FastMCP as `mcp` (must be named `mcp`, `server`, or `app`)
# ────────────────────────────────────────────────────────────────────────────────
mcp = FastMCP(
	"metatrader",
	lifespan=app_lifespan,
	dependencies=[],
)

# ────────────────────────────────────────────────────────────────────────────────
# 3) Register tools with @mcp.tool()
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_account_info(ctx: Context) -> dict:
	"""Get account information (balance, equity, profit, margin level, free margin, account type, leverage, currency)."""
	client = get_client(ctx)
	return client.account.get_trade_statistics()

@mcp.tool()
def get_deals(ctx: Context, from_date: Optional[str] = None, to_date: Optional[str] = None, symbol: Optional[str] = None) -> str:
	"""Get historical deals as CSV. Date input in format: 'YYYY-MM-DD'."""
	client = get_client(ctx)
	df = client.history.get_deals_as_dataframe(from_date=from_date, to_date=to_date, group=symbol)
	return df.to_csv() if hasattr(df, 'to_csv') else str(df)

@mcp.tool()
def get_orders(ctx: Context, from_date: Optional[str] = None, to_date: Optional[str] = None, symbol: Optional[str] = None) -> str:
	"""Get historical orders as CSV. Date input in format: 'YYYY-MM-DD'"""
	client = get_client(ctx)
	df = client.history.get_orders_as_dataframe(from_date=from_date, to_date=to_date, group=symbol)
	return df.to_csv() if hasattr(df, 'to_csv') else str(df)

def get_candles_by_date(ctx: Context, symbol_name: str, timeframe: str, from_date: str = None, to_date: str = None) -> str:
	"""Get candle data for a symbol in a given timeframe and date range as CSV. Date input in format: ISO 8601 or 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM'."""
	client = get_client(ctx)
	df = client.market.get_candles_by_date(symbol_name=symbol_name, timeframe=timeframe, from_date=from_date, to_date=to_date)
	return df.to_csv() if hasattr(df, 'to_csv') else str(df)

@mcp.tool()
def get_candles_latest(ctx: Context, symbol_name: str, timeframe: str, count: int = 100) -> str:
	"""Get the latest N candles for a symbol and timeframe as CSV."""
	client = get_client(ctx)
	df = client.market.get_candles_latest(symbol_name=symbol_name, timeframe=timeframe, count=count)
	return df.to_csv() if hasattr(df, 'to_csv') else str(df)

@mcp.tool()
def get_symbol_price(ctx: Context, symbol_name: str) -> dict:
	"""Get the latest price info for a symbol as a dictionary."""
	client = get_client(ctx)
	return client.market.get_symbol_price(symbol_name=symbol_name)

@mcp.tool()
def get_all_symbols(ctx: Context) -> list:
	"""Get a list of all available market symbols."""
	client = get_client(ctx)
	return client.market.get_symbols()

@mcp.tool()
def get_symbols(ctx: Context, group: Optional[str] = None) -> list:
	"""
	Get a list of available market symbols. Filter symbols by group pattern (e.g., '*USD*').
	"""
	client = get_client(ctx)
	return client.market.get_symbols(group=group)

# ────────────────────────────────────────────────────────────────────────────────
# Order module tools
# ────────────────────────────────────────────────────────────────────────────────

@mcp.tool()
def get_all_positions(ctx: Context) -> list:
	"""Get all open positions."""
	client = get_client(ctx)
	df = client.order.get_all_positions()
	return df.to_csv() if hasattr(df, 'to_csv') else str(df)

@mcp.tool()
def get_positions_by_symbol(ctx: Context, symbol: str) -> list:
	"""Get open positions for a specific symbol."""
	client = get_client(ctx)
	df = client.order.get_positions_by_symbol(symbol=symbol)
	return df.to_csv() if hasattr(df, 'to_csv') else str(df)

@mcp.tool()
def get_positions_by_id(ctx: Context, id: Union[int, str]) -> list:
	"""Get open positions by ID."""
	client = get_client(ctx)
	df = client.order.get_positions_by_id(id=id)
	return df.to_csv() if hasattr(df, 'to_csv') else str(df)

@mcp.tool()
def get_all_pending_orders(ctx: Context) -> list:
	"""Get all pending orders."""
	client = get_client(ctx)
	df = client.order.get_all_pending_orders()
	return df.to_csv() if hasattr(df, 'to_csv') else str(df)

@mcp.tool()
def get_pending_orders_by_symbol(ctx: Context, symbol: str) -> list:
	"""Get pending orders for a specific symbol."""
	client = get_client(ctx)
	df = client.order.get_pending_orders_by_symbol(symbol=symbol)
	return df.to_csv() if hasattr(df, 'to_csv') else str(df)

@mcp.tool()
def get_pending_orders_by_id(ctx: Context, id: Union[int, str]) -> list:
	"""Get pending orders by id."""
	client = get_client(ctx)
	df = client.order.get_pending_orders_by_id(id=id)
	return df.to_csv() if hasattr(df, 'to_csv') else str(df)

@mcp.tool()
def place_market_order(ctx: Context, symbol: str, volume: float, type: str) -> dict:
	"""
	Place a market order. Parameters:
		symbol: Symbol name (e.g., 'EURUSD')
		volume: Lot size. (e.g. 1.5)
		type: Order type ('BUY' or 'SELL')
	"""
	client = get_client(ctx)
	return client.order.place_market_order(symbol=symbol, volume=volume, type=type)

@mcp.tool()
def place_pending_order(ctx: Context, symbol: str, volume: float, type: str, price: float, stop_loss: Optional[Union[int, float]] = 0.0, take_profit: Optional[Union[int, float]] = 0.0) -> dict:
	"""
	Place a pending order. Parameters:
		symbol: Symbol name (e.g., 'EURUSD')
		volume: Lot size. (e.g. 1.5)
		type: Order type ('BUY', 'SELL').
		price: Pending order price.
		stop_loss (optional): Stop loss price.
		take_profit (optional): Take profit price.
	"""
	client = get_client(ctx)
	return client.order.place_pending_order(symbol=symbol, volume=volume, type=type, price=price, stop_loss=stop_loss, take_profit=take_profit)

@mcp.tool()
def modify_position(ctx: Context, id: Union[int, str], stop_loss: Optional[Union[int, float]] = None, take_profit: Optional[Union[int, float]] = None) -> dict:
	"""Modify an open position by ID."""
	client = get_client(ctx)
	return client.order.modify_position(id=id, stop_loss=stop_loss, take_profit=take_profit)
@mcp.tool()
def modify_pending_order(ctx: Context, id: Union[int, str], price: Optional[Union[int, float]] = None, stop_loss: Optional[Union[int, float]] = None, take_profit: Optional[Union[int, float]] = None) -> dict:
	"""Modify a pending order by ID."""
	client = get_client(ctx)
	return client.order.modify_pending_order(id=id, price=price, stop_loss=stop_loss, take_profit=take_profit)
@mcp.tool()
def close_position(ctx: Context, id: Union[int, str]) -> dict:
	"""Close an open position by ID."""
	client = get_client(ctx)
	return client.order.close_position(id=id)

@mcp.tool()
def cancel_pending_order(ctx: Context, id: Union[int, str]) -> dict:
	"""Cancel a pending order by ID."""
	client = get_client(ctx)
	return client.order.cancel_pending_order(id=id)

@mcp.tool()
def close_all_positions(ctx: Context) -> dict:
	"""Close all open positions."""
	client = get_client(ctx)
	return client.order.close_all_positions()

@mcp.tool()
def close_all_positions_by_symbol(ctx: Context, symbol: str) -> dict:
	"""Close all open positions for a specific symbol."""
	client = get_client(ctx)
	return client.order.close_all_positions_by_symbol(symbol=symbol)

@mcp.tool()
def close_all_profitable_positions(ctx: Context) -> dict:
	"""Close all profitable positions."""
	client = get_client(ctx)
	return client.order.close_all_profitable_positions()

@mcp.tool()
def close_all_losing_positions(ctx: Context) -> dict:
	"""Close all losing positions."""
	client = get_client(ctx)
	return client.order.close_all_losing_positions()

@mcp.tool()
def cancel_all_pending_orders(ctx: Context) -> dict:
	"""Cancel all pending orders."""
	client = get_client(ctx)
	return client.order.cancel_all_pending_orders()

@mcp.tool()
def cancel_pending_orders_by_symbol(ctx: Context, symbol: str) -> dict:
	"""Cancel all pending orders for a specific symbol."""
	client = get_client(ctx)
	return client.order.cancel_pending_orders_by_symbol(symbol=symbol)


# ════════════════════════════════════════════════════════════════════════════════
# Intelligence Layer Tools (portadas del polymarket-mcp-server)
# ════════════════════════════════════════════════════════════════════════════════

# ── Conviction ──────────────────────────────────────────────────────────────────

@mcp.tool()
def conviction_decide(ctx: Context, symbol: str, timeframe: str = "H1", bankroll: float = 1000) -> dict:
    """Analyze a single symbol with all indicators (RSI, MACD, MA cross, BB, SR).
    Returns BUY/SELL/PASS verdict with 0-99 confidence."""
    client = get_client(ctx)
    from metatrader_mcp.tools.conviction import decide
    return decide(client, symbol, timeframe, bankroll)

@mcp.tool()
def conviction_scan(ctx: Context, min_confidence: float = 50, bankroll: float = 1000) -> dict:
    """Scan available symbols and return top conviction opportunities sorted by confidence."""
    client = get_client(ctx)
    from metatrader_mcp.tools.conviction import scan
    return scan(client, bankroll, min_confidence)

# ── Regime ──────────────────────────────────────────────────────────────────────

@mcp.tool()
def regime_analyze(ctx: Context, symbol: str, timeframe: str = "H1", days: int = 14) -> dict:
    """Detect market regime for a symbol: trending, ranging, volatile, quiet.
    Returns advice on which strategy works best."""
    client = get_client(ctx)
    from metatrader_mcp.tools.regime import analyze
    return analyze(client, symbol, timeframe, days)

@mcp.tool()
def regime_scan(ctx: Context) -> dict:
    """Scan all available symbols and return regime classification for each."""
    client = get_client(ctx)
    from metatrader_mcp.tools.regime import scan
    return scan(client)

# ── Backtest ────────────────────────────────────────────────────────────────────

@mcp.tool()
def backtest_run(ctx: Context, symbol: str, timeframe: str = "H1", days: int = 30,
                 entry_rule: str = "rsi_oversold", exit_rule: str = "rsi_overbought",
                 bankroll: float = 1000, lot_size: float = 0.01) -> dict:
    """Backtest a strategy against historical data. Returns trades, win rate, profit factor, Sharpe."""
    client = get_client(ctx)
    from metatrader_mcp.tools.backtest import run
    return run(client, symbol, timeframe, days, entry_rule, exit_rule, bankroll, lot_size)

@mcp.tool()
def backtest_compare(ctx: Context, symbol: str) -> dict:
    """Compare multiple entry/exit rule combinations to find the best strategy."""
    client = get_client(ctx)
    from metatrader_mcp.tools.backtest import compare
    return compare(client, symbol)

# ── SelfLearn ───────────────────────────────────────────────────────────────────

@mcp.tool()
def selflearn_record(ctx: Context, symbol: str, predicted_direction: str, expected_edge_pct: float = 5, notes: str = "") -> dict:
    """Record a prediction for future calibration tracking."""
    from metatrader_mcp.tools.selflearn import record
    return record(symbol, predicted_direction, expected_edge_pct, notes)

@mcp.tool()
def selflearn_outcome(ctx: Context, prediction_id: int, actual_pnl_pct: float) -> dict:
    """Record the actual outcome of a prediction. Returns updated calibration report."""
    from metatrader_mcp.tools.selflearn import outcome
    return outcome(prediction_id, actual_pnl_pct)

@mcp.tool()
def selflearn_report(ctx: Context) -> dict:
    """Get calibration report: bias, Brier score, win rate, auto-adjustment advice."""
    from metatrader_mcp.tools.selflearn import report
    return report()

@mcp.tool()
def selflearn_reset(ctx: Context) -> dict:
    """Reset all selflearn prediction data."""
    from metatrader_mcp.tools.selflearn import reset
    return reset()

# ── PaperTrade ──────────────────────────────────────────────────────────────────

@mcp.tool()
def papertrade_open(ctx: Context, symbol: str, order_type: str, volume: float = 0.01,
                    entry_price: float = 0, stop_loss: float = 0, take_profit: float = 0,
                    reason: str = "") -> dict:
    """Open a simulated paper trade. No real MT5 order is placed."""
    client = get_client(ctx)
    from metatrader_mcp.tools.papertrade import open_order
    return open_order(client, symbol, order_type, volume, entry_price, stop_loss, take_profit, reason)

@mcp.tool()
def papertrade_close(ctx: Context, position_id: int, exit_price: float = 0) -> dict:
    """Close a paper trade and record simulated PnL."""
    client = get_client(ctx)
    from metatrader_mcp.tools.papertrade import close_order
    return close_order(client, position_id, exit_price)

@mcp.tool()
def papertrade_portfolio(ctx: Context) -> dict:
    """Get paper trading portfolio: balance, open positions, PnL, win rate."""
    from metatrader_mcp.tools.papertrade import portfolio
    return portfolio()

@mcp.tool()
def papertrade_reset(ctx: Context, balance: float = 10000) -> dict:
    """Reset paper trading portfolio to initial balance."""
    from metatrader_mcp.tools.papertrade import reset
    return reset(balance)

# ── Builder (Strategy Builder) ──────────────────────────────────────────────────

@mcp.tool()
def builder_create(ctx: Context, name: str, description: str = "",
                   entry_conditions: list = None, exit_conditions: list = None,
                   sl_atr_multiple: float = 1.5, tp_atr_multiple: float = 3.0,
                   max_risk_usd: float = 10, max_positions: int = 3) -> dict:
    """Create a reusable compound trading strategy with indicator conditions."""
    from metatrader_mcp.tools.builder import create
    return create(name, description, entry_conditions, exit_conditions,
                  sl_atr_multiple, tp_atr_multiple, max_risk_usd, max_positions)

@mcp.tool()
def builder_list(ctx: Context) -> dict:
    """List all saved strategies."""
    from metatrader_mcp.tools.builder import list_all
    return list_all()

@mcp.tool()
def builder_get(ctx: Context, name: str) -> dict:
    """Get a saved strategy by name."""
    from metatrader_mcp.tools.builder import get
    return get(name)

@mcp.tool()
def builder_delete(ctx: Context, name: str) -> dict:
    """Delete a saved strategy by name."""
    from metatrader_mcp.tools.builder import delete
    return delete(name)

@mcp.tool()
def builder_evaluate(ctx: Context, name: str, symbol: str, timeframe: str = "H1") -> dict:
    """Evaluate a saved strategy against current candle data. Returns entry signal, SL, TP."""
    client = get_client(ctx)
    from metatrader_mcp.tools.builder import evaluate
    return evaluate(client, name, symbol, timeframe)

# ── Scheduler ───────────────────────────────────────────────────────────────────

@mcp.tool()
def scheduler_configure(ctx: Context, interval_minutes: int = 60, daily_limit: int = 3,
                        min_confidence: int = 60, max_daily_drawdown_pct: float = 10,
                        max_consecutive_losses: int = 5, symbols: list = None) -> dict:
    """Configure the auto-execution scheduler parameters."""
    from metatrader_mcp.tools.scheduler import configure
    return configure(interval_minutes, daily_limit, min_confidence, max_daily_drawdown_pct, max_consecutive_losses, symbols)

@mcp.tool()
def scheduler_start(ctx: Context) -> dict:
    """Enable the scheduler for auto-trading."""
    from metatrader_mcp.tools.scheduler import start
    return start()

@mcp.tool()
def scheduler_stop(ctx: Context) -> dict:
    """Disable the scheduler immediately."""
    from metatrader_mcp.tools.scheduler import stop
    return stop()

@mcp.tool()
def scheduler_status(ctx: Context) -> dict:
    """Get scheduler status, daily trade count, consecutive losses, insurance fund."""
    from metatrader_mcp.tools.scheduler import status
    return status()

@mcp.tool()
def scheduler_tick(ctx: Context) -> dict:
    """Execute one scheduler tick: scan conviction, execute if threshold met.
    Call this periodically (e.g. every 60 min) to drive auto-trading."""
    client = get_client(ctx)
    from metatrader_mcp.tools.scheduler import tick
    return tick(client)

# ── Dashboard ───────────────────────────────────────────────────────────────────

@mcp.tool()
def dashboard_overview(ctx: Context) -> dict:
    """Consolidated system snapshot: account, positions, regime, conviction, scheduler, papertrade, guard."""
    client = get_client(ctx)
    from metatrader_mcp.tools.dashboard import overview
    return overview(client)

# ── Guard ───────────────────────────────────────────────────────────────────────

@mcp.tool()
def guard_start(ctx: Context) -> dict:
    """Enable auto-monitoring of positions (SL/TP checks, correlation risk)."""
    from metatrader_mcp.tools.guard import start
    return start()

@mcp.tool()
def guard_stop(ctx: Context) -> dict:
    """Disable auto-monitoring."""
    from metatrader_mcp.tools.guard import stop
    return stop()

@mcp.tool()
def guard_status(ctx: Context) -> dict:
    """Get guard monitoring status."""
    from metatrader_mcp.tools.guard import status
    return status()

@mcp.tool()
def guard_check(ctx: Context) -> dict:
    """Run one guard check: close positions at SL/TP, detect correlation risk.
    Call periodically (e.g. every 60s) from an external loop."""
    client = get_client(ctx)
    from metatrader_mcp.tools.guard import check
    return check(client)


# ── Insurance ───────────────────────────────────────────────────────────────────

@mcp.tool()
def insurance_status(ctx: Context) -> dict:
    """Get insurance fund balance and transaction history."""
    from metatrader_mcp.tools.insurance import status
    return status()

@mcp.tool()
def insurance_reset(ctx: Context) -> dict:
    """Reset insurance fund to zero."""
    from metatrader_mcp.tools.insurance import reset
    return reset()

# ── Emergency ───────────────────────────────────────────────────────────────────

@mcp.tool()
def emergency_configure(ctx: Context, max_consecutive_losses: int = 5, max_drawdown_pct: float = 30) -> dict:
    """Configure emergency brake thresholds."""
    from metatrader_mcp.tools.emergency import configure
    return configure(max_consecutive_losses, max_drawdown_pct)

@mcp.tool()
def emergency_status(ctx: Context) -> dict:
    """Get emergency brake status: active, consecutive losses, drawdown."""
    from metatrader_mcp.tools.emergency import status
    return status()

@mcp.tool()
def emergency_reset(ctx: Context) -> dict:
    """Manually reset the emergency brake and re-enable trading."""
    from metatrader_mcp.tools.emergency import reset
    return reset()

# ── Heartbeat ───────────────────────────────────────────────────────────────────

@mcp.tool()
def heartbeat_status(ctx: Context) -> dict:
    """Check if scheduler and guard are alive. Returns alerts if components missed intervals."""
    from metatrader_mcp.tools.heartbeat import check_status
    return check_status()

@mcp.tool()
def heartbeat_reset(ctx: Context) -> dict:
    """Reset heartbeat tracker."""
    from metatrader_mcp.tools.heartbeat import reset
    return reset()

# ── Live (Smart Order Placement) ────────────────────────────────────────────────

@mcp.tool()
def live_place_smart_order(ctx: Context, symbol: str, order_type: str, volume: float = 0.01,
                           sl_atr_multiple: float = 1.5, tp_atr_multiple: float = 3.0,
                           use_trailing: bool = True, use_breakeven: bool = True,
                           trailing_activation_pct: float = 0.5) -> dict:
    """Place a market order with intelligent auto-SL/TP based on ATR.
    Calculates SL/TP using ATR, opens the order, sets SL/TP immediately.
    Registers for trailing stop and breakeven monitoring."""
    client = get_client(ctx)
    from metatrader_mcp.tools.live import place_smart_order
    return place_smart_order(client, symbol, order_type, volume, sl_atr_multiple, tp_atr_multiple, use_trailing, use_breakeven, trailing_activation_pct)

@mcp.tool()
def live_set_trailing_stop(ctx: Context, ticket: str, atr_multiple: float = 1.5, activation_pips: float = 20) -> dict:
    """Move SL to trail behind price at ATR*distance. Only moves in profit direction."""
    client = get_client(ctx)
    from metatrader_mcp.tools.live import set_trailing_stop
    return set_trailing_stop(client, ticket, atr_multiple, activation_pips)

@mcp.tool()
def live_set_breakeven(ctx: Context, ticket: str, activation_profit_pct: float = 0.3) -> dict:
    """Move SL to entry price when profit exceeds activation_profit_pct."""
    client = get_client(ctx)
    from metatrader_mcp.tools.live import set_breakeven
    return set_breakeven(client, ticket, activation_profit_pct)

@mcp.tool()
def live_close_all(ctx: Context, close_live: bool = True, close_paper: bool = True) -> dict:
    """Close ALL positions (live MT5 + papertrade) in one call. Emergency shutdown."""
    client = get_client(ctx)
    from metatrader_mcp.tools.live import close_all
    return close_all(client, close_live, close_paper)


# ── Divergence ──────────────────────────────────────────────────────────────────

@mcp.tool()
def divergence_check(ctx: Context, symbol: str) -> dict:
    """Check for RSI/MACD divergence on current H1 data.
    Bullish divergence = price lower low, indicator higher low (upward reversal signal).
    Bearish divergence = price higher high, indicator lower high (downward reversal signal)."""
    client = get_client(ctx)
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=200)
    import pandas as pd
    if isinstance(df, pd.DataFrame):
        c = df['close'].dropna().values
        h = df['high'].dropna().values
        l = df['low'].dropna().values
        from metatrader_mcp.tools.divergence import check_divergence
        return check_divergence(c, h, l)
    return {"success": False, "error": "Cannot fetch data"}

# ── Market Context ──────────────────────────────────────────────────────────────

@mcp.tool()
def market_sessions(ctx: Context) -> dict:
    """Get currently active trading sessions with quality scores.
    London/NY overlap = best time to trade (highest liquidity)."""
    from metatrader_mcp.tools.market import active_sessions
    return active_sessions()

@mcp.tool()
def market_news_check(ctx: Context, symbol: str = "") -> dict:
    """Check for high-impact economic events nearby (NFP, FOMC, CPI, etc.).
    Returns 'HOLD' advice if event within 2 hours."""
    from metatrader_mcp.tools.market import check_high_impact_news
    return check_high_impact_news(symbol if symbol else None)

@mcp.tool()
def market_spread(ctx: Context, symbol: str) -> dict:
    """Analyze spread quality for a symbol. Returns tradeable/not tradeable."""
    client = get_client(ctx)
    from metatrader_mcp.tools.market import spread_analysis
    return spread_analysis(client, symbol)

@mcp.tool()
def market_best_time(ctx: Context, symbol: str) -> dict:
    """Get the best trading session and time for a specific symbol."""
    from metatrader_mcp.tools.market import best_time_to_trade
    return best_time_to_trade(symbol)

# ── Correlation ────────────────────────────────────────────────────────────────

@mcp.tool()
def correlation_check(ctx: Context, symbol_a: str, symbol_b: str) -> dict:
    """Get correlation between two symbols. |r|>0.7 = highly correlated."""
    from metatrader_mcp.tools.correlation import get_correlation
    return {"correlation": get_correlation(symbol_a, symbol_b)}

@mcp.tool()
def correlation_portfolio_risk(ctx: Context) -> dict:
    """Analyze current portfolio correlation risk.
    Warns if >30% of capital exposed to correlated symbols."""
    from metatrader_mcp.tools.papertrade import portfolio
    p = portfolio()
    syms = [x.get("symbol") for x in p.get("portfolio", {}).get("positions", []) if x.get("symbol")]
    from metatrader_mcp.tools.correlation import portfolio_risk
    return portfolio_risk(syms)

@mcp.tool()
def correlation_matrix(ctx: Context, symbols: list) -> dict:
    """Calculate correlation matrix from actual price data for given symbols."""
    client = get_client(ctx)
    from metatrader_mcp.tools.correlation import calculate_from_data
    return calculate_from_data(client, symbols)

# ── Analytics ───────────────────────────────────────────────────────────────────

@mcp.tool()
def analytics_report(ctx: Context) -> dict:
    """Full performance report: equity curve, Sharpe/Sortino/Calmar ratios, Monte Carlo drawdown."""
    from metatrader_mcp.tools.analytics import full_report
    return full_report()

@mcp.tool()
def analytics_ratios(ctx: Context) -> dict:
    """Calculate Sharpe, Sortino, Calmar ratios, win rate, profit factor from trade history."""
    from metatrader_mcp.tools.analytics import performance_ratios
    return performance_ratios()

@mcp.tool()
def analytics_monte_carlo(ctx: Context, simulations: int = 1000) -> dict:
    """Monte Carlo simulation: reshuffles trade outcomes to estimate drawdown risk.
    Returns probability of exceeding various drawdown levels."""
    from metatrader_mcp.tools.analytics import monte_carlo_drawdown
    return monte_carlo_drawdown(simulations=simulations)

@mcp.tool()
def analytics_equity_curve(ctx: Context) -> dict:
    """Compute equity curve from papertrade history. Returns balance progression + max drawdown."""
    from metatrader_mcp.tools.analytics import equity_curve
    return equity_curve()

# ── Backtest (enhanced) ─────────────────────────────────────────────────────────

@mcp.tool()
def backtest_walk_forward(ctx: Context, symbol: str, entry_rule: str = "rsi_oversold",
                           exit_rule: str = "rsi_overbought", bankroll: float = 1000,
                           lot_size: float = 0.01) -> dict:
    """Walk-forward backtest: trains on 60 days, tests on next 20, rolls forward.
    More realistic than simple backtest — tests on unseen data."""
    client = get_client(ctx)
    from metatrader_mcp.tools.backtest import walk_forward
    return walk_forward(client, symbol, "H1", 60, 20, entry_rule, exit_rule, bankroll, lot_size)

@mcp.tool()
def backtest_monte_carlo(ctx: Context, symbol: str, entry_rule: str = "rsi_oversold",
                          exit_rule: str = "rsi_overbought", bankroll: float = 1000,
                          lot_size: float = 0.01, simulations: int = 500) -> dict:
    """Monte Carlo on backtest: reshuffles trade outcomes to estimate max drawdown risk."""
    client = get_client(ctx)
    from metatrader_mcp.tools.backtest import monte_carlo
    return monte_carlo(client, symbol, entry_rule, exit_rule, bankroll, lot_size, simulations)


# ── ML Predictor ───────────────────────────────────────────────────────────────

@mcp.tool()
def predictor_train(ctx: Context) -> dict:
    """Train the ML model from all collected trade samples.
    Uses Naive Bayes on indicator values vs actual outcomes.
    Returns feature importance (which indicators matter most)."""
    from metatrader_mcp.tools.predictor import train
    return train()

@mcp.tool()
def predictor_status(ctx: Context) -> dict:
    """Get ML model status: samples collected, accuracy, feature importance."""
    from metatrader_mcp.tools.predictor import get_model_info
    return get_model_info()

@mcp.tool()
def predictor_predict(ctx: Context, symbol: str) -> dict:
    """Predict next direction for a symbol using the trained ML model.
    Uses current indicator values + historical pattern matching."""
    client = get_client(ctx)
    from metatrader_mcp.tools.conviction import decide
    d = decide(client, symbol)
    if d.get("success"):
        dec = d.get("decision", {})
        return {
            "success": True,
            "symbol": symbol,
            "price": dec.get("current_price"),
            "verdict": dec.get("verdict"),
            "confidence_pct": dec.get("confidence_pct"),
            "ml_probability": dec.get("ml_probability"),
            "ml_direction": dec.get("ml_direction"),
            "ml_boost": dec.get("ml_boost"),
            "model_trained": dec.get("model_trained", False),
        }
    return d

@mcp.tool()
def predictor_reset(ctx: Context) -> dict:
    """Reset all ML training data and start fresh."""
    from metatrader_mcp.tools.predictor import reset
    return reset()


# ── Multi-Broker ───────────────────────────────────────────────────────────────

@mcp.tool()
def broker_register(ctx: Context, name: str, login: str, password: str, server: str,
                    path: str = "", weight: float = 1.0) -> dict:
    """Register a new broker for multi-broker routing.
    Name is an alias (e.g. 'icmarkets'). Connect after registering."""
    from metatrader_mcp.tools.broker import register_broker
    return register_broker(name, login, password, server, path, weight)

@mcp.tool()
def broker_connect(ctx: Context, name: str) -> dict:
    """Connect to a registered broker's MT5 terminal."""
    from metatrader_mcp.tools.broker import connect_broker
    return connect_broker(name)

@mcp.tool()
def broker_disconnect(ctx: Context, name: str) -> dict:
    """Disconnect from a broker."""
    from metatrader_mcp.tools.broker import disconnect_broker
    return disconnect_broker(name)

@mcp.tool()
def broker_status(ctx: Context) -> dict:
    """Get status of all registered and connected brokers."""
    from metatrader_mcp.tools.broker import status
    return status()

@mcp.tool()
def broker_health(ctx: Context) -> dict:
    """Check health of all connected brokers."""
    from metatrader_mcp.tools.broker import health_check
    return health_check()

@mcp.tool()
def broker_find_best(ctx: Context, symbol: str) -> dict:
    """Find the best broker for a symbol (lowest spread + healthiest)."""
    from metatrader_mcp.tools.broker import find_best_broker
    return find_best_broker(symbol)

@mcp.tool()
def broker_route_order(ctx: Context, symbol: str, order_type: str, volume: float,
                       sl: float = 0, tp: float = 0) -> dict:
    """Route an order to the best available broker automatically.
    Compares spreads across all connected brokers and picks the best one."""
    from metatrader_mcp.tools.broker import route_order
    return route_order(symbol, order_type, volume, sl, tp)

@mcp.tool()
def broker_set_strategy(ctx: Context, strategy: str = "best_spread") -> dict:
    """Set routing strategy: best_spread | weighted | round_robin."""
    from metatrader_mcp.tools.broker import set_routing_strategy
    return set_routing_strategy(strategy)

# ── Arbitrage ──────────────────────────────────────────────────────────────────

@mcp.tool()
def arbitrage_configure(ctx: Context, min_spread_diff_pips: int = 5, max_volume: float = 0.1) -> dict:
    """Configure arbitrage engine parameters."""
    from metatrader_mcp.tools.arbitrage import configure
    return configure(min_spread_diff_pips, max_volume)

@mcp.tool()
def arbitrage_start(ctx: Context) -> dict:
    """Enable the arbitrage engine."""
    from metatrader_mcp.tools.arbitrage import start
    return start()

@mcp.tool()
def arbitrage_stop(ctx: Context) -> dict:
    """Disable the arbitrage engine."""
    from metatrader_mcp.tools.arbitrage import stop
    return stop()

@mcp.tool()
def arbitrage_scan(ctx: Context, broker_a: str, broker_b: str, symbol: str) -> dict:
    """Scan for arbitrage opportunity between two brokers for a symbol."""
    from metatrader_mcp.tools.broker import get_client
    ca = get_client(broker_a)
    cb = get_client(broker_b)
    if not ca or not cb:
        return {"success": False, "error": "One or both brokers not connected"}
    from metatrader_mcp.tools.arbitrage import scan
    return scan(ca, broker_a, cb, broker_b, symbol)

@mcp.tool()
def arbitrage_execute(ctx: Context, broker_a: str, broker_b: str, symbol: str,
                      volume: float = 0.01) -> dict:
    """Execute arbitrage: buy on cheap broker, sell on expensive broker."""
    from metatrader_mcp.tools.broker import get_client
    ca = get_client(broker_a)
    cb = get_client(broker_b)
    if not ca or not cb:
        return {"success": False, "error": "One or both brokers not connected"}
    from metatrader_mcp.tools.arbitrage import execute
    return execute(ca, broker_a, cb, broker_b, symbol, volume)

@mcp.tool()
def arbitrage_status(ctx: Context) -> dict:
    """Get arbitrage engine status and active positions."""
    from metatrader_mcp.tools.arbitrage import status
    return status()

# ── Anti-Manipulation ──────────────────────────────────────────────────────────

@mcp.tool()
def antimanipulation_smart_sl(ctx: Context, symbol: str, entry_price: float,
                               direction: int, atr: float, min_distance_pips: float = 10,
                               avoid_obvious: bool = True) -> dict:
    """Calculate an intelligent stop-loss that avoids obvious levels (round numbers)."""
    from metatrader_mcp.tools.antimanipulation import smart_stop_loss
    return smart_stop_loss(entry_price, direction, atr, min_distance_pips, avoid_obvious)

@mcp.tool()
def antimanipulation_analyze(ctx: Context, symbol: str) -> dict:
    """Full manipulation analysis: stop-hunting, spoofing, obvious levels."""
    client = get_client(ctx)
    from metatrader_mcp.tools.antimanipulation import analyze_symbol
    return analyze_symbol(client, symbol)

# ── Advanced Execution ─────────────────────────────────────────────────────────

@mcp.tool()
def execution_twap(ctx: Context, symbol: str, order_type: str, total_volume: float,
                   duration_seconds: int = 300, slices: int = 10,
                   randomize: bool = True) -> dict:
    """TWAP execution: split order into equal slices over time.
    Reduces market impact for large orders."""
    client = get_client(ctx)
    from metatrader_mcp.tools.execution import twap
    return twap(client, symbol, order_type, total_volume, duration_seconds, slices, randomize)

@mcp.tool()
def execution_vwap(ctx: Context, symbol: str, order_type: str, total_volume: float) -> dict:
    """VWAP execution: follow volume profile from recent bars.
    Slices follow the natural volume distribution."""
    client = get_client(ctx)
    from metatrader_mcp.tools.execution import vwap
    return vwap(client, symbol, order_type, total_volume)

@mcp.tool()
def execution_iceberg(ctx: Context, symbol: str, order_type: str, total_volume: float,
                      display_volume: float = 0.05, rest_seconds: int = 5) -> dict:
    """Iceberg order: only show display_volume at a time, hide real size.
    After each fill, waits rest_seconds before showing next slice."""
    client = get_client(ctx)
    from metatrader_mcp.tools.execution import iceberg
    return iceberg(client, symbol, order_type, total_volume, display_volume, rest_seconds)

@mcp.tool()
def execution_stealth(ctx: Context, symbol: str, order_type: str, volume: float,
                      max_spread_pips: float = 10, max_attempts: int = 10) -> dict:
    """Stealth entry: waits for favorable price + low spread before entering.
    Randomizes timing to avoid detection."""
    client = get_client(ctx)
    from metatrader_mcp.tools.execution import stealth_entry
    return stealth_entry(client, symbol, order_type, volume, max_spread_pips, max_attempts)

@mcp.tool()
def execution_smart_entry(ctx: Context, symbol: str, order_type: str,
                          atr_multiple: float = 0.5, max_wait_seconds: int = 120) -> dict:
    """Smart entry: waits for pullback before entering.
    BUY: waits for price to pull back from recent high.
    SELL: waits for price to pull back from recent low.
    Avoids chasing breakouts."""
    client = get_client(ctx)
    from metatrader_mcp.tools.execution import smart_entry_condition
    return smart_entry_condition(client, symbol, order_type, atr_multiple, max_wait_seconds)


# ── EA Bridge ──────────────────────────────────────────────────────────────────

@mcp.tool()
def ea_send_signal(ctx: Context, symbol: str, order_type: str, volume: float,
                   sl: float = 0, tp: float = 0, wait_result: bool = True) -> dict:
    """Send a trade signal to the MQL5 EA for tick-speed execution.
    EA must be running in MT5. Returns execution result from the EA."""
    from metatrader_mcp.tools.ea_bridge import send_signal
    return send_signal(symbol, order_type, volume, sl, tp, "market", wait_result)

@mcp.tool()
def ea_close_all(ctx: Context) -> dict:
    """Send close_all signal to EA. Closes all positions at tick speed."""
    from metatrader_mcp.tools.ea_bridge import send_close_all
    return send_close_all()

@mcp.tool()
def ea_modify_sl(ctx: Context, ticket: int, new_sl: float) -> dict:
    """Send modify SL signal to EA for a specific ticket."""
    from metatrader_mcp.tools.ea_bridge import send_modify_sl
    return send_modify_sl(ticket, new_sl)

@mcp.tool()
def ea_status(ctx: Context) -> dict:
    """Check EA bridge status and signal directory."""
    from metatrader_mcp.tools.ea_bridge import ea_status
    return ea_status()

# ── Volatility ─────────────────────────────────────────────────────────────────

@mcp.tool()
def volatility_regime(ctx: Context, symbol: str) -> dict:
    """Detect volatility regime: low/medium/high/extreme.
    Returns size multiplier and advice for position sizing."""
    client = get_client(ctx)
    from metatrader_mcp.tools.volatility import regime
    return regime(client, symbol)

@mcp.tool()
def volatility_straddle(ctx: Context, symbol: str) -> dict:
    """Generate straddle signal for volatility breakout.
    Places BUY STOP above range + SELL STOP below range.
    Profits from breakouts in either direction."""
    client = get_client(ctx)
    from metatrader_mcp.tools.volatility import straddle_signal
    return straddle_signal(client, symbol)

@mcp.tool()
def volatility_mean_reversion(ctx: Context, symbol: str, entry_std: float = 2.0) -> dict:
    """Mean reversion strategy for high volatility.
    When price deviates > entry_std from mean, enter counter-direction."""
    client = get_client(ctx)
    from metatrader_mcp.tools.volatility import mean_reversion
    return mean_reversion(client, symbol, entry_std=entry_std)

@mcp.tool()
def volatility_grid(ctx: Context, symbol: str, base_volume: float = 0.01,
                    grid_levels: int = 5) -> dict:
    """Adaptive grid trading strategy. Grid spacing scales with ATR.
    Wider in high volatility, tighter in low."""
    client = get_client(ctx)
    from metatrader_mcp.tools.volatility import adaptive_grid
    return adaptive_grid(client, symbol, base_volume, grid_levels)


# ── Sentiment ──────────────────────────────────────────────────────────────────

@mcp.tool()
def sentiment_analyze(ctx: Context, symbol: str, hours_back: int = 24) -> dict:
    """Analyze news sentiment for a symbol's currency.
    Returns bullish/bearish/neutral with score -1 to +1.
    Integrates with conviction to modulate confidence."""
    from metatrader_mcp.tools.sentiment import analyze_news
    return analyze_news(symbol, hours_back)

@mcp.tool()
def sentiment_integrate(ctx: Context, symbol: str) -> dict:
    """Run full conviction analysis modulated by news sentiment.
    If news strongly contradicts the technical signal,
    reduces confidence or passes the trade."""
    client = get_client(ctx)
    from metatrader_mcp.tools.conviction import decide
    from metatrader_mcp.tools.sentiment import integrate_with_conviction
    d = decide(client, symbol)
    if d.get("success"):
        return integrate_with_conviction(symbol, d)
    return d


# ── AutoOptimizer ──────────────────────────────────────────────────────────────

@mcp.tool()
def autooptimizer_enable(ctx: Context) -> dict:
    """Enable periodic auto-optimization of strategy parameters."""
    from metatrader_mcp.tools.autooptimizer import enable
    return enable()

@mcp.tool()
def autooptimizer_disable(ctx: Context) -> dict:
    """Disable periodic auto-optimization."""
    from metatrader_mcp.tools.autooptimizer import disable
    return disable()

@mcp.tool()
def autooptimizer_run(ctx: Context, symbol: str, fast_mode: bool = True) -> dict:
    """Run parameter optimization for a symbol now.
    Tests multiple param combinations via backtest and returns best config.
    fast_mode=True only tests mid-range values (fast).
    fast_mode=False tests full grid (slow but thorough)."""
    client = get_client(ctx)
    from metatrader_mcp.tools.autooptimizer import run_optimization
    return run_optimization(client, symbol, fast_mode)

@mcp.tool()
def autooptimizer_apply(ctx: Context) -> dict:
    """Apply the best found parameters to the live system configuration."""
    from metatrader_mcp.tools.autooptimizer import apply_best_params
    return apply_best_params()

@mcp.tool()
def autooptimizer_status(ctx: Context) -> dict:
    """Get auto-optimizer status, best params, and optimization history."""
    from metatrader_mcp.tools.autooptimizer import status
    return status()


# ── Candle Patterns ────────────────────────────────────────────────────────────

@mcp.tool()
def patterns_detect(ctx: Context, symbol: str, timeframe: str = "H1", count: int = 30) -> dict:
    """Detect 32 Japanese candlestick patterns on recent data.
    Returns bullish/bearish verdict with strongest patterns and reliability scores.
    Can detect: doji, hammer, engulfing, morning/evening star, three soldiers/crows,
    harami, piercing, dark cloud, tweezers, marubozu, spinning top, abandoned baby + more."""
    client = get_client(ctx)
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=count)
    import pandas as pd
    if isinstance(df, pd.DataFrame):
        candles = []
        for _, row in df.iterrows():
            candles.append({
                "open": row.get("open", 0), "high": row.get("high", 0),
                "low": row.get("low", 0), "close": row.get("close", 0),
                "volume": row.get("tick_volume", row.get("volume", 0)),
            })
        from metatrader_mcp.tools.patterns import detect_all, combine_with_conviction
        return detect_all(candles)
    return {"success": False, "error": "Cannot fetch data"}

@mcp.tool()
def patterns_integrate(ctx: Context, symbol: str, timeframe: str = "H1") -> dict:
    """Run conviction analysis + candle pattern confirmation.
    If patterns confirm the signal → STRONG_BUY/STRONG_SELL.
    If patterns contradict → PASS with explanation."""
    client = get_client(ctx)
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=30)
    import pandas as pd
    if isinstance(df, pd.DataFrame):
        candles = [{"open": r["open"], "high": r["high"], "low": r["low"],
                     "close": r["close"]} for _, r in df.iterrows()]
        from metatrader_mcp.tools.conviction import decide
        from metatrader_mcp.tools.patterns import combine_with_conviction
        d = decide(client, symbol, timeframe)
        if d.get("success"):
            return combine_with_conviction(candles, d)
        return d
    return {"success": False, "error": "Cannot fetch data"}


# ── Order Book ─────────────────────────────────────────────────────────────────

@mcp.tool()
def orderbook_analyze(ctx: Context, symbol: str) -> dict:
    """Analyze market depth (Level 2 order book) for a symbol.
    Returns bid/ask walls, pressure, imbalance, support/resistance zones.
    Shows where big players are placing orders."""
    client = get_client(ctx)
    from metatrader_mcp.tools.orderbook import analyze_depth
    return analyze_depth(client, symbol)

@mcp.tool()
def orderbook_integrate(ctx: Context, symbol: str) -> dict:
    """Conviction analysis modulated by order book pressure.
    If book shows buying pressure + BUY signal → boost.
    If book shows heavy resistance walls + BUY signal → caution."""
    client = get_client(ctx)
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=100)
    import pandas as pd
    if isinstance(df, pd.DataFrame):
        from metatrader_mcp.tools.conviction import decide
        from metatrader_mcp.tools.orderbook import integrate_with_conviction
        d = decide(client, symbol)
        if d.get("success"):
            return integrate_with_conviction(client, symbol, d)
        return d
    return {"success": False, "error": "Cannot fetch data"}


# ── Volume Profile ─────────────────────────────────────────────────────────────

@mcp.tool()
def volumeprofile_calculate(ctx: Context, symbol: str, timeframe: str = "H1", count: int = 48) -> dict:
    """Calculate Volume Profile from recent candles.
    Returns POC (point of control), Value Area (70% volume), HVNs, LVNs.
    Tells you if price is cheap/expensive vs market value."""
    client = get_client(ctx)
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=count)
    import pandas as pd
    if isinstance(df, pd.DataFrame):
        candles = [{"open": r["open"], "high": r["high"], "low": r["low"],
                     "close": r["close"], "volume": r.get("tick_volume", r.get("volume", 0))}
                   for _, r in df.iterrows()]
        from metatrader_mcp.tools.volumeprofile import calculate
        return calculate(candles)
    return {"success": False, "error": "Cannot fetch data"}

@mcp.tool()
def volumeprofile_integrate(ctx: Context, symbol: str, timeframe: str = "H1") -> dict:
    """Conviction analysis modulated by volume profile.
    If price below value + BUY → strong signal (cheap).
    If price above value + BUY → risky."""
    client = get_client(ctx)
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=48)
    import pandas as pd
    if isinstance(df, pd.DataFrame):
        candles = [{"open": r["open"], "high": r["high"], "low": r["low"],
                     "close": r["close"], "volume": r.get("tick_volume", r.get("volume", 0))}
                   for _, r in df.iterrows()]
        from metatrader_mcp.tools.conviction import decide
        from metatrader_mcp.tools.volumeprofile import integrate_with_conviction
        d = decide(client, symbol, timeframe)
        if d.get("success"):
            return integrate_with_conviction(candles, d)
        return d
    return {"success": False, "error": "Cannot fetch data"}


# ── Pyramiding ─────────────────────────────────────────────────────────────────

@mcp.tool()
def pyramiding_enable(ctx: Context, levels: int = 4, scaling: float = 0.5, min_profit_pct: float = 0.5) -> dict:
    """Enable pyramiding: add to winning positions during trends.
    levels=max adds, scaling=volume reduction per level, min_profit=activation threshold."""
    from metatrader_mcp.tools.pyramiding import enable
    return enable(levels, scaling, min_profit_pct)

@mcp.tool()
def pyramiding_disable(ctx: Context) -> dict:
    """Disable pyramiding."""
    from metatrader_mcp.tools.pyramiding import disable
    return disable()

@mcp.tool()
def pyramiding_evaluate(ctx: Context, symbol: str, position_ticket: int, current_price: float,
                        entry_price: float, position_type: str, volume: float) -> dict:
    """Evaluate if a position qualifies for pyramiding.
    Returns recommended add volume if profit threshold is met."""
    from metatrader_mcp.tools.pyramiding import evaluate
    return evaluate(None, symbol, position_ticket, current_price, entry_price, position_type, volume)

@mcp.tool()
def pyramiding_status(ctx: Context) -> dict:
    """Get pyramiding status, active pyramids, and history."""
    from metatrader_mcp.tools.pyramiding import status
    return status()


# ── AutoSwitch ─────────────────────────────────────────────────────────────────

@mcp.tool()
def autoswitch_enable(ctx: Context, max_losses: int = 3, cooldown: int = 2) -> dict:
    """Enable auto-strategy switching.
    Rotates through conviction → mean reversion → grid → straddle when a strategy fails."""
    from metatrader_mcp.tools.autoswitch import enable
    return enable(max_losses, cooldown)

@mcp.tool()
def autoswitch_disable(ctx: Context) -> dict:
    """Disable auto-strategy switching."""
    from metatrader_mcp.tools.autoswitch import disable
    return disable()

@mcp.tool()
def autoswitch_report(ctx: Context) -> dict:
    """Report current active strategy and why. See if we're in defensive mode."""
    from metatrader_mcp.tools.autoswitch import get_current_strategy, status
    return {
        "current": get_current_strategy(),
        "full_status": status(),
    }

@mcp.tool()
def autoswitch_on_result(ctx: Context, strategy: str, won: bool) -> dict:
    """Report a trade result to the auto-switcher.
    If strategy hits max_consecutive_losses, auto-rotates to next strategy."""
    from metatrader_mcp.tools.autoswitch import on_trade_result
    return on_trade_result(strategy, won)

@mcp.tool()
def autoswitch_reset(ctx: Context, strategy: str = "") -> dict:
    """Reset consecutive loss counter for a strategy (or all if empty)."""
    from metatrader_mcp.tools.autoswitch import reset_strategy
    return reset_strategy(strategy)

@mcp.tool()
def autoswitch_status(ctx: Context) -> dict:
    """Get auto-switch full status with all strategy stats."""
    from metatrader_mcp.tools.autoswitch import status
    return status()


# ── Ensemble ───────────────────────────────────────────────────────────────────

@mcp.tool()
def ensemble_evaluate(ctx: Context, symbol: str, bankroll: float = 1000) -> dict:
    """Run ALL 8 strategies simultaneously and produce ONE weighted decision.
    conviction + mean reversion + grid + straddle + patterns + orderbook + volumeprofile + sentiment.
    Each strategy votes BUY/SELL/PASS, weighted by recent performance.
    Only trades if ensemble confidence exceeds threshold."""
    client = get_client(ctx)
    from metatrader_mcp.tools.ensemble import evaluate
    return evaluate(client, symbol, bankroll)

@mcp.tool()
def ensemble_on_result(ctx: Context, strategy: str, won: bool) -> dict:
    """Record trade result for a strategy in the ensemble.
    Recalculates weights so better strategies get more vote power."""
    from metatrader_mcp.tools.ensemble import on_trade_result
    return on_trade_result(strategy, won)

@mcp.tool()
def ensemble_status(ctx: Context) -> dict:
    """Get ensemble status: weights per strategy, recent vote history."""
    from metatrader_mcp.tools.ensemble import status
    return status()


# ── Edge (Statistical Edge Calculator) ────────────────────────────────────────

@mcp.tool()
def edge_calculate(ctx: Context, strategy: str, symbol: str, rsi: float,
                   atr_pct: float, regime: str, session: str, direction: str) -> dict:
    """Calculate Expected Value (EV) and Kelly optimal size for a potential trade.
    Matches against historical similar setups.
    Only trades if EV > 0 and historical win rate > 55%.
    Returns: tradeable, EV, Kelly fraction, win rate of similar trades."""
    from metatrader_mcp.tools.edge import calculate
    return calculate(strategy, symbol, rsi, atr_pct, regime, session, direction)

@mcp.tool()
def edge_record_trade(ctx: Context, strategy: str, symbol: str, rsi: float,
                      atr_pct: float, regime: str, session: str,
                      direction: str, entry: float, exit: float, pnl: float) -> dict:
    """Record a completed trade for future statistical matching.
    Required for edge_calculate to work. Call after every closed trade."""
    from metatrader_mcp.tools.edge import record_trade
    return record_trade(strategy, symbol, rsi, atr_pct, regime, session, direction, entry, exit, pnl)

@mcp.tool()
def edge_configure(ctx: Context, min_similar: int = 5, min_win_rate: float = 55,
                   kelly_fraction: float = 0.25) -> dict:
    """Configure edge calculator parameters."""
    from metatrader_mcp.tools.edge import configure
    return configure(min_similar, min_win_rate, kelly_fraction)

@mcp.tool()
def edge_status(ctx: Context) -> dict:
    """Get edge calculator status and database size."""
    from metatrader_mcp.tools.edge import status
    return status()


# ── Multi-Market (External Market Correlation) ────────────────────────────────

@mcp.tool()
def multimarket_correlations(ctx: Context) -> dict:
    """Update and get live correlations between forex pairs and external markets
    (Gold XAUUSD, Oil XTIUSD, US30, DX). Updates from MT5 price data."""
    client = get_client(ctx)
    from metatrader_mcp.tools.multimarket import update_correlations
    return update_correlations(client)

@mcp.tool()
def multimarket_analyze(ctx: Context, symbol: str) -> dict:
    """Get external market context for a symbol.
    Combines known correlations + live data + current external direction.
    Returns bias adjustment for the symbol."""
    from metatrader_mcp.tools.multimarket import analyze
    return analyze(symbol)

@mcp.tool()
def multimarket_integrate(ctx: Context, symbol: str) -> dict:
    """Conviction analysis modulated by external market bias.
    If Gold/Oil/SP500 agree with the signal → boost confidence.
    If they strongly disagree → reduce confidence."""
    client = get_client(ctx)
    from metatrader_mcp.tools.conviction import decide
    from metatrader_mcp.tools.multimarket import integrate_with_conviction
    d = decide(client, symbol)
    if d.get("success"):
        return integrate_with_conviction(symbol, d)
    return d


# ── Anomaly Detection ─────────────────────────────────────────────────────────

@mcp.tool()
def anomaly_check(ctx: Context, symbol: str) -> dict:
    """Run anomaly detection: extreme volatility, wide spread, price gaps,
    volume spikes, price acceleration. Returns anomaly score 0-1.
    Score >0.5 → reduce size 50%. Score >0.7 → skip trade entirely."""
    client = get_client(ctx)
    from metatrader_mcp.tools.anomaly import check
    return check(client, symbol)

@mcp.tool()
def anomaly_status(ctx: Context) -> dict:
    """Get recent anomaly checks."""
    from metatrader_mcp.tools.anomaly import status
    return status()


# ── Evolution (Forward Testing Competition) ───────────────────────────────────

@mcp.tool()
def evolution_enable(ctx: Context, eval_window: int = 20) -> dict:
    """Enable forward testing competition.
    Runs challenger vs current strategy in paper mode.
    After eval_window trades each, deploys the winner."""
    from metatrader_mcp.tools.evolution import enable
    return enable(eval_window)

@mcp.tool()
def evolution_disable(ctx: Context) -> dict:
    """Disable forward testing competition."""
    from metatrader_mcp.tools.evolution import disable
    return disable()

@mcp.tool()
def evolution_deploy_challenger(ctx: Context, name: str, label: str = "") -> dict:
    """Deploy a challenger strategy. Runs alongside current in paper mode.
    After evaluation_window trades, compared and the winner gets deployed.
    Example: evolution_deploy_challenger('mean_reversion', 'Mean Rev v2')"""
    from metatrader_mcp.tools.evolution import deploy_challenger
    return deploy_challenger(name, label or name)

@mcp.tool()
def evolution_record_trade(ctx: Context, strategy_type: str, won: bool,
                           pnl: float, metadata: str = "") -> dict:
    """Record a trade for evolution tracking.
    strategy_type: 'current' or 'challenger'.
    After enough trades, auto-evolves if challenger outperforms."""
    from metatrader_mcp.tools.evolution import record_trade
    return record_trade(strategy_type, won, pnl, {"note": metadata})

@mcp.tool()
def evolution_status(ctx: Context) -> dict:
    """Get evolution status, current generation, comparison stats."""
    from metatrader_mcp.tools.evolution import status
    return status()


if __name__ == "__main__":
	load_dotenv()
	from metatrader_mcp.utils import resolve_transport_config, run_mcp

	parser = argparse.ArgumentParser(description="MetaTrader MCP Server")
	parser.add_argument("--login",    type=str, help="MT5 login")
	parser.add_argument("--password", type=str, help="MT5 password")
	parser.add_argument("--server",   type=str, help="MT5 server name")
	parser.add_argument("--path",     type=str, help="Path to MT5 terminal executable (optional)")
	parser.add_argument("--transport", type=str, choices=["sse", "stdio", "streamable-http"], default=None, help="MCP transport type (default: sse, env: MCP_TRANSPORT)")
	parser.add_argument("--host",     type=str, default=None, help="Host to bind for SSE/HTTP transport (default: 0.0.0.0, env: MCP_HOST)")
	parser.add_argument("--port",     type=int, default=None, help="Port to bind for SSE/HTTP transport (default: 8080, env: MCP_PORT)")

	args = parser.parse_args()

	# inject into lifespan via env vars
	if args.login:    os.environ["login"]    = args.login
	if args.password: os.environ["password"] = args.password
	if args.server:   os.environ["server"]   = args.server
	if args.path:     os.environ["MT5_PATH"] = args.path

	transport, host, port = resolve_transport_config(args.transport, args.host, args.port)

	# run the MCP server (must call mcp.run)
	run_mcp(mcp, transport, host, port)
