"""
MetaTrader MCP Client package.

This package provides a modular interface for communicating with the MetaTrader 5 terminal.
On Windows: native MetaTrader5 connection.
On Mac/Linux: remote connection to Windows machine via HTTP.
"""

try:
    from .client import MT5Client
    from .client_order import MT5Order
except (ImportError, ModuleNotFoundError):
    MT5Client = None
    MT5Order = None

from .exceptions import (
    MT5ClientError,
    ConnectionError,
    OrderError,
    MarketError,
    AccountError,
    HistoryError
)

__all__ = [

    "MT5Client",
    "MT5Order",

    "MT5ClientError",
    "ConnectionError",
    "OrderError",
    "MarketError",
    "AccountError",
    "HistoryError",
]
