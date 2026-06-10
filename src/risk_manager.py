#!/usr/bin/env python3
"""
Risk Manager - Circuit Breakers y Gestión de Riesgo
Protección automática del capital.
"""

from datetime import datetime, timedelta
from typing import Dict, Optional, Any


class RiskManager:
    """
    Gestiona riesgo y protege capital con circuit breakers.
    No requiere intervención humana.
    """

    def __init__(self, db, config: Dict, mt5_client=None):
        self.db = db
        self.config = config
        self.mt5_client = mt5_client  # Opcional, para checks en tiempo real

        # Límites configurables
        self.max_drawdown = config.get("drawdown_max", 15.0)  # %
        self.max_daily_loss = config.get("perdida_diaria_max", 2.0)  # % del balance
        self.max_consecutive_losses = config.get("perdidas_consecutivas_max", 3)
        self.cooling_period_minutes = config.get("cooling_minutes", 30)
        self.max_spread_pips = config.get("spread_max", 50)
        self.max_positions = config.get("max_posiciones_simultaneas", 3)

        # Estado interno
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = ""
        self._cooling_until: Optional[datetime] = None
        self._consecutive_losses = 0
        self._daily_starting_balance = 0.0
        self._max_equity_reached = 0.0

        # Inicializar
        self._load_state()

    def set_mt5_client(self, mt5_client):
        """Establecer referencia al cliente MT5 para checks en tiempo real."""
        self.mt5_client = mt5_client

    def _load_state(self):
        """Cargar estado previo de la base de datos."""
        try:
            history = self.db.get_risk_metrics_history(days=1)

            if history:
                latest = history[0]
                self._consecutive_losses = latest.get("consecutive_losses", 0)
                if latest.get("circuit_breaker_active"):
                    self._circuit_breaker_active = True
        except Exception as e:
            print(f"[RiskManager] Error cargando estado: {e}")

    def validate_order(self, symbol: str, volume: float,
                      stop_loss: Optional[float]) -> Dict[str, Any]:
        """
        Validar si una orden puede ser ejecutada.
        Retorna {'allowed': True/False, 'reason': str}
        """
        # 1. Verificar circuit breaker
        if self.is_circuit_breaker_active():
            return {
                "allowed": False,
                "reason": f"Circuit breaker activo: {self._circuit_breaker_reason}"
            }

        # 2. Verificar período de cooling
        if self._cooling_until and datetime.now() < self._cooling_until:
            remaining = (self._cooling_until - datetime.now()).seconds // 60
            return {
                "allowed": False,
                "reason": f"Período de cooling activo. Esperar {remaining} minutos"
            }

        # 3. Verificar spread
        if not self._check_spread(symbol):
            return {
                "allowed": False,
                "reason": f"Spread demasiado alto para {symbol}"
            }

        # 4. Verificar límite de posiciones
        if not self._check_position_limit():
            return {
                "allowed": False,
                "reason": f"Límite de {self.max_positions} posiciones alcanzado"
            }

        # 5. Verificar stop loss obligatorio
        if stop_loss is None or stop_loss == 0:
            return {
                "allowed": False,
                "reason": "Stop loss obligatorio para operar"
            }

        # 6. Verificar riesgo por trade
        risk_validation = self._check_trade_risk(symbol, volume, stop_loss)
        if not risk_validation["valid"]:
            return {
                "allowed": False,
                "reason": risk_validation["reason"]
            }

        return {"allowed": True, "reason": "OK"}

    def is_circuit_breaker_active(self) -> bool:
        """Verificar si el circuit breaker está activo."""
        # Re-verificar condiciones que podrían haber cambiado
        self._check_circuit_breakers()
        return self._circuit_breaker_active

    def get_circuit_breaker_reason(self) -> str:
        """Obtener razón del circuit breaker."""
        return self._circuit_breaker_reason

    def reset_circuit_breaker(self):
        """Resetear manualmente el circuit breaker."""
        self._circuit_breaker_active = False
        self._circuit_breaker_reason = ""
        self._cooling_until = None

        # Guardar en DB
        self.db.save_risk_metrics({
            "daily_drawdown": self.get_daily_drawdown(),
            "daily_loss": self.get_daily_loss(),
            "consecutive_losses": self._consecutive_losses,
            "circuit_breaker_active": False
        })

    def update_after_trade(self, profit: float):
        """Actualizar estado después de un trade cerrado."""
        if profit < 0:
            self._consecutive_losses += 1
        else:
            self._consecutive_losses = 0

        # Verificar si debemos activar circuit breaker
        self._check_circuit_breakers()

        # Guardar métricas
        self.db.save_risk_metrics({
            "daily_drawdown": self.get_daily_drawdown(),
            "daily_loss": self.get_daily_loss(),
            "consecutive_losses": self._consecutive_losses,
            "circuit_breaker_active": self._circuit_breaker_active,
            "max_drawdown_period": self._max_equity_reached
        })

    def get_daily_drawdown(self) -> float:
        """Calcular drawdown diario actual como porcentaje."""
        try:
            # Obtener balance inicial del día
            if self._daily_starting_balance == 0:
                self._daily_starting_balance = self.db.get_daily_starting_balance()

            if self._daily_starting_balance == 0:
                return 0.0

            # Obtener equity actual
            current_equity = self.db.get_latest_equity()

            if current_equity == 0:
                return 0.0

            # Actualizar máximo equity alcanzado
            if current_equity > self._max_equity_reached:
                self._max_equity_reached = current_equity

            # Calcular drawdown desde el máximo
            if self._max_equity_reached > 0:
                drawdown = ((self._max_equity_reached - current_equity) / self._max_equity_reached) * 100
                return max(0, drawdown)

            return 0.0
        except Exception:
            return 0.0

    def get_daily_loss(self) -> float:
        """Calcular pérdida acumulada del día como porcentaje del balance."""
        try:
            stats = self.db.get_trading_stats(days=1)
            pnl = stats.get("total_pnl", 0)

            if pnl >= 0:
                return 0.0

            # Calcular como porcentaje del balance
            if self._daily_starting_balance == 0:
                self._daily_starting_balance = self.db.get_daily_starting_balance()

            balance = self._daily_starting_balance if self._daily_starting_balance > 0 else 10000

            return abs(pnl / balance) * 100
        except Exception:
            return 0.0

    def get_consecutive_losses(self) -> int:
        """Obtener número de pérdidas consecutivas."""
        return self._consecutive_losses

    def _check_circuit_breakers(self):
        """Verificar y activar circuit breakers si es necesario."""
        if self._circuit_breaker_active:
            return

        # 1. Circuit breaker por drawdown
        drawdown = self.get_daily_drawdown()
        if drawdown >= self.max_drawdown:
            self._activate_circuit_breaker(
                f"Drawdown máximo alcanzado: {drawdown:.2f}%"
            )
            return

        # 2. Circuit breaker por pérdida diaria (porcentaje real del balance)
        daily_loss_pct = self.get_daily_loss()
        if daily_loss_pct >= self.max_daily_loss:
            self._activate_circuit_breaker(
                f"Pérdida diaria máxima alcanzada: {daily_loss_pct:.2f}%"
            )
            return

        # 3. Circuit breaker por pérdidas consecutivas
        if self._consecutive_losses >= self.max_consecutive_losses:
            self._activate_circuit_breaker(
                f"{self._consecutive_losses} pérdidas consecutivas"
            )
            return

    def _activate_circuit_breaker(self, reason: str):
        """Activar circuit breaker."""
        self._circuit_breaker_active = True
        self._circuit_breaker_reason = reason
        self._cooling_until = datetime.now() + timedelta(minutes=self.cooling_period_minutes)

        print(f"[RiskManager] CIRCUIT BREAKER ACTIVADO: {reason}")
        print(f"[RiskManager] Cooling period: {self.cooling_period_minutes} minutos")

    def _check_spread(self, symbol: str) -> bool:
        """Verificar si el spread es aceptable usando datos reales de MT5."""
        if not self.mt5_client:
            # Sin MT5 client, no podemos verificar - permitir
            return True

        try:
            tick = self.mt5_client.get_tick(symbol)
            if not tick:
                return True  # Sin datos, permitir

            spread = tick.get("spread", 0)
            symbol_info = self.mt5_client.get_symbol_info(symbol)
            point = symbol_info.get("point", 0.00001)

            if point == 0:
                return True

            # Convertir spread a pips
            spread_pips = spread / (point * 10) if point else 0

            if spread_pips > self.max_spread_pips:
                print(f"[RiskManager] Spread alto para {symbol}: {spread_pips:.1f} pips (máx: {self.max_spread_pips})")
                return False

            return True
        except Exception as e:
            print(f"[RiskManager] Error verificando spread: {e}")
            return True  # En caso de error, permitir

    def _check_position_limit(self) -> bool:
        """Verificar límite de posiciones abiertas contra MT5 real."""
        if not self.mt5_client:
            return True

        try:
            positions = self.mt5_client.get_positions()
            open_count = len(positions)

            if open_count >= self.max_positions:
                print(f"[RiskManager] Posiciones abiertas: {open_count}/{self.max_positions}")
                return False

            return True
        except Exception as e:
            print(f"[RiskManager] Error verificando posiciones: {e}")
            return True

    def _check_trade_risk(self, symbol: str, volume: float,
                         stop_loss: float) -> Dict[str, Any]:
        """Verificar riesgo individual del trade."""
        try:
            # Obtener balance actual
            balance = 10000.0  # Default
            if self.mt5_client:
                account_info = self.mt5_client.get_account_info()
                balance = account_info.get("balance", 10000)

            # Calcular riesgo monetario del trade
            symbol_info = {}
            if self.mt5_client:
                symbol_info = self.mt5_client.get_symbol_info(symbol)

            point = symbol_info.get("point", 0.00001)
            tick = {}
            if self.mt5_client:
                tick = self.mt5_client.get_tick(symbol) or {}

            # Calcular distancia al SL en pips
            current_price = tick.get("bid", 0) or tick.get("ask", 0)
            if current_price > 0 and stop_loss > 0:
                sl_distance_pips = abs(current_price - stop_loss) / (point * 10) if point > 0 else 50
            else:
                sl_distance_pips = 50  # Default

            # Valor por pip por lote
            if "JPY" in symbol:
                pip_value_per_lot = 1000
            elif "XAU" in symbol or "GOLD" in symbol:
                pip_value_per_lot = 1
            else:
                pip_value_per_lot = 10

            risk_amount = volume * sl_distance_pips * pip_value_per_lot

            # Verificar que no exceda 2% del balance por trade
            max_risk_per_trade = balance * 0.02

            if risk_amount > max_risk_per_trade:
                return {
                    "valid": False,
                    "reason": f"Riesgo del trade (${risk_amount:.2f}) excede máximo permitido (${max_risk_per_trade:.2f} = 2% de ${balance:.2f})"
                }

            return {"valid": True, "reason": "OK"}
        except Exception as e:
            return {"valid": True, "reason": f"Error en validación: {e}"}

    def calculate_position_size(self, balance: float, risk_percent: float,
                                stop_loss_pips: float, pip_value: float,
                                symbol: str = "") -> float:
        """
        Calcular tamaño de posición basado en riesgo porcentual.
        Implementación del Kelly Criterion fraccionario.
        """
        # Riesgo en dólares
        risk_amount = balance * (risk_percent / 100)

        # Calcular lotes
        lot_size = risk_amount / (stop_loss_pips * pip_value)

        # Kelly Criterion fraccionario
        stats = self.db.get_trading_stats(days=30)
        win_rate = stats.get("win_rate", 50) / 100
        avg_profit = stats.get("avg_profit", 1)
        avg_loss = abs(stats.get("avg_loss", 1))

        if avg_loss > 0 and win_rate > 0:
            b = avg_profit / avg_loss  # Ratio R:R
            kelly = (win_rate * b - (1 - win_rate)) / b

            # Fraccionario (0.25-0.75 del Kelly)
            kelly_fractional = max(0.25, min(kelly * 0.5, 0.75))

            # Ajustar lot size por Kelly
            lot_size *= kelly_fractional

        # Normalizar
        lot_size = round(lot_size / 0.01) * 0.01
        lot_size = max(0.01, min(lot_size, 10.0))

        return lot_size
