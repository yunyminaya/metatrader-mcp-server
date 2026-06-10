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

        # Guardar referencia al event loop del thread principal (donde corre MCP)
        try:
            self._event_loop = asyncio.get_event_loop()
        except RuntimeError:
            self._event_loop = None

        # Lock para operaciones thread-safe
        self._lock = threading.Lock()

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

        self._notify_sync("🤖 Daemon de trading iniciado")

    def stop(self):
        """Detener el daemon gracefulmente."""
        self._running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None

    def is_running(self) -> bool:
        """Verificar si el daemon está corriendo."""
        return self._running and self._thread is not None and self._thread.is_alive()

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
        with self._lock:
            self.cycle_minutes = config.get("ciclo_minutos", self.cycle_minutes)
            self.symbols = config.get("symbols", self.symbols)
            self.strategy = config.get("strategy", self.strategy)
            self.max_daily_trades = config.get("max_daily_trades", self.max_daily_trades)
            self.risk_per_trade = config.get("risk_per_trade", self.risk_per_trade)

    def force_cycle(self):
        """Forzar ejecución de un ciclo inmediatamente."""
        self._execute_cycle()

    def _notify_sync(self, message: str):
        """Enviar notificación de forma segura desde cualquier thread."""
        if not self.notifier:
            return
        try:
            if self._event_loop and self._event_loop.is_running():
                asyncio.run_coroutine_threadsafe(
                    self.notifier.send(message),
                    self._event_loop
                )
            else:
                # Si no hay loop async, usar print como fallback
                print(f"[Daemon Notify] {message}")
        except Exception as e:
            print(f"[Daemon Notify Error] {e}")

    def _run_loop(self):
        """Loop principal del daemon."""
        print("[Daemon] Loop principal iniciado")

        while self._running and not self._stop_event.is_set():
            try:
                # Verificar si es hora de operar
                if self._should_trade():
                    self._execute_cycle()

                # Calcular próximo ciclo
                self._next_cycle = datetime.now() + timedelta(minutes=self.cycle_minutes)

                # Esperar hasta el próximo ciclo (chequeando stop cada segundo)
                wait_seconds = self.cycle_minutes * 60
                for _ in range(wait_seconds):
                    if self._stop_event.is_set():
                        break
                    time.sleep(1)

            except Exception as e:
                print(f"[Daemon Error] {e}")
                time.sleep(60)  # Esperar 1 min en caso de error

        print("[Daemon] Loop principal terminado")

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
            print("[Daemon] MT5 desconectado, intentando reconexión...")
            self.mt5.reconnect()
            if not self.mt5.is_connected():
                print("[Daemon] No se pudo reconectar a MT5")
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

        # 3. Guardar snapshot de cuenta
        try:
            account = self.mt5.get_account_info()
            positions = self.mt5.get_positions()
            self.db.save_account_snapshot(account, len(positions))
        except Exception as e:
            print(f"[Daemon] Error guardando snapshot: {e}")

        # 4. Analizar cada símbolo
        for symbol in self.symbols:
            try:
                self._analyze_and_trade(symbol)
            except Exception as e:
                print(f"[Daemon Error] {symbol}: {e}")

        # 5. Gestionar posiciones abiertas (trailing stops, breakeven)
        self._manage_open_positions()

        # 6. Notificar resumen
        try:
            account = self.mt5.get_account_info()
            msg = (
                f"📊 Ciclo completado\n"
                f"Balance: ${account.get('balance', 0):.2f}\n"
                f"Equity: ${account.get('equity', 0):.2f}\n"
                f"Trades hoy: {today_trades}/{self.max_daily_trades}"
            )
            self._notify_sync(msg)
        except Exception:
            pass

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
            stop_loss_pips = self._calculate_stop_loss(features, symbol)
            lot_size = self._calculate_lot_size(symbol, stop_loss_pips)

            # Calcular SL/TP
            symbol_info = self.mt5.get_symbol_info(symbol)
            point = symbol_info.get("point", 0.00001)
            digits = symbol_info.get("digits", 5)

            if "JPY" in symbol:
                sl_distance = stop_loss_pips * point * 100
            elif "XAU" in symbol or "GOLD" in symbol:
                sl_distance = stop_loss_pips * point * 10
            else:
                sl_distance = stop_loss_pips * point * 10

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
                stop_loss=round(sl, digits),
                take_profit=round(tp, digits),
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

                self._notify_sync(
                    f"🎯 Trade ejecutado\n"
                    f"{symbol} {order_type.upper()}\n"
                    f"Lote: {lot_size}\n"
                    f"Score: {score}/100\n"
                    f"SL: {sl:.{digits}f} | TP: {tp:.{digits}f}"
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
                order_type = pos["type"]  # "buy" o "sell" (string desde mt5_client)

                # Obtener tick actual
                tick = self.mt5.get_tick(symbol)
                if not tick:
                    continue

                # Obtener info del símbolo
                symbol_info = self.mt5.get_symbol_info(symbol)
                point = symbol_info.get("point", 0.00001)
                digits = symbol_info.get("digits", 5)

                if "JPY" in symbol:
                    pip_value = point * 100
                else:
                    pip_value = point * 10

                # Calcular profit en pips de forma más precisa
                if order_type == "buy":
                    profit_pips = (tick["bid"] - open_price) / pip_value
                else:
                    profit_pips = (open_price - tick["ask"]) / pip_value

                # Breakeven: Si profit > 20 pips, mover SL a entrada
                if profit_pips > 20 and (current_sl == 0 or
                    (order_type == "buy" and current_sl < open_price) or
                    (order_type == "sell" and (current_sl > open_price or current_sl == 0))):
                    new_sl = round(open_price, digits)
                    self.mt5.modify_position(pos["ticket"], stop_loss=new_sl)
                    print(f"[Daemon] {symbol} Breakeven activado en {open_price:.{digits}f}")

                # Trailing stop escalonado: Si profit > 50 pips, mover SL a +30 pips
                elif profit_pips > 50:
                    if order_type == "buy":  # String comparison - FIXED
                        new_sl = round(open_price + (30 * pip_value), digits)
                        if new_sl > current_sl:
                            self.mt5.modify_position(pos["ticket"], stop_loss=new_sl)
                            print(f"[Daemon] {symbol} Trailing stop activado: SL -> {new_sl:.{digits}f}")
                    elif order_type == "sell":  # String comparison - FIXED
                        new_sl = round(open_price - (30 * pip_value), digits)
                        if new_sl < current_sl or current_sl == 0:
                            self.mt5.modify_position(pos["ticket"], stop_loss=new_sl)
                            print(f"[Daemon] {symbol} Trailing stop activado: SL -> {new_sl:.{digits}f}")

            except Exception as e:
                print(f"[Daemon Error] Gestionando posición {pos.get('ticket', '?')}: {e}")

    def _calculate_stop_loss(self, features: Dict, symbol: str = "") -> int:
        """Calcular stop loss en pips basado en ATR o volatilidad."""
        atr = features.get("atr", 0.0010)

        # Convertir ATR a pips
        if "JPY" in symbol:
            sl_pips = int(atr * 10000 * 1.5)  # 1.5x ATR
        elif "XAU" in symbol or "GOLD" in symbol:
            sl_pips = int(atr * 100 * 1.5)
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

        # Valor por pip (aproximado para 1 lote estándar)
        if "JPY" in symbol:
            pip_value_per_lot = 1000  # $1000 por pip por lote en USD/JPY
        elif "XAU" in symbol or "GOLD" in symbol:
            pip_value_per_lot = 1  # $1 por pip por lote en XAUUSD
        else:
            pip_value_per_lot = 10  # $10 por pip por lote en pares majors

        # Calcular lotes
        lot_size = risk_amount / (stop_loss_pips * pip_value_per_lot)

        # Normalizar a lotes estándar
        lot_size = round(lot_size / 0.01) * 0.01
        lot_size = max(0.01, min(lot_size, 10.0))  # Limitar

        return lot_size


def main():
    """Punto de entrada para ejecución directa del daemon."""
    import json
    from pathlib import Path

    config_path = Path.home() / ".metatrader-mcp" / "config.json"
    if not config_path.exists():
        print("Error: No se encontró configuración en ~/.metatrader-mcp/config.json")
        print("Ejecuta: metatrader-mcp-autonomous para configurar")
        return

    with open(config_path) as f:
        config = json.load(f)

    from database import TradingDatabase
    from risk_manager import RiskManager
    from ml_local import LocalMLScorer
    from notifier import TelegramNotifier, ConsoleNotifier
    from mt5_client import MT5Client

    # Inicializar componentes
    db_path = Path.home() / ".metatrader-mcp" / "trading.db"
    db = TradingDatabase(str(db_path))
    db.init_tables()

    risk_mgr = RiskManager(db, config.get("riesgo", {}))
    ml_scorer = LocalMLScorer(db)

    telegram_token = config.get("telegram", {}).get("token")
    telegram_chat = config.get("telegram", {}).get("chat_id")
    if telegram_token and telegram_chat:
        notifier = TelegramNotifier(telegram_token, telegram_chat)
    else:
        notifier = ConsoleNotifier()

    mt5 = MT5Client(
        login=config.get("mt5", {}).get("login"),
        password=config.get("mt5", {}).get("password"),
        server=config.get("mt5", {}).get("server"),
        path=config.get("mt5", {}).get("path")
    )

    if not mt5.connect():
        print("Error: No se pudo conectar a MT5")
        return

    # Crear y ejecutar daemon
    daemon = DaemonTrading(mt5, db, risk_mgr, ml_scorer, notifier, config)
    daemon.start()

    print("Daemon corriendo. Presiona Ctrl+C para detener.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nDeteniendo daemon...")
        daemon.stop()
        mt5.disconnect()
        db.close()
        print("Daemon detenido.")


if __name__ == "__main__":
    main()
