"""
Analytics — análisis de rendimiento de trading.

Incluye:
  - Equity curve
  - Sharpe, Sortino, Calmar ratios
  - Monte Carlo simulation (drawdown risk)
  - Trade journal statistics
  - Performance by day/week/symbol
"""
import logging
import math
import random
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


def _load_trades():
    """Load papertrade trades for analysis."""
    try:
        from .papertrade import portfolio as pt_portfolio
        p = pt_portfolio()
        return p.get("portfolio", {}).get("trades", [])
    except Exception:
        return []


def equity_curve(trades: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """Compute equity curve from trades."""
    if trades is None:
        trades = _load_trades()

    if not trades:
        return {"success": True, "equity": [], "total_pnl": 0, "message": "No trades"}

    sorted_trades = sorted(trades, key=lambda t: t.get("closed", t.get("opened", "")))
    curve = []
    balance = 10000  # starting balance estimate
    peak = balance
    max_dd = 0
    max_dd_pct = 0

    for t in sorted_trades:
        pnl = t.get("pnl_usd", 0)
        balance += pnl
        if balance > peak:
            peak = balance
        dd = peak - balance
        dd_pct = dd / peak * 100 if peak > 0 else 0
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct
            max_dd = dd

        curve.append({
            "timestamp": t.get("closed", t.get("opened", "")),
            "pnl_usd": round(pnl, 2),
            "balance": round(balance, 2),
            "drawdown_pct": round(dd_pct, 2),
        })

    total_pnl = sum(t.get("pnl_usd", 0) for t in sorted_trades)
    return {
        "success": True,
        "equity": curve[-100:],  # last 100 points
        "total_pnl": round(total_pnl, 2),
        "final_balance": round(balance, 2),
        "peak_balance": round(peak, 2),
        "max_drawdown_usd": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 1),
    }


def performance_ratios(trades: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """Calculate Sharpe, Sortino, Calmar ratios."""
    if trades is None:
        trades = _load_trades()

    if not trades:
        return {"success": True, "ratios": {}, "message": "No trades"}

    pnl_values = [t.get("pnl_pct", 0) for t in trades if t.get("pnl_pct") is not None]
    if len(pnl_values) < 3:
        return {"success": True, "ratios": {}, "message": "Need at least 3 trades"}

    n = len(pnl_values)
    avg_return = sum(pnl_values) / n
    variance = sum((r - avg_return)**2 for r in pnl_values) / (n - 1)
    std = math.sqrt(variance) if variance > 0 else 0.001
    neg_returns = [r for r in pnl_values if r < 0]
    downside_var = sum(r**2 for r in neg_returns) / n if neg_returns else 0.001
    downside_std = math.sqrt(downside_var)

    # Sharpe (assuming risk-free = 0, annualized for daily-ish trades)
    sharpe = round(avg_return / std * math.sqrt(365), 2) if std > 0 else 0

    # Sortino
    sortino = round(avg_return / downside_std * math.sqrt(365), 2) if downside_std > 0 else 0

    # Calmar
    eq = equity_curve(trades)
    max_dd_pct = eq.get("max_drawdown_pct", 1)
    total_pnl = sum(t.get("pnl_pct", 0) for t in trades)
    calmar = round(total_pnl / max(max_dd_pct, 0.1), 2) if max_dd_pct > 0 else 0

    # Win rate
    wins = sum(1 for t in trades if t.get("pnl_usd", 0) > 0)
    losses = sum(1 for t in trades if t.get("pnl_usd", 0) <= 0)
    win_rate = wins / n * 100 if n > 0 else 0

    # Avg win / loss
    avg_win = sum(t["pnl_pct"] for t in trades if t.get("pnl_usd", 0) > 0) / max(wins, 1)
    avg_loss = sum(t["pnl_pct"] for t in trades if t.get("pnl_usd", 0) <= 0) / max(losses, 1)
    profit_factor = abs(avg_win / max(avg_loss, 0.01)) if avg_loss != 0 else float("inf")

    return {
        "success": True,
        "ratios": {
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "calmar_ratio": calmar,
            "win_rate_pct": round(win_rate, 1),
            "profit_factor": round(profit_factor, 2),
            "avg_win_pct": round(avg_win, 2),
            "avg_loss_pct": round(avg_loss, 2),
            "total_trades": n,
        },
    }


def monte_carlo_drawdown(trades: Optional[List[Dict]] = None,
                          simulations: int = 1000, horizon_trades: int = 100) -> Dict[str, Any]:
    """Monte Carlo simulation of drawdown risk.

    Shuffles trade outcomes and simulates N possible futures.
    Returns probability of exceeding various drawdown levels.
    """
    if trades is None:
        trades = _load_trades()

    if not trades:
        return {"success": True, "monte_carlo": {}, "message": "No trades"}

    pnl_pcts = [t.get("pnl_pct", 0) for t in trades if t.get("pnl_pct") is not None]
    if len(pnl_pcts) < 5:
        return {"success": True, "monte_carlo": {}, "message": "Need at least 5 trades"}

    max_drawdowns = []
    for _ in range(simulations):
        sample = [random.choice(pnl_pcts) for _ in range(horizon_trades)]
        balance = 10000
        peak = 10000
        max_dd = 0
        for pnl in sample:
            balance *= (1 + pnl / 100)
            if balance > peak:
                peak = balance
            dd = (peak - balance) / peak * 100
            if dd > max_dd:
                max_dd = dd
        max_drawdowns.append(max_dd)

    max_drawdowns.sort()

    # Probability of exceeding thresholds
    thresholds = [5, 10, 15, 20, 25, 30, 40, 50]
    probs = {}
    for t in thresholds:
        count = sum(1 for dd in max_drawdowns if dd > t)
        probs[f"pct_dd_gt_{t}"] = round(count / simulations * 100, 1)

    # VaR (Value at Risk) at 95% and 99%
    var_95 = max_drawdowns[int(simulations * 0.95)] if simulations >= 20 else 0
    var_99 = max_drawdowns[int(simulations * 0.99)] if simulations >= 100 else 0

    return {
        "success": True,
        "monte_carlo_drawdown": {
            "simulations": simulations,
            "horizon_trades": horizon_trades,
            "max_drawdown_50pct": round(max_drawdowns[int(simulations * 0.5)], 1),
            "max_drawdown_95pct": round(max_drawdowns[int(simulations * 0.95)], 1),
            "max_drawdown_max": round(max_drawdowns[-1], 1),
            "var_95": round(var_95, 1),
            "var_99": round(var_99, 1),
            "probabilities": probs,
            "advice": "Reduce position size" if var_95 > 20 else "Risk profile acceptable",
        },
    }


def full_report(trades: Optional[List[Dict]] = None) -> Dict[str, Any]:
    """Full performance report combining equity curve, ratios, Monte Carlo."""
    if trades is None:
        trades = _load_trades()

    eq = equity_curve(trades)
    ratios = performance_ratios(trades)
    mc = monte_carlo_drawdown(trades)

    # Health score
    score = 50
    r = ratios.get("ratios", {})
    if r.get("sharpe_ratio", 0) > 1:
        score += 10
    if r.get("sharpe_ratio", 0) > 2:
        score += 10
    if r.get("win_rate_pct", 0) > 55:
        score += 10
    if r.get("profit_factor", 0) > 1.5:
        score += 10
    if eq.get("max_drawdown_pct", 100) < 15:
        score += 10

    return {
        "success": True,
        "report": {
            "health_score": min(score, 100),
            "total_trades": len(trades),
            "equity": eq,
            "ratios": r,
            "monte_carlo": mc.get("monte_carlo_drawdown", {}),
        },
    }
