#!/usr/bin/env python3
"""
Wine MT5 Bridge — Runs inside Wine Python to handle MetaTrader5 operations.
Communicates via JSON over stdin/stdout with the macOS-native MCP server.

Usage (called automatically by wine_adapter.py):
    WINEPREFIX=... wine C:\\Python312\\python.exe wine_bridge.py
"""

import sys
import json
import traceback
import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime


def serialize(obj):
    """Convert MT5 objects to JSON-serializable dicts."""
    if obj is None:
        return None
    if isinstance(obj, (int, float, str, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (list, tuple)):
        return [serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: serialize(v) for k, v in obj.items()}
    if isinstance(obj, pd.DataFrame):
        return obj.to_dict(orient="records")
    if hasattr(obj, "_asdict"):
        return serialize(obj._asdict())
    if hasattr(obj, "__dict__"):
        return {k: serialize(v) for k, v in obj.__dict__.items() if not k.startswith("_")}
    return str(obj)


def handle_command(cmd):
    """Execute a single MT5 command and return the result."""
    action = cmd.get("action")
    
    # ── Connection ──
    if action == "initialize":
        path = cmd.get("path")
        login = cmd.get("login")
        password = cmd.get("password")
        server = cmd.get("server")
        kwargs = {}
        if path:
            kwargs["path"] = path
        if login:
            kwargs["login"] = int(login)
        if password:
            kwargs["password"] = password
        if server:
            kwargs["server"] = server
        result = mt5.initialize(**kwargs)
        return {"success": result, "error": mt5.last_error()}
    
    if action == "shutdown":
        mt5.shutdown()
        return {"success": True}
    
    if action == "last_error":
        return {"data": mt5.last_error()}
    
    if action == "version":
        v = mt5.version()
        return {"data": serialize(v)}
    
    if action == "terminal_info":
        info = mt5.terminal_info()
        return {"data": serialize(info)}
    
    # ── Account ──
    if action == "account_info":
        info = mt5.account_info()
        return {"data": serialize(info)}
    
    # ── Symbols ──
    if action == "symbols_get":
        group = cmd.get("group")
        symbols = mt5.symbols_get(group) if group else mt5.symbols_get()
        return {"data": serialize(symbols)}
    
    if action == "symbol_info":
        symbol = cmd.get("symbol")
        info = mt5.symbol_info(symbol)
        return {"data": serialize(info)}
    
    if action == "symbol_info_tick":
        symbol = cmd.get("symbol")
        tick = mt5.symbol_info_tick(symbol)
        return {"data": serialize(tick)}
    
    # ── Market Data ──
    if action == "copy_rates_from_pos":
        symbol = cmd.get("symbol")
        timeframe = cmd.get("timeframe")
        start_pos = cmd.get("start_pos")
        count = cmd.get("count")
        rates = mt5.copy_rates_from_pos(symbol, timeframe, start_pos, count)
        return {"data": serialize(rates)}
    
    if action == "copy_rates_from":
        symbol = cmd.get("symbol")
        timeframe = cmd.get("timeframe")
        dt_from = cmd.get("datetime_from")
        count = cmd.get("count")
        if isinstance(dt_from, str):
            dt_from = datetime.fromisoformat(dt_from)
        rates = mt5.copy_rates_from(symbol, timeframe, dt_from, count)
        return {"data": serialize(rates)}
    
    if action == "copy_rates_range":
        symbol = cmd.get("symbol")
        timeframe = cmd.get("timeframe")
        dt_from = cmd.get("datetime_from")
        dt_to = cmd.get("datetime_to")
        if isinstance(dt_from, str):
            dt_from = datetime.fromisoformat(dt_from)
        if isinstance(dt_to, str):
            dt_to = datetime.fromisoformat(dt_to)
        rates = mt5.copy_rates_range(symbol, timeframe, dt_from, dt_to)
        return {"data": serialize(rates)}
    
    if action == "copy_ticks_from":
        symbol = cmd.get("symbol")
        dt_from = cmd.get("datetime_from")
        count = cmd.get("count")
        flags = cmd.get("flags", mt5.COPY_TICKS_ALL)
        if isinstance(dt_from, str):
            dt_from = datetime.fromisoformat(dt_from)
        ticks = mt5.copy_ticks_from(symbol, dt_from, count, flags)
        return {"data": serialize(ticks)}
    
    # ── Positions ──
    if action == "positions_get":
        group = cmd.get("group")
        symbol = cmd.get("symbol")
        ticket = cmd.get("ticket")
        kwargs = {}
        if group:
            kwargs["group"] = group
        if symbol:
            kwargs["symbol"] = symbol
        if ticket:
            kwargs["ticket"] = int(ticket)
        positions = mt5.positions_get(**kwargs) if kwargs else mt5.positions_get()
        return {"data": serialize(positions)}
    
    # ── Orders ──
    if action == "orders_get":
        group = cmd.get("group")
        symbol = cmd.get("symbol")
        ticket = cmd.get("ticket")
        kwargs = {}
        if group:
            kwargs["group"] = group
        if symbol:
            kwargs["symbol"] = symbol
        if ticket:
            kwargs["ticket"] = int(ticket)
        orders = mt5.orders_get(**kwargs) if kwargs else mt5.orders_get()
        return {"data": serialize(orders)}
    
    # ── Order Send ──
    if action == "order_send":
        request = cmd.get("request")
        result = mt5.order_send(request)
        return {"data": serialize(result)}
    
    # ── History ──
    if action == "history_deals_get":
        group = cmd.get("group")
        symbol = cmd.get("symbol")
        ticket = cmd.get("ticket")
        dt_from = cmd.get("datetime_from")
        dt_to = cmd.get("datetime_to")
        kwargs = {}
        if group:
            kwargs["group"] = group
        if symbol:
            kwargs["symbol"] = symbol
        if ticket:
            kwargs["ticket"] = int(ticket)
        if dt_from:
            if isinstance(dt_from, str):
                dt_from = datetime.fromisoformat(dt_from)
            kwargs["date_from"] = dt_from
        if dt_to:
            if isinstance(dt_to, str):
                dt_to = datetime.fromisoformat(dt_to)
            kwargs["date_to"] = dt_to
        deals = mt5.history_deals_get(**kwargs) if kwargs else mt5.history_deals_get()
        return {"data": serialize(deals)}
    
    if action == "history_orders_get":
        group = cmd.get("group")
        symbol = cmd.get("symbol")
        ticket = cmd.get("ticket")
        dt_from = cmd.get("datetime_from")
        dt_to = cmd.get("datetime_to")
        kwargs = {}
        if group:
            kwargs["group"] = group
        if symbol:
            kwargs["symbol"] = symbol
        if ticket:
            kwargs["ticket"] = int(ticket)
        if dt_from:
            if isinstance(dt_from, str):
                dt_from = datetime.fromisoformat(dt_from)
            kwargs["date_from"] = dt_from
        if dt_to:
            if isinstance(dt_to, str):
                dt_to = datetime.fromisoformat(dt_to)
            kwargs["date_to"] = dt_to
        orders = mt5.history_orders_get(**kwargs) if kwargs else mt5.history_orders_get()
        return {"data": serialize(orders)}
    
    if action == "history_deals_total":
        dt_from = cmd.get("datetime_from")
        dt_to = cmd.get("datetime_to")
        if isinstance(dt_from, str):
            dt_from = datetime.fromisoformat(dt_from)
        if isinstance(dt_to, str):
            dt_to = datetime.fromisoformat(dt_to)
        total = mt5.history_deals_total(dt_from, dt_to)
        return {"data": total}
    
    if action == "history_orders_total":
        dt_from = cmd.get("datetime_from")
        dt_to = cmd.get("datetime_to")
        if isinstance(dt_from, str):
            dt_from = datetime.fromisoformat(dt_from)
        if isinstance(dt_to, str):
            dt_to = datetime.fromisoformat(dt_to)
        total = mt5.history_orders_total(dt_from, dt_to)
        return {"data": total}
    
    return {"error": f"Unknown action: {action}"}


def main():
    """Main loop: read JSON commands from stdin, write JSON responses to stdout."""
    # Send ready signal
    sys.stdout.write(json.dumps({"status": "ready"}) + "\n")
    sys.stdout.flush()
    
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            cmd = json.loads(line)
            result = handle_command(cmd)
        except Exception as e:
            result = {"error": str(e), "traceback": traceback.format_exc()}
        
        sys.stdout.write(json.dumps(result) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
