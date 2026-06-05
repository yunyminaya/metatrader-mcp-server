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
import sqlite3
import signal
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import numpy as np
from mcp.server.fastmcp import FastMCP, Context
from sklearn.ensemble import RandomForestClassifier
import joblib

# Importar módulos locales
from database import TradingDatabase
from risk_manager import RiskManager
from ml_local import LocalMLScorer
from notifier import TelegramNotifier
from mt5_client import MT5Client
from daemon_trading import DaemonTrading

# Crear servidor MCP
mcp = FastMCP("metatrader-autonomous")

# Variables globales para el estado
db: Optional[TradingDatabase] = None
risk_mgr: Optional[RiskManager] = None
ml_scorer: Optional[LocalMLScorer] = None
notifier: Optional[TelegramNotifier] = None
mt5: Optional[MT5Client] = None
daemon: Optional[DaemonTrading] = None

# ============================================================
# LIFECYCLE
# ============================================================

@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[Dict]:
    """Maneja el ciclo de vida del servidor."""
    global db, risk_mgr, ml_scorer, notifier, mt5, daemon

    # Cargar configuración
    config_path = Path.home() / ".metatrader-mcp" / "config.json"
    if config_path.exists():
        with open(config_path) as f:
            config = json.load(f)
    else:
        config = {}

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

    # Conectar a MT5
    mt5 = MT5Client(
        login=config.get("mt5", {}).get("login"),
        password=config.get("mt5", {}).get("password"),
        server=config.get("mt5", {}).get("server"),
        path=config.get("mt5", {}).get("path")
    )
    mt5.connect()

    # Iniciar modo daemon si está configurado
    if config.get("autonomo", False):
        daemon = DaemonTrading(mt5, db, risk_mgr, ml_scorer, notifier, config)
        daemon.start()
        if notifier:
            await notifier.send("🤖 MetaTrader MCP Autónomo iniciado")

    yield {
        "db": db,
        "mt5": mt5,
        "daemon": daemon,
        "config": config
    }

    # Cleanup
    if daemon:
        daemon.stop()
    if mt5:
        mt5.disconnect()
    if db:
        db.close()

mcp = FastMCP("metatrader-autonomous", lifespan=app_lifespan)

# ============================================================
# TOOLS - INFORMACIÓN DE CUENTA
# ============================================================

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
    db = ctx.request_context.lifespan_context["db"]

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
        asyncio.create_task(notifier.send(
            f"🚀 Modo autónomo iniciado\n"
            f"Símbolos: {', '.join(symbols)}\n"
            f"Estrategia: {strategy}\n"
            f"Riesgo: {risk_per_trade}% por trade"
        ))

    return {"success": True, "config": config}

@mcp.tool()
def stop_autonomous_mode(ctx: Context) -> Dict[str, Any]:
    """Detener modo autónomo."""
    daemon = ctx.request_context.lifespan_context.get("daemon")
    notifier = ctx.request_context.lifespan_context.get("notifier")

    if daemon and daemon.is_running():
        daemon.stop()

        if notifier:
            asyncio.create_task(notifier.send("🛑 Modo autónomo detenido"))

        return {"success": True, "message": "Modo autónomo detenido"}

    return {"success": False, "message": "Modo autónomo no estaba corriendo"}

@mcp.tool()
def force_cycle_now(ctx: Context) -> Dict[str, Any]:
    """Forzar ejecución inmediata de un ciclo de trading (bypass espera)."""
    daemon = ctx.request_context.lifespan_context.get("daemon")

    if not daemon or not daemon.is_running():
        return {"success": False, "error": "Daemon no está corriendo"}

    # Ejecutar ciclo inmediatamente
    threading.Thread(target=daemon.force_cycle).start()

    return {"success": True, "message": "Ciclo forzado iniciado"}

# ============================================================
# TOOLS - RIESGO Y PROTECCIÓN
# ============================================================

@mcp.tool()
def get_risk_metrics(ctx: Context) -> Dict[str, Any]:
    """Obtener métricas de riesgo actuales (drawdown, etc)."""
    db = ctx.request_context.lifespan_context["db"]
    risk_mgr = ctx.request_context.lifespan_context.get("risk_mgr")

    return {
        "daily_drawdown": risk_mgr.get_daily_drawdown() if risk_mgr else 0,
        "daily_loss": risk_mgr.get_daily_loss() if risk_mgr else 0,
        "consecutive_losses": risk_mgr.get_consecutive_losses() if risk_mgr else 0,
        "circuit_breaker_active": risk_mgr.is_circuit_breaker_active() if risk_mgr else False,
        "open_positions_count": len(ctx.request_context.lifespan_context["mt5"].get_positions())
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
        asyncio.create_task(notifier.send(
            f"🚨 EMERGENCY STOP ACTIVADO\n"
            f"Posiciones cerradas: {len(positions)}\n"
            f"Todas las órdenes canceladas"
        ))

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
# MAIN
# ============================================================

if __name__ == "__main__":
    # Parsear argumentos
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
