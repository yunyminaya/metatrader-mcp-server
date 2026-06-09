import MetaTrader5 as mt5
from ..exceptions import MarketDataError


def symbol_select(connection, symbol_name: str, enable: bool = True) -> dict:
    if not mt5.symbol_select(symbol_name, enable):
        err = mt5.last_error()
        return {"error": True, "message": f"Failed to {'enable' if enable else 'disable'} symbol '{symbol_name}': {err}", "data": None}
    return {"error": False, "message": f"Symbol '{symbol_name}' {'added to' if enable else 'removed from'} Market Watch", "data": {"symbol": symbol_name, "enabled": enable}}
