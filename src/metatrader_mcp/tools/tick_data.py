import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


def get_latest_ticks(client, symbol: str, count: int = 100) -> Dict[str, Any]:
    try:
        df = client.market.get_ticks_latest(symbol_name=symbol, count=count)
    except Exception as e:
        return {"error": True, "message": str(e), "data": None}
    if df is None or len(df) == 0:
        return {"error": True, "message": "No tick data", "data": None}
    ticks = df.head(20).to_dict(orient='records')
    last = ticks[0] if ticks else {}
    bid_volume = df['bid'].diff().abs().sum() if 'bid' in df else 0
    ask_volume = df['ask'].diff().abs().sum() if 'ask' in df else 0
    return {
        "error": False,
        "message": f"{len(df)} ticks retrieved",
        "data": {
            "symbol": symbol,
            "total_ticks": len(df),
            "latest_ticks": [{k: str(v) if isinstance(v, datetime) else v for k, v in t.items()} for t in ticks[:10]],
            "last_bid": last.get("bid"),
            "last_ask": last.get("ask"),
            "last_spread": round(last.get("ask", 0) - last.get("bid", 0), 5) if last.get("bid") and last.get("ask") else None,
            "bid_volume_delta": round(bid_volume, 2),
            "ask_volume_delta": round(ask_volume, 2),
        }
    }


def get_ticks_range_history(client, symbol: str, from_date: str, to_date: Optional[str] = None, count: int = 100000) -> Dict[str, Any]:
    try:
        from_date_dt = datetime.fromisoformat(from_date)
        to_date_dt = datetime.fromisoformat(to_date) if to_date else datetime.now()
    except ValueError as e:
        return {"error": True, "message": f"Invalid date format: {e}", "data": None}
    try:
        df = client.market.get_ticks_range(symbol_name=symbol, from_date=from_date_dt, to_date=to_date_dt, count=count)
    except Exception as e:
        return {"error": True, "message": str(e), "data": None}
    if df is None or len(df) == 0:
        return {"error": True, "message": "No tick data in range", "data": None}
    ticks_sample = df.head(50).to_dict(orient='records')
    return {
        "error": False,
        "message": f"{len(df)} ticks from {from_date} to {to_date or 'now'}",
        "data": {
            "symbol": symbol,
            "from_date": from_date,
            "to_date": to_date or datetime.now().isoformat(),
            "total_ticks": len(df),
            "sample_ticks": [{k: str(v) if isinstance(v, datetime) else v for k, v in t.items()} for t in ticks_sample[:10]],
        }
    }


def orderbook_depth(client, symbol: str, depth: int = 10) -> Dict[str, Any]:
    try:
        ob = client.market.get_market_depth(symbol_name=symbol, depth=depth)
    except Exception as e:
        return {"error": True, "message": str(e), "data": None}
    if not ob:
        return {"error": True, "message": "No market depth available", "data": None}
    return {
        "error": False,
        "message": f"DOM: {len(ob.get('bids', []))} bids / {len(ob.get('asks', []))} asks",
        "data": ob
    }
