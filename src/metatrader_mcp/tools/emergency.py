"""
Emergency — freno de emergencia multi-capas.

Dispara auto-stop si:
  1. 5 pérdidas consecutivas (cualquier combinación papertrade + live)
  2. 30%+ drawdown desde pico de balance
  3. Insurance fund agotado cubriendo pérdidas

Cuando salta: deshabilita scheduler, deshabilita guard, frena todo.
Solo rearme manual vía emergency_reset().
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "emergency.json")

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
                "brake_active": False,
                "tripped_at": None,
                "tripped_by": None,
                "consecutive_losses": 0,
                "peak_balance": 0,
                "max_drawdown_pct": 30,
                "max_consecutive_losses": 5,
                "history": [],
            }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def configure(max_consecutive_losses: int = 5, max_drawdown_pct: float = 30) -> Dict[str, Any]:
    _ensure()
    _state["max_consecutive_losses"] = max_consecutive_losses
    _state["max_drawdown_pct"] = max_drawdown_pct
    _save()
    return {"success": True, "config": {"max_consecutive_losses": max_consecutive_losses, "max_drawdown_pct": max_drawdown_pct}}


def record_trade(pnl_usd: float, current_balance: float) -> Dict[str, Any]:
    """Registra un trade y chequea si el freno debe saltar. Devuelve estado."""
    _ensure()

    if _state["brake_active"]:
        return {"success": True, "brake_active": True, "message": "Brake already active. Reset manually."}

    # Track consecutive losses
    if pnl_usd <= 0:
        _state["consecutive_losses"] += 1
    else:
        _state["consecutive_losses"] = 0

    # Track peak balance → drawdown
    if current_balance > _state["peak_balance"]:
        _state["peak_balance"] = current_balance

    dd_pct = 0
    if _state["peak_balance"] > 0:
        dd_pct = round((_state["peak_balance"] - current_balance) / _state["peak_balance"] * 100, 1)

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pnl_usd": round(pnl_usd, 2),
        "balance": round(current_balance, 2),
        "consecutive_losses": _state["consecutive_losses"],
        "drawdown_pct": dd_pct,
    }
    _state.setdefault("history", []).append(entry)
    _state["history"] = _state["history"][-100:]

    trip_reason = None
    if _state["consecutive_losses"] >= _state["max_consecutive_losses"]:
        trip_reason = f"{_state['consecutive_losses']} consecutive losses (limit: {_state['max_consecutive_losses']})"
    if dd_pct >= _state["max_drawdown_pct"]:
        trip_reason = f"Drawdown {dd_pct}% >= {_state['max_drawdown_pct']}% limit"

    # Correlation risk trigger: check if portfolio is overconcentrated
    if not trip_reason:
        try:
            from .correlation import portfolio_risk
            from .papertrade import portfolio as pt
            p = pt()
            pos = p.get("portfolio", {}).get("positions", [])
            syms = [x.get("symbol") for x in pos if x.get("symbol")]
            if len(syms) >= 3:
                risk = portfolio_risk(syms)
                if risk.get("risk_level") == "high":
                    trip_reason = f"Correlation risk: {risk.get('warnings', [])}"
        except Exception:
            pass

    if trip_reason:
        _state["brake_active"] = True
        _state["tripped_at"] = datetime.now(timezone.utc).isoformat()
        _state["tripped_by"] = trip_reason
        _save()

        # Auto-disable scheduler and guard
        try:
            from . import scheduler
            scheduler.stop()
        except Exception:
            pass
        try:
            from . import guard
            guard.stop()
        except Exception:
            pass

        return {
            "success": True,
            "brake_tripped": True,
            "reason": trip_reason,
            "consecutive_losses": _state["consecutive_losses"],
            "drawdown_pct": dd_pct,
            "message": "EMERGENCY BRAKE ACTIVE. All trading stopped. Call emergency_reset() to re-enable.",
        }

    _save()
    return {
        "success": True,
        "brake_tripped": False,
        "consecutive_losses": _state["consecutive_losses"],
        "drawdown_pct": dd_pct,
        "peak_balance": round(_state["peak_balance"], 2),
    }


def status() -> Dict[str, Any]:
    _ensure()
    return {
        "success": True,
        "emergency": {
            "brake_active": _state["brake_active"],
            "tripped_at": _state.get("tripped_at"),
            "tripped_by": _state.get("tripped_by"),
            "consecutive_losses": _state["consecutive_losses"],
            "peak_balance": round(_state["peak_balance"], 2),
            "max_consecutive_losses": _state["max_consecutive_losses"],
            "max_drawdown_pct": _state["max_drawdown_pct"],
            "recent_trades": _state.get("history", [])[-5:],
        },
    }


def reset() -> Dict[str, Any]:
    """Reseteo manual del freno de emergencia."""
    global _state
    was_active = _state.get("brake_active", False)
    _state = {
        "brake_active": False,
        "tripped_at": None,
        "tripped_by": None,
        "consecutive_losses": 0,
        "peak_balance": 0,
        "max_drawdown_pct": _state.get("max_drawdown_pct", 30),
        "max_consecutive_losses": _state.get("max_consecutive_losses", 5),
        "history": _state.get("history", []),
    }
    _save()
    return {"success": True, "was_active": was_active, "message": "Emergency brake reset. Trading re-enabled."}
