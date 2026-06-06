"""
AutoSwitch — cambio automático de estrategia según rendimiento.

Cuando una estrategia falla N veces consecutivas,
el sistema rota automáticamente a otra estrategia.

Ciclo de rotación:
  1. Conviction (default) — estrategia principal
  2. Mean Reversion — cuando el mercado está sobre-extendido
  3. Adaptive Grid — cuando hay rango/consolidación
  4. Straddle — cuando hay alta volatilidad/breakout
  5. Volver a Conviction

Reglas:
  - Cada estrategia tiene un contador de pérdidas consecutivas
  - Al llegar a `max_losses` (default 3), rota a la siguiente
  - Después de rotar, da `cooldown_trades` (default 2) antes de juzgar
  - Si todas fallan, pasa a modo defensivo (solo guard_check)
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "autoswitch.json")

_state: Dict[str, Any] = {}

_STRATEGY_CYCLE = ["conviction", "mean_reversion", "adaptive_grid", "straddle"]
_STRATEGY_LABELS = {
    "conviction": "Conviction v2 (10 indicators + ML)",
    "mean_reversion": "Mean Reversion (>2σ deviation)",
    "adaptive_grid": "Adaptive Grid (ATR-spaced)",
    "straddle": "Volatility Straddle (breakout)",
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
                "current_strategy": "conviction",
                "max_losses": 3,
                "cooldown_trades": 2,
                "strategies": {
                    s: {"consecutive_losses": 0, "total_losses": 0, "total_wins": 0, "last_switch": None, "cooldown_remaining": 0}
                    for s in _STRATEGY_CYCLE
                },
                "switch_history": [],
                "defensive_mode": False,
            }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def enable(max_losses: int = 3, cooldown: int = 2) -> Dict[str, Any]:
    _ensure()
    _state["enabled"] = True
    _state["max_losses"] = max_losses
    _state["cooldown_trades"] = cooldown
    _save()
    return {"success": True, "config": {
        "max_losses": max_losses, "cooldown_trades": cooldown
    }}


def disable() -> Dict[str, Any]:
    _ensure()
    _state["enabled"] = False
    _save()
    return {"success": True}


def on_trade_result(strategy: str, won: bool) -> Dict[str, Any]:
    """Call after each trade. Updates strategy stats and may trigger switch."""
    _ensure()
    if not _state.get("enabled"):
        return {"success": True, "action": "none", "reason": "autoswitch_disabled"}

    strat = _state["strategies"].get(strategy)
    if not strat:
        return {"success": False, "error": f"Unknown strategy: {strategy}"}

    # Decrement cooldown
    if strat["cooldown_remaining"] > 0:
        strat["cooldown_remaining"] -= 1

    if won:
        strat["total_wins"] += 1
        strat["consecutive_losses"] = 0
        _save()
        return {"success": True, "action": "none", "reason": "win_reset_losses"}

    # Loss
    strat["total_losses"] += 1
    strat["consecutive_losses"] += 1

    # Check if we should switch (only if not in cooldown)
    if strat["cooldown_remaining"] > 0:
        _save()
        return {"success": True, "action": "none",
                "reason": f"cooldown_{strat['cooldown_remaining']}_remaining"}

    if strat["consecutive_losses"] >= _state["max_losses"]:
        return _switch_strategy(strategy)

    _save()
    return {"success": True, "action": "none",
            "reason": f"losses_{strat['consecutive_losses']}/{_state['max_losses']}"}


def _switch_strategy(from_strategy: str) -> Dict[str, Any]:
    """Switch from current strategy to next in cycle."""
    current_idx = _STRATEGY_CYCLE.index(from_strategy) if from_strategy in _STRATEGY_CYCLE else -1
    next_idx = (current_idx + 1) % len(_STRATEGY_CYCLE)
    new_strategy = _STRATEGY_CYCLE[next_idx]

    _state["current_strategy"] = new_strategy
    _state["strategies"][new_strategy]["cooldown_remaining"] = _state["cooldown_trades"]
    _state.setdefault("switch_history", []).append({
        "from": from_strategy,
        "to": new_strategy,
        "reason": f"{_state['max_losses']}_consecutive_losses",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    _state["switch_history"] = _state["switch_history"][-20:]

    # Check if we've gone through all strategies and are back to the original
    _check_defensive_mode()

    _save()

    return {
        "success": True,
        "action": "switch",
        "from_strategy": from_strategy,
        "from_label": _STRATEGY_LABELS.get(from_strategy, from_strategy),
        "to_strategy": new_strategy,
        "to_label": _STRATEGY_LABELS.get(new_strategy, new_strategy),
        "defensive_mode": _state.get("defensive_mode", False),
    }


def _check_defensive_mode():
    """If all strategies have been tried and we're back to conviction, go defensive."""
    history = _state.get("switch_history", [])
    if len(history) >= len(_STRATEGY_CYCLE):
        recent = history[-len(_STRATEGY_CYCLE):]
        tried_all = all(
            any(h["from"] == s for h in recent) or any(h["to"] == s for h in recent)
            for s in _STRATEGY_CYCLE
        )
        if tried_all:
            _state["defensive_mode"] = True


def get_current_strategy() -> Dict[str, Any]:
    """Returns what strategy to use right now."""
    _ensure()
    return {
        "success": True,
        "strategy": _state.get("current_strategy", "conviction"),
        "label": _STRATEGY_LABELS.get(_state.get("current_strategy"), _state.get("current_strategy")),
        "defensive_mode": _state.get("defensive_mode", False),
        "strategies": _state.get("strategies", {}),
    }


def reset_strategy(strategy: str = "") -> Dict[str, Any]:
    """Reset losses for a strategy (or all if empty)."""
    _ensure()
    if strategy and strategy in _state["strategies"]:
        _state["strategies"][strategy]["consecutive_losses"] = 0
    elif not strategy:
        for s in _state["strategies"]:
            _state["strategies"][s]["consecutive_losses"] = 0
        _state["defensive_mode"] = False
        _state["current_strategy"] = "conviction"
    _save()
    return {"success": True}


def status() -> Dict[str, Any]:
    _ensure()
    return {
        "success": True,
        "autoswitch": {
            "enabled": _state.get("enabled", False),
            "current_strategy": _state.get("current_strategy"),
            "current_label": _STRATEGY_LABELS.get(_state.get("current_strategy")),
            "max_losses": _state.get("max_losses", 3),
            "cooldown_trades": _state.get("cooldown_trades", 2),
            "defensive_mode": _state.get("defensive_mode", False),
            "strategies": _state.get("strategies", {}),
            "recent_switches": _state.get("switch_history", [])[-5:],
        }
    }
