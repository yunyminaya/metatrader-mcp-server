import logging
import math
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _get_pip_info(symbol_info: dict) -> float:
    digits = symbol_info.get("digits", 5)
    return 10 ** -(digits - 1) if digits > 3 else 0.0001


def fibonacci_retracement(client, symbol: str, timeframe: str = "H1", swing_high: Optional[float] = None, swing_low: Optional[float] = None, lookback: int = 200) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 30:
        return {"error": True, "message": "Not enough data", "data": None}
    if swing_high is None:
        swing_high = df['high'].max()
    if swing_low is None:
        swing_low = df['low'].min()
    diff = swing_high - swing_low
    levels = {
        "0.0": swing_high,
        "0.236": swing_high - 0.236 * diff,
        "0.382": swing_high - 0.382 * diff,
        "0.5": swing_high - 0.5 * diff,
        "0.618": swing_high - 0.618 * diff,
        "0.786": swing_high - 0.786 * diff,
        "1.0": swing_low,
    }
    extensions = {
        "1.272": swing_high + 0.272 * diff if swing_high >= swing_low else swing_low - 0.272 * diff,
        "1.414": swing_high + 0.414 * diff if swing_high >= swing_low else swing_low - 0.414 * diff,
        "1.618": swing_high + 0.618 * diff if swing_high >= swing_low else swing_low - 0.618 * diff,
        "2.0": swing_high + 1.0 * diff if swing_high >= swing_low else swing_low - 1.0 * diff,
        "2.618": swing_high + 1.618 * diff if swing_high >= swing_low else swing_low - 1.618 * diff,
    }
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df['close'].iloc[0]
    nearest_level = min(levels.items(), key=lambda x: abs(x[1] - current)) if levels else (None, None)
    return {
        "error": False,
        "message": f"Nearest Fib level: {nearest_level[0]} at {nearest_level[1]:.{symbol_info_digits(symbol, client)}}",
        "data": {
            "swing_high": round(swing_high, symbol_info_digits(symbol, client)),
            "swing_low": round(swing_low, symbol_info_digits(symbol, client)),
            "retracement_levels": {k: round(v, symbol_info_digits(symbol, client)) for k, v in levels.items()},
            "extension_levels": {k: round(v, symbol_info_digits(symbol, client)) for k, v in extensions.items()},
            "current_price": round(current, symbol_info_digits(symbol, client)),
            "nearest_level": {"level": nearest_level[0], "price": round(nearest_level[1], symbol_info_digits(symbol, client))} if nearest_level[0] else None,
        }
    }


def symbol_info_digits(symbol: str, client) -> int:
    info = client.market.get_symbol_info(symbol)
    return info.get("digits", 5) if isinstance(info, dict) else 5


def pivot_points(client, symbol: str, timeframe: str = "D1", method: str = "standard") -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=3)
    if df is None or len(df) < 1:
        return {"error": True, "message": "Not enough data", "data": None}
    prev = df.iloc[0]
    high = prev['high']
    low = prev['low']
    close = prev['close']
    digits = symbol_info_digits(symbol, client)
    pp = (high + low + close) / 3
    if method == "standard":
        levels = {
            "PP": pp,
            "R1": 2 * pp - low, "R2": pp + (high - low), "R3": high + 2 * (pp - low),
            "S1": 2 * pp - high, "S2": pp - (high - low), "S3": low - 2 * (high - pp),
        }
    elif method == "fibonacci":
        levels = {
            "PP": pp,
            "R1": pp + 0.382 * (high - low), "R2": pp + 0.618 * (high - low), "R3": pp + 1.0 * (high - low),
            "S1": pp - 0.382 * (high - low), "S2": pp - 0.618 * (high - low), "S3": pp - 1.0 * (high - low),
        }
    elif method == "camarilla":
        levels = {
            "PP": pp,
            "R1": close + (high - low) * 1.1 / 12, "R2": close + (high - low) * 1.1 / 6, "R3": close + (high - low) * 1.1 / 4, "R4": close + (high - low) * 1.1 / 2,
            "S1": close - (high - low) * 1.1 / 12, "S2": close - (high - low) * 1.1 / 6, "S3": close - (high - low) * 1.1 / 4, "S4": close - (high - low) * 1.1 / 2,
        }
    elif method == "woodie":
        pp = (high + low + 2 * close) / 4
        levels = {
            "PP": pp,
            "R1": 2 * pp - low, "R2": pp + (high - low), "R3": high + 2 * (pp - low),
            "S1": 2 * pp - high, "S2": pp - (high - low), "S3": low - 2 * (high - pp),
        }
    else:
        return {"error": True, "message": f"Unknown method: {method}", "data": None}
    return {
        "error": False,
        "message": f"Pivot points ({method}) calculated for {symbol}",
        "data": {"method": method, "timeframe": timeframe, "levels": {k: round(v, digits) for k, v in levels.items()}}
    }


def ichimoku(client, symbol: str, timeframe: str = "H1",
              tenkan_period: int = 9, kijun_period: int = 26, senkou_period: int = 52) -> Dict[str, Any]:
    max_period = max(tenkan_period, kijun_period, senkou_period) * 2
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=max_period)
    if df is None or len(df) < max_period:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time')
    def calc_line(period):
        return df_sorted['high'].rolling(period).max().combine_first(df_sorted['high'].expanding().max()).values, \
               df_sorted['low'].rolling(period).min().combine_first(df_sorted['low'].expanding().min()).values
    tenkan_high, tenkan_low = calc_line(tenkan_period)
    kijun_high, kijun_low = calc_line(kijun_period)
    span_a_high, span_a_low = calc_line(senkou_period)
    tenkan = (tenkan_high + tenkan_low) / 2
    kijun = (kijun_high + kijun_low) / 2
    senkou_a = ((tenkan + kijun) / 2)
    senkou_b = ((span_a_high + span_a_low) / 2)
    chikou = df_sorted['close'].shift(-kijun_period)
    last_tenkan = tenkan[-1] if len(tenkan) > 0 else 0
    last_kijun = kijun[-1] if len(kijun) > 0 else 0
    last_senkou_a = senkou_a[-senkou_period] if len(senkou_a) > senkou_period else senkou_a[-1] if len(senkou_a) > 0 else 0
    last_senkou_b = senkou_b[-senkou_period] if len(senkou_b) > senkou_period else senkou_b[-1] if len(senkou_b) > 0 else 0
    current_close = df_sorted['close'].iloc[-1]
    cloud_top = max(last_senkou_a, last_senkou_b)
    cloud_bottom = min(last_senkou_a, last_senkou_b)
    in_cloud = cloud_bottom <= current_close <= cloud_top
    above_cloud = current_close > cloud_top
    below_cloud = current_close < cloud_bottom
    cloud_color = "green" if last_senkou_a > last_senkou_b else "red"
    tk_cross = "BUY" if last_tenkan > last_kijun else "SELL" if last_tenkan < last_kijun else "NEUTRAL"
    digits = symbol_info_digits(symbol, client)
    return {
        "error": False,
        "message": f"Ichimoku: TK={tk_cross}, Cloud={cloud_color}, Price={'Above' if above_cloud else 'Below' if below_cloud else 'In'} Cloud",
        "data": {
            "tenkan_sen": round(last_tenkan, digits),
            "kijun_sen": round(last_kijun, digits),
            "senkou_span_a": round(last_senkou_a, digits),
            "senkou_span_b": round(last_senkou_b, digits),
            "chikou_span": round(chikou.iloc[-1] if len(chikou) > 0 else 0, digits),
            "current_price": round(current_close, digits),
            "cloud_top": round(cloud_top, digits),
            "cloud_bottom": round(cloud_bottom, digits),
            "price_vs_cloud": "above" if above_cloud else "below" if below_cloud else "in_cloud",
            "cloud_color": cloud_color,
            "tk_cross_signal": tk_cross,
        }
    }


def support_resistance(client, symbol: str, timeframe: str = "H1", lookback: int = 200, min_touches: int = 2) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 30:
        return {"error": True, "message": "Not enough data", "data": None}
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df['close'].iloc[0]
    bins = 50
    highs = df['high'].values
    lows = df['low'].values
    all_levels = np.concatenate([highs, lows])
    hist, edges = np.histogram(all_levels, bins=bins)
    support_zones = []
    resistance_zones = []
    digits = symbol_info_digits(symbol, client)
    for i in range(len(hist)):
        if hist[i] >= min_touches * (len(df) / 100):
            level = (edges[i] + edges[i + 1]) / 2
            strength = int(min(hist[i], 20))
            if level < current:
                support_zones.append({"level": round(level, digits), "strength": strength, "touches": int(hist[i])})
            elif level > current:
                resistance_zones.append({"level": round(level, digits), "strength": strength, "touches": int(hist[i])})
    support_zones.sort(key=lambda x: x["level"], reverse=True)
    resistance_zones.sort(key=lambda x: x["level"])
    nearest_support = support_zones[0] if support_zones else None
    nearest_resistance = resistance_zones[0] if resistance_zones else None
    return {
        "error": False,
        "message": f"Nearest S: {nearest_support['level'] if nearest_support else 'N/A'} / R: {nearest_resistance['level'] if nearest_resistance else 'N/A'}",
        "data": {
            "current_price": round(current, digits),
            "nearest_support": nearest_support,
            "nearest_resistance": nearest_resistance,
            "support_zones": support_zones[:5],
            "resistance_zones": resistance_zones[:5],
        }
    }


def market_structure(client, symbol: str, timeframe: str = "H1", lookback: int = 100) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 20:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time')
    highs = df_sorted['high'].values
    lows = df_sorted['low'].values
    closes = df_sorted['close'].values
    swing_highs = []
    swing_lows = []
    for i in range(1, len(df_sorted) - 1):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            swing_highs.append({"index": i, "price": highs[i], "time": df_sorted.iloc[i]['time']})
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            swing_lows.append({"index": i, "price": lows[i], "time": df_sorted.iloc[i]['time']})
    trend = "RANGING"
    if len(swing_highs) > 1 and len(swing_lows) > 1:
        sh_prices = [s["price"] for s in swing_highs[-5:]]
        sl_prices = [s["price"] for s in swing_lows[-5:]]
        if len(sh_prices) >= 2 and sh_prices[-1] > sh_prices[-2] and sl_prices[-1] > sl_prices[-2]:
            trend = "UPTREND"
        elif len(sh_prices) >= 2 and sh_prices[-1] < sh_prices[-2] and sl_prices[-1] < sl_prices[-2]:
            trend = "DOWNTREND"
    bos_signals = []
    for i in range(1, min(len(swing_highs), 5)):
        if swing_highs[-i]["price"] > swing_highs[-(i + 1)]["price"]:
            bos_signals.append(f"BOS_UP_{i}")
        if swing_lows[-i]["price"] < swing_lows[-(i + 1)]["price"]:
            bos_signals.append(f"BOS_DOWN_{i}")
    choch_signals = []
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        last_sh = swing_highs[-1]
        prev_sh = swing_highs[-2]
        last_sl = swing_lows[-1]
        prev_sl = swing_lows[-2]
        if last_sh["price"] > prev_sh["price"] and last_sl["price"] < prev_sl["price"]:
            choch_signals.append("CHOCH_BEARISH")
        if last_sh["price"] < prev_sh["price"] and last_sl["price"] > prev_sl["price"]:
            choch_signals.append("CHOCH_BULLISH")
    digits = symbol_info_digits(symbol, client)
    return {
        "error": False,
        "message": f"Market structure: {trend}",
        "data": {
            "trend": trend,
            "swing_highs": [{"price": round(s["price"], digits), "index": s["index"]} for s in swing_highs[-10:]],
            "swing_lows": [{"price": round(s["price"], digits), "index": s["index"]} for s in swing_lows[-10:]],
            "bos_signals": bos_signals[-5:],
            "choch_signals": choch_signals,
        }
    }


def order_blocks(client, symbol: str, timeframe: str = "H1", lookback: int = 100) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 20:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time')
    bullish_blocks = []
    bearish_blocks = []
    for i in range(2, len(df_sorted)):
        prev2 = df_sorted.iloc[i - 2]
        prev1 = df_sorted.iloc[i - 1]
        curr = df_sorted.iloc[i]
        if prev2['close'] < prev2['open'] and prev1['close'] > prev1['open'] and curr['close'] > curr['open']:
            bullish_blocks.append({
                "index": i - 1, "high": prev1['high'], "low": prev1['low'],
                "open": prev1['open'], "close": prev1['close'],
                "time": str(prev1['time'])
            })
        if prev2['close'] > prev2['open'] and prev1['close'] < prev1['open'] and curr['close'] < curr['open']:
            bearish_blocks.append({
                "index": i - 1, "high": prev1['high'], "low": prev1['low'],
                "open": prev1['open'], "close": prev1['close'],
                "time": str(prev1['time'])
            })
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df['close'].iloc[0]
    digits = symbol_info_digits(symbol, client)
    bullish_nearby = [b for b in bullish_blocks if b['high'] < current][-3:] if bullish_blocks else []
    bearish_nearby = [b for b in bearish_blocks if b['low'] > current][:3] if bearish_blocks else []
    return {
        "error": False,
        "message": f"Found {len(bullish_blocks)} bullish + {len(bearish_blocks)} bearish order blocks",
        "data": {
            "current_price": round(current, digits),
            "bullish_blocks_nearby": [{"high": round(b["high"], digits), "low": round(b["low"], digits)} for b in bullish_nearby],
            "bearish_blocks_nearby": [{"high": round(b["high"], digits), "low": round(b["low"], digits)} for b in bearish_nearby],
            "total_bullish": len(bullish_blocks),
            "total_bearish": len(bearish_blocks),
        }
    }


def heikin_ashi(client, symbol: str, timeframe: str = "H1", count: int = 50) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=count)
    if df is None or len(df) < 3:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').copy()
    ha = pd.DataFrame(index=df_sorted.index)
    ha_close = (df_sorted['open'] + df_sorted['high'] + df_sorted['low'] + df_sorted['close']) / 4
    ha_open = [df_sorted['open'].iloc[0]]
    for i in range(1, len(df_sorted)):
        ha_open.append((ha_open[-1] + ha_close.iloc[i - 1]) / 2)
    ha['open'] = ha_open
    ha['close'] = ha_close.values
    ha['high'] = df_sorted[['high', 'open', 'close']].max(axis=1).values
    ha['low'] = df_sorted[['low', 'open', 'close']].min(axis=1).values
    ha['direction'] = ['BULLISH' if ha['close'].iloc[i] >= ha['open'].iloc[i] else 'BEARISH' for i in range(len(ha))]
    consecutive_bullish = 0
    consecutive_bearish = 0
    for d in reversed(ha['direction'].values):
        if d == 'BULLISH':
            consecutive_bullish += 1
            consecutive_bearish = 0
        else:
            consecutive_bearish += 1
            consecutive_bullish = 0
    digits = symbol_info_digits(symbol, client)
    trend = "STRONG_BULLISH" if consecutive_bullish >= 5 else "BULLISH" if consecutive_bullish >= 2 else \
            "STRONG_BEARISH" if consecutive_bearish >= 5 else "BEARISH" if consecutive_bearish >= 2 else "NEUTRAL"
    return {
        "error": False,
        "message": f"Heikin-Ashi trend: {trend}",
        "data": {
            "trend": trend,
            "consecutive_bullish": consecutive_bullish,
            "consecutive_bearish": consecutive_bearish,
            "last_candle": {
                "open": round(ha['open'].iloc[-1], digits),
                "close": round(ha['close'].iloc[-1], digits),
                "high": round(ha['high'].iloc[-1], digits),
                "low": round(ha['low'].iloc[-1], digits),
                "direction": ha['direction'].iloc[-1],
            }
        }
    }


def mtf_trend(client, symbol: str, timeframes: Optional[List[str]] = None) -> Dict[str, Any]:
    if timeframes is None:
        timeframes = ["M5", "M15", "H1", "H4", "D1"]
    results = {}
    alignments = []
    for tf in timeframes:
        try:
            df = client.market.get_candles_latest(symbol_name=symbol, timeframe=tf, count=50)
            if df is None or len(df) < 20:
                continue
            df_sorted = df.sort_values('time')
            ema20 = df_sorted['close'].ewm(span=20).mean().iloc[-1]
            ema50 = df_sorted['close'].ewm(span=50).mean().iloc[-1] if len(df_sorted) >= 50 else ema20
            last_close = df_sorted['close'].iloc[-1]
            if last_close > ema20 > ema50:
                trend = "BULLISH"
            elif last_close < ema20 < ema50:
                trend = "BEARISH"
            elif ema20 > ema50:
                trend = "BULLISH_ALERT"
            elif ema20 < ema50:
                trend = "BEARISH_ALERT"
            else:
                trend = "NEUTRAL"
            results[tf] = trend
            alignments.append(trend)
        except Exception as e:
            results[tf] = f"ERROR: {e}"
    bullish_count = sum(1 for a in alignments if "BULLISH" in a)
    bearish_count = sum(1 for a in alignments if "BEARISH" in a)
    if bullish_count >= 4:
        verdict = "STRONG_BULLISH"
    elif bullish_count >= 3:
        verdict = "BULLISH"
    elif bearish_count >= 4:
        verdict = "STRONG_BEARISH"
    elif bearish_count >= 3:
        verdict = "BEARISH"
    else:
        verdict = "MIXED"
    return {
        "error": False,
        "message": f"MTF trend: {verdict} ({bullish_count}B/{bearish_count}S on {len(results)} TFs)",
        "data": {"timeframes": results, "verdict": verdict, "bullish_count": bullish_count, "bearish_count": bearish_count}
    }


def atr_stop_levels(client, symbol: str, timeframe: str = "H1", atr_multiplier: float = 2.0, lookback: int = 100) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 20:
        return {"error": True, "message": "Not enough data", "data": None}
    atr = (df['high'] - df['low']).mean()
    price = client.market.get_symbol_price(symbol)
    bid = price.get("bid", 0) if price else 0
    ask = price.get("ask", 0) if price else 0
    digits = symbol_info_digits(symbol, client)
    return {
        "error": False,
        "message": f"ATR={atr:.{digits}f} × {atr_multiplier} = {atr * atr_multiplier:.{digits}f}",
        "data": {
            "atr": round(atr, digits),
            "atr_multiplier": atr_multiplier,
            "long_stop_loss": round(bid - atr * atr_multiplier, digits),
            "long_target_1": round(bid + atr * atr_multiplier, digits),
            "long_target_2": round(bid + atr * atr_multiplier * 2, digits),
            "long_target_3": round(bid + atr * atr_multiplier * 3, digits),
            "short_stop_loss": round(ask + atr * atr_multiplier, digits),
            "short_target_1": round(ask - atr * atr_multiplier, digits),
            "short_target_2": round(ask - atr * atr_multiplier * 2, digits),
            "short_target_3": round(ask - atr * atr_multiplier * 3, digits),
            "bid": round(bid, digits),
            "ask": round(ask, digits),
        }
    }
