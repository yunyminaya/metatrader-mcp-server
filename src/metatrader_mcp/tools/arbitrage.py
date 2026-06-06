"""
Arbitrage — motor de arbitraje entre brokers.

Detecta diferencias de precio del mismo símbolo entre brokers
y ejecuta: Long en el broker barato, Short en el broker caro.

Cuando los precios convergen, cierra ambas posiciones.
Riesgo teóricamente cero (solo riesgo de ejecución).
"""
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "arbitrage.json")

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
                "min_spread_diff_pips": 5,
                "max_position_volume": 0.1,
                "active_arbitrages": [],
                "completed": [],
                "total_pnl": 0,
            }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def configure(min_spread_diff_pips: int = 5, max_volume: float = 0.1) -> Dict[str, Any]:
    _ensure()
    _state["min_spread_diff_pips"] = min_spread_diff_pips
    _state["max_position_volume"] = max_volume
    _save()
    return {"success": True, "config": {"min_spread_diff_pips": min_spread_diff_pips, "max_volume": max_volume}}


def start() -> Dict[str, Any]:
    _ensure()
    _state["enabled"] = True
    _save()
    return {"success": True, "message": "Arbitrage engine enabled"}


def stop() -> Dict[str, Any]:
    _ensure()
    _state["enabled"] = False
    _save()
    return {"success": True, "message": "Arbitrage engine disabled"}


def scan(client_a, name_a: str, client_b, name_b: str, symbol: str) -> Dict[str, Any]:
    """Scan for arbitrage opportunity between two brokers for a symbol.

    Returns:
        opportunity: dict with action or None
    """
    _ensure()
    if not _state.get("enabled"):
        return {"success": False, "error": "Arbitrage disabled"}

    try:
        price_a = client_a.market.get_symbol_price(symbol_name=symbol)
        price_b = client_b.market.get_symbol_price(symbol_name=symbol)
    except Exception as e:
        return {"success": False, "error": f"Price fetch failed: {e}"}

    if not price_a or not price_b:
        return {"success": False, "error": "No price data"}

    bid_a = price_a.get("bid", 0)
    ask_a = price_a.get("ask", 0)
    bid_b = price_b.get("bid", 0)
    ask_b = price_b.get("ask", 0)

    mid_a = (bid_a + ask_a) / 2
    mid_b = (bid_b + ask_b) / 2
    diff_pips = abs(mid_a - mid_b) * 10000  # approximate pip conversion

    result = {
        "symbol": symbol,
        "broker_a": name_a,
        "broker_b": name_b,
        "mid_a": mid_a,
        "mid_b": mid_b,
        "diff_pips": round(diff_pips, 1),
        "threshold_pips": _state["min_spread_diff_pips"],
    }

    if diff_pips < _state["min_spread_diff_pips"]:
        result["opportunity"] = False
        result["message"] = f"Diff {diff_pips:.1f}pips < {_state['min_spread_diff_pips']} threshold"
        return {"success": True, "scan": result}

    # Determine direction
    if mid_a < mid_b:
        cheap_broker = name_a
        cheap_ask = ask_a
        expensive_broker = name_b
        expensive_bid = bid_b
    else:
        cheap_broker = name_b
        cheap_ask = ask_b
        expensive_broker = name_a
        expensive_bid = bid_a

    volume = min(_state["max_position_volume"], 0.1)

    result["opportunity"] = True
    result["action"] = {
        "buy_on": cheap_broker,
        "buy_price": cheap_ask,
        "sell_on": expensive_broker,
        "sell_price": expensive_bid,
        "volume": volume,
        "expected_profit_pips": round(diff_pips - 2, 1),  # minus spread costs
    }

    return {"success": True, "scan": result}


def execute(client_a, name_a: str, client_b, name_b: str, symbol: str,
            volume: float = 0.01) -> Dict[str, Any]:
    """Execute an arbitrage: buy on cheap broker, sell on expensive broker."""
    _ensure()
    scan_result = scan(client_a, name_a, client_b, name_b, symbol)
    if not scan_result.get("success"):
        return scan_result

    scan_data = scan_result.get("scan", {})
    if not scan_data.get("opportunity"):
        return {"success": False, "error": "No opportunity", "scan": scan_data}

    action = scan_data["action"]

    # Buy on cheap broker
    try:
        buy_client = client_a if name_a == action["buy_on"] else client_b
        buy_result = buy_client.order.place_market_order(
            symbol=symbol, volume=volume, type="BUY"
        )
    except Exception as e:
        return {"success": False, "error": f"Buy failed on {action['buy_on']}: {e}"}

    # Sell on expensive broker
    try:
        sell_client = client_b if name_b == action["sell_on"] else client_a
        sell_result = sell_client.order.place_market_order(
            symbol=symbol, volume=volume, type="SELL"
        )
    except Exception as e:
        # Close buy position first
        try:
            buy_ticket = buy_result.get("ticket", 0)
            if buy_ticket:
                buy_client.order.close_position(id=str(buy_ticket))
        except Exception:
            pass
        return {"success": False, "error": f"Sell failed on {action['sell_on']}: {e}"}

    arb_entry = {
        "id": len(_state.get("completed", [])) + 1,
        "symbol": symbol,
        "buy_broker": action["buy_on"],
        "sell_broker": action["sell_on"],
        "volume": volume,
        "buy_price": action["buy_price"],
        "sell_price": action["sell_price"],
        "expected_pips": action["expected_profit_pips"],
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "buy_ticket": buy_result.get("ticket", "?"),
        "sell_ticket": sell_result.get("ticket", "?"),
    }

    _state.setdefault("active_arbitrages", []).append(arb_entry)
    _save()

    return {
        "success": True,
        "arbitrage": arb_entry,
        "message": f"Arbitrage opened: Long on {action['buy_on']} @ {action['buy_price']}, Short on {action['sell_on']} @ {action['sell_price']}",
    }


def close_arbitrage(arb_id: int, client_a, client_b) -> Dict[str, Any]:
    """Close an active arbitrage by ID."""
    _ensure()
    for arb in _state.get("active_arbitrages", []):
        if arb.get("id") == arb_id:
            try:
                # Close both legs
                buy_client = client_a if client_a else None
                sell_client = client_b if client_b else None

                buy_ticket = arb.get("buy_ticket")
                sell_ticket = arb.get("sell_ticket")

                results = {}
                if buy_ticket and buy_client:
                    try:
                        r = buy_client.order.close_position(id=str(buy_ticket))
                        results["buy_close"] = r
                    except Exception as e:
                        results["buy_close"] = {"error": str(e)}

                if sell_ticket and sell_client:
                    try:
                        r = sell_client.order.close_position(id=str(sell_ticket))
                        results["sell_close"] = r
                    except Exception as e:
                        results["sell_close"] = {"error": str(e)}

                arb["closed_at"] = datetime.now(timezone.utc).isoformat()
                arb["close_results"] = results
                _state.setdefault("completed", []).append(arb)
                _state["active_arbitrages"] = [a for a in _state["active_arbitrages"] if a["id"] != arb_id]
                _save()

                return {"success": True, "arbitrage_id": arb_id, "results": results}
            except Exception as e:
                return {"success": False, "error": str(e)}

    return {"success": False, "error": f"Arbitrage {arb_id} not found"}


def status() -> Dict[str, Any]:
    _ensure()
    return {
        "success": True,
        "arbitrage": {
            "enabled": _state.get("enabled"),
            "min_spread_diff_pips": _state.get("min_spread_diff_pips"),
            "active_arbitrages": len(_state.get("active_arbitrages", [])),
            "completed": len(_state.get("completed", [])),
            "total_pnl": round(_state.get("total_pnl", 0), 2),
            "details": _state.get("active_arbitrages", [])[-5:],
            "recent_completed": _state.get("completed", [])[-5:],
        },
    }
