"""
Order Flow Imbalance — tick-level bid/ask volume analysis.

Lee datos de ticks de MT5 (bid/ask/volume) y computa:
  - Cumulative Volume Delta (CVD): (bid_vol - ask_vol) acumulado
  - Imbalance Ratio: bid_vol / (bid_vol + ask_vol)
  - Absorción: precio no se mueve en volumen alto → reversión inminente
  - Agotamiento: imbalance extremo + precio desacelerando

Usa datos de tick disponibles en MT5 sin necesidad de librerías externas.
"""
import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

_MIN_TICKS = 50  # Minimum ticks to produce a signal


def check(client, symbol: str, lookback_ticks: int = 200) -> Dict[str, Any]:
    """Analyze tick-level order flow for a symbol.

    Args:
        client: MT5 client
        symbol: symbol to analyze
        lookback_ticks: number of recent ticks to use

    Returns:
        dict with imbalance metrics and trading signal
    """
    try:
        ticks = client.market.get_ticks(symbol_name=symbol, count=lookback_ticks)
    except Exception as e:
        return {"success": False, "error": f"Cannot get ticks: {e}"}

    if ticks is None or (hasattr(ticks, "empty") and ticks.empty):
        return {"success": False, "error": "No tick data available"}

    import pandas as pd

    if isinstance(ticks, pd.DataFrame):
        ticks_dict = ticks.to_dict("records")
    elif isinstance(ticks, list):
        ticks_dict = ticks
    else:
        return {"success": False, "error": f"Unexpected tick format: {type(ticks)}"}

    if len(ticks_dict) < _MIN_TICKS:
        return {
            "success": False,
            "error": f"Need at least {_MIN_TICKS} ticks, have {len(ticks_dict)}",
        }

    return _analyze_ticks(ticks_dict)


def _analyze_ticks(ticks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Core tick analysis engine."""
    # Extract bid, ask, volume (MT5 tick format varies)
    bids = []
    asks = []
    volumes = []
    timestamps = []
    prices = []

    for t in ticks:
        bid = t.get("bid", t.get("Bid", t.get("price_bid", 0)))
        ask = t.get("ask", t.get("Ask", t.get("price_ask", 0)))
        vol = t.get("volume", t.get("Volume", t.get("tick_volume", 1)))
        ts = t.get("time", t.get("Time", t.get("timestamp", 0)))
        price = t.get("price", t.get("Price", t.get("last", bid or ask)))

        if not bid and not ask:
            continue

        bids.append(float(bid) if bid else float(price))
        asks.append(float(ask) if ask else float(price))
        volumes.append(int(vol) if vol else 1)
        timestamps.append(ts if isinstance(ts, (int, float)) else 0)
        prices.append(float(price) if price else 0)

    if len(bids) < _MIN_TICKS:
        return {"success": False, "error": f"Only {len(bids)} usable ticks"}

    n = len(bids)
    mid_prices = [(b + a) / 2 for b, a in zip(bids, asks)]
    spreads = [abs(a - b) for a, b in zip(asks, bids)]
    current_price = mid_prices[-1]

    # Determine tick direction based on price movement
    deltas = []
    for i in range(1, n):
        if mid_prices[i] > mid_prices[i - 1]:
            deltas.append(1)  # uptick
        elif mid_prices[i] < mid_prices[i - 1]:
            deltas.append(-1)  # downtick
        else:
            deltas.append(0)  # zero-tick

    # Volume-weighted delta
    vol_deltas = []
    for i in range(min(n - 1, len(deltas))):
        vol_deltas.append(deltas[i] * volumes[i + 1])

    # Cumulative Volume Delta (last 50 bars)
    lookback = min(50, len(vol_deltas))
    cvd = sum(vol_deltas[-lookback:])
    cvd_total = sum(vol_deltas)

    # Imbalance ratio (last window)
    window = min(30, len(vol_deltas))
    recent_deltas = vol_deltas[-window:]
    buy_vol = sum(v for v in recent_deltas if v > 0)
    sell_vol = abs(sum(v for v in recent_deltas if v < 0))
    total_vol = buy_vol + sell_vol
    imbalance_ratio = buy_vol / max(total_vol, 1)

    # Absorption detection: high volume but price not moving
    price_range = max(mid_prices[-window:]) - min(mid_prices[-window:])
    price_range_pct = price_range / max(current_price, 0.0001) * 100
    vol_per_tick = sum(volumes[-window:]) / max(window, 1)
    avg_vol = sum(volumes) / max(n, 1)
    absorption_score = (vol_per_tick / max(avg_vol, 1)) / max(price_range_pct, 0.001)

    # Exhaustion: extreme imbalance + price slowing
    recent_cvd = sum(vol_deltas[-min(15, len(vol_deltas)):])
    prev_cvd = sum(vol_deltas[-min(30, len(vol_deltas)):-min(15, len(vol_deltas))]) if len(vol_deltas) >= 30 else 0
    cvd_slowing = abs(recent_cvd) < abs(prev_cvd) * 0.5 if prev_cvd != 0 else False
    extreme_imbalance = imbalance_ratio > 0.75 or imbalance_ratio < 0.25
    exhaustion_detected = extreme_imbalance and cvd_slowing

    # Generate signal
    if absorption_score > 3 and price_range_pct < 0.05:
        verdict = "ABSORPTION"
        signal = "reversal_soon"
        confidence = min(70, int(absorption_score * 15))
        reason = f"High volume ({vol_per_tick:.0f}/tick) with tiny range ({price_range_pct:.3f}%) — absorption"
    elif exhaustion_detected and imbalance_ratio > 0.7:
        verdict = "EXHAUSTION_BUY"
        signal = "sell"
        confidence = min(75, int(abs(cvd_slowing) * 30 + 40))
        reason = f"Buying exhaustion — CVD slowing ({prev_cvd:.0f} -> {recent_cvd:.0f})"
    elif exhaustion_detected and imbalance_ratio < 0.3:
        verdict = "EXHAUSTION_SELL"
        signal = "buy"
        confidence = min(75, int(abs(cvd_slowing) * 30 + 40))
        reason = f"Selling exhaustion — CVD slowing ({prev_cvd:.0f} -> {recent_cvd:.0f})"
    elif imbalance_ratio > 0.65:
        verdict = "BULLISH_FLOW"
        signal = "buy"
        confidence = int((imbalance_ratio - 0.5) * 200)
        reason = f"Strong buying pressure — imbalance ratio {imbalance_ratio:.0%}"
    elif imbalance_ratio < 0.35:
        verdict = "BEARISH_FLOW"
        signal = "sell"
        confidence = int((0.5 - imbalance_ratio) * 200)
        reason = f"Strong selling pressure — imbalance ratio {imbalance_ratio:.0%}"
    else:
        verdict = "NEUTRAL"
        signal = "neutral"
        confidence = 0
        reason = f"Balanced order flow — imbalance {imbalance_ratio:.0%}"

    return {
        "success": True,
        "symbol": "",
        "current_price": round(current_price, 5),
        "tick_count": n,
        "verdict": verdict,
        "signal": signal,
        "confidence_pct": min(confidence, 85),
        "reason": reason,
        "metrics": {
            "cvd": int(cvd),
            "cvd_total": int(cvd_total),
            "imbalance_ratio": round(imbalance_ratio, 3),
            "buy_volume": int(buy_vol),
            "sell_volume": int(sell_vol),
            "total_delta_volume": int(total_vol),
            "absorption_score": round(absorption_score, 1),
            "price_range_pct": round(price_range_pct, 3),
            "avg_spread": round(sum(spreads[-50:]) / min(50, len(spreads)), 5),
        },
    }


def get_imbalance(client, symbol: str) -> Dict[str, Any]:
    """Quick imbalance check — lighter version of check()."""
    result = check(client, symbol, lookback_ticks=100)
    if not result.get("success"):
        return result

    metrics = result.get("metrics", {})
    return {
        "success": True,
        "symbol": symbol,
        "imbalance_ratio": metrics.get("imbalance_ratio", 0.5),
        "cvd": metrics.get("cvd", 0),
        "signal": result.get("signal", "neutral"),
        "confidence": result.get("confidence_pct", 0),
    }


def integrate_with_conviction(client, symbol: str, conviction_decision: Dict[str, Any]) -> Dict[str, Any]:
    """Modulate conviction with order flow signal.

    If order flow strongly disagrees with conviction verdict,
    reduce confidence or override.
    """
    flow = get_imbalance(client, symbol)
    if not flow.get("success"):
        return conviction_decision

    decision = conviction_decision.get("decision", {})
    verdict = decision.get("verdict", "")
    confidence = decision.get("confidence_pct", 50)

    flow_signal = flow.get("signal", "neutral")
    flow_conf = flow.get("confidence", 0)

    if flow_signal == "buy" and "BUY" in verdict:
        decision["orderflow_boost"] = "agreement"
        decision["confidence_pct"] = min(confidence * 1.2, 99)
    elif flow_signal == "sell" and "SELL" in verdict:
        decision["orderflow_boost"] = "agreement"
        decision["confidence_pct"] = min(confidence * 1.2, 99)
    elif flow_signal == "buy" and "SELL" in verdict and flow_conf > 60:
        decision["orderflow_boost"] = "override"
        decision["verdict"] = "PASS"
        decision["confidence_pct"] = 0
        decision["orderflow_reason"] = "Order flow strongly disagrees — holding"
    elif flow_signal == "sell" and "BUY" in verdict and flow_conf > 60:
        decision["orderflow_boost"] = "override"
        decision["verdict"] = "PASS"
        decision["confidence_pct"] = 0
        decision["orderflow_reason"] = "Order flow strongly disagrees — holding"
    else:
        decision["orderflow_boost"] = "neutral"

    decision["orderflow_imbalance"] = flow.get("imbalance_ratio", 0.5)
    decision["orderflow_cvd"] = flow.get("cvd", 0)
    conviction_decision["decision"] = decision

    return conviction_decision
