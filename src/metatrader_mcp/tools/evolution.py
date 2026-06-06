"""
Evolution — forward testing competition entre estrategias.

Mantiene dos espacios:
  - current: estrategia desplegada actualmente
  - challenger: nueva estrategia compitiendo

Después de N trades cada una, se comparan.
Si el challenger gana, se despliega automáticamente.

Similar a como evolucionan los sistemas de trading algorítmico
institucionales (Two Sigma, Renaissance usan esto).
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "evolution.json")

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
                "evaluation_window": 20,
                "current": {
                    "name": "conviction",
                    "label": "Conviction v2 (10 indicators)",
                    "parameters": {},
                    "trades": [],
                    "wins": 0, "losses": 0,
                    "total_pnl": 0,
                },
                "challenger": {
                    "name": "",
                    "label": "",
                    "parameters": {},
                    "trades": [],
                    "wins": 0, "losses": 0,
                    "total_pnl": 0,
                },
                "evolution_history": [],
                "generation": 0,
            }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def enable(eval_window: int = 20) -> Dict[str, Any]:
    _ensure()
    _state["enabled"] = True
    _state["evaluation_window"] = eval_window
    _save()
    return {"success": True, "evaluation_window": eval_window}


def disable() -> Dict[str, Any]:
    _ensure()
    _state["enabled"] = False
    _save()
    return {"success": True}


def deploy_challenger(challenger_name: str, challenger_label: str, params: dict = None) -> Dict[str, Any]:
    """Deploy a new challenger strategy.

    The challenger runs alongside current in paper mode.
    After evaluation_window trades each, we compare.
    """
    _ensure()
    _state["challenger"] = {
        "name": challenger_name,
        "label": challenger_label,
        "parameters": params or {},
        "trades": [],
        "wins": 0, "losses": 0,
        "total_pnl": 0,
        "deployed_at": datetime.now(timezone.utc).isoformat(),
    }
    _save()
    return {"success": True, "challenger": challenger_name}


def record_trade(strategy_type: str, won: bool, pnl: float, metadata: dict = None) -> Dict[str, Any]:
    """Record a trade result for current or challenger.

    Args:
        strategy_type: 'current' or 'challenger'
        won: True if profitable
        pnl: profit/loss amount
        metadata: optional dict

    Returns:
        May trigger evolution if evaluation window reached.
    """
    _ensure()
    if not _state.get("enabled"):
        return {"success": True, "evolution": "disabled"}

    if strategy_type not in ("current", "challenger"):
        return {"success": False, "error": "strategy_type must be 'current' or 'challenger'"}

    slot = _state[strategy_type]
    slot.setdefault("trades", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "won": won,
        "pnl": round(pnl, 2),
        "metadata": metadata or {},
    })
    slot["trades"] = slot["trades"][-_state.get("evaluation_window", 20) * 2:]
    if won:
        slot["wins"] += 1
    else:
        slot["losses"] += 1
    slot["total_pnl"] = round(slot.get("total_pnl", 0) + pnl, 2)

    _save()

    # Check if evolution should run
    return _check_evolution()


def _check_evolution() -> Dict[str, Any]:
    """Check if both current and challenger have enough trades to compare."""
    current_trades = len(_state["current"].get("trades", []))
    challenger_trades = len(_state["challenger"].get("trades", []))

    window = _state.get("evaluation_window", 20)
    min_trades = max(5, window // 2)

    if current_trades < min_trades or challenger_trades < min_trades:
        return {"success": True, "evolution": "waiting",
                "current_trades": current_trades, "challenger_trades": challenger_trades,
                "needed": min_trades}

    # Compare recent performance
    c_trades = _state["current"]["trades"][-window:]
    ch_trades = _state["challenger"]["trades"][-window:]

    c_wins = sum(1 for t in c_trades if t.get("won"))
    ch_wins = sum(1 for t in ch_trades if t.get("won"))

    c_win_rate = c_wins / len(c_trades) * 100 if c_trades else 0
    ch_win_rate = ch_wins / len(ch_trades) * 100 if ch_trades else 0

    c_pnl = sum(t.get("pnl", 0) for t in c_trades)
    ch_pnl = sum(t.get("pnl", 0) for t in ch_trades)

    # Does challenger win?
    challenger_won = ch_win_rate > c_win_rate and ch_pnl > c_pnl

    if challenger_won:
        # EVOLVE: challenger becomes the new current
        _state["generation"] = _state.get("generation", 0) + 1
        _state.setdefault("evolution_history", []).append({
            "generation": _state["generation"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "old_current": _state["current"]["name"],
            "new_current": _state["challenger"]["name"],
            "old_win_rate": round(c_win_rate, 1),
            "new_win_rate": round(ch_win_rate, 1),
            "old_pnl": round(c_pnl, 2),
            "new_pnl": round(ch_pnl, 2),
        })

        # Promote challenger to current
        old_current = dict(_state["current"])
        _state["current"] = dict(_state["challenger"])
        _state["current"]["trades"] = _state["current"].get("trades", [])[-window:]

        # Reset challenger
        _state["challenger"] = {
            "name": "", "label": "",
            "parameters": {}, "trades": [],
            "wins": 0, "losses": 0, "total_pnl": 0,
        }

        _save()

        return {
            "success": True,
            "evolution": "evolved",
            "generation": _state["generation"],
            "new_current": _state["current"]["name"],
            "old_win_rate": round(c_win_rate, 1),
            "new_win_rate": round(ch_win_rate, 1),
            "old_pnl": round(c_pnl, 2),
            "new_pnl": round(ch_pnl, 2),
            "message": f"Evolved to {_state['current']['name']} (gen {_state['generation']})",
        }

    return {
        "success": True,
        "evolution": "no_change",
        "current_win_rate": round(c_win_rate, 1),
        "challenger_win_rate": round(ch_win_rate, 1),
        "current_pnl": round(c_pnl, 2),
        "challenger_pnl": round(ch_pnl, 2),
    }


def status() -> Dict[str, Any]:
    _ensure()
    window = _state.get("evaluation_window", 20)
    c = _state["current"]
    ch = _state["challenger"]

    c_recent = c.get("trades", [])[-window:] if c.get("trades") else []
    ch_recent = ch.get("trades", [])[-window:] if ch.get("trades") else []

    return {
        "success": True,
        "evolution": {
            "enabled": _state.get("enabled", False),
            "generation": _state.get("generation", 0),
            "evaluation_window": window,
            "current": {
                "name": c.get("name"),
                "label": c.get("label"),
                "trades_total": len(c.get("trades", [])),
                "recent_trades": len(c_recent),
                "recent_win_rate": round(sum(1 for t in c_recent if t.get("won")) / max(len(c_recent), 1) * 100, 1),
                "total_pnl": c.get("total_pnl", 0),
            },
            "challenger": {
                "name": ch.get("name") or "None",
                "label": ch.get("label") or "None",
                "trades_total": len(ch.get("trades", [])),
                "recent_trades": len(ch_recent),
                "recent_win_rate": round(sum(1 for t in ch_recent if t.get("won")) / max(len(ch_recent), 1) * 100, 1),
                "total_pnl": ch.get("total_pnl", 0),
            },
            "history": _state.get("evolution_history", [])[-5:],
        }
    }
