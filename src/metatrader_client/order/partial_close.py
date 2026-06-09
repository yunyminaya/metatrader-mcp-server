from typing import Union
from ..types import TradeRequestActions
from .send_order import send_order
from .get_positions_by_id import get_positions_by_id


def partial_close_position(connection, id: Union[str, int], volume: float):
    try:
        position_id = int(id)
    except ValueError:
        return {"error": True, "message": f"Invalid position ID '{id}'", "data": None}
    if volume <= 0:
        return {"error": True, "message": "Volume must be positive", "data": None}
    positions = get_positions_by_id(connection, position_id)
    if positions.index.size == 0:
        return {"error": True, "message": f"Position ID '{id}' not found", "data": None}
    position = positions.iloc[0]
    current_vol = position["volume"]
    if volume > current_vol:
        return {"error": True, "message": f"Cannot close {volume} — position only has {current_vol}", "data": None}
    response = send_order(
        connection,
        action=TradeRequestActions.DEAL,
        position=position_id,
        order_type="SELL" if position["type"] == "BUY" else "BUY",
        symbol=position["symbol"],
        volume=volume,
    )
    if response.get("error", response.get("success")) is False:
        return {"error": True, "message": response.get("message", "Partial close failed"), "data": None}
    remaining = round(current_vol - volume, 2)
    return {
        "error": False,
        "message": f"Partially closed {volume} of position {position_id}. Remaining: {remaining}",
        "data": {"closed_volume": volume, "remaining_volume": remaining, "position_id": position_id, "symbol": position["symbol"]}
    }


def partial_close_percent(connection, id: Union[str, int], percent: float):
    try:
        position_id = int(id)
    except ValueError:
        return {"error": True, "message": f"Invalid position ID '{id}'", "data": None}
    if percent <= 0 or percent > 100:
        return {"error": True, "message": "Percent must be between 1 and 100", "data": None}
    positions = get_positions_by_id(connection, position_id)
    if positions.index.size == 0:
        return {"error": True, "message": f"Position ID '{id}' not found", "data": None}
    position = positions.iloc[0]
    volume = round(position["volume"] * percent / 100, 2)
    if volume <= 0:
        return {"error": True, "message": f"Volume too small ({volume}). Increase percent.", "data": None}
    return partial_close_position(connection, position_id, volume)
