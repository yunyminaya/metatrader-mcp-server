#!/usr/bin/env python3
"""
MetaTrader MCP Server - Modo Autónomo 100%
Servidor MCP completo para trading automático sin intervención humana.

Features:
- Modo daemon con ciclo automático
- Persistencia SQLite
- Configuración declarativa
- Circuit breakers
- ML local para scoring
- Notificaciones Telegram
- Reconexión automática MT5
"""

import asyncio
import json
import signal
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from mcp.server.fastmcp import FastMCP, Context

# Importar módulos locales (relativos al paquete)
try:
    from .database import TradingDatabase
    from .risk_manager import RiskManager
    from .ml_local import LocalMLScorer
    from .notifier import TelegramNotifier, ConsoleNotifier
    from .broker_client import BrokerClient
    from .mt5_client import MT5Client
    from .mt4_bridge import MT4BridgeClient
    from .daemon_trading import DaemonTrading
except ImportError:
    # Fallback para ejecución directa: python src/server.py
    from database import TradingDatabase
    from risk_manager import RiskManager
    from ml_local import LocalMLScorer
    from notifier import TelegramNotifier, ConsoleNotifier
    from broker_client import BrokerClient
    from mt5_client import MT5Client
    from mt4_bridge import MT4BridgeClient
    from daemon_trading import DaemonTrading


# ============================================================
# LIFECYCLE
# ============================================================

# Variables globales para el estado (usadas por el lifespan)
_db: Optional[TradingDatabase] = None
_risk_mgr: Optional[RiskManager] = None
_ml_scorer: Optional[LocalMLScorer] = None
_notifier: Optional[Any] = None  # TelegramNotifier o ConsoleNotifier
_broker: Optional[BrokerClient] = None  # MT5Client o MT4BridgeClient
_daemon: Optional[DaemonTrading] = None


@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[Dict]:
    """Maneja el ciclo de vida del servidor."""
    global _db, _risk_mgr, _ml_scorer, _notifier, _broker, _daemon

    # Cargar configuración
    config_path = Path.home() / ".metatrader-mcp" / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
    else:
        config = {}

    # Inicializar componentes
    db_path = Path.home() / ".metatrader-mcp" / "trading.db"
    _db = TradingDatabase(str(db_path))
    _db.init_tables()

    _risk_mgr = RiskManager(_db, config.get("riesgo", {}))
    _ml_scorer = LocalMLScorer(_db)

    telegram_token = config.get("telegram", {}).get("token")
    telegram_chat = config.get("telegram", {}).get("chat_id")
    if telegram_token and telegram_chat:
        _notifier = TelegramNotifier(telegram_token, telegram_chat)
    else:
        _notifier = ConsoleNotifier()

    # Crear broker client según configuración (MT4 o MT5)
    broker_type = config.get("broker_type", "mt5").lower()
    bridge_path = config.get("mt4_bridge_path", None)

    if broker_type == "mt4":
        print("[MCP] Usando MetaTrader 4 (Bridge EA)")
        _broker = MT4BridgeClient(
            bridge_path=bridge_path,
            login=config.get("mt5", {}).get("login"),
            password=config.get("mt5", {}).get("password"),
            server=config.get("mt5", {}).get("server"),
        )
    else:
        print("[MCP] Usando MetaTrader 5 (Python API)")
        _broker = MT5Client(
            login=config.get("mt5", {}).get("login"),
            password=config.get("mt5", {}).get("password"),
            server=config.get("mt5", {}).get("server"),
            path=config.get("mt5", {}).get("path")
        )

    _broker.connect()

    # Establecer referencia al broker en risk manager
    if _risk_mgr:
        _risk_mgr.set_mt5_client(_broker)

    # Iniciar modo daemon si está configurado
    if config.get("autonomo", False):
        _daemon = DaemonTrading(_broker, _db, _risk_mgr, _ml_scorer, _notifier, config)
        _daemon.start()
        if _notifier:
            try:
                await _notifier.send("🤖 MetaTrader MCP Autónomo iniciado")
            except Exception:
                pass

    yield {
        "db": _db,
        "mt5": _broker,  # Mantener nombre "mt5" para compatibilidad con tools
        "broker": _broker,
        "daemon": _daemon,
        "risk_mgr": _risk_mgr,
        "ml_scorer": _ml_scorer,
        "notifier": _notifier,
        "config": config
    }

    # Cleanup
    if _daemon:
        _daemon.stop()
    if _broker:
        _broker.disconnect()
    if _db:
        _db.close()


# Crear servidor MCP - UNA SOLA INSTANCIA con lifespan
mcp = FastMCP("metatrader-autonomous", lifespan=app_lifespan)


# ============================================================
# TOOLS - INFORMACIÓN DE CUENTA
# ============================================================

@mcp.tool()
def get_broker_info(ctx: Context) -> Dict[str, Any]:
    """Obtener información del broker (MT4 o MT5)."""
    broker = ctx.request_context.lifespan_context.get("broker")
    if not broker:
        return {"error": "Broker no conectado"}
    return {
        "broker_type": broker.get_broker_type(),
        "connected": broker.is_connected(),
        "account_info": broker.get_account_info()
    }

@mcp.tool()
def get_account_info(ctx: Context) -> Dict[str, Any]:
    """Obtener información de la cuenta de trading."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.get_account_info()

@mcp.tool()
def get_balance(ctx: Context) -> float:
    """Obtener el balance actual de la cuenta."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.get_balance()

@mcp.tool()
def get_equity(ctx: Context) -> float:
    """Obtener el equity actual de la cuenta."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.get_equity()

@mcp.tool()
def get_margin_info(ctx: Context) -> Dict[str, Any]:
    """Obtener información de margen (free, level, etc)."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.get_margin_info()

# ============================================================
# TOOLS - POSICIONES
# ============================================================

@mcp.tool()
def get_positions(ctx: Context, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """Obtener posiciones abiertas. Filtrar por símbolo opcionalmente."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.get_positions(symbol)

@mcp.tool()
def close_position(ctx: Context, ticket: int) -> Dict[str, Any]:
    """Cerrar una posición específica por su ticket."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    db = ctx.request_context.lifespan_context["db"]

    result = mt5_client.close_position(ticket)

    if result["success"]:
        db.log_trade({
            "action": "close",
            "ticket": ticket,
            "profit": result.get("profit", 0),
            "timestamp": datetime.now().isoformat()
        })

    return result

@mcp.tool()
def close_all_positions(ctx: Context, symbol: Optional[str] = None) -> Dict[str, Any]:
    """Cerrar todas las posiciones. Filtrar por símbolo opcionalmente."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    db = ctx.request_context.lifespan_context["db"]

    result = mt5_client.close_all_positions(symbol)

    if result["success"]:
        db.log_trade({
            "action": "close_all",
            "symbol": symbol,
            "closed_count": result.get("closed_count", 0),
            "timestamp": datetime.now().isoformat()
        })

    return result

@mcp.tool()
def close_profitable_positions(ctx: Context, min_profit: float = 0) -> Dict[str, Any]:
    """Cerrar solo posiciones con beneficio >= min_profit."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]

    positions = mt5_client.get_positions()
    closed = []

    for pos in positions:
        if pos["profit"] >= min_profit:
            result = mt5_client.close_position(pos["ticket"])
            if result["success"]:
                closed.append(pos)

    return {
        "success": True,
        "closed_count": len(closed),
        "closed_positions": closed
    }

@mcp.tool()
def modify_position_sl_tp(ctx: Context, ticket: int,
                         stop_loss: Optional[float] = None,
                         take_profit: Optional[float] = None) -> Dict[str, Any]:
    """Modificar stop loss y/o take profit de una posición abierta."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.modify_position(ticket, stop_loss, take_profit)

@mcp.tool()
def set_trailing_stop(ctx: Context, ticket: int, points: int) -> Dict[str, Any]:
    """Activar trailing stop en una posición (puntos de distancia)."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.set_trailing_stop(ticket, points)

# ============================================================
# TOOLS - ÓRDENES
# ============================================================

@mcp.tool()
def place_market_order(ctx: Context,
                      symbol: str,
                      order_type: str,  # "buy" o "sell"
                      volume: float,
                      stop_loss: Optional[float] = None,
                      take_profit: Optional[float] = None,
                      comment: str = "MCP Autonomous") -> Dict[str, Any]:
    """Abrir orden de mercado (buy/sell)."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    db = ctx.request_context.lifespan_context["db"]
    risk_mgr = ctx.request_context.lifespan_context.get("risk_mgr")

    # Validar riesgo antes de operar
    if risk_mgr:
        validation = risk_mgr.validate_order(symbol, volume, stop_loss)
        if not validation["allowed"]:
            return {"success": False, "error": validation["reason"]}

    result = mt5_client.place_market_order(
        symbol=symbol,
        order_type=order_type,
        volume=volume,
        stop_loss=stop_loss,
        take_profit=take_profit,
        comment=comment
    )

    if result["success"]:
        db.log_trade({
            "action": "open",
            "symbol": symbol,
            "type": order_type,
            "volume": volume,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "ticket": result.get("ticket"),
            "timestamp": datetime.now().isoformat()
        })

    return result

@mcp.tool()
def place_pending_order(ctx: Context,
                       symbol: str,
                       order_type: str,  # "buy_limit", "sell_limit", "buy_stop", "sell_stop"
                       volume: float,
                       price: float,
                       stop_loss: Optional[float] = None,
                       take_profit: Optional[float] = None,
                       expiration: Optional[str] = None) -> Dict[str, Any]:
    """Colocar orden pendiente (limit/stop)."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.place_pending_order(
        symbol=symbol,
        order_type=order_type,
        volume=volume,
        price=price,
        stop_loss=stop_loss,
        take_profit=take_profit,
        expiration=expiration
    )

@mcp.tool()
def cancel_order(ctx: Context, order_id: int) -> Dict[str, Any]:
    """Cancelar una orden pendiente."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.cancel_order(order_id)

@mcp.tool()
def cancel_all_orders(ctx: Context, symbol: Optional[str] = None) -> Dict[str, Any]:
    """Cancelar todas las órdenes pendientes. Filtrar por símbolo opcionalmente."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.cancel_all_orders(symbol)

@mcp.tool()
def get_orders(ctx: Context, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
    """Obtener órdenes pendientes."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.get_orders(symbol)

# ============================================================
# TOOLS - MERCADO Y ANÁLISIS
# ============================================================

@mcp.tool()
def get_symbol_info(ctx: Context, symbol: str) -> Dict[str, Any]:
    """Obtener información de un símbolo (spread, digits, point, etc)."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.get_symbol_info(symbol)

@mcp.tool()
def get_tick(ctx: Context, symbol: str) -> Dict[str, Any]:
    """Obtener tick actual (bid, ask, last, volume)."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.get_tick(symbol)

@mcp.tool()
def get_candles(ctx: Context,
               symbol: str,
               timeframe: str,  # "M1", "M5", "M15", "H1", "H4", "D1", etc
               count: int = 100) -> List[Dict[str, Any]]:
    """Obtener velas históricas."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.get_candles(symbol, timeframe, count)

@mcp.tool()
def get_symbols_list(ctx: Context, group: str = "*") -> List[str]:
    """Obtener lista de símbolos disponibles."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    return mt5_client.get_symbols(group)

@mcp.tool()
def calculate_lot_size(ctx: Context,
                      symbol: str,
                      risk_percent: float,
                      stop_loss_pips: float) -> float:
    """Calcular tamaño de lote basado en riesgo porcentual y SL en pips."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    account_info = mt5_client.get_account_info()
    balance = account_info.get("balance", 0)

    tick = mt5_client.get_tick(symbol)
    point = mt5_client.get_symbol_info(symbol).get("point", 0.00001)

    # Calcular valor monetario del riesgo
    risk_amount = balance * (risk_percent / 100)

    # Calcular valor por pip
    pip_value = point * 10  # Aproximado para la mayoría de pares
    if "JPY" in symbol:
        pip_value = point * 100

    # Calcular lot size
    lot_size = risk_amount / (stop_loss_pips * pip_value)

    # Normalizar a lotes estándar (0.01 mínimo)
    lot_size = round(lot_size / 0.01) * 0.01
    lot_size = max(0.01, min(lot_size, 100))  # Limitar entre 0.01 y 100

    return lot_size

# ============================================================
# TOOLS - MODO AUTÓNOMO
# ============================================================

@mcp.tool()
def get_autonomous_status(ctx: Context) -> Dict[str, Any]:
    """Obtener estado del modo autónomo (daemon)."""
    daemon = ctx.request_context.lifespan_context.get("daemon")
    db = ctx.request_context.lifespan_context["db"]

    if not daemon:
        return {"running": False, "message": "Modo autónomo no activado"}

    stats = db.get_trading_stats(days=1)

    return {
        "running": daemon.is_running(),
        "last_cycle": daemon.get_last_cycle_time(),
        "next_cycle": daemon.get_next_cycle_time(),
        "today_trades": stats.get("trade_count", 0),
        "today_pnl": stats.get("total_pnl", 0),
        "config": daemon.get_config()
    }

@mcp.tool()
def start_autonomous_mode(ctx: Context,
                         symbols: List[str],
                         strategy: str = "fenix",
                         risk_per_trade: float = 1.0,
                         max_daily_trades: int = 5) -> Dict[str, Any]:
    """Iniciar modo autónomo con configuración específica."""
    daemon = ctx.request_context.lifespan_context.get("daemon")
    notifier = ctx.request_context.lifespan_context.get("notifier")

    if not daemon:
        return {"success": False, "error": "Daemon no inicializado en el lifespan"}

    config = {
        "symbols": symbols,
        "strategy": strategy,
        "risk_per_trade": risk_per_trade,
        "max_daily_trades": max_daily_trades,
        "ciclo_minutos": 15
    }

    daemon.update_config(config)

    if not daemon.is_running():
        daemon.start()

    if notifier:
        try:
            asyncio.create_task(notifier.send(
                f"🚀 Modo autónomo iniciado\n"
                f"Símbolos: {', '.join(symbols)}\n"
                f"Estrategia: {strategy}\n"
                f"Riesgo: {risk_per_trade}% por trade"
            ))
        except Exception:
            pass

    return {"success": True, "config": config}

@mcp.tool()
def stop_autonomous_mode(ctx: Context) -> Dict[str, Any]:
    """Detener modo autónomo."""
    daemon = ctx.request_context.lifespan_context.get("daemon")
    notifier = ctx.request_context.lifespan_context.get("notifier")

    if daemon and daemon.is_running():
        daemon.stop()

        if notifier:
            try:
                asyncio.create_task(notifier.send("🛑 Modo autónomo detenido"))
            except Exception:
                pass

        return {"success": True, "message": "Modo autónomo detenido"}

    return {"success": False, "message": "Modo autónomo no estaba corriendo"}

@mcp.tool()
def force_cycle_now(ctx: Context) -> Dict[str, Any]:
    """Forzar ejecución inmediata de un ciclo de trading (bypass espera)."""
    daemon = ctx.request_context.lifespan_context.get("daemon")

    if not daemon or not daemon.is_running():
        return {"success": False, "error": "Daemon no está corriendo"}

    # Ejecutar ciclo inmediatamente
    threading.Thread(target=daemon.force_cycle, daemon=True).start()

    return {"success": True, "message": "Ciclo forzado iniciado"}

# ============================================================
# TOOLS - RIESGO Y PROTECCIÓN
# ============================================================

@mcp.tool()
def get_risk_metrics(ctx: Context) -> Dict[str, Any]:
    """Obtener métricas de riesgo actuales (drawdown, etc)."""
    db = ctx.request_context.lifespan_context["db"]
    risk_mgr = ctx.request_context.lifespan_context.get("risk_mgr")
    mt5_client = ctx.request_context.lifespan_context["mt5"]

    return {
        "daily_drawdown": risk_mgr.get_daily_drawdown() if risk_mgr else 0,
        "daily_loss": risk_mgr.get_daily_loss() if risk_mgr else 0,
        "consecutive_losses": risk_mgr.get_consecutive_losses() if risk_mgr else 0,
        "circuit_breaker_active": risk_mgr.is_circuit_breaker_active() if risk_mgr else False,
        "open_positions_count": len(mt5_client.get_positions())
    }

@mcp.tool()
def reset_circuit_breaker(ctx: Context) -> Dict[str, Any]:
    """Resetear manualmente el circuit breaker (después de revisar situación)."""
    risk_mgr = ctx.request_context.lifespan_context.get("risk_mgr")

    if risk_mgr:
        risk_mgr.reset_circuit_breaker()
        return {"success": True, "message": "Circuit breaker reseteado"}

    return {"success": False, "error": "Risk manager no disponible"}

@mcp.tool()
def emergency_stop(ctx: Context) -> Dict[str, Any]:
    """Parada de emergencia: cierra TODO y detiene modo autónomo."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]
    daemon = ctx.request_context.lifespan_context.get("daemon")
    notifier = ctx.request_context.lifespan_context.get("notifier")

    # Detener daemon
    if daemon and daemon.is_running():
        daemon.stop()

    # Cerrar todas las posiciones
    positions = mt5_client.get_positions()
    for pos in positions:
        mt5_client.close_position(pos["ticket"])

    # Cancelar todas las órdenes
    mt5_client.cancel_all_orders()

    if notifier:
        try:
            asyncio.create_task(notifier.send(
                f"🚨 EMERGENCY STOP ACTIVADO\n"
                f"Posiciones cerradas: {len(positions)}\n"
                f"Todas las órdenes canceladas"
            ))
        except Exception:
            pass

    return {
        "success": True,
        "positions_closed": len(positions),
        "message": "Parada de emergencia completada"
    }

# ============================================================
# TOOLS - ML Y ANÁLISIS
# ============================================================

@mcp.tool()
def get_trade_score(ctx: Context, symbol: str, setup_type: str = "fenix") -> Dict[str, Any]:
    """Obtener score 0-100 de un trade potencial usando ML local."""
    ml_scorer = ctx.request_context.lifespan_context.get("ml_scorer")
    mt5_client = ctx.request_context.lifespan_context["mt5"]

    if not ml_scorer:
        return {"error": "ML scorer no disponible"}

    # Obtener datos del mercado
    candles = mt5_client.get_candles(symbol, "H1", 50)
    tick = mt5_client.get_tick(symbol)

    score, features = ml_scorer.calculate_score(symbol, candles, tick, setup_type)

    return {
        "symbol": symbol,
        "score": score,
        "setup_type": setup_type,
        "recommendation": "trade" if score >= 85 else "watch" if score >= 70 else "skip",
        "features": features
    }

@mcp.tool()
def train_ml_model(ctx: Context, days: int = 30) -> Dict[str, Any]:
    """Entrenar modelo ML con trades históricos (mejora scoring)."""
    ml_scorer = ctx.request_context.lifespan_context.get("ml_scorer")
    db = ctx.request_context.lifespan_context["db"]

    if not ml_scorer:
        return {"error": "ML scorer no disponible"}

    trades = db.get_historical_trades(days=days)
    result = ml_scorer.train(trades)

    return {
        "success": True,
        "trades_used": len(trades),
        "accuracy": result.get("accuracy", 0),
        "message": f"Modelo entrenado con {len(trades)} trades"
    }

# ============================================================
# TOOLS - REPORTES Y HISTORIAL
# ============================================================

@mcp.tool()
def get_daily_report(ctx: Context, days: int = 1) -> Dict[str, Any]:
    """Obtener reporte de trading del día especificado."""
    db = ctx.request_context.lifespan_context["db"]
    stats = db.get_trading_stats(days=days)

    return {
        "period_days": days,
        "total_trades": stats.get("trade_count", 0),
        "winning_trades": stats.get("winning_trades", 0),
        "losing_trades": stats.get("losing_trades", 0),
        "total_pnl": stats.get("total_pnl", 0),
        "win_rate": stats.get("win_rate", 0),
        "average_profit": stats.get("avg_profit", 0),
        "average_loss": stats.get("avg_loss", 0),
        "profit_factor": stats.get("profit_factor", 0),
        "sharpe_ratio": stats.get("sharpe_ratio", 0)
    }

@mcp.tool()
def get_trade_history(ctx: Context, limit: int = 50) -> List[Dict[str, Any]]:
    """Obtener historial de trades recientes."""
    db = ctx.request_context.lifespan_context["db"]
    return db.get_recent_trades(limit)

@mcp.tool()
def export_trades_csv(ctx: Context, filepath: str, days: int = 30) -> Dict[str, Any]:
    """Exportar trades a CSV para análisis externo."""
    db = ctx.request_context.lifespan_context["db"]
    result = db.export_to_csv(filepath, days)

    return {
        "success": result,
        "filepath": filepath,
        "message": f"Trades exportados a {filepath}"
    }

# ============================================================
# TOOLS — ESTRATEGIA FÉNIX $5
# ============================================================

@mcp.tool()
def run_fenix_5usd(ctx: Context, dry_run: bool = True) -> Dict[str, Any]:
    """Ejecuta la estrategia Fénix con objetivo de $5 de ganancia.
    Usa dry_run=False solo con autorización explícita del usuario."""
    import subprocess
    import os

    script = str(Path.home() / "Robo MQL5" / "fenix_5usd.py")
    flag = "--dry-run" if dry_run else "--live"
    mode_label = "DRY-RUN" if dry_run else "LIVE"

    if not dry_run:
        return {
            "error": "Para ejecutar en real escribe: 'SI OPERAR' directamente en la terminal con --live"
        }

    if not Path(script).exists():
        return {"error": f"Script no encontrado: {script}"}

    try:
        env = dict(os.environ)
        env["PYTHONPATH"] = (
            str(Path.home() / "Robo MQL5" / "scripts")
            + ":" + str(Path.home() / "Desktop/metatrader-mcp-server/src")
        )
        result = subprocess.run(
            [sys.executable, script, flag],
            capture_output=True, text=True, timeout=120, env=env
        )
        return {
            "mode": mode_label,
            "output": result.stdout[-2000:] if result.stdout else "",
            "errors": result.stderr[-500:] if result.stderr else "",
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {"mode": mode_label, "output": "Timeout — estrategia corriendo en background"}
    except Exception as e:
        return {"error": str(e)}


@mcp.tool()
def fenix_market_scan(ctx: Context) -> Dict[str, Any]:
    """Analiza EURUSD y GBPUSD en M15 con EMA8/21 + RSI. Devuelve señales activas."""
    mt5_client = ctx.request_context.lifespan_context["mt5"]

    try:
        signals = []
        for sym in ["EURUSD", "GBPUSD"]:
            rates_raw = mt5_client.get_candles(sym, "M15", 60)
            if not rates_raw or len(rates_raw) < 25:
                continue

            closes = [r["close"] for r in rates_raw]

            # EMA calculation
            def ema_calc(data, n):
                k = 2 / (n + 1)
                e = [data[0]]
                for p in data[1:]:
                    e.append(p * k + e[-1] * (1 - k))
                return e

            # RSI calculation
            def rsi_calc(closes_data, n=14):
                if len(closes_data) < n + 1:
                    return 50.0
                diffs = [closes_data[i] - closes_data[i - 1] for i in range(1, len(closes_data))]
                g = [max(d, 0) for d in diffs[-n:]]
                l = [abs(min(d, 0)) for d in diffs[-n:]]
                ag, al = sum(g) / n, sum(l) / n
                return 100 - (100 / (1 + ag / al)) if al else 100.0

            e8 = ema_calc(closes, 8)
            e21 = ema_calc(closes, 21)
            r = rsi_calc(closes)
            prev_r = rsi_calc(closes[:-1])

            tick = mt5_client.get_tick(sym)
            info = mt5_client.get_symbol_info(sym)

            if not tick or not info:
                continue

            pip = info.get("point", 0.00001) * 10
            spread = round((tick["ask"] - tick["bid"]) / pip, 1) if tick else 0

            entry = None
            if e8[-2] < e21[-2] and e8[-1] > e21[-1] and prev_r < 32 and r >= 32:
                entry = "BUY"
            elif e8[-2] > e21[-2] and e8[-1] < e21[-1] and prev_r > 68 and r <= 68:
                entry = "SELL"

            signals.append({
                "symbol": sym,
                "price": tick.get("bid", 0),
                "ema8_above_21": e8[-1] > e21[-1],
                "rsi": round(r, 1),
                "spread_pips": spread,
                "signal": entry or "ESPERAR"
            })

        account = mt5_client.get_account_info()
        return {
            "account": {
                "balance": account.get("balance", 0),
                "equity": account.get("equity", 0),
                "target": account.get("balance", 0) + 5
            },
            "signals": signals
        }
    except Exception as e:
        return {"error": str(e)}


# ============================================================
# MAIN
# ============================================================

def main():
    """Punto de entrada principal."""
    import argparse

    parser = argparse.ArgumentParser(description="MetaTrader MCP Server - Autonomous Mode")
    parser.add_argument("--daemon", action="store_true", help="Ejecutar en modo daemon")
    parser.add_argument("--config", type=str, help="Ruta al archivo de configuración")
    parser.add_argument("--transport", type=str, default="stdio", choices=["stdio", "sse"])
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)

    args = parser.parse_args()

    # Si se especifica config, usar esa ruta
    if args.config:
        config_path = Path(args.config)
        if config_path.exists():
            # Copiar a la ubicación esperada
            target_path = Path.home() / ".metatrader-mcp" / "config.json"
            target_path.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.copy(config_path, target_path)

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="sse", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
