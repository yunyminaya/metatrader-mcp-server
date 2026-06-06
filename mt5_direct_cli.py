#!/usr/bin/env python3
"""One-shot MT5 command runner executed inside Wine Python.

The macOS MCP process calls this through wine64. It does not receive or store
login/password; mt5.initialize() attaches to the already-open local terminal.
"""

import json
import sys
from datetime import datetime, timedelta

import MetaTrader5 as mt5


def ser(obj):
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "item"):
        return obj.item()
    if hasattr(obj, "_asdict"):
        return {k: ser(v) for k, v in obj._asdict().items()}
    if isinstance(obj, dict):
        return {k: ser(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [ser(x) for x in obj]
    return str(obj)


TF = {
    "M1": mt5.TIMEFRAME_M1,
    "M2": mt5.TIMEFRAME_M2,
    "M3": mt5.TIMEFRAME_M3,
    "M4": mt5.TIMEFRAME_M4,
    "M5": mt5.TIMEFRAME_M5,
    "M6": mt5.TIMEFRAME_M6,
    "M10": mt5.TIMEFRAME_M10,
    "M12": mt5.TIMEFRAME_M12,
    "M15": mt5.TIMEFRAME_M15,
    "M20": mt5.TIMEFRAME_M20,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H2": mt5.TIMEFRAME_H2,
    "H3": mt5.TIMEFRAME_H3,
    "H4": mt5.TIMEFRAME_H4,
    "H6": mt5.TIMEFRAME_H6,
    "H8": mt5.TIMEFRAME_H8,
    "H12": mt5.TIMEFRAME_H12,
    "D1": mt5.TIMEFRAME_D1,
    "W1": mt5.TIMEFRAME_W1,
    "MN1": mt5.TIMEFRAME_MN1,
}


DEFAULT_SCAN_SYMBOLS = [
    "EURUSD.FX",
    "AUDUSD.FX",
    "USDCAD.FX",
    "USDCHF.FX",
    "AUDCAD.FX",
    "NZDCAD.FX",
    "CNHJPY.FX",
    "NOKSEK.FX",
]


def account():
    info = mt5.account_info()
    if info is None:
        return {"error": "account_info unavailable", "last_error": ser(mt5.last_error())}
    data = ser(info)
    login = str(data.pop("login", ""))
    data["login_masked"] = "***" + login[-4:] if login else None
    data.pop("name", None)
    return data


def price(cmd):
    symbol = cmd["symbol"]
    mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if tick is None:
        return {"error": f"no tick for {symbol}", "last_error": ser(mt5.last_error())}
    data = ser(tick)
    if info:
        data["digits"] = info.digits
        data["point"] = info.point
        data["spread"] = info.spread
    data["symbol"] = symbol
    return data


def symbols(cmd):
    group = cmd.get("pattern")
    vals = mt5.symbols_get(group) if group else mt5.symbols_get()
    names = [s.name for s in vals] if vals else []
    return {"count": len(names), "symbols": names}


def symbol_info(cmd):
    symbol = cmd["symbol"]
    mt5.symbol_select(symbol, True)
    info = mt5.symbol_info(symbol)
    if info is None:
        return {"error": f"no symbol info for {symbol}", "last_error": ser(mt5.last_error())}
    data = ser(info)
    return {
        "symbol": symbol,
        "digits": data.get("digits"),
        "point": data.get("point"),
        "spread": data.get("spread"),
        "volume_min": data.get("volume_min"),
        "volume_max": data.get("volume_max"),
        "volume_step": data.get("volume_step"),
        "trade_contract_size": data.get("trade_contract_size"),
        "trade_tick_size": data.get("trade_tick_size"),
        "trade_tick_value": data.get("trade_tick_value"),
        "trade_stops_level": data.get("trade_stops_level"),
        "trade_freeze_level": data.get("trade_freeze_level"),
        "trade_mode": data.get("trade_mode"),
        "visible": data.get("visible"),
        "select": data.get("select"),
    }


def symbols_info(cmd):
    group = cmd.get("pattern")
    vals = mt5.symbols_get(group) if group else mt5.symbols_get()
    rows = []
    for info in vals or []:
        rows.append({
            "symbol": info.name,
            "digits": int(info.digits),
            "spread": int(info.spread),
            "volume_min": float(info.volume_min),
            "volume_step": float(info.volume_step),
            "volume_max": float(info.volume_max),
            "trade_contract_size": float(info.trade_contract_size),
            "trade_tick_size": float(info.trade_tick_size),
            "trade_tick_value": float(info.trade_tick_value),
            "trade_stops_level": int(info.trade_stops_level),
            "trade_mode": int(info.trade_mode),
        })
    return {"count": len(rows), "symbols": rows}


def candles(cmd):
    symbol = cmd["symbol"]
    timeframe = cmd.get("timeframe", "M1").upper()
    count = int(cmd.get("count", 100))
    count = max(1, min(1000, count))
    mt5.symbol_select(symbol, True)
    rates = mt5.copy_rates_from_pos(symbol, TF.get(timeframe, mt5.TIMEFRAME_M1), 0, count)
    if rates is None:
        return {"error": f"no rates for {symbol}", "last_error": ser(mt5.last_error())}
    rows = []
    for row in rates:
        rows.append({
            "time": int(row["time"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "tick_volume": int(row["tick_volume"]),
            "spread": int(row["spread"]),
            "real_volume": int(row["real_volume"]),
        })
    return {"symbol": symbol, "timeframe": timeframe, "count": len(rows), "candles": rows}


def positions(cmd):
    kwargs = {}
    if cmd.get("symbol"):
        kwargs["symbol"] = cmd["symbol"]
    if cmd.get("ticket"):
        kwargs["ticket"] = int(cmd["ticket"])
    vals = mt5.positions_get(**kwargs) if kwargs else mt5.positions_get()
    rows = ser(vals) if vals else []
    return {"count": len(rows), "positions": rows, "total_profit": sum(float(p.get("profit", 0)) for p in rows)}


def orders(cmd):
    vals = mt5.orders_get()
    rows = ser(vals) if vals else []
    return {"count": len(rows), "orders": rows}


def history(cmd):
    days = int(cmd.get("days", 30))
    symbol = cmd.get("symbol")
    end = datetime.now()
    start = end - timedelta(days=days)
    vals = mt5.history_deals_get(start, end, group=symbol) if symbol else mt5.history_deals_get(start, end)
    rows = ser(vals) if vals else []
    return {"count": len(rows), "deals": rows}


def order_request(cmd):
    symbol = cmd["symbol"]
    typ = cmd["type"].upper()
    volume = float(cmd.get("volume", 0.01))
    sl = float(cmd.get("stop_loss", 0.0) or 0.0)
    tp = float(cmd.get("take_profit", 0.0) or 0.0)
    mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        raise RuntimeError(f"no tick for {symbol}")
    order_type = mt5.ORDER_TYPE_BUY if typ == "BUY" else mt5.ORDER_TYPE_SELL
    return {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": tick.ask if typ == "BUY" else tick.bid,
        "sl": sl,
        "tp": tp,
        "deviation": int(cmd.get("deviation", 10)),
        "magic": int(cmd.get("magic", 20260605)),
        "comment": str(cmd.get("comment", "mcp"))[:31],
        "type_time": mt5.ORDER_TIME_GTC,
    }


def check_order(cmd):
    req = order_request(cmd)
    results = []
    for filling in [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]:
        req["type_filling"] = filling
        res = mt5.order_check(req)
        data = ser(res)
        data["filling"] = filling
        results.append(data)
        if res and res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            break
    return {"request": ser(req), "result": results[-1], "attempts": results}


def _candles_rows(symbol, timeframe, count):
    rates = mt5.copy_rates_from_pos(symbol, TF.get(timeframe, mt5.TIMEFRAME_M1), 0, count)
    if rates is None or len(rates) < 3:
        return []
    return [
        {
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "tick_volume": int(row["tick_volume"]),
            "spread": int(row["spread"]),
        }
        for row in rates
    ]


def _pip_size(symbol, info):
    if "JPY" in symbol.upper() or info.digits in (2, 3):
        return 0.01
    if info.digits in (4, 5):
        return 0.0001
    return info.point * 10.0


def _movement_pips(rows, pip):
    if len(rows) < 2 or pip <= 0:
        return 0.0
    return (rows[-1]["close"] - rows[-2]["close"]) / pip


def _risk_usd(info, volume, sl_points):
    tick_size = float(getattr(info, "trade_tick_size", 0.0) or 0.0)
    tick_value = float(getattr(info, "trade_tick_value", 0.0) or 0.0)
    point = float(getattr(info, "point", 0.0) or 0.0)
    if tick_size <= 0 or tick_value <= 0 or point <= 0:
        return None
    return float(sl_points) * (point / tick_size) * tick_value * float(volume)


def _normalize_volume(info, requested_volume, auto_min_volume):
    vol_min = float(getattr(info, "volume_min", 0.01) or 0.01)
    vol_max = float(getattr(info, "volume_max", requested_volume) or requested_volume)
    vol_step = float(getattr(info, "volume_step", vol_min) or vol_min)
    volume = vol_min if auto_min_volume else float(requested_volume)
    volume = max(vol_min, min(volume, vol_max))
    if vol_step > 0:
        steps = round((volume - vol_min) / vol_step)
        volume = vol_min + steps * vol_step
    return round(volume, 8)


def _order_check_any(req):
    attempts = []
    for filling in [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]:
        req["type_filling"] = filling
        res = mt5.order_check(req)
        data = ser(res)
        data["filling"] = filling
        attempts.append(data)
        if res and res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            break
    return attempts[-1] if attempts else {}, attempts


def scan_strategy(cmd):
    """One-process guarded scanner for small-account MCP trading decisions."""
    symbols = cmd.get("symbols") or DEFAULT_SCAN_SYMBOLS
    requested_volume = float(cmd.get("volume", 0.01))
    auto_min_volume = bool(cmd.get("auto_min_volume", False))
    max_volume = float(cmd.get("max_volume", 0.01))
    max_spread_points = int(cmd.get("max_spread_points", 25))
    max_margin_use_pct = float(cmd.get("max_margin_use_pct", 35.0))
    min_post_free_margin_pct = float(cmd.get("min_post_trade_free_margin_pct", 55.0))
    max_risk_usd = float(cmd.get("max_risk_usd", 0.22))
    max_positions = int(cmd.get("max_positions", 1))
    min_score = float(cmd.get("min_score", 70.0))

    acct = mt5.account_info()
    if acct is None:
        return {"error": "account_info unavailable", "last_error": ser(mt5.last_error())}
    acct_data = account()
    free = float(getattr(acct, "margin_free", 0.0) or 0.0)
    equity = float(getattr(acct, "equity", 0.0) or 0.0)
    pos = mt5.positions_get() or []

    rows = []
    candidates = []
    for symbol in symbols:
        row = {"symbol": symbol, "candidate": False, "reasons": []}
        try:
            if len(pos) >= max_positions:
                row["reasons"].append(f"open_positions {len(pos)} >= limit {max_positions}")
            if not mt5.symbol_select(symbol, True):
                row["reasons"].append("symbol_select failed")
                rows.append(row)
                continue

            info = mt5.symbol_info(symbol)
            tick = mt5.symbol_info_tick(symbol)
            if info is None or tick is None:
                row["reasons"].append("missing symbol info or tick")
                rows.append(row)
                continue

            point = float(info.point or 0.0)
            pip = _pip_size(symbol, info)
            volume = _normalize_volume(info, requested_volume, auto_min_volume)
            spread_points = int(getattr(info, "spread", 0) or 0)
            row.update({
                "bid": float(tick.bid),
                "ask": float(tick.ask),
                "spread_points": spread_points,
                "digits": int(info.digits),
                "point": point,
                "volume": volume,
                "volume_min": float(getattr(info, "volume_min", 0.0) or 0.0),
                "volume_step": float(getattr(info, "volume_step", 0.0) or 0.0),
            })
            if volume > max_volume:
                row["reasons"].append(f"volume {volume:.3f} > max_volume {max_volume:.3f}")
            if spread_points > max_spread_points:
                row["reasons"].append(f"spread {spread_points} > limit {max_spread_points}")

            m1 = _candles_rows(symbol, "M1", 6)
            m5 = _candles_rows(symbol, "M5", 6)
            m15 = _candles_rows(symbol, "M15", 6)
            if not m1 or not m5 or not m15:
                row["reasons"].append("not enough candles")
                rows.append(row)
                continue

            m1_move = _movement_pips(m1, pip)
            m5_move = _movement_pips(m5, pip)
            m15_move = _movement_pips(m15, pip)
            prev_m1 = (m1[-2]["close"] - m1[-3]["close"]) / pip if len(m1) >= 3 else 0.0
            row.update({
                "m1_pips": round(m1_move, 3),
                "m5_pips": round(m5_move, 3),
                "m15_pips": round(m15_move, 3),
                "prev_m1_pips": round(prev_m1, 3),
            })

            direction = None
            if m1_move > 0 and m5_move >= 0 and m15_move >= 0:
                direction = "BUY"
            elif m1_move < 0 and m5_move <= 0 and m15_move <= 0:
                direction = "SELL"
            if direction is None:
                row["reasons"].append("timeframes not aligned")
                rows.append(row)
                continue
            if direction == "BUY" and prev_m1 < -0.5:
                row["reasons"].append("prior M1 contradicts BUY")
            if direction == "SELL" and prev_m1 > 0.5:
                row["reasons"].append("prior M1 contradicts SELL")

            abs_moves = abs(m1_move) + 0.7 * abs(m5_move) + 0.5 * abs(m15_move)
            score = min(100.0, 45.0 + abs_moves * 9.0)
            if not row["reasons"]:
                score += 5.0
            row["direction"] = direction
            row["score"] = round(score, 2)
            if score < min_score:
                row["reasons"].append(f"score {score:.1f} < {min_score:.1f}")

            stop_level = int(getattr(info, "trade_stops_level", 0) or 0)
            sl_points = int(max(stop_level + spread_points + 8, spread_points * 3 + 20, 45))
            tp_points = int(sl_points * float(cmd.get("reward_risk", 1.4)))
            risk = _risk_usd(info, volume, sl_points)
            row.update({
                "sl_points": sl_points,
                "tp_points": tp_points,
                "estimated_sl_risk_usd": round(risk, 4) if risk is not None else None,
            })
            if risk is not None and risk > max_risk_usd:
                row["reasons"].append(f"estimated SL risk ${risk:.2f} > limit ${max_risk_usd:.2f}")

            if direction == "BUY":
                price = float(tick.ask)
                sl = round(price - sl_points * point, info.digits)
                tp = round(price + tp_points * point, info.digits)
                order_type = mt5.ORDER_TYPE_BUY
            else:
                price = float(tick.bid)
                sl = round(price + sl_points * point, info.digits)
                tp = round(price - tp_points * point, info.digits)
                order_type = mt5.ORDER_TYPE_SELL
            row.update({"entry": round(price, info.digits), "stop_loss": sl, "take_profit": tp})

            req = {
                "action": mt5.TRADE_ACTION_DEAL,
                "symbol": symbol,
                "volume": volume,
                "type": order_type,
                "price": price,
                "sl": sl,
                "tp": tp,
                "deviation": int(cmd.get("deviation", 10)),
                "magic": int(cmd.get("magic", 20260605)),
                "comment": "mcp_scan",
                "type_time": mt5.ORDER_TIME_GTC,
            }
            check, attempts = _order_check_any(req)
            retcode = int(check.get("retcode", 0) or 0)
            margin = float(check.get("margin", 0.0) or 0.0)
            margin_use_pct = (margin / free * 100.0) if free > 0 else 999.0
            post_free_pct = ((free - margin) / equity * 100.0) if equity > 0 else 0.0
            row.update({
                "order_check_retcode": retcode,
                "order_check_comment": check.get("comment"),
                "margin": round(margin, 4),
                "margin_use_pct": round(margin_use_pct, 2),
                "post_trade_free_margin_pct": round(post_free_pct, 2),
                "order_check_attempts": attempts,
            })
            if retcode not in (0, 10009):
                row["reasons"].append(f"OrderCheck retcode={retcode}: {check.get('comment')}")
            if margin_use_pct > max_margin_use_pct:
                row["reasons"].append(f"margin use {margin_use_pct:.1f}% > limit {max_margin_use_pct:.1f}%")
            if post_free_pct < min_post_free_margin_pct:
                row["reasons"].append(f"post-trade free margin {post_free_pct:.1f}% < limit {min_post_free_margin_pct:.1f}%")

            row["candidate"] = not row["reasons"]
            if row["candidate"]:
                candidates.append(row)
            rows.append(row)
        except Exception as exc:
            row["reasons"].append(str(exc))
            rows.append(row)

    candidates.sort(key=lambda r: (r.get("score", 0), -r.get("spread_points", 999)), reverse=True)
    return {
        "account": acct_data,
        "positions_count": len(pos),
        "limits": {
            "volume": requested_volume,
            "auto_min_volume": auto_min_volume,
            "max_volume": max_volume,
            "max_positions": max_positions,
            "max_spread_points": max_spread_points,
            "max_margin_use_pct": max_margin_use_pct,
            "min_post_trade_free_margin_pct": min_post_free_margin_pct,
            "max_risk_usd": max_risk_usd,
            "min_score": min_score,
        },
        "candidate_count": len(candidates),
        "candidates": candidates[: int(cmd.get("limit", 5))],
        "scanned": rows,
    }


def send_order(cmd):
    req = order_request(cmd)
    attempts = []
    for filling in [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]:
        req["type_filling"] = filling
        res = mt5.order_send(req)
        data = ser(res)
        data["filling"] = filling
        attempts.append(data)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            return {"success": True, "request": ser(req), "result": data, "attempts": attempts}
        if res and res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            break
    return {"success": False, "request": ser(req), "result": attempts[-1] if attempts else None, "attempts": attempts}


def pending_type(mt5_order_type, price, tick):
    if mt5_order_type == "BUY":
        return mt5.ORDER_TYPE_BUY_LIMIT if price < tick.ask else mt5.ORDER_TYPE_BUY_STOP
    return mt5.ORDER_TYPE_SELL_LIMIT if price > tick.bid else mt5.ORDER_TYPE_SELL_STOP


def send_pending_order(cmd):
    symbol = cmd["symbol"]
    typ = cmd["type"].upper()
    volume = float(cmd.get("volume", 0.01))
    order_price = float(cmd["price"])
    sl = float(cmd.get("stop_loss", 0.0) or 0.0)
    tp = float(cmd.get("take_profit", 0.0) or 0.0)
    mt5.symbol_select(symbol, True)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return {"success": False, "error": f"no tick for {symbol}"}
    req = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": volume,
        "type": pending_type(typ, order_price, tick),
        "price": order_price,
        "sl": sl,
        "tp": tp,
        "deviation": int(cmd.get("deviation", 10)),
        "magic": int(cmd.get("magic", 20260605)),
        "comment": str(cmd.get("comment", "mcp_pending"))[:31],
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN,
    }
    res = mt5.order_send(req)
    return {"success": bool(res and res.retcode == mt5.TRADE_RETCODE_DONE), "request": ser(req), "result": ser(res)}


def cancel_pending_order(cmd):
    ticket = int(cmd["ticket"])
    req = {"action": mt5.TRADE_ACTION_REMOVE, "order": ticket}
    res = mt5.order_send(req)
    return {"success": bool(res and res.retcode == mt5.TRADE_RETCODE_DONE), "request": req, "result": ser(res)}


def modify_position(cmd):
    ticket = int(cmd["ticket"])
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        return {"success": False, "error": f"position not found: {ticket}"}
    current = pos[0]
    req = {
        "action": mt5.TRADE_ACTION_SLTP,
        "position": ticket,
        "symbol": current.symbol,
        "sl": float(cmd.get("stop_loss", current.sl) if cmd.get("stop_loss", "") != "" else current.sl),
        "tp": float(cmd.get("take_profit", current.tp) if cmd.get("take_profit", "") != "" else current.tp),
    }
    res = mt5.order_send(req)
    return {"success": bool(res and res.retcode == mt5.TRADE_RETCODE_DONE), "request": req, "result": ser(res)}


def close_position(cmd):
    ticket = int(cmd["ticket"])
    pos = mt5.positions_get(ticket=ticket)
    if not pos:
        return {"success": False, "error": f"position not found: {ticket}"}
    p = pos[0]
    tick = mt5.symbol_info_tick(p.symbol)
    close_type = mt5.ORDER_TYPE_SELL if p.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "position": ticket,
        "symbol": p.symbol,
        "volume": p.volume,
        "type": close_type,
        "price": tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask,
        "deviation": int(cmd.get("deviation", 10)),
        "magic": int(cmd.get("magic", 20260605)),
        "comment": "mcp_close",
        "type_time": mt5.ORDER_TIME_GTC,
    }
    attempts = []
    for filling in [mt5.ORDER_FILLING_FOK, mt5.ORDER_FILLING_IOC, mt5.ORDER_FILLING_RETURN]:
        req["type_filling"] = filling
        res = mt5.order_send(req)
        data = ser(res)
        data["filling"] = filling
        attempts.append(data)
        if res and res.retcode == mt5.TRADE_RETCODE_DONE:
            return {"success": True, "request": ser(req), "result": data, "attempts": attempts}
        if res and res.retcode != mt5.TRADE_RETCODE_INVALID_FILL:
            break
    return {"success": False, "request": ser(req), "result": attempts[-1] if attempts else None, "attempts": attempts}


def close_all(cmd):
    mode = cmd.get("mode", "all")
    vals = mt5.positions_get()
    results = []
    for p in vals or []:
        if mode == "profitable" and p.profit <= 0:
            continue
        if mode == "losing" and p.profit >= 0:
            continue
        results.append(close_position({"ticket": p.ticket}))
    return {"mode": mode, "closed_attempts": len(results), "results": results}


def handle(cmd):
    action = cmd.get("action")
    if action == "account": return account()
    if action == "price": return price(cmd)
    if action == "symbols": return symbols(cmd)
    if action == "symbol_info": return symbol_info(cmd)
    if action == "symbols_info": return symbols_info(cmd)
    if action == "candles": return candles(cmd)
    if action == "positions": return positions(cmd)
    if action == "orders": return orders(cmd)
    if action == "history": return history(cmd)
    if action == "check_order": return check_order(cmd)
    if action == "scan_strategy": return scan_strategy(cmd)
    if action == "send_order": return send_order(cmd)
    if action == "send_pending_order": return send_pending_order(cmd)
    if action == "cancel_pending_order": return cancel_pending_order(cmd)
    if action == "modify_position": return modify_position(cmd)
    if action == "close_position": return close_position(cmd)
    if action == "close_all": return close_all(cmd)
    return {"error": f"unknown action: {action}"}


def main():
    cmd = json.loads(sys.argv[1])
    if not mt5.initialize():
        print(json.dumps({"error": "mt5.initialize failed", "last_error": ser(mt5.last_error())}))
        return
    try:
        print(json.dumps(handle(cmd), ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"error": str(exc), "last_error": ser(mt5.last_error())}, ensure_ascii=False))
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
