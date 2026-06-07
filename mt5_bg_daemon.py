#!/usr/bin/env python3
"""
mt5_bg_daemon.py — Persistent background precompute engine.
Runs forever, writes state to bg_state.json every cycle.
Start:  python3 mt5_bg_daemon.py &
Status: python3 -c "import json; print(json.load(open('data/bg_state.json')))"
"""
import json, os, sys, time, subprocess
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).parent
DATA_DIR = HERE / "data"
STATE_FILE = DATA_DIR / "bg_state.json"
WINE_BIN = "/Applications/MetaTrader 5.app/Contents/SharedSupport/wine/bin/wine"
WINEPREFIX = os.path.expanduser("~/Library/Application Support/net.metaquotes.wine.metatrader5")
WINE_PYTHON = os.path.expanduser("~/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Python312/python.exe")
DIRECT_CLI = str(HERE / "mt5_direct_cli.py")

SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD", "NZDUSD", "USDCHF"]
FX_PAIRS = {"EURUSD","GBPUSD","USDJPY","USDCAD","USDCHF","AUDUSD","NZDUSD",
            "EURGBP","EURJPY","EURCHF","AUDJPY","GBPJPY","CHFJPY","EURAUD",
            "EURCAD","GBPCHF","GBPAUD","AUDCAD","AUDCHF","AUDNZD","CADCHF",
            "CADJPY","NZDCAD","NZDJPY","NZDCHF","GBPNZD","EURNZD"}
CACHE = {}

def fix(sym):
    return sym + ".FX" if sym in FX_PAIRS else sym

def mt5_call(cmd, timeout=90):
    env = os.environ.copy()
    env["WINEPREFIX"] = WINEPREFIX
    env["WINEDEBUG"] = "-all"
    proc = subprocess.run(
        [WINE_BIN, WINE_PYTHON, DIRECT_CLI, json.dumps(cmd)],
        text=True, capture_output=True, timeout=timeout, env=env,
    )
    lines = [l for l in proc.stdout.splitlines() if l.strip()]
    if not lines:
        return {"error": "no output"}
    return json.loads(lines[-1])

def backtest_quick(closes):
    if len(closes) < 30:
        return None
    def ema(data, period):
        if len(data) < period:
            return [None]*len(data)
        mult = 2/(period+1)
        result = [None]*(period-1)
        ema_v = sum(data[:period])/period
        for i in range(period-1, len(data)):
            if i == period-1:
                result.append(ema_v)
            else:
                ema_v = (data[i]-ema_v)*mult+ema_v
                result.append(ema_v)
        return result
    f = ema(closes, 5)
    s = ema(closes, 20)
    trades = []
    bal = 100.0
    pos = None
    for i in range(20, len(closes)-1):
        if f[i] is None or s[i] is None:
            continue
        if pos is None:
            if f[i-1] <= s[i-1] and f[i] > s[i]:
                pos = {"t": "BUY", "e": closes[i], "idx": i}
            elif f[i-1] >= s[i-1] and f[i] < s[i]:
                pos = {"t": "SELL", "e": closes[i], "idx": i}
            continue
        if i - pos["idx"] < 5:
            continue
        pnl = 0
        if pos["t"] == "BUY":
            pnl = (closes[i]-pos["e"])/pos["e"]
        else:
            pnl = (pos["e"]-closes[i])/pos["e"]
        bal += bal * pnl * 10
        trades.append(bal - 100)
        pos = None
    if len(trades) < 3:
        return None
    wins = sum(1 for t in trades if t > 0)
    wr = wins/len(trades)*100
    pw = sum(t for t in trades if t > 0)
    pl = abs(sum(t for t in trades if t <= 0))
    pf = round(pw/pl, 2) if pl > 0 else "inf"
    return {"trades": len(trades), "win_rate": round(wr, 1), "profit_factor": pf, "net_pnl": round(bal-100, 2)}

def compute(sym, price_data, m1_candles):
    bid = price_data.get("bid", 0)
    ask = price_data.get("ask", 0)
    spread = price_data.get("spread", 99)
    tradeable = spread < 80 and bid > 0
    info = {"bid": bid, "ask": ask, "spread": spread, "tradeable": tradeable}
    
    closes = [c.get("close", 0) for c in m1_candles]
    highs = [c.get("high", 0) for c in m1_candles]
    lows = [c.get("low", 0) for c in m1_candles]
    
    if len(m1_candles) >= 5:
        r5 = m1_candles[-5:]
        mom = (r5[-1]["close"] - r5[-5]["close"]) / r5[-5]["close"] * 100
        v_sum = sum(c2["high"] - c2["low"] for c2 in m1_candles[-4:])
        c_sum = sum(c2["close"] for c2 in m1_candles[-4:]) or 1
        info["momentum"] = round(mom, 3)
        info["volatility"] = round(v_sum / c_sum * 100 * 4, 3)
        info["resist"] = round(max(highs[-20:]), 5)
        info["support"] = round(min(lows[-20:]), 5)
        info["range"] = round((max(highs[-20:]) - min(lows[-20:])) * 10000, 1)
    
    if len(closes) >= 15:
        r15 = closes[-15:]
        g = l = 0
        for i in range(1, len(r15)):
            d = r15[i]-r15[i-1]
            g += max(d, 0)
            l += max(-d, 0)
        rsi = 50 if l == 0 else round(100-100/(1+g/l), 1)
        info["rsi"] = rsi
        info["rsi_signal"] = "overbought" if rsi > 70 else "oversold" if rsi < 30 else "neutral"
    
    if len(closes) >= 25:
        fast = sum(closes[-5:])/5
        slow = sum(closes[-20:])/20
        pf = sum(closes[-6:-1])/5
        ps = sum(closes[-21:-1])/20
        info["trend"] = "up" if fast > slow else "down"
        info["cross"] = "bullish" if pf <= ps and fast > slow else "bearish" if pf >= ps and fast < slow else "none"
        
        trs = []
        for i in range(1, min(15, len(m1_candles))):
            c_i = m1_candles[-i]
            c_im1 = m1_candles[-i-1]
            hl = c_i["high"] - c_i["low"]
            hc = abs(c_i["high"] - c_im1["close"])
            lc = abs(c_i["low"] - c_im1["close"])
            trs.append(max(hl, hc, lc))
        atr = sum(trs)/len(trs) if trs else 0
        info["atr_pips"] = round(atr*10000, 1)
        info["sl_pips"] = round(atr*1.5*10000, 1)
        info["tp_pips"] = round(atr*3*10000, 1)
    
    if tradeable and len(closes) >= 200:
        bt = backtest_quick(closes)
        if bt:
            wr = bt["win_rate"]
            pf = bt["profit_factor"]
            pf_v = float(pf) if pf != "inf" and pf else 0
            info["bt_signal"] = "GOOD" if wr > 55 and pf_v > 1.2 else "NEUTRAL" if wr > 40 else "SKIP"
            info["bt_win_rate"] = wr
            info["bt_profit_factor"] = pf
    
    # Pre-compute scenarios for +5p, +10p, +15p
    if tradeable and bid > 0:
        pip = 0.0001
        sc = {}
        for pips in [5, 10, 15, 20]:
            up = bid + pips * pip
            dn = bid - pips * pip
            sc[f"+{pips}p"] = {"price": round(up, 5), "action": "hold" if pips < 15 else "tp_zone"}
            sc[f"-{pips}p"] = {"price": round(dn, 5), "action": "hold" if pips < 10 else "sl_warn" if pips < 15 else "sl_zone"}
        info["scenarios"] = sc
    
    return info

def run_cycle():
    """One full cycle: batch fetch → compute for all symbols → save state."""
    # Build batch
    cmds = []
    for sym in SYMBOLS:
        s = fix(sym)
        cmds.append({"action": "price", "symbol": s})
        cmds.append({"action": "candles", "symbol": s, "timeframe": "M1", "count": 300})
    
    try:
        raw = mt5_call({"action": "batch", "commands": cmds}, timeout=45)
        results = raw.get("results", [])
    except:
        results = []
    
    if not results:
        # Fallback: sequential
        results = []
        for sym in SYMBOLS:
            s = fix(sym)
            results.append(mt5_call({"action": "price", "symbol": s}, timeout=15))
            results.append(mt5_call({"action": "candles", "symbol": s, "timeframe": "M1", "count": 300}, timeout=20))
    
    # Parse results
    symbols_data = {}
    for i, sym in enumerate(SYMBOLS):
        try:
            price = results[i*2] if i*2 < len(results) else {}
            candles_raw = results[i*2+1] if i*2+1 < len(results) else {}
            m1 = candles_raw.get("candles", candles_raw.get("data", []))
            if not m1:
                m1 = CACHE.get(sym, [])
            else:
                CACHE[sym] = m1[-300:] if len(m1) > 300 else m1
            symbols_data[sym] = compute(sym, price, m1)
        except Exception as e:
            symbols_data[sym] = {"error": str(e)}
    
    # Market context
    now = datetime.now(timezone.utc)
    h = now.hour
    if 8 <= h < 17:
        ses, q = "London", "high"
    elif 13 <= h < 22:
        ses, q = "NY", "high" if h < 17 else "medium"
    elif 0 <= h < 9:
        ses, q = "Asia/Pacific", "low"
    else:
        ses, q = "off_hours", "very_low"
    
    best = sorted(
        [(s, d) for s, d in symbols_data.items() if isinstance(d, dict) and d.get("tradeable")],
        key=lambda x: x[1].get("bt_win_rate", 0) if x[1].get("bt_win_rate") else 0,
        reverse=True,
    )
    
    state = {
        "timestamp": now.isoformat(),
        "status": "running",
        "session": ses, "session_quality": q, "hour_utc": h,
        "market_volatility": symbols_data.get("EURUSD", {}).get("volatility", "?"),
        "best_pairs": [{"symbol": s, "trend": d.get("trend", "?"),
                        "bt": d.get("bt_signal", "?"), "spread": d.get("spread", 99)}
                       for s, d in best[:5] if isinstance(d, dict)],
        "top_opportunity": best[0] if best else None,
        "symbols": symbols_data,
    }
    
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)
    
    now_str = now.strftime("%H:%M:%S")
    n_tradeable = sum(1 for s in symbols_data.values() if isinstance(s, dict) and s.get("tradeable"))
    n_good = sum(1 for s in symbols_data.values() if isinstance(s, dict) and s.get("bt_signal") == "GOOD")
    print(f"[{now_str}] {len(results)} items | {n_tradeable} tradeable | {n_good} GOOD signals", flush=True)
    return state

def main():
    print(f"BG Daemon started. PID={os.getpid()}", flush=True)
    print(f"Symbols: {SYMBOLS}", flush=True)
    cycle = 0
    while True:
        try:
            run_cycle()
            cycle += 1
        except Exception as e:
            print(f"Cycle {cycle} error: {e}", flush=True)
            import traceback
            traceback.print_exc()
        # Sleep 7 seconds
        for _ in range(7):
            time.sleep(1)

if __name__ == "__main__":
    main()
