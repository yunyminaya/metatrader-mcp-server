import logging
import time
import threading
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


_auto_tasks = {}


def schedule_close_positions(client, symbol: Optional[str] = None, close_time: Optional[str] = None, close_day: Optional[str] = None) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    positions = client.order.get_all_positions()
    if positions is None or positions.index.size == 0:
        return {"error": False, "message": "No positions to close", "data": None}
    if close_time:
        target_hour, target_min = map(int, close_time.split(":"))
        if now.hour >= target_hour and now.minute >= target_min:
            filtered = positions
            if symbol:
                filtered = positions[positions['symbol'] == symbol]
            closed = []
            errors = []
            for _, pos in filtered.iterrows():
                try:
                    r = client.order.close_position(pos['ticket'])
                    if r.get("error"):
                        errors.append({"id": pos['ticket'], "error": r.get("message")})
                    else:
                        closed.append(pos['ticket'])
                except Exception as e:
                    errors.append({"id": pos['ticket'], "error": str(e)})
            return {
                "error": len(closed) == 0,
                "message": f"Scheduled close: {len(closed)} closed, {len(errors)} errors",
                "data": {"closed_ids": closed, "errors": errors, "trigger": f"time={close_time}"}
            }
        return {"error": False, "message": f"Waiting for {close_time} UTC (current: {now.hour:02d}:{now.minute:02d})", "data": None}
    if close_day:
        target_dow = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"].index(close_day.lower())
        if now.weekday() >= target_dow:
            filtered = positions
            if symbol:
                filtered = positions[positions['symbol'] == symbol]
            closed = []
            errors = []
            for _, pos in filtered.iterrows():
                try:
                    r = client.order.close_position(pos['ticket'])
                    if r.get("error"):
                        errors.append({"id": pos['ticket'], "error": r.get("message")})
                    else:
                        closed.append(pos['ticket'])
                except Exception as e:
                    errors.append({"id": pos['ticket'], "error": str(e)})
            return {
                "error": len(closed) == 0,
                "message": f"Weekly close ({close_day}): {len(closed)} closed, {len(errors)} errors",
                "data": {"closed_ids": closed, "errors": errors, "trigger": f"day={close_day}"}
            }
        return {"error": False, "message": f"Waiting for {close_day} (current: {now.strftime('%A')})", "data": None}
    return {"error": True, "message": "Specify close_time (HH:MM) or close_day", "data": None}


def hedge_all_on_drawdown(client, drawdown_threshold_pct: float = 15.0, hedge_symbol: str = "XAUUSD") -> Dict[str, Any]:
    import MetaTrader5 as mt5
    acc = mt5.account_info()
    if acc is None:
        return {"error": True, "message": "Cannot get account info", "data": None}
    balance = acc.balance
    equity = acc.equity
    if balance == 0:
        return {"error": True, "message": "Zero balance", "data": None}
    dd_pct = (balance - equity) / balance * 100
    if dd_pct < drawdown_threshold_pct:
        return {"error": False, "message": f"Drawdown {dd_pct:.1f}% below threshold {drawdown_threshold_pct}%. No hedge needed.", "data": {"drawdown_pct": round(dd_pct, 1), "threshold": drawdown_threshold_pct, "hedged": False}}
    try:
        positions = client.order.get_all_positions()
    except Exception as e:
        return {"error": True, "message": str(e), "data": None}
    net_volume = 0.0
    if positions is not None and positions.index.size > 0:
        for _, pos in positions.iterrows():
            if pos["type"] == "BUY":
                net_volume += pos["volume"]
            else:
                net_volume -= pos["volume"]
    if net_volume == 0:
        return {"error": False, "message": "No net exposure to hedge", "data": None}
    hedge_type = "SELL" if net_volume > 0 else "BUY"
    hedge_vol = round(abs(net_volume) * 0.5, 2)
    try:
        result = client.order.place_market_order(type=hedge_type, symbol=hedge_symbol, volume=hedge_vol)
    except Exception as e:
        return {"error": True, "message": f"Hedge failed: {e}", "data": None}
    return {
        "error": False,
        "message": f"Auto-hedge triggered at {dd_pct:.1f}% drawdown: {hedge_vol} {hedge_type} {hedge_symbol}",
        "data": {"drawdown_pct": round(dd_pct, 1), "threshold": drawdown_threshold_pct, "hedge_symbol": hedge_symbol, "hedge_type": hedge_type, "hedge_volume": hedge_vol, "result": result}
    }


def trail_all_positions(client, atr_multiplier: float = 2.0, symbol: Optional[str] = None) -> Dict[str, Any]:
    positions = client.order.get_all_positions()
    if positions is None or positions.index.size == 0:
        return {"error": False, "message": "No positions to trail", "data": None}
    results = []
    errors = []
    for _, pos in positions.iterrows():
        if symbol and pos["symbol"] != symbol:
            continue
        try:
            df = client.market.get_candles_latest(symbol_name=pos["symbol"], timeframe="H1", count=50)
            if df is None or len(df) < 5:
                errors.append({"id": pos['ticket'], "error": "Not enough data"})
                continue
            atr = (df['high'] - df['low']).mean()
            sl_distance = atr * atr_multiplier
            entry = pos["price_open"]
            current = pos["price_current"]
            sl = pos["sl"]
            if pos["type"] == "BUY":
                new_sl = current - sl_distance
                if sl is None or sl == 0 or new_sl > sl:
                    r = client.order.modify_position(id=pos['ticket'], stop_loss=new_sl)
                    results.append({"id": pos['ticket'], "symbol": pos["symbol"], "old_sl": sl, "new_sl": new_sl, "result": r})
            else:
                new_sl = current + sl_distance
                if sl is None or sl == 0 or new_sl < sl:
                    r = client.order.modify_position(id=pos['ticket'], stop_loss=new_sl)
                    results.append({"id": pos['ticket'], "symbol": pos["symbol"], "old_sl": sl, "new_sl": new_sl, "result": r})
        except Exception as e:
            errors.append({"id": pos['ticket'], "error": str(e)})
    return {
        "error": False,
        "message": f"Trailed {len(results)} positions" + (f" ({len(errors)} errors)" if errors else ""),
        "data": {"trailed": results, "errors": errors}
    }


def breakeven_all_profitable(client, profit_pips: float = 10.0, symbol: Optional[str] = None) -> Dict[str, Any]:
    positions = client.order.get_all_positions()
    if positions is None or positions.index.size == 0:
        return {"error": False, "message": "No positions", "data": None}
    results = []
    errors = []
    for _, pos in positions.iterrows():
        if symbol and pos["symbol"] != symbol:
            continue
        try:
            info = client.market.get_symbol_info(pos["symbol"])
            digits = info.get("digits", 5) if isinstance(info, dict) else 5
            pip_size = 10 ** -(digits - 1) if digits > 3 else 0.0001
            entry = pos["price_open"]
            current = pos["price_current"]
            sl = pos["sl"]
            profit_distance = current - entry if pos["type"] == "BUY" else entry - current
            profit_in_pips = profit_distance / pip_size
            if profit_in_pips >= profit_pips and (sl is None or sl == 0 or (pos["type"] == "BUY" and sl < entry) or (pos["type"] == "SELL" and sl > entry)):
                new_sl = entry + (pip_size * 1 if pos["type"] == "BUY" else -pip_size * 1)
                r = client.order.modify_position(id=pos['ticket'], stop_loss=new_sl)
                results.append({"id": pos['ticket'], "symbol": pos["symbol"], "profit_pips": profit_in_pips, "old_sl": sl, "new_sl": new_sl, "result": r})
        except Exception as e:
            errors.append({"id": pos['ticket'], "error": str(e)})
    return {
        "error": False,
        "message": f"Breakeven set on {len(results)} positions",
        "data": {"breakeven_set": results, "errors": errors}
    }


def protect_profits(client, trail_activation_pips: float = 20.0, trail_distance_pips: float = 10.0, symbol: Optional[str] = None) -> Dict[str, Any]:
    positions = client.order.get_all_positions()
    if positions is None or positions.index.size == 0:
        return {"error": False, "message": "No positions", "data": None}
    results = []
    errors = []
    for _, pos in positions.iterrows():
        if symbol and pos["symbol"] != symbol:
            continue
        try:
            info = client.market.get_symbol_info(pos["symbol"])
            digits = info.get("digits", 5) if isinstance(info, dict) else 5
            pip_size = 10 ** -(digits - 1) if digits > 3 else 0.0001
            entry = pos["price_open"]
            current = pos["price_current"]
            sl = pos["sl"]
            if pos["type"] == "BUY":
                profit_pips = (current - entry) / pip_size
                if profit_pips >= trail_activation_pips:
                    new_sl = current - trail_distance_pips * pip_size
                    if sl is None or sl == 0 or new_sl > sl:
                        r = client.order.modify_position(id=pos['ticket'], stop_loss=round(new_sl, digits))
                        results.append({"id": pos['ticket'], "symbol": pos["symbol"], "profit_pips": round(profit_pips, 1), "old_sl": sl, "new_sl": round(new_sl, digits), "result": r})
            else:
                profit_pips = (entry - current) / pip_size
                if profit_pips >= trail_activation_pips:
                    new_sl = current + trail_distance_pips * pip_size
                    if sl is None or sl == 0 or new_sl < sl:
                        r = client.order.modify_position(id=pos['ticket'], stop_loss=round(new_sl, digits))
                        results.append({"id": pos['ticket'], "symbol": pos["symbol"], "profit_pips": round(profit_pips, 1), "old_sl": sl, "new_sl": round(new_sl, digits), "result": r})
        except Exception as e:
            errors.append({"id": pos['ticket'], "error": str(e)})
    return {
        "error": False,
        "message": f"Profit protection active on {len(results)} positions",
        "data": {"protected": results, "errors": errors}
    }
