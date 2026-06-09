"""
Fear & Greed Index — sentimiento de mercado basado en CNN Fear & Greed.

Cuando el mercado está en Extreme Fear → oportunidad de COMPRA (contrarian).
Cuando Extreme Greed → reducir riesgo, favorecer SELL.

Integrates with conviction.py to modulate confidence and position sizing.
"""
import json
import logging
import math
import urllib.request
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_FALLBACK_VALUE = 50  # Neutral
_CACHE: Dict[str, Any] = {}
_CACHE_TTL_SECONDS = 3600  # 1 hour


def fetch(force_refresh: bool = False) -> Dict[str, Any]:
    """Fetch current Fear & Greed Index.

    Tries:
      1. alternative.me API (free, no key needed)
      2. CNN Money RSS fallback
      3. Cached value if all fail

    Returns:
        dict with value (0-100), label, timestamp
    """
    now = datetime.now(timezone.utc).timestamp()
    if not force_refresh and _CACHE.get("timestamp") and (now - _CACHE["timestamp"]) < _CACHE_TTL_SECONDS:
        return _CACHE["data"]

    result = {"value": _FALLBACK_VALUE, "label": "neutral", "source": "fallback"}

    # Try alternative.me API
    sources_tried = []
    try:
        url = "https://api.alternative.me/fng/?limit=1&format=json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data and "data" in data and len(data["data"]) > 0:
                entry = data["data"][0]
                val = int(entry.get("value", _FALLBACK_VALUE))
                val = max(0, min(100, val))
                label = entry.get("value_classification", _classify(val))
                result = {"value": val, "label": label.lower(), "source": "alternative.me"}
                sources_tried.append("alternative.me OK")
    except Exception as e:
        sources_tried.append(f"alternative.me fail: {e}")

    # Fallback: compute from S&P 500 put/call ratio proxy (CNN)
    if result["source"] == "fallback":
        try:
            url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
            req = urllib.request.Request(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://money.cnn.com/data/fear-and-greed/",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                fng = data.get("fear_and_greed", {})
                score = fng.get("score", _FALLBACK_VALUE)
                val = int(score)
                val = max(0, min(100, val))
                label = _classify(val)
                result = {"value": val, "label": label, "source": "cnn"}
                sources_tried.append("CNN OK")
        except Exception as e:
            sources_tried.append(f"CNN fail: {e}")

    result["sources_tried"] = sources_tried
    result["timestamp"] = datetime.now(timezone.utc).isoformat()

    # Cache
    _CACHE["data"] = result
    _CACHE["timestamp"] = now

    return result


def _classify(value: int) -> str:
    if value <= 25:
        return "extreme_fear"
    elif value <= 40:
        return "fear"
    elif value <= 60:
        return "neutral"
    elif value <= 75:
        return "greed"
    else:
        return "extreme_greed"


def _get_currency(symbol: str) -> str:
    clean = symbol.upper().replace(".FX", "")
    majors = {"EUR", "GBP", "USD", "JPY", "AUD", "CAD", "CHF", "NZD"}
    for m in majors:
        if clean.startswith(m):
            return m
    return "USD"


def analyze(symbol: str, conviction_decision: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Get Fear & Greed analysis for a symbol.

    Args:
        symbol: trading symbol (e.g. EURUSD)
        conviction_decision: optional conviction decision to modulate

    Returns:
        dict with Fear & Greed value, signal, and modulated conviction if provided
    """
    fng = fetch()
    val = fng["value"]
    label = fng["label"]

    # Generate trading signal
    signal = "neutral"
    size_multiplier = 1.0
    reason = ""

    currency = _get_currency(symbol)
    # USD-paired symbols: Fear & Greed measures US equity market sentiment
    # Extreme Fear → risk-off → USD may weaken, favor non-USD currencies
    # Extreme Greed → risk-on → USD may strengthen
    if val <= 25:
        if currency == "USD":
            signal = "bearish"
            size_multiplier = 0.3
            reason = "Extreme Fear (risk-off) — USD may weaken"
        else:
            signal = "bullish"
            size_multiplier = 1.3
            reason = "Extreme Fear — contrarian BUY opportunity"
    elif val <= 40:
        signal = "caution"
        size_multiplier = 0.7
        reason = "Fear — reduce size, wait for extremes"
    elif val >= 90:
        signal = "strong_bearish"
        size_multiplier = 0.2
        reason = "Extreme Greed — top likely near, reduce risk significantly"
    elif val >= 75:
        signal = "bearish"
        size_multiplier = 0.5
        reason = "Greed — market overbought, reduce long exposure"
    else:
        signal = "neutral"
        size_multiplier = 1.0
        reason = "Neutral sentiment — trade normally"

    result = {
        "success": True,
        "symbol": symbol,
        "fear_greed_value": val,
        "fear_greed_label": label,
        "signal": signal,
        "size_multiplier": size_multiplier,
        "reason": reason,
        "source": fng.get("source", "unknown"),
        "timestamp": fng.get("timestamp", ""),
    }

    # Modulate conviction decision if provided
    if conviction_decision:
        decision = conviction_decision.get("decision", {})
        original_conf = decision.get("confidence_pct", 50)
        original_verdict = decision.get("verdict", "")

        if signal in ("strong_bearish", "bearish") and "BUY" in original_verdict:
            decision["fear_greed_modulation"] = "reduce"
            decision["confidence_pct"] = max(original_conf * 0.4, 10)
            if decision["confidence_pct"] < 30:
                decision["verdict"] = "PASS"
            decision["fear_greed_reason"] = reason
        elif signal in ("bullish",) and "SELL" in original_verdict:
            decision["fear_greed_modulation"] = "reduce"
            decision["confidence_pct"] = max(original_conf * 0.4, 10)
            if decision["confidence_pct"] < 30:
                decision["verdict"] = "PASS"
            decision["fear_greed_reason"] = reason
        else:
            decision["fear_greed_modulation"] = "neutral"
            decision["fear_greed_reason"] = reason

        decision["fear_greed_value"] = val
        decision["fear_greed_label"] = label
        conviction_decision["decision"] = decision
        result["modulated_conviction"] = conviction_decision

    return result
