#!/usr/bin/env python3
"""
MT4 Bridge Client - Cliente para MetaTrader 4
Se comunica con MT4 mediante archivos JSON (file-based bridge).
Requiere el EA bridge (MCP_Bridge.mq4) corriendo en MT4.

Arquitectura:
    Python (este archivo) ←→ archivos JSON ←→ EA .mq4 (en MT4)

Flujo:
    1. Python escribe un comando en commands/cmd_XXXXX.json
    2. EA lee el comando, ejecuta en MT4, escribe respuesta en responses/resp_XXXXX.json
    3. Python lee la respuesta y la retorna
"""

import json
import time
import uuid
import os
from pathlib import Path
from typing import Dict, List, Optional, Any

try:
    from .broker_client import BrokerClient
except ImportError:
    from broker_client import BrokerClient


class MT4BridgeClient(BrokerClient):
    """
    Cliente para MT4 que se comunica via archivos JSON.
    
    Requiere:
    1. El EA 'MCP_Bridge.mq4' instalado y corriendo en MT4
    2. Carpeta compartida configurada (default: ~/MT4_Bridge/)
    3. MT4 con "Allow external experts" habilitado
    
    El EA monitorea la carpeta de comandos cada segundo,
    ejecuta las operaciones en MT4 y escribe las respuestas.
    """

    def __init__(self, bridge_path: Optional[str] = None,
                 login: Optional[int] = None,
                 password: Optional[str] = None,
                 server: Optional[str] = None,
                 timeout: float = 10.0):
        """
        Args:
            bridge_path: Ruta a la carpeta del bridge (default: ~/MT4_Bridge/)
            login: Número de cuenta MT4 (informativo)
            password: Password MT4 (informativo, el login lo hace el EA)
            server: Servidor MT4 (informativo)
            timeout: Segundos máximos a esperar por respuesta del EA
        """
        self.login = login
        self.password = password
        self.server = server
        self.timeout = timeout

        # Configurar rutas del bridge
        if bridge_path:
            self.bridge_dir = Path(bridge_path)
        else:
            self.bridge_dir = Path.home() / "MT4_Bridge"

        self.commands_dir = self.bridge_dir / "commands"
        self.responses_dir = self.bridge_dir / "responses"

        self._connected = False
        self._account_info_cache: Optional[Dict] = None

    def get_broker_type(self) -> str:
        """Retorna 'mt4'."""
        return "mt4"

    def _ensure_dirs(self):
        """Crear directorios del bridge si no existen."""
        self.commands_dir.mkdir(parents=True, exist_ok=True)
        self.responses_dir.mkdir(parents=True, exist_ok=True)

    def _send_command(self, command: str, params: Dict = None,
                     wait_response: bool = True) -> Dict[str, Any]:
        """
        Enviar comando al EA via archivo JSON.
        
        Args:
            command: Nombre del comando (ej: "get_account_info")
            params: Parámetros del comando
            wait_response: Si True, espera la respuesta del EA
            
        Returns:
            Respuesta del EA como diccionario
        """
        self._ensure_dirs()

        cmd_id = str(uuid.uuid4())[:8]
        cmd_file = self.commands_dir / f"cmd_{cmd_id}.json"
        resp_file = self.responses_dir / f"resp_{cmd_id}.json"

        # Limpiar respuesta previa si existe
        if resp_file.exists():
            resp_file.unlink()

        # Escribir comando
        cmd_data = {
            "id": cmd_id,
            "command": command,
            "params": params or {},
            "timestamp": time.time()
        }

        with open(cmd_file, 'w') as f:
            json.dump(cmd_data, f)

        if not wait_response:
            return {"success": True, "id": cmd_id, "status": "sent"}

        # Esperar respuesta
        start_time = time.time()
        while time.time() - start_time < self.timeout:
            if resp_file.exists():
                try:
                    with open(resp_file, 'r') as f:
                        response = json.load(f)

                    # Limpiar archivos
                    try:
                        cmd_file.unlink()
                        resp_file.unlink()
                    except OSError:
                        pass

                    return response
                except (json.JSONDecodeError, IOError):
                    pass

            time.sleep(0.1)  # Poll cada 100ms

        # Timeout - limpiar comando
        try:
            cmd_file.unlink()
        except OSError:
            pass

        return {"success": False, "error": f"Timeout esperando respuesta del EA ({self.timeout}s)"}

    # ============ Connection ============

    def connect(self) -> bool:
        """Conectar a MT4 via bridge. Verifica que el EA esté corriendo."""
        print("[MT4 Bridge] Verificando conexión con EA...")

        # Verificar que el directorio existe
        self._ensure_dirs()

        # Enviar ping al EA
        response = self._send_command("ping", {})

        if response.get("success"):
            self._connected = True
            # Obtener info de cuenta para confirmar
            account = self._send_command("get_account_info", {})
            if account.get("success"):
                self._account_info_cache = account.get("data", {})
                print(f"[MT4 Bridge] Conectado: {self._account_info_cache.get('login', '?')} @ "
                      f"{self._account_info_cache.get('server', '?')}")
            else:
                print("[MT4 Bridge] Conectado (sin info de cuenta)")
            return True
        else:
            print(f"[MT4 Bridge] Error: {response.get('error', 'EA no responde')}")
            print("[MT4 Bridge] Asegúrate de que el EA 'MCP_Bridge.mq4' esté corriendo en MT4")
            return False

    def disconnect(self):
        """Desconectar de MT4."""
        self._connected = False
        self._account_info_cache = None
        print("[MT4 Bridge] Desconectado")

    def reconnect(self) -> bool:
        """Reconectar a MT4."""
        print("[MT4 Bridge] Intentando reconexión...")
        self.disconnect()
        time.sleep(2)
        return self.connect()

    def is_connected(self) -> bool:
        """Verificar si está conectado."""
        if not self._connected:
            return False

        # Ping rápido para verificar
        response = self._send_command("ping", {}, wait_response=True)
        if not response.get("success"):
            self._connected = False
            return False

        return True

    # ============ Account Info ============

    def get_account_info(self) -> Dict[str, Any]:
        """Obtener información de la cuenta."""
        response = self._send_command("get_account_info", {})
        if response.get("success"):
            self._account_info_cache = response.get("data", {})
            return self._account_info_cache
        return {"error": response.get("error", "No conectado")}

    def get_balance(self) -> float:
        """Obtener balance."""
        info = self.get_account_info()
        return info.get("balance", 0)

    def get_equity(self) -> float:
        """Obtener equity."""
        info = self.get_account_info()
        return info.get("equity", 0)

    def get_margin_info(self) -> Dict[str, Any]:
        """Obtener información de margen."""
        info = self.get_account_info()
        return {
            "margin": info.get("margin", 0),
            "free_margin": info.get("free_margin", 0),
            "margin_level": info.get("margin_level", 0)
        }

    # ============ Positions ============

    def get_positions(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Obtener posiciones abiertas."""
        params = {}
        if symbol:
            params["symbol"] = symbol

        response = self._send_command("get_positions", params)
        if response.get("success"):
            return response.get("data", [])
        return []

    def close_position(self, ticket: int) -> Dict[str, Any]:
        """Cerrar una posición."""
        response = self._send_command("close_position", {"ticket": ticket})
        return response

    def close_all_positions(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Cerrar todas las posiciones."""
        params = {}
        if symbol:
            params["symbol"] = symbol

        response = self._send_command("close_all_positions", params)
        return response

    def modify_position(self, ticket: int,
                       stop_loss: Optional[float] = None,
                       take_profit: Optional[float] = None) -> Dict[str, Any]:
        """Modificar SL/TP de una posición."""
        params = {"ticket": ticket}
        if stop_loss is not None:
            params["stop_loss"] = stop_loss
        if take_profit is not None:
            params["take_profit"] = take_profit

        response = self._send_command("modify_position", params)
        return response

    # ============ Orders ============

    def place_market_order(self, symbol: str, order_type: str,
                          volume: float, stop_loss: Optional[float] = None,
                          take_profit: Optional[float] = None,
                          comment: str = "MCP") -> Dict[str, Any]:
        """Colocar orden de mercado."""
        params = {
            "symbol": symbol,
            "order_type": order_type,
            "volume": volume,
            "comment": comment
        }
        if stop_loss is not None:
            params["stop_loss"] = stop_loss
        if take_profit is not None:
            params["take_profit"] = take_profit

        response = self._send_command("place_market_order", params)
        return response

    def place_pending_order(self, symbol: str, order_type: str,
                           volume: float, price: float,
                           stop_loss: Optional[float] = None,
                           take_profit: Optional[float] = None,
                           expiration: Optional[str] = None) -> Dict[str, Any]:
        """Colocar orden pendiente."""
        params = {
            "symbol": symbol,
            "order_type": order_type,
            "volume": volume,
            "price": price
        }
        if stop_loss is not None:
            params["stop_loss"] = stop_loss
        if take_profit is not None:
            params["take_profit"] = take_profit
        if expiration:
            params["expiration"] = expiration

        response = self._send_command("place_pending_order", params)
        return response

    def cancel_order(self, order_id: int) -> Dict[str, Any]:
        """Cancelar orden pendiente."""
        response = self._send_command("cancel_order", {"order_id": order_id})
        return response

    def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Cancelar todas las órdenes pendientes."""
        params = {}
        if symbol:
            params["symbol"] = symbol

        response = self._send_command("cancel_all_orders", params)
        return response

    def get_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Obtener órdenes pendientes."""
        params = {}
        if symbol:
            params["symbol"] = symbol

        response = self._send_command("get_orders", params)
        if response.get("success"):
            return response.get("data", [])
        return []

    # ============ Market Data ============

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Obtener información del símbolo."""
        response = self._send_command("get_symbol_info", {"symbol": symbol})
        if response.get("success"):
            return response.get("data", {})
        return {"error": response.get("error", "Error obteniendo info")}

    def get_tick(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Obtener tick actual."""
        response = self._send_command("get_tick", {"symbol": symbol})
        if response.get("success"):
            return response.get("data", {})
        return None

    def get_candles(self, symbol: str, timeframe: str,
                   count: int = 100) -> List[Dict[str, Any]]:
        """Obtener velas históricas."""
        params = {
            "symbol": symbol,
            "timeframe": timeframe,
            "count": count
        }

        response = self._send_command("get_candles", params)
        if response.get("success"):
            return response.get("data", [])
        return []

    def get_symbols(self, group: str = "*") -> List[str]:
        """Obtener lista de símbolos disponibles."""
        response = self._send_command("get_symbols", {"group": group})
        if response.get("success"):
            return response.get("data", [])
        return []
