"""
Conviction v2 — señal de trading multi-estrategia para MT5.

Ahora incluye:
  - 10 indicadores (RSI, MACD, MA cross, BB, SR, ADX, Stochastic, ATR, Volume, Momentum)
  - Divergence detection (RSI + MACD)
  - Multi-timeframe confluence (H1 / H4 / D1 alignment)
  - Session filter (London, NY, Asian)
  - Spread guard
  - Kelly-adjusted position sizing
  - Verdict BUY/SELL/PASS con confianza 0-99
"""
import logging
import math
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════════════════
# Helper: extract price series
# ════════════════════════════════════════════════════════════════════════════════

def _series(df, field="close"):
    if df is None or (hasattr(df, 'empty') and df.empty):
        return None
    try:
        if isinstance(df, pd.DataFrame) and field in df.columns:
            return df[field].dropna().values
        return None
    except Exception:
        return None


def _ohlc(df):
    """Return (closes, highs, lows, volumes) or (None,...)"""
    if df is None or (hasattr(df, 'empty') and df.empty):
        return None, None, None, None
    try:
        if isinstance(df, pd.DataFrame):
            c = _series(df, "close")
            h = _series(df, "high")
            l = _series(df, "low")
            v = _series(df, "tick_volume") if "tick_volume" in df.columns else _series(df, "volume")
            return c, h, l, v
    except Exception:
        pass
    return None, None, None, None


# ════════════════════════════════════════════════════════════════════════════════
# Individual indicators
# ════════════════════════════════════════════════════════════════════════════════

def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50
    deltas = [closes[i+1] - closes[i] for i in range(len(closes)-1)]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    return 100 - (100 / (1 + avg_gain / avg_loss))


def _ma_cross(closes, fast=5, slow=20):
    if len(closes) < slow + 1:
        return 0
    fast_ma = sum(closes[-fast:]) / fast
    slow_ma = sum(closes[-slow:]) / slow
    fast_prev = sum(closes[-(fast+1):-1]) / fast
    slow_prev = sum(closes[-(slow+1):-1]) / slow
    if fast_prev <= slow_prev and fast_ma > slow_ma:
        return 1
    if fast_prev >= slow_prev and fast_ma < slow_ma:
        return -1
    return 0.5 if fast_ma > slow_ma else -0.5


def _macd(closes, fast=12, slow=26, signal=9):
    if len(closes) < slow + signal + 1:
        return 0
    def ema(data, period):
        mult = 2 / (period + 1)
        result = [data[0]]
        for i in range(1, len(data)):
            result.append((data[i] - result[-1]) * mult + result[-1])
        return result
    macd_line = [ema(closes[:i+1], fast)[-1] - ema(closes[:i+1], slow)[-1] for i in range(slow, len(closes))]
    if len(macd_line) < signal + 1:
        return 0
    sig = ema(macd_line, signal)
    if len(sig) < 2:
        return 0
    # histogram direction
    hist = macd_line[-1] - sig[-1]
    hist_prev = macd_line[-2] - sig[-2]
    if len(macd_line) >= 3 and len(sig) >= 3:
        hist_prev2 = macd_line[-3] - sig[-3]
        if hist > 0 and hist > hist_prev > hist_prev2:
            return 1.5  # accelerating bullish
        if hist < 0 and hist < hist_prev < hist_prev2:
            return -1.5  # accelerating bearish
    if sig[-2] <= macd_line[-2] and sig[-1] > macd_line[-1]:
        return -1
    if sig[-2] >= macd_line[-2] and sig[-1] < macd_line[-1]:
        return 1
    return 0.5 if macd_line[-1] > sig[-1] else -0.5


def _bb_position(closes, period=20):
    if len(closes) < period:
        return 0.5
    sma = sum(closes[-period:]) / period
    var = sum((c - sma)**2 for c in closes[-period:]) / period
    std = math.sqrt(var)
    if std == 0:
        return 0.5
    current = closes[-1]
    if current >= sma + 2 * std:
        return 1
    if current <= sma - 2 * std:
        return -1
    return (current - sma) / std


def _adx(closes, highs, lows, period=14):
    """ADX — trend strength. 0-100. >25 = trending."""
    if len(closes) < period * 2:
        return 0
    trs = []
    plus_dm = []
    minus_dm = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
        up = highs[i] - highs[i-1]
        down = lows[i-1] - lows[i]
        plus_dm.append(up if up > down and up > 0 else 0)
        minus_dm.append(down if down > up and down > 0 else 0)
    if len(trs) < period:
        return 0
    atr = sum(trs[-period:]) / period
    if atr == 0:
        return 0
    avg_plus = sum(plus_dm[-period:]) / period
    avg_minus = sum(minus_dm[-period:]) / period
    plus_di = avg_plus / atr * 100
    minus_di = avg_minus / atr * 100
    dx = abs(plus_di - minus_di) / max(plus_di + minus_di, 0.001) * 100
    return dx


def _stochastic(closes, highs, lows, period=14, k_smooth=3):
    """Stochastic %K + %D. Returns (k, d)."""
    if len(closes) < period + k_smooth:
        return 50, 50
    ks = []
    for i in range(len(closes) - period + 1):
        hh = max(highs[i:i+period])
        ll = min(lows[i:i+period])
        if hh == ll:
            ks.append(50)
        else:
            ks.append((closes[i+period-1] - ll) / (hh - ll) * 100)
    k = sum(ks[-k_smooth:]) / k_smooth if len(ks) >= k_smooth else 50
    d = sum(ks[-(k_smooth+1):-1]) / k_smooth if len(ks) > k_smooth else k
    return k, d


def _momentum(closes, period=10):
    """Rate of change."""
    if len(closes) < period + 1:
        return 0
    return (closes[-1] - closes[-period]) / closes[-period] * 100


def _atr(highs, lows, closes, period=14):
    if len(closes) < period + 1:
        return 0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    return sum(trs[-period:]) / min(period, len(trs)) if trs else 0


def _support_resistance(highs, lows, closes):
    if len(highs) < 20:
        return 0
    recent_low = min(lows[-10:])
    recent_high = max(highs[-10:])
    current = closes[-1]
    if current <= recent_low * 1.005:
        return -1
    if current >= recent_high * 0.995:
        return 1
    return 0


# ════════════════════════════════════════════════════════════════════════════════
# Session filter
# ════════════════════════════════════════════════════════════════════════════════

_SESSIONS = {
    "london_open": (7, 9),  # UTC
    "london_close": (15, 17),
    "ny_open": (12, 14),
    "ny_close": (20, 22),
    "asian": (23, 2),
    "overlap_london_ny": (12, 15),
}

def _active_sessions():
    """Returns list of active trading sessions based on UTC hour."""
    now = datetime.now(timezone.utc)
    h = now.hour
    active = []
    if 7 <= h < 9:
        active.append("london_open")
    if 6 <= h < 17:
        active.append("london")
    if 12 <= h < 21:
        active.append("new_york")
    if 12 <= h < 15:
        active.append("london_ny_overlap")
    if 0 <= h < 6 or h >= 22:
        active.append("asian")
    return active


def _session_score():
    """Returns multiplier 0.0-1.0 based on session quality."""
    active = _active_sessions()
    if "london_ny_overlap" in active:
        return 1.0
    if "london" in active or "new_york" in active:
        return 0.8
    if "london_open" in active:
        return 0.7
    return 0.4  # Asian session or weekend


# ════════════════════════════════════════════════════════════════════════════════
# Multi-timeframe confluence
# ════════════════════════════════════════════════════════════════════════════════

def _mtf_alignment(client, symbol: str) -> Dict[str, Any]:
    """Check trend alignment across D1, H4, H1. Returns bias -1 to 1."""
    tf_bias = {}
    for tf in ["D1", "H4", "H1"]:
        try:
            df = client.market.get_candles_latest(symbol_name=symbol, timeframe=tf, count=100)
            c = _series(df, "close")
            if c is None or len(c) < 30:
                tf_bias[tf] = 0
                continue
            # Direction: price vs MA50
            ma50 = sum(c[-50:]) / 50 if len(c) >= 50 else sum(c[-20:]) / 20
            tf_bias[tf] = 1 if c[-1] > ma50 else (-1 if c[-1] < ma50 else 0)
        except Exception:
            tf_bias[tf] = 0
    # Weighted sum: D1=3, H4=2, H1=1
    alignment = tf_bias.get("D1", 0) * 3 + tf_bias.get("H4", 0) * 2 + tf_bias.get("H1", 0)
    max_alignment = 6
    return {
        "alignment": round(alignment / max_alignment * 100, 0),
        "bias": alignment,
        "d1": tf_bias.get("D1", 0),
        "h4": tf_bias.get("H4", 0),
        "h1": tf_bias.get("H1", 0),
    }


# ════════════════════════════════════════════════════════════════════════════════
# Spread guard
# ════════════════════════════════════════════════════════════════════════════════

def _spread_ok(client, symbol: str, max_spread_pips: float = 15) -> Dict[str, Any]:
    """Check spread. Returns {'ok': bool, 'spread': pips}."""
    try:
        price = client.market.get_symbol_price(symbol_name=symbol)
        if not price:
            return {"ok": False, "spread": 999, "error": "No price"}
        spread = price.get("spread", 999)
        return {"ok": spread <= max_spread_pips, "spread": spread}
    except Exception as e:
        return {"ok": False, "spread": 999, "error": str(e)}


# ════════════════════════════════════════════════════════════════════════════════
# Main decision function
# ════════════════════════════════════════════════════════════════════════════════

def decide(client, symbol: str, timeframe: str = "H1", bankroll: float = 1000,
           max_spread_pips: float = 15, use_mtf: bool = True,
           min_mtf_alignment: float = 50) -> Dict[str, Any]:
    """Analiza UN símbolo con 10+ indicadores.

    Returns:
        BUY / SELL / PASS + confidence 0-99 + lot size sugerido
    """
    # Spread check first (cheap)
    spread = _spread_ok(client, symbol, max_spread_pips)
    if not spread["ok"]:
        return {
            "success": False,
            "error": f"Spread too high: {spread.get('spread', '?')} pips (max: {max_spread_pips})",
        }

    # Fetch H1 candles
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=200)
    except Exception as e:
        return {"success": False, "error": f"Cannot fetch data: {e}"}

    closes, highs, lows, volumes = _ohlc(df)
    if closes is None or len(closes) < 50:
        return {"success": False, "error": "Not enough candle data"}

    # Multi-timeframe confluence
    mtf = _mtf_alignment(client, symbol) if use_mtf else {"alignment": 50, "bias": 0}
    if use_mtf and mtf["alignment"] < min_mtf_alignment:
        return {
            "success": True,
            "decision": {
                "symbol": symbol,
                "timeframe": timeframe,
                "verdict": "PASS",
                "confidence_pct": 0,
                "reason": f"MTF alignment {mtf['alignment']}% < {min_mtf_alignment}%",
                "mtf": mtf,
                "spread_pips": spread["spread"],
            },
        }

    # Session score
    session_mult = _session_score()

    result = {
        "symbol": symbol,
        "timeframe": timeframe,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "current_price": round(closes[-1], 5),
        "spread_pips": spread["spread"],
        "mtf": mtf,
        "sessions": _active_sessions(),
        "session_quality": round(session_mult * 100, 0),
        "indicators": {},
        "scores": [],
        "verdict": "PASS",
        "confidence_pct": 0,
    }

    scores = []

    # ── RSI ──
    rsi_val = _rsi(closes)
    result["indicators"]["rsi"] = round(rsi_val, 1)
    if rsi_val < 25:
        scores.append(25)
        result["indicators"]["rsi_signal"] = "oversold_extreme"
    elif rsi_val < 35:
        scores.append(18)
        result["indicators"]["rsi_signal"] = "oversold"
    elif rsi_val > 75:
        scores.append(-25)
        result["indicators"]["rsi_signal"] = "overbought_extreme"
    elif rsi_val > 65:
        scores.append(-18)
        result["indicators"]["rsi_signal"] = "overbought"
    elif rsi_val < 45:
        scores.append(8)
        result["indicators"]["rsi_signal"] = "bullish"
    elif rsi_val > 55:
        scores.append(-8)
        result["indicators"]["rsi_signal"] = "bearish"
    else:
        result["indicators"]["rsi_signal"] = "neutral"

    # ── MA Cross ──
    ma_cross = _ma_cross(closes)
    result["indicators"]["ma_cross"] = ma_cross
    if ma_cross == 1:
        scores.append(20)
    elif ma_cross == -1:
        scores.append(-20)
    else:
        scores.append(ma_cross * 12)

    # ── MACD ──
    macd_sig = _macd(closes)
    result["indicators"]["macd"] = macd_sig
    if macd_sig >= 1.5:
        scores.append(22)
    elif macd_sig <= -1.5:
        scores.append(-22)
    elif macd_sig == 1:
        scores.append(15)
    elif macd_sig == -1:
        scores.append(-15)
    else:
        scores.append(int(macd_sig * 12))

    # ── Bollinger ──
    bb = _bb_position(closes)
    result["indicators"]["bb_z_score"] = round(bb, 2)
    if bb >= 2:
        scores.append(-20)
    elif bb <= -2:
        scores.append(20)
    elif bb >= 1:
        scores.append(-12)
    elif bb <= -1:
        scores.append(12)
    else:
        scores.append(int(-bb * 8))

    # ── ADX (trend strength) ──
    adx_val = _adx(closes, highs, lows)
    result["indicators"]["adx"] = round(adx_val, 1)
    if adx_val > 30:
        scores.append(15)
        result["indicators"]["adx_signal"] = "strong_trend"
    elif adx_val > 25:
        scores.append(10)
        result["indicators"]["adx_signal"] = "trending"
    elif adx_val > 20:
        scores.append(3)
        result["indicators"]["adx_signal"] = "weak_trend"
    else:
        scores.append(-5)
        result["indicators"]["adx_signal"] = "ranging"

    # ── Stochastic ──
    stoch_k, stoch_d = _stochastic(closes, highs, lows)
    result["indicators"]["stoch_k"] = round(stoch_k, 1)
    result["indicators"]["stoch_d"] = round(stoch_d, 1)
    if stoch_k < 20 and stoch_d < 20:
        scores.append(18)
        result["indicators"]["stoch_signal"] = "oversold"
    elif stoch_k > 80 and stoch_d > 80:
        scores.append(-18)
        result["indicators"]["stoch_signal"] = "overbought"
    elif stoch_k < 30:
        scores.append(10)
        result["indicators"]["stoch_signal"] = "bullish"
    elif stoch_k > 70:
        scores.append(-10)
        result["indicators"]["stoch_signal"] = "bearish"
    else:
        result["indicators"]["stoch_signal"] = "neutral"

    # ── Momentum ──
    mom = _momentum(closes)
    result["indicators"]["momentum"] = round(mom, 2)
    if mom > 2:
        scores.append(12)
        result["indicators"]["momentum_signal"] = "strong_bullish"
    elif mom > 1:
        scores.append(6)
        result["indicators"]["momentum_signal"] = "bullish"
    elif mom < -2:
        scores.append(-12)
        result["indicators"]["momentum_signal"] = "strong_bearish"
    elif mom < -1:
        scores.append(-6)
        result["indicators"]["momentum_signal"] = "bearish"
    else:
        result["indicators"]["momentum_signal"] = "neutral"

    # ── Support/Resistance ──
    sr = _support_resistance(highs, lows, closes)
    result["indicators"]["sr_signal"] = sr
    if sr == 1:
        scores.append(-10)
    elif sr == -1:
        scores.append(10)

    # ── Trend vs SMA50 ──
    if len(closes) >= 50:
        sma50 = sum(closes[-50:]) / 50
        if closes[-1] > sma50 * 1.01:
            scores.append(12)
            result["indicators"]["trend"] = "above_sma50"
        elif closes[-1] < sma50 * 0.99:
            scores.append(-12)
            result["indicators"]["trend"] = "below_sma50"
        else:
            result["indicators"]["trend"] = "at_sma50"

    # ── Volume confirmation ──
    if volumes is not None and len(volumes) >= 20:
        avg_vol = sum(volumes[-20:]) / 20
        if avg_vol > 0:
            vol_ratio = volumes[-1] / avg_vol
            result["indicators"]["volume_ratio"] = round(vol_ratio, 2)
            if vol_ratio > 1.5:
                scores.append(8)
                result["indicators"]["volume_signal"] = "high"
            elif vol_ratio < 0.5:
                scores.append(-5)
                result["indicators"]["volume_signal"] = "low"
            else:
                result["indicators"]["volume_signal"] = "normal"

    # ── Divergence check ──
    try:
        from .divergence import check_divergence
        div = check_divergence(closes, highs, lows)
        if div["bullish_divergent"]:
            scores.append(25)
            result["indicators"]["divergence"] = "bullish"
        elif div["bearish_divergent"]:
            scores.append(-25)
            result["indicators"]["divergence"] = "bearish"
        else:
            result["indicators"]["divergence"] = "none"
    except Exception:
        pass

    # ── MTF bias ──
    mtf_bias = mtf.get("bias", 0)
    if mtf_bias > 0:
        scores.append(mtf_bias * 3)
        result["indicators"]["mtf_bias"] = "bullish"
    elif mtf_bias < 0:
        scores.append(mtf_bias * 3)
        result["indicators"]["mtf_bias"] = "bearish"
    else:
        result["indicators"]["mtf_bias"] = "mixed"

    # ── Session multiplier ──
    session_bonus = int((session_mult - 0.5) * 20)
    scores.append(session_bonus)
    result["indicators"]["session_bonus"] = session_bonus

    # ── Final calculation ──
    total_score = sum(scores)
    # Max possible score (rough estimate)
    max_possible = 25 + 20 + 22 + 20 + 15 + 18 + 12 + 10 + 12 + 8 + 25 + 6 + 10
    confidence = round(min(max(total_score / max_possible * 100, -99), 99), 0)

    result["total_score"] = total_score
    result["max_possible"] = max_possible

    if confidence >= 55:
        result["verdict"] = "BUY" if total_score > 0 else "SELL"
    elif confidence >= 35:
        result["verdict"] = "BUY (cautious)" if total_score > 0 else "SELL (cautious)"
    else:
        result["verdict"] = "PASS"

    result["confidence_pct"] = abs(confidence)

    pos_count = sum(1 for s in scores if s > 0)
    neg_count = sum(1 for s in scores if s < 0)
    result["indicators_positive"] = pos_count
    result["indicators_negative"] = neg_count
    result["indicators_total"] = len(scores)

    # ── Kelly-adjusted position sizing ──
    win_prob = abs(confidence) / 100
    kelly_pct = max(min((win_prob * 1.5 - 0.5) * 100, 10), 0)
    # Scale down proportionally to confidence
    kelly_pct = kelly_pct * (abs(confidence) / 100)

    result["kelly_pct"] = round(kelly_pct, 1)
    result["suggested_lot_size"] = round(bankroll * kelly_pct / 100 / 1000, 2) if bankroll > 0 else 0.01
    result["suggested_risk_usd"] = round(bankroll * kelly_pct / 100, 2)

    # ML modulation
    try:
        from .predictor import modulate_confidence
        conviction_result = {"success": True, "decision": result}
        modulated = modulate_confidence(conviction_result)
        result = modulated.get("decision", result)
    except Exception:
        pass

    return {"success": True, "decision": result}


def scan(client, bankroll: float = 1000, min_confidence: float = 55,
         max_spread_pips: float = 15, use_mtf: bool = True,
         min_mtf_alignment: float = 50, limit: int = 5) -> Dict[str, Any]:
    """Scan available symbols and return top opportunities.

    Applies ALL filters: spread, MTF alignment, session, confidence.
    """
    try:
        syms = client.market.get_symbols()
        if not syms:
            return {"success": False, "error": "No symbols"}
    except Exception as e:
        return {"success": False, "error": str(e)}

    session_mult = _session_score()
    if session_mult < 0.3:
        return {"success": True, "opportunities": [], "total_scan": 0,
                "message": "Low-activity session, not scanning"}

    results = []
    for sym in syms[:30]:
        s = sym.get("name", sym) if isinstance(sym, dict) else sym
        try:
            d = decide(client, s, "H1", bankroll, max_spread_pips, use_mtf, min_mtf_alignment)
            if d.get("success"):
                dec = d.get("decision", {})
                if dec.get("confidence_pct", 0) >= min_confidence and "BUY" in dec.get("verdict", ""):
                    results.append({
                        "symbol": s,
                        "price": dec.get("current_price"),
                        "verdict": dec["verdict"],
                        "confidence_pct": dec["confidence_pct"],
                        "suggested_lot": dec.get("suggested_lot_size"),
                        "kelly_pct": dec.get("kelly_pct"),
                        "spread_pips": dec.get("spread_pips"),
                        "mtf_alignment": dec.get("mtf", {}).get("alignment"),
                        "session_quality": dec.get("session_quality"),
                        "indicators_pos": dec.get("indicators_positive"),
                        "indicators_neg": dec.get("indicators_negative"),
                    })
        except Exception:
            continue

    results.sort(key=lambda x: x["confidence_pct"], reverse=True)
    return {
        "success": True,
        "opportunities": results[:limit],
        "total_scan": len(results),
        "session_quality": session_mult,
    }
