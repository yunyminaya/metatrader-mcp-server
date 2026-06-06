"""
Dashboard v2 — vista consolidada de TODO el sistema.

Incluye: cuenta, posiciones, régimen MTF, convicción v2,
scheduler, papertrade, guard, insurance, emergency, heartbeat,
analytics, correlación, divergencia, contexto de mercado.
"""
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


def overview(client) -> Dict[str, Any]:
    """Snapshot completo del sistema de trading con todo integrado."""
    now = datetime.now(timezone.utc).isoformat()
    result = {
        "timestamp": now,
        "account": {},
        "positions": [],
        "regime": {},
        "conviction": [],
        "scheduler": {},
        "papertrade": {},
        "guard": {},
        "insurance": {},
        "emergency": {},
        "heartbeat": {},
        "analytics": {},
        "correlation": {},
        "divergence": {},
        "market_context": {},
    }

    # Account
    try:
        acct = client.account.get_account()
        result["account"] = {
            "balance": round(acct.get("balance", 0), 2),
            "equity": round(acct.get("equity", 0), 2),
            "margin": round(acct.get("margin", 0), 2),
            "free_margin": round(acct.get("margin_free", 0), 2),
            "margin_level_pct": round(acct.get("margin_level", 0), 1),
        }
    except Exception as e:
        result["account"] = {"error": str(e)}

    # Positions
    try:
        pos = client.account.get_positions()
        result["positions"] = [
            {
                "ticket": p.get("ticket"),
                "symbol": p.get("symbol"),
                "type": "BUY" if p.get("type") in (0, "buy") else "SELL",
                "volume": p.get("volume"),
                "price": p.get("price_open"),
                "sl": p.get("sl"),
                "tp": p.get("tp"),
                "profit": round(p.get("profit", 0), 2),
            }
            for p in (pos or [])
        ]
    except Exception as e:
        result["positions"] = {"error": str(e)}

    # Regime (MTF)
    try:
        from .regime import scan as regime_scan
        r = regime_scan(client)
        if r.get("success"):
            result["regime"] = {
                "summary": r.get("summary", {}),
                "session": r.get("session", {}),
                "total_scanned": r.get("total"),
            }
    except Exception:
        pass

    # Conviction
    try:
        from .conviction import scan as conv_scan
        bal = result["account"].get("balance", 1000)
        c = conv_scan(client, bankroll=bal, min_confidence=50)
        if c.get("success"):
            result["conviction"] = {
                "opportunities": c.get("opportunities", [])[:5],
                "session_quality": c.get("session_quality"),
                "total_found": c.get("total_scan"),
            }
    except Exception:
        pass

    # Scheduler
    try:
        from .scheduler import status as sched_status
        s = sched_status()
        if s.get("success"):
            sc = s.get("scheduler", {})
            result["scheduler"] = {
                "enabled": sc.get("enabled"),
                "trades_today": sc.get("trades_today"),
                "daily_limit": sc.get("daily_limit"),
                "min_confidence": sc.get("min_confidence"),
                "consecutive_losses": sc.get("consecutive_losses"),
                "insurance_fund": sc.get("insurance_fund", 0),
            }
    except Exception:
        pass

    # PaperTrade
    try:
        from .papertrade import portfolio as pt_portfolio
        p = pt_portfolio()
        if p.get("success"):
            pf = p.get("portfolio", {})
            result["papertrade"] = {
                "balance": pf.get("balance"),
                "open_positions": pf.get("open_positions"),
                "total_trades": pf.get("total_trades"),
                "win_rate_pct": pf.get("win_rate_pct"),
                "net_pnl_usd": pf.get("net_pnl_usd"),
            }
    except Exception:
        pass

    # Guard
    try:
        from .guard import status as guard_status
        g = guard_status()
        if g.get("success"):
            gd = g.get("guard", {})
            result["guard"] = {
                "monitoring": gd.get("monitoring"),
                "last_check": gd.get("last_check"),
                "active_auto_closes": gd.get("active_auto_closes", 0),
            }
    except Exception:
        pass

    # Insurance
    try:
        from .insurance import status as ins_status
        i = ins_status()
        if i.get("success"):
            result["insurance"] = i.get("insurance", {})
    except Exception:
        pass

    # Emergency
    try:
        from .emergency import status as em_status
        e = em_status()
        if e.get("success"):
            em = e.get("emergency", {})
            result["emergency"] = {
                "brake_active": em.get("brake_active"),
                "tripped_by": em.get("tripped_by"),
                "consecutive_losses": em.get("consecutive_losses"),
                "peak_balance": em.get("peak_balance"),
            }
    except Exception:
        pass

    # Heartbeat
    try:
        from .heartbeat import check_status as hb_check
        h = hb_check(60)
        if h.get("success"):
            result["heartbeat"] = h.get("heartbeat", {})
    except Exception:
        pass

    # Analytics
    try:
        from .analytics import full_report
        a = full_report()
        if a.get("success"):
            rpt = a.get("report", {})
            result["analytics"] = {
                "health_score": rpt.get("health_score"),
                "ratios": rpt.get("ratios", {}),
                "monte_carlo": rpt.get("monte_carlo", {}),
            }
    except Exception:
        pass

    # Correlation
    try:
        from .correlation import portfolio_risk
        syms = [p.get("symbol") for p in result["positions"] if p.get("symbol")]
        if syms:
            corr = portfolio_risk(syms)
            if corr.get("success"):
                result["correlation"] = {
                    "risk_level": corr.get("risk_level"),
                    "warnings": corr.get("warnings", []),
                    "effective_positions": corr.get("effective_positions"),
                }
    except Exception:
        pass

    # Market context
    try:
        from .market import active_sessions, check_high_impact_news
        mc = active_sessions()
        news = check_high_impact_news()
        result["market_context"] = {
            "sessions": mc.get("sessions"),
            "session_quality": mc.get("quality"),
            "news_nearby": news.get("has_event"),
            "news_advice": news.get("advice"),
        }
    except Exception:
        pass

    # Multi-broker
    try:
        from .broker import status as broker_status
        bs = broker_status()
        if bs.get("success"):
            result["broker"] = {
                "total_connected": bs.get("total_connected"),
                "total_registered": bs.get("total_registered"),
                "active": bs.get("active_broker"),
                "strategy": bs.get("routing_strategy"),
                "brokers": [{"name": b["name"], "connected": b["connected"], "healthy": b["healthy"]}
                           for b in bs.get("brokers", [])],
            }
    except Exception:
        pass

    # Anti-manipulation
    try:
        if result.get("positions"):
            sym = result["positions"][0]["symbol"]
            from .antimanipulation import analyze_symbol
            ama = analyze_symbol(client, sym)
            if ama.get("success"):
                result["antimanipulation"] = {
                    "manipulation_risk": ama.get("manipulation_risk"),
                    "stop_hunting": ama.get("stop_hunting", {}).get("hunting_detected"),
                    "spoofing": ama.get("spoofing", {}).get("spoofing_detected"),
                    "at_obvious_level": ama.get("at_obvious_level"),
                }
    except Exception:
        pass

    # Health score
    score = 30
    if result["account"].get("balance", 0) > 0:
        score += 10
    if result["scheduler"].get("enabled"):
        score += 8
    if result["papertrade"].get("win_rate_pct", 0) > 55:
        score += 10
    if result["guard"].get("monitoring"):
        score += 8
    if result["insurance"].get("balance", 0) > 20:
        score += 8
    if not result["emergency"].get("brake_active"):
        score += 8
    if result["heartbeat"].get("healthy"):
        score += 8
    if result["analytics"].get("health_score", 0) > 60:
        score += 10

    result["health_score"] = min(score, 100)

    return {"success": True, "dashboard": result}
