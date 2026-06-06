"""
Broker — multi-broker engine para MT5.

Conecta N brokers simultáneamente, compara spreads/ejecución
y enruta cada trade al mejor broker disponible.

Características:
  - Gestión de N conexiones MT5
  - Health check periódico por broker
  - Ranking en vivo por spread, slippage, latencia
  - Failover automático si un broker se cae
  - Router inteligente: elige mejor broker para cada trade
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "brokers.json")

_brokers: Dict[str, Any] = {}
_clients: Dict[str, Any] = {}


def _ensure():
    global _brokers
    if not _brokers:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE) as f:
                    _brokers = json.load(f)
        except Exception:
            _brokers = {"brokers": {}, "active": None, "routing_strategy": "best_spread"}


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        state = {
            "brokers": _brokers.get("brokers", {}),
            "active": _brokers.get("active"),
            "routing_strategy": _brokers.get("routing_strategy", "best_spread"),
        }
        with open(DATA_FILE, "w") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save brokers: {e}")


def register_broker(name: str, login: str, password: str, server: str,
                    path: str = "", weight: float = 1.0) -> Dict[str, Any]:
    """Register a broker connection.

    Args:
        name: unique broker alias (e.g. 'icmarkets', 'ftmo')
        login: MT5 account number
        password: MT5 password
        server: MT5 server name
        path: path to terminal64.exe (optional)
        weight: routing weight (1.0 = normal, higher = preferred)
    """
    _ensure()
    brokers = _brokers.setdefault("brokers", {})

    if name in brokers:
        return {"success": False, "error": f"Broker '{name}' already registered"}

    broker_info = {
        "name": name,
        "login": login,
        "password": "***",  # never store plaintext in save
        "server": server,
        "path": path,
        "weight": weight,
        "enabled": True,
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "stats": {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "total_slippage_pips": 0,
            "avg_spread_pips": 0,
            "last_health": None,
            "healthy": True,
        },
    }
    brokers[name] = broker_info

    # Store credentials separately for actual connection (not saved to JSON)
    _brokers.setdefault("_credentials", {})[name] = {
        "login": login,
        "password": password,
    }

    if not _brokers.get("active"):
        _brokers["active"] = name

    _save()
    return {"success": True, "broker": {k: v for k, v in broker_info.items() if k != "password"}}


def remove_broker(name: str) -> Dict[str, Any]:
    _ensure()
    if _brokers.get("brokers", {}).pop(name, None):
        _brokers.setdefault("_credentials", {}).pop(name, None)
        if _brokers.get("active") == name:
            remaining = list(_brokers["brokers"].keys())
            _brokers["active"] = remaining[0] if remaining else None
        if name in _clients:
            try:
                _clients[name].disconnect()
            except Exception:
                pass
            del _clients[name]
        _save()
        return {"success": True}
    return {"success": False, "error": f"Broker '{name}' not found"}


def connect_broker(name: str) -> Dict[str, Any]:
    """Connect to a registered broker's MT5 terminal."""
    _ensure()
    creds = _brokers.get("_credentials", {}).get(name)
    info = _brokers.get("brokers", {}).get(name)

    if not creds or not info:
        return {"success": False, "error": f"Broker '{name}' not registered"}

    try:
        from metatrader_client import MT5Client
        config = {
            "login": int(creds["login"]),
            "password": creds["password"],
            "server": info["server"],
            "path": info.get("path") or None,
            "timeout": 60000,
        }
        client = MT5Client(config)
        client.connect()
        _clients[name] = client
        info["stats"]["last_health"] = datetime.now(timezone.utc).isoformat()
        info["stats"]["healthy"] = True
        _save()
        return {"success": True, "broker": name, "server": info["server"]}
    except Exception as e:
        info["stats"]["healthy"] = False
        _save()
        return {"success": False, "error": str(e)}


def disconnect_broker(name: str) -> Dict[str, Any]:
    if name in _clients:
        try:
            _clients[name].disconnect()
        except Exception:
            pass
        del _clients[name]
        return {"success": True}
    return {"success": False, "error": f"Not connected: {name}"}


def get_client(name: str = None):
    """Get MT5 client for a broker. If name=None, returns active broker client."""
    _ensure()
    if name is None:
        name = _brokers.get("active")
    if not name or name not in _clients:
        return None
    return _clients.get(name)


def health_check(name: str = None) -> Dict[str, Any]:
    """Check broker connection health. Tests connection + spread."""
    _ensure()
    brokers_to_check = [name] if name else list(_brokers.get("brokers", {}).keys())
    results = []

    for bname in brokers_to_check:
        client = _clients.get(bname)
        info = _brokers.get("brokers", {}).get(bname, {})
        if not client or not info:
            results.append({"broker": bname, "connected": False, "healthy": False})
            continue

        try:
            account = client.account.get_account()
            balance = account.get("balance", 0)
            info["stats"]["healthy"] = True
            info["stats"]["last_health"] = datetime.now(timezone.utc).isoformat()
            results.append({
                "broker": bname,
                "connected": True,
                "healthy": True,
                "balance": balance,
                "server": info.get("server"),
            })
        except Exception as e:
            info["stats"]["healthy"] = False
            logger.warning(f"Broker {bname} unhealthy: {e}")
            results.append({"broker": bname, "connected": False, "healthy": False, "error": str(e)})

    _save()
    return {"success": True, "health": results}


def compare_spread(client, symbol: str) -> Dict[str, Any]:
    """Get spread for a symbol from a specific broker client."""
    try:
        price = client.market.get_symbol_price(symbol_name=symbol)
        if price:
            spread = price.get("spread", 999)
            return {"spread": spread, "spread_pips": spread / 10}
    except Exception:
        pass
    return {"spread": 999, "spread_pips": 999}


def find_best_broker(symbol: str) -> Dict[str, Any]:
    """Compare all connected brokers for a symbol and return the best one."""
    _ensure()
    if not _clients:
        return {"success": False, "error": "No connected brokers"}

    candidates = []
    for name, client in _clients.items():
        info = _brokers.get("brokers", {}).get(name, {})
        if not info.get("stats", {}).get("healthy", False):
            continue
        try:
            sp = compare_spread(client, symbol)
            candidates.append({
                "broker": name,
                "spread": sp["spread"],
                "spread_pips": sp["spread_pips"],
                "weight": info.get("weight", 1.0),
                "server": info.get("server"),
            })
        except Exception:
            continue

    if not candidates:
        return {"success": False, "error": "No healthy brokers"}

    # Sort by spread (best first)
    candidates.sort(key=lambda x: x["spread"])

    strategy = _brokers.get("routing_strategy", "best_spread")
    if strategy == "best_spread":
        best = candidates[0]
    elif strategy == "weighted":
        # Weighted best: best spread * (1/weight)
        scored = [(c, c["spread"] / max(c["weight"], 0.1)) for c in candidates]
        best = min(scored, key=lambda x: x[1])[0]
    else:
        best = candidates[0]

    return {
        "success": True,
        "best_broker": best["broker"],
        "spread_pips": best["spread_pips"],
        "candidates": candidates,
        "strategy": strategy,
    }


def route_order(symbol: str, order_type: str, volume: float,
                sl: float = 0, tp: float = 0) -> Dict[str, Any]:
    """Route an order to the best available broker.

    Steps:
      1. Find best broker for this symbol (lowest spread + healthy)
      2. Place market order on that broker
      3. Set SL/TP if provided
      4. Log trade to broker stats
    """
    best = find_best_broker(symbol)
    if not best.get("success"):
        return best

    broker_name = best["best_broker"]
    client = _clients.get(broker_name)
    if not client:
        return {"success": False, "error": f"Broker '{broker_name}' not connected"}

    try:
        result = client.order.place_market_order(symbol=symbol, volume=volume, type=order_type)
    except Exception as e:
        return {"success": False, "error": f"Order failed on {broker_name}: {e}"}

    ticket = result.get("ticket") or (result.get("data") or {}).get("ticket")
    if ticket and (sl or tp):
        try:
            client.order.modify_position(id=str(ticket), stop_loss=sl, take_profit=tp)
        except Exception as e:
            logger.warning(f"Could not set SL/TP on {broker_name} ticket {ticket}: {e}")

    # Update broker stats
    info = _brokers.get("brokers", {}).get(broker_name, {})
    info.setdefault("stats", {})["total_trades"] = info["stats"].get("total_trades", 0) + 1
    _save()

    return {
        "success": True,
        "broker": broker_name,
        "server": best.get("candidates", [{}])[0].get("server") if best.get("candidates") else None,
        "best_spread_pips": best.get("spread_pips"),
        "candidates_considered": len(best.get("candidates", [])),
        "order_result": result,
    }


def set_routing_strategy(strategy: str = "best_spread") -> Dict[str, Any]:
    """Set routing strategy: best_spread | weighted | round_robin."""
    _ensure()
    if strategy not in ("best_spread", "weighted", "round_robin"):
        return {"success": False, "error": f"Unknown strategy: {strategy}"}
    _brokers["routing_strategy"] = strategy
    _save()
    return {"success": True, "strategy": strategy}


def status() -> Dict[str, Any]:
    """Get status of all registered and connected brokers."""
    _ensure()
    registered = []
    for name, info in _brokers.get("brokers", {}).items():
        connected = name in _clients
        try:
            if connected and _clients[name]:
                h = _clients[name].account.get_account()
                balance = h.get("balance", 0)
            else:
                balance = 0
        except Exception:
            balance = 0
            connected = False

        registered.append({
            "name": name,
            "server": info.get("server"),
            "enabled": info.get("enabled", True),
            "connected": connected,
            "healthy": info.get("stats", {}).get("healthy", False),
            "balance": balance,
            "total_trades": info.get("stats", {}).get("total_trades", 0),
            "avg_spread_pips": info.get("stats", {}).get("avg_spread_pips"),
        })

    return {
        "success": True,
        "active_broker": _brokers.get("active"),
        "routing_strategy": _brokers.get("routing_strategy", "best_spread"),
        "total_connected": sum(1 for r in registered if r["connected"]),
        "total_registered": len(registered),
        "brokers": registered,
    }
