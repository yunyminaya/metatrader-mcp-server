"""
Economic Calendar — eventos de alto impacto y predicción de reacción del mercado.

Escanea calendarios económicos para detectar eventos de alto impacto
(NFP, FOMC, CPI, GDP, etc.) y sugiere:
  - Auto-halt trading 30-60 min antes de eventos de alto impacto
  - Ajustar tamaño de posición según la magnitud esperada
  - Dirección probable basada en datos históricos
"""
import logging
import math
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)

# Known high-impact events with typical market reaction direction
_HIGH_IMPACT_EVENTS = {
    "nfp": {"name": "Non-Farm Payrolls", "impact": 5, "bias": "usd_strength_if_beats"},
    "nonfarm payrolls": {"name": "Non-Farm Payrolls", "impact": 5, "bias": "usd_strength_if_beats"},
    "fomc": {"name": "FOMC Rate Decision", "impact": 5, "bias": "usd_strength_if_hawkish"},
    "federal reserve rate decision": {"name": "FOMC Rate Decision", "impact": 5, "bias": "usd_strength_if_hawkish"},
    "cpi": {"name": "Consumer Price Index", "impact": 4, "bias": "usd_strength_if_higher"},
    "consumer price index": {"name": "Consumer Price Index", "impact": 4, "bias": "usd_strength_if_higher"},
    "gdp": {"name": "Gross Domestic Product", "impact": 4, "bias": "usd_strength_if_higher"},
    "gross domestic product": {"name": "Gross Domestic Product", "impact": 4, "bias": "usd_strength_if_higher"},
    "ecb rate decision": {"name": "ECB Rate Decision", "impact": 4, "bias": "eur_strength_if_hawkish"},
    "european central bank rate decision": {"name": "ECB Rate Decision", "impact": 4, "bias": "eur_strength_if_hawkish"},
    "bank of england rate decision": {"name": "BOE Rate Decision", "impact": 4, "bias": "gbp_strength_if_hawkish"},
    "retail sales": {"name": "Retail Sales", "impact": 3, "bias": "usd_strength_if_higher"},
    "unemployment": {"name": "Unemployment Rate", "impact": 3, "bias": "usd_weakness_if_higher"},
    "unemployment rate": {"name": "Unemployment Rate", "impact": 3, "bias": "usd_weakness_if_higher"},
    "pmi": {"name": "PMI Data", "impact": 3, "bias": "currency_strength_if_higher"},
    "manufacturing pmi": {"name": "Manufacturing PMI", "impact": 3, "bias": "currency_strength_if_higher"},
    "services pmi": {"name": "Services PMI", "impact": 3, "bias": "currency_strength_if_higher"},
    "consumer confidence": {"name": "Consumer Confidence", "impact": 2, "bias": "usd_strength_if_higher"},
    "durable goods": {"name": "Durable Goods Orders", "impact": 3, "bias": "usd_strength_if_higher"},
    "industrial production": {"name": "Industrial Production", "impact": 2, "bias": "currency_strength_if_higher"},
    "fed chair speech": {"name": "Fed Chair Speech", "impact": 4, "bias": "unpredictable"},
    "jobless claims": {"name": "Jobless Claims", "impact": 2, "bias": "usd_weakness_if_higher"},
    "treasury refunding": {"name": "Treasury Refunding Announcement", "impact": 3, "bias": "usd_strength_if_lower_deficit"},
    "g20 summit": {"name": "G20 Summit", "impact": 3, "bias": "unpredictable"},
    "opec meeting": {"name": "OPEC Meeting", "impact": 4, "bias": "oil_higher_if_cuts"},
}

_CACHE: Dict[str, Any] = {}
_CACHE_TTL_MINUTES = 30


def _get_currency(symbol: str) -> str:
    clean = symbol.upper().replace(".FX", "")
    majors = {"EUR", "GBP", "USD", "JPY", "AUD", "CAD", "CHF", "NZD"}
    for m in majors:
        if clean.startswith(m):
            return m
    return "USD"


def _currency_pair_pairs(symbol: str) -> List[str]:
    """Get both currencies involved in a pair."""
    clean = symbol.upper().replace(".FX", "")
    majors = {"EUR", "GBP", "USD", "JPY", "AUD", "CAD", "CHF", "NZD"}
    currencies = []
    for m in majors:
        if clean.startswith(m):
            currencies.append(m)
            remaining = clean.replace(m, "", 1)
            for m2 in majors:
                if remaining.startswith(m2) or remaining == m2:
                    currencies.append(m2)
                    break
            break
    if len(currencies) < 2:
        # Try splitting by common separators
        for sep in ["/", "-", "."]:
            parts = clean.split(sep)
            if len(parts) >= 2:
                c1 = parts[0][:3] if len(parts[0]) >= 3 else parts[0]
                c2 = parts[1][:3] if len(parts[1]) >= 3 else parts[1]
                if c1 in majors and c2 in majors:
                    currencies = [c1, c2]
                    break
    return currencies


def fetch(days_ahead: int = 7) -> Dict[str, Any]:
    """Fetch upcoming economic events.

    Uses ForexFactory calendar (free, no API key needed) or fallback
    to built-in event schedule.

    Args:
        days_ahead: how many days ahead to scan

    Returns:
        dict with list of events
    """
    now = datetime.now(timezone.utc)
    cache_key = f"events_{days_ahead}_{now.strftime('%Y%m%d_%H')}"

    if cache_key in _CACHE:
        age = (now - _CACHE[cache_key].get("_cached_at", now)).total_seconds()
        if age < _CACHE_TTL_MINUTES * 60:
            data = _CACHE[cache_key].copy()
            data.pop("_cached_at", None)
            return data

    events = []

    # Try ForexFactory via scraping
    try:
        import urllib.request
        from urllib.parse import quote

        url = f"https://www.forexfactory.com/calendar?day={now.strftime('%b%d%Y').lower()}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        # Parse directly from the ForexFactory table
        # Look for event rows with impact indicators
        events = _parse_forexfactory(html, days_ahead, now)
    except Exception as e:
        logger.warning(f"ForexFactory fetch failed: {e}")

    # If parsing failed, use built-in schedule
    if not events:
        events = _builtin_schedule(days_ahead, now)

    result = {
        "success": True,
        "events": events,
        "count": len(events),
        "high_impact_count": sum(1 for e in events if e.get("impact", 0) >= 4),
        "generated_at": now.isoformat(),
    }
    result["_cached_at"] = now
    _CACHE[cache_key] = result

    return result


def _parse_forexfactory(html: str, days_ahead: int, now: datetime) -> List[Dict[str, Any]]:
    """Parse ForexFactory HTML to extract events.

    This parser looks for the calendar table rows.
    """
    events: List[Dict[str, Any]] = []
    current_date = now.strftime("%Y-%m-%d")

    # Find calendar rows in the HTML
    # ForexFactory uses <tr class="calendar_row"> with data-event attributes
    rows = re.findall(
        r'<tr[^>]*class="calendar_row[^"]*"[^>]*>(.*?)</tr>',
        html, re.DOTALL
    )

    for row_html in rows:
        # Extract event name
        name_match = re.search(r'<span[^>]*class="calendar__event-title[^"]*"[^>]*>(.*?)</span>', row_html, re.DOTALL)
        if not name_match:
            name_match = re.search(r'class="event">(.*?)<', row_html)
        if not name_match:
            name_match = re.search(r'title="([^"]+)"', row_html)

        if not name_match:
            continue

        event_name = re.sub(r'<[^>]+>', '', name_match.group(1)).strip()
        if not event_name or len(event_name) < 3:
            continue

        # Extract impact (red = high, orange = medium, yellow = low)
        impact = 1
        if "red" in row_html.lower() or "high" in row_html.lower():
            impact = 5
        elif "orange" in row_html.lower() or "med" in row_html.lower():
            impact = 3

        # Extract currency
        currency_match = re.search(r'class="calendar__currency[^"]*"[^>]*>([^<]+)<', row_html, re.DOTALL)
        currency = currency_match.group(1).strip() if currency_match else "USD"

        # Extract time
        time_match = re.search(r'class="calendar__time[^"]*"[^>]*>([^<]+)<', row_html, re.DOTALL)
        time_str = time_match.group(1).strip() if time_match else ""

        # Extract previous/forecast
        prev_match = re.search(r'class="calendar__previous[^"]*"[^>]*>([^<]+)<', row_html, re.DOTALL)
        forecast_match = re.search(r'class="calendar__forecast[^"]*"[^>]*>([^<]+)<', row_html, re.DOTALL)
        prev_val = prev_match.group(1).strip() if prev_match else ""
        forecast_val = forecast_match.group(1).strip() if forecast_match else ""

        # Determine direction bias
        bias = "unpredictable"
        event_lower = event_name.lower()
        for key, info in _HIGH_IMPACT_EVENTS.items():
            if key in event_lower:
                bias = info["bias"]
                if impact < info["impact"]:
                    impact = info["impact"]
                break

        event = {
            "name": event_name,
            "currency": currency,
            "time": time_str,
            "date": current_date,
            "impact": impact,
            "impact_label": "high" if impact >= 4 else ("medium" if impact >= 3 else "low"),
            "previous": prev_val,
            "forecast": forecast_val,
            "bias": bias,
        }
        events.append(event)

    return events[:50]  # Limit to 50 events


def _builtin_schedule(days_ahead: int, now: datetime) -> List[Dict[str, Any]]:
    """Built-in fallback schedule of known recurring events."""
    events: List[Dict[str, Any]] = []
    current = now

    # Known recurring events (approximate schedule)
    recurring = [
        # NFP: first Friday of every month at 8:30 AM ET
        {"name": "Non-Farm Payrolls", "currency": "USD", "day_offset": (4 - now.weekday()) % 7,
         "time": "12:30", "impact": 5, "bias": "usd_strength_if_beats"},
        # FOMC: 8 times per year, approximate
        {"name": "FOMC Rate Decision", "currency": "USD", "day_offset": 14,
         "time": "18:00", "impact": 5, "bias": "usd_strength_if_hawkish"},
        # CPI: monthly
        {"name": "Consumer Price Index", "currency": "USD", "day_offset": 10,
         "time": "12:30", "impact": 4, "bias": "usd_strength_if_higher"},
        # ECB Rate
        {"name": "ECB Rate Decision", "currency": "EUR", "day_offset": 12,
         "time": "12:15", "impact": 4, "bias": "eur_strength_if_hawkish"},
        # BOE Rate
        {"name": "BOE Rate Decision", "currency": "GBP", "day_offset": 13,
         "time": "11:00", "impact": 4, "bias": "gbp_strength_if_hawkish"},
        # Jobless claims: every Thursday
        {"name": "Jobless Claims", "currency": "USD", "day_offset": (3 - now.weekday()) % 7,
         "time": "12:30", "impact": 2, "bias": "usd_weakness_if_higher"},
    ]

    for event in recurring:
        if event["day_offset"] > days_ahead * 7:
            continue
        event_date = current + timedelta(days=event["day_offset"])
        events.append({
            "name": event["name"],
            "currency": event["currency"],
            "time": event["time"],
            "date": event_date.strftime("%Y-%m-%d"),
            "impact": event["impact"],
            "impact_label": "high" if event["impact"] >= 4 else ("medium" if event["impact"] >= 3 else "low"),
            "previous": "",
            "forecast": "",
            "bias": event["bias"],
            "source": "builtin",
        })

    return events


def check_events(symbol: str, hours_window: int = 48) -> Dict[str, Any]:
    """Check for upcoming high-impact events relevant to a symbol.

    Args:
        symbol: symbol to check
        hours_window: window in hours to look ahead

    Returns:
        dict with events found and trading advice
    """
    currencies = _currency_pair_pairs(symbol)
    if not currencies:
        currencies = [_get_currency(symbol), "USD"]

    cal = fetch(days_ahead=max(1, hours_window // 24 + 1))
    events = cal.get("events", [])

    now = datetime.now(timezone.utc)
    relevant = []

    for event in events:
        if event.get("currency") not in currencies:
            continue

        # Parse event time
        event_date = event.get("date", "")
        event_time = event.get("time", "00:00")
        try:
            if ":" in str(event_time):
                parts = event_time.split(":")
                hour = int(parts[0])
                minute = int(parts[1][:2]) if len(parts) > 1 else 0
                ampm = "am" if "am" in event_time.lower() else "pm"
                if "pm" in event_time.lower() and hour < 12:
                    hour += 12
                if "am" in event_time.lower() and hour == 12:
                    hour = 0
                event_dt = datetime.strptime(event_date, "%Y-%m-%d") if event_date else now
                event_dt = event_dt.replace(hour=hour, minute=minute, tzinfo=timezone.utc)
            else:
                event_dt = now + timedelta(hours=hours_window)
        except Exception:
            event_dt = now + timedelta(hours=hours_window)

        hours_until = (event_dt - now).total_seconds() / 3600
        if hours_until < 0:
            hours_until += 24  # next occurrence
        if hours_until > hours_window:
            continue

        event["hours_until"] = round(hours_until, 1)
        relevant.append(event)

    # Sort by time
    relevant.sort(key=lambda e: e.get("hours_until", 999))

    # Generate advice
    high_impact = [e for e in relevant if e.get("impact", 0) >= 4]
    if high_impact:
        nearest = high_impact[0]
        hours = nearest.get("hours_until", 0)
        if hours <= 1:
            advice = "HALT_TRADING"
            reason = f"High-impact event '{nearest['name']}' in {hours:.0f} min — halt trading"
        elif hours <= 6:
            advice = "REDUCE_SIZE"
            reason = f"High-impact event '{nearest['name']}' in {hours:.0f}h — reduce size 50%"
        else:
            advice = "CAUTION"
            reason = f"High-impact event '{nearest['name']}' in {hours:.0f}h — be cautious"
    else:
        advice = "NORMAL"
        reason = "No high-impact events in window — trade normally"

    return {
        "success": True,
        "symbol": symbol,
        "currencies": currencies,
        "events_found": len(relevant),
        "high_impact_count": len(high_impact),
        "events": relevant[:10],
        "advice": advice,
        "reason": reason,
        "window_hours": hours_window,
    }
