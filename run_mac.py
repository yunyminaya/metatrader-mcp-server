#!/usr/bin/env python3
"""
run_mac.py — Live trading loop for macOS/Wine bridge.
Uses _mt5_direct (mt5_mac_mcp.py) + all 68 intelligence tools.
Auto-starts Monday 00:00 UTC, trades during London/NY sessions.
"""
import json, os, sys, time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from mt5_mac_mcp import _mt5_direct
from mt5_mcp_intelligence import init as intel_init, TOOLS

intel_init(_mt5_direct)
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

def t(name, args={}):
    return TOOLS[name][0](args)

def today():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def is_weekend():
    return datetime.now(timezone.utc).weekday() >= 5

def save_state(state):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "scheduler_mac.json"), "w") as f:
        json.dump(state, f, indent=2)

def load_state():
    path = os.path.join(DATA_DIR, "scheduler_mac.json")
    if os.path.exists(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "enabled": True,
        "interval_sec": 300,
        "daily_limit": 3,
        "min_confidence": 60,
        "trades_today": 0,
        "date": "",
        "consecutive_losses": 0,
        "peak_equity": 0,
        "last_run": None,
        "symbols": ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD"],
    }

def main():
    state = load_state()
    print("╔══════════════════════════════════════════╗")
    print("║   MAC TRADING LOOP — tastyfx LIVE        ║")
    print("╚══════════════════════════════════════════╝")
    print(f"Account: ${_mt5_direct({'action':'account'})['balance']:.2f}")
    print(f"Symbols: {state['symbols']}")
    print(f"Interval: {state['interval_sec']}s | Daily limit: {state['daily_limit']}")
    print(f"Min confidence: {state['min_confidence']}%")

    while True:
        now = datetime.now(timezone.utc)
        today_str = today()
        weekday = now.weekday()

        # Reset daily counter
        if state["date"] != today_str:
            state["trades_today"] = 0
            state["date"] = today_str

        # Skip weekend
        if is_weekend():
            print(f"[{now.strftime('%H:%M:%S')}] Weekend — sleeping 1h")
            time.sleep(3600)
            continue

        # Session check
        ses = t("market_sessions")
        quality = ses.get("quality", 0)
        if quality < 0.5:
            print(f"[{now.strftime('%H:%M:%S')}] Low liquidity session ({quality*100:.0f}%) — skipping")
            time.sleep(state["interval_sec"])
            continue

        # News check
        news = t("news_check")
        if news.get("has_event"):
            for ev in news.get("events", []):
                if ev.get("impact") in ("high", "medium"):
                    print(f"[{now.strftime('%H:%M:%S')}] News event near: {ev.get('title')} — skipping")
                    time.sleep(state["interval_sec"])
                    continue

        # Daily limit
        if state["trades_today"] >= state["daily_limit"]:
            print(f"[{now.strftime('%H:%M:%S')}] Daily limit reached ({state['daily_limit']})")
            time.sleep(state["interval_sec"])
            continue

        # Conviction scan on each symbol
        best_sym = None
        best_conf = 0
        best_verdict = None

        for sym in state["symbols"]:
            try:
                conv = t("conviction_decide", {"symbol": sym})
                d = conv.get("decision", {})
                conf = d.get("confidence_pct", 0)
                verdict = d.get("verdict", "PASS")
                signals = d.get("signals", [])
                if verdict != "PASS" and conf >= state["min_confidence"]:
                    print(f"  {sym}: {verdict} ({conf}%) {signals}")
                    if conf > best_conf:
                        best_conf = conf
                        best_sym = sym
                        best_verdict = verdict
            except Exception as e:
                continue

        if best_sym and best_verdict:
            order_type = "BUY" if "BUY" in best_verdict else "SELL"
            print(f"\n🎯 TRADE SIGNAL: {order_type} {best_sym} @ {best_conf}% confidence")

            # Check spread
            price_info = _mt5_direct({"action": "price", "symbol": best_sym})
            spread = price_info.get("spread", 999)
            print(f"   Spread: {spread} points (limit: 80)")

            # Run preflight check
            check = _mt5_direct({
                "action": "check_order",
                "symbol": best_sym,
                "type": order_type,
                "volume": 0.01,
            })
            retcode = check.get("result", {}).get("retcode", -1)
            print(f"   OrderCheck retcode: {retcode}")

            if spread <= 80 and retcode in (0, 10009):
                print(f"   ✅ Would place {order_type} 0.01 {best_sym}")
                state["trades_today"] += 1

                # Send order (paper mode for now — needs confirm_live)
                result = _mt5_direct({
                    "action": "send_order",
                    "symbol": best_sym,
                    "type": order_type,
                    "volume": 0.01,
                })
                if result.get("success"):
                    print(f"   ✅ Order executed: ticket={result.get('ticket')}")
                else:
                    print(f"   ❌ Order failed: {result.get('error')}")
                    print(f"   Result: {json.dumps(result, indent=2)}")
            else:
                print(f"   ❌ Blocked by guard (spread={spread}, retcode={retcode})")

        else:
            print(f"[{now.strftime('%H:%M:%S')}] No tradeable signal above {state['min_confidence']}%")

        save_state(state)
        time.sleep(state["interval_sec"])

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user")
