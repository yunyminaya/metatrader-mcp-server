#!/usr/bin/env python3
"""
Notifier - Notificaciones Telegram
Alertas gratuitas sin gastar tokens.
"""

import asyncio
from typing import Optional
import aiohttp


class TelegramNotifier:
    """
    Envía notificaciones a Telegram.
    Gratuito, solo requiere bot token y chat ID.
    """

    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id
        self.base_url = f"https://api.telegram.org/bot{token}"
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Obtener o crear sesión HTTP."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """Enviar mensaje a Telegram."""
        try:
            session = await self._get_session()

            url = f"{self.base_url}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode
            }

            async with session.post(url, json=payload) as response:
                if response.status == 200:
                    return True
                else:
                    print(f"[Notifier Error] Telegram API: {response.status}")
                    return False

        except Exception as e:
            print(f"[Notifier Error] Enviando mensaje: {e}")
            return False

    async def send_trade_alert(self, symbol: str, direction: str,
                               volume: float, entry_price: float,
                               stop_loss: float, take_profit: float,
                               score: int) -> bool:
        """Enviar alerta de trade ejecutado."""
        emoji = "🟢" if direction == "buy" else "🔴"

        message = (
            f"{emoji} <b>NUEVO TRADE</b>\n\n"
            f"<b>Símbolo:</b> {symbol}\n"
            f"<b>Dirección:</b> {direction.upper()}\n"
            f"<b>Volumen:</b> {volume} lots\n"
            f"<b>Entrada:</b> {entry_price:.5f}\n"
            f"<b>SL:</b> {stop_loss:.5f}\n"
            f"<b>TP:</b> {take_profit:.5f}\n"
            f"<b>Score:</b> {score}/100\n"
        )

        return await self.send(message)

    async def send_close_alert(self, symbol: str, profit: float,
                              ticket: int, reason: str = "manual") -> bool:
        """Enviar alerta de cierre de posición."""
        emoji = "✅" if profit > 0 else "❌"

        message = (
            f"{emoji} <b>POSICIÓN CERRADA</b>\n\n"
            f"<b>Símbolo:</b> {symbol}\n"
            f"<b>Ticket:</b> #{ticket}\n"
            f"<b>Profit:</b> ${profit:.2f}\n"
            f"<b>Razón:</b> {reason}\n"
        )

        return await self.send(message)

    async def send_risk_alert(self, drawdown: float, daily_loss: float,
                             consecutive_losses: int) -> bool:
        """Enviar alerta de riesgo (circuit breaker)."""
        message = (
            f"⚠️ <b>ALERTA DE RIESGO</b>\n\n"
            f"<b>Drawdown:</b> {drawdown:.2f}%\n"
            f"<b>Pérdida diaria:</b> ${daily_loss:.2f}\n"
            f"<b>Pérdidas consecutivas:</b> {consecutive_losses}\n\n"
            f"🔒 Circuit breaker activado. Trading pausado."
        )

        return await self.send(message)

    async def send_daily_report(self, stats: dict) -> bool:
        """Enviar reporte diario."""
        message = (
            f"📊 <b>REPORTE DIARIO</b>\n\n"
            f"<b>Trades:</b> {stats.get('trade_count', 0)}\n"
            f"<b>Ganados:</b> {stats.get('winning_trades', 0)}\n"
            f"<b>Perdidos:</b> {stats.get('losing_trades', 0)}\n"
            f"<b>Win Rate:</b> {stats.get('win_rate', 0):.1f}%\n"
            f"<b>P&L:</b> ${stats.get('total_pnl', 0):.2f}\n"
            f"<b>Profit Factor:</b> {stats.get('profit_factor', 0):.2f}\n"
        )

        return await self.send(message)

    async def send_emergency(self, message: str) -> bool:
        """Enviar alerta de emergencia."""
        emergency_msg = f"🚨 <b>EMERGENCY STOP</b>\n\n{message}"
        return await self.send(emergency_msg)

    async def close(self):
        """Cerrar sesión HTTP."""
        if self._session and not self._session.closed:
            await self._session.close()


class ConsoleNotifier:
    """
    Notificador simple a consola (fallback si no hay Telegram).
    """

    async def send(self, message: str, **kwargs) -> bool:
        """Imprimir mensaje en consola."""
        print(f"[NOTIFICATION] {message}")
        return True

    async def send_trade_alert(self, **kwargs) -> bool:
        """Alerta de trade en consola."""
        print(f"[TRADE ALERT] {kwargs}")
        return True

    async def send_close_alert(self, **kwargs) -> bool:
        """Alerta de cierre en consola."""
        print(f"[CLOSE ALERT] {kwargs}")
        return True

    async def send_risk_alert(self, **kwargs) -> bool:
        """Alerta de riesgo en consola."""
        print(f"[RISK ALERT] {kwargs}")
        return True

    async def send_daily_report(self, **kwargs) -> bool:
        """Reporte diario en consola."""
        print(f"[DAILY REPORT] {kwargs}")
        return True

    async def send_emergency(self, message: str) -> bool:
        """Emergencia en consola."""
        print(f"[EMERGENCY] {message}")
        return True

    async def close(self):
        """No-op para consola."""
        pass
