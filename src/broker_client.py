#!/usr/bin/env python3
"""
Broker Client - Abstract Base Class
Interfaz común para MT4 y MT5.
Cualquier broker client debe implementar estos métodos.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any


class BrokerClient(ABC):
    """
    Interfaz abstracta para clientes de broker (MT4/MT5).
    Todos los métodos que el servidor MCP necesita están definidos aquí.
    """

    # ============ Connection ============

    @abstractmethod
    def connect(self) -> bool:
        """Conectar al broker. Retorna True si exitoso."""
        ...

    @abstractmethod
    def disconnect(self):
        """Desconectar del broker."""
        ...

    @abstractmethod
    def reconnect(self) -> bool:
        """Reconectar al broker."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Verificar si está conectado."""
        ...

    @abstractmethod
    def get_broker_type(self) -> str:
        """Retorna 'mt4' o 'mt5'."""
        ...

    # ============ Account Info ============

    @abstractmethod
    def get_account_info(self) -> Dict[str, Any]:
        """Obtener información de la cuenta."""
        ...

    @abstractmethod
    def get_balance(self) -> float:
        """Obtener balance actual."""
        ...

    @abstractmethod
    def get_equity(self) -> float:
        """Obtener equity actual."""
        ...

    @abstractmethod
    def get_margin_info(self) -> Dict[str, Any]:
        """Obtener información de margen."""
        ...

    # ============ Positions ============

    @abstractmethod
    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Obtener posiciones abiertas."""
        ...

    @abstractmethod
    def close_position(self, ticket: int) -> Dict[str, Any]:
        """Cerrar una posición por ticket."""
        ...

    @abstractmethod
    def close_all_positions(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Cerrar todas las posiciones."""
        ...

    @abstractmethod
    def modify_position(self, ticket: int,
                       stop_loss: Optional[float] = None,
                       take_profit: Optional[float] = None) -> Dict[str, Any]:
        """Modificar SL/TP de una posición."""
        ...

    # ============ Orders ============

    @abstractmethod
    def place_market_order(self, symbol: str, order_type: str,
                          volume: float, stop_loss: Optional[float] = None,
                          take_profit: Optional[float] = None,
                          comment: str = "") -> Dict[str, Any]:
        """Colocar orden de mercado."""
        ...

    @abstractmethod
    def place_pending_order(self, symbol: str, order_type: str,
                           volume: float, price: float,
                           stop_loss: Optional[float] = None,
                           take_profit: Optional[float] = None,
                           expiration: Optional[str] = None) -> Dict[str, Any]:
        """Colocar orden pendiente."""
        ...

    @abstractmethod
    def cancel_order(self, order_id: int) -> Dict[str, Any]:
        """Cancelar orden pendiente."""
        ...

    @abstractmethod
    def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Cancelar todas las órdenes pendientes."""
        ...

    @abstractmethod
    def get_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Obtener órdenes pendientes."""
        ...

    # ============ Market Data ============

    @abstractmethod
    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Obtener información del símbolo."""
        ...

    @abstractmethod
    def get_tick(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Obtener tick actual."""
        ...

    @abstractmethod
    def get_candles(self, symbol: str, timeframe: str,
                   count: int = 100) -> List[Dict[str, Any]]:
        """Obtener velas históricas."""
        ...

    @abstractmethod
    def get_symbols(self, group: str = "*") -> List[str]:
        """Obtener lista de símbolos disponibles."""
        ...

    # ============ Helper (optional override) ============

    def set_trailing_stop(self, ticket: int, points: int) -> Dict[str, Any]:
        """Activar trailing stop. Default: no soportado nativamente."""
        return {"success": False, "error": "Trailing stop no soportado nativamente"}
