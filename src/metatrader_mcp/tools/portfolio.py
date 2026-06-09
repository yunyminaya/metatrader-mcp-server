import logging
import math
from typing import Dict, Any, List, Optional, Tuple

logger = logging.getLogger(__name__)


def pair_trade_signal(client, symbol_long: str, symbol_short: str, timeframe: str = "H1", lookback: int = 100, z_entry: float = 2.0, z_exit: float = 0.5) -> Dict[str, Any]:
    try:
        df1 = client.market.get_candles_latest(symbol_name=symbol_long, timeframe=timeframe, count=lookback)
        df2 = client.market.get_candles_latest(symbol_name=symbol_short, timeframe=timeframe, count=lookback)
    except Exception as e:
        return {"error": True, "message": str(e), "data": None}
    if df1 is None or df2 is None or len(df1) < 20 or len(df2) < 20:
        return {"error": True, "message": "Not enough data for both symbols", "data": None}
    df1_sorted = df1.sort_values('time')
    df2_sorted = df2.sort_values('time')
    merged = df1_sorted[['time', 'close']].merge(df2_sorted[['time', 'close']], on='time', suffixes=('_1', '_2'))
    if len(merged) < 20:
        return {"error": True, "message": "Not enough aligned data points", "data": None}
    price1 = merged['close_1'].values
    price2 = merged['close_2'].values
    ratio = price1 / price2
    mean = ratio.mean()
    std = ratio.std()
    if std == 0:
        return {"error": True, "message": "Zero standard deviation in spread", "data": None}
    current_ratio = ratio[-1]
    z_score = (current_ratio - mean) / std
    correlation = float(merged[['close_1', 'close_2']].corr().iloc[0, 1])
    signal = "NEUTRAL"
    message = "Spread within normal range"
    if z_score > z_entry:
        signal = "SHORT_SPREAD"
        message = f"Sell {symbol_long}, Buy {symbol_short} (ratio {current_ratio:.5f} is {z_score:.1f}σ above mean)"
    elif z_score < -z_entry:
        signal = "LONG_SPREAD"
        message = f"Buy {symbol_long}, Sell {symbol_short} (ratio {current_ratio:.5f} is {z_score:.1f}σ below mean)"
    elif abs(z_score) < z_exit:
        signal = "EXIT"
        message = "Spread mean-reverted"
    return {
        "error": False,
        "message": message,
        "data": {
            "symbol_long": symbol_long,
            "symbol_short": symbol_short,
            "current_ratio": round(current_ratio, 5),
            "mean_ratio": round(mean, 5),
            "std_ratio": round(std, 5),
            "z_score": round(z_score, 2),
            "correlation": round(correlation, 3),
            "signal": signal,
        }
    }


def basket_market_order(client, orders: List[dict]) -> Dict[str, Any]:
    results = []
    errors = []
    for order in orders:
        symbol = order.get("symbol")
        order_type = order.get("type", "BUY")
        volume = order.get("volume", 0.01)
        sl = order.get("stop_loss", 0)
        tp = order.get("take_profit", 0)
        try:
            result = client.order.place_market_order(type=order_type, symbol=symbol, volume=volume)
            if result.get("error"):
                errors.append({"symbol": symbol, "error": result.get("message", "unknown")})
            else:
                pos_id = getattr(result.get("data"), 'order', None) or getattr(result.get("data"), 'ticket', None)
                if pos_id and (sl or tp):
                    client.order.modify_position(id=pos_id, stop_loss=sl, take_profit=tp)
                results.append({"symbol": symbol, "type": order_type, "volume": volume, "result": result})
        except Exception as e:
            errors.append({"symbol": symbol, "error": str(e)})
    return {
        "error": len(results) == 0,
        "message": f"Basket: {len(results)} placed, {len(errors)} errors",
        "data": {"results": results, "errors": errors, "total_orders": len(orders)}
    }


def hedge_exposure(client, hedge_symbol: str, hedge_ratio: float = 1.0) -> Dict[str, Any]:
    try:
        positions = client.order.get_all_positions()
    except Exception as e:
        return {"error": True, "message": str(e), "data": None}
    if positions is None or positions.index.size == 0:
        return {"error": True, "message": "No open positions to hedge", "data": None}
    net_long_volume = 0.0
    net_short_volume = 0.0
    for _, pos in positions.iterrows():
        if pos["type"] == "BUY":
            net_long_volume += pos["volume"]
        else:
            net_short_volume += pos["volume"]
    net_exposure = net_long_volume - net_short_volume
    hedge_volume = round(abs(net_exposure) * hedge_ratio, 2)
    if hedge_volume < 0.01:
        return {"error": False, "message": "No net exposure to hedge", "data": {"net_exposure": net_exposure, "hedge_needed": 0}}
    hedge_type = "SELL" if net_exposure > 0 else "BUY"
    try:
        result = client.order.place_market_order(type=hedge_type, symbol=hedge_symbol, volume=hedge_volume)
    except Exception as e:
        return {"error": True, "message": f"Hedge failed: {e}", "data": None}
    return {
        "error": result.get("error", False),
        "message": f"Hedged {hedge_volume} {hedge_type} on {hedge_symbol}",
        "data": {
            "net_long_volume": net_long_volume,
            "net_short_volume": net_short_volume,
            "net_exposure": net_exposure,
            "hedge_symbol": hedge_symbol,
            "hedge_type": hedge_type,
            "hedge_volume": hedge_volume,
            "hedge_ratio": hedge_ratio,
            "trade_result": result,
        }
    }


def correlation_hedge(client, primary_symbol: str, hedge_symbol: str, position_type: str, volume: float, correlation_threshold: float = 0.7) -> Dict[str, Any]:
    result = pair_trade_signal(client, primary_symbol, hedge_symbol)
    if result.get("error"):
        return result
    corr = result["data"]["correlation"]
    if abs(corr) < correlation_threshold:
        return {"error": False, "message": f"Correlation {corr:.2f} below threshold {correlation_threshold}. No hedge needed.", "data": {"correlation": corr, "hedged": False}}
    hedge_direction = "SELL" if position_type.upper() == "BUY" else "BUY"
    hedge_vol = round(volume * abs(corr), 2)
    try:
        hedge_result = client.order.place_market_order(type=hedge_direction, symbol=hedge_symbol, volume=hedge_vol)
    except Exception as e:
        return {"error": True, "message": f"Hedge trade failed: {e}", "data": None}
    return {
        "error": False,
        "message": f"Hedged {primary_symbol} with {hedge_symbol} (corr={corr:.2f})",
        "data": {
            "primary_symbol": primary_symbol,
            "hedge_symbol": hedge_symbol,
            "correlation": corr,
            "position_type": position_type,
            "hedge_type": hedge_direction,
            "hedge_volume": hedge_vol,
            "hedge_result": hedge_result,
        }
    }
