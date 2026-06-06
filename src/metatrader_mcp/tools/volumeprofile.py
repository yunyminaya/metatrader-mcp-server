"""
VolumeProfile — perfil de volumen por precio (Market Profile).

Calcula:
  - Point of Control (POC): precio con mayor volumen
  - Value Area (VA): rango donde se negoció el 70% del volumen
  - High Volume Nodes (HVN): zonas de soporte/resistencia
  - Low Volume Nodes (LVN): zonas de breakout (huecos)

Útil para:
  - Saber dónde entrar (cerca de POC)
  - Saber dónde poner SL (detrás de HVN)
  - Saber dónde poner TP (en LVN opuesto)
  - Detectar si el precio está en zona de valor o fuera
"""
import logging
from typing import Dict, Any, List, Optional
import numpy as np
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def calculate(candles: List[Dict], num_bins: int = 24) -> Dict[str, Any]:
    """Calculate volume profile from candle data.

    Args:
        candles: list of dicts with open, high, low, close, volume (tick volume)
                num_bins: number of price levels to divide the range into

    Returns:
        POC, Value Area, HVNs, LVNs
    """
    if len(candles) < 10:
        return {"success": False, "error": "Need at least 10 candles"}

    # Extract prices and volumes
    highs = np.array([max(c.get('high', c.get('h', 0)), c.get('close', c.get('c', 0))) for c in candles])
    lows = np.array([min(c.get('low', c.get('l', 0)), c.get('open', c.get('o', 0))) for c in candles])
    volumes = np.array([c.get('volume', c.get('tick_volume', c.get('vol', 0))) for c in candles])

    if np.sum(volumes) == 0:
        # Fallback: use candle range as proxy
        volumes = np.ones(len(candles))

    price_min = np.min(lows)
    price_max = np.max(highs)
    price_range = price_max - price_min

    if price_range == 0:
        return {"success": False, "error": "No price range"}

    bin_size = price_range / num_bins

    # Allocate volume to price bins
    # For each candle, distribute volume across its high-low range
    bin_volume = np.zeros(num_bins)
    bin_prices = np.array([price_min + (i + 0.5) * bin_size for i in range(num_bins)])

    for i in range(len(candles)):
        h = highs[i]
        l = lows[i]
        v = volumes[i]

        if h == l:
            idx = min(int((h - price_min) / bin_size), num_bins - 1)
            bin_volume[idx] += v
        else:
            # Distribute proportionally
            c_low = max(0, (l - price_min) / bin_size)
            c_high = min(num_bins - 1, (h - price_min) / bin_size)
            n_bins_covered = max(1, c_high - c_low)
            bin_volume[int(c_low):int(c_high) + 1] += v / n_bins_covered

    # Point of Control (POC) — price with most volume
    poc_idx = np.argmax(bin_volume)
    poc_price = bin_prices[poc_idx]
    poc_volume = bin_volume[poc_idx]

    # Value Area — find price levels containing 70% of total volume
    # Start from POC and expand outward
    total_vol = np.sum(bin_volume)
    target_vol = total_vol * 0.70

    sorted_indices = [poc_idx]
    left = poc_idx - 1
    right = poc_idx + 1

    vol_sum = bin_volume[poc_idx]
    while vol_sum < target_vol and (left >= 0 or right < num_bins):
        if left >= 0 and (right >= num_bins or bin_volume[left] >= bin_volume[right]):
            sorted_indices.append(left)
            vol_sum += bin_volume[left]
            left -= 1
        elif right < num_bins:
            sorted_indices.append(right)
            vol_sum += bin_volume[right]
            right += 1
        else:
            break

    va_high_idx = max(sorted_indices)
    va_low_idx = min(sorted_indices)
    va_high = bin_prices[va_high_idx]
    va_low = bin_prices[va_low_idx]

    # HVNs: bins with volume >= 2x average
    avg_vol = total_vol / num_bins
    hvn_indices = np.where(bin_volume >= avg_vol * 2)[0]
    hvns = [round(float(bin_prices[i]), 5) for i in hvn_indices if i != poc_idx]

    # LVNs: bins with volume <= 0.3x average (gaps/low liquidity zones)
    lvn_indices = np.where(bin_volume <= avg_vol * 0.3)[0]
    lvns = [round(float(bin_prices[i]), 5) for i in lvn_indices]

    # Current price position relative to value area
    current_price = candles[-1].get('close', candles[-1].get('c', 0))
    if current_price > va_high:
        position = "above_value"
    elif current_price < va_low:
        position = "below_value"
    else:
        position = "in_value"

    # Advice
    if position == "below_value":
        advice = "potential_buy"  # Cheap vs value
    elif position == "above_value":
        advice = "potential_sell"  # Expensive vs value
    elif hvns:
        # If in value and near HVN, expect rejection
        near_hvn = any(abs(current_price - h) / current_price < 0.001 for h in hvns)
        advice = "wait_rejection" if near_hvn else "neutral"
    else:
        advice = "neutral"

    return {
        "success": True,
        "point_of_control": round(float(poc_price), 5),
        "poc_volume": round(float(poc_volume), 2),
        "value_area_high": round(float(va_high), 5),
        "value_area_low": round(float(va_low), 5),
        "value_area_width": round(float(va_high - va_low), 5),
        "total_volume": round(float(total_vol), 2),
        "current_price": round(float(current_price), 5),
        "price_position": position,
        "high_volume_nodes": hvns[:5],
        "low_volume_nodes": lvns[:5],
        "advice": advice,
        "interpretation": (
            f"Price is {position}. "
            f"POC at {poc_price:.5f}. "
            f"Value area: {va_low:.5f} - {va_high:.5f}. "
            f"{'HVNs at ' + str(hvns[:3]) if hvns else 'No strong HVNs'}. "
            f"{'LVNs (breakout zones) at ' + str(lvns[:3]) if lvns else 'No clear LVNs'}."
        ),
    }


def integrate_with_conviction(candles: List[Dict], conviction_decision: Dict[str, Any]) -> Dict[str, Any]:
    """Modulate conviction with volume profile.

    If price is below value area and conviction says BUY → strong signal (cheap).
    If price is above value area and conviction says BUY → risky.
    """
    vp = calculate(candles)
    if not vp.get("success"):
        return conviction_decision

    position = vp.get("price_position", "in_value")
    dec = conviction_decision.get("decision", {})
    conf = dec.get("confidence_pct", 0)
    v = dec.get("verdict", "")

    is_buy = "BUY" in v
    is_sell = "SELL" in v

    if is_buy and position == "below_value":
        dec["vp_boost"] = "price_below_value_cheap"
        dec["confidence_pct"] = min(conf * 1.25, 99)
    elif is_sell and position == "above_value":
        dec["vp_boost"] = "price_above_value_expensive"
        dec["confidence_pct"] = min(conf * 1.25, 99)
    elif is_buy and position == "above_value":
        dec["vp_boost"] = "price_above_value_risky"
        dec["confidence_pct"] = conf * 0.5
    elif is_sell and position == "below_value":
        dec["vp_boost"] = "price_below_value_risky"
        dec["confidence_pct"] = conf * 0.5

    # If near HVN, expect reversal
    if position == "in_value":
        for hvn in vp.get("high_volume_nodes", []):
            if abs(candles[-1]["close"] - hvn) / candles[-1]["close"] < 0.001:
                dec["vp_note"] = f"near_HVN_{hvn}_expect_rejection"
                break

    dec["volume_profile"] = {
        "poc": vp["point_of_control"],
        "value_area": [vp["value_area_low"], vp["value_area_high"]],
        "position": position,
    }
    conviction_decision["decision"] = dec
    return conviction_decision
