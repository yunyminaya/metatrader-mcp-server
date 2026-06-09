"""
Advanced ML Predictor — XGBoost/sklearn con modelos por sesión.

Reemplaza predictor.py (Naive Bayes) con gradient boosting real.
Entrena modelos separados para London/NY/Asian sessions.
Auto-feature engineering via features.py.
"""
import json
import logging
import math
import os
import pickle
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import numpy as np

from metatrader_mcp.tools.features import generate_with_indicators, get_feature_names

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
MODEL_DIR = os.path.join(DATA_DIR, "ml_models")

# Try to import sklearn; fallback to simple mode
_HAS_SKLEARN = False
try:
    from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    _HAS_SKLEARN = True
except ImportError:
    GradientBoostingClassifier = None
    RandomForestClassifier = None
    StandardScaler = None

# Try xgboost (import can raise XGBoostError too on Mac without libomp)
_HAS_XGB = False
try:
    from xgboost import XGBClassifier
    _HAS_XGB = True
except Exception:
    XGBClassifier = None

SESSIONS = ["london", "ny", "asian"]

_ml_state: Dict[str, Any] = {
    "models": {},
    "scalers": {},
    "feature_importance": {},
    "accuracy": {},
    "total_samples": 0,
    "last_train_time": None,
    "version": 0,
}


def _session_dir() -> str:
    os.makedirs(MODEL_DIR, exist_ok=True)
    return MODEL_DIR


def _model_path(session: str) -> str:
    return os.path.join(_session_dir(), f"model_{session}.pkl")


def _scaler_path(session: str) -> str:
    return os.path.join(_session_dir(), f"scaler_{session}.pkl")


def _meta_path() -> str:
    return os.path.join(_session_dir(), "meta.json")


def _get_session() -> str:
    """Detect current trading session based on UTC hour."""
    hour = datetime.now(timezone.utc).hour
    if 1 <= hour < 9:
        return "asian"
    elif 9 <= hour < 17:
        return "london"
    else:
        return "ny"


def _collect_training_data(client) -> Dict[str, Any]:
    """Collect features + outcomes from papertrade history for training."""
    from metatrader_mcp.tools.papertrade import portfolio
    from metatrader_mcp.tools.predictor import _load_model as load_nb_model

    p = portfolio()
    trades = p.get("portfolio", {}).get("trades", [])
    if not trades:
        return {"samples": [], "targets": [], "sessions": []}

    samples = []
    targets = []
    sessions_list = []

    for t in trades:
        pnl = t.get("pnl", 0)
        features_raw = t.get("features", t.get("indicators", {}))
        if not features_raw:
            continue
        try:
            client.market.get_candles_latest(symbol_name=t.get("symbol", "EURUSD"), timeframe="H1", count=20)
        except Exception:
            pass
        features = generate_with_indicators([], features_raw)
        if features:
            samples.append(features)
            targets.append(1.0 if pnl > 0 else 0.0)
            ts = t.get("timestamp", "")
            try:
                dt = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)
            except Exception:
                dt = datetime.now(timezone.utc)
            hour = dt.hour if dt else datetime.now(timezone.utc).hour
            if 1 <= hour < 9:
                sessions_list.append("asian")
            elif 9 <= hour < 17:
                sessions_list.append("london")
            else:
                sessions_list.append("ny")

    return {
        "samples": samples,
        "targets": targets,
        "sessions": sessions_list,
    }


def _feature_matrix(samples: List[Dict[str, float]]) -> np.ndarray:
    """Convert list of feature dicts to numpy array (aligned by union of keys)."""
    if not samples:
        return np.array([])
    all_keys = set()
    for s in samples:
        all_keys.update(s.keys())
    all_keys = sorted(all_keys)
    matrix = []
    for s in samples:
        row = [s.get(k, 0.0) for k in all_keys]
        matrix.append(row)
    return np.array(matrix, dtype=float), all_keys


def train(client, force_retrain: bool = False) -> Dict[str, Any]:
    """Train ML models from papertrade history.

    Trains one model per session (london/ny/asian) if enough data.
    Uses GradientBoostingClassifier (sklearn) or XGBoost if available.

    Args:
        client: MT5 client
        force_retrain: ignore cached models and retrain
    """
    from metatrader_mcp.tools.predictor import train as nb_train

    if not _HAS_SKLEARN and not _HAS_XGB:
        logger.warning("sklearn not available, falling back to Naive Bayes predictor")
        return nb_train()

    data = _collect_training_data(client)
    all_samples = data["samples"]
    all_targets = data["targets"]
    all_sessions = data["sessions"]

    if len(all_samples) < 20:
        return {
            "success": False,
            "error": f"Need at least 20 samples, have {len(all_samples)}",
            "trained": False,
        }

    results = {}
    global _ml_state

    for session in SESSIONS:
        idxs = [i for i, s in enumerate(all_sessions) if s == session]
        if len(idxs) < 10:
            results[session] = {"trained": False, "samples": len(idxs), "reason": "too_few"}
            continue

        s_samples = [all_samples[i] for i in idxs]
        s_targets = [all_targets[i] for i in idxs]

        matrix, keys = _feature_matrix(s_samples)
        if matrix.shape[0] < 10 or matrix.shape[1] == 0:
            results[session] = {"trained": False, "samples": matrix.shape[0], "reason": "bad_matrix"}
            continue

        # Scale features
        scaler = StandardScaler() if StandardScaler else None
        if scaler:
            X = scaler.fit_transform(matrix)
        else:
            X = matrix

        y = np.array(s_targets, dtype=float)

        # Train model
        if _HAS_XGB and XGBClassifier:
            model = XGBClassifier(
                n_estimators=100, max_depth=4, learning_rate=0.1,
                subsample=0.8, colsample_bytree=0.8, random_state=42,
                use_label_encoder=False, eval_metric="logloss",
            )
        elif GradientBoostingClassifier:
            model = GradientBoostingClassifier(
                n_estimators=100, max_depth=3, learning_rate=0.1,
                subsample=0.8, random_state=42,
            )
        elif RandomForestClassifier:
            model = RandomForestClassifier(
                n_estimators=100, max_depth=4, random_state=42,
            )
        else:
            results[session] = {"trained": False, "reason": "no_classifier"}
            continue

        model.fit(X, y)

        # Feature importance
        if hasattr(model, "feature_importances_"):
            importances = dict(zip(keys, model.feature_importances_.tolist()))
            sorted_imp = sorted(importances.items(), key=lambda x: x[1], reverse=True)
            _ml_state["feature_importance"][session] = {
                k: round(v, 4) for k, v in sorted_imp[:20] if v > 0
            }
        else:
            _ml_state["feature_importance"][session] = {}

        # Save model and scaler
        try:
            with open(_model_path(session), "wb") as f:
                pickle.dump(model, f)
            if scaler:
                with open(_scaler_path(session), "wb") as f:
                    pickle.dump(scaler, f)
        except Exception as e:
            logger.warning(f"Cannot save model for {session}: {e}")

        # Accuracy on training data
        acc = float(np.mean((model.predict(X) > 0.5) == (y > 0.5)))
        _ml_state["accuracy"][session] = round(acc * 100, 1)
        _ml_state["models"][session] = model
        if scaler:
            _ml_state["scalers"][session] = scaler

        results[session] = {
            "trained": True,
            "samples": len(idxs),
            "accuracy_pct": round(acc * 100, 1),
            "features": len(keys),
            "model_type": "XGBoost" if _HAS_XGB else "GradientBoosting",
        }

    _ml_state["total_samples"] = len(all_samples)
    _ml_state["last_train_time"] = datetime.now(timezone.utc).isoformat()
    _ml_state["version"] += 1

    # Save metadata
    try:
        meta = {
            "version": _ml_state["version"],
            "total_samples": _ml_state["total_samples"],
            "last_train_time": _ml_state["last_train_time"],
            "accuracy": _ml_state["accuracy"],
            "feature_importance": {s: dict(list(v.items())[:5]) for s, v in _ml_state["feature_importance"].items()},
        }
        with open(_meta_path(), "w") as f:
            json.dump(meta, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save ML meta: {e}")

    trained_count = sum(1 for r in results.values() if isinstance(r, dict) and r.get("trained"))

    return {
        "success": True,
        "trained": trained_count > 0,
        "sessions_trained": trained_count,
        "total_samples": len(all_samples),
        "results": results,
    }


def predict(client, symbol: str, session_override: Optional[str] = None) -> Dict[str, Any]:
    """Predict direction using trained ML model for current session.

    If no sklearn model is available, falls back to predictor.py (Naive Bayes).

    Args:
        client: MT5 client
        symbol: symbol to predict
        session_override: force a specific session model

    Returns:
        dict with direction, probability, confidence
    """
    from metatrader_mcp.tools.predictor import predict as nb_predict
    from metatrader_mcp.tools.conviction import decide

    if not _HAS_SKLEARN and not _HAS_XGB:
        logger.warning("sklearn not available, falling back to Naive Bayes")
        conv = decide(client, symbol)
        if conv.get("success"):
            return nb_predict(conv.get("decision", {}).get("indicators", {}))
        return nb_predict({})

    session = session_override or _get_session()
    model = _ml_state["models"].get(session)
    scaler = _ml_state["scalers"].get(session)

    # Try loading from disk
    if model is None:
        try:
            with open(_model_path(session), "rb") as f:
                model = pickle.load(f)
            _ml_state["models"][session] = model
            try:
                with open(_scaler_path(session), "rb") as f:
                    scaler = pickle.load(f)
                    _ml_state["scalers"][session] = scaler
            except Exception:
                scaler = None
        except Exception:
            pass

    if model is None:
        # Fallback to Naive Bayes
        conv = decide(client, symbol)
        if conv.get("success"):
            return nb_predict(conv.get("decision", {}).get("indicators", {}))
        return nb_predict({})

    # Get current features
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=200)
    except Exception:
        df = None

    candles = []
    if df is not None:
        import pandas as pd
        if isinstance(df, pd.DataFrame) and not df.empty:
            for _, row in df.iterrows():
                candles.append(row.to_dict())

    conv = decide(client, symbol)
    indicators = conv.get("decision", {}).get("indicators", {}) if conv.get("success") else {}

    features = generate_with_indicators(candles, indicators)

    if not features:
        return nb_predict(indicators)

    # Build feature vector aligned to training keys
    all_keys = set()
    for s in _ml_state["feature_importance"].get(session, {}):
        all_keys.add(s)
    # Also try to recover keys from saved model metadata
    try:
        with open(_meta_path()) as f:
            meta = json.load(f)
    except Exception:
        meta = {}

    if not all_keys:
        return nb_predict(indicators)

    sorted_keys = sorted(all_keys)
    vec = np.array([[features.get(k, 0.0) for k in sorted_keys]], dtype=float)

    if scaler:
        try:
            vec = scaler.transform(vec)
        except Exception:
            pass

    try:
        proba = model.predict_proba(vec)[0]
        pred = model.predict(vec)[0]
    except Exception:
        return nb_predict(indicators)

    if len(proba) >= 2:
        probability = max(proba[0], proba[1]) * 100
        direction = "BUY" if pred > 0.5 else "SELL"
    else:
        probability = 50.0
        direction = "NEUTRAL"

    confidence = "high" if probability >= 75 else ("medium" if probability >= 60 else "low")

    return {
        "direction": direction,
        "probability": round(probability, 1),
        "confidence": confidence,
        "session": session,
        "model_ready": True,
        "model_version": _ml_state.get("version", 0),
        "total_samples": _ml_state.get("total_samples", 0),
        "features_used": len(sorted_keys),
    }


def session_predict(client, symbol: str, session: str) -> Dict[str, Any]:
    """Predict using a specific session model (london/ny/asian)."""
    if session not in SESSIONS:
        return {"success": False, "error": f"Invalid session: {session}. Use: {SESSIONS}"}
    return predict(client, symbol, session_override=session)


def status() -> Dict[str, Any]:
    """Get ML model status."""
    try:
        with open(_meta_path()) as f:
            meta = json.load(f)
    except Exception:
        meta = {}

    models_loaded = sum(1 for s in SESSIONS if os.path.exists(_model_path(s)))

    return {
        "success": True,
        "models_loaded": models_loaded,
        "sessions": {
            s: {
                "trained": os.path.exists(_model_path(s)),
                "accuracy_pct": _ml_state.get("accuracy", {}).get(s, meta.get("accuracy", {}).get(s)),
                "top_features": list(_ml_state.get("feature_importance", {}).get(s, {}).keys())[:5],
            }
            for s in SESSIONS
        },
        "total_samples": _ml_state.get("total_samples", meta.get("total_samples", 0)),
        "version": _ml_state.get("version", meta.get("version", 0)),
        "last_train_time": _ml_state.get("last_train_time", meta.get("last_train_time")),
        "model_type": "XGBoost" if _HAS_XGB else ("GradientBoosting" if _HAS_SKLEARN else "NaiveBayes"),
    }


def reset() -> Dict[str, Any]:
    """Reset all ML models and training data."""
    global _ml_state
    _ml_state = {
        "models": {}, "scalers": {}, "feature_importance": {},
        "accuracy": {}, "total_samples": 0, "last_train_time": None, "version": 0,
    }
    import shutil
    try:
        if os.path.exists(MODEL_DIR):
            shutil.rmtree(MODEL_DIR)
    except Exception:
        pass
    return {"success": True}
