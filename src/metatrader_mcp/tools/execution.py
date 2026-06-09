"""
Execution — algoritmos de ejecución avanzada.

Incluye:
  - TWAP (Time-Weighted Average Price): divide orden en fragmentos iguales en el tiempo
  - VWAP (Volume-Weighted Average Price): divide según volumen esperado
  - Iceberg: solo muestra una parte de la orden en el mercado
  - Stealth: randomiza parámetros para no dejar huella
  - Smart entry: espera pullback/retroceso para mejor precio
"""
import logging
import math
import random
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Callable

logger = logging.getLogger(__name__)


def twap(client, symbol: str, order_type: str, total_volume: float,
         duration_seconds: int = 300, slices: int = 10,
         randomize: bool = True, min_slice_volume: float = 0.01) -> Dict[str, Any]:
    """TWAP execution: split order into equal slices over time.

    Args:
        symbol: symbol to trade
        order_type: BUY or SELL
        total_volume: total volume to execute
        duration_seconds: total execution time
        slices: number of slices
        randomize: add random delay between slices
        min_slice_volume: minimum volume per slice

    Returns:
        execution report
    """
    if slices <= 0:
        return {"success": False, "error": "slices must be > 0"}

    slice_volume = max(total_volume / slices, min_slice_volume)
    actual_slices = min(slices, int(total_volume / min_slice_volume))
    slice_volume = total_volume / max(actual_slices, 1)
    delay = duration_seconds / max(actual_slices, 1)

    results = []
    total_filled = 0
    total_slippage = 0
    weighted_price = 0

    for i in range(actual_slices):
        vol = min(slice_volume, total_volume - total_filled)
        if vol < min_slice_volume:
            break

        try:
            result = client.order.place_market_order(symbol=symbol, volume=vol, type=order_type)
            results.append({
                "slice": i + 1,
                "volume": vol,
                "result": result,
            })
            total_filled += vol

            # Track slippage
            price = result.get("price", 0) if isinstance(result, dict) else 0
            if price:
                weighted_price = (weighted_price * (total_filled - vol) + price * vol) / total_filled
        except Exception as e:
            results.append({"slice": i + 1, "volume": vol, "error": str(e)})
            break

        if i < actual_slices - 1:
            wait = delay * (random.uniform(0.8, 1.2) if randomize else 1)
            time.sleep(wait)

    return {
        "success": True,
        "algorithm": "TWAP",
        "symbol": symbol,
        "type": order_type,
        "total_requested": total_volume,
        "total_filled": round(total_filled, 2),
        "fill_rate_pct": round(total_filled / max(total_volume, 0.01) * 100, 1),
        "slices_executed": len(results),
        "weighted_avg_price": round(weighted_price, 5) if weighted_price else 0,
        "slice_details": results[-10:],  # last 10 slices
    }


def vwap(client, symbol: str, order_type: str, total_volume: float,
         timeframe: str = "M5", lookback_bars: int = 12) -> Dict[str, Any]:
    """VWAP execution: follow volume profile.

    Estimates volume distribution from recent bars and slices accordingly.
    """
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback_bars)
    except Exception as e:
        return {"success": False, "error": f"Cannot fetch volume data: {e}"}

    import pandas as pd
    if df is None or (hasattr(df, 'empty') and df.empty):
        return {"success": False, "error": "No data"}

    if isinstance(df, pd.DataFrame):
        vols = df['tick_volume'].dropna().values if 'tick_volume' in df.columns else None
    else:
        vols = None

    if vols is None or len(vols) < 2:
        return {"success": False, "error": "Not enough volume data"}

    # Normalize volume weights
    total_hist_vol = sum(vols)
    if total_hist_vol == 0:
        return {"success": False, "error": "Zero volume"}
    weights = [v / total_hist_vol for v in vols]

    results = []
    total_filled = 0
    weighted_price = 0

    for i, w in enumerate(weights):
        vol = max(total_volume * w, 0.01)
        vol = min(vol, total_volume - total_filled)
        if vol < 0.005:
            continue

        try:
            result = client.order.place_market_order(symbol=symbol, volume=vol, type=order_type)
            results.append({
                "slice": i + 1,
                "volume_weight": round(w * 100, 1),
                "volume": vol,
                "result": result,
            })
            total_filled += vol
            price = result.get("price", 0) if isinstance(result, dict) else 0
            if price:
                weighted_price = (weighted_price * (total_filled - vol) + price * vol) / total_filled
        except Exception as e:
            results.append({"slice": i + 1, "volume": vol, "error": str(e)})
            break

        if total_filled >= total_volume:
            break

        time.sleep(1)

    return {
        "success": True,
        "algorithm": "VWAP",
        "symbol": symbol,
        "type": order_type,
        "total_requested": total_volume,
        "total_filled": round(total_filled, 2),
        "fill_rate_pct": round(total_filled / max(total_volume, 0.01) * 100, 1),
        "slices_executed": len(results),
        "weighted_avg_price": round(weighted_price, 5) if weighted_price else 0,
        "slice_details": results[-10:],
    }


def iceberg(client, symbol: str, order_type: str, total_volume: float,
            display_volume: float = 0.05, rest_after_fill: int = 5) -> Dict[str, Any]:
    """Iceberg order: only show display_volume at a time.

    After a fill, wait rest_after_fill seconds, then place next slice.
    """
    if display_volume <= 0:
        return {"success": False, "error": "display_volume must be > 0"}

    filled = 0
    results = []
    weighted_price = 0

    while filled < total_volume:
        vol = min(display_volume, total_volume - filled)
        if vol < 0.01:
            break

        try:
            result = client.order.place_market_order(symbol=symbol, volume=vol, type=order_type)
            results.append({
                "slice": len(results) + 1,
                "volume": vol,
                "result": result,
            })
            filled += vol
            price = result.get("price", 0) if isinstance(result, dict) else 0
            if price:
                weighted_price = (weighted_price * (filled - vol) + price * vol) / filled
        except Exception as e:
            results.append({"slice": len(results) + 1, "volume": vol, "error": str(e)})
            break

        time.sleep(rest_after_fill)

    return {
        "success": True,
        "algorithm": "ICEBERG",
        "symbol": symbol,
        "type": order_type,
        "total_requested": total_volume,
        "total_filled": round(filled, 2),
        "display_volume": display_volume,
        "slices_executed": len(results),
        "weighted_avg_price": round(weighted_price, 5) if weighted_price else 0,
        "slice_details": results[-10:],
    }


def stealth_entry(client, symbol: str, order_type: str, volume: float,
                  max_spread_pips: float = 10, max_attempts: int = 10,
                  price_deviation_pct: float = 0.05) -> Dict[str, Any]:
    """Stealth entry: waits for favorable price before entering.

    Strategy:
      1. Monitor price for N attempts (or timeout)
      2. Enter only if spread is within limit
      3. Randomize entry timing to avoid detection
      4. Wait for pullback (BUY at bid dip, SELL at ask spike)

    Args:
        symbol: symbol to trade
        order_type: BUY or SELL
        volume: order volume
        max_spread_pips: max acceptable spread
        max_attempts: max monitoring attempts before giving up
        price_deviation_pct: max % deviation from current price to accept

    Returns:
        execution report
    """
    monitor_seconds = 30
    base_price = 0
    attempts = 0

    for attempt in range(max_attempts):
        attempts += 1
        try:
            price = client.market.get_symbol_price(symbol_name=symbol)
            if not price:
                time.sleep(1)
                continue

            spread = price.get("spread", 999)
            bid = price.get("bid", 0)
            ask = price.get("ask", 0)

            if base_price == 0:
                base_price = (bid + ask) / 2

            if spread / 10 > max_spread_pips:
                time.sleep(random.uniform(2, 5))
                continue

            # Check if price is favorable
            if order_type.upper() == "BUY":
                current = ask
                deviation = (base_price - current) / max(base_price, 0.0001) * 100
                if deviation > 0:
                    pass
                elif abs(deviation) < price_deviation_pct:
                    pass
                else:
                    time.sleep(random.uniform(1, 3))
                    continue
            else:
                current = bid
                deviation = (current - base_price) / max(base_price, 0.0001) * 100
                if deviation > 0:
                    pass
                elif abs(deviation) < price_deviation_pct:
                    pass
                else:
                    time.sleep(random.uniform(1, 3))
                    continue

            # Enter
            result = client.order.place_market_order(symbol=symbol, volume=volume, type=order_type)
            return {
                "success": True,
                "algorithm": "STEALTH",
                "symbol": symbol,
                "type": order_type,
                "volume": volume,
                "attempts": attempts,
                "entry_price": current,
                "spread_pips": spread / 10,
                "result": result,
            }

        except Exception:
            time.sleep(2)

    return {
        "success": False,
        "error": f"Could not enter after {attempts} attempts",
        "algorithm": "STEALTH",
        "attempts": attempts,
    }


def smart_entry_condition(client, symbol: str, order_type: str,
                          atr_multiple: float = 0.5,
                          max_wait_seconds: int = 120) -> Dict[str, Any]:
    """Enter only when price pulls back ATR * multiple from recent high/low.

    For BUY: wait for price to pull back from recent high
    For SELL: wait for price to pull back from recent low

    This avoids chasing breakouts and gets better entries.
    """
    import time as _time
    from datetime import datetime

    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe="M5", count=20)
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

    if len(c) < 10:
        return {"success": False, "error": "Not enough data"}

    # Calculate ATR
    trs = []
    for i in range(1, len(c)):
        tr = max(h[i] - l[i], abs(h[i] - c[i-1]), abs(l[i] - c[i-1]))
        trs.append(tr)
    atr = sum(trs[-10:]) / 10 if trs else 0
    if atr == 0:
        return {"success": False, "error": "ATR is zero"}

    recent_high = max(h[-10:])
    recent_low = min(l[-10:])

    start = _time.time()
    while _time.time() - start < max_wait_seconds:
        try:
            price = client.market.get_symbol_price(symbol_name=symbol)
            if not price:
                _time.sleep(1)
                continue

            bid = price.get("bid", 0)
            ask = price.get("ask", 0)
            current = (bid + ask) / 2

            if order_type.upper() == "BUY":
                pullback = recent_high - current
                if pullback >= atr * atr_multiple and current > recent_low:
                    return {"success": True, "condition_met": True, "price": current,
                            "pullback_pips": round(pullback / 0.0001, 1), "atr": round(atr, 5)}
            else:
                pullback = current - recent_low
                if pullback >= atr * atr_multiple and current < recent_high:
                    return {"success": True, "condition_met": True, "price": current,
                            "pullback_pips": round(pullback / 0.0001, 1), "atr": round(atr, 5)}

            _time.sleep(5)
        except Exception:
            _time.sleep(5)

    return {"success": True, "condition_met": False,
            "message": f"Condition not met within {max_wait_seconds}s"}
