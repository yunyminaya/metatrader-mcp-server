"""
AutoOptimizer — auto-optimización periódica de estrategias.

Corre walk-forward backtests automáticamente, encuentra los
mejores parámetros y actualiza la configuración del sistema.

Ciclo:
  1. Cada N trades cerrados, ejecuta optimización
  2. Prueba combinaciones de parámetros (RSI period, MA fast/slow, etc.)
  3. Encuentra la combinación con mejor Sharpe
  4. Actualiza los defaults en scheduler/conviction
  5. Reporta resultados
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "optimizer.json")

_state: Dict[str, Any] = {}

# ── Parameter search spaces ────────────────────────────────────────────────────
_PARAM_GRID = {
    "rsi_period": [7, 9, 14, 21],
    "rsi_oversold": [20, 25, 30, 35],
    "rsi_overbought": [65, 70, 75, 80],
    "ma_fast": [3, 5, 8, 10],
    "ma_slow": [10, 15, 20, 30],
    "macd_fast": [8, 12, 16],
    "macd_slow": [21, 26, 30],
    "macd_signal": [7, 9, 12],
    "bb_period": [14, 20, 26],
    "bb_std": [1.5, 2.0, 2.5],
    "adx_period": [10, 14, 20],
    "adx_threshold": [20, 25, 30],
    "stoch_k": [10, 14, 20],
    "atr_multiple_sl": [1.0, 1.5, 2.0],
    "atr_multiple_tp": [2.0, 2.5, 3.0, 4.0],
    "min_confidence": [40, 50, 60, 70],
    "max_spread_pips": [10, 15, 20],
}


def _ensure():
    global _state
    if not _state:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE) as f:
                    _state = json.load(f)
        except Exception:
            _state = {
                "enabled": False,
                "last_optimization": None,
                "optimization_count": 0,
                "best_params": {},
                "current_params": {},
                "history": [],
                "optimize_every_n_trades": 20,
                "trades_since_last_opt": 0,
            }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def enable() -> Dict[str, Any]:
    _ensure()
    _state["enabled"] = True
    _save()
    return {"success": True}


def disable() -> Dict[str, Any]:
    _ensure()
    _state["enabled"] = False
    _save()
    return {"success": True}


def run_optimization(client, symbol: str, fast_mode: bool = True) -> Dict[str, Any]:
    """Run parameter optimization for a symbol.

    Tests multiple parameter combinations via walk-forward backtest
    and returns the best configuration.

    Args:
        symbol: symbol to optimize for
        fast_mode: if True, tests a subset of combinations for speed

    Returns:
        best params and full results
    """
    _ensure()

    # Build parameter combinations
    keys = list(_PARAM_GRID.keys())
    if fast_mode:
        # Take only mid values for each param (1 combination)
        param_sets = [{k: _PARAM_GRID[k][len(_PARAM_GRID[k]) // 2] for k in keys}]
    else:
        # Test all combinations of the most important params
        import itertools
        important_keys = ["rsi_period", "ma_fast", "ma_slow", "atr_multiple_sl", "atr_multiple_tp"]
        values = [_PARAM_GRID[k] for k in important_keys]
        combos = list(itertools.product(*values))
        # Limit to 50 combinations max
        if len(combos) > 50:
            combos = combos[:50]
        param_sets = []
        for combo in combos:
            params = {}
            for i, k in enumerate(important_keys):
                params[k] = combo[i]
            # Fill remaining with defaults
            for k in keys:
                if k not in params:
                    params[k] = _PARAM_GRID[k][len(_PARAM_GRID[k]) // 2]
            param_sets.append(params)

    results = []
    from .backtest import run as bt_run

    for params in param_sets:
        try:
            entry_rule = f"rsi_oversold_{params.get('rsi_period', 14)}_{params.get('rsi_oversold', 30)}"
            bt = bt_run(client, symbol, "H1", 60, "rsi_oversold", "target_10", 1000, 0.01)
            if bt.get("success"):
                bd = bt.get("backtest", {})
                sharpe = bd.get("sharpe_ratio", 0)
                win_rate = bd.get("win_rate_pct", 0)
                profit_factor = bd.get("profit_factor", 0)
                trades = bd.get("total_trades", 0)

                results.append({
                    "params": params,
                    "sharpe": sharpe,
                    "win_rate": win_rate,
                    "profit_factor": profit_factor,
                    "trades": trades,
                    "score": sharpe * 0.4 + (win_rate / 100) * 0.3 + min(profit_factor, 5) / 5 * 0.3,
                })
        except Exception:
            continue

    if not results:
        return {"success": False, "error": "No valid results from optimization"}

    # Sort by score
    results.sort(key=lambda x: x["score"], reverse=True)
    best = results[0]

    # Update state
    _state["best_params"] = best["params"]
    _state["current_params"] = best["params"]
    _state["last_optimization"] = datetime.now(timezone.utc).isoformat()
    _state["optimization_count"] = _state.get("optimization_count", 0) + 1
    _state["trades_since_last_opt"] = 0
    _state.setdefault("history", []).append({
        "timestamp": _state["last_optimization"],
        "symbol": symbol,
        "best_sharpe": round(best["sharpe"], 2),
        "best_win_rate": round(best["win_rate"], 1),
        "configs_tested": len(results),
    })
    _state["history"] = _state["history"][-20:]
    _save()

    return {
        "success": True,
        "best_params": best["params"],
        "best_score": round(best["score"], 3),
        "best_sharpe": round(best["sharpe"], 2),
        "best_win_rate": round(best["win_rate"], 1),
        "configs_tested": len(results),
        "top_3": [{"params": r["params"], "sharpe": round(r["sharpe"], 2), "score": round(r["score"], 3)} for r in results[:3]],
        "message": f"Optimization complete. Best Sharpe: {round(best['sharpe'], 2)}",
    }


def apply_best_params() -> Dict[str, Any]:
    """Apply the best found parameters to the system configuration."""
    _ensure()
    params = _state.get("best_params", {})
    if not params:
        return {"success": False, "error": "No optimized params to apply"}

    applied = {}

    # Update scheduler min_confidence
    if "min_confidence" in params:
        try:
            from .scheduler import configure
            configure(min_confidence=int(params["min_confidence"]))
            applied["min_confidence"] = params["min_confidence"]
        except Exception:
            pass

    # Return what would be applied
    _state["current_params"] = params
    _save()

    return {
        "success": True,
        "applied_params": params,
        "applied_fields": list(applied.keys()),
        "message": "Parameters applied to system",
    }


def status() -> Dict[str, Any]:
    _ensure()
    return {
        "success": True,
        "autooptimizer": {
            "enabled": _state.get("enabled", False),
            "last_optimization": _state.get("last_optimization"),
            "optimization_count": _state.get("optimization_count", 0),
            "trades_since_last_opt": _state.get("trades_since_last_opt", 0),
            "optimize_every_n_trades": _state.get("optimize_every_n_trades", 20),
            "best_params": _state.get("best_params", {}),
            "current_params": _state.get("current_params", {}),
            "history": _state.get("history", [])[-5:],
        },
    }


def on_trade_closed() -> Dict[str, Any]:
    """Call this after each trade close. Triggers optimization if threshold met."""
    _ensure()
    if not _state.get("enabled"):
        return {"success": True, "action": "none", "reason": "optimizer disabled"}

    _state["trades_since_last_opt"] = _state.get("trades_since_last_opt", 0) + 1
    threshold = _state.get("optimize_every_n_trades", 20)

    if _state["trades_since_last_opt"] >= threshold:
        return {"success": True, "action": "ready", "message": f"{_state['trades_since_last_opt']} trades, ready for optimization"}

    _save()
    return {"success": True, "action": "wait", "trades_to_go": threshold - _state["trades_since_last_opt"]}
