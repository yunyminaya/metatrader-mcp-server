"""
MetaTrader MCP Server - Autonomous
Módulo para trading autónomo 100%.
"""

__version__ = "1.0.0"
__author__ = "MetaTrader MCP Team"

from .server import mcp
from .daemon_trading import DaemonTrading
from .database import TradingDatabase
from .risk_manager import RiskManager
from .ml_local import LocalMLScorer
from .notifier import TelegramNotifier, ConsoleNotifier
from .mt5_client import MT5Client

__all__ = [
    "mcp",
    "DaemonTrading",
    "TradingDatabase",
    "RiskManager",
    "LocalMLScorer",
    "TelegramNotifier",
    "ConsoleNotifier",
    "MT5Client",
]
