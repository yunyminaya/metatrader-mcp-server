"""
Patterns — 32 patrones de velas japonesas con scoring estadístico.

Clasifica cada patrón por:
  - Nombre
  - Dirección (bullish / bearish)
  - Fuerza (1-5)
  - Confiabilidad histórica

Útil como filtro de entrada: si convicción dice BUY
Y hay patrón bullish → confianza extra.
Si convicción dice BUY pero hay patrón bearish → PASS.
"""
import logging
from typing import List, Dict, Any, Optional
import numpy as np

logger = logging.getLogger(__name__)

# ── Confiabilidad histórica aproximada (basada en estudios) ─────────────────────
_RELIABILITY = {
    "bullish_engulfing": 0.63, "bearish_engulfing": 0.61,
    "morning_star": 0.72, "evening_star": 0.69,
    "three_white_soldiers": 0.68, "three_black_crows": 0.65,
    "hammer": 0.58, "shooting_star": 0.56,
    "doji": 0.50, "long_legged_doji": 0.52,
    "dragonfly_doji": 0.55, "gravestone_doji": 0.53,
    "harami_bullish": 0.55, "harami_bearish": 0.54,
    "piercing_line": 0.62, "dark_cloud_cover": 0.60,
    "tweezers_top": 0.57, "tweezers_bottom": 0.58,
    "marubozu_bullish": 0.60, "marubozu_bearish": 0.59,
    "spinning_top": 0.48,
    "abandoned_baby_bullish": 0.74, "abandoned_baby_bearish": 0.71,
    "three_inside_up": 0.64, "three_inside_down": 0.62,
    "morning_doji_star": 0.73, "evening_doji_star": 0.70,
    "hanging_man": 0.54,
    "counter_attack_bullish": 0.60, "counter_attack_bearish": 0.58,
    "rising_three": 0.62, "falling_three": 0.60,
}


def _body_size(open_p, close_p):
    return abs(close_p - open_p)


def _total_range(high, low):
    return high - low if high > low else 0.001


def _is_bullish(open_p, close_p):
    return close_p > open_p


def _upper_shadow(high, open_p, close_p):
    return high - max(open_p, close_p)


def _lower_shadow(low, open_p, close_p):
    return min(open_p, close_p) - low


def _avg_body(prices):
    return np.mean([abs(p[i]['close'] - p[i]['open']) for i in range(len(p))])


def detect_all(candles: List[Dict]) -> Dict[str, Any]:
    """Detect ALL candle patterns in the last N candles.

    Args:
        candles: list of dicts with o, h, l, c keys
                e.g. [{"open":1.1, "high":1.12, "low":1.09, "close":1.11}, ...]

    Returns:
        detected patterns sorted by strength
    """
    if len(candles) < 3:
        return {"success": False, "error": "Need at least 3 candles", "patterns": []}

    # Parse into arrays
    c = candles
    o = np.array([x.get('open', x.get('o', 0)) for x in c])
    h = np.array([x.get('high', x.get('h', 0)) for x in c])
    l = np.array([x.get('low', x.get('l', 0)) for x in c])
    cl = np.array([x.get('close', x.get('c', 0)) for x in c])

    patterns = []

    # ── 1. SINGLE CANDLE PATTERNS ──

    # Doji: open ≈ close (within 5% of range)
    for i in range(len(c)):
        body = _body_size(o[i], cl[i])
        rng = _total_range(h[i], l[i])
        if rng == 0:
            continue
        if body / rng < 0.05:
            us = _upper_shadow(h[i], o[i], cl[i])
            ls = _lower_shadow(l[i], o[i], cl[i])
            if abs(us - ls) / rng < 0.1:
                patterns.append(_p("doji", "neutral", 2, i))
            elif us > 2 * ls:
                patterns.append(_p("gravestone_doji", "bearish", 3, i))
            elif ls > 2 * us:
                patterns.append(_p("dragonfly_doji", "bullish", 3, i))
            else:
                patterns.append(_p("long_legged_doji", "neutral", 2, i))

    # Hammer: small body, long lower shadow (2x body), little upper shadow
    # Appears in downtrend
    for i in range(1, len(c)):
        body = _body_size(o[i], cl[i])
        rng = _total_range(h[i], l[i])
        us = _upper_shadow(h[i], o[i], cl[i])
        ls = _lower_shadow(l[i], o[i], cl[i])
        if body > 0 and rng > 0:
            if ls >= 2 * body and us <= body * 0.3:
                if cl[i] > o[i]:
                    patterns.append(_p("hammer", "bullish", 4, i))
                else:
                    patterns.append(_p("hanging_man", "bearish", 3, i))

    # Shooting Star: small body, long upper shadow, little lower shadow
    for i in range(1, len(c)):
        body = _body_size(o[i], cl[i])
        rng = _total_range(h[i], l[i])
        us = _upper_shadow(h[i], o[i], cl[i])
        ls = _lower_shadow(l[i], o[i], cl[i])
        if body > 0 and rng > 0 and ls <= body * 0.3 and us >= 2 * body:
            patterns.append(_p("shooting_star", "bearish", 4, i))

    # Marubozu: no shadows, full body
    for i in range(len(c)):
        body = _body_size(o[i], cl[i])
        rng = _total_range(h[i], l[i])
        if body > 0 and rng > 0:
            us = _upper_shadow(h[i], o[i], cl[i])
            ls = _lower_shadow(l[i], o[i], cl[i])
            if us / rng < 0.05 and ls / rng < 0.05:
                if _is_bullish(o[i], cl[i]):
                    patterns.append(_p("marubozu_bullish", "bullish", 3, i))
                else:
                    patterns.append(_p("marubozu_bearish", "bearish", 3, i))

    # Spinning Top: small body, shadows on both sides
    for i in range(len(c)):
        body = _body_size(o[i], cl[i])
        rng = _total_range(h[i], l[i])
        if body > 0 and rng > 0:
            us = _upper_shadow(h[i], o[i], cl[i])
            ls = _lower_shadow(l[i], o[i], cl[i])
            if body / rng < 0.3 and us > body and ls > body:
                patterns.append(_p("spinning_top", "neutral", 1, i))

    # ── 2. TWO CANDLE PATTERNS ──
    for i in range(1, len(c)):
        j = i - 1

        # Engulfing
        body1 = _body_size(o[j], cl[j])
        body2 = _body_size(o[i], cl[i])
        if body1 > 0 and body2 > 0:
            if _is_bearish(o[j], cl[j]) and _is_bullish(o[i], cl[i]):
                if cl[i] > o[j] and o[i] < cl[j]:
                    patterns.append(_p("bullish_engulfing", "bullish", 5, i))
            elif _is_bullish(o[j], cl[j]) and _is_bearish(o[i], cl[i]):
                if o[i] > cl[j] and cl[i] < o[j]:
                    patterns.append(_p("bearish_engulfing", "bearish", 5, i))

        # Harami (opposite of engulfing)
        if body1 > 0 and body2 > 0 and body2 <= body1 * 0.6:
            if _is_bearish(o[j], cl[j]) and _is_bullish(o[i], cl[i]):
                patterns.append(_p("harami_bullish", "bullish", 3, i))
            elif _is_bullish(o[j], cl[j]) and _is_bearish(o[i], cl[i]):
                patterns.append(_p("harami_bearish", "bearish", 3, i))

        # Piercing Line (bullish reversal)
        if _is_bearish(o[j], cl[j]) and _is_bullish(o[i], cl[i]):
            mid = o[j] - (o[j] - cl[j]) / 2
            if cl[i] > mid and o[i] < cl[j]:
                patterns.append(_p("piercing_line", "bullish", 4, i))

        # Dark Cloud Cover (bearish reversal)
        if _is_bullish(o[j], cl[j]) and _is_bearish(o[i], cl[i]):
            mid = o[j] + (cl[j] - o[j]) / 2
            if cl[i] < mid and o[i] > cl[j]:
                patterns.append(_p("dark_cloud_cover", "bearish", 4, i))

        # Tweezers Top / Bottom
        if abs(h[i] - h[j]) / max(h[i], h[j]) < 0.001:
            if _is_bullish(o[j], cl[j]) and _is_bearish(o[i], cl[i]):
                patterns.append(_p("tweezers_top", "bearish", 3, i))
        if abs(l[i] - l[j]) / max(l[i], l[j]) < 0.001:
            if _is_bearish(o[j], cl[j]) and _is_bullish(o[i], cl[i]):
                patterns.append(_p("tweezers_bottom", "bullish", 3, i))

        # Counter-attack lines
        if cl[i] == cl[j]:
            if _is_bearish(o[j], cl[j]) and _is_bullish(o[i], cl[i]):
                patterns.append(_p("counter_attack_bullish", "bullish", 3, i))
            elif _is_bullish(o[j], cl[j]) and _is_bearish(o[i], cl[i]):
                patterns.append(_p("counter_attack_bearish", "bearish", 3, i))

    # ── 3. THREE CANDLE PATTERNS ──
    for i in range(2, len(c)):
        a, b, cur = i - 2, i - 1, i

        # Morning Star / Evening Star
        if _is_bearish(o[a], cl[a]) and _is_bullish(o[cur], cl[cur]):
            body_b = _body_size(o[b], cl[b])
            if body_b < _avg_body([c[a], c[cur]]) * 0.5:
                if cl[cur] > o[a] - (o[a] - cl[a]) / 2:
                    patterns.append(_p("morning_star", "bullish", 5, cur))
                    # Check if middle is doji
                    if body_b / _total_range(h[b], l[b]) < 0.05:
                        patterns.append(_p("morning_doji_star", "bullish", 5, cur))

        if _is_bullish(o[a], cl[a]) and _is_bearish(o[cur], cl[cur]):
            body_b = _body_size(o[b], cl[b])
            if body_b < _avg_body([c[a], c[cur]]) * 0.5:
                if cl[cur] < o[a] + (cl[a] - o[a]) / 2:
                    patterns.append(_p("evening_star", "bearish", 5, cur))
                    if body_b / _total_range(h[b], l[b]) < 0.05:
                        patterns.append(_p("evening_doji_star", "bearish", 5, cur))

        # Abandoned Baby (gap on both sides of doji)
        if _is_bearish(o[a], cl[a]) and _is_bullish(o[cur], cl[cur]):
            body_b = _body_size(o[b], cl[b])
            rng_b = _total_range(h[b], l[b])
            if body_b / rng_b < 0.05:
                if h[b] < l[a] and h[b] < l[cur]:
                    patterns.append(_p("abandoned_baby_bullish", "bullish", 5, cur))

        if _is_bullish(o[a], cl[a]) and _is_bearish(o[cur], cl[cur]):
            body_b = _body_size(o[b], cl[b])
            rng_b = _total_range(h[b], l[b])
            if body_b / rng_b < 0.05:
                if l[b] > h[a] and l[b] > h[cur]:
                    patterns.append(_p("abandoned_baby_bearish", "bearish", 5, cur))

        # Three White Soldiers / Three Black Crows
        if all(_is_bullish(o[i], cl[i]) for i in range(a, cur + 1)):
            if all(cl[i] > cl[i - 1] and o[i] > o[i - 1] for i in range(b, cur + 1)):
                if cl[cur] > cl[b] > cl[a] and o[cur] > o[b] > o[a]:
                    patterns.append(_p("three_white_soldiers", "bullish", 4, cur))

        if all(_is_bearish(o[i], cl[i]) for i in range(a, cur + 1)):
            if all(cl[i] < cl[i - 1] and o[i] < o[i - 1] for i in range(b, cur + 1)):
                if cl[cur] < cl[b] < cl[a] and o[cur] < o[b] < o[a]:
                    patterns.append(_p("three_black_crows", "bearish", 4, cur))

        # Three Inside Up / Down
        if _is_bearish(o[a], cl[a]) and _is_bullish(o[cur], cl[cur]):
            body_a = _body_size(o[a], cl[a])
            body_b = _body_size(o[b], cl[b])
            body_c = _body_size(o[cur], cl[cur])
            if body_b <= body_a * 0.6 and cl[cur] > o[a]:
                patterns.append(_p("three_inside_up", "bullish", 4, cur))

        if _is_bullish(o[a], cl[a]) and _is_bearish(o[cur], cl[cur]):
            body_a = _body_size(o[a], cl[a])
            body_b = _body_size(o[b], cl[b])
            body_c = _body_size(o[cur], cl[cur])
            if body_b <= body_a * 0.6 and cl[cur] < o[a]:
                patterns.append(_p("three_inside_down", "bearish", 4, cur))

        # Rising Three / Falling Three
        if _is_bullish(o[a], cl[a]) and _is_bullish(o[cur], cl[cur]):
            if all(_is_bearish(o[i], cl[i]) for i in range(b, cur)):
                if all(x < cl[a] for x in cl[b:cur]) and cl[cur] > h[a]:
                    patterns.append(_p("rising_three", "bullish", 4, cur))

        if _is_bearish(o[a], cl[a]) and _is_bearish(o[cur], cl[cur]):
            if all(_is_bullish(o[i], cl[i]) for i in range(b, cur)):
                if all(x > cl[a] for x in cl[b:cur]) and cl[cur] < l[a]:
                    patterns.append(_p("falling_three", "bearish", 4, cur))

    # Deduplicate: keep strongest pattern per index
    seen = {}
    for p in patterns:
        key = (p["index"], p["name"])
        if key not in seen or p["strength"] > seen[key]["strength"]:
            seen[key] = p
    patterns = list(seen.values())

    # Sort by strength desc
    patterns.sort(key=lambda x: x["strength"], reverse=True)

    # Build overall verdict
    bullish_count = sum(1 for p in patterns if p["direction"] == "bullish")
    bearish_count = sum(1 for p in patterns if p["direction"] == "bearish")

    if bullish_count > bearish_count + 1:
        verdict = "bullish"
    elif bearish_count > bullish_count + 1:
        verdict = "bearish"
    else:
        verdict = "neutral"

    strongest = patterns[0] if patterns else None
    strongest_reliability = _RELIABILITY.get(strongest["name"], 0.5) if strongest else 0

    return {
        "success": True,
        "total_patterns": len(patterns),
        "bullish_patterns": bullish_count,
        "bearish_patterns": bearish_count,
        "verdict": verdict,
        "strongest": strongest,
        "strongest_reliability": round(strongest_reliability, 3),
        "patterns": [{
            "name": p["name"],
            "direction": p["direction"],
            "strength": p["strength"],
            "index": p["index"],
            "reliability": _RELIABILITY.get(p["name"], 0.5),
        } for p in patterns[:10]],  # Top 10
        "advice": (
            "strong_buy" if bullish_count >= 3 and strongest_reliability > 0.6
            else "buy" if verdict == "bullish"
            else "strong_sell" if bearish_count >= 3 and strongest_reliability > 0.6
            else "sell" if verdict == "bearish"
            else "neutral"
        ),
    }


def _p(name, direction, strength, index):
    return {"name": name, "direction": direction, "strength": strength, "index": int(index)}


def _is_bearish(open_p, close_p):
    return close_p <= open_p


def combine_with_conviction(candles: List[Dict], conviction_decision: Dict[str, Any]) -> Dict[str, Any]:
    """Modulate conviction decision with candle patterns.

    If conviction says BUY and patterns confirm → boost confidence.
    If conviction says BUY but patterns show bearish reversal → PASS.
    """
    pat = detect_all(candles)
    if not pat.get("success"):
        return conviction_decision

    verdict = pat.get("verdict", "neutral")
    advice = pat.get("advice", "neutral")
    dec = conviction_decision.get("decision", {})
    conf = dec.get("confidence_pct", 0)
    v = dec.get("verdict", "")

    is_bullish_signal = "BUY" in v
    is_bearish_signal = "SELL" in v

    if is_bullish_signal and verdict == "bullish":
        dec["patterns_boost"] = "confirmation"
        dec["confidence_pct"] = min(conf * 1.2, 99)
        dec["verdict"] = "STRONG_BUY"
    elif is_bearish_signal and verdict == "bearish":
        dec["patterns_boost"] = "confirmation"
        dec["confidence_pct"] = min(conf * 1.2, 99)
        dec["verdict"] = "STRONG_SELL"
    elif is_bullish_signal and verdict == "bearish" and pat.get("strongest_reliability", 0) > 0.55:
        dec["patterns_boost"] = "conflict_bearish"
        dec["confidence_pct"] = conf * 0.4
        if dec["confidence_pct"] < 50:
            dec["verdict"] = "PASS"
    elif is_bearish_signal and verdict == "bullish" and pat.get("strongest_reliability", 0) > 0.55:
        dec["patterns_boost"] = "conflict_bullish"
        dec["confidence_pct"] = conf * 0.4
        if dec["confidence_pct"] < 50:
            dec["verdict"] = "PASS"

    dec["candle_patterns"] = {
        "verdict": verdict,
        "patterns_found": pat["total_patterns"],
        "strongest": pat.get("strongest"),
    }
    conviction_decision["decision"] = dec
    return conviction_decision
