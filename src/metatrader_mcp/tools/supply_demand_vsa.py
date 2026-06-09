import logging
import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _digits(client, symbol: str) -> int:
    info = client.market.get_symbol_info(symbol)
    return info.get("digits", 5) if isinstance(info, dict) else 5


def supply_demand_zones(client, symbol: str, timeframe: str = "H1", lookback: int = 200) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 30:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    supply_zones = []
    demand_zones = []
    for i in range(2, len(df_sorted) - 2):
        base = df_sorted.iloc[i]
        before = df_sorted.iloc[i - 1]
        after = df_sorted.iloc[i + 1]
        after2 = df_sorted.iloc[i + 2]
        # Supply: strong bearish move after a base (high volume body)
        if base['close'] < base['open'] and abs(base['close'] - base['open']) > (base['high'] - base['low']) * 0.6:
            if after['close'] < after['open'] and after2['close'] < after2['open']:
                supply_zones.append({
                    "zone_high": base['high'], "zone_low": base['low'],
                    "strength": min(5, int(abs(base['close'] - base['open']) / (base['high'] - base['low'] + 0.001) * 10)),
                    "index": i, "type": "SUPPLY"
                })
        # Demand: strong bullish move after a base
        if base['close'] > base['open'] and abs(base['close'] - base['open']) > (base['high'] - base['low']) * 0.6:
            if after['close'] > after['open'] and after2['close'] > after2['open']:
                demand_zones.append({
                    "zone_high": base['high'], "zone_low": base['low'],
                    "strength": min(5, int(abs(base['close'] - base['open']) / (base['high'] - base['low'] + 0.001) * 10)),
                    "index": i, "type": "DEMAND"
                })
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df_sorted['close'].iloc[-1]
    d = _digits(client, symbol)
    nearby_supply = [z for z in reversed(supply_zones) if z['zone_low'] > current][:3] if supply_zones else []
    nearby_demand = [z for z in reversed(demand_zones) if z['zone_high'] < current][:3] if demand_zones else []
    return {
        "error": False,
        "message": f"Supply: {len(nearby_supply)} above / Demand: {len(nearby_demand)} below",
        "data": {
            "current_price": round(current, d),
            "supply_above": [{"high": round(z["zone_high"], d), "low": round(z["zone_low"], d), "strength": z["strength"]} for z in nearby_supply],
            "demand_below": [{"high": round(z["zone_high"], d), "low": round(z["zone_low"], d), "strength": z["strength"]} for z in nearby_demand],
            "total_supply_zones": len(supply_zones),
            "total_demand_zones": len(demand_zones),
        }
    }


def vsa_analysis(client, symbol: str, timeframe: str = "H1", lookback: int = 100) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 20:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    vol_col = 'tick_volume' if 'tick_volume' in df_sorted.columns else 'real_volume' if 'real_volume' in df_sorted.columns else None
    if vol_col is None:
        return {"error": True, "message": "No volume column", "data": None}
    avg_vol = df_sorted[vol_col].mean()
    signals = []
    spread = df_sorted['high'] - df_sorted['low']
    avg_spread = spread.mean()
    for i in range(len(df_sorted)):
        row = df_sorted.iloc[i]
        vol_ratio = row[vol_col] / max(avg_vol, 1)
        spread_ratio = (row['high'] - row['low']) / max(avg_spread, 0.0001)
        body = abs(row['close'] - row['open'])
        body_ratio = body / max(row['high'] - row['low'], 0.0001)
        is_up = row['close'] > row['open']
        is_down = row['close'] < row['open']
        if vol_ratio > 1.5 and spread_ratio > 1.3:
            if is_up and body_ratio > 0.6:
                signals.append({"type": "CLIMAX_BUYING", "index": i, "strength": "STRONG" if vol_ratio > 2 else "MODERATE"})
            elif is_down and body_ratio > 0.6:
                signals.append({"type": "CLIMAX_SELLING", "index": i, "strength": "STRONG" if vol_ratio > 2 else "MODERATE"})
        elif vol_ratio < 0.5 and spread_ratio < 0.7:
            if is_up:
                signals.append({"type": "NO_DEMAND_UPTICK", "index": i, "strength": "WARNING"})
            elif is_down:
                signals.append({"type": "NO_SUPPLY_DOWNTICK", "index": i, "strength": "WARNING"})
        elif vol_ratio > 1.8 and spread_ratio < 0.8:
            signals.append({"type": "ABSORPTION", "index": i, "strength": "NOTABLE"})
    last5 = signals[-5:] if signals else []
    bullish_count = sum(1 for s in signals if s["type"] in ["CLIMAX_SELLING", "NO_SUPPLY_DOWNTICK", "ABSORPTION"])
    bearish_count = sum(1 for s in signals if s["type"] in ["CLIMAX_BUYING", "NO_DEMAND_UPTICK"])
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df_sorted['close'].iloc[-1]
    return {
        "error": False,
        "message": f"VSA: {bullish_count} bullish / {bearish_count} bearish signals",
        "data": {
            "current_price": current,
            "recent_signals": [{"type": s["type"], "strength": s["strength"]} for s in last5],
            "bullish_signals": bullish_count,
            "bearish_signals": bearish_count,
            "avg_volume": round(avg_vol, 0),
            "interpretation": "BULLISH_VSA" if bullish_count > bearish_count * 1.5 else "BEARISH_VSA" if bearish_count > bullish_count * 1.5 else "NEUTRAL_VSA",
        }
    }


def cumulative_delta(client, symbol: str, tick_count: int = 1000) -> Dict[str, Any]:
    try:
        df = client.market.get_ticks_latest(symbol_name=symbol, count=tick_count)
    except Exception as e:
        return {"error": True, "message": f"Tick data unavailable: {e}", "data": None}
    if df is None or len(df) < 10:
        return {"error": True, "message": "Not enough tick data", "data": None}
    d = _digits(client, symbol)
    if 'bid' in df.columns and 'ask' in df.columns:
        df = df.sort_values('time').reset_index(drop=True)
        df['mid'] = (df['bid'] + df['ask']) / 2
        df['delta'] = df['mid'].diff().fillna(0)
        buy_volume = df[df['delta'] > 0]['delta'].abs().sum()
        sell_volume = df[df['delta'] < 0]['delta'].abs().sum()
        buy_ticks = int((df['delta'] > 0).sum())
        sell_ticks = int((df['delta'] < 0).sum())
    elif 'last' in df.columns:
        df = df.sort_values('time').reset_index(drop=True)
        df['delta'] = df['last'].diff().fillna(0)
        buy_volume = df[df['delta'] > 0]['delta'].abs().sum()
        sell_volume = df[df['delta'] < 0]['delta'].abs().sum()
        buy_ticks = int((df['delta'] > 0).sum())
        sell_ticks = int((df['delta'] < 0).sum())
    else:
        return {"error": True, "message": "Tick data missing bid/ask/last columns", "data": None}
    net_delta = buy_volume - sell_volume
    total_volume = buy_volume + sell_volume
    delta_ratio = buy_volume / max(sell_volume, 0.0001)
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else 0
    df['cumulative_delta'] = df['delta'].cumsum()
    divergence = False
    recent_price_change = df['mid'].iloc[-1] - df['mid'].iloc[-min(int(len(df)*0.2), 100)] if 'mid' in df else 0
    recent_delta_change = df['cumulative_delta'].iloc[-1] - df['cumulative_delta'].iloc[-min(int(len(df)*0.2), 100)]
    if recent_price_change > 0 and recent_delta_change < 0:
        divergence = True
        divergence_type = "BEARISH_DIVERGENCE"
    elif recent_price_change < 0 and recent_delta_change > 0:
        divergence = True
        divergence_type = "BULLISH_DIVERGENCE"
    return {
        "error": False,
        "message": f"Delta: {net_delta:.2f} ({buy_ticks}B/{sell_ticks}S) ratio={delta_ratio:.2f}" + (" " + divergence_type if divergence else ""),
        "data": {
            "current_price": round(current, d),
            "buy_volume": round(buy_volume, 2),
            "sell_volume": round(sell_volume, 2),
            "net_delta": round(net_delta, 2),
            "buy_ticks": buy_ticks,
            "sell_ticks": sell_ticks,
            "delta_ratio": round(delta_ratio, 2),
            "cumulative_delta": round(df['cumulative_delta'].iloc[-1], 2),
            "divergence": divergence,
            "divergence_type": divergence_type if divergence else None,
            "interpretation": "BUYING_PRESSURE" if net_delta > 0 else "SELLING_PRESSURE" if net_delta < 0 else "NEUTRAL",
        }
    }


def inside_bars_breakout(client, symbol: str, timeframe: str = "H1", lookback: int = 100) -> Dict[str, Any]:
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe=timeframe, count=lookback)
    if df is None or len(df) < 10:
        return {"error": True, "message": "Not enough data", "data": None}
    df_sorted = df.sort_values('time').reset_index(drop=True)
    inside_bars = []
    breakouts = []
    for i in range(1, len(df_sorted)):
        prev = df_sorted.iloc[i - 1]
        curr = df_sorted.iloc[i]
        if curr['high'] <= prev['high'] and curr['low'] >= prev['low']:
            inside_bars.append({"index": i, "high": curr['high'], "low": curr['low'], "mother_high": prev['high'], "mother_low": prev['low']})
        for ib in inside_bars[-3:]:
            if i == ib["index"] + 1:
                if curr['close'] > ib['mother_high']:
                    breakouts.append({"type": "BREAKOUT_UP", "index": i, "price": curr['close'], "mother_high": ib['mother_high'], "strength": "STRONG" if curr['close'] > ib['mother_high'] + (ib['mother_high'] - ib['mother_low']) * 0.3 else "MODERATE"})
                elif curr['close'] < ib['mother_low']:
                    breakouts.append({"type": "BREAKOUT_DOWN", "index": i, "price": curr['close'], "mother_low": ib['mother_low'], "strength": "STRONG" if curr['close'] < ib['mother_low'] - (ib['mother_high'] - ib['mother_low']) * 0.3 else "MODERATE"})
    price = client.market.get_symbol_price(symbol)
    current = price.get("bid") if price else df_sorted['close'].iloc[-1]
    d = _digits(client, symbol)
    return {
        "error": False,
        "message": f"{len(inside_bars)} inside bars, {len(breakouts)} breakouts",
        "data": {
            "current_price": round(current, d),
            "recent_inside_bars": [{"high": round(ib["high"], d), "low": round(ib["low"], d), "mother_high": round(ib["mother_high"], d), "mother_low": round(ib["mother_low"], d)} for ib in inside_bars[-5:]],
            "recent_breakouts": [{"type": b["type"], "price": round(b["price"], d), "strength": b["strength"]} for b in breakouts[-5:]],
            "total_inside_bars": len(inside_bars),
            "total_breakouts": len(breakouts),
        }
    }
