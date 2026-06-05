#!/usr/bin/env python3
"""
MT5 Client - Cliente para MetaTrader 5
Conecta y opera en MT4/MT5.
"""

from typing import Dict, List, Optional, Any
import time


class MT5Client:
    """
    Cliente para conectar y operar en MetaTrader 5.
    Soporta reconnexión automática.
    """

    def __init__(self, login: Optional[int] = None,
                 password: Optional[str] = None,
                 server: Optional[str] = None,
                 path: Optional[str] = None):
        self.login = login
        self.password = password
        self.server = server
        self.path = path

        self.mt5 = None
        self._connected = False
        self._last_connection_attempt = 0

    def connect(self) -> bool:
        """Conectar a MetaTrader 5."""
        try:
            import MetaTrader5 as mt5
            self.mt5 = mt5

            # Inicializar
            if self.path:
                initialized = self.mt5.initialize(self.path)
            else:
                initialized = self.mt5.initialize()

            if not initialized:
                print(f"[MT5] Error inicializando: {self.mt5.last_error()}")
                return False

            # Login si se proporcionaron credenciales
            if self.login and self.password and self.server:
                authorized = self.mt5.login(
                    self.login,
                    password=self.password,
                    server=self.server
                )
                if not authorized:
                    print(f"[MT5] Error de login: {self.mt5.last_error()}")
                    self.mt5.shutdown()
                    return False

            self._connected = True
            self._last_connection_attempt = time.time()

            # Info de cuenta
            account_info = self.mt5.account_info()
            if account_info:
                print(f"[MT5] Conectado: {account_info.login} @ {account_info.server}")

            return True

        except ImportError:
            print("[MT5 Error] MetaTrader5 package no instalado")
            print("Ejecuta: pip install MetaTrader5")
            return False
        except Exception as e:
            print(f"[MT5 Error] Conectando: {e}")
            return False

    def disconnect(self):
        """Desconectar de MT5."""
        if self.mt5 and self._connected:
            self.mt5.shutdown()
            self._connected = False
            print("[MT5] Desconectado")

    def reconnect(self) -> bool:
        """Reconectar a MT5."""
        print("[MT5] Intentando reconexión...")
        self.disconnect()
        time.sleep(2)
        return self.connect()

    def is_connected(self) -> bool:
        """Verificar si está conectado."""
        if not self._connected or not self.mt5:
            return False

        # Verificar conexión activa intentando obtener account info
        try:
            info = self.mt5.account_info()
            return info is not None
        except:
            self._connected = False
            return False

    # ============ Account Info ============

    def get_account_info(self) -> Dict[str, Any]:
        """Obtener información de la cuenta."""
        if not self.is_connected():
            return {"error": "No conectado"}

        info = self.mt5.account_info()
        if info is None:
            return {"error": "No se pudo obtener info"}

        return {
            "login": info.login,
            "name": info.name,
            "server": info.server,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "margin_level": info.margin_level,
            "currency": info.currency
        }

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
        if not self.is_connected():
            return []

        if symbol:
            positions = self.mt5.positions_get(symbol=symbol)
        else:
            positions = self.mt5.positions_get()

        if positions is None:
            return []

        result = []
        for pos in positions:
            result.append({
                "ticket": pos.ticket,
                "symbol": pos.symbol,
                "type": "buy" if pos.type == 0 else "sell",
                "volume": pos.volume,
                "open_price": pos.price_open,
                "current_price": pos.price_current,
                "profit": pos.profit,
                "swap": pos.swap,
                "sl": pos.sl,
                "tp": pos.tp,
                "open_time": pos.time,
                "comment": pos.comment
            })

        return result

    def close_position(self, ticket: int) -> Dict[str, Any]:
        """Cerrar una posición."""
        if not self.is_connected():
            return {"success": False, "error": "No conectado"}

        # Obtener posición
        position = self.mt5.positions_get(ticket=ticket)
        if not position:
            return {"success": False, "error": f"Posición {ticket} no encontrada"}

        position = position[0]

        # Preparar orden de cierre
        symbol = position.symbol
        tick = self.mt5.symbol_info_tick(symbol)

        if position.type == 0:  # Buy
            order_type = self.mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:  # Sell
            order_type = self.mt5.ORDER_TYPE_BUY
            price = tick.ask

        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": position.volume,
            "type": order_type,
            "position": ticket,
            "price": price,
            "deviation": 20,
            "magic": 123456,
            "comment": "MCP Close",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }

        result = self.mt5.order_send(request)

        if result.retcode == self.mt5.TRADE_RETCODE_DONE:
            return {
                "success": True,
                "ticket": ticket,
                "profit": position.profit,
                "price": price
            }
        else:
            return {
                "success": False,
                "error": f"Error cerrando: {result.retcode}"
            }

    def close_all_positions(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Cerrar todas las posiciones."""
        positions = self.get_positions(symbol)
        closed = []
        errors = []

        for pos in positions:
            result = self.close_position(pos["ticket"])
            if result["success"]:
                closed.append(pos["ticket"])
            else:
                errors.append(f"{pos['ticket']}: {result.get('error')}")

        return {
            "success": len(errors) == 0,
            "closed_count": len(closed),
            "closed_tickets": closed,
            "errors": errors
        }

    def modify_position(self, ticket: int,
                       stop_loss: Optional[float] = None,
                       take_profit: Optional[float] = None) -> Dict[str, Any]:
        """Modificar SL/TP de una posición."""
        if not self.is_connected():
            return {"success": False, "error": "No conectado"}

        position = self.mt5.positions_get(ticket=ticket)
        if not position:
            return {"success": False, "error": "Posición no encontrada"}

        position = position[0]

        request = {
            "action": self.mt5.TRADE_ACTION_SLTP,
            "position": ticket,
            "symbol": position.symbol,
            "sl": stop_loss if stop_loss is not None else position.sl,
            "tp": take_profit if take_profit is not None else position.tp,
        }

        result = self.mt5.order_send(request)

        if result.retcode == self.mt5.TRADE_RETCODE_DONE:
            return {"success": True, "ticket": ticket}
        else:
            return {"success": False, "error": f"Error: {result.retcode}"}

    def set_trailing_stop(self, ticket: int, points: int) -> Dict[str, Any]:
        """Activar trailing stop (usando SL dinámico)."""
        # MT5 no tiene trailing stop nativo en la API
        # Hay que implementarlo manualmente en el daemon
        return {
            "success": False,
            "error": "Trailing stop implementado en daemon_trading.py"
        }

    # ============ Orders ============

    def place_market_order(self, symbol: str, order_type: str,
                          volume: float, stop_loss: Optional[float] = None,
                          take_profit: Optional[float] = None,
                          comment: str = "MCP") -> Dict[str, Any]:
        """Colocar orden de mercado."""
        if not self.is_connected():
            return {"success": False, "error": "No conectado"}

        # Obtener precio actual
        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            return {"success": False, "error": f"Símbolo {symbol} no disponible"}

        # Determinar tipo de orden
        if order_type.lower() == "buy":
            mt5_order_type = self.mt5.ORDER_TYPE_BUY
            price = tick.ask
        elif order_type.lower() == "sell":
            mt5_order_type = self.mt5.ORDER_TYPE_SELL
            price = tick.bid
        else:
            return {"success": False, "error": f"Tipo de orden inválido: {order_type}"}

        request = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": volume,
            "type": mt5_order_type,
            "price": price,
            "deviation": 20,
            "magic": 123456,
            "comment": comment,
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_IOC,
        }

        if stop_loss:
            request["sl"] = stop_loss
        if take_profit:
            request["tp"] = take_profit

        result = self.mt5.order_send(request)

        if result.retcode == self.mt5.TRADE_RETCODE_DONE:
            return {
                "success": True,
                "ticket": result.order,
                "volume": result.volume,
                "price": result.price
            }
        else:
            return {
                "success": False,
                "error": f"Error: {result.retcode}",
                "retcode": result.retcode
            }

    def place_pending_order(self, symbol: str, order_type: str,
                           volume: float, price: float,
                           stop_loss: Optional[float] = None,
                           take_profit: Optional[float] = None,
                           expiration: Optional[str] = None) -> Dict[str, Any]:
        """Colocar orden pendiente (limit/stop)."""
        if not self.is_connected():
            return {"success": False, "error": "No conectado"}

        # Mapear tipos de orden
        order_types = {
            "buy_limit": self.mt5.ORDER_TYPE_BUY_LIMIT,
            "sell_limit": self.mt5.ORDER_TYPE_SELL_LIMIT,
            "buy_stop": self.mt5.ORDER_TYPE_BUY_STOP,
            "sell_stop": self.mt5.ORDER_TYPE_SELL_STOP,
        }

        mt5_type = order_types.get(order_type.lower())
        if mt5_type is None:
            return {"success": False, "error": f"Tipo inválido: {order_type}"}

        request = {
            "action": self.mt5.TRADE_ACTION_PENDING,
            "symbol": symbol,
            "volume": volume,
            "type": mt5_type,
            "price": price,
            "deviation": 20,
            "magic": 123456,
            "comment": "MCP Pending",
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self.mt5.ORDER_FILLING_RETURN,
        }

        if stop_loss:
            request["sl"] = stop_loss
        if take_profit:
            request["tp"] = take_profit

        result = self.mt5.order_send(request)

        if result.retcode == self.mt5.TRADE_RETCODE_DONE:
            return {
                "success": True,
                "order_id": result.order,
                "price": price
            }
        else:
            return {"success": False, "error": f"Error: {result.retcode}"}

    def cancel_order(self, order_id: int) -> Dict[str, Any]:
        """Cancelar orden pendiente."""
        if not self.is_connected():
            return {"success": False, "error": "No conectado"}

        request = {
            "action": self.mt5.TRADE_ACTION_REMOVE,
            "order": order_id,
        }

        result = self.mt5.order_send(request)

        if result.retcode == self.mt5.TRADE_RETCODE_DONE:
            return {"success": True, "order_id": order_id}
        else:
            return {"success": False, "error": f"Error: {result.retcode}"}

    def cancel_all_orders(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Cancelar todas las órdenes pendientes."""
        if not self.is_connected():
            return {"success": False, "error": "No conectado"}

        orders = self.get_orders(symbol)
        cancelled = []
        errors = []

        for order in orders:
            result = self.cancel_order(order["ticket"])
            if result["success"]:
                cancelled.append(order["ticket"])
            else:
                errors.append(f"{order['ticket']}: {result.get('error')}")

        return {
            "success": len(errors) == 0,
            "cancelled_count": len(cancelled),
            "cancelled_tickets": cancelled,
            "errors": errors
        }

    def get_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Obtener órdenes pendientes."""
        if not self.is_connected():
            return []

        if symbol:
            orders = self.mt5.orders_get(symbol=symbol)
        else:
            orders = self.mt5.orders_get()

        if orders is None:
            return []

        result = []
        for order in orders:
            type_names = {
                2: "buy_limit",
                3: "sell_limit",
                4: "buy_stop",
                5: "sell_stop",
            }
            result.append({
                "ticket": order.ticket,
                "symbol": order.symbol,
                "type": type_names.get(order.type, f"type_{order.type}"),
                "volume": order.volume_current,
                "price": order.price_open,
                "sl": order.sl,
                "tp": order.tp,
                "time": order.time_setup,
                "expiration": order.time_expiration
            })

        return result

    # ============ Market Data ============

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Obtener información del símbolo."""
        if not self.is_connected():
            return {"error": "No conectado"}

        info = self.mt5.symbol_info(symbol)
        if info is None:
            return {"error": f"Símbolo {symbol} no encontrado"}

        return {
            "name": info.name,
            "spread": info.spread,
            "digits": info.digits,
            "point": info.point,
            "tick_size": info.trade_tick_size,
            "contract_size": info.trade_contract_size,
            "min_lot": info.volume_min,
            "max_lot": info.volume_max,
            "lot_step": info.volume_step,
            "swap_long": info.swap_long,
            "swap_short": info.swap_short
        }

    def get_tick(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Obtener tick actual."""
        if not self.is_connected():
            return None

        tick = self.mt5.symbol_info_tick(symbol)
        if tick is None:
            return None

        return {
            "symbol": symbol,
            "time": tick.time,
            "bid": tick.bid,
            "ask": tick.ask,
            "last": tick.last,
            "volume": tick.volume,
            "spread": tick.ask - tick.bid
        }

    def get_candles(self, symbol: str, timeframe: str,
                   count: int = 100) -> List[Dict[str, Any]]:
        """Obtener velas históricas."""
        if not self.is_connected():
            return []

        # Mapear timeframes
        tf_map = {
            "M1": self.mt5.TIMEFRAME_M1,
            "M5": self.mt5.TIMEFRAME_M5,
            "M15": self.mt5.TIMEFRAME_M15,
            "M30": self.mt5.TIMEFRAME_M30,
            "H1": self.mt5.TIMEFRAME_H1,
            "H4": self.mt5.TIMEFRAME_H4,
            "D1": self.mt5.TIMEFRAME_D1,
            "W1": self.mt5.TIMEFRAME_W1,
            "MN1": self.mt5.TIMEFRAME_MN1,
        }

        mt5_tf = tf_map.get(timeframe.upper(), self.mt5.TIMEFRAME_H1)

        rates = self.mt5.copy_rates_from_pos(symbol, mt5_tf, 0, count)

        if rates is None:
            return []

        result = []
        for rate in rates:
            result.append({
                "time": rate[0],
                "open": rate[1],
                "high": rate[2],
                "low": rate[3],
                "close": rate[4],
                "tick_volume": rate[5],
                "spread": rate[6],
                "real_volume": rate[7]
            })

        return result

    def get_symbols(self, group: str = "*") -> List[str]:
        """Obtener lista de símbolos."""
        if not self.is_connected():
            return []

        symbols = self.mt5.symbols_get(group=group)
        if symbols is None:
            return []

        return [s.name for s in symbols]
