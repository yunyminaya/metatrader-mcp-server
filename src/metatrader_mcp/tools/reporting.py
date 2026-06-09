import logging
import math
import pandas as pd
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def drawdown_report(client, lookback_days: int = 90) -> Dict[str, Any]:
    from metatrader_client.account.risk_calculations import calculate_max_drawdown
    try:
        deals = client.history.get_deals_as_dataframe()
    except Exception as e:
        return {"error": True, "message": f"Cannot get deals: {e}", "data": None}
    if deals is None or len(deals) == 0:
        return {"error": True, "message": "No trade history", "data": None}
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    if 'time' in deals.columns:
        deals['time'] = pd.to_datetime(deals['time'], utc=True)
        deals = deals[deals['time'] >= cutoff]
    if len(deals) == 0:
        return {"error": True, "message": f"No deals in last {lookback_days} days", "data": None}
    profit_col = 'profit' if 'profit' in deals.columns else None
    if profit_col is None:
        return {"error": True, "message": "No profit column in deals", "data": None}
    equity_curve = []
    running = 0
    for _, d in deals.iterrows():
        running += d[profit_col]
        equity_curve.append(running)
    dd_result = calculate_max_drawdown(equity_curve)
    return dd_result


def monthly_performance(client, months: int = 12) -> Dict[str, Any]:
    try:
        deals = client.history.get_deals_as_dataframe()
    except Exception as e:
        return {"error": True, "message": str(e), "data": None}
    if deals is None or len(deals) == 0:
        return {"error": True, "message": "No deals", "data": None}
    profit_col = 'profit' if 'profit' in deals.columns else None
    if profit_col is None:
        return {"error": True, "message": "No profit column", "data": None}
    if 'time' in deals.columns:
        deals['time'] = pd.to_datetime(deals['time'], utc=True)
        deals['month'] = deals['time'].dt.to_period('M')
    monthly = deals.groupby('month')[profit_col].agg(['sum', 'count', 'mean'])
    monthly.columns = ['pnl', 'trades', 'avg_trade']
    monthly = monthly.sort_index(ascending=False).head(months)
    total_pnl = monthly['pnl'].sum()
    profitable_months = (monthly['pnl'] > 0).sum()
    total_months = len(monthly)
    win_rate_pct = round(profitable_months / total_months * 100, 1) if total_months > 0 else 0
    monthly_dict = {}
    for idx, row in monthly.iterrows():
        monthly_dict[str(idx)] = {
            "pnl": round(row['pnl'], 2),
            "trades": int(row['trades']),
            "avg_trade": round(row['avg_trade'], 2),
        }
    return {
        "error": False,
        "message": f"Monthly: +{total_pnl:.2f} over {total_months}m ({win_rate_pct}% win months)",
        "data": {
            "months": monthly_dict,
            "total_pnl": round(total_pnl, 2),
            "profitable_months": int(profitable_months),
            "total_months": total_months,
            "win_rate_pct": win_rate_pct,
            "best_month": max(monthly_dict.items(), key=lambda x: x[1]['pnl']) if monthly_dict else None,
            "worst_month": min(monthly_dict.items(), key=lambda x: x[1]['pnl']) if monthly_dict else None,
        }
    }


def symbol_performance(client) -> Dict[str, Any]:
    try:
        deals = client.history.get_deals_as_dataframe()
    except Exception as e:
        return {"error": True, "message": str(e), "data": None}
    if deals is None or len(deals) == 0:
        return {"error": True, "message": "No deals", "data": None}
    profit_col = 'profit' if 'profit' in deals.columns else None
    symbol_col = 'symbol' if 'symbol' in deals.columns else None
    if profit_col is None or symbol_col is None:
        return {"error": True, "message": "Missing required columns", "data": None}
    symbols = deals.groupby(symbol_col)[profit_col].agg(['sum', 'count', 'mean', lambda x: (x > 0).sum()])
    symbols.columns = ['pnl', 'trades', 'avg_trade', 'wins']
    symbols['losses'] = symbols['trades'] - symbols['wins']
    symbols['win_rate'] = (symbols['wins'] / symbols['trades'] * 100).round(1)
    symbols = symbols.sort_values('pnl', ascending=False)
    symbols_dict = {}
    for idx, row in symbols.iterrows():
        symbols_dict[idx] = {
            "pnl": round(row['pnl'], 2),
            "trades": int(row['trades']),
            "win_rate": row['win_rate'],
            "wins": int(row['wins']),
            "losses": int(row['losses']),
            "avg_trade": round(row['avg_trade'], 2),
        }
    return {
        "error": False,
        "message": f"PnL by symbol: {len(symbols_dict)} symbols",
        "data": {"symbols": symbols_dict, "total_pnl": round(symbols['pnl'].sum(), 2)}
    }


def trade_journal(client, limit: int = 50, symbol: Optional[str] = None) -> Dict[str, Any]:
    try:
        deals = client.history.get_deals_as_dataframe()
        orders = client.history.get_orders_as_dataframe()
    except Exception as e:
        return {"error": True, "message": str(e), "data": None}
    trades = []
    if deals is not None and len(deals) > 0:
        if symbol and 'symbol' in deals.columns:
            deals = deals[deals['symbol'] == symbol]
        for _, d in deals.head(limit).iterrows():
            trade = {col: d[col] for col in deals.columns}
            if 'time' in trade:
                trade['time'] = str(trade['time'])
            if 'profit' in trade:
                trade['result'] = "WIN" if trade['profit'] > 0 else "LOSS" if trade['profit'] < 0 else "BREAKEVEN"
            trades.append(trade)
    return {
        "error": False,
        "message": f"{len(trades)} trades in journal",
        "data": {"trades": trades, "total": len(trades), "symbol_filter": symbol}
    }


def account_summary(client) -> Dict[str, Any]:
    import MetaTrader5 as mt5
    acc = mt5.account_info()
    if acc is None:
        return {"error": True, "message": "No account info", "data": None}
    try:
        positions = client.order.get_all_positions()
    except Exception:
        positions = None
    pos_count = positions.index.size if positions is not None else 0
    long_positions = len(positions[positions['type'] == 'BUY']) if positions is not None and 'type' in positions.columns else 0
    short_positions = pos_count - long_positions
    long_pnl = round(positions[positions['type'] == 'BUY']['profit'].sum(), 2) if positions is not None and 'profit' in positions.columns else 0
    short_pnl = round(positions[positions['type'] == 'SELL']['profit'].sum(), 2) if positions is not None and 'profit' in positions.columns else 0
    total_positions_pnl = round(positions['profit'].sum(), 2) if positions is not None and 'profit' in positions.columns else 0
    return {
        "error": False,
        "message": f"Balance: {acc.balance:.2f} | Equity: {acc.equity:.2f} | Margin: {acc.margin:.2f} | {pos_count} positions",
        "data": {
            "balance": round(acc.balance, 2),
            "equity": round(acc.equity, 2),
            "margin": round(acc.margin, 2),
            "free_margin": round(acc.margin_free, 2),
            "margin_level": round(acc.margin_level, 2) if acc.margin_level else None,
            "leverage": acc.leverage,
            "currency": acc.currency,
            "server": acc.server,
            "company": acc.company,
            "name": acc.name,
            "open_positions": pos_count,
            "long_positions": long_positions,
            "short_positions": short_positions,
            "long_pnl": long_pnl,
            "short_pnl": short_pnl,
            "total_positions_pnl": total_positions_pnl,
            "profit": round(acc.profit, 2),
            "daily_profit": round(getattr(acc, 'profit', 0), 2),
        }
    }
