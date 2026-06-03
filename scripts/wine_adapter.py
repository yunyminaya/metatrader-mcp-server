#!/usr/bin/env python3
"""
Wine MT5 Adapter — Drop-in replacement for MetaTrader5 on macOS.
Runs natively on macOS and delegates MT5 operations to wine_bridge.py
running inside Wine Python via subprocess.

Usage:
    This module is used automatically when MetaTrader5 is not available
    on the system (macOS). The metatrader_client modules import this
    instead of the real MetaTrader5 package.
"""

import os
import sys
import json
import subprocess
import platform
import threading
from typing import Optional, Any


class WineMT5Adapter:
    """Adapter that communicates with MetaTrader5 via Wine bridge."""

    def __init__(self):
        self._process = None
        self._lock = threading.Lock()
        self._wine_bin = None
        self._wineprefix = None
        self._wine_python = None
        self._bridge_path = None
        self._initialized = False
        self._detect_wine()

    def _detect_wine(self):
        """Auto-detect Wine and MT5 installation on macOS."""
        if platform.system() != "Darwin":
            return

        # Find MetaTrader 5.app
        mt5_app = "/Applications/MetaTrader 5.app"
        if not os.path.exists(mt5_app):
            return

        # Wine binary
        wine_bin = os.path.join(mt5_app, "Contents/SharedSupport/wine/bin/wine")
        if os.path.exists(wine_bin):
            self._wine_bin = wine_bin

        # Find Wine prefix
        prefix_paths = [
            os.path.expanduser("~/Library/Application Support/net.metaquotes.wine.metatrader5"),
            os.path.expanduser("~/Library/Application Support/net.metaquotes.wine.MetaTrader5.tastyfx"),
        ]
        for prefix in prefix_paths:
            if os.path.isdir(os.path.join(prefix, "drive_c")):
                self._wineprefix = prefix
                break

        # Wine Python
        if self._wineprefix:
            python_exe = os.path.join(
                self._wineprefix, "drive_c/Python312/python.exe"
            )
            if os.path.exists(python_exe):
                self._wine_python = python_exe

        # Bridge script (same directory as this file)
        bridge = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wine_bridge.py")
        if os.path.exists(bridge):
            self._bridge_path = bridge

    @property
    def available(self) -> bool:
        """Check if Wine MT5 bridge is available."""
        return all([
            self._wine_bin,
            self._wineprefix,
            self._wine_python,
            self._bridge_path,
        ])

    def _start_bridge(self):
        """Start the Wine bridge subprocess."""
        if self._process and self._process.poll() is None:
            return  # Already running

        # Convert bridge path to Windows path
        bridge_win = self._bridge_path.replace("/", "\\")
        # Wine needs Windows-style path
        if bridge_win.startswith("\\Users"):
            bridge_win = f"Z:{bridge_win}"

        env = os.environ.copy()
        env["WINEPREFIX"] = self._wineprefix
        env["WINEDEBUG"] = "-all"  # Suppress Wine debug output

        self._process = subprocess.Popen(
            [self._wine_bin, self._wine_python, bridge_win],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            bufsize=0,
        )

        # Wait for ready signal
        line = self._process.stdout.readline().decode().strip()
        if not line:
            raise ConnectionError("Wine bridge failed to start")
        ready = json.loads(line)
        if ready.get("status") != "ready":
            raise ConnectionError(f"Wine bridge unexpected response: {ready}")

    def _send_command(self, cmd: dict) -> dict:
        """Send a command to the Wine bridge and return the response."""
        with self._lock:
            if not self._process or self._process.poll() is not None:
                self._start_bridge()

            self._process.stdin.write((json.dumps(cmd) + "\n").encode())
            self._process.stdin.flush()

            line = self._process.stdout.readline().decode().strip()
            if not line:
                raise ConnectionError("Wine bridge closed unexpectedly")
            return json.loads(line)

    def stop(self):
        """Stop the Wine bridge subprocess."""
        if self._process:
            try:
                self._send_command({"action": "shutdown"})
            except Exception:
                pass
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except Exception:
                self._process.kill()
            self._process = None

    # ── MetaTrader5 API Compatibility ──

    def initialize(
        self,
        path: Optional[str] = None,
        login: Optional[int] = None,
        password: Optional[str] = None,
        server: Optional[str] = None,
        timeout: Optional[int] = None,
        portable: Optional[bool] = None,
    ) -> bool:
        """Initialize connection to MT5 terminal."""
        cmd = {"action": "initialize"}
        if path:
            cmd["path"] = path
        if login:
            cmd["login"] = login
        if password:
            cmd["password"] = password
        if server:
            cmd["server"] = server

        result = self._send_command(cmd)
        self._initialized = result.get("success", False)
        return self._initialized

    def shutdown(self):
        """Shutdown MT5 connection."""
        self._send_command({"action": "shutdown"})
        self._initialized = False

    def last_error(self):
        """Get last error."""
        result = self._send_command({"action": "last_error"})
        return tuple(result.get("data", (-1, "Unknown error")))

    def version(self):
        """Get MT5 version."""
        result = self._send_command({"action": "version"})
        return result.get("data")

    def terminal_info(self):
        """Get terminal info."""
        result = self._send_command({"action": "terminal_info"})
        return result.get("data")

    def account_info(self):
        """Get account info."""
        result = self._send_command({"action": "account_info"})
        return result.get("data")

    def symbols_get(self, group: Optional[str] = None):
        """Get symbols."""
        cmd = {"action": "symbols_get"}
        if group:
            cmd["group"] = group
        result = self._send_command(cmd)
        return result.get("data")

    def symbol_info(self, symbol: str):
        """Get symbol info."""
        result = self._send_command({"action": "symbol_info", "symbol": symbol})
        return result.get("data")

    def symbol_info_tick(self, symbol: str):
        """Get symbol tick."""
        result = self._send_command({"action": "symbol_info_tick", "symbol": symbol})
        return result.get("data")

    def copy_rates_from_pos(self, symbol, timeframe, start_pos, count):
        """Get candles from position."""
        result = self._send_command({
            "action": "copy_rates_from_pos",
            "symbol": symbol,
            "timeframe": timeframe,
            "start_pos": start_pos,
            "count": count,
        })
        return result.get("data")

    def copy_rates_from(self, symbol, timeframe, datetime_from, count):
        """Get candles from datetime."""
        result = self._send_command({
            "action": "copy_rates_from",
            "symbol": symbol,
            "timeframe": timeframe,
            "datetime_from": datetime_from.isoformat() if hasattr(datetime_from, "isoformat") else str(datetime_from),
            "count": count,
        })
        return result.get("data")

    def copy_rates_range(self, symbol, timeframe, datetime_from, datetime_to):
        """Get candles in date range."""
        result = self._send_command({
            "action": "copy_rates_range",
            "symbol": symbol,
            "timeframe": timeframe,
            "datetime_from": datetime_from.isoformat() if hasattr(datetime_from, "isoformat") else str(datetime_from),
            "datetime_to": datetime_to.isoformat() if hasattr(datetime_to, "isoformat") else str(datetime_to),
        })
        return result.get("data")

    def positions_get(self, group=None, symbol=None, ticket=None):
        """Get open positions."""
        cmd = {"action": "positions_get"}
        if group:
            cmd["group"] = group
        if symbol:
            cmd["symbol"] = symbol
        if ticket:
            cmd["ticket"] = ticket
        result = self._send_command(cmd)
        return result.get("data")

    def orders_get(self, group=None, symbol=None, ticket=None):
        """Get pending orders."""
        cmd = {"action": "orders_get"}
        if group:
            cmd["group"] = group
        if symbol:
            cmd["symbol"] = symbol
        if ticket:
            cmd["ticket"] = ticket
        result = self._send_command(cmd)
        return result.get("data")

    def order_send(self, request):
        """Send an order."""
        result = self._send_command({"action": "order_send", "request": request})
        return result.get("data")

    def history_deals_get(self, group=None, symbol=None, ticket=None, date_from=None, date_to=None):
        """Get historical deals."""
        cmd = {"action": "history_deals_get"}
        if group:
            cmd["group"] = group
        if symbol:
            cmd["symbol"] = symbol
        if ticket:
            cmd["ticket"] = ticket
        if date_from:
            cmd["datetime_from"] = date_from.isoformat() if hasattr(date_from, "isoformat") else str(date_from)
        if date_to:
            cmd["datetime_to"] = date_to.isoformat() if hasattr(date_to, "isoformat") else str(date_to)
        result = self._send_command(cmd)
        return result.get("data")

    def history_orders_get(self, group=None, symbol=None, ticket=None, date_from=None, date_to=None):
        """Get historical orders."""
        cmd = {"action": "history_orders_get"}
        if group:
            cmd["group"] = group
        if symbol:
            cmd["symbol"] = symbol
        if ticket:
            cmd["ticket"] = ticket
        if date_from:
            cmd["datetime_from"] = date_from.isoformat() if hasattr(date_from, "isoformat") else str(date_from)
        if date_to:
            cmd["datetime_to"] = date_to.isoformat() if hasattr(date_to, "isoformat") else str(date_to)
        result = self._send_command(cmd)
        return result.get("data")

    def history_deals_total(self, date_from, date_to):
        """Get total number of deals in date range."""
        result = self._send_command({
            "action": "history_deals_total",
            "datetime_from": date_from.isoformat() if hasattr(date_from, "isoformat") else str(date_from),
            "datetime_to": date_to.isoformat() if hasattr(date_to, "isoformat") else str(date_to),
        })
        return result.get("data", 0)

    def history_orders_total(self, date_from, date_to):
        """Get total number of orders in date range."""
        result = self._send_command({
            "action": "history_orders_total",
            "datetime_from": date_from.isoformat() if hasattr(date_from, "isoformat") else str(date_from),
            "datetime_to": date_to.isoformat() if hasattr(date_to, "isoformat") else str(date_to),
        })
        return result.get("data", 0)

    # ── MT5 Constants (common ones) ──
    TIMEFRAME_M1 = 1
    TIMEFRAME_M2 = 2
    TIMEFRAME_M3 = 3
    TIMEFRAME_M4 = 4
    TIMEFRAME_M5 = 5
    TIMEFRAME_M6 = 6
    TIMEFRAME_M10 = 10
    TIMEFRAME_M12 = 12
    TIMEFRAME_M15 = 15
    TIMEFRAME_M20 = 20
    TIMEFRAME_M30 = 30
    TIMEFRAME_H1 = 16385
    TIMEFRAME_H2 = 16386
    TIMEFRAME_H3 = 16387
    TIMEFRAME_H4 = 16388
    TIMEFRAME_H6 = 16390
    TIMEFRAME_H8 = 16392
    TIMEFRAME_H12 = 16396
    TIMEFRAME_D1 = 16408
    TIMEFRAME_W1 = 32769
    TIMEFRAME_MN1 = 43201

    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TYPE_BUY_LIMIT = 2
    ORDER_TYPE_SELL_LIMIT = 3
    ORDER_TYPE_BUY_STOP = 4
    ORDER_TYPE_SELL_STOP = 5
    ORDER_TYPE_BUY_STOP_LIMIT = 6
    ORDER_TYPE_SELL_STOP_LIMIT = 7

    ORDER_TIME_GTC = 0
    ORDER_TIME_DAY = 1
    ORDER_TIME_SPECIFIED = 2
    ORDER_TIME_SPECIFIED_DAY = 3

    ORDER_FILLING_FOK = 0
    ORDER_FILLING_IOC = 1
    ORDER_FILLING_RETURN = 2

    TRADE_RETCODE_DONE = 10009
    TRADE_RETCODE_DONE_PARTIAL = 10010
    TRADE_RETCODE_ERROR = 10011

    COPY_TICKS_ALL = 2
    COPY_TICKS_INFO = 1
    COPY_TICKS_TRADE = 0


# Singleton instance
_adapter = None


def get_adapter() -> Optional[WineMT5Adapter]:
    """Get or create the Wine MT5 adapter singleton."""
    global _adapter
    if _adapter is None:
        _adapter = WineMT5Adapter()
    return _adapter


def is_macos() -> bool:
    """Check if running on macOS."""
    return platform.system() == "Darwin"


def needs_wine_bridge() -> bool:
    """Check if the Wine bridge is needed (macOS without native MT5)."""
    if not is_macos():
        return False
    try:
        import MetaTrader5
        return False
    except ImportError:
        adapter = get_adapter()
        return adapter.available
