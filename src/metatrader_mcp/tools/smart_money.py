import logging
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _digits(client, symbol: str) -> int:
    info = client.market.get_symbol_info(symbol)
    return info.get("digits", 5) if isinstance(info, dict) else 5


def fair_value_gaps(client, symbol: str, timeframe: str = "H1", lookback: int = 100) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 10:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    bull_fvgs = []
    bear_fvgs = []
    for i in range(1, len(df_sorted)):
        prev = df_sorted.iloc[i - 1]
        curr = df_sorted.iloc[i]
        if curr['low'] > prev['high']:
            bull_fvgs.append({"index": i, "gap_high": prev['high'], "gap_low": curr['low'], "gap_size": curr['low'] - prev['high'], "direction": "BULLISH"})
        if curr['high'] < prev['low']:
            bear_fvgs.append({"index": i, "gap_high": curr['high'], "gap_low": prev['low'], "gap_size": prev['low'] - curr['high'], "direction": "BEARISH"})
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df_sorted['close'].iloc[-1]
    unfilled = []
    for fvg in bull_fvgs + bear_fvgs:
        if (fvg["direction"] == "BULLISH" and current < fvg["gap_high"]) or \
           (fvg["direction"] == "BEARISH" and current > fvg["gap_low"]):
            unfilled.append(fvg)
    d = _digits(client, symbol)
    return {
        "error": False,
        "message": f"{len(unfilled)} unfilled FVGs of {len(bull_fvgs)+len(bear_fvgs)} total",
        "data": {
            "current_price": round(current, d),
            "bullish_fvgs": [{"gap_high": round(f["gap_high"], d), "gap_low": round(f["gap_low"], d), "size": round(f["gap_size"], d)} for f in bull_fvgs[-5:]],
            "bearish_fvgs": [{"gap_high": round(f["gap_high"], d), "gap_low": round(f["gap_low"], d), "size": round(f["gap_size"], d)} for f in bear_fvgs[-5:]],
            "unfilled_fvgs_nearby": [{"direction": f["direction"], "gap_high": round(f["gap_high"], d), "gap_low": round(f["gap_low"], d)} for f in unfilled[-5:]],
            "total_bullish": len(bull_fvgs), "total_bearish": len(bear_fvgs), "unfilled_count": len(unfilled),
        }
    }


def liquidity_zones(client, symbol: str, timeframe: str = "H1", lookback: int = 100) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 20:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    highs = df_sorted['high'].values
    lows = df_sorted['low'].values
    closes = df_sorted['close'].values
    buy_liquidity = [] # above swing highs (stop hunts)
    sell_liquidity = [] # below swing lows
    for i in range(2, len(df_sorted) - 2):
        if highs[i] > highs[i - 1] and highs[i] > highs[i - 2] and highs[i] > highs[i + 1]:
            buy_liquidity.append({"index": i, "price": highs[i], "type": "BUY_STOPS_ABOVE"})
        if lows[i] < lows[i - 1] and lows[i] < lows[i - 2] and lows[i] < lows[i + 1]:
            sell_liquidity.append({"index": i, "price": lows[i], "type": "SELL_STOPS_BELOW"})
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df_sorted['close'].iloc[-1]
    nearest_buy = None
    nearest_sell = None
    for liq in reversed(buy_liquidity):
        if liq["price"] > current:
            nearest_buy = liq
            break
    for liq in reversed(sell_liquidity):
        if liq["price"] < current:
            nearest_sell = liq
            break
    d = _digits(client, symbol)
    return {
        "error": False,
        "message": f"Liquidity above: {nearest_buy['price'] if nearest_buy else 'N/A'} / below: {nearest_sell['price'] if nearest_sell else 'N/A'}",
        "data": {
            "current_price": round(current, d),
            "buy_liquidity_above": [{"price": round(l["price"], d)} for l in buy_liquidity[-5:]],
            "sell_liquidity_below": [{"price": round(l["price"], d)} for l in sell_liquidity[-5:]],
            "nearest_liquidity_above": round(nearest_buy["price"], d) if nearest_buy else None,
            "nearest_liquidity_below": round(nearest_sell["price"], d) if nearest_sell else None,
            "total_buy_liquidity_zones": len(buy_liquidity),
            "total_sell_liquidity_zones": len(sell_liquidity),
        }
    }


def displacement_detection(client, symbol: str, timeframe: str = "H1", lookback: int = 100, multiplier: float = 1.5) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 20:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    avg_range = (df_sorted['high'] - df_sorted['low']).mean()
    displacements = []
    for i in range(1, len(df_sorted)):
        body = abs(df_sorted['close'].iloc[i] - df_sorted['open'].iloc[i])
        total_range = df_sorted['high'].iloc[i] - df_sorted['low'].iloc[i]
        if total_range > avg_range * multiplier and body > total_range * 0.6:
            direction = "BULLISH" if df_sorted['close'].iloc[i] > df_sorted['open'].iloc[i] else "BEARISH"
            gap_from_prev = df_sorted['open'].iloc[i] - df_sorted['close'].iloc[i - 1] if direction == "BULLISH" else df_sorted['close'].iloc[i - 1] - df_sorted['open'].iloc[i]
            displacements.append({"index": i, "direction": direction, "range": total_range, "body": body, "gap": max(0, gap_from_prev)})
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df_sorted['close'].iloc[-1]
    d = _digits(client, symbol)
    bullish_displacements = [dis for dis in displacements if dis["direction"] == "BULLISH"]
    bearish_displacements = [dis for dis in displacements if dis["direction"] == "BEARISH"]
    return {
        "error": False,
        "message": f"{len(displacements)} displacements detected ({len(bullish_displacements)}B/{len(bearish_displacements)}S)",
        "data": {
            "current_price": round(current, d),
            "recent_displacements": [{"direction": dis["direction"], "range": round(dis["range"], d), "gap": round(dis["gap"], d)} for dis in displacements[-5:]],
            "bullish_count": len(bullish_displacements),
            "bearish_count": len(bearish_displacements),
            "avg_range": round(avg_range, d),
            "multiplier": multiplier,
        }
    }


def order_flow_imbalance(client, symbol: str, lookback_bars: int = 50, timeframe: str = "M5") -> Dict[str, Any]:
    try:
        df = client.market.get_ticks_latest(symbol_name=symbol, count=500)
    except Exception:
        try:
            df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback_bars)
            if df is not None:
                df['buy_volume'] = df['tick_volume'] * (df['close'] - df['low']) / (df['high'] - df['low'] + 0.0001)
                df['sell_volume'] = df['tick_volume'] - df['buy_volume']
                net = df['buy_volume'].sum() - df['sell_volume'].sum()
                ratio = df['buy_volume'].sum() / max(df['sell_volume'].sum(), 0.001)
                return {
                    "error": False,
                    "message": f"Order flow: ratio {ratio:.2f}, net {net:.0f}",
                    "data": {"imbalance_ratio": round(ratio, 2), "net_delta": round(net, 0), "buy_volume": round(df['buy_volume'].sum(), 0), "sell_volume": round(df['sell_volume'].sum(), 0), "source": "candle_volume_estimate"},
                }
        except Exception:
            pass
        return {"error": True, "message": "Tick data not available for this symbol", "data": None}
    if df is None or len(df) < 10:
        return {"error": True, "message": "Not enough tick data", "data": None}
    if 'bid' in df.columns and 'ask' in df.columns:
        df['mid'] = (df['bid'] + df['ask']) / 2
        df['delta'] = df['bid'].diff()
        buy_ticks = (df['delta'] > 0).sum()
        sell_ticks = (df['delta'] < 0).sum()
    elif 'last' in df.columns:
        df['delta'] = df['last'].diff()
        buy_ticks = (df['delta'] > 0).sum()
        sell_ticks = (df['delta'] < 0).sum()
    else:
        return {"error": True, "message": "Tick data missing bid/ask/last", "data": None}
    total = buy_ticks + sell_ticks
    ratio = buy_ticks / max(sell_ticks, 1)
    imbalance = (buy_ticks - sell_ticks) / max(total, 1)
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else 0
    return {
        "error": False,
        "message": f"Order flow: {buy_ticks}B/{sell_ticks}S ratio={ratio:.2f}",
        "data": {
            "buy_ticks": int(buy_ticks), "sell_ticks": int(sell_ticks),
            "total_ticks": int(total), "ratio": round(ratio, 2),
            "imbalance": round(imbalance, 4),
            "current_price": current,
            "interpretation": "BUYING_PRESSURE" if ratio > 1.2 else "SELLING_PRESSURE" if ratio < 0.8 else "NEUTRAL",
        }
    }


def market_structure_break(client, symbol: str, timeframe: str = "H1", lookback: int = 100) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 20:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    highs = df_sorted['high'].values
    lows = df_sorted['low'].values
    closes = df_sorted['close'].values
    swing_highs = []
    swing_lows = []
    for i in range(1, len(df_sorted) - 1):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            swing_highs.append({"index": i, "price": highs[i]})
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            swing_lows.append({"index": i, "price": lows[i]})
    msb_signals = []
    for i in range(1, len(swing_highs)):
        if swing_highs[i]["price"] > swing_highs[i - 1]["price"]:
            msb_signals.append({"type": "MSB_BULLISH", "broken_level": swing_highs[i - 1]["price"], "new_high": swing_highs[i]["price"]})
    for i in range(1, len(swing_lows)):
        if swing_lows[i]["price"] < swing_lows[i - 1]["price"]:
            msb_signals.append({"type": "MSB_BEARISH", "broken_level": swing_lows[i - 1]["price"], "new_low": swing_lows[i]["price"]})
    choch_signals = []
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        if swing_highs[-1]["price"] > swing_highs[-2]["price"] and swing_lows[-1]["price"] < swing_lows[-2]["price"]:
            choch_signals.append("CHOCH_BEARISH (higher high + lower low)")
        if swing_highs[-1]["price"] < swing_highs[-2]["price"] and swing_lows[-1]["price"] > swing_lows[-2]["price"]:
            choch_signals.append("CHOCH_BULLISH (lower high + higher low)")
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df_sorted['close'].iloc[-1]
    d = _digits(client, symbol)
    current_trend = "UPTREND" if len(swing_highs) >= 2 and swing_highs[-1]["price"] > swing_highs[-2]["price"] and len(swing_lows) >= 2 and swing_lows[-1]["price"] > swing_lows[-2]["price"] else \
                    "DOWNTREND" if len(swing_highs) >= 2 and swing_highs[-1]["price"] < swing_highs[-2]["price"] and len(swing_lows) >= 2 and swing_lows[-1]["price"] < swing_lows[-2]["price"] else \
                    "RANGING"
    return {
        "error": False,
        "message": f"Trend: {current_trend} | MSBs: {len([m for m in msb_signals if 'BULLISH' in m['type']])}B/{len([m for m in msb_signals if 'BEARISH' in m['type']])}S",
        "data": {
            "current_price": round(current, d),
            "trend": current_trend,
            "structure_breaks": [{"type": s["type"], "level": round(s.get("broken_level") or s.get("new_high") or s.get("new_low", 0), d)} for s in msb_signals[-5:]],
            "change_of_character": choch_signals,
            "swing_highs": [round(s["price"], d) for s in swing_highs[-5:]],
            "swing_lows": [round(s["price"], d) for s in swing_lows[-5:]],
        }
    }
