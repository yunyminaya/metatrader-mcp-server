"""
Scheduler — ejecución periódica automática de trades en MT5.

Evaluación periódica de convicción, ejecución con confirmación
de fill, resolución automática, límites de drawdown y trades diarios.
NO ejecuta loops internos (el host llama al tool schedule_tick).
"""
import json
import logging
import math
import os
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "scheduler.json")

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
                "interval_minutes": 60,
                "daily_limit": 3,
                "min_confidence": 60,
                "max_daily_drawdown_pct": 10,
                "max_consecutive_losses": 5,
                "symbols": [],
                "trades_today": 0,
                "date": "",
                "consecutive_losses": 0,
                "peak_balance": 0,
                "insurance_fund": 0,
                "last_run": None,
            }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def configure(interval_minutes: int = 60, daily_limit: int = 3,
              min_confidence: int = 60, max_daily_drawdown_pct: float = 10,
              max_consecutive_losses: int = 5, symbols: list = None) -> Dict[str, Any]:
    _ensure()
    _state["interval_minutes"] = interval_minutes
    _state["daily_limit"] = daily_limit
    _state["min_confidence"] = min_confidence
    _state["max_daily_drawdown_pct"] = max_daily_drawdown_pct
    _state["max_consecutive_losses"] = max_consecutive_losses
    if symbols is not None:
        _state["symbols"] = symbols
    _save()
    return {"success": True, "config": _state}


def status(client_holder=None) -> Dict[str, Any]:
    _ensure()
    return {"success": True, "scheduler": _state}


def start(client_holder=None) -> Dict[str, Any]:
    _ensure()
    _state["enabled"] = True
    _state["last_run"] = datetime.now(timezone.utc).isoformat()
    _save()
    # Registrar para auto-ejecución: se espera que el llamador
    # haga schedule_tick periódicamente.
    return {"success": True, "message": "Scheduler enabled — call schedule_tick periodically"}


def stop(client_holder=None) -> Dict[str, Any]:
    _ensure()
    _state["enabled"] = False
    _save()
    return {"success": True, "message": "Scheduler disabled"}


def tick(client) -> Dict[str, Any]:
    """Un tick del scheduler. Evalúa convicción y ejecuta si procede."""
    _ensure()
    if not _state.get("enabled"):
        return {"success": False, "error": "Scheduler disabled", "actioned": False}

    # ── Heartbeat ──
    try:
        from .heartbeat import tick as hb_tick
        hb_tick("scheduler")
    except Exception:
        pass

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # Reset diario
    if _state.get("date") != today:
        _state["trades_today"] = 0
        _state["date"] = today

    # Check daily limit
    if _state["trades_today"] >= _state["daily_limit"]:
        return {"success": True, "actioned": False, "reason": "Daily limit reached"}

    # Emergency brake check
    try:
        from .emergency import status as em_status
        em = em_status()
        if em.get("emergency", {}).get("brake_active"):
            return {"success": False, "actioned": False, "error": "Emergency brake active", "reason": em.get("emergency", {}).get("tripped_by")}
    except Exception:
        pass

    # Insurance fund
    insurance = _state.get("insurance_fund", 0)

    # Obtener símbolos a escanear
    symbols = _state.get("symbols", [])
    if not symbols:
        try:
            syms = client.market.get_symbols()
            symbols = [s.get("name", s) if isinstance(s, dict) else s for s in (syms or [])[:10]]
        except Exception:
            symbols = []

    if not symbols:
        return {"success": False, "error": "No symbols available", "actioned": False}

    # Escanear convicción
    from . import conviction

    results = []
    full_decisions = []
    for sym in symbols[:5]:
        try:
            d = conviction.decide(client, sym, "H1")
            if d.get("success"):
                dec = d.get("decision", {})
                results.append({
                    "symbol": sym,
                    "verdict": dec.get("verdict"),
                    "confidence": dec.get("confidence_pct", 0),
                    "lot": dec.get("suggested_lot_size", 0.01),
                })
                full_decisions.append(dec)
        except Exception:
            continue

    best = max(results, key=lambda x: x["confidence"]) if results else None
    if not best or best["confidence"] < _state["min_confidence"]:
        _state["last_run"] = now.isoformat()
        _save()
        return {"success": True, "actioned": False, "reason": f"No signal >={_state['min_confidence']} confidence"}

    # Ejecutar papertrade
    from . import papertrade

    verdict = best["verdict"]
    if "BUY" in verdict:
        order_type = "BUY"
    elif "SELL" in verdict:
        order_type = "SELL"
    else:
        return {"success": True, "actioned": False, "reason": "PASS verdict"}

    # Extract features for ML training
    features = {}
    for dec in full_decisions:
        if dec.get("symbol") == best["symbol"]:
            indicators = dec.get("indicators", {})
            from .predictor import extract_features
            features = extract_features(indicators)
            features["session_quality"] = dec.get("session_quality", 50)
            features["mtf_alignment"] = dec.get("mtf", {}).get("alignment", 0)
            break

    result = papertrade.open_order(
        client, best["symbol"], order_type, best["lot"],
        reason=f"auto:scheduler confidence={best['confidence']}",
        features=features if features else None,
    )

    if result.get("success"):
        _state["trades_today"] += 1
        _state["last_run"] = now.isoformat()
        _save()

    # Registrar trade en emergency (para conteo de pérdidas consecutivas)
    try:
        from .emergency import record_trade
        from .insurance import status as ins_status
        bal = ins_status().get("insurance", {}).get("balance", 0)
        # papertrade initial margin approximation
        entry = result.get("position", {}).get("entry_price", 1)
        vol = best["lot"]
        margin_est = vol * 100000 / max(entry, 0.0001)
        record_trade(0 if result.get("success") else -margin_est, bal)
    except Exception:
        pass

    return {
        "success": True,
        "actioned": True,
        "trade": {
            "symbol": best["symbol"],
            "type": order_type,
            "lot": best["lot"],
            "confidence": best["confidence"],
        },
        "result": result,
        "trades_today": _state["trades_today"],
        "daily_limit": _state["daily_limit"],
        "insurance_fund": round(insurance, 2),
    }
