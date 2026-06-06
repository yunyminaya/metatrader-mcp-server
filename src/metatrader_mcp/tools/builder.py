"""
Builder — constructor compuesto de estrategias para MT5.

Permite combinar indicadores con operadores lógicos,
asignar SL/TP dinámicos y persistir estrategias.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "strategies.json")

_strategies: Dict[str, Any] = {}


def _ensure():
    global _strategies
    if not _strategies:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE) as f:
                    _strategies = json.load(f)
        except Exception:
            _strategies = {"strategies": {}}


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_strategies, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def create(name: str, description: str = "", entry_conditions: Optional[List[Dict]] = None,
           exit_conditions: Optional[List[Dict]] = None,
           sl_atr_multiple: float = 1.5, tp_atr_multiple: float = 3.0,
           max_risk_usd: float = 10, max_positions: int = 3) -> Dict[str, Any]:
    _ensure()
    if name in _strategies.get("strategies", {}):
        return {"success": False, "error": f"Strategy '{name}' exists"}

    strategy = {
        "name": name,
        "description": description,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "entry_conditions": entry_conditions or [],
        "exit_conditions": exit_conditions or [],
        "sl_atr_multiple": sl_atr_multiple,
        "tp_atr_multiple": tp_atr_multiple,
        "max_risk_usd": max_risk_usd,
        "max_positions": max_positions,
        "enabled": True,
    }
    _strategies.setdefault("strategies", {})[name] = strategy
    _save()
    return {"success": True, "strategy": strategy}


def list_all() -> Dict[str, Any]:
    _ensure()
    return {"success": True, "strategies": list(_strategies.get("strategies", {}).values())}


def get(name: str) -> Dict[str, Any]:
    _ensure()
    s = _strategies.get("strategies", {}).get(name)
    if not s:
        return {"success": False, "error": f"Strategy '{name}' not found"}
    return {"success": True, "strategy": s}


def delete(name: str) -> Dict[str, Any]:
    _ensure()
    if _strategies.get("strategies", {}).pop(name, None):
        _save()
        return {"success": True}
    return {"success": False, "error": f"Strategy '{name}' not found"}


def evaluate(client, name: str, symbol: str, timeframe: str = "H1") -> Dict[str, Any]:
    """Evalúa una estrategia guardada contra velas actuales."""
    s = _strategies.get("strategies", {}).get(name)
    if not s:
        return {"success": False, "error": f"Strategy '{name}' not found"}

    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=100)
    except Exception as e:
        return {"success": False, "error": f"Cannot fetch data: {e}"}

    if df is None or (hasattr(df, 'empty') and df.empty):
        return {"success": False, "error": "No candle data"}

    import pandas as pd
    if isinstance(df, pd.DataFrame):
        data = df.to_dict(orient="records")
    else:
        return {"success": False, "error": "Unexpected format"}

    if len(data) < 30:
        return {"success": False, "error": "Not enough candles"}

    closes = [float(d["close"]) for d in data]
    highs = [float(d["high"]) for d in data]
    lows = [float(d["low"]) for d in data]

    # Calcular ATR para SL/TP dinámico
    trs = []
    for i in range(1, len(data)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    atr = sum(trs[-14:]) / min(14, len(trs)) if trs else 0

    signal = {"entry": None, "confidence": 0, "reasons": [], "sl": 0, "tp": 0}

    for cond in s.get("entry_conditions", []):
        indicator = cond.get("indicator", "").lower()
        operator = cond.get("operator", ">")
        value = cond.get("value", 0)

        val = _get_indicator_value(indicator, closes, highs, lows)
        if val is None:
            continue

        if operator == ">" and val > value:
            signal["reasons"].append(f"{indicator} {val:.2f} > {value}")
            signal["confidence"] += 20
        elif operator == "<" and val < value:
            signal["reasons"].append(f"{indicator} {val:.2f} < {value}")
            signal["confidence"] += 20
        elif operator == "==" and abs(val - value) < 0.01:
            signal["reasons"].append(f"{indicator} {val:.2f} == {value}")
            signal["confidence"] += 20
        elif operator == "cross_above" and _crossed_above(indicator, closes, highs, lows):
            signal["reasons"].append(f"{indicator} crossed above")
            signal["confidence"] += 25
        elif operator == "cross_below" and _crossed_below(indicator, closes, highs, lows):
            signal["reasons"].append(f"{indicator} crossed below")
            signal["confidence"] += 25

    entries = max(len(s.get("entry_conditions", [])), 1)
    signal["confidence"] = min(signal["confidence"] / entries * 100, 99)

    if signal["confidence"] >= 50:
        signal["entry"] = "BUY" if closes[-1] > sum(closes[-5:]) / 5 else "SELL"
        price = closes[-1]
        if atr > 0:
            signal["sl"] = round(price - atr * s["sl_atr_multiple"], 5)
            signal["tp"] = round(price + atr * s["tp_atr_multiple"], 5)
        signal["lot_size"] = min(round(s["max_risk_usd"] / max(atr * s["sl_atr_multiple"] * 1000, 0.01), 2), 0.1)

    return {
        "success": True,
        "evaluation": {
            "strategy": name,
            "symbol": symbol,
            "timeframe": timeframe,
            "price": round(closes[-1], 5),
            "atr": round(atr, 5),
            "signal": signal,
        },
    }


def _get_indicator_value(indicator: str, closes: list, highs: list, lows: list):
    if len(closes) < 20:
        return None
    if indicator == "rsi":
        from .conviction import _rsi as rsi_func
        return rsi_func(closes)
    if indicator == "ma_fast":
        return sum(closes[-5:]) / 5
    if indicator == "ma_slow":
        return sum(closes[-20:]) / 20
    if indicator == "bb_position":
        return _bb_pos(closes)
    if indicator == "atr_pct":
        trs = []
        for i in range(1, len(closes)):
            tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
            trs.append(tr)
        atr = sum(trs[-14:]) / min(14, len(trs)) if trs else 0
        return atr / closes[-1] * 100 if closes[-1] else 0
    if indicator == "volume_ratio":
        return 1.0
    return None


def _bb_pos(closes, period=20):
    if len(closes) < period:
        return 0
    sma = sum(closes[-period:]) / period
    variance = sum((c - sma)**2 for c in closes[-period:]) / period
    std = variance ** 0.5
    current = closes[-1]
    if current >= sma + 2 * std:
        return 2
    if current <= sma - 2 * std:
        return -2
    return (current - sma) / std if std else 0


def _crossed_above(indicator, closes, highs, lows):
    if len(closes) < 2:
        return False
    v_prev = _get_indicator_value(indicator, closes[:-1], highs[:-1], lows[:-1])
    v_curr = _get_indicator_value(indicator, closes, highs, lows)
    if v_prev is None or v_curr is None:
        return False
    if indicator in ("rsi",):
        threshold = 30
    elif indicator == "bb_position":
        threshold = -1
    else:
        return False
    return v_prev <= threshold and v_curr > threshold


def _crossed_below(indicator, closes, highs, lows):
    if len(closes) < 2:
        return False
    v_prev = _get_indicator_value(indicator, closes[:-1], highs[:-1], lows[:-1])
    v_curr = _get_indicator_value(indicator, closes, highs, lows)
    if v_prev is None or v_curr is None:
        return False
    if indicator in ("rsi",):
        threshold = 70
    elif indicator == "bb_position":
        threshold = 1
    else:
        return False
    return v_prev >= threshold and v_curr < threshold
