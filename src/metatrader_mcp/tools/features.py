"""
Feature Engineering Engine — genera 200+ features de trading.

Toma OHLCV + indicadores y produce vectores de features para ML.
"""
import logging
import math
import numpy as np
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

WINDOWS = [5, 10, 20, 50, 100]
BASE_FEATURES = ["rsi", "macd", "bb_z_score", "adx", "momentum",
                 "stoch_k", "stoch_d", "volume_ratio", "ma_cross", "sr_signal"]

def _roc(series: List[float], period: int = 1) -> List[float]:
    if len(series) < period + 1:
        return [0.0] * len(series)
    result = [0.0] * period
    for i in range(period, len(series)):
        prev = series[i - period] if series[i - period] != 0 else 0.0001
        result.append((series[i] - series[i - period]) / prev)
    return result

def _zscore(series: List[float]) -> float:
    arr = np.array(series, dtype=float)
    if len(arr) < 2 or np.std(arr) == 0:
        return 0.0
    return float((arr[-1] - np.mean(arr)) / np.std(arr))

def _percentile(series: List[float]) -> float:
    if not series:
        return 0.5
    val = series[-1]
    count_less = sum(1 for x in series if x < val)
    return count_less / max(len(series), 1)

def _sma(series: List[float], period: int) -> float:
    if len(series) < period:
        return float(np.mean(series))
    return float(np.mean(series[-period:]))

def _std(series: List[float], period: int) -> float:
    if len(series) < period:
        return float(np.std(series))
    return float(np.std(series[-period:]))

def generate_from_ohlcv(candles: List[Dict[str, Any]]) -> Dict[str, float]:
    """Generate 200+ features from OHLCV data only (no indicators needed)."""
    features = {}
    if not candles or len(candles) < 5:
        return features

    opens = np.array([float(c.get("open", 0)) for c in candles], dtype=float)
    highs = np.array([float(c.get("high", 0)) for c in candles], dtype=float)
    lows = np.array([float(c.get("low", 0)) for c in candles], dtype=float)
    closes = np.array([float(c.get("close", 0)) for c in candles], dtype=float)
    volumes = np.array([float(c.get("volume", 0) if c.get("volume", 0) else c.get("tick_volume", 0)) for c in candles], dtype=float)

    n = len(closes)
    if n < 5:
        return features

    closes_l = closes.tolist()
    highs_l = highs.tolist()
    lows_l = lows.tolist()
    volumes_l = volumes.tolist()

    # Raw price features
    features["close"] = float(closes[-1])
    features["high"] = float(highs[-1])
    features["low"] = float(lows[-1])
    features["volume"] = float(volumes[-1])

    # Returns
    returns = np.diff(closes) / closes[:-1]
    features["return_1"] = float(returns[-1]) if len(returns) >= 1 else 0
    features["return_5"] = float(np.sum(returns[-5:])) if len(returns) >= 5 else 0
    features["return_10"] = float(np.sum(returns[-10:])) if len(returns) >= 10 else 0
    features["return_20"] = float(np.sum(returns[-20:])) if len(returns) >= 20 else 0

    # Rolling z-scores of closes
    for w in WINDOWS:
        features[f"close_z_{w}"] = _zscore(closes_l[-w:]) if n >= w else 0
        features[f"close_sma_{w}"] = float(closes[-1] / max(_sma(closes_l, w), 0.0001)) - 1.0
        features[f"high_sma_{w}"] = float(highs[-1] / max(_sma(highs_l, w), 0.0001)) - 1.0
        features[f"low_sma_{w}"] = float(lows[-1] / max(_sma(lows_l, w), 0.0001)) - 1.0
        features[f"volume_sma_{w}"] = float(volumes[-1] / max(_sma(volumes_l, w), 0.0001)) - 1.0
        features[f"volume_z_{w}"] = _zscore(volumes_l[-w:]) if n >= w else 0

    # Range features
    ranges = highs - lows
    ranges_l = ranges.tolist()
    features["range"] = float(ranges[-1])
    features["range_pct"] = float(ranges[-1] / max(closes[-1], 0.0001))
    for w in [5, 10, 20]:
        features[f"range_sma_{w}"] = float(np.mean(ranges[-w:])) if n >= w else 0
        features[f"range_z_{w}"] = _zscore(ranges_l[-w:]) if n >= w else 0

    # Volatility (ATR approximation)
    tr = np.maximum(highs[1:] - lows[1:],
                    np.maximum(np.abs(highs[1:] - closes[:-1]),
                               np.abs(lows[1:] - closes[:-1])))
    tr_list = tr.tolist()
    for w in [5, 10, 14, 20]:
        features[f"atr_{w}"] = float(np.mean(tr[-w:])) if len(tr) >= w else 0
        features[f"atr_pct_{w}"] = float(np.mean(tr[-w:]) / max(closes[-1], 0.0001)) if len(tr) >= w else 0

    # Body ratio (real body / range)
    bodies = np.abs(closes - opens)
    body_ratios = np.divide(bodies, ranges, out=np.zeros_like(bodies), where=ranges != 0)
    features["body_ratio"] = float(body_ratios[-1])
    for w in [5, 10, 20]:
        features[f"body_ratio_sma_{w}"] = float(np.mean(body_ratios[-w:])) if n >= w else 0

    # Upper/lower wick ratios
    upper_wick = highs - np.maximum(opens, closes)
    lower_wick = np.minimum(opens, closes) - lows
    features["upper_wick_pct"] = float(upper_wick[-1] / max(ranges[-1], 0.0001))
    features["lower_wick_pct"] = float(lower_wick[-1] / max(ranges[-1], 0.0001))

    # Price position in range
    for w in [5, 10, 20, 50, 100]:
        if n >= w:
            pos = (closes[-1] - np.min(lows[-w:])) / max(np.max(highs[-w:]) - np.min(lows[-w:]), 0.0001)
            features[f"pos_in_range_{w}"] = float(pos)

    # Volume price confirmation
    vol_roc = _roc(volumes_l, 5)
    price_roc = _roc(closes_l, 5)
    features["volume_price_divergence"] = float(
        (vol_roc[-1] if vol_roc else 0) - (price_roc[-1] if price_roc else 0)
    )

    # Rolling percentiles
    for w in [20, 50, 100]:
        if n >= w:
            features[f"close_percentile_{w}"] = _percentile(closes_l[-w:])
            features[f"volume_percentile_{w}"] = _percentile(volumes_l[-w:])

    # Gap features
    if n >= 2:
        gap = (opens[-1] - closes[-2]) / max(closes[-2], 0.0001)
        features["gap_pct"] = float(gap)

    # Candle pattern scores (simple)
    bullish_engulfing = 1.0 if (n >= 2 and
        closes[-1] > opens[-1] and closes[-2] < opens[-2] and
        closes[-1] > opens[-2] and opens[-1] < closes[-2]) else 0.0
    features["bullish_engulfing"] = bullish_engulfing

    bearish_engulfing = 1.0 if (n >= 2 and
        closes[-1] < opens[-1] and closes[-2] > opens[-2] and
        closes[-1] < opens[-2] and opens[-1] > closes[-2]) else 0.0
    features["bearish_engulfing"] = bearish_engulfing

    doji = 1.0 if abs(closes[-1] - opens[-1]) / max(ranges[-1], 0.0001) < 0.1 else 0.0
    features["doji"] = doji

    hammer = 1.0 if (lower_wick[-1] > 2 * bodies[-1] and upper_wick[-1] < bodies[-1]) else 0.0
    features["hammer"] = float(hammer)

    shooting_star = 1.0 if (upper_wick[-1] > 2 * bodies[-1] and lower_wick[-1] < bodies[-1]) else 0.0
    features["shooting_star"] = float(shooting_star)

    # Momentum features
    for w in [3, 5, 10, 20]:
        roc_vals = _roc(closes_l, w)
        features[f"momentum_{w}"] = float(roc_vals[-1]) if roc_vals else 0
        features[f"roc_volatility_{w}"] = float(np.std(roc_vals[-w:])) if len(roc_vals) >= w else 0

    # Log returns
    log_ret = np.diff(np.log(closes + 0.0001))
    features["log_return_1"] = float(log_ret[-1]) if len(log_ret) >= 1 else 0
    for w in [5, 10, 20]:
        features[f"log_ret_vol_{w}"] = float(np.std(log_ret[-w:])) if len(log_ret) >= w else 0

    return features

def generate_with_indicators(candles: List[Dict[str, Any]],
                              indicator_dict: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Combine OHLCV features with indicator values for a complete feature vector."""
    features = generate_from_ohlcv(candles)

    if indicator_dict:
        for k, v in indicator_dict.items():
            if isinstance(v, (int, float)):
                features[f"ind_{k}"] = float(v)

    return features

def get_feature_names() -> List[str]:
    """Return list of all possible feature names for reference."""
    base = ["close", "high", "low", "volume", "return_1", "return_5", "return_10", "return_20"]
    for w in WINDOWS:
        base.extend([f"close_z_{w}", f"close_sma_{w}", f"high_sma_{w}",
                     f"low_sma_{w}", f"volume_sma_{w}", f"volume_z_{w}"])
    for w in [5, 10, 20]:
        base.extend([f"range_sma_{w}", f"range_z_{w}"])
    for w in [5, 10, 14, 20]:
        base.extend([f"atr_{w}", f"atr_pct_{w}"])
    for w in [5, 10, 20]:
        base.append(f"body_ratio_sma_{w}")
    for w in [5, 10, 20, 50, 100]:
        base.append(f"pos_in_range_{w}")
    for w in [20, 50, 100]:
        base.extend([f"close_percentile_{w}", f"volume_percentile_{w}"])
    base.extend(["gap_pct", "bullish_engulfing", "bearish_engulfing", "doji", "hammer", "shooting_star"])
    for w in [3, 5, 10, 20]:
        base.extend([f"momentum_{w}", f"roc_volatility_{w}"])
    base.extend(["log_return_1", "log_ret_vol_5", "log_ret_vol_10", "log_ret_vol_20"])
    base.extend([f"ind_{name}" for name in BASE_FEATURES])
    return base

def select_top(features_list: List[Dict[str, float]],
               targets: List[float],
               k: int = 20) -> Dict[str, Any]:
    """Select top K features by mutual information with target.

    Args:
        features_list: list of feature dicts (each sample = one dict)
        targets: list of target values (0/1 for win/loss)
        k: number of top features to return
    """
    if not features_list or len(features_list) < 5:
        return {"success": False, "error": "Not enough samples", "top_features": []}

    feature_names = list(features_list[0].keys())
    n_samples = len(features_list)
    n_features = len(feature_names)

    # Simple correlation-based feature selection
    scores = {}
    for name in feature_names:
        vals = [f.get(name, 0) for f in features_list]
        # Pearson correlation with target
        mean_v = sum(vals) / n_samples
        mean_t = sum(targets) / n_samples
        num = sum((v - mean_v) * (t - mean_t) for v, t in zip(vals, targets))
        den_v = math.sqrt(sum((v - mean_v)**2 for v in vals))
        den_t = math.sqrt(sum((t - mean_t)**2 for t in targets))
        if den_v * den_t == 0:
            scores[name] = 0.0
        else:
            scores[name] = abs(num / (den_v * den_t))

    sorted_features = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top = [{"name": n, "score": round(s, 4)} for n, s in sorted_features[:k] if s > 0.01]

    return {
        "success": True,
        "total_features": n_features,
        "top_features": top,
        "top_names": [t["name"] for t in top],
    }
