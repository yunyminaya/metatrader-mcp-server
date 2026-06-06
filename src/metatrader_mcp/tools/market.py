"""
Market — contexto de mercado para MT5.

Incluye:
  - Trading sessions (London, NY, Asian, overlap)
  - High-impact news calendar (event-driven halt)
  - Spread/liquidity analysis
  - Best session for each symbol
"""
import logging
import math
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)

# ── High-impact economic events (monthly recurring) ────────────────────────────
# Approximate schedule: 1st week of month, specific UTC times
_HIGH_IMPACT_EVENTS = [
    {"name": "NFP (Non-Farm Payrolls)", "week": "first_friday", "time": "12:30", "currency": "USD"},
    {"name": "FOMC Rate Decision", "week": "variable", "time": "18:00", "currency": "USD"},
    {"name": "CPI (Consumer Price Index)", "week": "second_week", "time": "12:30", "currency": "USD"},
    {"name": "GDP (Gross Domestic Product)", "week": "last_week", "time": "12:30", "currency": "USD"},
    {"name": "ECB Rate Decision", "week": "variable", "time": "12:45", "currency": "EUR"},
    {"name": "BoE Rate Decision", "week": "variable", "time": "12:00", "currency": "GBP"},
    {"name": "BOJ Rate Decision", "week": "variable", "time": "03:00", "currency": "JPY"},
    {"name": "Retail Sales (US)", "week": "second_week", "time": "12:30", "currency": "USD"},
    {"name": "Industrial Production", "week": "third_week", "time": "13:15", "currency": "USD"},
    {"name": "Unemployment Claims", "week": "weekly", "time": "12:30", "currency": "USD", "every_week": True},
    {"name": "ISM Manufacturing PMI", "week": "first_monday", "time": "14:00", "currency": "USD"},
    {"name": "PMI Services", "week": "first_week", "time": "13:45", "currency": "USD"},
]

# EUR = EURUSD, EURGBP, EURJPY, EURCHF, EURAUD, EURNZD, EURCAD
# USD = all XXXUSD pairs
# GBP = GBPUSD, EURGBP, GBPJPY, GBPAUD, GBPCHF, GBPNZD, GBPCAD
# JPY = USDJPY, EURJPY, GBPJPY, AUDJPY, CHFJPY, CADJPY, NZDJPY
_CURRENCY_TO_SYMBOLS = {
    "USD": ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "NZDUSD",
            "EURUSD.FX", "GBPUSD.FX", "USDJPY.FX"],
    "EUR": ["EURUSD", "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURNZD", "EURCAD",
            "EURUSD.FX", "EURGBP.FX", "EURJPY.FX"],
    "GBP": ["GBPUSD", "EURGBP", "GBPJPY", "GBPAUD", "GBPCHF", "GBPNZD", "GBPCAD",
            "GBPUSD.FX", "EURGBP.FX", "GBPJPY.FX"],
    "JPY": ["USDJPY", "EURJPY", "GBPJPY", "AUDJPY", "CHFJPY", "CADJPY", "NZDJPY",
            "USDJPY.FX", "EURJPY.FX", "GBPJPY.FX"],
    "AUD": ["AUDUSD", "AUDJPY", "AUDNZD", "AUDCAD", "AUDCHF", "EURAUD", "GBPAUD"],
    "CAD": ["USDCAD", "EURCAD", "GBPCAD", "AUDCAD", "CADCHF", "CADJPY", "NZDCAD"],
    "CHF": ["USDCHF", "EURCHF", "GBPCHF", "AUDCHF", "CADCHF", "CHFJPY", "NZDCHF"],
    "NZD": ["NZDUSD", "NZDJPY", "NZDCHF", "NZDCAD", "AUDNZD", "EURNZD", "GBPNZD"],
}

_SYMBOL_TO_CURRENCY = {}
for cur, syms in _CURRENCY_TO_SYMBOLS.items():
    for s in syms:
        base = s.replace(".FX", "").replace(".", "")
        _SYMBOL_TO_CURRENCY[s] = cur


def get_currency(symbol: str) -> str:
    """Map symbol to primary currency for news check."""
    clean = symbol.upper().replace(".FX", "").replace(".", "")
    # Direct lookup
    if symbol.upper() in _SYMBOL_TO_CURRENCY:
        return _SYMBOL_TO_CURRENCY[symbol.upper()]
    # Extract from pair: EURUSD -> USD
    try:
        majors = {"USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD"}
        for c in majors:
            if clean.startswith(c):
                return c
    except Exception:
        pass
    return "USD"


def _get_current_week_day() -> tuple:
    """Returns (week_number, weekday_name, current_hour_utc)."""
    now = datetime.now(timezone.utc)
    week_num = now.isocalendar()[1]
    day_name = now.strftime("%A")
    hour = now.hour
    minute = now.minute
    return week_num, day_name, hour, minute, now


def _this_month_first_friday():
    """Returns day of month for first Friday."""
    from calendar import monthrange, weekday
    now = datetime.now(timezone.utc)
    first_day = weekday(now.year, now.month, 1)
    # weekday(): Mon=0, ..., Fri=4
    days_until_friday = (4 - first_day) % 7
    return 1 + days_until_friday


def check_high_impact_news(symbol: Optional[str] = None, hours_window: int = 2) -> Dict[str, Any]:
    """Check if there's a high-impact news event within hours_window.

    Returns:
        {
            "has_event": bool,
            "events": [...],
            "affected_symbols": [...],
            "advice": "HOLD" | "TRADE"
        }
    """
    week_num, day_name, hour, minute, now = _get_current_week_day()
    first_friday = _this_month_first_friday()

    near_events = []
    for event in _HIGH_IMPACT_EVENTS:
        event_hour, event_min = map(int, event["time"].split(":"))
        event_time = now.replace(hour=event_hour, minute=event_min, second=0, microsecond=0)
        delta = abs((now - event_time).total_seconds() / 3600)

        # Check if this event happens today (approximate)
        is_today = False
        w = event.get("week")
        if w == "first_friday" and day_name == "Friday":
            is_today = abs(now.day - first_friday) <= 2
        elif w == "first_monday" and day_name == "Monday":
            is_today = now.day <= 7
        elif w == "second_week" and 8 <= now.day <= 15:
            is_today = day_name in ("Tuesday", "Wednesday", "Thursday")
        elif w == "third_week" and 15 <= now.day <= 22:
            is_today = day_name in ("Tuesday", "Wednesday", "Thursday")
        elif w == "last_week":
            from calendar import monthrange
            last = monthrange(now.year, now.month)[1]
            is_today = now.day >= last - 5 and day_name in ("Wednesday", "Thursday")
        elif w == "weekly" or event.get("every_week"):
            is_today = day_name == "Thursday"
        elif w == "variable":
            # FOMC: ~6 weeks apart, approximate
            is_today = day_name == "Wednesday" and 15 <= now.day <= 22

        if is_today or delta <= hours_window:
            affected = _CURRENCY_TO_SYMBOLS.get(event["currency"], [])
            near_events.append({
                "name": event["name"],
                "currency": event["currency"],
                "time": event["time"],
                "hours_away": round(delta, 1),
                "affected_symbols": affected[:5],
            })

    # Filter by symbol if requested
    if symbol:
        sym_upper = symbol.upper().replace(".FX", "")
        filtered = [e for e in near_events if symbol.upper() in e.get("affected_symbols", []) or
                    sym_upper in e.get("affected_symbols", [])]
        near_events = filtered

    advice = "HOLD" if near_events else "TRADE"

    return {
        "has_event": len(near_events) > 0,
        "events": near_events,
        "advice": advice,
        "total_events_found": len(near_events),
    }


def active_sessions() -> Dict[str, Any]:
    """Return currently active trading sessions with quality scores."""
    now = datetime.now(timezone.utc)
    h = now.hour
    wd = now.weekday()

    sessions = []

    # Weekend check
    if wd >= 5:
        return {"sessions": ["weekend"], "best": None, "quality": 0, "message": "Weekend — no trading"}

    if 0 <= h < 6:
        sessions.append({"name": "asian", "quality": 3, "hours_left": 6 - h})
    if 6 <= h < 12:
        sessions.append({"name": "london_morning", "quality": 4, "hours_left": 12 - h})
    if 7 <= h < 9:
        sessions.append({"name": "london_open", "quality": 5, "hours_left": 9 - h})
    if 12 <= h < 15:
        sessions.append({"name": "london_ny_overlap", "quality": 5, "hours_left": 15 - h})
    if 12 <= h < 21:
        sessions.append({"name": "new_york", "quality": 4, "hours_left": 21 - h})
    if 20 <= h < 22:
        sessions.append({"name": "ny_close", "quality": 3, "hours_left": 22 - h})
    if 15 <= h < 24:
        sessions.append({"name": "post_europe", "quality": 2, "hours_left": 24 - h})
    if 22 <= h < 24:
        sessions.append({"name": "asian_pre", "quality": 1, "hours_left": 24 - h})

    best = max(sessions, key=lambda s: s["quality"]) if sessions else None
    quality = best["quality"] / 5 if best else 0

    return {
        "sessions": [s["name"] for s in sessions],
        "best": best["name"] if best else None,
        "quality": round(quality * 100, 0),
        "is_open": quality >= 0.4,
    }


def best_time_to_trade(symbol: str) -> Dict[str, Any]:
    """Return the best session and time for a specific symbol."""
    cur = get_currency(symbol)

    session_map = {
        "USD": {"best": "london_ny_overlap", "time": "12:00-15:00 UTC", "quality": 5},
        "EUR": {"best": "london_open", "time": "06:00-12:00 UTC", "quality": 5},
        "GBP": {"best": "london_open", "time": "06:00-09:00 UTC", "quality": 5},
        "JPY": {"best": "asian", "time": "00:00-06:00 UTC", "quality": 4},
        "AUD": {"best": "asian", "time": "00:00-06:00 UTC", "quality": 4},
        "NZD": {"best": "asian", "time": "00:00-06:00 UTC", "quality": 3},
        "CAD": {"best": "london_ny_overlap", "time": "12:00-15:00 UTC", "quality": 4},
        "CHF": {"best": "london_open", "time": "06:00-12:00 UTC", "quality": 4},
    }
    info = session_map.get(cur, {"best": "london", "time": "06:00-15:00 UTC", "quality": 3})
    return {"symbol": symbol, "currency": cur, "best_session": info["best"], "best_time": info["time"]}


def spread_analysis(client, symbol: str) -> Dict[str, Any]:
    """Analyze spread for a symbol. Returns current spread + historical avg."""
    try:
        price = client.market.get_symbol_price(symbol_name=symbol)
        if not price:
            return {"success": False, "error": "No price"}
    except Exception as e:
        return {"success": False, "error": str(e)}

    spread = price.get("spread", 0)
    spread_pips = spread / 10 if spread else 0  # approximate

    # Determine quality
    if spread_pips <= 5:
        quality = "excellent"
    elif spread_pips <= 10:
        quality = "good"
    elif spread_pips <= 20:
        quality = "fair"
    else:
        quality = "poor"

    return {
        "success": True,
        "symbol": symbol,
        "spread_points": spread,
        "spread_pips": round(spread_pips, 1),
        "quality": quality,
        "tradeable": spread_pips <= 15,
        "advice": "Trade" if spread_pips <= 15 else "Wait for tighter spread",
    }
