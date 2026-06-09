import logging
import math
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _digits(client, symbol: str) -> int:
    info = client.market.get_symbol_info(symbol)
    return info.get("digits", 5) if isinstance(info, dict) else 5


def _swing_points(df_sorted: pd.DataFrame) -> Tuple[List, List]:
    highs = df_sorted['high'].values
    lows = df_sorted['low'].values
    swing_highs = []
    swing_lows = []
    for i in range(1, len(df_sorted) - 1):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            swing_highs.append({"index": i, "price": highs[i]})
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            swing_lows.append({"index": i, "price": lows[i]})
    return swing_highs, swing_lows


def _check_ratio(a: float, b: float, target: float, tolerance: float = 0.05) -> bool:
    if b == 0:
        return False
    ratio = a / b
    return target - tolerance <= ratio <= target + tolerance


def detect_harmonic_patterns(client, symbol: str, timeframe: str = "H1", lookback: int = 200) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 50:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    sh, sl = _swing_points(df_sorted)
    if len(sh) < 5 or len(sl) < 5:
        return {"error": False, "message": "Not enough swing points for harmonic detection", "data": None}
    patterns = []
    def _harmony(swings, pattern_type):
        found = []
        for i in range(len(swings) - 4):
            try:
                x = swings[i]["price"]
                a = swings[i + 1]["price"]
                b = swings[i + 2]["price"]
                c = swings[i + 3]["price"]
                d = swings[i + 4]["price"]
            except IndexError:
                continue
            xa = abs(a - x)
            ab = abs(b - a)
            bc = abs(c - b)
            cd = abs(d - c)
            is_bearish = (a > x and b < a and c > b and d < c) or (a < x and b > a and c < b and d > c)
            if xa == 0 or ab == 0 or bc == 0:
                continue
            if pattern_type == "GARTLEY" and _check_ratio(ab, xa, 0.618) and _check_ratio(bc, ab, 0.886) and _check_ratio(cd, xa, 0.786):
                found.append({"pattern": "Gartley", "direction": "BULLISH" if d < c else "BEARISH", "completion": d, "x": x, "a": a, "b": b, "c": c, "d": d, "confidence": "HIGH" if _check_ratio(cd, xa, 0.786, 0.02) else "MEDIUM"})
            elif pattern_type == "BAT" and _check_ratio(ab, xa, 0.382, 0.05) and _check_ratio(bc, ab, 0.886) and _check_ratio(cd, xa, 0.886):
                found.append({"pattern": "Bat", "direction": "BULLISH" if d < c else "BEARISH", "completion": d, "x": x, "a": a, "b": b, "c": c, "d": d, "confidence": "HIGH" if _check_ratio(cd, xa, 0.886, 0.02) else "MEDIUM"})
            elif pattern_type == "CRAB" and _check_ratio(ab, xa, 0.382, 0.05) and _check_ratio(bc, ab, 1.618, 0.1) and _check_ratio(cd, xa, 1.618, 0.1):
                found.append({"pattern": "Crab", "direction": "BULLISH" if d < c else "BEARISH", "completion": d, "x": x, "a": a, "b": b, "c": c, "d": d, "confidence": "HIGH" if _check_ratio(cd, xa, 1.618, 0.05) else "MEDIUM"})
            elif pattern_type == "BUTTERFLY" and _check_ratio(ab, xa, 0.786) and _check_ratio(bc, ab, 0.886) and _check_ratio(cd, xa, 1.272, 0.1):
                found.append({"pattern": "Butterfly", "direction": "BULLISH" if d < c else "BEARISH", "completion": d, "x": x, "a": a, "b": b, "c": c, "d": d, "confidence": "HIGH" if _check_ratio(cd, xa, 1.272, 0.05) else "MEDIUM"})
            elif pattern_type == "SHARK" and _check_ratio(ab, xa, 0.5, 0.1) and _check_ratio(bc, ab, 1.0, 0.1) and _check_ratio(cd, xa, 0.886, 0.1):
                found.append({"pattern": "Shark", "direction": "BULLISH" if d < c else "BEARISH", "completion": d, "x": x, "a": a, "b": b, "c": c, "d": d, "confidence": "MEDIUM"})
            elif pattern_type == "CYPHER" and _check_ratio(ab, xa, 0.382, 0.1) and _check_ratio(bc, ab, 1.272, 0.15) and _check_ratio(cd, xa, 0.786, 0.1):
                found.append({"pattern": "Cypher", "direction": "BULLISH" if d < c else "BEARISH", "completion": d, "x": x, "a": a, "b": b, "c": c, "d": d, "confidence": "MEDIUM"})
        return found
    for swing_set, name in [(sh + sl, "MIXED")]:
        patterns.extend(_harmony(sorted(swing_set[:30], key=lambda s: s["index"]), "GARTLEY"))
        patterns.extend(_harmony(sorted(swing_set[:30], key=lambda s: s["index"]), "BAT"))
        patterns.extend(_harmony(sorted(swing_set[:30], key=lambda s: s["index"]), "CRAB"))
        patterns.extend(_harmony(sorted(swing_set[:30], key=lambda s: s["index"]), "BUTTERFLY"))
        patterns.extend(_harmony(sorted(swing_set[:30], key=lambda s: s["index"]), "SHARK"))
        patterns.extend(_harmony(sorted(swing_set[:30], key=lambda s: s["index"]), "CYPHER"))
    patterns = patterns[:10]
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df_sorted['close'].iloc[-1]
    d = _digits(client, symbol)
    nearest = min(patterns, key=lambda p: abs(p["completion"] - current)) if patterns else None
    return {
        "error": False,
        "message": f"{len(patterns)} harmonic patterns found" + (f" — nearest: {nearest['pattern']} {nearest['direction']} at {nearest['completion']:.{d}f}" if nearest else ""),
        "data": {
            "current_price": round(current, d),
            "patterns": [{"pattern": p["pattern"], "direction": p["direction"], "completion_price": round(p["completion"], d), "confidence": p["confidence"]} for p in patterns],
            "nearest_pattern": {"pattern": nearest["pattern"], "direction": nearest["direction"], "completion_price": round(nearest["completion"], d), "confidence": nearest["confidence"]} if nearest else None,
        }
    }


def gann_levels(client, symbol: str, timeframe: str = "D1", lookback: int = 100, method: str = "square") -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 30:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    high = df_sorted['high'].max()
    low = df_sorted['low'].min()
    range_price = high - low
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df_sorted['close'].iloc[-1]
    d = _digits(client, symbol)
    if method == "square":
        levels = {}
        for i in range(1, 13):
            levels[f"{i*45}deg_below_{i}"] = low + range_price * (1 - i / 12)
            levels[f"{i*45}deg_above_{i}"] = low + range_price * (1 + i / 12)
        fan_levels = {k: round(v, d) for k, v in levels.items() if v > low - range_price * 0.5 and v < high + range_price * 0.5}
    elif method == "fan":
        angles = [0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]
        fan_levels = {f"angle_{a:.3f}": round(low + range_price * a, d) for a in angles}
    else:
        return {"error": True, "message": f"Unknown method: {method}", "data": None}
    return {
        "error": False,
        "message": f"Gann {method} levels computed for {symbol}",
        "data": {"method": method, "timeframe": timeframe, "high": round(high, d), "low": round(low, d), "current_price": round(current, d), "levels": fan_levels}
    }


def elliott_wave(client, symbol: str, timeframe: str = "H1", lookback: int = 200) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 100:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    sh, sl = _swing_points(df_sorted)
    sorted_swings = sorted(sh + sl, key=lambda s: s["index"])
    if len(sorted_swings) < 8:
        return {"error": False, "message": "Not enough swings for wave count", "data": None}
    wave_count = 0
    current_wave = 0
    last_type = None
    waves = []
    for i in range(min(len(sorted_swings), 20)):
        is_sh = any(s["index"] == sorted_swings[i]["index"] for s in sh)
        wave_type = "IMPULSE_UP" if is_sh else "IMPULSE_DOWN"
        if last_type is None:
            current_wave += 1
            waves.append({"wave": current_wave, "type": wave_type, "price": sorted_swings[i]["price"]})
            last_type = wave_type
        elif wave_type != last_type:
            current_wave += 1
            waves.append({"wave": current_wave, "type": wave_type, "price": sorted_swings[i]["price"]})
            last_type = wave_type
    impulse_count = sum(1 for w in waves if "UP" in w["type"])
    corrective_count = sum(1 for w in waves if "DOWN" in w["type"])
    likely_position = "WAVE_3" if impulse_count == 2 else "WAVE_5" if impulse_count >= 4 else "CORRECTIVE_B" if corrective_count >= 2 else "EARLY_IMPULSE"
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df_sorted['close'].iloc[-1]
    d = _digits(client, symbol)
    wave_prices = [round(w["price"], d) for w in waves[-8:]]
    return {
        "error": False,
        "message": f"Elliott Wave: likely in {likely_position} ({len(waves)} swings counted)",
        "data": {
            "current_price": round(current, d),
            "likely_position": likely_position,
            "impulse_waves": impulse_count,
            "corrective_waves": corrective_count,
            "recent_waves": [{"wave": w["wave"], "type": w["type"][:3], "price": round(w["price"], d)} for w in waves[-8:]],
            "next_expected": "IMPULSE_UP" if impulse_count % 2 == 0 else "CORRECTIVE",
        }
    }


def renko_bricks(client, symbol: str, brick_size_pips: float = 10, lookback: int = 200, timeframe: str = "M5") -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 20:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    info = client.market.get_symbol_info(symbol)
    digits = info.get("digits", 5) if isinstance(info, dict) else 5
    pip_size = 10 ** -(digits - 1) if digits > 3 else 0.0001
    brick_size = brick_size_pips * pip_size
    bricks = []
    current_brick = None
    direction = None
    for _, row in df_sorted.iterrows():
        price = row['close']
        if current_brick is None:
            current_brick = price
            continue
        change = price - current_brick
        if abs(change) >= brick_size:
            brick_dir = "BULLISH" if change > 0 else "BEARISH"
            if direction is None or brick_dir == direction:
                num_bricks = int(abs(change) / brick_size)
                for _ in range(min(num_bricks, 5)):
                    bricks.append({"direction": brick_dir, "price": round(current_brick + (brick_size if brick_dir == "BULLISH" else -brick_size), digits)})
                    current_brick += (brick_size if brick_dir == "BULLISH" else -brick_size)
                direction = brick_dir
            else:
                if abs(change) >= brick_size * 2:
                    direction = brick_dir
                    num_bricks = int(abs(change) / brick_size)
                    for _ in range(min(num_bricks, 3)):
                        bricks.append({"direction": brick_dir, "price": round(current_brick + (brick_size if brick_dir == "BULLISH" else -brick_size), digits)})
                        current_brick += (brick_size if brick_dir == "BULLISH" else -brick_size)
    last_bricks = bricks[-10:] if bricks else []
    bullish_bricks = sum(1 for b in bricks if b["direction"] == "BULLISH") if bricks else 0
    bearish_bricks = sum(1 for b in bricks if b["direction"] == "BEARISH") if bricks else 0
    trend = "BULLISH" if bullish_bricks >= bearish_bricks * 2 else "BEARISH" if bearish_bricks >= bullish_bricks * 2 else "NEUTRAL"
    return {
        "error": False,
        "message": f"Renko trend: {trend} ({len(bricks)} bricks)",
        "data": {
            "brick_size_pips": brick_size_pips,
            "brick_size": round(brick_size, digits),
            "total_bricks": len(bricks),
            "bullish_bricks": bullish_bricks,
            "bearish_bricks": bearish_bricks,
            "trend": trend,
            "recent_bricks": [{"direction": b["direction"], "price": b["price"]} for b in last_bricks],
        }
    }
