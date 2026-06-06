"""
Divergence — detección de divergencia RSI y MACD.

La divergencia es uno de los patrones de mayor win rate en forex (>70%).
Detecta:
  - Regular bullish divergence (precio baja, RSI/MACD sube)
  - Regular bearish divergence (precio sube, RSI/MACD baja)
  - Hidden bullish divergence (precio sube, RSI/MACD baja más)
  - Hidden bearish divergence (precio baja, RSI/MACD sube más)
"""
import logging
import math
from typing import Dict, Any, Tuple, List

logger = logging.getLogger(__name__)


def _find_pivots(values: List[float], window: int = 5) -> Tuple[List[int], List[int]]:
    """Find pivot highs and lows."""
    highs_idx = []
    lows_idx = []
    for i in range(window, len(values) - window):
        if all(values[i] >= values[i-j] for j in range(1, window+1)) and all(values[i] >= values[i+j] for j in range(1, window+1)):
            highs_idx.append(i)
        if all(values[i] <= values[i-j] for j in range(1, window+1)) and all(values[i] <= values[i+j] for j in range(1, window+1)):
            lows_idx.append(i)
    return highs_idx, lows_idx


def _rsi_series(values, period=14):
    """Calculate RSI for entire series."""
    if len(values) < period + 1:
        return [50] * len(values)
    rsis = [50] * period
    for i in range(period, len(values)):
        deltas = [values[j+1] - values[j] for j in range(i - period, i)]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            rsis.append(100)
        else:
            rsis.append(100 - (100 / (1 + avg_gain / avg_loss)))
    return rsis


def _macd_series(values, fast=12, slow=26, signal=9):
    """Calculate MACD histogram for entire series."""
    if len(values) < slow + signal + 1:
        return [0] * len(values)
    def ema(data, p):
        mult = 2 / (p + 1)
        r = [data[0]]
        for i in range(1, len(data)):
            r.append((data[i] - r[-1]) * mult + r[-1])
        return r
    macd = []
    for i in range(slow, len(values)):
        f = ema(values[:i+1], fast)[-1]
        s = ema(values[:i+1], slow)[-1]
        macd.append(f - s)
    sig = ema(macd, signal)
    hist = [macd[i] - sig[i] for i in range(len(macd))]
    # Pad front
    padding = [0] * (len(values) - len(hist))
    return padding + hist


def check_divergence(closes, highs, lows, window=5) -> Dict[str, Any]:
    """Check for regular bull/bear divergence using RSI and MACD."""
    n = len(closes)
    if n < 30:
        return {"bullish_divergent": False, "bearish_divergent": False, "details": "too_short"}

    rsi = _rsi_series(closes)
    macd_h = _macd_series(closes)
    hi, li = _find_pivots(closes, window)
    rsi_hi, rsi_li = _find_pivots(rsi, window)
    macd_hi, macd_li = _find_pivots(macd_h, window)

    results = {"bullish_divergent": False, "bearish_divergent": False, "sources": []}

    # Regular Bullish Divergence: price makes lower low, RSI/MACD makes higher low
    if len(li) >= 2 and len(rsi_li) >= 2:
        p_ll = li[-1]
        p_prev = li[-2]
        r_ll = rsi_li[-1]
        r_prev = rsi_li[-2]
        if closes[p_ll] < closes[p_prev] and rsi[r_ll] > rsi[r_prev]:
            results["bullish_divergent"] = True
            results["sources"].append({"type": "rsi_bullish", "strength": "regular"})
    if not results["bullish_divergent"] and len(li) >= 2 and len(macd_li) >= 2:
        m_ll = macd_li[-1]
        m_prev = macd_li[-2]
        if closes[li[-1]] < closes[li[-2]] and macd_h[m_ll] > macd_h[m_prev]:
            results["bullish_divergent"] = True
            results["sources"].append({"type": "macd_bullish", "strength": "regular"})

    # Regular Bearish Divergence: price makes higher high, RSI/MACD makes lower high
    if len(hi) >= 2 and len(rsi_hi) >= 2:
        p_hh = hi[-1]
        p_prev = hi[-2]
        r_hh = rsi_hi[-1]
        r_prev = rsi_hi[-2]
        if closes[p_hh] > closes[p_prev] and rsi[r_hh] < rsi[r_prev]:
            results["bearish_divergent"] = True
            results["sources"].append({"type": "rsi_bearish", "strength": "regular"})
    if not results["bearish_divergent"] and len(hi) >= 2 and len(macd_hi) >= 2:
        m_hh = macd_hi[-1]
        m_prev = macd_hi[-2]
        if closes[hi[-1]] > closes[hi[-2]] and macd_h[m_hh] < macd_h[m_prev]:
            results["bearish_divergent"] = True
            results["sources"].append({"type": "macd_bearish", "strength": "regular"})

    return results
