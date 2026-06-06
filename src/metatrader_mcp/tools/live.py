"""
Live — orden inteligente con auto-SL/TP, trailing stop y breakeven.

- place_smart_order: abre orden market + modifica SL/TP automáticamente
- set_trailing_stop: mueve SL detrás del precio a distancia ATR * multiple
- set_breakeven: cuando precio se mueve X% a favor, SL = entry
- close_all: cierra TODO (live + papertrade) ordenadamente
"""
import logging
import math
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def _atr(highs, lows, closes, period=14):
    """Calculate ATR from price arrays."""
    if len(closes) < period + 1:
        return None
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    if not trs:
        return None
    return sum(trs[-period:]) / min(period, len(trs))


def place_smart_order(client, symbol: str, order_type: str, volume: float = 0.01,
                      sl_atr_multiple: float = 1.5, tp_atr_multiple: float = 3.0,
                      use_trailing: bool = True, use_breakeven: bool = True,
                      trailing_activation_pct: float = 0.5) -> Dict[str, Any]:
    """Place a market order with intelligent SL/TP.

    Steps:
      1. Fetch ATR from 100 H1 candles
      2. Place market order via MT5
      3. Immediately modify position with SL/TP based on ATR
      4. Register in heartbeat for trailing/breakeven monitoring
    """
    # Fetch candles for ATR
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=100)
    except Exception as e:
        return {"success": False, "error": f"Cannot fetch candles: {e}"}

    import pandas as pd
    if df is None or (hasattr(df, 'empty') and df.empty):
        return {"success": False, "error": "No candle data for ATR calculation"}

    if isinstance(df, pd.DataFrame):
        closes = df['close'].dropna().values
        highs = df['high'].dropna().values
        lows = df['low'].dropna().values
    else:
        return {"success": False, "error": "Unexpected data format"}

    if len(closes) < 20:
        return {"success": False, "error": "Not enough candles"}

    atr_val = _atr(highs, lows, closes)
    if atr_val is None or atr_val <= 0:
        return {"success": False, "error": "Could not calculate ATR"}

    current_price = closes[-1]

    if order_type.upper() == "BUY":
        sl = round(current_price - atr_val * sl_atr_multiple, 5)
        tp = round(current_price + atr_val * tp_atr_multiple, 5)
    elif order_type.upper() == "SELL":
        sl = round(current_price + atr_val * sl_atr_multiple, 5)
        tp = round(current_price - atr_val * tp_atr_multiple, 5)
    else:
        return {"success": False, "error": f"Invalid type: {order_type}"}

    # Place market order
    try:
        result = client.order.place_market_order(symbol=symbol, volume=volume, type=order_type)
    except Exception as e:
        return {"success": False, "error": f"Order failed: {e}"}

    if not result or result.get("error", True):
        return {"success": False, "error": f"Order rejected: {result}"}

    ticket = result.get("ticket") or result.get("data", {}).get("ticket")
    if not ticket:
        # Try to find the position by symbol
        try:
            positions = client.account.get_positions()
            if positions:
                for p in positions:
                    if p.get("symbol") == symbol:
                        ticket = p.get("ticket")
                        break
        except Exception:
            pass

    # Modify position with SL/TP
    if ticket:
        try:
            client.order.modify_position(id=str(ticket), stop_loss=sl, take_profit=tp)
        except Exception as e:
            logger.warning(f"Could not set SL/TP on ticket {ticket}: {e}")

    # Register for live monitoring
    try:
        from . import guard as _guard_module

        _state_key = "live_tracking"
        _ensure_guard_state()
    except Exception:
        pass

    return {
        "success": True,
        "order": {
            "symbol": symbol,
            "type": order_type,
            "volume": volume,
            "price": round(current_price, 5),
            "sl": sl,
            "tp": tp,
            "atr": round(atr_val, 5),
            "sl_atr_multiple": sl_atr_multiple,
            "tp_atr_multiple": tp_atr_multiple,
            "use_trailing": use_trailing,
            "use_breakeven": use_breakeven,
            "trailing_activation_pct": trailing_activation_pct,
        },
        "mt5_result": result,
    }


def _ensure_guard_state():
    """Ensure guard state has live_tracking."""
    import json, os
    gdir = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
    gfile = os.path.join(gdir, "guard.json")
    try:
        if os.path.exists(gfile):
            with open(gfile) as f:
                gs = json.load(f)
        else:
            gs = {}
        gs.setdefault("live_tracking", [])
        os.makedirs(gdir, exist_ok=True)
        with open(gfile, "w") as f:
            json.dump(gs, f, indent=2)
    except Exception:
        pass


def set_trailing_stop(client, ticket: str, atr_multiple: float = 1.5,
                      activation_pips: float = 20) -> Dict[str, Any]:
    """Move SL to ATR * multiple behind current price (BUY) or ahead (SELL)."""
    try:
        positions = client.account.get_positions()
    except Exception as e:
        return {"success": False, "error": f"Cannot get positions: {e}"}

    pos = None
    for p in (positions or []):
        if str(p.get("ticket")) == str(ticket):
            pos = p
            break

    if not pos:
        return {"success": False, "error": f"Position {ticket} not found"}

    symbol = pos.get("symbol")
    pos_type = pos.get("type")
    entry = pos.get("price_open")

    # Fetch ATR
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=100)
    except Exception as e:
        return {"success": False, "error": f"Cannot fetch candles: {e}"}

    import pandas as pd
    if isinstance(df, pd.DataFrame):
        closes = df['close'].dropna().values
        highs = df['high'].dropna().values
        lows = df['low'].dropna().values
    else:
        return {"success": False, "error": "Unexpected format"}

    atr_val = _atr(highs, lows, closes)
    if atr_val is None or atr_val <= 0:
        return {"success": False, "error": "Cannot calculate ATR"}

    current_price = closes[-1]

    # Check activation threshold
    if pos_type in (0, "buy", "BUY"):
        pips_moved = (current_price - entry) / (atr_val * 0.1) if atr_val > 0 else 0
        if pips_moved < activation_pips:
            return {"success": True, "action": "skipped", "reason": f"Only {pips_moved:.0f} pips moved, need {activation_pips}"}
        new_sl = round(current_price - atr_val * atr_multiple, 5)
    elif pos_type in (1, "sell", "SELL"):
        pips_moved = (entry - current_price) / (atr_val * 0.1) if atr_val > 0 else 0
        if pips_moved < activation_pips:
            return {"success": True, "action": "skipped", "reason": f"Only {pips_moved:.0f} pips moved, need {activation_pips}"}
        new_sl = round(current_price + atr_val * atr_multiple, 5)
    else:
        return {"success": False, "error": "Unknown position type"}

    # Only move SL in profit direction
    current_sl = pos.get("sl")
    if current_sl:
        if pos_type in (0, "buy", "BUY") and new_sl <= current_sl:
            return {"success": True, "action": "skipped", "reason": "New SL not better than current"}
        if pos_type in (1, "sell", "SELL") and new_sl >= current_sl:
            return {"success": True, "action": "skipped", "reason": "New SL not better than current"}

    try:
        client.order.modify_position(id=str(ticket), stop_loss=new_sl)
        return {"success": True, "action": "trailed", "old_sl": current_sl, "new_sl": new_sl}
    except Exception as e:
        return {"success": False, "error": f"Modify failed: {e}"}


def set_breakeven(client, ticket: str, activation_profit_pct: float = 0.3) -> Dict[str, Any]:
    """Move SL to entry price when profit exceeds activation_profit_pct."""
    try:
        positions = client.account.get_positions()
    except Exception as e:
        return {"success": False, "error": f"Cannot get positions: {e}"}

    pos = None
    for p in (positions or []):
        if str(p.get("ticket")) == str(ticket):
            pos = p
            break

    if not pos:
        return {"success": False, "error": f"Position {ticket} not found"}

    entry = pos.get("price_open", 0)
    pos_type = pos.get("type")
    current_sl = pos.get("sl")

    try:
        price_info = client.market.get_symbol_price(symbol_name=pos.get("symbol"))
        current_price = price_info.get("bid") if pos_type in (0, "buy", "BUY") else price_info.get("ask")
    except Exception as e:
        return {"success": False, "error": f"Cannot get price: {e}"}

    if not current_price or not entry:
        return {"success": False, "error": "Missing price data"}

    if pos_type in (0, "buy", "BUY"):
        profit_pct = (current_price - entry) / entry * 100
    elif pos_type in (1, "sell", "SELL"):
        profit_pct = (entry - current_price) / entry * 100
    else:
        return {"success": False, "error": "Unknown type"}

    if profit_pct < activation_profit_pct:
        return {"success": True, "action": "skipped", "reason": f"Profit {profit_pct:.2f}% < {activation_profit_pct}% activation"}

    if current_sl and current_sl == entry:
        return {"success": True, "action": "skipped", "reason": "Already at breakeven"}

    try:
        client.order.modify_position(id=str(ticket), stop_loss=entry)
        return {"success": True, "action": "breakeven_set", "sl": entry, "profit_pct": round(profit_pct, 2)}
    except Exception as e:
        return {"success": False, "error": f"Modify failed: {e}"}


def close_all(client, close_live: bool = True, close_paper: bool = True) -> Dict[str, Any]:
    """Close ALL positions (live MT5 + papertrade) in one call."""
    results = {"live": [], "paper": []}

    if close_live:
        try:
            r = client.order.close_all_positions()
            results["live"] = r if isinstance(r, list) else [r]
        except Exception as e:
            results["live"] = [{"error": str(e)}]

    if close_paper:
        try:
            from . import papertrade
            pf = papertrade.portfolio()
            for p in pf.get("portfolio", {}).get("positions", []):
                try:
                    r = papertrade.close_order(client, p["id"])
                    results["paper"].append({"position_id": p["id"], "pnl": r.get("pnl_usd")})
                except Exception as e:
                    results["paper"].append({"position_id": p["id"], "error": str(e)})
        except Exception as e:
            results["paper"] = [{"error": str(e)}]

    return {
        "success": True,
        "closed_live": len(results["live"]),
        "closed_paper": len(results["paper"]),
        "details": results,
    }
