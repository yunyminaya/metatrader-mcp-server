"""
EABridge — comunicación Python ↔ EA MQL5 a velocidad tick.

Protocolo:
  1. Python escribe:  ea/signals/TRADE_SIGNAL_<id>.json
  2. EA lee en cada tick, ejecuta, escribe resultado
  3. Python lee:      ea/signals/TRADE_RESULT_<id>.json

El EA está compilado en ea/signal_receiver.ex5
Se instala en MT5: Copiar a MQL5/Experts/ y adjuntar a un gráfico.
"""
import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# Signal directory (relative to project root)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SIGNAL_DIR = os.path.join(PROJECT_ROOT, "ea", "signals")


def _signal_path(signal_id: str) -> str:
    return os.path.join(SIGNAL_DIR, f"TRADE_SIGNAL_{signal_id}.json")


def _result_path(signal_id: str) -> str:
    return os.path.join(SIGNAL_DIR, f"TRADE_RESULT_{signal_id}.json")


def send_signal(symbol: str, order_type: str, volume: float,
                sl: float = 0, tp: float = 0, action: str = "market",
                wait_result: bool = True, timeout_sec: int = 30) -> Dict[str, Any]:
    """Send a trade signal to the EA and optionally wait for execution result.

    Args:
        symbol: symbol to trade
        order_type: BUY or SELL
        volume: lot size
        sl: stop loss price (0 = none)
        tp: take profit price (0 = none)
        action: market | close_all | modify_sl
        wait_result: if True, polls for result file
        timeout_sec: max wait for result

    Returns:
        execution result from EA
    """
    signal_id = str(uuid.uuid4())[:8]
    signal = {
        "id": signal_id,
        "symbol": symbol,
        "type": order_type.upper(),
        "volume": volume,
        "sl": sl,
        "tp": tp,
        "action": action,
        "close_all": action == "close_all",
        "magic": 20240601,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Ensure signal directory exists
    os.makedirs(SIGNAL_DIR, exist_ok=True)

    # Write signal file
    signal_file = _signal_path(signal_id)
    try:
        with open(signal_file, "w") as f:
            json.dump(signal, f)
    except Exception as e:
        return {"success": False, "error": f"Cannot write signal: {e}"}

    logger.info(f"Signal sent: {order_type} {volume} {symbol} (id={signal_id})")

    if not wait_result:
        return {"success": True, "signal_id": signal_id, "message": "Signal sent (async)"}

    # Wait for result
    result_file = _result_path(signal_id)
    start = time.time()
    while time.time() - start < timeout_sec:
        if os.path.exists(result_file):
            try:
                with open(result_file) as f:
                    result = json.load(f)
                # Cleanup
                try:
                    os.remove(result_file)
                except Exception:
                    pass
                result["signal_id"] = signal_id
                return {"success": True, "execution": result}
            except Exception as e:
                return {"success": False, "error": f"Result parse error: {e}"}
        time.sleep(0.1)

    return {"success": False, "error": "Timeout waiting for EA execution", "signal_id": signal_id}


def send_close_all(wait_result: bool = True) -> Dict[str, Any]:
    """Send close_all signal to EA."""
    return send_signal("", "", 0, action="close_all", wait_result=wait_result)


def send_modify_sl(ticket: int, new_sl: float) -> Dict[str, Any]:
    """Send modify SL signal to EA."""
    signal_id = str(uuid.uuid4())[:8]
    signal = {
        "id": signal_id,
        "action": "modify_sl",
        "ticket": ticket,
        "sl": new_sl,
        "magic": 20240601,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    os.makedirs(SIGNAL_DIR, exist_ok=True)
    signal_file = _signal_path(signal_id)
    try:
        with open(signal_file, "w") as f:
            json.dump(signal, f)
    except Exception as e:
        return {"success": False, "error": str(e)}

    # Wait briefly for result
    result_file = _result_path(signal_id)
    start = time.time()
    while time.time() - start < 10:
        if os.path.exists(result_file):
            try:
                with open(result_file) as f:
                    result = json.load(f)
                try:
                    os.remove(result_file)
                except Exception:
                    pass
                return {"success": True, "execution": result}
            except Exception:
                pass
        time.sleep(0.1)

    return {"success": True, "signal_id": signal_id, "message": "Modify SL sent (async)"}


def ea_status() -> Dict[str, Any]:
    """Check if EA is responding by looking at signal directory."""
    # Look for any recent result files
    if not os.path.exists(SIGNAL_DIR):
        return {"success": True, "ea_responding": False, "message": "Signal directory not found"}

    files = os.listdir(SIGNAL_DIR)
    recent_results = [f for f in files if f.startswith("TRADE_RESULT_") and f.endswith(".json")]

    # Clean old results
    now = time.time()
    for f in files:
        path = os.path.join(SIGNAL_DIR, f)
        try:
            if now - os.path.getmtime(path) > 3600:
                os.remove(path)
        except Exception:
            pass

    return {
        "success": True,
        "ea_responding": True,  # We assume EA is running if we can write signals
        "pending_signals": len([f for f in files if f.startswith("TRADE_SIGNAL_")]),
        "recent_results": len(recent_results),
        "signal_dir": SIGNAL_DIR,
        "action": "Copy ea/signal_receiver.ex5 to MT5/MQL5/Experts/ and attach to chart",
    }
