"""
Regime v2 — detector de régimen multi-timeframe + contexto de sesión.

Clasifica cada símbolo en trending/ranging/volatile/quiet
usando D1 + H4 + H1 y contexto de sesión.
"""
import logging
import math
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

_regimes: Dict[str, Dict[str, Any]] = {}
_MAX_REGIMES = 100


def _classify_regime(closes, highs, lows):
    """Classify regime from price arrays."""
    n = len(closes)
    if n < 10:
        return "unknown", 0, 0

    returns = [(closes[i+1] - closes[i]) / closes[i] * 100 for i in range(n - 1)]
    avg_return = sum(returns) / len(returns) if returns else 0
    abs_returns = [abs(r) for r in returns]
    avg_abs = sum(abs_returns) / len(abs_returns) if abs_returns else 0

    # ATR-like
    trs = []
    for i in range(1, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    atr = sum(trs) / len(trs) if trs else 0
    atr_pct = atr / closes[-1] * 100 if closes[-1] > 0 else 0

    # ADX-like
    up = sum(1 for r in returns if r > 0)
    down = sum(1 for r in returns if r < 0)
    trend_bias = (up - down) / len(returns) if returns else 0
    adx = abs(trend_bias) * 100

    # Normalized volatility
    if avg_abs > 0:
        var = math.sqrt(sum((r - avg_return)**2 for r in returns) / (n - 1)) / avg_abs
    else:
        var = 1

    if var > 2.0 or atr_pct > 1.5:
        return "volatile", adx, atr_pct
    elif adx > 50 and abs(avg_return) > avg_abs * 0.4:
        return "trending", adx, atr_pct
    elif adx > 30:
        return "trending", adx, atr_pct
    elif var < 0.7 and atr_pct < 0.5:
        return "quiet", adx, atr_pct
    else:
        return "ranging", adx, atr_pct


def _mtf_regime(client, symbol: str) -> Dict[str, Any]:
    """Get regime across D1, H4, H1 and combine."""
    tfs = ["D1", "H4", "H1"]
    results = {}
    for tf in tfs:
        try:
            df = client.market.get_candles_latest(symbol_name=symbol, timeframe=tf, count=100)
            if df is None or (hasattr(df, 'empty') and df.empty):
                results[tf] = {"regime": "unknown", "adx": 0}
                continue
            import pandas as pd
            if isinstance(df, pd.DataFrame):
                c = df['close'].dropna().values
                h = df['high'].dropna().values
                l = df['low'].dropna().values
                if len(c) < 10:
                    results[tf] = {"regime": "unknown", "adx": 0}
                    continue
                r, adx_val, _ = _classify_regime(c, h, l)
                results[tf] = {"regime": r, "adx": round(adx_val, 0)}
        except Exception:
            results[tf] = {"regime": "unknown", "adx": 0}

    # Combined verdict
    d1 = results.get("D1", {}).get("regime", "unknown")
    h4 = results.get("H4", {}).get("regime", "unknown")
    h1 = results.get("H1", {}).get("regime", "unknown")

    # If D1 is trending, that dominates
    if d1 == "trending":
        combined = "trending"
    elif h4 == "trending" and h1 != "ranging":
        combined = "trending"
    elif d1 == "volatile" or h4 == "volatile":
        combined = "volatile"
    elif d1 == "quiet" and h4 == "quiet":
        combined = "quiet"
    elif h4 == "ranging" and h1 == "ranging":
        combined = "ranging"
    else:
        combined = h4  # default to H4

    return {
        "combined_regime": combined,
        "d1": results.get("D1"),
        "h4": results.get("H4"),
        "h1": results.get("H1"),
    }


def analyze(client, symbol: str, timeframe: str = "H1", days: int = 14,
            use_mtf: bool = True) -> Dict[str, Any]:
    """Detect market regime with optional multi-timeframe analysis."""
    # MTF analysis
    mtf = _mtf_regime(client, symbol) if use_mtf else {}

    # H1 detailed
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=days * 24)
    except Exception as e:
        return {"success": False, "error": f"Cannot fetch candles: {e}"}

    if df is None or (hasattr(df, 'empty') and df.empty):
        return {"success": False, "error": "No candle data"}

    import pandas as pd
    if isinstance(df, pd.DataFrame):
        c = df['close'].dropna().values
        h = df['high'].dropna().values
        l = df['low'].dropna().values
    else:
        return {"success": False, "error": "Unexpected data format"}

    if len(c) < 10:
        return {"success": False, "error": "Not enough candles"}

    regime, adx, atr_pct = _classify_regime(c, h, l)

    # Session context
    from .market import active_sessions as sessions
    ctx = sessions()

    recs = {
        "trending": "Seguir tendencia: MA cross, breakout, pullback en D1/H4",
        "ranging": "Mean reversion: RSI extremes, support/resistance",
        "volatile": "REDUCIR TAMAÑO 50%: stops amplios, esperar noticias",
        "quiet": "BUSCAR OTRO PAR: spreads bajos, poco movimiento",
    }

    result = {
        "symbol": symbol,
        "timeframe": timeframe,
        "regime": regime,
        "adx_strength": round(adx, 0),
        "atr_pct": round(atr_pct, 3),
        "session_quality": ctx.get("quality"),
        "advice": recs.get(regime, ""),
    }

    if use_mtf:
        result["mtf"] = mtf
        result["advice"] = f"D1:{mtf.get('d1',{}).get('regime','?')} H4:{mtf.get('h4',{}).get('regime','?')} H1:{mtf.get('h1',{}).get('regime','?')} → Combined: {mtf.get('combined_regime','?')}. {recs.get(mtf.get('combined_regime',''), '')}"

    _regimes[symbol] = result
    return {"success": True, "regime": result}


def scan(client) -> Dict[str, Any]:
    """Scan symbols and return regime + session context."""
    try:
        symbols = client.market.get_symbols()
        if not symbols:
            return {"success": False, "error": "No symbols available"}
    except Exception as e:
        return {"success": False, "error": f"Cannot get symbols: {e}"}

    ctx = {}
    try:
        from .market import active_sessions as sessions
        ctx = sessions()
    except Exception:
        pass

    results = []
    for sym in symbols[:20]:
        s = sym.get("name", sym) if isinstance(sym, dict) else sym
        try:
            r = analyze(client, s)
            if r.get("success"):
                reg = r.get("regime", {})
                results.append({
                    "symbol": s,
                    "regime": reg.get("regime"),
                    "mtf": reg.get("mtf", {}).get("combined_regime"),
                    "adx": reg.get("adx_strength"),
                    "atr_pct": reg.get("atr_pct"),
                    "session_quality": reg.get("session_quality"),
                })
        except Exception:
            continue

    counts = {}
    for r in results:
        reg = r.get("regime", "unknown")
        counts[reg] = counts.get(reg, 0) + 1

    return {
        "success": True,
        "regimes": results,
        "summary": counts,
        "session": ctx,
        "total": len(results),
    }
