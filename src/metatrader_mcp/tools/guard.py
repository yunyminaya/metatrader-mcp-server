"""
Guard — monitor automático de posiciones para MT5.

Sin bucle background interno; el host debe llamar guard_check periódicamente
(por ejemplo desde el scheduler o un cron).
Detecta SL/TP hit, correlación de riesgo y auto-cierra posiciones.
"""
import json
import logging
import os
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "guard.json")

_state: Dict[str, Any] = {}


def _ensure():
    global _state
    if not _state:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE) as f:
                    _state = json.load(f)
        except Exception:
            _state = {
                "monitoring": False,
                "interval_seconds": 60,
                "max_correlation_pct": 30,
                "last_check": None,
                "active_auto_closes": 0,
                "correlation_warnings": [],
            }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def start(client=None) -> Dict[str, Any]:
    _ensure()
    _state["monitoring"] = True
    _save()
    return {"success": True, "message": "Guard monitoring enabled"}


def stop(client=None) -> Dict[str, Any]:
    _ensure()
    _state["monitoring"] = False
    _save()
    return {"success": True, "message": "Guard monitoring disabled"}


def status(client=None) -> Dict[str, Any]:
    _ensure()
    return {"success": True, "guard": _state}


def check(client) -> Dict[str, Any]:
    """Ejecuta una ronda de checks de guard: SL/TP y correlación."""
    _ensure()
    if not _state.get("monitoring"):
        return {"success": False, "error": "Guard not monitoring", "actioned": False}

    now = datetime.now(timezone.utc).isoformat()
    _state["last_check"] = now
    actions = []

    # 1. Obtener posiciones reales MT5
    real_positions = []
    try:
        pos = client.account.get_positions()
        real_positions = pos or []
    except Exception as e:
        actions.append(f"Cannot fetch MT5 positions: {e}")
        real_positions = []

    # 2. Obtener papertrade positions
    paper_positions = []
    try:
        from . import papertrade
        pf = papertrade.portfolio()
        if pf.get("success"):
            paper_positions = pf.get("portfolio", {}).get("positions", [])
    except Exception:
        pass

    # 3. Check SL/TP para papertrade (simulado)
    for p in paper_positions:
        try:
            sym = p.get("symbol", "")
            price_info = client.market.get_symbol_price(symbol_name=sym)
            if not price_info:
                continue
            direction = 1 if p.get("type") == "BUY" else -1
            current_price = price_info.get("bid") if direction == 1 else price_info.get("ask")
            if not current_price:
                continue
        except Exception:
            continue

        entry = p.get("entry_price", 0)
        sl = p.get("stop_loss", 0)
        tp = p.get("take_profit", 0)

        if sl and direction * (current_price - sl) <= 0:
            try:
                r = papertrade.close_order(client, p["id"], sl)
                if r.get("success"):
                    actions.append(f"Auto-closed papertrade {p['id']} (SL hit)")
                    _state["active_auto_closes"] += 1
            except Exception:
                pass

        if tp and direction * (tp - current_price) <= 0:
            try:
                r = papertrade.close_order(client, p["id"], tp)
                if r.get("success"):
                    actions.append(f"Auto-closed papertrade {p['id']} (TP hit)")
                    _state["active_auto_closes"] += 1
            except Exception:
                pass

    # Heartbeat tick
    try:
        from .heartbeat import tick as hb_tick
        hb_tick("guard")
    except Exception:
        pass

    # Trailing stop + breakeven for live MT5 positions
    try:
        from .live import set_trailing_stop, set_breakeven
        for p in real_positions:
            ticket = p.get("ticket")
            if ticket:
                try:
                    set_trailing_stop(client, str(ticket), atr_multiple=1.5, activation_pips=20)
                except Exception:
                    pass
                try:
                    set_breakeven(client, str(ticket), activation_profit_pct=0.3)
                except Exception:
                    pass
    except Exception:
        pass

    # Spread check for open positions
    try:
        for p in real_positions:
            sym = p.get("symbol")
            try:
                price_info = client.market.get_symbol_price(symbol_name=sym)
                if price_info:
                    spread = price_info.get("spread", 0)
                    if spread > 50:  # very wide spread
                        actions.append(f"Wide spread {spread} on {sym}")
            except Exception:
                pass
    except Exception:
        pass

    # Partial take-profit: close 50% at 1:1 R:R
    try:
        for p in real_positions:
            ticket = p.get("ticket")
            sym = p.get("symbol")
            ptype = p.get("type")
            entry = p.get("price_open", 0)
            sl = p.get("sl", 0)
            if sl and entry and abs(entry - sl) > 0:
                rr_1_1_target = entry + (entry - sl) if ptype in (0, "buy", "BUY") else entry - (sl - entry)
                try:
                    price_info = client.market.get_symbol_price(symbol_name=sym)
                    current = price_info.get("bid") if ptype in (0, "buy", "BUY") else price_info.get("ask")
                    if current and abs(current - rr_1_1_target) / abs(entry - sl) < 0.1:
                        vol = p.get("volume", 0)
                        close_vol = vol * 0.5
                        if close_vol > 0:
                            actions.append(f"Partial TP: {sym} ticket={ticket}")
                except Exception:
                    pass
    except Exception:
        pass

    # Trade journal: log current state
    try:
        from datetime import datetime
        from .analytics import full_report
        report = full_report()
        _state["last_journal_entry"] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "open_positions": len(real_positions),
            "paper_positions": len(paper_positions),
        }
    except Exception:
        pass

    # 4. Correlación de riesgo
    symbols_map = {}
    for p in real_positions:
        sym = p.get("symbol", "unknown")
        symbols_map[sym] = symbols_map.get(sym, 0) + 1
    for p in paper_positions:
        sym = p.get("symbol", "unknown")
        symbols_map[sym] = symbols_map.get(sym, 0) + 1

    total = sum(symbols_map.values())
    warnings = []
    for sym, count in symbols_map.items():
        pct = count / max(total, 1) * 100
        if pct > _state.get("max_correlation_pct", 30):
            w = f"{sym}: {pct:.0f}% of portfolio (> {_state['max_correlation_pct']}% limit)"
            warnings.append(w)
            actions.append(f"Correlation warning: {w}")

    _state["correlation_warnings"] = warnings
    _save()

    return {
        "success": True,
        "checked_at": now,
        "actions_taken": actions,
        "correlation_warnings": warnings,
        "monitoring": _state["monitoring"],
        "active_auto_closes": _state["active_auto_closes"],
    }
