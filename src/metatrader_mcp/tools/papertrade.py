"""
Papertrade — simulación de trading con PnL para MT5.

Persiste a data/papertrade.json.
Las órdenes se simulan (no se envían a MT5).
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "papertrade.json")

_portfolio: Dict[str, Any] = {}


def _ensure():
    global _portfolio
    if not _portfolio:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE) as f:
                    _portfolio = json.load(f)
        except Exception:
            _portfolio = {"balance": 10000, "positions": [], "trades": [], "total_trades": 0}


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_portfolio, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def _next_id():
    _portfolio["total_trades"] = _portfolio.get("total_trades", 0) + 1
    return _portfolio["total_trades"]


def open_order(client, symbol: str, order_type: str, volume: float = 0.01,
               entry_price: float = 0, stop_loss: float = 0, take_profit: float = 0,
               reason: str = "", features: dict = None) -> Dict[str, Any]:
    _ensure()

    if not entry_price:
        try:
            price_data = client.market.get_symbol_price(symbol_name=symbol)
            if price_data:
                if order_type.upper() == "BUY":
                    entry_price = price_data.get("ask", 0)
                else:
                    entry_price = price_data.get("bid", 0)
        except Exception:
            entry_price = 1.0

    if entry_price <= 0:
        return {"success": False, "error": "Invalid price"}

    margin = volume * 100000 / max(entry_price, 0.0001)
    if _portfolio["balance"] < margin:
        return {"success": False, "error": f"Insufficient balance (need {margin:.2f}, have {_portfolio['balance']:.2f})"}

    now = datetime.now(timezone.utc).isoformat()
    pos = {
        "id": _next_id(),
        "symbol": symbol,
        "type": order_type.upper(),
        "volume": volume,
        "entry_price": round(entry_price, 5),
        "stop_loss": round(stop_loss, 5) if stop_loss else 0,
        "take_profit": round(take_profit, 5) if take_profit else 0,
        "reason": reason,
        "status": "open",
        "opened_at": now,
        "features": features if features else {},
    }
    _portfolio.setdefault("positions", []).append(pos)
    _portfolio["balance"] -= margin
    _save()
    return {"success": True, "position": pos, "margin_used": round(margin, 2), "remaining_balance": round(_portfolio["balance"], 2)}


def close_order(client, position_id: int, exit_price: float = 0) -> Dict[str, Any]:
    _ensure()
    for pos in _portfolio.get("positions", []):
        if pos.get("id") == position_id and pos.get("status") == "open":
            if not exit_price:
                try:
                    price_data = client.market.get_symbol_price(symbol_name=pos["symbol"])
                    if price_data:
                        if pos["type"] == "BUY":
                            exit_price = price_data.get("bid", pos["entry_price"])
                        else:
                            exit_price = price_data.get("ask", pos["entry_price"])
                except Exception:
                    exit_price = pos["entry_price"]

            direction = 1 if pos["type"] == "BUY" else -1
            pnl = direction * (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
            pnl_usd = pnl * pos["volume"] * 1000 / 100

            pos["status"] = "closed"
            pos["exit_price"] = round(exit_price, 5)
            pos["pnl_pct"] = round(pnl, 2)
            pos["pnl_usd"] = round(pnl_usd, 2)
            pos["closed_at"] = datetime.now(timezone.utc).isoformat()

            margin = pos["volume"] * 100000 / max(pos["entry_price"], 0.0001)
            _portfolio["balance"] += margin + pnl_usd * 10
            _portfolio.setdefault("trades", []).append({
                "id": pos["id"],
                "symbol": pos["symbol"],
                "type": pos["type"],
                "entry": pos["entry_price"],
                "exit": exit_price,
                "pnl_pct": round(pnl, 2),
                "pnl_usd": round(pnl_usd, 2),
                "opened": pos["opened_at"],
                "closed": pos["closed_at"],
            })

            _portfolio["positions"] = [p for p in _portfolio["positions"] if p.get("id") != position_id or p.get("status") != "open"]
            _save()

            # Auto-fund insurance (5% of profit)
            try:
                from .insurance import auto_fund
                auto_fund(pnl_usd)
            except Exception:
                pass

            # Collect ML training sample
            try:
                if pos.get("features"):
                    from .predictor import collect_training_sample
                    collect_training_sample(pos["features"], pnl_usd > 0)
            except Exception:
                pass

            # Record trade in emergency (track consecutive losses)
            try:
                from .emergency import record_trade
                record_trade(pnl_usd, _portfolio["balance"])
            except Exception:
                pass

            return {"success": True, "pnl_pct": round(pnl, 2), "pnl_usd": round(pnl_usd, 2), "balance": round(_portfolio["balance"], 2)}

    return {"success": False, "error": f"Position {position_id} not found"}


def portfolio() -> Dict[str, Any]:
    _ensure()
    positions = [p for p in _portfolio.get("positions", []) if p.get("status") == "open"]
    trades = _portfolio.get("trades", [])
    wins = sum(1 for t in trades if t.get("pnl_usd", 0) > 0)
    loss = sum(1 for t in trades if t.get("pnl_usd", 0) <= 0)
    total_pnl = sum(t.get("pnl_usd", 0) for t in trades)
    balance = _portfolio.get("balance", 0)

    return {
        "success": True,
        "portfolio": {
            "balance": round(balance, 2),
            "open_positions": len(positions),
            "total_trades": len(trades),
            "wins": wins,
            "losses": loss,
            "win_rate_pct": round(wins / max(len(trades), 1) * 100, 1),
            "net_pnl_usd": round(total_pnl, 2),
            "positions": positions,
            "recent_trades": trades[-10:],
        },
    }


def reset(balance: float = 10000) -> Dict[str, Any]:
    global _portfolio
    _portfolio = {"balance": balance, "positions": [], "trades": [], "total_trades": 0}
    _save()
    return {"success": True, "starting_balance": balance}
