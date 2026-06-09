"""
Commitment of Traders (COT) Analysis — posicionamiento institucional.

Descarga datos semanales de la CFTC (Commodity Futures Trading Commission)
y analiza el posicionamiento de:
  - Commercial (smart money / hedgers): suelen estar en lo correcto
  - Non-Commercial (speculators / large traders): a menudo se equivocan en extremos
  - Non-Reportable (retail): contrario

Genera señales cuando commercial y non-commercial divergen fuertemente
(commercial acumulando mientras speculators venden = oportunidad de compra).
"""
import csv
import io
import json
import logging
import math
import os
import urllib.request
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
COT_FILE = os.path.join(DATA_DIR, "cot_data.json")

# Mapping from symbol -> CFTC market code and currency
_COT_MARKETS = {
    "EUR":   {"code": "099741", "name": "EURO FX", "exchange": "CME"},
    "GBP":   {"code": "096742", "name": "BRITISH POUND", "exchange": "CME"},
    "JPY":   {"code": "097741", "name": "JAPANESE YEN", "exchange": "CME"},
    "AUD":   {"code": "232741", "name": "AUSTRALIAN DOLLAR", "exchange": "CME"},
    "CAD":   {"code": "090741", "name": "CANADIAN DOLLAR", "exchange": "CME"},
    "CHF":   {"code": "092741", "name": "SWISS FRANC", "exchange": "CME"},
    "NZD":   {"code": "112741", "name": "NEW ZEALAND DOLLAR", "exchange": "CME"},
    "MXN":   {"code": "095741", "name": "MEXICAN PESO", "exchange": "CME"},
}

_CACHE: Dict[str, Any] = {}
_CACHE_TTL_HOURS = 12


def _get_currency(symbol: str) -> str:
    clean = symbol.upper().replace(".FX", "")
    majors = {"EUR", "GBP", "USD", "JPY", "AUD", "CAD", "CHF", "NZD", "MXN"}
    for m in majors:
        if clean.startswith(m):
            return m
    return "USD"


def fetch(force_refresh: bool = False) -> Dict[str, Any]:
    """Download latest CFTC COT data.

    Uses CFTC's legacy format from their website (free, public domain).

    Returns:
        dict with market data keyed by currency
    """
    now = datetime.now(timezone.utc)
    if not force_refresh and _CACHE.get("data") and _CACHE.get("timestamp"):
        age = (now - _CACHE["timestamp"]).total_seconds()
        if age < _CACHE_TTL_HOURS * 3600:
            return _CACHE["data"]

    # Try loading from disk first
    if not force_refresh:
        try:
            if os.path.exists(COT_FILE):
                age = (now - datetime.fromtimestamp(os.path.getmtime(COT_FILE))).total_seconds()
                if age < _CACHE_TTL_HOURS * 3600:
                    with open(COT_FILE) as f:
                        data = json.load(f)
                        _CACHE["data"] = data
                        _CACHE["timestamp"] = now
                        return data
        except Exception:
            pass

    result: Dict[str, Any] = {"markets": {}, "timestamp": now.isoformat(), "source": ""}

    # CFTC provides a single CSV with all legacy COT data
    urls = [
        "https://www.cftc.gov/dea/futures/deanetf.csv",
        "https://www.cftc.gov/files/dea/history/futures_fin_xls_2025.zip",
    ]

    fetched = False
    for url in urls:
        if fetched:
            break
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                content = resp.read().decode("utf-8", errors="replace")
                parsed = _parse_cot_csv(content)
                if parsed:
                    result["markets"] = parsed
                    result["source"] = url
                    fetched = True
        except Exception as e:
            logger.warning(f"COT fetch failed for {url}: {e}")
            continue

    if not fetched:
        logger.error("All COT data sources failed")
        return {"success": False, "error": "Cannot fetch COT data", "markets": {}}

    # Cache
    _CACHE["data"] = result
    _CACHE["timestamp"] = now

    # Save to disk
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(COT_FILE, "w") as f:
            json.dump(result, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save COT data: {e}")

    return result


def _parse_cot_csv(content: str) -> Dict[str, Any]:
    """Parse CFTC legacy CSV format into structured market data."""
    markets: Dict[str, Any] = {}
    try:
        reader = csv.DictReader(io.StringIO(content))

        # CFTC legacy CSV has many columns; we need:
        # - Market and Exchange Names
        # - Non-Commercial Long/Short/Spread
        # - Commercial Long/Short
        # - Non-Reportable Long/Short
        # - Open Interest
        for row in reader:
            market_name = (row.get("Market and Exchange Names", "") or "").strip().upper()
            exchange = (row.get("Market and Exchange Names", "") or "").split(" - ")
            exchange_name = exchange[-1].strip() if len(exchange) > 1 else ""

            # Match to our known currencies
            currency = None
            for cur, info in _COT_MARKETS.items():
                if info["name"] in market_name:
                    currency = cur
                    break

            if not currency:
                continue

            try:
                noncomm_long = int(row.get("Non- Commercial Long", row.get("Non-Commercial Long", 0)))
                noncomm_short = int(row.get("Non- Commercial Short", row.get("Non-Commercial Short", 0)))
                comm_long = int(row.get("Commercial Long", 0))
                comm_short = int(row.get("Commercial Short", 0))
                nonrep_long = int(row.get("Non-Reportable Long", 0))
                nonrep_short = int(row.get("Non-Reportable Short", 0))
                open_interest = int(row.get("Open Interest", 0))
                as_of = row.get("As of Date", row.get("Date", now_to_str()))
            except (ValueError, KeyError) as e:
                logger.warning(f"Parse error for {market_name}: {e}")
                continue

            noncomm_net = noncomm_long - noncomm_short
            comm_net = comm_long - comm_short
            nonrep_net = nonrep_long - nonrep_short

            # Percent of open interest
            oi = max(open_interest, 1)
            noncomm_pct = (noncomm_long + noncomm_short) / oi * 100 if oi > 0 else 0
            comm_pct = (comm_long + comm_short) / oi * 100 if oi > 0 else 0

            markets[currency] = {
                "currency": currency,
                "market_name": market_name.strip(),
                "exchange": exchange_name or "CME",
                "as_of_date": as_of,
                "open_interest": open_interest,
                "non_commercial": {
                    "long": noncomm_long,
                    "short": noncomm_short,
                    "net": noncomm_net,
                    "pct_of_oi": round(noncomm_pct, 1),
                },
                "commercial": {
                    "long": comm_long,
                    "short": comm_short,
                    "net": comm_net,
                    "pct_of_oi": round(comm_pct, 1),
                },
                "non_reportable": {
                    "long": nonrep_long,
                    "short": nonrep_short,
                    "net": nonrep_net,
                },
                "net_positions": {
                    "commercial_net": comm_net,
                    "speculator_net": noncomm_net,
                    "retail_net": nonrep_net,
                    "divergence": "commercial_bullish" if comm_net > 0 and noncomm_net < 0
                                  else ("commercial_bearish" if comm_net < 0 and noncomm_net > 0
                                        else "aligned"),
                },
            }
    except Exception as e:
        logger.error(f"COT CSV parse error: {e}")

    return markets


def now_to_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def analyze(symbol: str) -> Dict[str, Any]:
    """Get COT analysis for a symbol.

    Args:
        symbol: trading symbol (e.g. EURUSD)

    Returns:
        dict with COT positioning, signal, and advice
    """
    currency = _get_currency(symbol)
    data = fetch()

    if not data.get("markets"):
        return {"success": False, "error": "No COT data available, try later"}

    market = data["markets"].get(currency)
    if not market:
        return {
            "success": True,
            "symbol": symbol,
            "currency": currency,
            "data_available": False,
            "message": f"No COT data for {currency}",
        }

    net = market.get("net_positions", {})
    divergence = net.get("divergence", "aligned")
    comm_net = net.get("commercial_net", 0)
    spec_net = net.get("speculator_net", 0)

    # Generate signal
    if divergence == "commercial_bullish":
        signal = "bullish"
        confidence = min(80, max(50, abs(comm_net) / max(abs(spec_net) + 1, 1) * 30 + 50))
        reason = f"Commercials (smart money) net LONG {comm_net}, speculators net SHORT {spec_net}"
    elif divergence == "commercial_bearish":
        signal = "bearish"
        confidence = min(80, max(50, abs(comm_net) / max(abs(spec_net) + 1, 1) * 30 + 50))
        reason = f"Commercials (smart money) net SHORT {abs(comm_net)}, speculators net LONG {spec_net}"
    else:
        signal = "neutral"
        confidence = 50
        reason = f"Commercials ({comm_net}) and speculators ({spec_net}) are aligned — no divergence"

    return {
        "success": True,
        "symbol": symbol,
        "currency": currency,
        "data_available": True,
        "as_of_date": market.get("as_of_date", ""),
        "signal": signal,
        "confidence_pct": round(confidence, 0),
        "reason": reason,
        "divergence": divergence,
        "commercial_net": comm_net,
        "speculator_net": spec_net,
        "commercial_pct_of_oi": market.get("commercial", {}).get("pct_of_oi", 0),
        "advice": "favor_long" if signal == "bullish" else ("favor_short" if signal == "bearish" else "neutral"),
    }


def get_all() -> Dict[str, Any]:
    """Get COT data for all available currencies."""
    data = fetch()
    markets = data.get("markets", {})

    results = []
    for currency, market in markets.items():
        net = market.get("net_positions", {})
        results.append({
            "currency": currency,
            "commercial_net": net.get("commercial_net", 0),
            "speculator_net": net.get("speculator_net", 0),
            "divergence": net.get("divergence", "aligned"),
            "as_of_date": market.get("as_of_date", ""),
        })

    return {
        "success": True,
        "markets": results,
        "count": len(results),
        "timestamp": data.get("timestamp", ""),
    }
