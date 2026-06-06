"""
Correlation — matriz de correlación entre símbolos.

Ayuda a evitar sobreexposición a un mismo factor de riesgo.
Por ejemplo: EURUSD + GBPUSD long simultáneo = correlación ~0.8.
"""
import logging
import math
from typing import Dict, Any, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)

# ── Known forex correlations (approximate, stable pairs) ──────────────────────
# Range: -1 to 1. Positive = move together. Negative = move opposite.
_KNOWN_CORRELATIONS = {
    ("EURUSD", "GBPUSD"): 0.85,
    ("EURUSD", "USDCHF"): -0.90,
    ("EURUSD", "USDJPY"): 0.55,
    ("EURUSD", "USDCAD"): 0.60,
    ("GBPUSD", "USDCHF"): -0.75,
    ("GBPUSD", "USDJPY"): 0.50,
    ("USDJPY", "USDCHF"): 0.65,
    ("USDJPY", "USDCAD"): 0.55,
    ("USDCAD", "AUDUSD"): -0.70,
    ("AUDUSD", "NZDUSD"): 0.85,
    ("AUDUSD", "USDCHF"): -0.55,
    ("AUDUSD", "USDJPY"): 0.65,
    ("NZDUSD", "USDCHF"): -0.50,
    ("EURGBP", "EURJPY"): 0.70,
    ("EURGBP", "EURCHF"): 0.75,
    ("EURJPY", "GBPJPY"): 0.85,
    ("EURCHF", "GBPCHF"): 0.80,
    ("GBPJPY", "USDJPY"): 0.70,
    ("AUDJPY", "NZDJPY"): 0.85,
    ("AUDJPY", "USDJPY"): 0.75,
    ("NZDJPY", "USDJPY"): 0.70,
}


def _normalize_symbol(s: str) -> str:
    return s.upper().replace(".FX", "")


def get_correlation(sym_a: str, sym_b: str) -> float:
    """Get known correlation between two symbols. -1 to 1."""
    a = _normalize_symbol(sym_a)
    b = _normalize_symbol(sym_b)
    if a == b:
        return 1.0
    pair = _KNOWN_CORRELATIONS.get((a, b))
    if pair is not None:
        return pair
    pair = _KNOWN_CORRELATIONS.get((b, a))
    if pair is not None:
        return pair
    return 0.0


def calculate_from_data(client, symbols: List[str], timeframe: str = "H1", bars: int = 100) -> Dict[str, Any]:
    """Calculate correlation matrix from actual price data."""
    price_data = {}
    for sym in symbols:
        try:
            df = client.market.get_candles_latest(symbol_name=sym, timeframe=timeframe, count=bars)
            if isinstance(df, pd.DataFrame) and "close" in df.columns:
                price_data[sym] = df["close"].dropna().values
        except Exception:
            continue

    symbols_ok = list(price_data.keys())
    if len(symbols_ok) < 2:
        return {"success": False, "error": "Need at least 2 symbols with data"}

    # Align by length
    min_len = min(len(v) for v in price_data.values())
    aligned = {s: v[-min_len:] for s, v in price_data.items()}

    # Calculate returns
    returns = {}
    for s, vals in aligned.items():
        returns[s] = [(vals[i+1] - vals[i]) / vals[i] * 100 for i in range(len(vals)-1)]

    # Correlation matrix
    names = list(returns.keys())
    matrix = []
    for i, s1 in enumerate(names):
        row = []
        for j, s2 in enumerate(names):
            if i == j:
                row.append(1.0)
                continue
            r1 = returns[s1]
            r2 = returns[s2]
            n = min(len(r1), len(r2))
            if n < 3:
                row.append(0)
                continue
            r1, r2 = r1[-n:], r2[-n:]
            mean1 = sum(r1) / n
            mean2 = sum(r2) / n
            num = sum((r1[i] - mean1) * (r2[i] - mean2) for i in range(n))
            den1 = math.sqrt(sum((r1[i] - mean1)**2 for i in range(n)))
            den2 = math.sqrt(sum((r2[i] - mean2)**2 for i in range(n)))
            corr = num / max(den1 * den2, 0.0001)
            row.append(round(corr, 2))
        matrix.append({"symbol": s1, "correlations": row})

    return {
        "success": True,
        "symbols": names,
        "matrix": matrix,
        "note": "Rows/symbols in same order. Diag=1.0. |r|>0.7 = high correlation.",
    }


def portfolio_risk(symbols: List[str], weights: Optional[List[float]] = None) -> Dict[str, Any]:
    """Calculate portfolio correlation risk. Warn if >30% in same group."""
    if not symbols:
        return {"success": True, "risk": "none", "warnings": []}

    if weights is None:
        weights = [1.0 / len(symbols)] * len(symbols)

    # Group by correlation clusters
    warnings = []
    high_corr_pairs = []
    for i in range(len(symbols)):
        for j in range(i+1, len(symbols)):
            corr = get_correlation(symbols[i], symbols[j])
            if abs(corr) >= 0.7:
                high_corr_pairs.append({
                    "pair": f"{symbols[i]} / {symbols[j]}",
                    "correlation": corr,
                    "combined_weight": round((weights[i] + weights[j]) * 100, 1),
                })
                if (weights[i] + weights[j]) > 0.3:
                    warnings.append(f"{symbols[i]} + {symbols[j]}: {corr:.0%} corr, {((weights[i]+weights[j])*100):.0f}% portfolio")

    # Number of effective positions (higher = better diversified)
    if len(high_corr_pairs) > 0:
        effective_positions = round(len(symbols) / (1 + len(high_corr_pairs) * 0.3), 1)
    else:
        effective_positions = len(symbols)

    risk_level = "high" if len(warnings) >= 2 else ("medium" if len(warnings) >= 1 else "low")

    return {
        "success": True,
        "symbols": symbols,
        "effective_positions": effective_positions,
        "risk_level": risk_level,
        "warnings": warnings,
        "high_correlation_pairs": high_corr_pairs,
        "advice": "Reduce correlated positions" if risk_level == "high" else "Diversification OK",
    }
