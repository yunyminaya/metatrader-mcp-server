import logging
import math
import time
import json
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


_alert_state = {
    "price_alerts": [],
    "protection": {
        "daily_loss_limit": None,
        "max_positions": None,
        "max_correlation_exposure": None,
        "max_drawdown": None,
        "trading_enabled": True,
        "start_balance": None,
        "today_pnl": 0,
        "today_date": None,
    }
}


def _check_daily_reset(client):
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    p = _alert_state["protection"]
    if p["today_date"] != today:
        p["today_date"] = today
        p["today_pnl"] = 0
        if p["start_balance"] is None:
            import MetaTrader5 as mt5
            acc = mt5.account_info()
            if acc:
                p["start_balance"] = acc.balance


def _get_account_metrics(client):
    import MetaTrader5 as mt5
    acc = mt5.account_info()
    if acc is None:
        return None
    positions = client.order.get_all_positions()
    pos_count = positions.index.size if positions is not None else 0
    return {
        "balance": acc.balance, "equity": acc.equity, "margin": acc.margin,
        "free_margin": acc.margin_free, "margin_level": acc.margin_level,
        "profit": acc.profit, "positions": pos_count,
    }


def alert_price_set(client, symbol: str, price: float, direction: str = "ABOVE", note: str = "") -> Dict[str, Any]:
    _alert_state["price_alerts"].append({
        "id": len(_alert_state["price_alerts"]) + 1,
        "symbol": symbol, "price": price, "direction": direction.upper(),
        "note": note, "triggered": False, "created": datetime.now(timezone.utc).isoformat(),
    })
    return {"error": False, "message": f"Alert set: {symbol} {direction} {price}", "data": {"alert_id": _alert_state["price_alerts"][-1]["id"]}}


def alert_check(client) -> Dict[str, Any]:
    triggered = []
    active = []
    for alert in _alert_state["price_alerts"]:
        if alert["triggered"]:
            continue
        try:
            price_info = client.market.get_symbol_price(alert["symbol"])
            current = price_info.get("bid") if price_info else None
            if current is None:
                continue
            if alert["direction"] == "ABOVE" and current >= alert["price"]:
                alert["triggered"] = True
                triggered.append({"id": alert["id"], "symbol": alert["symbol"], "direction": "ABOVE", "price": alert["price"], "current": current, "note": alert["note"]})
            elif alert["direction"] == "BELOW" and current <= alert["price"]:
                alert["triggered"] = True
                triggered.append({"id": alert["id"], "symbol": alert["symbol"], "direction": "BELOW", "price": alert["price"], "current": current, "note": alert["note"]})
            else:
                active.append({"id": alert["id"], "symbol": alert["symbol"], "direction": alert["direction"], "price": alert["price"], "current": round(current, 5)})
        except Exception:
            continue
    return {"error": False, "message": f"{len(triggered)} triggered, {len(active)} active",
            "data": {"triggered": triggered, "active_alerts": active}}


def alert_list(client) -> Dict[str, Any]:
    return {"error": False, "message": f"{len(_alert_state['price_alerts'])} alerts",
            "data": {"alerts": _alert_state["price_alerts"]}}


def alert_clear(client, alert_id: Optional[int] = None) -> Dict[str, Any]:
    if alert_id:
        _alert_state["price_alerts"] = [a for a in _alert_state["price_alerts"] if a["id"] != alert_id]
        return {"error": False, "message": f"Alert {alert_id} cleared", "data": None}
    _alert_state["price_alerts"] = []
    return {"error": False, "message": "All alerts cleared", "data": None}


def protection_configure(client, daily_loss_limit: float = 0, max_positions: int = 0, max_drawdown_pct: float = 0, max_correlation: float = 0) -> Dict[str, Any]:
    p = _alert_state["protection"]
    if daily_loss_limit > 0:
        p["daily_loss_limit"] = daily_loss_limit
    if max_positions > 0:
        p["max_positions"] = max_positions
    if max_drawdown_pct > 0:
        p["max_drawdown"] = max_drawdown_pct
    if max_correlation > 0:
        p["max_correlation_exposure"] = max_correlation
    _check_daily_reset(client)
    return {"error": False, "message": "Protection configured", "data": p}


def protection_status(client) -> Dict[str, Any]:
    _check_daily_reset(client)
    metrics = _get_account_metrics(client)
    p = _alert_state["protection"]
    blocks = []
    if metrics:
        if p["daily_loss_limit"] and abs(p["today_pnl"]) >= p["daily_loss_limit"]:
            blocks.append("DAILY_LOSS_LIMIT")
        if p["max_positions"] and metrics["positions"] >= p["max_positions"]:
            blocks.append("MAX_POSITIONS")
        if p["max_drawdown"]:
            dd = (metrics["balance"] - metrics["equity"]) / max(metrics["balance"], 1) * 100
            if dd >= p["max_drawdown"]:
                blocks.append("MAX_DRAWDOWN")
        if p["max_correlation_exposure"]:
            pass
    return {
        "error": False,
        "message": f"Protection: {len(blocks)} blocks active" if blocks else "Protection: all clear",
        "data": {"protection": p, "account": metrics, "active_blocks": blocks, "trading_enabled": len(blocks) == 0}
    }


def protection_reset_daily(client) -> Dict[str, Any]:
    p = _alert_state["protection"]
    p["today_pnl"] = 0
    p["today_date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {"error": False, "message": "Daily counter reset", "data": None}


def market_scanner(client, timeframes: str = "H1,H4,D1", min_volume: float = 0, max_symbols: int = 20) -> Dict[str, Any]:
    tfs = [t.strip() for t in timeframes.split(",")] if timeframes else ["H1"]
    try:
        symbols = client.market.get_symbols()
    except Exception as e:
        return {"error": True, "message": str(e), "data": None}
    if not symbols:
        return {"error": True, "message": "No symbols available", "data": None}
    results = []
    for sym in symbols[:max_symbols]:
        try:
            info = client.market.get_symbol_info(sym)
            if min_volume and info:
                pass
            row = {"symbol": sym}
            for tf in tfs:
                df = client.market.get_candles_latest(symbol_name=sym, timeframe=tf, count=50)
                if df is None or len(df) < 20:
                    row[tf] = "NO_DATA"
                    continue
                df_sorted = df.sort_values('time')
                closes = df_sorted['close'].values
                ema20 = pd.Series(closes).ewm(span=20).mean().iloc[-1]
                ema50 = pd.Series(closes).ewm(span=50).mean().iloc[-1] if len(closes) >= 50 else ema20
                last = closes[-1]
                atr = (df_sorted['high'] - df_sorted['low']).mean()
                if last > ema20 > ema50:
                    trend = "BULLISH"
                elif last < ema20 < ema50:
                    trend = "BEARISH"
                elif last > ema50:
                    trend = "BULLISH_WEAK"
                elif last < ema50:
                    trend = "BEARISH_WEAK"
                else:
                    trend = "NEUTRAL"
                bb_upper = closes.mean() + 2 * closes.std()
                bb_lower = closes.mean() - 2 * closes.std()
                rsi_period = 14
                deltas = np.diff(closes)
                gains = deltas[deltas > 0].sum() if deltas[deltas > 0].sum() > 0 else 0.001
                losses = abs(deltas[deltas < 0].sum()) if deltas[deltas < 0].sum() != 0 else 0.001
                rs = gains / losses if losses > 0 else 1
                rsi = 100 - 100 / (1 + rs)
                row[f"{tf}_trend"] = trend
                row[f"{tf}_rsi"] = round(rsi, 1)
                row[f"{tf}_atr"] = round(atr, 5)
                row[f"{tf}_bb_position"] = "OVERBOUGHT" if last > bb_upper else "OVERSOLD" if last < bb_lower else "NORMAL"
            results.append(row)
        except Exception:
            continue
    bullish_count = sum(1 for r in results if any("BULLISH" in str(r.get(f"{tf}_trend", "")) for tf in tfs))
    bearish_count = sum(1 for r in results if any("BEARISH" in str(r.get(f"{tf}_trend", "")) for tf in tfs))
    scanner_results = []
    for r in results[:10]:
        entry = {"symbol": r["symbol"]}
        for tf in tfs:
            entry.update({k: v for k, v in r.items() if k.startswith(tf)})
        scanner_results.append(entry)
    return {
        "error": False,
        "message": f"{len(results)} symbols scanned: {bullish_count}B / {bearish_count}S bullish/bearish",
        "data": {
            "timeframes": tfs,
            "total_scanned": len(results),
            "bullish_count": bullish_count,
            "bearish_count": bearish_count,
            "results": scanner_results,
        }
    }


def trade_compounding_plan(client, target_profit: float = 100000, starting_balance: Optional[float] = None, trades_per_day: int = 3, avg_win_pct: float = 1.5, win_rate: float = 0.6) -> Dict[str, Any]:
    import MetaTrader5 as mt5
    acc = mt5.account_info()
    if acc is None and starting_balance is None:
        return {"error": True, "message": "No account info and no starting balance provided", "data": None}
    balance = starting_balance or (acc.balance if acc else 1000)
    kelly = (win_rate - (1 - win_rate) / (avg_win_pct / max(avg_win_pct * 0.6, 0.01))) if avg_win_pct > 0 else 0
    kelly = max(0, kelly)
    bet_pct = kelly * 0.25
    daily_growth = (1 + avg_win_pct / 100 * bet_pct * win_rate - avg_win_pct * 0.6 / 100 * bet_pct * (1 - win_rate)) ** trades_per_day - 1
    months_to_target = 0
    if daily_growth > 0:
        import math as m
        months_to_target = m.log(target_profit / balance) / m.log(1 + daily_growth) / 21 if daily_growth > 0 else 999
    projection = []
    proj_balance = balance
    for day in range(min(int(months_to_target * 21), 252)):
        proj_balance *= (1 + daily_growth)
        if day % 21 == 0:
            projection.append({"month": day // 21 + 1, "balance": round(proj_balance, 2)})
        if proj_balance >= target_profit:
            break
    return {
        "error": False,
        "message": f"${balance} → ${target_profit}: ~{months_to_target:.0f} months",
        "data": {
            "starting_balance": balance,
            "target_profit": target_profit,
            "estimated_months": round(months_to_target, 0),
            "daily_growth_pct": round(daily_growth * 100, 3),
            "kelly_bet_pct": round(bet_pct * 100, 1),
            "win_rate": win_rate,
            "avg_win_pct": avg_win_pct,
            "projection": projection[:12],
        }
    }
