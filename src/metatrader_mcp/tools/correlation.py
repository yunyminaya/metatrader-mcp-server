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


# ── Cross-asset reference symbols ──────────────────────────────────────────────
_CROSS_ASSET_SYMBOLS = {
    "DXY": "USDX",       # US Dollar Index
    "SPX": "SP500",       # S&P 500
    "XAU": "GOLD",        # Gold
    "US10Y": "US10Y",     # US 10-Year Yield
    "USOIL": "WTI",       # Crude Oil
}

# Fallback correlations for cross-asset (approximate)
_CROSS_ASSET_CORR = {
    "EURUSD": {"DXY": -0.95, "SPX": 0.50, "XAU": 0.40, "US10Y": -0.30, "USOIL": 0.35},
    "GBPUSD": {"DXY": -0.85, "SPX": 0.45, "XAU": 0.30, "US10Y": -0.25, "USOIL": 0.30},
    "USDJPY": {"DXY": 0.70, "SPX": -0.30, "XAU": -0.50, "US10Y": 0.60, "USOIL": -0.20},
    "USDCAD": {"DXY": 0.65, "SPX": -0.20, "XAU": -0.60, "US10Y": 0.20, "USOIL": -0.70},
    "AUDUSD": {"DXY": -0.75, "SPX": 0.65, "XAU": 0.55, "US10Y": -0.35, "USOIL": 0.60},
    "NZDUSD": {"DXY": -0.70, "SPX": 0.55, "XAU": 0.40, "US10Y": -0.30, "USOIL": 0.45},
    "USDCHF": {"DXY": 0.85, "SPX": -0.35, "XAU": -0.50, "US10Y": 0.40, "USOIL": -0.25},
    "EURJPY": {"DXY": -0.40, "SPX": 0.60, "XAU": 0.35, "US10Y": -0.20, "USOIL": 0.30},
    "GBPJPY": {"DXY": -0.30, "SPX": 0.55, "XAU": 0.25, "US10Y": -0.15, "USOIL": 0.25},
}


def cross_asset_correlation(client, symbol: str) -> Dict[str, Any]:
    """Get correlation of a symbol with major cross-asset benchmarks.

    Computes live rolling correlation with DXY, SPX, Gold, US10Y, Oil.
    Falls back to known approximate values if data unavailable.

    Args:
        client: MT5 client
        symbol: trading symbol (e.g. EURUSD)

    Returns:
        dict with correlations, divergences, and advice
    """
    sym = _normalize_symbol(symbol)
    known = _CROSS_ASSET_CORR.get(sym, {})
    ref_sym_map = {
        "DXY": None, "SPX": None, "XAU": None, "US10Y": None, "USOIL": None,
    }

    # Try to find cross-asset symbols in available MT5 symbols
    try:
        all_syms = client.account.get_symbols() if hasattr(client.account, "get_symbols") else []
        if not all_syms:
            all_syms = []
        all_sym_names = []
        for s in (all_syms or []):
            if hasattr(s, "name"):
                all_sym_names.append(s.name)
            elif isinstance(s, dict):
                all_sym_names.append(s.get("name", ""))
            elif isinstance(s, str):
                all_sym_names.append(s)

        # Map common names
        for name in all_sym_names:
            name_upper = name.upper()
            for ref, aliases in [("DXY", ["DXY", "USDX", "USDOLLAR", "DX"]),
                                  ("SPX", ["SPX", "SP500", "US500", "S&P"]),
                                  ("XAU", ["XAU", "GOLD", "XAUUSD"]),
                                  ("US10Y", ["US10Y", "UST10Y", "US10YR", "TNOTE"]),
                                  ("USOIL", ["USOIL", "WTI", "CRUDE", "OIL", "XTIUSD"])]:
                if ref_sym_map[ref] is None:
                    for alias in aliases:
                        if alias in name_upper:
                            ref_sym_map[ref] = name
                            break
    except Exception:
        pass

    # Compute live correlations where possible
    live_corrs: Dict[str, Any] = {}
    for ref_name, mt5_name in ref_sym_map.items():
        if mt5_name is None:
            live_corrs[ref_name] = {"correlation": known.get(ref_name, 0), "source": "historical"}
            continue

        try:
            df_a = client.market.get_candles_latest(symbol_name=sym, timeframe="H1", count=100)
            df_b = client.market.get_candles_latest(symbol_name=mt5_name, timeframe="H1", count=100)
            import pandas as pd
            if (isinstance(df_a, pd.DataFrame) and isinstance(df_b, pd.DataFrame)
                    and not df_a.empty and not df_b.empty
                    and "close" in df_a.columns and "close" in df_b.columns):
                ca = df_a["close"].dropna().values
                cb = df_b["close"].dropna().values
                n = min(len(ca), len(cb))
                if n >= 20:
                    ra = [(ca[i+1] - ca[i]) / ca[i] * 100 for i in range(n - 1)]
                    rb = [(cb[i+1] - cb[i]) / cb[i] * 100 for i in range(n - 1)]
                    mean_a = sum(ra) / len(ra)
                    mean_b = sum(rb) / len(rb)
                    num = sum((ra[i] - mean_a) * (rb[i] - mean_b) for i in range(len(ra)))
                    den = math.sqrt(sum((ra[i] - mean_a)**2 for i in range(len(ra)))) * \
                          math.sqrt(sum((rb[i] - mean_b)**2 for i in range(len(rb))))
                    corr = num / max(den, 0.0001)
                    live_corrs[ref_name] = {"correlation": round(corr, 2), "source": "live"}
                else:
                    live_corrs[ref_name] = {"correlation": known.get(ref_name, 0), "source": "historical"}
        except Exception:
            live_corrs[ref_name] = {"correlation": known.get(ref_name, 0), "source": "historical"}

    # Detect divergences (when correlation breaks down significantly)
    divergences = []
    for ref_name, data in live_corrs.items():
        corr = data["correlation"]
        hist = known.get(ref_name, 0)
        if abs(hist) > 0.5 and abs(corr - hist) > 0.4:
            divergences.append({
                "asset": ref_name,
                "historical_correlation": hist,
                "current_correlation": corr,
                "divergence": "significant",
                "meaning": "Normal relationship broken — potential regime change",
            })

    # Generate advice
    overall_risk = sum(abs(v.get("correlation", 0)) for v in live_corrs.values()) / max(len(live_corrs), 1)
    if divergences:
        advice = "CAUTION: Multiple correlation divergences detected — regime may be changing"
    elif overall_risk > 0.6:
        advice = "Symbol moves with broad market — hedge with DXY or Gold"
    elif overall_risk < 0.3:
        advice = "Symbol trades independently — good for diversification"
    else:
        advice = "Moderate cross-asset correlation"

    return {
        "success": True,
        "symbol": sym,
        "cross_asset_correlations": live_corrs,
        "divergences": divergences,
        "overall_correlation_risk": round(overall_risk, 2),
        "advice": advice,
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
