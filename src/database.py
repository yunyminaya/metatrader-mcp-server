#!/usr/bin/env python3
"""
Database - Persistencia SQLite
Guarda estado de trading entre reinicios.
Thread-safe con locking para acceso concurrente.
"""

import sqlite3
import json
import threading
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
import csv


class TradingDatabase:
    """
    Base de datos SQLite para persistencia de trading.
    Mantiene historial, estadísticas y estado entre reinicios.
    Thread-safe con locking explícito.
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()

    def _get_connection(self) -> sqlite3.Connection:
        """Obtener conexión a la base de datos (una por thread)."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            # WAL mode para mejor concurrencia
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def _execute_safe(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        """Ejecutar query de forma thread-safe."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor

    def _query_safe(self, query: str, params: tuple = ()) -> List[sqlite3.Row]:
        """Ejecutar query de lectura de forma thread-safe."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()

    def init_tables(self):
        """Inicializar tablas necesarias."""
        conn = self._get_connection()
        cursor = conn.cursor()

        # Tabla de trades
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                symbol TEXT,
                type TEXT,
                volume REAL,
                open_price REAL,
                close_price REAL,
                stop_loss REAL,
                take_profit REAL,
                ticket INTEGER,
                profit REAL,
                score INTEGER,
                strategy TEXT,
                timestamp TEXT NOT NULL,
                exit_timestamp TEXT,
                exit_reason TEXT,
                features TEXT
            )
        """)

        # Tabla de estado del daemon
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS daemon_state (
                id INTEGER PRIMARY KEY,
                running BOOLEAN,
                last_cycle TEXT,
                config TEXT,
                updated_at TEXT
            )
        """)

        # Tabla de métricas de riesgo
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS risk_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT NOT NULL,
                daily_drawdown REAL,
                daily_loss REAL,
                consecutive_losses INTEGER,
                circuit_breaker_active BOOLEAN,
                max_drawdown_period REAL,
                timestamp TEXT
            )
        """)

        # Tabla de snapshots de cuenta
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                balance REAL,
                equity REAL,
                margin REAL,
                free_margin REAL,
                margin_level REAL,
                open_positions INTEGER
            )
        """)

        # Tabla de configuración
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        """)

        # Índices
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_ticket ON trades(ticket)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_risk_date ON risk_metrics(date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON account_snapshots(timestamp)")

        conn.commit()

    def log_trade(self, trade_data: Dict[str, Any]):
        """Registrar un trade en la base de datos."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO trades (
                    action, symbol, type, volume, open_price, close_price,
                    stop_loss, take_profit, ticket, profit, score, strategy,
                    timestamp, features
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_data.get("action"),
                trade_data.get("symbol"),
                trade_data.get("type"),
                trade_data.get("volume"),
                trade_data.get("open_price"),
                trade_data.get("close_price"),
                trade_data.get("stop_loss"),
                trade_data.get("take_profit"),
                trade_data.get("ticket"),
                trade_data.get("profit"),
                trade_data.get("score"),
                trade_data.get("strategy"),
                trade_data.get("timestamp", datetime.now().isoformat()),
                json.dumps(trade_data.get("features", {}))
            ))

            conn.commit()

    def update_trade_exit(self, ticket: int, profit: float,
                         exit_reason: str = "manual"):
        """Actualizar trade cuando se cierra."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                UPDATE trades
                SET profit = ?, exit_timestamp = ?, exit_reason = ?
                WHERE ticket = ? AND action = 'open'
            """, (profit, datetime.now().isoformat(), exit_reason, ticket))

            conn.commit()

    def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Obtener trades recientes."""
        rows = self._query_safe("""
            SELECT * FROM trades
            ORDER BY timestamp DESC
            LIMIT ?
        """, (limit,))

        return [dict(row) for row in rows]

    def get_historical_trades(self, days: int = 30) -> List[Dict[str, Any]]:
        """Obtener trades históricos para ML."""
        since = (datetime.now() - timedelta(days=days)).isoformat()

        rows = self._query_safe("""
            SELECT * FROM trades
            WHERE timestamp > ? AND action = 'open'
            ORDER BY timestamp DESC
        """, (since,))

        return [dict(row) for row in rows]

    def get_today_trade_count(self) -> int:
        """Contar trades de hoy."""
        rows = self._query_safe("""
            SELECT COUNT(*) as cnt FROM trades
            WHERE date(timestamp) = date('now')
            AND action = 'open'
        """)

        return rows[0]["cnt"] if rows else 0

    def get_trading_stats(self, days: int = 1) -> Dict[str, Any]:
        """Obtener estadísticas de trading."""
        since = (datetime.now() - timedelta(days=days)).isoformat()

        # Total trades
        rows = self._query_safe("""
            SELECT COUNT(*) as cnt, COALESCE(SUM(profit), 0) as total_pnl
            FROM trades
            WHERE timestamp > ? AND action = 'open'
        """, (since,))

        total_trades = rows[0]["cnt"] if rows else 0
        total_pnl = rows[0]["total_pnl"] if rows else 0

        # Winning trades
        rows = self._query_safe("""
            SELECT COUNT(*) as cnt, COALESCE(AVG(profit), 0) as avg_val
            FROM trades
            WHERE timestamp > ? AND action = 'open' AND profit > 0
        """, (since,))

        winning_trades = rows[0]["cnt"] if rows else 0
        avg_profit = rows[0]["avg_val"] if rows else 0

        # Losing trades
        rows = self._query_safe("""
            SELECT COUNT(*) as cnt, COALESCE(AVG(profit), 0) as avg_val
            FROM trades
            WHERE timestamp > ? AND action = 'open' AND profit < 0
        """, (since,))

        losing_trades = rows[0]["cnt"] if rows else 0
        avg_loss = rows[0]["avg_val"] if rows else 0

        # Calcular métricas
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        # Profit factor
        gross_profit = abs(avg_profit * winning_trades) if avg_profit else 0
        gross_loss = abs(avg_loss * losing_trades) if avg_loss else 1
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        # Sharpe ratio simplificado
        sharpe_ratio = self._calculate_sharpe(days)

        return {
            "trade_count": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "total_pnl": total_pnl or 0,
            "win_rate": win_rate,
            "avg_profit": avg_profit,
            "avg_loss": avg_loss,
            "profit_factor": profit_factor,
            "sharpe_ratio": sharpe_ratio
        }

    def _calculate_sharpe(self, days: int = 30) -> float:
        """Calcular Sharpe Ratio simplificado (asumiendo risk-free rate = 0)."""
        since = (datetime.now() - timedelta(days=days)).isoformat()

        rows = self._query_safe("""
            SELECT profit FROM trades
            WHERE timestamp > ? AND action = 'open' AND profit IS NOT NULL
            ORDER BY timestamp
        """, (since,))

        if not rows or len(rows) < 2:
            return 0.0

        profits = [row["profit"] for row in rows if row["profit"] is not None]

        if len(profits) < 2:
            return 0.0

        avg_return = sum(profits) / len(profits)
        variance = sum((p - avg_return) ** 2 for p in profits) / (len(profits) - 1)
        std_dev = math.sqrt(variance) if variance > 0 else 0

        if std_dev == 0:
            return 0.0

        # Annualizar (asumiendo ~252 días de trading)
        sharpe = (avg_return / std_dev) * math.sqrt(252)
        return round(sharpe, 2)

    def save_account_snapshot(self, account_info: Dict[str, Any],
                             open_positions: int):
        """Guardar snapshot de la cuenta."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT INTO account_snapshots
                (timestamp, balance, equity, margin, free_margin, margin_level, open_positions)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.now().isoformat(),
                account_info.get("balance"),
                account_info.get("equity"),
                account_info.get("margin"),
                account_info.get("free_margin"),
                account_info.get("margin_level"),
                open_positions
            ))

            conn.commit()

    def save_risk_metrics(self, metrics: Dict[str, Any]):
        """Guardar métricas de riesgo."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            today = datetime.now().strftime("%Y-%m-%d")

            cursor.execute("""
                INSERT OR REPLACE INTO risk_metrics
                (id, date, daily_drawdown, daily_loss, consecutive_losses,
                 circuit_breaker_active, max_drawdown_period, timestamp)
                VALUES (
                    (SELECT id FROM risk_metrics WHERE date = ?),
                    ?, ?, ?, ?, ?, ?, ?
                )
            """, (
                today, today,
                metrics.get("daily_drawdown", 0),
                metrics.get("daily_loss", 0),
                metrics.get("consecutive_losses", 0),
                metrics.get("circuit_breaker_active", False),
                metrics.get("max_drawdown_period", 0),
                datetime.now().isoformat()
            ))

            conn.commit()

    def get_risk_metrics_history(self, days: int = 7) -> List[Dict[str, Any]]:
        """Obtener histórico de métricas de riesgo."""
        since = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        rows = self._query_safe("""
            SELECT * FROM risk_metrics
            WHERE date >= ?
            ORDER BY date DESC
        """, (since,))

        return [dict(row) for row in rows]

    def get_daily_starting_balance(self) -> float:
        """Obtener balance inicial del día."""
        rows = self._query_safe("""
            SELECT balance FROM account_snapshots
            WHERE date(timestamp) = date('now')
            ORDER BY timestamp ASC LIMIT 1
        """)

        if rows:
            return rows[0]["balance"]
        return 0.0

    def get_latest_equity(self) -> float:
        """Obtener equity más reciente."""
        rows = self._query_safe("""
            SELECT equity FROM account_snapshots
            ORDER BY timestamp DESC LIMIT 1
        """)

        if rows:
            return rows[0]["equity"]
        return 0.0

    def set_config(self, key: str, value: Any):
        """Guardar configuración."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()

            cursor.execute("""
                INSERT OR REPLACE INTO config (key, value, updated_at)
                VALUES (?, ?, ?)
            """, (key, json.dumps(value), datetime.now().isoformat()))

            conn.commit()

    def get_config(self, key: str, default: Any = None) -> Any:
        """Obtener configuración."""
        rows = self._query_safe("SELECT value FROM config WHERE key = ?", (key,))

        if rows:
            try:
                return json.loads(rows[0]["value"])
            except json.JSONDecodeError:
                return rows[0]["value"]
        return default

    def export_to_csv(self, filepath: str, days: int = 30) -> bool:
        """Exportar trades a CSV."""
        try:
            trades = self.get_historical_trades(days)

            if not trades:
                return False

            with open(filepath, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=trades[0].keys())
                writer.writeheader()
                writer.writerows(trades)

            return True
        except Exception as e:
            print(f"[Database Error] Export CSV: {e}")
            return False

    def close(self):
        """Cerrar conexión a la base de datos."""
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
