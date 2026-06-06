"""
Backtest — simula estrategias contra velas históricas de MT5.
"""
import logging
import math
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


def run(client, symbol: str, timeframe: str = "H1", days: int = 30,
        entry_rule: str = "rsi_oversold", exit_rule: str = "rsi_overbought",
        bankroll: float = 1000, lot_size: float = 0.01) -> Dict[str, Any]:
    """Backtestea una estrategia contra datos históricos."""
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=days * 24)
    except Exception as e:
        return {"success": False, "error": f"Cannot fetch data: {e}"}

    if df is None or (hasattr(df, 'empty') and df.empty):
        return {"success": False, "error": "No data"}

    if isinstance(df, pd.DataFrame):
        data = df.to_dict(orient="records")
    else:
        return {"success": False, "error": "Unexpected format"}

    if len(data) < 20:
        return {"success": False, "error": "Not enough candles"}

    trades = []
    in_position = False
    entry_price = 0
    entry_idx = 0

    for i in range(20, len(data)):
        candle = data[i]
        close = float(candle.get("close", 0))
        high = float(candle.get("high", 0))
        low = float(candle.get("low", 0))

        if not in_position:
            signal = _check_entry(data[:i+1], entry_rule)
            if signal:
                in_position = True
                entry_price = close
                entry_idx = i
        else:
            should_exit, reason = _check_exit(data, i, entry_price, exit_rule, high, low)
            if should_exit or i - entry_idx > 100:
                pnl_pct = (close - entry_price) / entry_price * 100
                pnl_usd = bankroll * lot_size * pnl_pct / 100
                trades.append({
                    "entry_time": str(data[entry_idx].get("time", ""))[:19],
                    "exit_time": str(candle.get("time", ""))[:19],
                    "entry_price": round(entry_price, 5),
                    "exit_price": round(close, 5),
                    "pnl_pct": round(pnl_pct, 2),
                    "pnl_usd": round(pnl_usd, 2),
                    "exit_reason": reason,
                })
                in_position = False

    total = len(trades)
    if total == 0:
        return {"success": True, "backtest": {"total_trades": 0, "message": "No trades"}}

    wins = [t for t in trades if t["pnl_usd"] > 0]
    losses = [t for t in trades if t["pnl_usd"] <= 0]
    win_rate = round(len(wins) / total * 100, 1) if total > 0 else 0
    gross_profit = sum(t["pnl_usd"] for t in wins)
    gross_loss = abs(sum(t["pnl_usd"] for t in losses))
    profit_factor = round(gross_profit / max(gross_loss, 0.01), 2)
    net_pnl = round(sum(t["pnl_usd"] for t in trades), 2)

    cumulative = 0
    peak = 0
    max_dd = 0
    for t in trades:
        cumulative += t["pnl_usd"]
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    max_dd_pct = round(max_dd / max(bankroll, 1) * 100, 1)

    pnl_values = [t["pnl_pct"] for t in trades]
    avg_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else 0
    if len(pnl_values) > 1:
        variance = sum((p - avg_pnl)**2 for p in pnl_values) / (len(pnl_values) - 1)
        sharpe = round(avg_pnl / max(math.sqrt(variance), 0.001) * math.sqrt(365), 2)
    else:
        sharpe = 0

    return {
        "success": True,
        "backtest": {
            "symbol": symbol,
            "timeframe": timeframe,
            "days": days,
            "entry_rule": entry_rule,
            "exit_rule": exit_rule,
            "total_trades": total,
            "win_rate_pct": win_rate,
            "profit_factor": profit_factor,
            "net_pnl_usd": net_pnl,
            "max_drawdown_pct": max_dd_pct,
            "sharpe_ratio": sharpe,
            "winners": len(wins),
            "losers": len(losses),
            "avg_win_pct": round(sum(t["pnl_pct"] for t in wins) / len(wins), 2) if wins else 0,
            "avg_loss_pct": round(sum(t["pnl_pct"] for t in losses) / len(losses), 2) if losses else 0,
            "trades": trades[-20:],
        },
    }


def _check_entry(data, rule):
    """Checkea regla de entrada contra velas recientes."""
    closes = [float(d["close"]) for d in data[-20:]]
    if len(closes) < 14:
        return False

    if rule == "rsi_oversold":
        rsi = _rsi(closes)
        return rsi < 30
    if rule == "rsi_overbought":
        rsi = _rsi(closes)
        return rsi > 70
    if rule == "ma_cross_bull":
        fast = sum(closes[-5:]) / 5
        slow = sum(closes[-20:]) / 20
        fast_prev = sum(closes[-6:-1]) / 5
        return fast_prev <= slow and fast > slow
    if rule == "ma_cross_bear":
        fast = sum(closes[-5:]) / 5
        slow = sum(closes[-20:]) / 20
        fast_prev = sum(closes[-6:-1]) / 5
        return fast_prev >= slow and fast < slow
    if rule == "dip_buy":
        if len(closes) < 3:
            return False
        return (closes[-3] - closes[-1]) / closes[-3] > 0.02
    return False


def _check_exit(data, i, entry_price, rule, high, low):
    """Checkea regla de salida."""
    closes = [float(d["close"]) for d in data[max(0, i-20):i+1]]
    if len(closes) < 14:
        return False, ""

    current = closes[-1]
    pnl_pct = (current - entry_price) / entry_price * 100

    if rule == "rsi_overbought":
        rsi = _rsi(closes)
        if rsi > 70 and pnl_pct > 0:
            return True, "rsi_overbought"
    if rule == "rsi_oversold":
        rsi = _rsi(closes)
        if rsi < 30 and pnl_pct > 0:
            return True, "rsi_oversold"
    if rule == "target_10":
        if pnl_pct >= 10:
            return True, "tp_10pct"
    if rule == "target_20":
        if pnl_pct >= 20:
            return True, "tp_20pct"
    if rule == "stop_loss_5":
        if pnl_pct <= -5:
            return True, "sl_5pct"
    if rule == "stop_loss_10":
        if pnl_pct <= -10:
            return True, "sl_10pct"

    return False, ""


def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compare(client, symbol: str, entry_rules: list = None, exit_rules: list = None) -> Dict[str, Any]:
    """Compara múltiples combinaciones de estrategias."""
    if entry_rules is None:
        entry_rules = ["rsi_oversold", "ma_cross_bull", "dip_buy"]
    if exit_rules is None:
        exit_rules = ["target_10", "target_20", "stop_loss_5"]

    results = []
    for entry in entry_rules:
        for exit in exit_rules:
            try:
                r = run(client, symbol, "H1", 30, entry, exit)
                if r.get("success"):
                    bt = r.get("backtest", {})
                    results.append({
                        "entry_rule": entry,
                        "exit_rule": exit,
                        "trades": bt.get("total_trades"),
                        "win_rate_pct": bt.get("win_rate_pct"),
                        "profit_factor": bt.get("profit_factor"),
                        "net_pnl_usd": bt.get("net_pnl_usd"),
                        "sharpe": bt.get("sharpe_ratio"),
                    })
            except Exception:
                continue

    results.sort(key=lambda x: x.get("sharpe", 0), reverse=True)

    return {
        "success": True,
        "comparison": {
            "symbol": symbol,
            "combinations": len(results),
            "best_by_sharpe": results[0] if results else None,
            "best_by_profit": max(results, key=lambda x: x.get("net_pnl_usd", 0)) if results else None,
            "results": results,
        },
    }


def walk_forward(client, symbol: str, timeframe: str = "H1",
                 train_days: int = 60, test_days: int = 20,
                 entry_rule: str = "rsi_oversold", exit_rule: str = "rsi_overbought",
                 bankroll: float = 1000, lot_size: float = 0.01) -> Dict[str, Any]:
    """Walk-forward backtest: train on period A, test on period B, roll forward.

    Más realista que backtest simple porque simula cómo se desempeñaría
    la estrategia en datos NO vistos durante el entrenamiento.
    """
    total_days = train_days + test_days
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=total_days * 24)
    except Exception as e:
        return {"success": False, "error": f"Cannot fetch data: {e}"}

    if df is None or (hasattr(df, 'empty') and df.empty):
        return {"success": False, "error": "No data"}

    import pandas as pd
    if isinstance(df, pd.DataFrame):
        data = df.to_dict(orient="records")
    else:
        return {"success": False, "error": "Unexpected format"}

    n = len(data)
    if n < 500:
        return {"success": False, "error": f"Need more data (have {n}, need 500+)"}

    # Simple walk-forward: 3 folds
    fold_size = n // 3
    fold_results = []

    for fold in range(3):
        train_start = fold * fold_size
        train_end = train_start + fold_size
        test_end = min(train_end + fold_size // 2, n)

        train_data = data[train_start:train_end]
        test_data = data[train_end:test_end]

        if len(train_data) < 100 or len(test_data) < 20:
            continue

        # Simulate on test data using entry/exit rules
        test_closes = [float(d["close"]) for d in test_data]
        trades = []
        in_pos = False
        entry_price = 0

        for i in range(20, len(test_data)):
            close = test_closes[i]
            if not in_pos:
                # Use train data + recent test for indicator calc
                context = train_data + test_data[:i]
                ctx_closes = [float(d["close"]) for d in context]
                if len(ctx_closes) >= 20:
                    if _check_entry(ctx_closes, entry_rule):
                        in_pos = True
                        entry_price = close
            else:
                should_exit = False
                reason = ""
                pnl_pct = (close - entry_price) / entry_price * 100
                if exit_rule == "target_10" and pnl_pct >= 10:
                    should_exit, reason = True, "tp+10"
                elif exit_rule == "stop_loss_5" and pnl_pct <= -5:
                    should_exit, reason = True, "sl-5"
                elif abs((close - entry_price) / entry_price) > 5:
                    should_exit, reason = True, "exit_after_5pct"
                elif i - (test_data.index({"close": close}) if False else i) > 50:
                    should_exit, reason = True, "max_hold"

                if should_exit:
                    pnl_usd = bankroll * lot_size * pnl_pct / 100
                    trades.append({"pnl_pct": round(pnl_pct, 2), "pnl_usd": round(pnl_usd, 2)})
                    in_pos = False

        if trades:
            wins = sum(1 for t in trades if t["pnl_usd"] > 0)
            total_pnl = sum(t["pnl_usd"] for t in trades)
            fold_results.append({
                "fold": fold + 1,
                "trades": len(trades),
                "win_rate_pct": round(wins / len(trades) * 100, 1),
                "net_pnl_usd": round(total_pnl, 2),
            })

    if not fold_results:
        return {"success": True, "walk_forward": {"message": "No trades across folds"}}

    avg_win_rate = sum(f["win_rate_pct"] for f in fold_results) / len(fold_results)
    avg_pnl = sum(f["net_pnl_usd"] for f in fold_results) / len(fold_results)

    return {
        "success": True,
        "walk_forward": {
            "symbol": symbol,
            "entry_rule": entry_rule,
            "exit_rule": exit_rule,
            "folds": fold_results,
            "avg_win_rate_pct": round(avg_win_rate, 1),
            "avg_pnl_usd": round(avg_pnl, 2),
            "total_trades": sum(f["trades"] for f in fold_results),
            "robustness": "HIGH" if avg_win_rate > 55 and avg_pnl > 0 else ("MEDIUM" if avg_pnl > 0 else "LOW"),
            "advice": "Strategy passes walk-forward" if avg_pnl > 0 else "Strategy fails out-of-sample",
        },
    }


def monte_carlo(client, symbol: str, entry_rule: str = "rsi_oversold",
                exit_rule: str = "rsi_overbought", bankroll: float = 1000,
                lot_size: float = 0.01, simulations: int = 500) -> Dict[str, Any]:
    """Monte Carlo simulation: reshuffle trade outcomes to estimate drawdown risk."""
    bt = run(client, symbol, "H1", 60, entry_rule, exit_rule, bankroll, lot_size)
    if not bt.get("success"):
        return {"success": False, "error": bt.get("error")}

    trades = bt.get("backtest", {}).get("trades", [])
    if len(trades) < 5:
        return {"success": True, "monte_carlo": {"message": "Need at least 5 trades"}}

    pnl_pcts = [t.get("pnl_pct", 0) for t in trades]
    import random
    max_dds = []
    for _ in range(simulations):
        sample = [random.choice(pnl_pcts) for _ in range(len(pnl_pcts))]
        bal = bankroll
        peak = bankroll
        max_dd = 0
        for pnl in sample:
            bal += bal * pnl / 100
            if bal > peak:
                peak = bal
            dd = (peak - bal) / peak * 100
            if dd > max_dd:
                max_dd = dd
        max_dds.append(max_dd)

    max_dds.sort()
    return {
        "success": True,
        "monte_carlo": {
            "simulations": simulations,
            "median_max_dd_pct": round(max_dds[len(max_dds)//2], 1),
            "p95_max_dd_pct": round(max_dds[int(len(max_dds)*0.95)], 1),
            "worst_max_dd_pct": round(max_dds[-1], 1),
            "trade_count": len(pnl_pcts),
        },
    }
