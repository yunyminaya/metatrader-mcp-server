from typing import Optional
import math


def calculate_lot_size_by_risk(connection, symbol: str, risk_percent: float, stop_loss_pips: float, account_balance: Optional[float] = None) -> dict:
    if account_balance is None:
        import MetaTrader5 as mt5
        acc = mt5.account_info()
        if acc is None:
            return {"error": True, "message": "Cannot get account info", "data": None}
        account_balance = acc.balance
    risk_amount = account_balance * (risk_percent / 100)
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return {"error": True, "message": f"Symbol '{symbol}' not found", "data": None}
    tick_value = symbol_info.trade_tick_value
    tick_size = symbol_info.trade_tick_size
    if tick_size == 0 or tick_value == 0:
        return {"error": True, "message": "Cannot calculate lot size — tick size or value is zero", "data": None}
    pip_value_per_lot = tick_value * (stop_loss_pips * 10 * tick_size / tick_size) if symbol_info.digits in [5, 3] else tick_value * stop_loss_pips
    pip_value_per_lot = tick_value * (stop_loss_pips / (tick_size * 10)) if symbol_info.digits >= 5 else tick_value * (stop_loss_pips / tick_size)
    risk_per_pip = risk_amount / stop_loss_pips if stop_loss_pips > 0 else risk_amount
    lot_step = symbol_info.volume_step
    min_lot = symbol_info.volume_min
    max_lot = symbol_info.volume_max
    raw_lot_size = risk_amount / max(pip_value_per_lot * stop_loss_pips, 0.001)
    lot_size = max(min_lot, round(raw_lot_size / lot_step) * lot_step)
    lot_size = min(lot_size, max_lot)
    return {
        "error": False,
        "message": f"Lot size calculated: {lot_size}",
        "data": {
            "symbol": symbol,
            "risk_percent": risk_percent,
            "risk_amount_usd": round(risk_amount, 2),
            "stop_loss_pips": stop_loss_pips,
            "lot_size": lot_size,
            "min_lot": min_lot,
            "max_lot": max_lot,
            "account_balance": round(account_balance, 2),
        }
    }


def calculate_kelly_size(win_rate: float, avg_win: float, avg_loss: float, bankroll: float, kelly_fraction: float = 0.25) -> dict:
    if avg_loss == 0:
        return {"error": True, "message": "Average loss cannot be zero", "data": None}
    r = abs(avg_win / avg_loss)
    kelly_pct = (win_rate - (1 - win_rate) / r) if r > 0 else 0
    kelly_pct = max(0, kelly_pct)
    fraction = kelly_pct * kelly_fraction
    bet_size = bankroll * fraction
    return {
        "error": False,
        "message": f"Kelly recommends {fraction*100:.1f}% of bankroll",
        "data": {
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "r_multiple": round(r, 2),
            "full_kelly_pct": round(kelly_pct * 100, 1),
            "kelly_fraction": kelly_fraction,
            "recommended_pct": round(fraction * 100, 1),
            "recommended_amount_usd": round(bet_size, 2),
            "bankroll": bankroll,
        }
    }


def calculate_optimal_f(trades: list) -> dict:
    profits = [t for t in trades if t > 0]
    losses = [t for t in trades if t < 0]
    if not profits or not losses:
        return {"error": True, "message": "Need both winning and losing trades", "data": None}
    best_f = 0
    best_twr = -999999
    for f in [i / 100 for i in range(1, 100)]:
        twr = 1.0
        for t in trades:
            if t > 0:
                twr *= (1 + f * abs(t / max(losses)))
            else:
                twr *= (1 - f * abs(t / max(losses)))
        if twr > best_twr:
            best_twr = twr
            best_f = f
    return {
        "error": False,
        "message": f"Optimal f = {best_f:.2f}",
        "data": {
            "optimal_f": round(best_f, 3),
            "optimal_f_pct": round(best_f * 100, 1),
            "total_trades": len(trades),
            "winning_trades": len(profits),
            "losing_trades": len(losses),
            "win_rate": round(len(profits) / max(len(trades), 1), 3),
        }
    }


def calculate_max_drawdown(equity_curve: list) -> dict:
    if not equity_curve:
        return {"error": True, "message": "Empty equity curve", "data": None}
    peak = equity_curve[0]
    max_dd = 0
    max_dd_pct = 0
    dd_start = 0
    dd_end = 0
    current_dd_start = 0
    for i, val in enumerate(equity_curve):
        if val > peak:
            peak = val
            current_dd_start = i
        dd = peak - val
        dd_pct = dd / peak * 100 if peak > 0 else 0
        if dd_pct > max_dd_pct:
            max_dd = dd
            max_dd_pct = dd_pct
            dd_start = current_dd_start
            dd_end = i
    return {
        "error": False,
        "message": f"Max drawdown: {max_dd_pct:.1f}%",
        "data": {
            "max_drawdown_abs": round(max_dd, 2),
            "max_drawdown_pct": round(max_dd_pct, 1),
            "peak_value": round(peak, 2),
            "peak_index": dd_start,
            "trough_index": dd_end,
            "drawdown_duration": dd_end - dd_start if dd_end > dd_start else 0,
        }
    }


def calculate_sharpe_ratio(returns: list, risk_free_rate: float = 0.02) -> dict:
    if len(returns) < 2:
        return {"error": True, "message": "Need at least 2 returns", "data": None}
    import statistics
    mean_ret = statistics.mean(returns)
    std_ret = statistics.stdev(returns)
    if std_ret == 0:
        return {"error": True, "message": "Zero standard deviation", "data": None}
    sharpe = (mean_ret - risk_free_rate / 252) / std_ret * math.sqrt(252)
    return {
        "error": False,
        "message": f"Sharpe ratio: {sharpe:.2f}",
        "data": {"sharpe_ratio": round(sharpe, 2), "annualized_return": round(mean_ret * 252 * 100, 2), "annualized_vol": round(std_ret * math.sqrt(252) * 100, 2)}
    }


def calculate_sortino_ratio(returns: list, risk_free_rate: float = 0.02) -> dict:
    if len(returns) < 2:
        return {"error": True, "message": "Need at least 2 returns", "data": None}
    import statistics
    mean_ret = statistics.mean(returns)
    downside = [r for r in returns if r < 0]
    if len(downside) == 0:
        return {"error": False, "message": "No downside — Sortino is infinite", "data": {"sortino_ratio": None, "note": "No losing periods"}}
    downside_std = statistics.stdev([0] + downside) if len(downside) == 1 else statistics.stdev(downside)
    if downside_std == 0:
        return {"error": True, "message": "Zero downside deviation", "data": None}
    sortino = (mean_ret - risk_free_rate / 252) / downside_std * math.sqrt(252)
    return {
        "error": False,
        "message": f"Sortino ratio: {sortino:.2f}",
        "data": {"sortino_ratio": round(sortino, 2), "downside_deviation": round(downside_std * math.sqrt(252) * 100, 2)}
    }
