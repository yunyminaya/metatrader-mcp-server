"""
Insurance — fondo de seguro que se auto-financia con 5% de cada profit.

Persiste a data/insurance.json. Usado por scheduler y emergency
como colchón para absorber pérdidas sin tocar el bankroll principal.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "insurance.json")

_fund: Dict[str, Any] = {}


def _ensure():
    global _fund
    if not _fund:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE) as f:
                    _fund = json.load(f)
        except Exception:
            _fund = {"balance": 0, "total_deposited": 0, "total_withdrawn": 0, "transactions": []}


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_fund, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def deposit(amount: float, source: str = "profit") -> Dict[str, Any]:
    """Deposit amount into insurance fund (5% of profit)."""
    _ensure()
    _fund["balance"] += amount
    _fund["total_deposited"] += amount
    _fund.setdefault("transactions", []).append({
        "type": "deposit",
        "amount": round(amount, 2),
        "source": source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "balance_after": round(_fund["balance"], 2),
    })
    _save()
    return {"success": True, "insurance_balance": round(_fund["balance"], 2), "deposited": round(amount, 2)}


def withdraw(amount: float, reason: str = "loss_cover") -> Dict[str, Any]:
    """Withdraw from insurance to cover losses. Returns True if enough funds."""
    _ensure()
    if _fund["balance"] < amount:
        return {"success": False, "error": f"Insufficient insurance (have {_fund['balance']:.2f}, need {amount:.2f})"}
    _fund["balance"] -= amount
    _fund["total_withdrawn"] += amount
    _fund.setdefault("transactions", []).append({
        "type": "withdraw",
        "amount": round(amount, 2),
        "reason": reason,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "balance_after": round(_fund["balance"], 2),
    })
    _save()
    return {"success": True, "insurance_balance": round(_fund["balance"], 2), "withdrawn": round(amount, 2)}


def auto_fund(pnl_usd: float) -> Dict[str, Any]:
    """Auto-deposit 5% of profit into insurance. No-op on loss."""
    if pnl_usd <= 0:
        return {"success": True, "action": "skipped", "reason": "no_profit"}
    amount = round(pnl_usd * 0.05, 2)
    if amount <= 0:
        return {"success": True, "action": "skipped", "reason": "amount_too_small"}
    return deposit(amount, source="auto_profit_share")


def status() -> Dict[str, Any]:
    _ensure()
    return {
        "success": True,
        "insurance": {
            "balance": round(_fund["balance"], 2),
            "total_deposited": round(_fund["total_deposited"], 2),
            "total_withdrawn": round(_fund["total_withdrawn"], 2),
            "net_position": round(_fund["total_deposited"] - _fund["total_withdrawn"], 2),
            "recent_transactions": _fund.get("transactions", [])[-10:],
        },
    }


def reset() -> Dict[str, Any]:
    global _fund
    _fund = {"balance": 0, "total_deposited": 0, "total_withdrawn": 0, "transactions": []}
    _save()
    return {"success": True}
