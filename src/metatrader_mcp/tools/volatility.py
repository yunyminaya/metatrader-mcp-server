"""
Volatility — estrategias que aprovechan la volatilidad.

Incluye:
  - Volatility Regime: clasifica volatilidad en baja/media/alta/extrema
  - Straddle automático: compra CALL y PUT antes de eventos de alto impacto
  - Mean Reversion: compra en spikes de volatilidad, vende en contracción
  - Grid Adaptativo: grid que escala según la volatilidad actual
  - Volatility Harvesting: captura prima de volatilidad
"""
import logging
import math
import random
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def _atr(highs, lows, closes, period=14):
    """Calculate ATR from price arrays."""
    if len(closes) < period + 1:
        return 0
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    return sum(trs[-period:]) / min(period, len(trs)) if trs else 0


def regime(client, symbol: str) -> Dict[str, Any]:
    """Detect volatility regime for a symbol.

    Returns:
        low / medium / high / extreme + advice
    """
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=100)
    except Exception as e:
        return {"success": False, "error": str(e)}

    import pandas as pd
    if df is None or (hasattr(df, 'empty') and df.empty):
        return {"success": False, "error": "No data"}

    if isinstance(df, pd.DataFrame):
        c = df['close'].dropna().values
        h = df['high'].dropna().values
        l = df['low'].dropna().values
    else:
        return {"success": False, "error": "Unexpected format"}

    if len(c) < 20:
        return {"success": False, "error": "Not enough data"}

    current_atr = _atr(h, l, c)
    current_price = c[-1]

    # ATR as % of price
    atr_pct = current_atr / max(current_price, 0.0001) * 100 if current_price > 0 else 0

    # Historical ATR percentiles (last 100 bars)
    atr_history = []
    for i in range(14, len(h)):
        trs = []
        for j in range(i - 13, i + 1):
            if j > 0 and j < len(h):
                tr = max(h[j] - l[j], abs(h[j] - c[j-1]), abs(l[j] - c[j-1]))
                trs.append(tr)
        if trs:
            atr_history.append(sum(trs) / len(trs))

    if not atr_history:
        return {"success": False, "error": "Cannot compute ATR history"}

    atr_history.sort()
    n = len(atr_history)
    p25 = atr_history[int(n * 0.25)] if n > 0 else current_atr
    p50 = atr_history[int(n * 0.50)] if n > 0 else current_atr
    p75 = atr_history[int(n * 0.75)] if n > 0 else current_atr
    p90 = atr_history[int(n * 0.90)] if n > 0 else current_atr

    if current_atr >= p90:
        vol_regime = "extreme"
        size_mult = 0.3
        advice = "HIGH ALERT: Reduce position size 70%. Wait for contraction."
    elif current_atr >= p75:
        vol_regime = "high"
        size_mult = 0.6
        advice = "Elevated volatility. Reduce size 40%. Use wider stops."
    elif current_atr <= p25:
        vol_regime = "low"
        size_mult = 1.2
        advice = "Low volatility. Normal trading. Stops can be tighter."
    else:
        vol_regime = "medium"
        size_mult = 1.0
        advice = "Normal volatility. Standard position sizing."

    # Bollinger Band width (volatility indicator)
    sma = sum(c[-20:]) / 20 if len(c) >= 20 else current_price
    var = sum((x - sma)**2 for x in c[-20:]) / 20 if len(c) >= 20 else 0
    std = math.sqrt(var) if var > 0 else 0
    bb_width = std / max(sma, 0.0001) * 100 if sma > 0 else 0

    return {
        "success": True,
        "symbol": symbol,
        "regime": vol_regime,
        "atr": round(current_atr, 5),
        "atr_pct": round(atr_pct, 3),
        "bb_width_pct": round(bb_width, 2),
        "percentile_current": round((sum(1 for a in atr_history if a < current_atr) / max(n, 1)) * 100, 0),
        "size_multiplier": size_mult,
        "advice": advice,
    }


def straddle_signal(client, symbol: str, lookback_hours: int = 24) -> Dict[str, Any]:
    """Generate straddle signal for high-volatility events.

    Strategy:
      - Calculate expected move (ATR * 2) over next period
      - If expected move > threshold, signal straddle (Buy both directions)
      - Entry: place BUY STOP above range + SELL STOP below range
      - When one triggers, cancel the other

    This profits from breakouts in either direction.
    """
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=lookback_hours)
    except Exception as e:
        return {"success": False, "error": str(e)}

    import pandas as pd
    if df is None or (hasattr(df, 'empty') and df.empty):
        return {"success": False, "error": "No data"}

    if isinstance(df, pd.DataFrame):
        c = df['close'].dropna().values
        h = df['high'].dropna().values
        l = df['low'].dropna().values
    else:
        return {"success": False, "error": "Unexpected format"}

    if len(c) < 20:
        return {"success": False, "error": "Not enough data"}

    current_price = c[-1]
    atr_val = _atr(h, l, c)
    range_high = max(h[-20:])
    range_low = min(l[-20:])

    # Expected move
    expected_move = atr_val * 2
    expected_pct = expected_move / max(current_price, 0.0001) * 100

    # Signal
    signal = None
    if expected_pct >= 0.5:  # 0.5% expected move = worthwhile
        signal = {
            "type": "STRADDLE",
            "symbol": symbol,
            "current_price": round(current_price, 5),
            "atr": round(atr_val, 5),
            "expected_move_pct": round(expected_pct, 2),
            "buy_stop_above": round(range_high + atr_val * 0.5, 5),
            "sell_stop_below": round(range_low - atr_val * 0.5, 5),
            "volume": 0.01,  # small for straddle
            "stop_loss_atr_multiple": 1.5,
        }
        verdict = "STRADDLE"
    else:
        verdict = "PASS"

    return {
        "success": True,
        "symbol": symbol,
        "current_price": round(current_price, 5),
        "range_high": round(range_high, 5),
        "range_low": round(range_low, 5),
        "expected_move_pct": round(expected_pct, 2),
        "signal": signal,
        "verdict": verdict,
    }


def mean_reversion(client, symbol: str, lookback_bars: int = 50,
                   entry_std: float = 2.0, target_std: float = 0.5) -> Dict[str, Any]:
    """Mean reversion strategy for high volatility.

    When price deviates > entry_std from mean, enter counter-direction.
    Exit when price returns to within target_std of mean.

    Best in ranging/volatile regimes. WORST in trending.
    """
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=max(lookback_bars, 50))
    except Exception as e:
        return {"success": False, "error": str(e)}

    import pandas as pd
    if df is None or (hasattr(df, 'empty') and df.empty):
        return {"success": False, "error": "No data"}

    if isinstance(df, pd.DataFrame):
        c = df['close'].dropna().values
        h = df['high'].dropna().values
        l = df['low'].dropna().values
    else:
        return {"success": False, "error": "Unexpected format"}

    if len(c) < entry_std * 10:
        return {"success": False, "error": "Not enough data"}

    current = c[-1]
    mean = sum(c[-lookback_bars:]) / lookback_bars
    var = sum((x - mean)**2 for x in c[-lookback_bars:]) / lookback_bars
    std = math.sqrt(var) if var > 0 else 0.0001
    z_score = (current - mean) / max(std, 0.0001)

    result = {
        "symbol": symbol,
        "current_price": round(current, 5),
        "mean_price": round(mean, 5),
        "std": round(std, 5),
        "z_score": round(z_score, 2),
        "entry_std": entry_std,
        "target_std": target_std,
    }

    if z_score > entry_std:
        result["verdict"] = "SELL"
        result["confidence"] = round(min((z_score / entry_std) * 60, 85), 0)
        result["reason"] = f"Price {z_score:.1f}σ above mean — SELL mean reversion"
        result["target"] = round(mean + target_std * std, 5)
        result["stop"] = round(current + std * 1.5, 5)
    elif z_score < -entry_std:
        result["verdict"] = "BUY"
        result["confidence"] = round(min((abs(z_score) / entry_std) * 60, 85), 0)
        result["reason"] = f"Price {abs(z_score):.1f}σ below mean — BUY mean reversion"
        result["target"] = round(mean - target_std * std, 5)
        result["stop"] = round(current - std * 1.5, 5)
    else:
        result["verdict"] = "PASS"
        result["confidence"] = 0
        result["reason"] = f"Price {z_score:.1f}σ from mean — within range"

    return {"success": True, "mean_reversion": result}


def adaptive_grid(client, symbol: str, base_volume: float = 0.01,
                  grid_levels: int = 5, grid_spacing_atr: float = 1.0,
                  take_profit_atr: float = 2.0) -> Dict[str, Any]:
    """Adaptive grid trading strategy.

    Grid spacing scales with ATR — wider in high volatility, tighter in low.
    Places BUY orders below price, SELL orders above price.
    Each level has its own take profit.

    Args:
        symbol: symbol to trade
        base_volume: volume per grid level
        grid_levels: number of levels above and below
        grid_spacing_atr: grid spacing as ATR multiple
        take_profit_atr: TP distance as ATR multiple
    """
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=50)
    except Exception as e:
        return {"success": False, "error": str(e)}

    import pandas as pd
    if df is None or (hasattr(df, 'empty') and df.empty):
        return {"success": False, "error": "No data"}

    if isinstance(df, pd.DataFrame):
        c = df['close'].dropna().values
        h = df['high'].dropna().values
        l = df['low'].dropna().values
    else:
        return {"success": False, "error": "Unexpected format"}

    if len(c) < 20:
        return {"success": False, "error": "Not enough data"}

    current = c[-1]
    atr_val = _atr(h, l, c)
    if atr_val == 0:
        return {"success": False, "error": "ATR is zero"}

    spacing = atr_val * grid_spacing_atr
    tp_distance = atr_val * take_profit_atr

    levels = []
    # Buy levels below price
    for i in range(1, grid_levels + 1):
        entry = current - spacing * i
        tp = entry + tp_distance
        levels.append({
            "direction": "BUY",
            "level": i,
            "entry_price": round(entry, 5),
            "volume": round(base_volume, 2),
            "take_profit": round(tp, 5),
            "distance_pips": round((current - entry) / 0.0001, 0),
        })

    # Sell levels above price
    for i in range(1, grid_levels + 1):
        entry = current + spacing * i
        tp = entry - tp_distance
        levels.append({
            "direction": "SELL",
            "level": i,
            "entry_price": round(entry, 5),
            "volume": round(base_volume, 2),
            "take_profit": round(tp, 5),
            "distance_pips": round((entry - current) / 0.0001, 0),
        })

    total_exposure = sum(l["volume"] for l in levels) * 2  # both sides

    return {
        "success": True,
        "symbol": symbol,
        "current_price": round(current, 5),
        "atr": round(atr_val, 5),
        "grid_spacing_pips": round(spacing / 0.0001, 0),
        "take_profit_pips": round(tp_distance / 0.0001, 0),
        "levels": levels,
        "total_levels": len(levels),
        "total_exposure_volume": round(total_exposure, 2),
        "strategy": "Grid adaptativo — spacing escala con ATR",
    }
