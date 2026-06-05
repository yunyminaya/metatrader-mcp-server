#!/usr/bin/env python3
"""
Daemon Trading - Modo autónomo 100%
Ciclo automático de trading sin intervención humana.
"""

import asyncio
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any


class DaemonTrading:
    """
    Daemon que ejecuta ciclos de trading automáticos.
    No requiere intervención humana después de configurar.
    """

    def __init__(self, mt5_client, db, risk_mgr, ml_scorer, notifier, config: Dict):
        self.mt5 = mt5_client
        self.db = db
        self.risk_mgr = risk_mgr
        self.ml_scorer = ml_scorer
        self.notifier = notifier
        self.config = config

        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_cycle: Optional[datetime] = None
        self._next_cycle: Optional[datetime] = None
        self._stop_event = threading.Event()

        # Configuración del ciclo
        self.cycle_minutes = config.get("ciclo_minutos", 15)
        self.symbols = config.get("pares", ["EURUSD", "XAUUSD", "GBPUSD"])
        self.strategy = config.get("estrategia", {}).get("tipo", "fenix")
        self.score_minimo = config.get("estrategia", {}).get("score_minimo", 95)
        self.max_daily_trades = config.get("max_operaciones_dia", 5)
        self.risk_per_trade = config.get("riesgo", {}).get("kelly_fraccion", 0.25)

    def start(self):
        """Iniciar el daemon en un thread separado."""
        if self._running:
            return

        self._running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        if self.notifier:
            asyncio.run_coroutine_threadsafe(
                self.notifier.send("🤖 Daemon de trading iniciado"),
                asyncio.get_event_loop()
            )

    def stop(self):
        """Detener el daemon gracefulmente."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)

    def is_running(self) -> bool:
        """Verificar si el daemon está corriendo."""
        return self._running

    def get_last_cycle_time(self) -> Optional[str]:
        """Obtener timestamp del último ciclo."""
        return self._last_cycle.isoformat() if self._last_cycle else None

    def get_next_cycle_time(self) -> Optional[str]:
        """Obtener timestamp del próximo ciclo."""
        return self._next_cycle.isoformat() if self._next_cycle else None

    def get_config(self) -> Dict:
        """Obtener configuración actual."""
        return {
            "cycle_minutes": self.cycle_minutes,
            "symbols": self.symbols,
            "strategy": self.strategy,
            "score_minimo": self.score_minimo,
            "max_daily_trades": self.max_daily_trades,
            "risk_per_trade": self.risk_per_trade
        }

    def update_config(self, config: Dict):
        """Actualizar configuración en caliente."""
        self.cycle_minutes = config.get("ciclo_minutos", self.cycle_minutes)
        self.symbols = config.get("symbols", self.symbols)
        self.strategy = config.get("strategy", self.strategy)
        self.max_daily_trades = config.get("max_daily_trades", self.max_daily_trades)
        self.risk_per_trade = config.get("risk_per_trade", self.risk_per_trade)

    def force_cycle(self):
        """Forzar ejecución de un ciclo inmediatamente."""
        self._execute_cycle()

    def _run_loop(self):
        """Loop principal del daemon."""
        while self._running and not self._stop_event.is_set():
            try:
                # Verificar si es hora de operar
                if self._should_trade():
                    self._execute_cycle()

                # Calcular próximo ciclo
                self._next_cycle = datetime.now() + timedelta(minutes=self.cycle_minutes)

                # Esperar hasta el próximo ciclo (chequeando stop cada segundo)
                for _ in range(self.cycle_minutes * 60):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)

            except Exception as e:
                print(f"[Daemon Error] {e}")
                time.sleep(60)  # Esperar 1 min en caso de error

    def _should_trade(self) -> bool:
        """Verificar condiciones para operar."""
        # Verificar horario de trading
        now = datetime.now()
        horario = self.config.get("horario_operacion", {})

        if horario:
            inicio = horario.get("inicio", "00:00")
            fin = horario.get("fin", "23:59")

            inicio_time = datetime.strptime(inicio, "%H:%M").time()
            fin_time = datetime.strptime(fin, "%H:%M").time()
            current_time = now.time()

            if not (inicio_time <= current_time <= fin_time):
                return False

        # Verificar día de la semana
        dias_operacion = self.config.get("dias_operacion", [0, 1, 2, 3, 4])  # Lunes-Viernes
        if now.weekday() not in dias_operacion:
            return False

        # Verificar conexión a MT5
        if not self.mt5.is_connected():
            self.mt5.reconnect()
            if not self.mt5.is_connected():
                return False

        return True

    def _execute_cycle(self):
        """Ejecutar un ciclo completo de trading."""
        self._last_cycle = datetime.now()
        print(f"[Daemon] Ciclo iniciado: {self._last_cycle.isoformat()}")

        # 1. Verificar circuit breakers
        if self.risk_mgr and self.risk_mgr.is_circuit_breaker_active():
            print("[Daemon] Circuit breaker activo - skip ciclo")
            return

        # 2. Verificar límite de trades diarios
        today_trades = self.db.get_today_trade_count()
        if today_trades >= self.max_daily_trades:
            print(f"[Daemon] Límite diario alcanzado ({today_trades}/{self.max_daily_trades})")
            return

        # 3. Analizar cada símbolo
        for symbol in self.symbols:
            try:
                self._analyze_and_trade(symbol)
            except Exception as e:
                print(f"[Daemon Error] {symbol}: {e}")

        # 4. Gestionar posiciones abiertas (trailing stops, breakeven)
        self._manage_open_positions()

        # 5. Notificar resumen
        if self.notifier:
            account = self.mt5.get_account_info()
            msg = (
                f"📊 Ciclo completado\n"
                f"Balance: ${account.get('balance', 0):.2f}\n"
                f"Equity: ${account.get('equity', 0):.2f}\n"
                f"Trades hoy: {today_trades}/{self.max_daily_trades}"
            )
            asyncio.run_coroutine_threadsafe(
                self.notifier.send(msg),
                asyncio.get_event_loop()
            )

    def _analyze_and_trade(self, symbol: str):
        """Analizar símbolo y ejecutar trade si cumple criterios."""
        # Obtener datos
        candles = self.mt5.get_candles(symbol, "H1", 50)
        tick = self.mt5.get_tick(symbol)

        if not candles or not tick:
            return

        # Calcular score con ML local
        score, features = self.ml_scorer.calculate_score(symbol, candles, tick, self.strategy)

        print(f"[Daemon] {symbol} Score: {score}/100")

        # Si score es suficiente, ejecutar trade
        if score >= self.score_minimo:
            # Determinar dirección
            direction = features.get("direction", "buy")

            # Calcular lot size basado en riesgo
            stop_loss_pips = self._calculate_stop_loss(features)
            lot_size = self._calculate_lot_size(symbol, stop_loss_pips)

            # Calcular SL/TP
            point = self.mt5.get_symbol_info(symbol).get("point", 0.00001)
            sl_distance = stop_loss_pips * point * (100 if "JPY" in symbol else 10)

            if direction == "buy":
                sl = tick["bid"] - sl_distance
                tp = tick["bid"] + (sl_distance * 2)  # 1:2 RR
                order_type = "buy"
            else:
                sl = tick["ask"] + sl_distance
                tp = tick["ask"] - (sl_distance * 2)
                order_type = "sell"

            # Validar riesgo
            if self.risk_mgr:
                validation = self.risk_mgr.validate_order(symbol, lot_size, sl)
                if not validation["allowed"]:
                    print(f"[Daemon] {symbol} Rechazado por risk manager: {validation['reason']}")
                    return

            # Ejecutar orden
            result = self.mt5.place_market_order(
                symbol=symbol,
                order_type=order_type,
                volume=lot_size,
                stop_loss=round(sl, 5),
                take_profit=round(tp, 5),
                comment=f"Daemon Score:{score}"
            )

            if result["success"]:
                self.db.log_trade({
                    "action": "open",
                    "symbol": symbol,
                    "type": order_type,
                    "volume": lot_size,
                    "stop_loss": sl,
                    "take_profit": tp,
                    "ticket": result.get("ticket"),
                    "score": score,
                    "strategy": self.strategy,
                    "timestamp": datetime.now().isoformat()
                })

                if self.notifier:
                    asyncio.run_coroutine_threadsafe(
                        self.notifier.send(
                            f"🎯 Trade ejecutado\n"
                            f"{symbol} {order_type.upper()}\n"
                            f"Lote: {lot_size}\n"
                            f"Score: {score}/100\n"
                            f"SL: {sl:.5f} | TP: {tp:.5f}"
                        ),
                        asyncio.get_event_loop()
                    )

                print(f"[Daemon] {symbol} Trade ejecutado - Ticket: {result.get('ticket')}")

    def _manage_open_positions(self):
        """Gestionar posiciones abiertas (trailing stop, breakeven)."""
        positions = self.mt5.get_positions()

        for pos in positions:
            try:
                symbol = pos["symbol"]
                profit = pos["profit"]
                open_price = pos["open_price"]
                current_sl = pos.get("sl", 0)
                order_type = pos["type"]  # 0=buy, 1=sell

                # Obtener tick actual
                tick = self.mt5.get_tick(symbol)
                if not tick:
                    continue

                # Breakeven: Si profit > X pips, mover SL a entrada
                point = self.mt5.get_symbol_info(symbol).get("point", 0.00001)
                pip_value = point * 100 if "JPY" in symbol else point * 10

                profit_pips = abs(profit / (pos["volume"] * 10))  # Aprox

                if profit_pips > 20 and current_sl == 0:  # 20 pips de profit
                    new_sl = open_price
                    self.mt5.modify_position(pos["ticket"], stop_loss=new_sl)
                    print(f"[Daemon] {symbol} Breakeven activado")

                # Trailing stop escalonado
                elif profit_pips > 50:
                    # Mover SL a +30 pips de profit
                    if order_type == 0:  # Buy
                        new_sl = open_price + (30 * pip_value)
                        if new_sl > current_sl:
                            self.mt5.modify_position(pos["ticket"], stop_loss=new_sl)
                    else:  # Sell
                        new_sl = open_price - (30 * pip_value)
                        if new_sl < current_sl or current_sl == 0:
                            self.mt5.modify_position(pos["ticket"], stop_loss=new_sl)

            except Exception as e:
                print(f"[Daemon Error] Gestionando posición: {e}")

    def _calculate_stop_loss(self, features: Dict) -> int:
        """Calcular stop loss en pips basado en ATR o volatilidad."""
        atr = features.get("atr", 0.0010)
        symbol = features.get("symbol", "EURUSD")

        # Convertir ATR a pips
        if "JPY" in symbol:
            sl_pips = int(atr * 10000 * 1.5)  # 1.5x ATR
        else:
            sl_pips = int(atr * 10000 * 1.5)

        # Limitar entre 20-100 pips
        return max(20, min(sl_pips, 100))

    def _calculate_lot_size(self, symbol: str, stop_loss_pips: int) -> float:
        """Calcular tamaño de lote basado en riesgo porcentual."""
        account = self.mt5.get_account_info()
        balance = account.get("balance", 10000)

        # Riesgo en dólares
        risk_amount = balance * (self.risk_per_trade / 100)

        # Valor por pip (aproximado)
        if "JPY" in symbol:
            pip_value = 0.01  # Para 0.01 lotes en pares JPY
        else:
            pip_value = 0.10  # Para 0.01 lotes en pares normales

        # Calcular lotes
        lot_size = risk_amount / (stop_loss_pips * pip_value)

        # Normalizar a lotes estándar
        lot_size = round(lot_size / 0.01) * 0.01
        lot_size = max(0.01, min(lot_size, 10.0))  # Limitar

        return lot_size
