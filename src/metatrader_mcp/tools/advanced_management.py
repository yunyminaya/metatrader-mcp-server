import logging
import math
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple, Union
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


def _digits(client, symbol: str) -> int:
    info = client.market.get_symbol_info(symbol)
    return info.get("digits", 5) if isinstance(info, dict) else 5


def chandelier_exit(client, symbol: str, timeframe: str = "H1", atr_multiplier: float = 3.0, lookback: int = 100) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 20:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    atr = (df_sorted['high'] - df_sorted['low']).mean()
    highest_high = df_sorted['high'].rolling(22).max().iloc[-1]
    lowest_low = df_sorted['low'].rolling(22).min().iloc[-1]
    long_stop = highest_high - atr * atr_multiplier
    short_stop = lowest_low + atr * atr_multiplier
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df_sorted['close'].iloc[-1]
    d = _digits(client, symbol)
    return {
        "error": False,
        "message": f"Chandelier Long SL: {long_stop:.{d}f} | Short SL: {short_stop:.{d}f}",
        "data": {
            "current_price": round(current, d),
            "long_stop_loss": round(long_stop, d),
            "short_stop_loss": round(short_stop, d),
            "atr": round(atr, d),
            "atr_multiplier": atr_multiplier,
            "highest_22_high": round(highest_high, d),
            "lowest_22_low": round(lowest_low, d),
        }
    }


def parabolic_sar(client, symbol: str, timeframe: str = "H1", lookback: int = 100, acceleration: float = 0.02, max_acceleration: float = 0.2) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 30:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    high = df_sorted['high'].values
    low = df_sorted['low'].values
    close = df_sorted['close'].values
    sar = np.zeros(len(df_sorted))
    af = acceleration
    is_up = True
    ep = low[0]
    sar[0] = high[0]
    for i in range(1, len(df_sorted)):
        if is_up:
            sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
            if sar[i] > low[i]:
                is_up = False
                sar[i] = ep
                af = acceleration
                ep = low[i]
            else:
                if high[i] > ep:
                    ep = high[i]
                    af = min(af + acceleration, max_acceleration)
        else:
            sar[i] = sar[i - 1] + af * (ep - sar[i - 1])
            if sar[i] < high[i]:
                is_up = True
                sar[i] = ep
                af = acceleration
                ep = high[i]
            else:
                if low[i] < ep:
                    ep = low[i]
                    af = min(af + acceleration, max_acceleration)
    last_sar = sar[-1]
    sar_direction = "BULLISH" if close[-1] > last_sar else "BEARISH" if close[-1] < last_sar else "NEUTRAL"
    for i in range(-1, -min(len(sar), 5), -1):
        if (sar[i] < close[i]) != (sar[i - 1] < close[i - 1]):
            flip_index = i
            break
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else close[-1]
    d = _digits(client, symbol)
    return {
        "error": False,
        "message": f"Parabolic SAR: {sar_direction} (current: {current:.{d}f} | SAR: {last_sar:.{d}f})",
        "data": {
            "current_price": round(current, d),
            "sar": round(last_sar, d),
            "direction": sar_direction,
            "acceleration": acceleration,
            "max_acceleration": max_acceleration,
            "recent_sar": [round(s, d) for s in sar[-10:]],
        }
    }


def moving_average_trail(client, symbol: str, timeframe: str = "H1", ma_period: int = 20, ma_type: str = "EMA") -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=ma_period * 3)
    if df is None or len(df) < ma_period:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    closes = df_sorted['close'].values
    if ma_type.upper() == "EMA":
        ma = pd.Series(closes).ewm(span=ma_period).mean().iloc[-1]
    elif ma_type.upper() == "SMA":
        ma = pd.Series(closes).rolling(ma_period).mean().iloc[-1]
    elif ma_type.upper() == "WMA":
        weights = np.arange(1, ma_period + 1)
        ma = np.dot(closes[-ma_period:], weights) / weights.sum()
    else:
        return {"error": True, "message": f"Unknown MA type: {ma_type}", "data": None}
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else closes[-1]
    d = _digits(client, symbol)
    direction = "BULLISH" if current > ma else "BEARISH" if current < ma else "NEUTRAL"
    distance_pct = abs(current - ma) / max(ma, 0.0001) * 100
    return {
        "error": False,
        "message": f"MA Trail ({ma_type} {ma_period}): {direction} at {ma:.{d}f} | distance {distance_pct:.1f}%",
        "data": {
            "current_price": round(current, d),
            "ma_value": round(ma, d),
            "ma_type": ma_type.upper(),
            "ma_period": ma_period,
            "direction": direction,
            "distance_pct": round(distance_pct, 2),
            "long_trail": round(ma, d) if direction == "BULLISH" else None,
            "short_trail": round(ma, d) if direction == "BEARISH" else None,
        }
    }


def session_breakout(client, symbol: str, session: str = "ASIAN", lookback: int = 20, timeframe: str = "M5") -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=lookback * 24)
    if df is None or len(df) < 20:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    df_sorted['hour'] = pd.to_datetime(df_sorted['time']).dt.hour
    if session.upper() == "ASIAN":
        session_df = df_sorted[df_sorted['hour'].between(0, 8)]
    elif session.upper() == "LONDON":
        session_df = df_sorted[df_sorted['hour'].between(7, 16)]
    elif session.upper() == "NY":
        session_df = df_sorted[df_sorted['hour'].between(12, 21)]
    elif session.upper() == "LONDON_NY":
        session_df = df_sorted[df_sorted['hour'].between(12, 21)]
    else:
        return {"error": True, "message": f"Unknown session: {session}", "data": None}
    if len(session_df) < 5:
        return {"error": True, "message": "Not enough session data", "data": None}
    avg_range = (session_df['high'] - session_df['low']).mean()
    avg_session_high = session_df['high'].mean()
    avg_session_low = session_df['low'].mean()
    today_hour = datetime.now(timezone.utc).hour
    if session.upper() == "ASIAN" and today_hour > 8:
        try:
            asian_high = df_sorted[df_sorted['hour'].between(0, 8)].iloc[-1]['high'] if len(df_sorted[df_sorted['hour'].between(0, 8)]) > 0 else 0
            asian_low = df_sorted[df_sorted['hour'].between(0, 8)].iloc[-1]['low'] if len(df_sorted[df_sorted['hour'].between(0, 8)]) > 0 else 0
        except Exception:
            asian_high = df_sorted['high'].iloc[-5:].max() if len(df_sorted) >= 5 else 0
            asian_low = df_sorted['low'].iloc[-5:].min() if len(df_sorted) >= 5 else 0
    else:
        asian_high = avg_session_high
        asian_low = avg_session_low
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df_sorted['close'].iloc[-1]
    d = _digits(client, symbol)
    if current > asian_high:
        breakout = "BULLISH_BREAKOUT"
    elif current < asian_low:
        breakout = "BEARISH_BREAKOUT"
    else:
        breakout = "INSIDE_RANGE"
    return {
        "error": False,
        "message": f"{session} session: {breakout}",
        "data": {
            "session": session,
            "current_price": round(current, d),
            "session_high": round(asian_high, d),
            "session_low": round(asian_low, d),
            "breakout": breakout,
            "avg_range": round(avg_range, d),
            "avg_session_high": round(avg_session_high, d),
            "avg_session_low": round(avg_session_low, d),
            "breakout_target": round(current + avg_range, d) if breakout == "BULLISH_BREAKOUT" else round(current - avg_range, d) if breakout == "BEARISH_BREAKOUT" else None,
        }
    }


def time_based_analysis(client, symbol: str, lookback_days: int = 30, timeframe: str = "H1") -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback_days * 24)
    if df is None or len(df) < 100:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    df_sorted['datetime'] = pd.to_datetime(df_sorted['time'], utc=True)
    df_sorted['hour'] = df_sorted['datetime'].dt.hour
    df_sorted['day_of_week'] = df_sorted['datetime'].dt.dayofweek
    df_sorted['direction'] = np.where(df_sorted['close'] > df_sorted['open'], 1, 0)
    df_sorted['range'] = df_sorted['high'] - df_sorted['low']
    hourly = df_sorted.groupby('hour').agg(
        avg_range=('range', 'mean'), win_rate=('direction', 'mean'), count=('direction', 'count')
    ).reset_index()
    daily = df_sorted.groupby('day_of_week').agg(
        avg_range=('range', 'mean'), win_rate=('direction', 'mean'), count=('direction', 'count')
    ).reset_index()
    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    best_hour = hourly.loc[hourly['win_rate'].idxmax()] if not hourly.empty else None
    worst_hour = hourly.loc[hourly['win_rate'].idxmin()] if not hourly.empty else None
    best_day = daily.loc[daily['win_rate'].idxmax()] if not daily.empty else None
    best_hour_int = int(best_hour['hour']) if best_hour is not None else None
    best_day_int = int(best_day['day_of_week']) if best_day is not None else None
    return {
        "error": False,
        "message": f"Best: {best_hour_int}:00 UTC ({best_hour['win_rate']*100:.0f}%) / {day_names[best_day_int] if best_day_int is not None else 'N/A'} ({best_day['win_rate']*100:.0f}%)" if best_hour is not None and best_day is not None else "Time analysis",
        "data": {
            "best_hour_utc": {"hour": best_hour_int, "win_rate": round(best_hour['win_rate'] * 100, 1) if best_hour is not None else None, "avg_range": round(best_hour['avg_range'], 5) if best_hour is not None else None} if best_hour is not None else None,
            "worst_hour_utc": {"hour": int(worst_hour['hour']), "win_rate": round(worst_hour['win_rate'] * 100, 1), "avg_range": round(worst_hour['avg_range'], 5)} if worst_hour is not None else None,
            "best_day": {"day": day_names[int(best_day['day_of_week'])], "win_rate": round(best_day['win_rate'] * 100, 1), "avg_range": round(best_day['avg_range'], 5)} if best_day is not None else None,
            "hourly_performance": [{"hour": int(r['hour']), "win_rate": round(r['win_rate'] * 100, 1), "avg_range": round(r['avg_range'], 5), "samples": int(r['count'])} for _, r in hourly.iterrows()],
            "daily_performance": [{"day": day_names[int(r['day_of_week'])], "win_rate": round(r['win_rate'] * 100, 1), "avg_range": round(r['avg_range'], 5), "samples": int(r['count'])} for _, r in daily.iterrows()],
        }
    }
