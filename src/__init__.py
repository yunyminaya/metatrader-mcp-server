"""
MetaTrader MCP Server - Autonomous
Módulo para trading autónomo 100%.
"""

__version__ = "1.0.0"
__author__ = "MetaTrader MCP Team"

# Imports lazy para evitar errores si MT5 no está instalado
# (ej: en desarrollo o en sistemas no-Windows)

def __getattr__(name):
    """Lazy imports para evitar ImportError si MetaTrader5 no está disponible."""
    _exports = {
        "mcp": ".server:mcp",
        "DaemonTrading": ".daemon_trading:DaemonTrading",
        "TradingDatabase": ".database:TradingDatabase",
        "RiskManager": ".risk_manager:RiskManager",
        "LocalMLScorer": ".ml_local:LocalMLScorer",
        "TelegramNotifier": ".notifier:TelegramNotifier",
        "ConsoleNotifier": ".notifier:ConsoleNotifier",
        "MT5Client": ".mt5_client:MT5Client",
    }

    if name in _exports:
        module_path, class_name = _exports[name].split(":")
        import importlib
        module = importlib.import_module(module_path, package="src")
        return getattr(module, class_name)

    raise AttributeError(f"module 'src' has no attribute {name}")

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
