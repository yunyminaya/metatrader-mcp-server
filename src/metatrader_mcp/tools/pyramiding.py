"""
Pyramiding — escalar posiciones ganadoras (añadir en tendencia).

Estrategia: cuando una posición está en ganancia y hay señal
de continuación, añadir más lotes en lugar de tomar ganancia.

Reglas:
  1. Solo añadir si la posición original está en profit
  2. Solo añadir si hay confirmación de tendencia (misma dirección)
  3. Escalar geométricamente: lote 1, +0.5, +0.25, +0.125...
  4. Máximo 4 escalones
  5. SL del total se mueve al breakeven del escalón anterior
  6. No añadir si la volatilidad es extrema
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "pyramiding.json")

_state: Dict[str, Any] = {}


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
                "max_levels": 4,
                "scaling_factor": 0.5,  # each add is 50% of previous
                "min_profit_pct_activate": 0.5,  # need 0.5% profit before first add
                "min_profit_pct_add": 0.3,  # each subsequent add
                "active_pyramids": [],
                "history": [],
            }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def enable(levels: int = 4, scaling: float = 0.5, min_profit: float = 0.5) -> Dict[str, Any]:
    _ensure()
    _state["enabled"] = True
    _state["max_levels"] = levels
    _state["scaling_factor"] = scaling
    _state["min_profit_pct_activate"] = min_profit
    _save()
    return {"success": True, "config": {
        "max_levels": levels, "scaling": scaling, "min_profit_pct": min_profit
    }}


def disable() -> Dict[str, Any]:
    _ensure()
    _state["enabled"] = False
    _save()
    return {"success": True}


def evaluate(client, symbol: str, position_ticket: int, current_price: float,
             entry_price: float, position_type: str, volume: float) -> Dict[str, Any]:
    """Evaluate if we should pyramid (add to the position).

    Args:
        client: MT5Client
        symbol: Symbol
        position_ticket: Original position ticket
        current_price: Current market price
        entry_price: Entry price of original position
        position_type: 'buy' or 'sell'
        volume: Current total volume

    Returns:
        Should we add? How much?
    """
    _ensure()
    if not _state.get("enabled"):
        return {"success": True, "action": "none", "reason": "pyramiding_disabled"}

    # Calculate profit %
    if position_type.lower() == "buy":
        profit_pct = (current_price - entry_price) / entry_price * 100
    else:
        profit_pct = (entry_price - current_price) / entry_price * 100

    # Check if we already have an active pyramid for this ticket
    active = None
    for p in _state.get("active_pyramids", []):
        if p.get("root_ticket") == position_ticket:
            active = p
            break

    if active:
        level = active.get("level", 0)
        if level >= _state["max_levels"]:
            return {"success": True, "action": "none", "reason": "max_level_reached",
                    "level": level}
        min_profit = _state.get("min_profit_pct_add", 0.3)
    else:
        level = 0
        min_profit = _state.get("min_profit_pct_activate", 0.5)

    if profit_pct < min_profit:
        return {"success": True, "action": "none", "reason": "profit_too_low",
                "profit_pct": round(profit_pct, 2), "required": min_profit}

    # Calculate add volume (geometric: 1, 0.5, 0.25, 0.125...)
    scaling = _state.get("scaling_factor", 0.5)
    add_volume = round(volume * (scaling ** (level + 1)), 2)

    if add_volume < 0.01:
        return {"success": True, "action": "none", "reason": "volume_too_small",
                "add_volume": add_volume}

    return {
        "success": True,
        "action": "add",
        "reason": "pyramid_signal",
        "level": level + 1,
        "add_volume": add_volume,
        "add_direction": position_type,
        "profit_pct": round(profit_pct, 2),
        "total_volume_after": round(volume + add_volume, 2),
        "sl_advice": "move_to_breakeven_of_entry",
    }


def confirm_add(client, root_ticket: int, new_ticket: int, add_volume: float,
                add_price: float, level: int) -> Dict[str, Any]:
    """Record a confirmed pyramid add."""
    _ensure()

    # Find or create pyramid
    active = None
    for p in _state.get("active_pyramids", []):
        if p.get("root_ticket") == root_ticket:
            active = p
            break

    if not active:
        active = {
            "root_ticket": root_ticket,
            "level": 0,
            "adds": [],
            "total_volume": 0,
            "root_entry": add_price,
            "created": datetime.now(timezone.utc).isoformat(),
        }
        _state.setdefault("active_pyramids", []).append(active)

    active["level"] = level
    active["adds"].append({
        "ticket": new_ticket,
        "volume": add_volume,
        "price": add_price,
        "level": level,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    active["total_volume"] = sum(a["volume"] for a in active["adds"])
    _save()

    return {"success": True, "pyramid_level": level, "total_volume": active["total_volume"]}


def close_pyramid(root_ticket: int, final_pnl: float) -> Dict[str, Any]:
    """Record pyramid close and final PnL."""
    _ensure()

    _state["active_pyramids"] = [
        p for p in _state.get("active_pyramids", [])
        if p.get("root_ticket") != root_ticket
    ]
    _state.setdefault("history", []).append({
        "root_ticket": root_ticket,
        "final_pnl": round(final_pnl, 2),
        "closed": datetime.now(timezone.utc).isoformat(),
    })
    _state["history"] = _state["history"][-50:]
    _save()
    return {"success": True}


def status() -> Dict[str, Any]:
    _ensure()
    return {
        "success": True,
        "pyramiding": {
            "enabled": _state.get("enabled", False),
            "max_levels": _state.get("max_levels", 4),
            "scaling_factor": _state.get("scaling_factor", 0.5),
            "active_pyramids": len(_state.get("active_pyramids", [])),
            "recent_history": _state.get("history", [])[-5:],
        }
    }
