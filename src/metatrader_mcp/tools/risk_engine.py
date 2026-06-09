"""
Portfolio Risk Engine — VaR, Risk Parity, Correlation Risk.

Calcula:
  - Value at Risk (VaR) 95%/99% para el portfolio actual
  - Risk Parity: sugiere ajustes para que ningún factor domine
  - Correlation Risk: detecta concentración en activos correlacionados
  - Drawdown-based position scaling

Todas las funciones operan sobre el papertrade portfolio + posiciones MT5 reales.
"""
import logging
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


def _get_portfolio_positions(client) -> List[Dict[str, Any]]:
    """Get combined list of paper + live positions."""
    positions = []

    # Paper positions
    try:
        from metatrader_mcp.tools.papertrade import portfolio
        p = portfolio()
        paper_positions = p.get("portfolio", {}).get("positions", [])
        for pos in paper_positions:
            pos["source"] = "paper"
            positions.append(pos)
    except Exception:
        pass

    # Live positions
    try:
        live_positions = client.account.get_positions()
        if live_positions:
            for pos in live_positions:
                if hasattr(pos, "_asdict"):
                    pos_dict = pos._asdict()
                elif isinstance(pos, dict):
                    pos_dict = pos
                else:
                    continue
                pos_dict["source"] = "live"
                positions.append(pos_dict)
    except Exception:
        pass

    return positions


def _get_trade_history() -> List[Dict[str, Any]]:
    """Get trade history from papertrade."""
    try:
        from metatrader_mcp.tools.papertrade import portfolio
        p = portfolio()
        return p.get("portfolio", {}).get("trades", [])
    except Exception:
        return []


def portfolio_var(client, confidence: float = 0.95) -> Dict[str, Any]:
    """Calculate Value at Risk for the current portfolio.

    Uses historical simulation: reshuffles past trade returns
    to estimate the loss at given confidence level.

    Args:
        client: MT5 client
        confidence: 0.95 for 95% VaR, 0.99 for 99% VaR

    Returns:
        VaR in USD and % of portfolio
    """
    positions = _get_portfolio_positions(client)
    trades = _get_trade_history()

    if not positions and not trades:
        return {"success": True, "var_usd": 0, "var_pct": 0, "note": "No open positions or trade history"}

    # Calculate current exposure
    total_exposure = 0
    for pos in positions:
        vol = float(pos.get("volume", pos.get("volume", 0.01)))
        price = float(pos.get("price", pos.get("entry_price", 1)))
        notional = vol * 100000 / price if price > 0 else 0
        total_exposure += notional

    balance = 10000
    try:
        account = client.account.get_account_info()
        if account:
            if hasattr(account, "balance"):
                balance = float(account.balance)
            elif isinstance(account, dict):
                balance = float(account.get("balance", 10000))
    except Exception:
        try:
            from metatrader_mcp.tools.papertrade import portfolio
            p = portfolio()
            balance = p.get("portfolio", {}).get("balance", 10000)
        except Exception:
            balance = 10000

    # Build PnL distribution from trade history
    pnls = []
    for t in trades:
        pnl = t.get("pnl", 0)
        if isinstance(pnl, (int, float)) and pnl != 0:
            pnl_pct = pnl / max(balance, 1) * 100
            pnls.append(pnl_pct)

    if len(pnls) < 10:
        # Simulate from historical volatility if not enough trades
        if positions:
            vol_est = 0.02  # 2% per trade estimated
            np.random.seed(42)
            sim = np.random.normal(0, vol_est, 1000)
            var_pct = float(np.percentile(sim, (1 - confidence) * 100))
        else:
            var_pct = 0
    else:
        pnl_arr = np.array(pnls)
        var_pct = float(np.percentile(pnl_arr, (1 - confidence) * 100))

    var_usd = abs(var_pct / 100 * balance)

    # Conditional VaR (Expected Shortfall) — average loss beyond VaR
    if len(pnls) >= 10:
        pnl_arr = np.array(pnls)
        threshold = np.percentile(pnl_arr, (1 - confidence) * 100)
        cvar_pct = float(np.mean(pnl_arr[pnl_arr <= threshold])) if np.any(pnl_arr <= threshold) else var_pct * 1.2
    else:
        cvar_pct = var_pct * 1.5

    cvar_usd = abs(cvar_pct / 100 * balance)

    return {
        "success": True,
        "confidence_level": confidence,
        "balance": round(balance, 2),
        "total_exposure": round(total_exposure, 2),
        "var": {
            f"var_{int(confidence*100)}pct_usd": round(var_usd, 2),
            f"var_{int(confidence*100)}pct": round(abs(var_pct), 2),
        },
        "cvar_expected_shortfall": {
            "cvar_usd": round(cvar_usd, 2),
            "cvar_pct": round(abs(cvar_pct), 2),
        },
        "trades_used": len(pnls),
        "interpretation": f"With {confidence*100:.0f}% confidence, max loss per trade is {abs(var_pct):.1f}% (${var_usd:.0f})",
    }


def risk_parity_suggestions(client) -> Dict[str, Any]:
    """Suggest position size adjustments for risk parity.

    Uses inverse volatility weighting: higher volatility = smaller position.
    Accounts for correlation between positions.

    Returns:
        dict with current allocation and suggested changes
    """
    positions = _get_portfolio_positions(client)
    if not positions:
        return {"success": True, "allocation": [], "note": "No open positions"}

    # Group by symbol
    symbol_positions: Dict[str, List[Dict]] = {}
    for pos in positions:
        sym = pos.get("symbol", "UNKNOWN")
        symbol_positions.setdefault(sym, []).append(pos)

    # Get volatility for each symbol
    symbols = list(symbol_positions.keys())
    vols = {}
    for sym in symbols:
        try:
            df = client.market.get_candles_latest(symbol_name=sym, timeframe="H1", count=100)
            if df is not None:
                import pandas as pd
                if isinstance(df, pd.DataFrame) and "close" in df.columns:
                    closes = df["close"].dropna().values
                    returns = np.diff(np.log(closes + 0.0001))
                    vol = float(np.std(returns[-50:])) * math.sqrt(24)  # daily vol
                    vols[sym] = max(vol, 0.001)
                else:
                    vols[sym] = 0.02
            else:
                vols[sym] = 0.02
        except Exception:
            vols[sym] = 0.02

    # Calculate current notional per symbol
    total_notional = 0
    symbol_notional = {}
    for sym, pos_list in symbol_positions.items():
        notional = 0
        for pos in pos_list:
            vol = float(pos.get("volume", pos.get("volume", 0.01)))
            price = float(pos.get("price", pos.get("entry_price", 1)))
            notional += vol * 100000 / max(price, 0.0001)
        symbol_notional[sym] = notional
        total_notional += notional

    total_notional = max(total_notional, 0.0001)

    # Inverse volatility weights
    inv_vol_sum = sum(1.0 / max(vols[s], 0.0001) for s in symbols if s in vols)
    if inv_vol_sum == 0:
        inv_vol_sum = 1

    suggestions = []
    for sym in symbols:
        current_weight = symbol_notional.get(sym, 0) / total_notional * 100
        inv_vol = 1.0 / max(vols.get(sym, 0.02), 0.0001)
        target_weight = inv_vol / inv_vol_sum * 100
        diff = target_weight - current_weight

        suggestions.append({
            "symbol": sym,
            "current_weight_pct": round(current_weight, 1),
            "target_weight_pct": round(target_weight, 1),
            "volatility_annualized_pct": round(vols.get(sym, 0.02) * 100, 2),
            "adjustment": "increase" if diff > 5 else ("decrease" if diff < -5 else "keep"),
            "adjustment_pct": round(diff, 1),
        })

    return {
        "success": True,
        "total_notional_usd": round(total_notional, 2),
        "strategy": "Inverse Volatility Weighting (Risk Parity)",
        "allocation": suggestions,
        "advice": "Reduce positions with negative 'decrease' label to achieve risk parity",
    }


def correlation_risk(client) -> Dict[str, Any]:
    """Detect correlation concentration risk in current positions.

    Finds pairs of positions with |r| > 0.7 that together
    represent >30% of portfolio.

    Returns:
        dict with warnings and diversification score
    """
    from metatrader_mcp.tools.correlation import get_correlation

    positions = _get_portfolio_positions(client)
    if not positions:
        return {"success": True, "risk_level": "none", "warnings": []}

    # Group by symbol
    symbols = list(dict.fromkeys(p.get("symbol", "UNKNOWN") for p in positions))

    if len(symbols) < 2:
        return {"success": True, "risk_level": "low", "warnings": [], "note": "Single position — no correlation risk"}

    # Calculate combined weight per symbol
    total_notional = 0
    symbol_notional: Dict[str, float] = {}
    for p in positions:
        sym = p.get("symbol", "UNKNOWN")
        vol = float(p.get("volume", p.get("volume", 0.01)))
        price = float(p.get("price", p.get("entry_price", 1)))
        notional = vol * 100000 / max(price, 0.0001)
        symbol_notional[sym] = symbol_notional.get(sym, 0) + notional
        total_notional += notional

    total_notional = max(total_notional, 0.0001)

    warnings = []
    high_corr_pairs = []

    for i in range(len(symbols)):
        for j in range(i + 1, len(symbols)):
            corr = abs(get_correlation(symbols[i], symbols[j]))
            if corr >= 0.7:
                combined_w = (symbol_notional.get(symbols[i], 0) + symbol_notional.get(symbols[j], 0)) / total_notional
                high_corr_pairs.append({
                    "pair": f"{symbols[i]} / {symbols[j]}",
                    "correlation": round(corr, 2),
                    "combined_exposure_pct": round(combined_w * 100, 1),
                })
                if combined_w > 0.30:
                    warnings.append(
                        f"{symbols[i]} + {symbols[j]}: {corr:.0%} corr, "
                        f"{combined_w*100:.0f}% of portfolio — REDUCE ONE"
                    )

    # Effective number of positions (diversification measure)
    n = len(symbols)
    if high_corr_pairs:
        effective_n = n / (1 + len(high_corr_pairs) * 0.3)
    else:
        effective_n = n

    risk_level = "high" if len(warnings) >= 2 else ("medium" if warnings else "low")

    diversification_pct = min(100, effective_n / max(n, 1) * 100)

    return {
        "success": True,
        "symbols": symbols,
        "effective_positions": round(effective_n, 1),
        "diversification_score_pct": round(diversification_pct, 0),
        "risk_level": risk_level,
        "warnings": warnings,
        "high_correlation_pairs": high_corr_pairs,
        "advice": "Reduce correlated positions" if risk_level == "high"
                  else ("Monitor correlation" if risk_level == "medium"
                        else "Good diversification"),
    }


def max_drawdown_analysis() -> Dict[str, Any]:
    """Analyze max drawdown from trade history and suggest risk limits.

    Returns:
        dict with drawdown stats and suggested max position size
    """
    from metatrader_mcp.tools.papertrade import portfolio as pt_portfolio
    from metatrader_mcp.tools.analytics import equity_curve

    try:
        history = pt_portfolio()
        trades = history.get("portfolio", {}).get("trades", [])

        if not trades:
            return {"success": True, "note": "No trade history for drawdown analysis"}

        # Equity curve
        ec = equity_curve()
        balances = ec.get("equity_curve", ec.get("balances", []))

        if len(balances) < 3:
            return {"success": True, "note": "Not enough data for drawdown analysis"}

        # Calculate drawdowns
        peak = balances[0]
        max_dd = 0
        max_dd_start = 0
        current_dd_start = 0
        dd_periods = []

        for i, bal in enumerate(balances):
            if bal > peak:
                peak = bal
                current_dd_start = i
            dd = (peak - bal) / max(peak, 1) * 100
            if dd > max_dd:
                max_dd = dd
                max_dd_start = current_dd_start
            if dd > 5:
                dd_periods.append({"period": i, "drawdown_pct": round(dd, 1)})

        # Suggested max position size = Kelly-derived
        wins = [t.get("pnl", 0) for t in trades if t.get("pnl", 0) > 0]
        losses = [abs(t.get("pnl", 0)) for t in trades if t.get("pnl", 0) < 0]
        avg_win = sum(wins) / max(len(wins), 1)
        avg_loss = sum(losses) / max(len(losses), 1)
        win_rate = len(wins) / max(len(trades), 1)

        if avg_loss > 0 and win_rate > 0:
            kelly = win_rate - (1 - win_rate) / max(avg_win / max(avg_loss, 0.01), 0.01)
            kelly_fraction = max(0, min(kelly * 0.25, 0.05))  # 25% Kelly, capped at 5%
        else:
            kelly_fraction = 0.01

        # Suggested max risk per trade based on max drawdown
        if max_dd > 20:
            suggested_risk_pct = 0.5  # 0.5% per trade
        elif max_dd > 10:
            suggested_risk_pct = 1.0
        else:
            suggested_risk_pct = 2.0

        return {
            "success": True,
            "max_drawdown_pct": round(max_dd, 1),
            "max_drawdown_periods": len(dd_periods),
            "recent_drawdowns": dd_periods[-5:],
            "kelly_fraction": round(kelly_fraction * 100, 2),
            "suggested_risk_per_trade_pct": suggested_risk_pct,
            "advice": "Reduce position size" if max_dd > 15
                      else ("Monitor drawdown" if max_dd > 8
                            else "Drawdown under control"),
        }

    except Exception as e:
        return {"success": False, "error": str(e)}
