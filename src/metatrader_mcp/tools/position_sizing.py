import logging
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def calculate_size_by_risk(client, symbol: str, risk_percent: float, sl_pips: float) -> Dict[str, Any]:
    from metatrader_client.account.risk_calculations import calculate_lot_size_by_risk
    return calculate_lot_size_by_risk(client._connection, symbol, risk_percent, sl_pips)


def calculate_kelly(client, win_rate: float, avg_win: float, avg_loss: float, bankroll: float, kelly_fraction: float = 0.25) -> Dict[str, Any]:
    from metatrader_client.account.risk_calculations import calculate_kelly_size
    return calculate_kelly_size(win_rate, avg_win, avg_loss, bankroll, kelly_fraction)


def calculate_size_by_atr(client, symbol: str, risk_percent: float, atr_multiple: float = 2.0) -> Dict[str, Any]:
    import MetaTrader5 as mt5
    acc = mt5.account_info()
    if acc is None:
        return {"error": True, "message": "Cannot get account info", "data": None}
    balance = acc.balance
    risk_amount = balance * (risk_percent / 100)
    df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=100)
    if df is None or len(df) == 0:
        return {"error": True, "message": f"No data for {symbol}", "data": None}
    atr = (df['high'] - df['low']).mean()
    sl_distance = atr * atr_multiple
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return {"error": True, "message": f"Symbol {symbol} not found", "data": None}
    tick_value = symbol_info.trade_tick_value
    tick_size = symbol_info.trade_tick_size
    if tick_size == 0 or tick_value == 0:
        return {"error": True, "message": "Tick size/value zero", "data": None}
    sl_in_ticks = sl_distance / tick_size
    risk_per_lot = sl_in_ticks * tick_value
    if risk_per_lot <= 0:
        return {"error": True, "message": "Risk per lot is zero", "data": None}
    lot_step = symbol_info.volume_step
    min_lot = symbol_info.volume_min
    max_lot = symbol_info.volume_max
    raw_lot = risk_amount / risk_per_lot
    lot_size = max(min_lot, round(raw_lot / lot_step) * lot_step)
    lot_size = min(lot_size, max_lot)
    return {
        "error": False,
        "message": f"Lot size: {lot_size} (ATR={atr:.{symbol_info.digits}f}, risk={risk_percent}%)",
        "data": {
            "symbol": symbol,
            "lot_size": lot_size,
            "atr": round(atr, symbol_info.digits),
            "atr_multiple": atr_multiple,
            "sl_distance": round(sl_distance, symbol_info.digits),
            "risk_percent": risk_percent,
            "risk_amount_usd": round(risk_amount, 2),
            "account_balance": round(balance, 2),
        }
    }


def calculate_max_position_size(client, symbol: str) -> Dict[str, Any]:
    import MetaTrader5 as mt5
    acc = mt5.account_info()
    if acc is None:
        return {"error": True, "message": "No account info", "data": None}
    symbol_info = mt5.symbol_info(symbol)
    if symbol_info is None:
        return {"error": True, "message": f"Symbol {symbol} not found", "data": None}
    free_margin = acc.margin_free
    margin_req = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, symbol, symbol_info.volume_min, symbol_info.ask or symbol_info.bid)
    if margin_req is None or margin_req == 0:
        return {"error": True, "message": "Cannot calculate margin requirement", "data": None}
    max_lots = int(free_margin / margin_req) * symbol_info.volume_min
    max_lots = min(max_lots, symbol_info.volume_max)
    return {
        "error": False,
        "message": f"Max position size for {symbol}: {max_lots} lots",
        "data": {"symbol": symbol, "max_lots": max_lots, "free_margin": round(free_margin, 2), "margin_per_lot": round(margin_req, 2)}
    }
