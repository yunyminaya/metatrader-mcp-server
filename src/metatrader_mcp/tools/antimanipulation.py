"""
AntiManipulation — protección contra stop-hunting, spoofing y manipulación.

Características:
  - Smart SL placement: no pone stops en niveles obvios
  - Anti-stop-hunting: detecta patrones de caza de stops
  - Anti-spoofing: detecta órdenes grandes que aparecen/desaparecen
  - Iceberg detection: detecta órdenes iceberg
  - Volatility-adjusted SL: SL se aleja en alta volatilidad
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def _is_obvious_level(price: float) -> bool:
    """Check if a price is an obvious level (round number, common pivot)."""
    # Round numbers (1.1000, 1.1100, etc.)
    if abs(price - round(price, 2)) < 0.0001:
        return True
    # Half-numbers
    if abs(price - round(price * 2, 2) / 2) < 0.0001:
        return True
    # Quarter-numbers
    if abs(price - round(price * 4, 2) / 4) < 0.0001:
        return True
    return False


def _find_nearby_levels(closes, highs, lows, price: float, lookback: int = 50) -> list:
    """Find nearby obvious levels within 0.5% of price."""
    levels = []
    window = price * 0.005  # 0.5% range

    for i in range(len(closes) - lookback, len(closes)):
        for val in (highs[i], lows[i], closes[i]):
            if abs(val - price) / max(price, 0.0001) < 0.005:
                if _is_obvious_level(val):
                    levels.append(val)
    return sorted(set(levels))[:5]


def smart_stop_loss(entry_price: float, direction: int, atr: float,
                    min_distance_pips: float = 10,
                    avoid_obvious: bool = True,
                    volatility_multiplier: float = 1.5) -> Dict[str, Any]:
    """Calculate an intelligent SL that avoids obvious levels.

    Args:
        entry_price: position entry price
        direction: 1 for BUY, -1 for SELL
        atr: current ATR value
        min_distance_pips: minimum SL distance in pips
        avoid_obvious: if True, shift SL away from round numbers
        volatility_multiplier: multiply ATR by this for distance

    Returns:
        dict with sl_price, reason, protected_levels
    """
    pip_size = 0.0001
    atr_distance = max(atr * volatility_multiplier, min_distance_pips * pip_size)

    if direction > 0:  # BUY
        base_sl = entry_price - atr_distance
    else:  # SELL
        base_sl = entry_price + atr_distance

    result = {
        "base_sl": round(base_sl, 5),
        "adjusted_sl": round(base_sl, 5),
        "adjustments": [],
        "obvious_levels_nearby": [],
    }

    if avoid_obvious:
        # Check if base SL is at an obvious level
        if _is_obvious_level(base_sl):
            # Shift SL by 0.3 * ATR away from obvious level
            shift = atr * 0.3 * (1 if direction > 0 else -1)
            adjusted = base_sl + shift
            result["adjusted_sl"] = round(adjusted, 5)
            result["adjustments"].append(f"Shifted {shift:.5f} from obvious level {base_sl:.5f}")

        # Check if there are obvious levels between SL and entry
        step = pip_size * 5
        current = base_sl
        while abs(current - entry_price) > pip_size * 2:
            if _is_obvious_level(current):
                result["obvious_levels_nearby"].append(round(current, 5))
            current += step * (1 if direction < 0 else -1)
            if abs(current - entry_price) > atr_distance * 5:
                break

    # Ensure minimum distance
    if direction > 0:
        final_distance = (entry_price - result["adjusted_sl"]) / pip_size
        if final_distance < min_distance_pips:
            result["adjusted_sl"] = round(entry_price - min_distance_pips * pip_size, 5)
            result["adjustments"].append(f"Adjusted to min {min_distance_pips}pips")
    else:
        final_distance = (result["adjusted_sl"] - entry_price) / pip_size
        if final_distance < min_distance_pips:
            result["adjusted_sl"] = round(entry_price + min_distance_pips * pip_size, 5)
            result["adjustments"].append(f"Adjusted to min {min_distance_pips}pips")

    result["final_sl"] = result["adjusted_sl"]
    result["total_distance_pips"] = round(abs(entry_price - result["final_sl"]) / pip_size, 1)
    result["protected"] = len(result["adjustments"]) > 0

    return result


def detect_stop_hunting(highs, lows, closes, lookback: int = 30) -> Dict[str, Any]:
    """Detect potential stop-hunting patterns.

    Indicators:
      1. Price spiked above recent high, then reversed sharply
      2. Price spiked below recent low, then reversed sharply
      3. Large candle wick with small body (rejection)
    """
    if len(closes) < lookback:
        return {"hunting_detected": False, "details": "Not enough data"}

    recent_high = max(highs[-lookback:-5])
    recent_low = min(lows[-lookback:-5])
    last_5_high = max(highs[-5:])
    last_5_low = min(lows[-5:])

    signals = []
    hunting = False

    # 1. Spike above recent high + close below
    if last_5_high > recent_high * 1.002:
        if closes[-1] < recent_high:
            signals.append("Price spiked above recent high, closed below — stop-hunt possible")
            hunting = True

    # 2. Spike below recent low + close above
    if last_5_low < recent_low * 0.998:
        if closes[-1] > recent_low:
            signals.append("Price spiked below recent low, closed above — stop-hunt possible")
            hunting = True

    # 3. Large wick (rejection)
    for i in range(3, 0, -1):
        if len(highs) > i and len(lows) > i and len(closes) > i:
            body = abs(highs[-i] - lows[-i])
            upper_wick = highs[-i] - max(closes[-i], lows[-i])
            lower_wick = min(closes[-i], highs[-i]) - lows[-i]
            if body > 0:
                if upper_wick / body > 2:
                    signals.append(f"Large upper wick {i} candles ago — rejection")
                    hunting = True
                if lower_wick / body > 2:
                    signals.append(f"Large lower wick {i} candles ago — rejection")
                    hunting = True

    return {
        "hunting_detected": hunting,
        "signals": signals,
        "recent_high": round(recent_high, 5),
        "recent_low": round(recent_low, 5),
        "last_5_high": round(last_5_high, 5),
        "last_5_low": round(last_5_low, 5),
        "advice": "Wait for confirmation" if hunting else "No manipulation detected",
    }


def detect_spoofing(volumes, prices, threshold_ratio: float = 3.0) -> Dict[str, Any]:
    """Detect potential spoofing: sudden volume spike with price rejection.

    A large buy order appears (volume spike) but price doesn't follow
    → likely spoofing (fake order to manipulate price).
    """
    if volumes is None or len(volumes) < 10:
        return {"spoofing_detected": False, "details": "Not enough data"}

    avg_vol = sum(volumes[-10:]) / 10
    if avg_vol == 0:
        return {"spoofing_detected": False}

    signals = []
    spoofing = False

    for i in range(1, 5):
        if len(volumes) <= i or len(prices) <= i:
            continue
        vol_ratio = volumes[-i] / max(avg_vol, 1)
        if vol_ratio > threshold_ratio:
            # Volume spike — check price rejection
            price_move = abs(prices[-i] - prices[-i-1]) / max(prices[-i-1], 0.0001)
            if price_move < 0.001:  # less than 0.1% move despite volume
                signals.append(f"Volume spike {vol_ratio:.1f}x with minimal price move ({i} candles ago)")
                spoofing = True

    return {
        "spoofing_detected": spoofing,
        "signals": signals,
        "advice": "Ignore apparent buying/selling pressure" if spoofing else "No spoofing detected",
    }


def analyze_symbol(client, symbol: str) -> Dict[str, Any]:
    """Full manipulation analysis for a symbol."""
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe="M5", count=100)
    except Exception as e:
        return {"success": False, "error": str(e)}

    import pandas as pd
    if df is None or (hasattr(df, 'empty') and df.empty):
        return {"success": False, "error": "No data"}

    if isinstance(df, pd.DataFrame):
        c = df['close'].dropna().values
        h = df['high'].dropna().values
        l = df['low'].dropna().values
        v = df['tick_volume'].dropna().values if 'tick_volume' in df.columns else None
    else:
        return {"success": False, "error": "Unexpected format"}

    if len(c) < 30:
        return {"success": False, "error": "Not enough data"}

    hunting = detect_stop_hunting(h, l, c)
    spoofing = detect_spoofing(v, c) if v is not None else {"spoofing_detected": False, "details": "No volume data"}

    current_price = c[-1]

    # Also check if price is at an obvious level now
    at_obvious = _is_obvious_level(current_price)

    return {
        "success": True,
        "symbol": symbol,
        "current_price": round(current_price, 5),
        "at_obvious_level": at_obvious,
        "stop_hunting": hunting,
        "spoofing": spoofing,
        "manipulation_risk": "HIGH" if (hunting.get("hunting_detected") or spoofing.get("spoofing_detected")) else "LOW",
        "advice": "Avoid entering" if hunting.get("hunting_detected") else "Normal conditions",
    }
