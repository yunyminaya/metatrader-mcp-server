"""
SelfLearn — tracking de predicciones vs resultados + auto-calibración.
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "selflearn.json")

_calibration: Dict[str, Any] = {}


def _load():
    global _calibration
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE) as f:
                _calibration = json.load(f)
    except Exception:
        _calibration = {"predictions": [], "total": 0}


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_calibration, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def _ensure():
    if not _calibration:
        _load()


def record(symbol: str, predicted_direction: str, expected_edge_pct: float = 5, notes: str = "") -> Dict[str, Any]:
    _ensure()
    entry = {
        "id": _calibration.get("total", 0) + 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "predicted_direction": predicted_direction,
        "expected_edge_pct": expected_edge_pct,
        "notes": notes,
        "actual_pnl_pct": None,
        "resolved": False,
    }
    _calibration.setdefault("predictions", []).append(entry)
    _calibration["total"] = _calibration.get("total", 0) + 1
    _save()
    return {"success": True, "prediction_id": entry["id"]}


def outcome(prediction_id: int, actual_pnl_pct: float) -> Dict[str, Any]:
    _ensure()
    for entry in _calibration.get("predictions", []):
        if entry.get("id") == prediction_id and not entry.get("resolved"):
            entry["actual_pnl_pct"] = actual_pnl_pct
            entry["resolved"] = True
            entry["resolved_at"] = datetime.now(timezone.utc).isoformat()
            _save()
            cal = _calibration_report()
            return {"success": True, "calibration": cal.get("report", {})}
    return {"success": False, "error": f"Prediction {prediction_id} not found"}


def report() -> Dict[str, Any]:
    _ensure()
    report_data = _calibration_report()
    bias = report_data.get("report", {}).get("bias", 0)
    adjustment = round(max(min(bias * 100, 50), -50), 1)

    if abs(adjustment) > 5:
        advice = f"Adjust edge by {adjustment:.0f}%"
        advice += ". Overconfident — reduce edge estimates." if bias > 0 else ". Underconfident — increase edge estimates."
    else:
        advice = "Calibration OK. Edges are reliable."

    report_data["adjustment_pct"] = adjustment
    report_data["advice"] = advice
    return {"success": True, "report": report_data}


def _calibration_report() -> Dict[str, Any]:
    predictions = _calibration.get("predictions", [])
    resolved = [p for p in predictions if p.get("resolved")]
    if not resolved:
        return {"report": {"total": len(predictions), "resolved": 0, "status": "no_data"}}

    total = len(resolved)
    wins = sum(1 for p in resolved if p.get("actual_pnl_pct", 0) > 0)
    win_rate = round(wins / total * 100, 1)
    avg_pnl = sum(p["actual_pnl_pct"] for p in resolved) / total
    avg_expected = sum(p["expected_edge_pct"] for p in resolved) / total
    bias = round(avg_expected - avg_pnl, 2)

    brier = sum((p["expected_edge_pct"] - max(p["actual_pnl_pct"], 0)) ** 2 for p in resolved) / total

    return {
        "report": {
            "total_predictions": len(predictions),
            "resolved": total,
            "pending": sum(1 for p in predictions if not p.get("resolved")),
            "win_rate_pct": win_rate,
            "avg_actual_pnl_pct": round(avg_pnl, 2),
            "avg_expected_edge_pct": round(avg_expected, 2),
            "bias": bias,
            "brier_score": round(brier, 4),
            "verdict": "overconfident" if bias > 5 else ("underconfident" if bias < -5 else "well-calibrated"),
        }
    }


def reset() -> Dict[str, Any]:
    global _calibration
    _calibration = {"predictions": [], "total": 0}
    _save()
    return {"success": True}
