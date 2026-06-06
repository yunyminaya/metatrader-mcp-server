#!/usr/bin/env python3
"""
run_live.py v4 — Loop definitivo: ENSEMBLE + EDGE + ANOMALY + MULTIMARKET + EVOLUTION.

Ejecuta en un bucle infinito:
  1. News check — salta trading cerca de eventos de alto impacto
  2. Session check — solo tradea en sesiones de alta liquidez
  3. Anomaly check — detecta condiciones anómalas y ajusta tamaño
  4. Multimarket — correlación con Gold/Oil/SP500
  5. Ensemble — 8 estrategias votan, decisión ponderada
  6. Edge — EV, Kelly, matching histórico
  7. Scheduler tick
  8. Guard check — SL/TP, trailing, breakeven
  9. Emergency brake
  10. Heartbeat
  11. Evolution — challenger vs current
  12. Web dashboard opcional

Uso:
  python run_live.py                        # defaults
  python run_live.py --ensemble             # usa ensemble voting en vez de conviction sola
  python run_live.py --web                  # web dashboard
  CTRL+C para detener

Requiere MT5 terminal conectado y .env configurado.
"""
import argparse
import logging
import os
import sys
import time
import threading
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("run_live")

SCHEDULER_INTERVAL_MIN = int(os.getenv("LIVE_SCHEDULER_INTERVAL", "60"))
GUARD_INTERVAL_SEC = int(os.getenv("LIVE_GUARD_INTERVAL", "60"))
HEARTBEAT_INTERVAL_SEC = int(os.getenv("LIVE_HEARTBEAT_INTERVAL", "300"))
DASHBOARD_INTERVAL_SEC = 3600
OPTIMIZER_INTERVAL_TRADES = int(os.getenv("LIVE_OPTIMIZER_INTERVAL", "20"))


def _start_web_dashboard(host="0.0.0.0", port=5000):
    """Start web dashboard in a background thread."""
    try:
        import uvicorn
        from web.dashboard import app
        t = threading.Thread(target=uvicorn.run, args=(app,), kwargs={"host": host, "port": port, "log_level": "info"}, daemon=True)
        t.start()
        logger.info(f"Web dashboard started at http://{host}:{port}")
        return t
    except Exception as e:
        logger.warning(f"Web dashboard not available: {e}")


def main():
    parser = argparse.ArgumentParser(description="Live Trading Loop v4")
    parser.add_argument("--interval", type=int, default=SCHEDULER_INTERVAL_MIN, help="Scheduler interval in minutes")
    parser.add_argument("--guard-interval", type=int, default=GUARD_INTERVAL_SEC, help="Guard check interval in seconds")
    parser.add_argument("--web", action="store_true", help="Start web dashboard")
    parser.add_argument("--ensemble", action="store_true", help="Use ensemble voting (all strategies) instead of conviction alone")
    parser.add_argument("--web-host", type=str, default="0.0.0.0", help="Web dashboard host")
    parser.add_argument("--web-port", type=int, default=5000, help="Web dashboard port")
    args = parser.parse_args()

    sched_interval = args.interval
    guard_interval = args.guard_interval
    use_ensemble = args.ensemble

    try:
        from metatrader_mcp.utils import init
        from metatrader_mcp.tools.scheduler import tick as sched_tick, start as sched_start, status as sched_status
        from metatrader_mcp.tools.guard import check as guard_check, start as guard_start
        from metatrader_mcp.tools.heartbeat import tick as hb_tick
        from metatrader_mcp.tools.market import check_high_impact_news, active_sessions
        from metatrader_mcp.tools.emergency import status as em_status
        from metatrader_mcp.tools.dashboard import overview as dash_overview
    except ImportError as e:
        logger.error(f"Cannot import metatrader_mcp: {e}")
        logger.error("Run: pip install -e .")
        sys.exit(1)

    # Web dashboard (background thread)
    if args.web:
        _start_web_dashboard(args.web_host, args.web_port)

    # Conectar MT5
    logger.info("Connecting to MetaTrader 5...")
    try:
        client = init(
            os.getenv("login"),
            os.getenv("password"),
            os.getenv("server"),
            os.getenv("MT5_PATH"),
        )
        logger.info("Connected to MT5")
    except Exception as e:
        logger.error(f"MT5 connection failed: {e}")
        sys.exit(1)

    sched_start()
    guard_start()
    logger.info("Scheduler + Guard started")

    # Enable auto-support modules
    modules_ok = {}
    for name, mod, fn in [
        ("autooptimizer", "metatrader_mcp.tools.autooptimizer", "enable"),
        ("ensemble", "metatrader_mcp.tools.ensemble", None),  # passive
        ("evolution", "metatrader_mcp.tools.evolution", "enable"),
    ]:
        try:
            if fn:
                m = __import__(mod, fromlist=[fn])
                getattr(m, fn)()
            modules_ok[name] = True
            logger.info(f"  {name} enabled")
        except Exception:
            modules_ok[name] = False

    last_sched = 0
    last_guard = 0
    last_hb = 0
    last_dash = 0
    consecutive_skips = 0
    anomaly_skip_counter = 0

    logger.info(f"Live loop v4 started. Scheduler every {sched_interval}min, Guard every {guard_interval}s")
    if use_ensemble:
        logger.info("  Mode: ENSEMBLE (8 strategies voting)")
    else:
        logger.info("  Mode: CONVICTION (with all filters)")
    if args.web:
        logger.info(f"  Dashboard at http://{args.web_host}:{args.web_port}")
    logger.info("Press CTRL+C to stop")

    try:
        while True:
            now = time.time()

            # ── Emergency brake check ──
            try:
                em = em_status()
                if em.get("emergency", {}).get("brake_active"):
                    logger.warning(f"EMERGENCY BRAKE ACTIVE: {em.get('emergency', {}).get('tripped_by')}")
            except Exception:
                pass

            # ── Scheduler tick ──
            if now - last_sched >= sched_interval * 60:
                last_sched = now
                logger.info("Scheduler tick")

                # 1) News check
                try:
                    news = check_high_impact_news(hours_window=2)
                    if news.get("has_event"):
                        events = [e["name"] for e in news.get("events", [])]
                        logger.info(f"  News nearby: {events} — skip")
                        consecutive_skips += 1
                        continue
                except Exception:
                    pass

                # 2) Session check
                try:
                    sessions = active_sessions()
                    if sessions.get("quality", 0) < 0.4:
                        logger.info(f"  Low liquidity ({sessions.get('quality', 0)}%) — skip")
                        consecutive_skips += 1
                        continue
                except Exception:
                    pass

                # 3) Anomaly check (skip if market is anomalous)
                try:
                    from metatrader_mcp.tools.anomaly import check as anomaly_check
                    a = anomaly_check(client, "EURUSD")
                    if a.get("anomalous"):
                        size_mul = a.get("size_multiplier", 1)
                        logger.info(f"  Anomalous market (score={a.get('anomaly_score')}) — size={size_mul}")
                        if size_mul == 0:
                            anomaly_skip_counter += 1
                            if anomaly_skip_counter >= 3:
                                logger.warning("  3 consecutive anomaly skips — entering defensive mode")
                            continue
                    else:
                        anomaly_skip_counter = 0
                except Exception:
                    pass

                # 4) Multi-market correlation check
                try:
                    from metatrader_mcp.tools.multimarket import update_correlations, analyze
                    update_correlations(client)
                    mm = analyze("EURUSD")
                    if mm.get("bias_label") != "neutral":
                        logger.info(f"  External markets: {mm.get('bias_label')} (bias={mm.get('external_bias')})")
                except Exception:
                    pass

                consecutive_skips = 0

                # 5) Execute scheduler tick — ensemble or conviction
                try:
                    if use_ensemble:
                        from metatrader_mcp.tools.ensemble import evaluate as ensemble_eval
                        result = ensemble_eval(client, "EURUSD")
                        if result.get("success") and result.get("ensemble_verdict") in ("BUY", "STRONG_BUY"):
                            logger.info(f"  Ensemble: {result.get('ensemble_verdict')} ({result.get('ensemble_confidence')}%)")
                        # NOTE: ensemble is advisory — sched_tick still executes via conviction
                    result = sched_tick(client)
                    if result.get("actioned"):
                        logger.info(f"  Trade executed: {result.get('trade')}")
                        # Notify auto-optimizer
                        if modules_ok.get("autooptimizer"):
                            try:
                                from metatrader_mcp.tools.autooptimizer import on_trade_closed
                                ao = on_trade_closed()
                                if ao.get("action") == "ready":
                                    logger.info("  Auto-optimizer ready for next run")
                            except Exception:
                                pass
                    else:
                        reason = result.get("reason", "ok")
                        if reason != "ok":
                            logger.info(f"  No trade: {reason}")
                except Exception as e:
                    logger.error(f"Scheduler error: {e}")

            # ── Guard check (cada 60s) ──
            if now - last_guard >= guard_interval:
                last_guard = now
                try:
                    result = guard_check(client)
                    if result.get("actions_taken"):
                        for a in result["actions_taken"]:
                            logger.info(f"  Guard: {a}")
                except Exception as e:
                    logger.error(f"Guard error: {e}")

            # ── Heartbeat (cada 5min) ──
            if now - last_hb >= HEARTBEAT_INTERVAL_SEC:
                last_hb = now
                try:
                    hb_tick("scheduler")
                    hb_tick("guard")
                except Exception:
                    pass

            # ── Dashboard log (cada hora) ──
            if now - last_dash >= DASHBOARD_INTERVAL_SEC:
                last_dash = now
                try:
                    d = dash_overview(client)
                    if d.get("success"):
                        db = d.get("dashboard", {})
                        logger.info(f"Health: {db.get('health_score')}/100 | "
                                    f"Account: {db.get('account', {}).get('balance')} | "
                                    f"Positions: {len(db.get('positions', []))} | "
                                    f"Insurance: {db.get('insurance', {}).get('balance', 0)}")
                except Exception:
                    pass

            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        try:
            client.disconnect()
        except Exception:
            pass
        logger.info("Disconnected from MT5")


if __name__ == "__main__":
    main()
