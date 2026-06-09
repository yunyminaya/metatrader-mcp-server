"""
MultiMarket — correlación de forex con mercados externos.

Conecta precios de:
  - XAUUSD (Gold)
  - XTIUSD / XBRUSD (Oil)
  - US30 / SP500 (US equities)
  - US 10Y Yield (bonos)

Y calcula correlación en vivo con cada par de forex.
Ajusta el bias de trading según la señal de los externos.
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "multimarket.json")

_state: Dict[str, Any] = {}

# Known relationships
_RELATIONSHIPS = {
    "EURUSD": {"XAUUSD": -0.3, "XTIUSD": 0.2, "US30": -0.2, "DX": 0.9},
    "GBPUSD": {"XAUUSD": -0.2, "XTIUSD": 0.1, "US30": -0.1, "DX": 0.8},
    "USDJPY": {"XAUUSD": -0.4, "XTIUSD": 0.3, "US30": -0.3, "DX": 0.7},
    "USDCAD": {"XAUUSD": 0.1, "XTIUSD": 0.7, "US30": -0.2, "DX": 0.6},
    "AUDUSD": {"XAUUSD": 0.5, "XTIUSD": 0.4, "US30": 0.1, "DX": -0.7},
    "NZDUSD": {"XAUUSD": 0.3, "XTIUSD": 0.2, "US30": 0.1, "DX": -0.6},
    "USDCHF": {"XAUUSD": -0.5, "XTIUSD": 0.1, "US30": -0.2, "DX": 0.8},
    "EURGBP": {"XAUUSD": -0.1, "XTIUSD": 0.0, "US30": 0.0, "DX": 0.1},
}


def _ensure():
    global _state
    if not _state:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE) as f:
                    _state = json.load(f)
        except Exception:
            _state = {
                "external_prices": {},
                "correlations": {},
                "last_update": None,
            }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def get_external_prices(client) -> Dict[str, Any]:
    """Fetch prices of external markets from MT5."""
    prices = {}
    symbols = ["XAUUSD", "XTIUSD", "XBRUSD", "US30", "SP500", "DX"]
    for sym in symbols:
        try:
            price_info = client.market.get_symbol_price(symbol_name=sym)
            if price_info and isinstance(price_info, dict):
                bid = price_info.get("bid", 0)
                ask = price_info.get("ask", 0)
                prices[sym] = {"bid": bid, "ask": ask, "mid": (bid + ask) / 2}
        except Exception:
            continue

    _state["external_prices"] = prices
    _state["last_update"] = datetime.now(timezone.utc).isoformat()
    _save()
    return prices


def _compute_correlation(series_a, series_b) -> float:
    if len(series_a) < 10 or len(series_b) < 10:
        return 0
    try:
        return float(np.corrcoef(series_a, series_b)[0, 1])
    except Exception:
        return 0


def update_correlations(client) -> Dict[str, Any]:
    """Update correlation matrix between forex and external markets."""
    forex_symbols = ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD", "NZDUSD", "USDCHF"]
    external = ["XAUUSD", "XTIUSD", "US30"]

    try:
        n_bars = 100
        forex_data = {}
        for sym in forex_symbols:
            try:
                df = client.market.get_candles_latest(symbol_name=sym, timeframe="H1", count=n_bars)
                import pandas as pd
                if isinstance(df, pd.DataFrame):
                    forex_data[sym] = df['close'].values
            except Exception:
                continue

        ext_data = {}
        for sym in external:
            try:
                df = client.market.get_candles_latest(symbol_name=sym, timeframe="H1", count=n_bars)
                import pandas as pd
                if isinstance(df, pd.DataFrame):
                    ext_data[sym] = df['close'].values
            except Exception:
                continue

        correlations = {}
        for fs, fvals in forex_data.items():
            correlations[fs] = {}
            for es, evals in ext_data.items():
                min_len = min(len(fvals), len(evals))
                if min_len >= 10:
                    c = _compute_correlation(fvals[-min_len:], evals[-min_len:])
                    correlations[fs][es] = round(c, 3)

        _state["correlations"] = correlations
        _save()

        return {"success": True, "correlations": correlations}

    except Exception as e:
        return {"success": False, "error": str(e)}


def analyze(symbol: str) -> Dict[str, Any]:
    """Get external market context for a symbol.

    Combines:
      - Known historical correlations
      - Live computed correlations
      - Current direction of external markets

    Returns adjusted bias.
    """
    _ensure()
    known = _RELATIONSHIPS.get(symbol, {})
    live = _state.get("correlations", {}).get(symbol, {})
    ext_prices = _state.get("external_prices", {})

    bias_adjustments = []
    total_direction = 0

    for ext_sym, hist_corr in known.items():
        live_corr = live.get(ext_sym, hist_corr)
        ext_info = ext_prices.get(ext_sym, {})

        # If external is rising and correlation is positive → bullish for pair
        # If external is rising and correlation is negative → bearish for pair
        if ext_info:
            mid = ext_info.get("mid", 0)
            if mid > 0:
                # Compare with last known price to determine direction
                last_prices = _state.get("_price_history", {}).get(ext_sym, [])
                is_rising = True
                if len(last_prices) >= 2:
                    is_rising = last_prices[-1] > last_prices[-2]
                else:
                    is_rising = True
                if ext_sym not in _state.setdefault("_price_history", {}):
                    _state["_price_history"][ext_sym] = []
                _state["_price_history"][ext_sym].append(mid)
                _state["_price_history"][ext_sym] = _state["_price_history"][ext_sym][-5:]
                direction = 1 if is_rising else -1
                total_direction += direction * live_corr
                bias_adjustments.append({
                    "market": ext_sym,
                    "correlation": round(live_corr, 2),
                    "direction": "up" if direction > 0 else "down",
                    "impact": "bullish" if direction * live_corr > 0 else "bearish",
                })

    avg_bias = total_direction / max(len(bias_adjustments), 1)

    return {
        "success": True,
        "symbol": symbol,
        "external_bias": round(avg_bias, 3),
        "bias_label": "bullish" if avg_bias > 0.2 else ("bearish" if avg_bias < -0.2 else "neutral"),
        "market_alignments": bias_adjustments,
        "known_correlations": known,
        "live_correlations": live,
    }


def integrate_with_conviction(symbol: str, conviction_decision: Dict[str, Any]) -> Dict[str, Any]:
    """Modulate conviction with external market bias.

    If external markets strongly agree → boost.
    If external markets strongly disagree → reduce.
    """
    mm = analyze(symbol)
    bias = mm.get("external_bias", 0)
    dec = conviction_decision.get("decision", {})
    conf = dec.get("confidence_pct", 0)
    v = dec.get("verdict", "")

    is_buy = "BUY" in v
    is_sell = "SELL" in v

    if is_buy and bias > 0.2:
        dec["multimarket_boost"] = "external_markets_agree"
        dec["confidence_pct"] = min(conf * 1.2, 99)
    elif is_sell and bias < -0.2:
        dec["multimarket_boost"] = "external_markets_agree"
        dec["confidence_pct"] = min(conf * 1.2, 99)
    elif is_buy and bias < -0.3:
        dec["multimarket_boost"] = "external_markets_conflict"
        dec["confidence_pct"] = conf * 0.5
    elif is_sell and bias > 0.3:
        dec["multimarket_boost"] = "external_markets_conflict"
        dec["confidence_pct"] = conf * 0.5

    dec["external_markets"] = {
        "bias": round(bias, 2),
        "label": mm.get("bias_label"),
        "alignments": mm.get("market_alignments", []),
    }
    conviction_decision["decision"] = dec
    return conviction_decision
