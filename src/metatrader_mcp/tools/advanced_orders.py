import logging
import time
import random
from typing import Dict, Any, Optional, Union
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


def place_bracket_order(client, symbol: str, order_type: str, volume: float,
                         stop_loss: float = 0, take_profit: float = 0,
                         entry_price: Optional[float] = None) -> Dict[str, Any]:
    if entry_price:
        result = client.order.place_pending_order(type=order_type, symbol=symbol, volume=volume, price=entry_price, stop_loss=stop_loss, take_profit=take_profit)
    else:
        result = client.order.place_market_order(type=order_type, symbol=symbol, volume=volume)
        if result.get("error") or not result.get("data"):
            return {"error": True, "message": f"Market entry failed: {result.get('message', 'unknown')}", "data": None}
        entry = result["data"]
        position_id = getattr(entry, 'order', None) or getattr(entry, 'ticket', None)
        if position_id and (stop_loss or take_profit):
            mod = client.order.modify_position(id=position_id, stop_loss=stop_loss, take_profit=take_profit)
            if mod.get("error"):
                logger.warning(f"Bracket SL/TP modify failed: {mod.get('message')}")
        return result
    return result


def place_oco_order(client, symbol: str, volume: float, take_profit_price: float,
                     stop_loss_price: float, order_type: str = "BUY") -> Dict[str, Any]:
    close_type = "SELL" if order_type.upper() == "BUY" else "BUY"
    tp_result = client.order.place_pending_order(
        type=close_type, symbol=symbol, volume=volume,
        price=take_profit_price
    )
    sl_result = client.order.place_pending_order(
        type=close_type, symbol=symbol, volume=volume,
        price=stop_loss_price
    )
    orders = []
    errors = []
    if tp_result.get("error"):
        errors.append(f"TP order failed: {tp_result.get('message')}")
        if sl_result.get("data"):
            client.order.cancel_pending_order(sl_result["data"])
    else:
        orders.append({"type": "TP", "order_id": tp_result["data"]})
    if sl_result.get("error"):
        errors.append(f"SL order failed: {sl_result.get('message')}")
        if tp_result.get("data"):
            client.order.cancel_pending_order(tp_result["data"])
    else:
        orders.append({"type": "SL", "order_id": sl_result["data"]})
    success = len(orders) == 2
    return {
        "error": not success,
        "message": f"OCO {'placed' if success else 'failed'}: {', '.join(errors) if errors else 'OK'}",
        "data": {"orders": orders, "symbol": symbol, "volume": volume}
    }


def place_oto_order(client, symbol: str, entry_type: str, entry_price: float,
                     entry_volume: float, stop_loss: float = 0, take_profit: float = 0) -> Dict[str, Any]:
    result = client.order.place_pending_order(
        type=entry_type, symbol=symbol, volume=entry_volume,
        price=entry_price, stop_loss=stop_loss, take_profit=take_profit
    )
    return result


def scale_into_position(client, symbol: str, order_type: str, total_volume: float,
                         slices: int = 3, price_gap_pips: float = 10,
                         stop_loss: float = 0, take_profit: float = 0) -> Dict[str, Any]:
    if slices < 2:
        return {"error": True, "message": "Need at least 2 slices", "data": None}
    volume_per_slice = round(total_volume / slices, 2)
    symbol_info = client.market.get_symbol_info(symbol)
    if not symbol_info:
        return {"error": True, "message": f"Symbol {symbol} not found", "data": None}
    price = client.market.get_symbol_price(symbol)
    if not price:
        return {"error": True, "message": f"No price for {symbol}", "data": None}
    current_price = price.get("bid") if order_type.upper() == "BUY" else price.get("ask")
    if not current_price:
        return {"error": True, "message": "No current price", "data": None}
    pip_size = 10 ** -(symbol_info.get("digits", 5) - 1) if symbol_info.get("digits", 5) > 3 else 0.0001
    gap = price_gap_pips * pip_size
    placed = []
    errors = []
    for i in range(slices):
        if order_type.upper() == "BUY":
            entry = current_price - (i * gap)
            if entry <= 0:
                errors.append(f"Slice {i}: price below zero")
                continue
        else:
            entry = current_price + (i * gap)
        result = client.order.place_pending_order(
            type=order_type, symbol=symbol, volume=volume_per_slice,
            price=entry, stop_loss=stop_loss, take_profit=take_profit
        )
        if result.get("error"):
            errors.append(f"Slice {i}: {result.get('message')}")
        else:
            placed.append({"slice": i, "price": entry, "volume": volume_per_slice, "order_id": result.get("data")})
    return {
        "error": len(placed) == 0,
        "message": f"Placed {len(placed)}/{slices} slices" + (f" ({len(errors)} errors)" if errors else ""),
        "data": {"placed_slices": placed, "errors": errors, "symbol": symbol, "total_placed_volume": sum(p["volume"] for p in placed)}
    }


def place_grid_orders(client, symbol: str, grid_type: str = "BUY_LIMIT",
                       levels: int = 5, step_pips: float = 20,
                       volume_per_level: float = 0.01,
                       stop_loss: float = 0, take_profit: float = 0) -> Dict[str, Any]:
    symbol_info = client.market.get_symbol_info(symbol)
    if not symbol_info:
        return {"error": True, "message": f"Symbol {symbol} not found", "data": None}
    price = client.market.get_symbol_price(symbol)
    if not price:
        return {"error": True, "message": f"No price for {symbol}", "data": None}
    pip_size = 10 ** -(symbol_info.get("digits", 5) - 1) if symbol_info.get("digits", 5) > 3 else 0.0001
    step = step_pips * pip_size
    base_price = price.get("bid") if "SELL" in grid_type.upper() else price.get("ask")
    if not base_price:
        return {"error": True, "message": "No base price", "data": None}
    placed = []
    errors = []
    for i in range(levels):
        if "BUY" in grid_type.upper():
            entry = base_price - ((i + 1) * step)
        else:
            entry = base_price + ((i + 1) * step)
        if entry <= 0:
            errors.append(f"Level {i + 1}: price {entry} invalid")
            continue
        result = client.order.place_pending_order(
            type=grid_type, symbol=symbol, volume=volume_per_level,
            price=entry, stop_loss=stop_loss, take_profit=take_profit
        )
        if result.get("error"):
            errors.append(f"Level {i + 1}: {result.get('message')}")
        else:
            placed.append({"level": i + 1, "price": entry, "volume": volume_per_level, "order_id": result.get("data")})
    return {
        "error": len(placed) == 0,
        "message": f"Grid {grid_type}: {len(placed)}/{levels} levels placed",
        "data": {"grid_type": grid_type, "symbol": symbol, "levels": placed, "errors": errors, "step_pips": step_pips}
    }


def partial_close(client, position_id: Union[int, str], volume: float) -> Dict[str, Any]:
    return client.order.partial_close_position(position_id, volume)


def partial_close_percent(client, position_id: Union[int, str], percent: float) -> Dict[str, Any]:
    return client.order.partial_close_percent(position_id, percent)


def split_position_into_targets(client, position_id: Union[int, str],
                                 targets: list, stop_loss: Optional[float] = None) -> Dict[str, Any]:
    positions = client.order.get_positions_by_id(position_id)
    if positions.index.size == 0:
        return {"error": True, "message": f"Position {position_id} not found", "data": None}
    pos = positions.iloc[0]
    total_vol = pos["volume"]
    pos_type = pos["type"]
    entry = pos["price_open"]
    symbol = pos["symbol"]
    if sum(t["percent"] for t in targets) > 100:
        return {"error": True, "message": "Total target percent exceeds 100%", "data": None}
    results = []
    remaining = total_vol
    for i, target in enumerate(targets):
        vol = round(total_vol * target["percent"] / 100, 2)
        if vol <= 0 or vol > remaining:
            continue
        tp = target.get("price", 0)
        sl = target.get("stop_loss", stop_loss or 0)
        if i == 0:
            mod = client.order.modify_position(id=position_id, stop_loss=sl, take_profit=tp)
            results.append({"target": i + 1, "action": "modify_existing", "volume": vol, "tp": tp, "sl": sl, "result": mod})
        else:
            vol = min(vol, remaining)
            if vol < 0.01:
                break
            result = client.order.partial_close_percent(position_id, round(vol / total_vol * 100, 1))
            results.append({"target": i + 1, "action": "partial_close_profit", "volume": vol, "tp": tp, "result": result})
        remaining -= vol
    return {
        "error": False,
        "message": f"Split position {position_id} into {len(targets)} targets",
        "data": {"symbol": symbol, "position_id": position_id, "total_volume": total_vol, "targets": results, "remaining_volume": round(remaining, 2)}
    }
