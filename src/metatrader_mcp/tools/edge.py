"""
Edge — calculadora de Expected Value, Kelly óptimo y matching histórico.

Para cada trade potencial:
  1. Busca setups históricos SIMILARES
     (misma estrategia, condiciones de mercado parecidas)
  2. Calcula win rate, avg win, avg loss de esos históricos
  3. Expected Value = (win% × avg_win) - (loss% × avg_loss)
  4. Kelly optimal f = edge / avg_win
  5. Solo tradea si EV > 0 y win rate de similares > 55%

Matching conditions:
  - Misma estrategia
  - Régimen de mercado similar
  - RSI en rango similar
  - Volatilidad (ATR%) similar
  - Sesión similar
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "edge.json")

_state: Dict[str, Any] = {}


def _ensure():
    global _state
    if not _state:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE) as f:
                    _state = json.load(f)
        except Exception:
            _state = {
                "trades_db": [],
                "min_similar_trades": 5,
                "min_win_rate": 55,
                "kelly_fraction": 0.25,
            }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def record_trade(strategy: str, symbol: str, rsi: float, atr_pct: float,
                 regime: str, session: str, direction: str,
                 entry: float, exit: float, pnl: float) -> Dict[str, Any]:
    """Record a completed trade for future matching."""
    _ensure()
    is_win = pnl > 0
    try:
        rr = abs((exit - entry) / max(entry, exit)) if entry > 0 else 0
    except Exception:
        rr = 0

    _state.setdefault("trades_db", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "strategy": strategy,
        "symbol": symbol,
        "rsi": round(rsi, 1),
        "atr_pct": round(atr_pct, 3),
        "regime": regime,
        "session": session,
        "direction": direction,
        "entry": entry,
        "exit": exit,
        "pnl": round(pnl, 2),
        "won": is_win,
        "rr": round(rr, 2),
    })
    _state["trades_db"] = _state["trades_db"][-500:]
    _save()
    return {"success": True, "total_db": len(_state["trades_db"])}


def calculate(strategy: str, symbol: str, rsi: float, atr_pct: float,
              regime: str, session: str, direction: str) -> Dict[str, Any]:
    """Calculate expected value for a potential trade."""
    _ensure()
    db = _state.get("trades_db", [])

    if len(db) < _state.get("min_similar_trades", 5):
        return {
            "success": True, "tradeable": True,
            "reason": "insufficient_history", "edge_calculated": False,
            "total_history": len(db),
        }

    matches = []
    for t in db:
        score = 0
        if t.get("strategy") == strategy:
            score += 30
        if t.get("direction") == direction:
            score += 15
        if t.get("regime") == regime:
            score += 15
        if t.get("session") == session:
            score += 10
        if t.get("symbol", "")[:3] == symbol[:3]:
            score += 10
        if abs(t.get("rsi", 50) - rsi) <= 10:
            score += 10
        if t.get("atr_pct", 0) > 0:
            ratio = min(atr_pct, t["atr_pct"]) / max(atr_pct, t["atr_pct"])
            if ratio > 0.7:
                score += 10
        if score >= 50:
            matches.append(t)

    if len(matches) < _state.get("min_similar_trades", 5):
        return {
            "success": True, "tradeable": True,
            "reason": "not_enough_matches", "edge_calculated": False,
            "matches_found": len(matches),
        }

    wins = [m for m in matches if m.get("won")]
    losses = [m for m in matches if not m.get("won")]

    win_rate = len(wins) / len(matches) * 100
    avg_win = sum(m.get("pnl", 0) for m in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(m.get("pnl", 0) for m in losses) / len(losses)) if losses else 0
    ev = (win_rate / 100 * avg_win) - ((100 - win_rate) / 100 * avg_loss) if avg_loss else 0

    b = avg_win / avg_loss if avg_loss > 0 else 0
    p = win_rate / 100
    q = 1 - p
    kelly = max(0, min((p * b - q) / b, 0.5)) if b > 0 else 0
    kelly_actual = kelly * _state.get("kelly_fraction", 0.25)

    min_wr = _state.get("min_win_rate", 55)
    tradeable = ev > 0 and win_rate >= min_wr

    return {
        "success": True, "tradeable": tradeable, "edge_calculated": True,
        "matches_found": len(matches),
        "win_rate": round(win_rate, 1),
        "avg_win_usd": round(avg_win, 2), "avg_loss_usd": round(avg_loss, 2),
        "expected_value": round(ev, 2),
        "kelly_full": round(kelly, 3), "kelly_actual": round(kelly_actual, 3),
        "edge_quality": "excellent" if ev > 5 and win_rate > 65
        else "good" if ev > 2 and win_rate > 60
        else "fair" if ev > 0 else "negative",
        "reason": "edge_confirmed" if tradeable else "ev_too_low" if ev <= 0 else "low_win_rate",
    }


def configure(min_similar: int = 5, min_win_rate: float = 55, kelly_fraction: float = 0.25) -> Dict[str, Any]:
    _ensure()
    _state["min_similar_trades"] = min_similar
    _state["min_win_rate"] = min_win_rate
    _state["kelly_fraction"] = kelly_fraction
    _save()
    return {"success": True}


def status() -> Dict[str, Any]:
    _ensure()
    return {
        "success": True,
        "edge": {
            "total_trades_db": len(_state.get("trades_db", [])),
            "min_similar_trades": _state.get("min_similar_trades", 5),
            "min_win_rate": _state.get("min_win_rate", 55),
            "kelly_fraction": _state.get("kelly_fraction", 0.25),
        }
    }
