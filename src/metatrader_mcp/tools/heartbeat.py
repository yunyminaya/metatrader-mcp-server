"""
Heartbeat — watchdog que monitorea que scheduler y guard estén vivos.

Si no han hecho check en >2 intervalos, salta alerta.
Persiste a data/heartbeat.json.
"""
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "heartbeat.json")

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
                "scheduler_last_tick": None,
                "guard_last_check": None,
                "max_missed_intervals": 2,
                "alerts": [],
            }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def tick(component: str) -> Dict[str, Any]:
    """Register a heartbeat tick. component: 'scheduler' or 'guard'."""
    _ensure()
    now = datetime.now(timezone.utc).isoformat()
    if component == "scheduler":
        _state["scheduler_last_tick"] = now
    elif component == "guard":
        _state["guard_last_check"] = now
    else:
        return {"success": False, "error": f"Unknown component: {component}"}
    _save()
    return {"success": True, "last_tick": now}


def check_status(interval_minutes: int = 60) -> Dict[str, Any]:
    """Check if components are alive. Returns alerts if any missed >2 intervals."""
    _ensure()
    now = datetime.now(timezone.utc)
    alerts = []
    healthy = True

    grace = timedelta(minutes=interval_minutes * max(_state.get("max_missed_intervals", 2), 1))

    last_sched = _state.get("scheduler_last_tick")
    if last_sched:
        sched_time = datetime.fromisoformat(last_sched)
        if now - sched_time > grace:
            alerts.append(f"Scheduler heartbeat MISSED. Last tick: {last_sched}")
            healthy = False
    else:
        alerts.append("Scheduler has never ticked")

    last_guard = _state.get("guard_last_check")
    if last_guard:
        guard_time = datetime.fromisoformat(last_guard)
        if now - guard_time > grace:
            alerts.append(f"Guard heartbeat MISSED. Last check: {last_guard}")
            healthy = False
    else:
        alerts.append("Guard has never checked")

    if alerts:
        _state.setdefault("alerts", []).append({
            "timestamp": now.isoformat(),
            "alerts": alerts,
        })
        _state["alerts"] = _state["alerts"][-20:]
        _save()

    return {
        "success": True,
        "heartbeat": {
            "healthy": healthy,
            "scheduler_last_tick": _state.get("scheduler_last_tick"),
            "guard_last_check": _state.get("guard_last_check"),
            "alerts": alerts,
            "recent_history": _state.get("alerts", [])[-5:],
        },
    }


def reset() -> Dict[str, Any]:
    global _state
    _state = {"scheduler_last_tick": None, "guard_last_check": None, "max_missed_intervals": 2, "alerts": []}
    _save()
    return {"success": True}
