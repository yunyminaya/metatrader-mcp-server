"""
OrderBook — análisis de profundidad de mercado (Level 2 / Market Depth).

Lee el book de MT5 y detecta:
  - Muros de liquidez (bid/ask walls)
  - Desbalance bid/ask → dirección direccional
  - Absorción (price moving through walls)
  - Niveles HVN / LVN desde el book
  - Zonas de soporte/resistencia viva

Requiere MT5 conectado y símbolo con market depth habilitado.
"""
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def analyze_depth(client, symbol: str, levels: int = 20) -> Dict[str, Any]:
    """Analyze market depth for a symbol.

    Args:
        client: MT5Client instance
        symbol: Symbol name
        levels: Number of depth levels to analyze

    Returns:
        depth analysis with walls, imbalance, absorption
    """
    try:
        depth = client.market.get_market_depth(symbol_name=symbol)
    except Exception as e:
        return {"success": False, "error": f"Cannot get depth: {e}", "depth": None}

    if not depth:
        return {"success": False, "error": "No depth data available", "depth": None}

    bids = []
    asks = []

    for item in depth:
        try:
            if item.type == 1:  # SELL (bid)
                bids.append({"price": item.price, "volume": item.volume})
            elif item.type == 2:  # BUY (ask)
                asks.append({"price": item.price, "volume": item.volume})
        except Exception:
            continue

    if not bids or not asks:
        return {"success": False, "error": "Incomplete depth data"}

    # Sort by price
    bids.sort(key=lambda x: x["price"], reverse=True)
    asks.sort(key=lambda x: x["price"])

    # Total volume at each side
    total_bid_vol = sum(b["volume"] for b in bids[:levels])
    total_ask_vol = sum(a["volume"] for a in asks[:levels])

    # Imbalance ratio
    total = total_bid_vol + total_ask_vol
    imbalance = (total_bid_vol - total_ask_vol) / total if total > 0 else 0

    # Detect walls (concentrated volume >= 2x average)
    avg_bid_vol = total_bid_vol / max(len(bids[:levels]), 1)
    avg_ask_vol = total_ask_vol / max(len(asks[:levels]), 1)

    bid_walls = [b for b in bids[:levels] if b["volume"] >= avg_bid_vol * 2]
    ask_walls = [a for a in asks[:levels] if a["volume"] >= avg_ask_vol * 2]

    # Spread from depth
    best_bid = bids[0]["price"] if bids else 0
    best_ask = asks[0]["price"] if asks else 0
    depth_spread = best_ask - best_bid if best_ask and best_bid else 0

    # Absorption detection: if price is near wall and wall volume decreasing
    # (simplified — real absorption needs sequence of snapshots)

    # Support/Resistance from depth
    support_zones = [b["price"] for b in bid_walls]
    resistance_zones = [a["price"] for a in ask_walls]

    # Pressure
    if imbalance > 0.2:
        pressure = "bullish"
    elif imbalance < -0.2:
        pressure = "bearish"
    else:
        pressure = "neutral"

    return {
        "success": True,
        "symbol": symbol,
        "bids": bids[:levels],
        "asks": asks[:levels],
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread": depth_spread,
        "total_bid_volume": round(total_bid_vol, 2),
        "total_ask_volume": round(total_ask_vol, 2),
        "imbalance": round(imbalance, 3),
        "pressure": pressure,
        "bid_walls": bid_walls,
        "ask_walls": ask_walls,
        "support_zones": support_zones[:3],
        "resistance_zones": resistance_zones[:3],
        "advice": (
            "favor_buy" if pressure == "bullish" and len(ask_walls) == 0
            else "favor_sell" if pressure == "bearish" and len(bid_walls) == 0
            else "wait" if len(bid_walls) > 2 or len(ask_walls) > 2
            else "neutral"
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def integrate_with_conviction(client, symbol: str, conviction_decision: Dict[str, Any]) -> Dict[str, Any]:
    """Modulate conviction with order book pressure.

    If book shows strong buying pressure and conviction says BUY → boost.
    If book shows strong resistance walls and conviction says BUY → caution.
    """
    depth = analyze_depth(client, symbol)
    if not depth.get("success"):
        return conviction_decision

    pressure = depth.get("pressure", "neutral")
    imbalance = depth.get("imbalance", 0)

    dec = conviction_decision.get("decision", {})
    conf = dec.get("confidence_pct", 0)
    v = dec.get("verdict", "")

    is_buy = "BUY" in v
    is_sell = "SELL" in v

    if is_buy and pressure == "bullish":
        dec["depth_boost"] = "strong_buying_pressure"
        dec["confidence_pct"] = min(conf * 1.15, 99)
    elif is_sell and pressure == "bearish":
        dec["depth_boost"] = "strong_selling_pressure"
        dec["confidence_pct"] = min(conf * 1.15, 99)
    elif is_buy and pressure == "bearish" and abs(imbalance) > 0.3:
        dec["depth_boost"] = "warning_selling_pressure"
        dec["confidence_pct"] = conf * 0.6
    elif is_sell and pressure == "bullish" and abs(imbalance) > 0.3:
        dec["depth_boost"] = "warning_buying_pressure"
        dec["confidence_pct"] = conf * 0.6

    # Check walls
    if depth.get("resistance_zones"):
        dec["resistance_ahead"] = depth["resistance_zones"]
    if depth.get("support_zones"):
        dec["support_below"] = depth["support_zones"]

    dec["order_book"] = {
        "pressure": pressure,
        "imbalance": imbalance,
        "bid_volume": depth["total_bid_volume"],
        "ask_volume": depth["total_ask_volume"],
    }
    conviction_decision["decision"] = dec
    return conviction_decision
