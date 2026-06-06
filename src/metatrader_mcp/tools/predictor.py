"""
Predictor — capa de Machine Learning para trading en MT5.

Entrena un clasificador (Random Forest) con los valores reales de
todos los indicadores vs el resultado del trade (ganó/perdió).

Auto-aprende de cada trade que haces. Sin sklearn, usa Naive Bayes
con tablas de probabilidad condicional.

Produce:
  - Dirección predicha (BUY/SELL)
  - Probabilidad 0-99
  - Feature importance (qué indicadores importan más)
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "predictor_data.json")
MODEL_FILE = os.path.join(DATA_DIR, "predictor_model.json")

_predictor_state: Dict[str, Any] = {}

# ── Feature names (must match keys produced by conviction.decide) ──────────────
FEATURE_NAMES = [
    "rsi", "ma_cross", "macd", "bb_z_score", "adx",
    "stoch_k", "stoch_d", "momentum", "sr_signal", "volume_ratio",
    "session_quality", "mtf_alignment",
]

# Discretization buckets for Naive Bayes
_BUCKETS = {
    "rsi": [(0, 30), (30, 45), (45, 55), (55, 70), (70, 100)],
    "ma_cross": [(-2, -0.5), (-0.5, 0.5), (0.5, 2)],
    "macd": [(-2, -0.5), (-0.5, 0.5), (0.5, 2)],
    "bb_z_score": [(-3, -1), (-1, -0.3), (-0.3, 0.3), (0.3, 1), (1, 3)],
    "adx": [(0, 20), (20, 25), (25, 30), (30, 60)],
    "stoch_k": [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)],
    "stoch_d": [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)],
    "momentum": [(-10, -1), (-1, -0.3), (-0.3, 0.3), (0.3, 1), (1, 10)],
    "sr_signal": [(-2, -0.5), (-0.5, 0.5), (0.5, 2)],
    "volume_ratio": [(0, 0.5), (0.5, 0.8), (0.8, 1.2), (1.2, 2), (2, 10)],
    "session_quality": [(0, 20), (20, 40), (40, 60), (60, 80), (80, 100)],
    "mtf_alignment": [(-100, -30), (-30, 30), (30, 100)],
}

_DEFAULT_BUCKETS = [(-1e9, 1e9)]


def _ensure():
    global _predictor_state
    if not _predictor_state:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE) as f:
                    _predictor_state = json.load(f)
            else:
                _reset_state()
        except Exception:
            _reset_state()


def _reset_state():
    global _predictor_state
    _predictor_state = {
        "samples": [],
        "total_samples": 0,
        "last_train_time": None,
        "model_version": 0,
        "feature_importance": {},
        "accuracy": 0,
        "win_rate_by_feature": {},
    }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            # Don't save raw data, only stats
            state_save = {
                "total_samples": _predictor_state.get("total_samples", 0),
                "last_train_time": _predictor_state.get("last_train_time"),
                "model_version": _predictor_state.get("model_version", 0),
                "feature_importance": _predictor_state.get("feature_importance", {}),
                "accuracy": _predictor_state.get("accuracy", 0),
            }
            json.dump(state_save, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save predictor state: {e}")


def _save_model(model_data: dict):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(MODEL_FILE, "w") as f:
            json.dump(model_data, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save model: {e}")


def _load_model() -> dict:
    try:
        if os.path.exists(MODEL_FILE):
            with open(MODEL_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _bucket_value(name: str, value: float) -> str:
    """Discretize a feature value into a bucket label."""
    buckets = _BUCKETS.get(name, _DEFAULT_BUCKETS)
    for lo, hi in buckets:
        if lo <= value < hi:
            return f"{lo}_{hi}"
    return f"{buckets[-1][0]}_{buckets[-1][1]}" if buckets else "default"


# ════════════════════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════════════════════

def collect_training_sample(features: Dict[str, float], outcome_win: bool) -> Dict[str, Any]:
    """Store a training sample (features + result) for model training.

    Called automatically by papertrade when a trade closes.
    Features dict must contain FEATURE_NAMES keys.

    Args:
        features: dict of indicator values at entry time
        outcome_win: True if trade was profitable
    """
    _ensure()
    sample = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "features": {k: features.get(k, 0) for k in FEATURE_NAMES},
        "outcome": 1 if outcome_win else 0,
    }
    _predictor_state.setdefault("samples", []).append(sample)
    _predictor_state["total_samples"] = _predictor_state.get("total_samples", 0) + 1
    _save()
    return {"success": True, "total_samples": _predictor_state["total_samples"]}


def train(min_samples: int = 10) -> Dict[str, Any]:
    """Train/retrain the predictor model from all collected samples.

    Uses Naive-Bayes-like conditional probability tables.
    Tracks win rate per feature bucket to compute feature importance.

    Args:
        min_samples: minimum samples to train
    """
    _ensure()
    samples = _predictor_state.get("samples", [])
    n = len(samples)

    if n < min_samples:
        return {"success": False, "error": f"Need {min_samples} samples, have {n}", "trained": False}

    # Separate features and outcomes
    wins = sum(1 for s in samples if s["outcome"] == 1)
    total_win_rate = wins / n

    # For each feature, compute win rate per bucket
    feature_stats = {}
    for name in FEATURE_NAMES:
        buckets = _BUCKETS.get(name, _DEFAULT_BUCKETS)
        bucket_stats = {}
        for lo, hi in buckets:
            label = f"{lo}_{hi}"
            bucket_samples = [s for s in samples if lo <= s["features"].get(name, 0) < hi]
            bucket_wins = sum(1 for s in bucket_samples if s["outcome"] == 1)
            bucket_total = len(bucket_samples)
            bucket_stats[label] = {
                "count": bucket_total,
                "wins": bucket_wins,
                "win_rate": bucket_wins / max(bucket_total, 1),
            }
        feature_stats[name] = bucket_stats

    # Feature importance = how much each feature's bucket win rates deviate from overall
    importance = {}
    for name, buckets in feature_stats.items():
        deviation = 0
        total_bucket_samples = 0
        for blabel, bstats in buckets.items():
            w = bstats["count"]
            total_bucket_samples += w
            deviation += w * abs(bstats["win_rate"] - total_win_rate)
        importance[name] = round(deviation / max(total_bucket_samples, 1) * 100, 1) if total_bucket_samples > 0 else 0

    # Normalize importance
    max_imp = max(importance.values()) if importance else 1
    if max_imp > 0:
        importance = {k: round(v / max_imp * 100, 0) for k, v in importance.items()}

    # Sort by importance
    importance = dict(sorted(importance.items(), key=lambda x: x[1], reverse=True))

    # Model data
    model = {
        "version": _predictor_state.get("model_version", 0) + 1,
        "total_samples": n,
        "total_win_rate": round(total_win_rate * 100, 1),
        "wins": wins,
        "losses": n - wins,
        "feature_stats": feature_stats,
        "feature_importance": importance,
        "trained_at": datetime.now(timezone.utc).isoformat(),
    }

    _predictor_state["model_version"] = model["version"]
    _predictor_state["feature_importance"] = importance
    _predictor_state["last_train_time"] = model["trained_at"]
    _predictor_state["accuracy"] = round(max(total_win_rate, 0.5) * 100, 1)

    _save_model(model)
    _save()

    top_features = list(importance.keys())[:5]
    logger.info(f"ML predictor trained: {n} samples, {model['total_win_rate']}% win rate")
    logger.info(f"Top features: {top_features}")

    return {
        "success": True,
        "trained": True,
        "model_version": model["version"],
        "total_samples": n,
        "win_rate": model["total_win_rate"],
        "top_features": top_features,
    }


def predict(features: Dict[str, float]) -> Dict[str, Any]:
    """Predict next direction using trained model.

    If model trained: Naive Bayes probability from feature buckets.
    If no model: returns neutral (50% BUY/SELL).

    Args:
        features: dict of indicator values (same keys as FEATURE_NAMES)

    Returns:
        {
            "direction": "BUY" | "SELL",
            "probability": 0-99,
            "confidence": "low" | "medium" | "high",
            "model_ready": bool,
        }
    """
    model = _load_model()
    if not model or model.get("total_samples", 0) < 5:
        return {
            "direction": "NEUTRAL",
            "probability": 50,
            "confidence": "low",
            "model_ready": False,
            "message": "Not enough training data",
        }

    feature_stats = model.get("feature_stats", {})
    total_win_rate = model.get("total_win_rate", 50) / 100

    # Naive Bayes: probability that this feature set predicts a win
    prob_win = math.log(total_win_rate / max(1 - total_win_rate, 0.01))
    prob_loss = math.log((1 - total_win_rate) / max(total_win_rate, 0.01))

    for name in FEATURE_NAMES:
        val = features.get(name, 0)
        bucket_label = _bucket_value(name, val)
        stats = feature_stats.get(name, {}).get(bucket_label, {"count": 0, "wins": 0, "win_rate": 0.5})

        # Laplace smoothing
        bucket_wr = (stats["wins"] + 1) / max(stats["count"] + 2, 1)
        prob_win += math.log(max(bucket_wr, 0.01))
        prob_loss += math.log(max(1 - bucket_wr, 0.01))

    # Normalize to probability
    total = math.exp(prob_win) + math.exp(prob_loss)
    win_probability = math.exp(prob_win) / max(total, 0.0001)

    # Direction
    direction = "BUY" if win_probability > 0.5 else "SELL"
    probability = round(max(win_probability, 1 - win_probability) * 100, 0)

    # Confidence level
    if probability >= 75:
        confidence = "high"
    elif probability >= 60:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "direction": direction,
        "probability": probability,
        "confidence": confidence,
        "model_ready": True,
        "model_version": model.get("version"),
        "total_samples": model.get("total_samples"),
        "overall_win_rate": model.get("total_win_rate"),
        "feature_values": {k: round(features.get(k, 0), 2) for k in FEATURE_NAMES},
    }


def get_model_info() -> Dict[str, Any]:
    """Get model status: version, samples, feature importance, accuracy."""
    _ensure()
    model = _load_model()

    return {
        "success": True,
        "predictor": {
            "model_ready": bool(model),
            "total_samples": _predictor_state.get("total_samples", 0),
            "model_version": _predictor_state.get("model_version", 0),
            "accuracy_pct": _predictor_state.get("accuracy", 0),
            "last_trained": _predictor_state.get("last_train_time"),
            "feature_importance": _predictor_state.get("feature_importance", {}),
            "top_features": list(_predictor_state.get("feature_importance", {}).keys())[:5],
            "raw_samples_in_memory": len(_predictor_state.get("samples", [])),
        },
    }


def reset() -> Dict[str, Any]:
    """Reset all training data and model."""
    global _predictor_state
    _reset_state()
    _save()
    # Remove model file
    try:
        if os.path.exists(MODEL_FILE):
            os.remove(MODEL_FILE)
    except Exception:
        pass
    return {"success": True}


# ════════════════════════════════════════════════════════════════════════════════
# Integration helpers (called by conviction/papertrade)
# ════════════════════════════════════════════════════════════════════════════════

def extract_features(indicator_dict: Dict[str, Any]) -> Dict[str, float]:
    """Extract standardized feature vector from conviction indicator dict."""
    features = {}
    # Map conviction indicator names to FEATURE_NAMES
    mapping = {
        "rsi": "rsi",
        "ma_cross": "ma_cross",
        "macd": "macd",
        "bb_z_score": "bb_z_score",
        "adx": "adx",
        "stoch_k": "stoch_k",
        "stoch_d": "stoch_d",
        "momentum": "momentum",
        "sr_signal": "sr_signal",
        "volume_ratio": "volume_ratio",
        "session_quality": "session_quality",
    }
    for model_key, conv_key in mapping.items():
        val = indicator_dict.get(conv_key, 0)
        if isinstance(val, (int, float)):
            features[model_key] = float(val)

    # MTF alignment
    mtf = indicator_dict.get("mtf_alignment", indicator_dict.get("mtf", {}).get("alignment", 0))
    features["mtf_alignment"] = float(mtf) if isinstance(mtf, (int, float)) else 0

    return features


def modulate_confidence(conviction_result: Dict[str, Any]) -> Dict[str, Any]:
    """Blend conviction score with ML prediction.

    Called by conviction.decide after all indicators are computed.
    Returns modified verdict with ML-adjusted confidence.
    """
    decision = conviction_result.get("decision", {})
    indicators = decision.get("indicators", {})
    if not indicators:
        return conviction_result

    features = extract_features(indicators)
    # Add session quality and MTF from decision
    features["session_quality"] = decision.get("session_quality", 50)
    features["mtf_alignment"] = decision.get("mtf", {}).get("alignment", 0)

    ml = predict(features)
    if not ml.get("model_ready"):
        return conviction_result

    # Blend: 70% conviction score + 30% ML probability
    original_confidence = decision.get("confidence_pct", 0)
    ml_prob = ml.get("probability", 50)

    # ML direction must agree with original verdict
    original_direction = 1 if "BUY" in decision.get("verdict", "") else (-1 if "SELL" in decision.get("verdict", "") else 0)
    ml_direction = 1 if ml["direction"] == "BUY" else -1

    if original_direction == 0:
        # Original was PASS — let ML override if highly confident
        if ml.get("confidence") == "high":
            adjusted = ml_prob * 0.6
            decision["verdict"] = "BUY" if ml_direction > 0 else "SELL"
            decision["ml_override"] = True
        else:
            adjusted = 0
            decision["ml_override"] = False
    elif original_direction == ml_direction:
        # Agreement: boost confidence
        adjusted = original_confidence * 0.7 + ml_prob * 0.3
        decision["ml_boost"] = "agreement"
    else:
        # Disagreement: reduce confidence, or PASS if ML is confident
        if ml.get("confidence") == "high" and ml_prob >= 70:
            adjusted = ml_prob * 0.5
            decision["verdict"] = "BUY" if ml_direction > 0 else "SELL"
            decision["ml_override"] = True
            decision["ml_boost"] = "override"
        else:
            adjusted = original_confidence * 0.5
            if adjusted < 40:
                decision["verdict"] = "PASS"
                decision["ml_boost"] = "veto"
            else:
                decision["ml_boost"] = "warning"

    decision["confidence_pct"] = min(abs(adjusted), 99)
    decision["ml_probability"] = ml_prob
    decision["ml_direction"] = ml["direction"]
    decision["model_trained"] = True

    conviction_result["decision"] = decision
    return conviction_result
