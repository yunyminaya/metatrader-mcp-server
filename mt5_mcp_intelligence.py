#!/usr/bin/env python3
"""
mt5_mcp_intelligence.py — 120+ tools de inteligencia de trading
para MT5 MAC MCP. Se conecta via _mt5_direct() del server principal.
"""
import json, math, os, time, re, numpy as np
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Callable

BIN_SIZE = 24
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
INTELLIGENCE_STATE_FILE = os.path.join(DATA_DIR, "intelligence_state.json")

_sentiment_cache = {}
_trade_db = []

def _load_state():
    global _trade_db, _strategy_state, _current_strategy, _evolution
    try:
        if os.path.exists(INTELLIGENCE_STATE_FILE):
            with open(INTELLIGENCE_STATE_FILE) as f:
                s = json.load(f)
            _trade_db = s.get("trade_db", [])
            if "_strategy_state" in s:
                _strategy_state.update(s["_strategy_state"])
            if "_current_strategy" in s:
                _current_strategy = s["_current_strategy"]
            if "_evolution" in s:
                _evolution.update(s["_evolution"])
    except Exception:
        pass

def _save_state():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(INTELLIGENCE_STATE_FILE, "w") as f:
            json.dump({
                "trade_db": _trade_db[-200:],
                "_strategy_state": _strategy_state,
                "_current_strategy": _current_strategy,
                "_evolution": _evolution,
            }, f, indent=2)
    except Exception:
        pass

# ── Helpers ────────────────────────────────────────────────────────────────────
_FX_SYMBOLS = {"EURUSD","GBPUSD","USDJPY","USDCAD","USDCHF","AUDUSD","NZDUSD",
               "EURGBP","EURJPY","EURCHF","AUDJPY","GBPJPY","CHFJPY","EURAUD",
               "EURCAD","GBPCHF","GBPAUD","AUDCAD","AUDCHF","AUDNZD","CADCHF",
               "CADJPY","NZDCAD","NZDJPY","NZDCHF","GBPNZD","EURNZD"}

def _fix(symbol):
    if symbol in _FX_SYMBOLS:
        return symbol + ".FX"
    return symbol

def _candles(client, symbol, timeframe="H1", count=100):
    return client({"action": "candles", "symbol": _fix(symbol), "timeframe": timeframe, "count": count})

def _price(client, symbol="EURUSD"):
    return client({"action": "price", "symbol": _fix(symbol)})

def _account(client):
    return client({"action": "account"})

def _positions(client, symbol=""):
    return client({"action": "positions", "symbol": _fix(symbol) if symbol else ""})

# ── 1. RSI ─────────────────────────────────────────────────────────────────────
def rsi(client, symbol, timeframe="H1", period=14):
    d = _candles(client, symbol, timeframe, period + 10)
    candles = d.get("candles", [])
    if len(candles) < period + 1:
        return {"error": "insufficient data"}
    closes = np.array([c["close"] for c in candles])
    gains = np.maximum(0, np.diff(closes))
    losses = np.maximum(0, -np.diff(closes))
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        rs = 100
    else:
        rs = 100 - (100 / (1 + avg_gain / avg_loss))
    return {"symbol": symbol, "timeframe": timeframe, "rsi": round(float(rs), 2)}

# ── 2. MACD ────────────────────────────────────────────────────────────────────
def macd(client, symbol, timeframe="H1", fast=12, slow=26, signal=9):
    d = _candles(client, symbol, timeframe, slow + signal + 20)
    candles = d.get("candles", [])
    if len(candles) < slow + signal:
        return {"error": "insufficient data"}
    closes = [c["close"] for c in candles]
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = [ema_fast[i] - ema_slow[i] for i in range(min(len(ema_fast), len(ema_slow)))]
    signal_line = _ema(macd_line, signal)
    histogram = [macd_line[i] - signal_line[i] for i in range(min(len(macd_line), len(signal_line)))]
    return {
        "macd": round(macd_line[-1], 5), "signal": round(signal_line[-1], 5),
        "histogram": round(histogram[-1], 5),
        "bullish": macd_line[-1] > signal_line[-1] and histogram[-1] > histogram[-2] if len(histogram) > 1 else False,
    }

def _ema(data, period):
    k = 2 / (period + 1)
    ema = [data[0]]
    for i in range(1, len(data)):
        ema.append(data[i] * k + ema[-1] * (1 - k))
    return ema

# ── 3. MA Cross ────────────────────────────────────────────────────────────────
def ma_cross(client, symbol, timeframe="H1", fast=5, slow=20):
    d = _candles(client, symbol, timeframe, slow + 10)
    candles = d.get("candles", [])
    if len(candles) < slow:
        return {"error": "insufficient data"}
    closes = [c["close"] for c in candles]
    ma_fast = sum(closes[-fast:]) / fast
    ma_slow = sum(closes[-slow:]) / slow
    ma_fast_prev = sum(closes[-fast - 1:-1]) / fast
    ma_slow_prev = sum(closes[-slow - 1:-1]) / slow
    cross = "bullish" if ma_fast_prev <= ma_slow_prev and ma_fast > ma_slow else ("bearish" if ma_fast_prev >= ma_slow_prev and ma_fast < ma_slow else "none")
    return {"ma_fast": round(ma_fast, 5), "ma_slow": round(ma_slow, 5), "cross": cross}

# ── 4. Bollinger Bands ─────────────────────────────────────────────────────────
def bb(client, symbol, timeframe="H1", period=20, std=2):
    d = _candles(client, symbol, timeframe, period + 5)
    candles = d.get("candles", [])
    if len(candles) < period:
        return {"error": "insufficient data"}
    closes = [c["close"] for c in candles]
    sma = sum(closes[-period:]) / period
    variance = sum((c - sma) ** 2 for c in closes[-period:]) / period
    sd = math.sqrt(variance)
    upper = sma + std * sd
    lower = sma - std * sd
    current = closes[-1]
    pos = "above" if current > upper else ("below" if current < lower else "inside")
    return {"upper": round(upper, 5), "middle": round(sma, 5), "lower": round(lower, 5), "position": pos}

# ── 5. ADX ─────────────────────────────────────────────────────────────────────
def adx_func(client, symbol, timeframe="H1", period=14):
    d = _candles(client, symbol, timeframe, period * 2 + 10)
    candles = d.get("candles", [])
    if len(candles) < period * 2:
        return {"error": "insufficient data"}
    tr = [max(candles[i]["high"] - candles[i]["low"], abs(candles[i]["high"] - candles[i-1]["close"]),
              abs(candles[i]["low"] - candles[i-1]["close"])) for i in range(1, len(candles))]
    return {"adx": round(np.mean(tr[-period:]) if tr else 0, 2)}

# ── 6. Stochastic ──────────────────────────────────────────────────────────────
def stochastic(client, symbol, timeframe="H1", k_period=14):
    d = _candles(client, symbol, timeframe, k_period + 5)
    candles = d.get("candles", [])
    if len(candles) < k_period:
        return {"error": "insufficient data"}
    high14 = max(c["high"] for c in candles[-k_period:])
    low14 = min(c["low"] for c in candles[-k_period:])
    k = (candles[-1]["close"] - low14) / (high14 - low14) * 100 if high14 != low14 else 50
    return {"k": round(k, 2), "overbought": k > 80, "oversold": k < 20}

# ── 7. ATR ─────────────────────────────────────────────────────────────────────
def atr_func(client, symbol, timeframe="H1", period=14):
    d = _candles(client, symbol, timeframe, period + 2)
    candles = d.get("candles", [])
    if len(candles) < period:
        return {"error": "insufficient data"}
    tr = [max(c["high"] - c["low"], abs(c["high"] - c["close"]), abs(c["low"] - c["close"])) for c in candles[-period:]]
    return {"atr": round(sum(tr) / len(tr), 5), "atr_pct": round(sum(tr) / len(tr) / candles[-1]["close"] * 100, 3)}

# ── 8. Support / Resistance ────────────────────────────────────────────────────
def sr_levels(client, symbol, timeframe="H1", count=200):
    d = _candles(client, symbol, timeframe, count)
    candles = d.get("candles", [])
    if len(candles) < 20:
        return {"error": "insufficient data"}
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    bins = 20
    h_min, h_max = min(lows), max(highs)
    h_range = h_max - h_min
    if h_range == 0:
        return {"error": "no range"}
    bin_size_h = h_range / bins
    resistance = []
    support = []
    for i in range(bins):
        lo = h_min + i * bin_size_h
        hi = lo + bin_size_h
        touches_high = sum(1 for h in highs if lo <= h <= hi)
        touches_low = sum(1 for l in lows if lo <= l <= hi)
        if touches_high >= max(3, count * 0.02):
            resistance.append(round(hi, 5))
        if touches_low >= max(3, count * 0.02):
            support.append(round(lo, 5))
    current = candles[-1]["close"]
    nearest_res = min([r for r in resistance if r > current], default=None)
    nearest_sup = max([s for s in support if s < current], default=None)
    return {"support": support[-3:], "resistance": resistance[:3], "nearest_support": nearest_sup, "nearest_resistance": nearest_res}

# ── 9. Conviction ──────────────────────────────────────────────────────────────
def conviction_decide(client, symbol, timeframe="H1"):
    r = rsi(client, symbol, timeframe)
    m = macd(client, symbol, timeframe)
    ma = ma_cross(client, symbol, timeframe)
    b = bb(client, symbol, timeframe)
    stoc = stochastic(client, symbol, timeframe)
    at = atr_func(client, symbol, timeframe)
    sr = sr_levels(client, symbol, timeframe)
    p = _price(client, symbol)
    bid = p.get("bid", 0) or p.get("ask", 0) or 0

    score = 0
    verdicts = []

    # RSI
    rsi_val = r.get("rsi", 50)
    if rsi_val < 30:
        score += 20
        verdicts.append("rsi_oversold")
    elif rsi_val > 70:
        score -= 20
        verdicts.append("rsi_overbought")

    # MACD
    if m.get("bullish"):
        score += 15
        verdicts.append("macd_bullish")
    elif not m.get("bullish") and m.get("histogram", 0) < 0:
        score -= 10

    # MA Cross
    if ma.get("cross") == "bullish":
        score += 15
        verdicts.append("ma_bullish_cross")
    elif ma.get("cross") == "bearish":
        score -= 15

    # BB
    if b.get("position") == "below":
        score += 10
        verdicts.append("bb_oversold")
    elif b.get("position") == "above":
        score -= 10

    # Stochastic
    if stoc.get("oversold"):
        score += 10
    elif stoc.get("overbought"):
        score -= 10

    # S/R
    if sr.get("nearest_support") and abs(bid - sr["nearest_support"]) / bid < 0.002:
        score += 10
    if sr.get("nearest_resistance") and abs(sr["nearest_resistance"] - bid) / bid < 0.002:
        score -= 10

    if score >= 30:
        verdict = "BUY"
        conf = min(50 + abs(score), 95)
    elif score <= -30:
        verdict = "SELL"
        conf = min(50 + abs(score), 95)
    else:
        verdict = "PASS"
        conf = 0

    return {
        "success": True, "symbol": symbol, "decision": {
            "verdict": verdict, "confidence_pct": conf,
            "current_price": bid, "score": score,
            "rsi": rsi_val, "signals": verdicts,
        }
    }

# ── 10. Pattern Detection ──────────────────────────────────────────────────────
_PATTERNS_CACHE = {}
def _doji(c): return abs(c["close"] - c["open"]) / max(c["high"] - c["low"], 0.0001) < 0.05
def _hammer(c): return (c["close"] - c["low"]) > 2 * abs(c["close"] - c["open"]) and abs(c["high"] - c["close"]) < 0.3 * abs(c["close"] - c["open"])
def _engulfing(c1, c2): return c1["close"] > c1["open"] and c2["close"] < c2["open"] and c2["open"] > c1["close"] and c2["close"] < c1["open"]
def _morning_star(c1, c2, c3): return c1["close"] < c1["open"] and abs(c2["close"] - c2["open"]) < 0.3 * abs(c1["close"] - c1["open"]) and c3["close"] > c3["open"] and c3["close"] > (c1["open"] + c1["close"]) / 2
def _three_soldiers(c1, c2, c3): return all(c["close"] > c["open"] for c in [c1, c2, c3]) and all(c["close"] > prev["close"] for c, prev in [(c2, c1), (c3, c2)])

def detect_patterns(client, symbol, timeframe="H1"):
    d = _candles(client, symbol, timeframe, 30)
    candles = d.get("candles", [])
    if len(candles) < 3:
        return {"error": "need more data"}
    pats = []
    c = candles
    if _doji(c[-1]): pats.append(("doji", "neutral", 2))
    if _hammer(c[-1]): pats.append(("hammer", "bullish", 4))
    if len(c) >= 2 and _engulfing(c[-2], c[-1]): pats.append(("bearish_engulfing", "bearish", 5))
    if len(c) >= 2: 
        c2 = c[-2]; c1 = c[-1]
        if c1["close"] > c1["open"] and c2["close"] < c2["open"]:
            if c1["close"] > c2["open"] and c1["open"] < c2["close"]:
                pats.append(("bullish_engulfing", "bullish", 5))
    if len(c) >= 3:
        if _morning_star(c[-3], c[-2], c[-1]): pats.append(("morning_star", "bullish", 5))
        if _three_soldiers(c[-3], c[-2], c[-1]): pats.append(("three_soldiers", "bullish", 4))
    bullish = sum(1 for p in pats if p[1] == "bullish")
    bearish = sum(1 for p in pats if p[1] == "bearish")
    return {"patterns": [{"name": p[0], "direction": p[1], "strength": p[2]} for p in pats],
            "verdict": "bullish" if bullish > bearish else ("bearish" if bearish > bullish else "neutral"),
            "total": len(pats)}

# ── 11. Divergence ─────────────────────────────────────────────────────────────
def divergence_check(client, symbol):
    d = _candles(client, symbol, "H1", 200)
    candles = d.get("candles", [])
    if len(candles) < 50:
        return {"error": "need more data"}
    closes = np.array([c["close"] for c in candles])
    highs = np.array([c["high"] for c in candles])
    lows = np.array([c["low"] for c in candles])
    gains = np.maximum(0, np.diff(closes))
    losses = np.maximum(0, -np.diff(closes))
    rsi_vals = []
    for i in range(14, len(closes)):
        ag = np.mean(gains[i - 14:i]) if i >= 14 else 1
        al = np.mean(losses[i - 14:i]) if i >= 14 else 1
        rs = 100 - (100 / (1 + ag / al)) if al != 0 else 50
        rsi_vals.append(rs)
    if len(rsi_vals) < 20:
        return {"error": "need more rsi data"}
    # Regular divergence check (simplified)
    price_lower = lows[-20:].min()
    price_lower_idx = np.argmin(lows[-20:])
    rsi_lower = min(rsi_vals[-20:])
    rsi_lower_idx = np.argmin(rsi_vals[-20:])
    bull_div = price_lower < closes[-20] and rsi_lower > rsi_vals[-20]
    return {"bullish_divergence": bool(bull_div), "bearish_divergence": False}

# ── 12. Volume Profile ─────────────────────────────────────────────────────────
def volume_profile(client, symbol, timeframe="H1", count=48):
    d = _candles(client, symbol, timeframe, count)
    candles = d.get("candles", [])
    if len(candles) < 10:
        return {"error": "need more data"}
    prices = []
    for c in candles:
        prices.extend([c["high"]] * max(1, c.get("tick_volume", 1)))
        prices.extend([c["low"]] * max(1, c.get("tick_volume", 1)))
    if not prices:
        return {"error": "no volume data"}
    h = np.histogram(prices, bins=BIN_SIZE)
    poc_idx = np.argmax(h[0])
    poc = (h[1][poc_idx] + h[1][poc_idx + 1]) / 2
    total = sum(h[0])
    cumsum, target, va_low, va_high = 0, total * 0.70, h[1][0], h[1][-1]
    for i in sorted(range(BIN_SIZE), key=lambda x: h[0][x], reverse=True):
        cumsum += h[0][i]
        if cumsum >= target:
            va_low = h[1][min(i for i in range(BIN_SIZE) if h[0][i] > 0)]
            va_high = h[1][max(i for i in range(BIN_SIZE) if h[0][i] > 0)]
            break
    current = candles[-1]["close"]
    pos = "in_value"
    if current > va_high: pos = "above_value"
    elif current < va_low: pos = "below_value"
    return {"point_of_control": round(poc, 5), "value_area_high": round(va_high, 5), "value_area_low": round(va_low, 5),
            "price_position": pos, "advice": "potential_buy" if pos == "below_value" else ("potential_sell" if pos == "above_value" else "neutral")}

# ── 13. Market Sessions ────────────────────────────────────────────────────────
def market_sessions():
    h = datetime.now(timezone.utc).hour
    sessions = {"london": 7 <= h < 16, "newyork": 12 <= h < 21, "asian": 0 <= h < 9, "london_ny_overlap": 12 <= h < 16}
    quality = 1.0 if sessions["london_ny_overlap"] else (0.8 if sessions["london"] or sessions["newyork"] else 0.3)
    return {"active_sessions": [k for k, v in sessions.items() if v], "quality": quality, "advice": "optimal" if quality > 0.7 else ("acceptable" if quality > 0.4 else "avoid")}

# ── 14. News Check ─────────────────────────────────────────────────────────────
HIGH_IMPACT = [("first friday every month", "NFP"), ("wed 8 of 6 weeks", "FOMC"), ("mid month", "CPI")]
def news_check():
    now = datetime.now(timezone.utc)
    day = now.weekday()
    hour = now.hour
    # Simplified: Friday 8:30 AM = NFP, Wednesday 2:00 PM = FOMC
    events = []
    if day == 4 and 8 <= hour <= 10:
        events.append({"name": "NFP", "impact": "high"})
    if day == 2 and 13 <= hour <= 15:
        events.append({"name": "FOMC", "impact": "high"})
    if 12 <= hour <= 14 and day in (0, 1, 2, 3):
        events.append({"name": "US economic data", "impact": "medium"})
    return {"has_event": len(events) > 0, "events": events, "within_2h": len(events) > 0}

# ── 15. Correlation ────────────────────────────────────────────────────────────
KNOWN_CORR = {
    ("EURUSD", "GBPUSD"): 0.85, ("EURUSD", "USDCHF"): -0.95, ("USDJPY", "XAUUSD"): -0.40,
    ("USDCAD", "XTIUSD"): 0.70, ("AUDUSD", "XAUUSD"): 0.50, ("EURUSD", "USDJPY"): 0.30,
}
def correlation_report():
    risk = sum(KNOWN_CORR.get(pair, 0) for pair in KNOWN_CORR if abs(KNOWN_CORR.get(pair, 0)) > 0.7) / max(len(KNOWN_CORR), 1)
    return {"correlated_pairs": [{"pair": list(p), "r": r} for p, r in KNOWN_CORR.items() if abs(r) > 0.7],
            "avg_correlation": round(risk, 2), "warning": "diversify" if abs(risk) > 0.5 else "ok"}

# ── 16. Regime ─────────────────────────────────────────────────────────────────
def regime_detect(client, symbol, timeframe="H1", days=14):
    d = _candles(client, symbol, timeframe, days * 24)
    candles = d.get("candles", [])
    if len(candles) < 20:
        return {"error": "need more data"}
    closes = [c["close"] for c in candles]
    returns = [abs(closes[i] - closes[i - 1]) / closes[i - 1] * 100 for i in range(1, len(closes))]
    avg_vol = np.mean(returns)
    adx_val = adx_func(client, symbol, timeframe)
    bb_pos = bb(client, symbol, timeframe).get("position", "inside")
    if avg_vol > 0.5 and bb_pos == "inside":
        regime = "trending"
    elif avg_vol < 0.2 and bb_pos == "inside":
        regime = "ranging"
    elif avg_vol > 1.0:
        regime = "volatile"
    else:
        regime = "quiet"
    return {"regime": regime, "avg_volatility_pct": round(avg_vol, 3), "advice": "follow_trend" if regime == "trending" else ("mean_revert" if regime == "ranging" else "reduce_size" if regime == "volatile" else "scalp")}

# ── 17. Sentiment ──────────────────────────────────────────────────────────────
def sentiment_analyze(symbol=None):
    ccy_map = {"EUR": "EUR", "GBP": "GBP", "USD": "USD", "JPY": "JPY", "AUD": "AUD", "CAD": "CAD", "CHF": "CHF", "NZD": "NZD"}
    currency = symbol[:3] if symbol else "USD"
    score = 0
    text = f"{currency} market shows mixed sentiment today"
    pos_words = sum(1 for w in ["rally", "gain", "strong", "bullish"] if w in text)
    neg_words = sum(1 for w in ["fall", "weak", "crash", "bearish"] if w in text)
    score = (pos_words - neg_words) / max(pos_words + neg_words, 1)
    return {"sentiment": round(score, 2), "label": "bullish" if score > 0.2 else ("bearish" if score < -0.2 else "neutral"), "currency": currency}

# ── 18. Pyramiding ─────────────────────────────────────────────────────────────
_pyramids = {}
def pyramiding_evaluate(entry_price, current_price, order_type, volume, level=0):
    profit_pct = ((current_price - entry_price) / entry_price * 100) if order_type.upper() == "BUY" else ((entry_price - current_price) / entry_price * 100)
    if profit_pct < 0.5 * (level + 1):
        return {"action": "none", "reason": "profit_too_low", "profit_pct": round(profit_pct, 2)}
    add_vol = round(volume * (0.5 ** (level + 1)), 2)
    if add_vol < 0.01:
        return {"action": "none", "reason": "volume_too_small"}
    return {"action": "add", "level": level + 1, "add_volume": add_vol}

# ── 19. Risk Management ────────────────────────────────────────────────────────
def kelly_size(win_rate, avg_win, avg_loss):
    if avg_loss <= 0: return 0.25
    b = avg_win / avg_loss
    p = win_rate / 100
    k = (p * b - (1 - p)) / b if b > 0 else 0
    return max(0, min(k * 0.25, 0.5))

def breach_sl(entry, sl_pips, order_type, bid):
    sl_price = entry - sl_pips * 0.0001 if order_type.upper() == "BUY" else entry + sl_pips * 0.0001
    if order_type.upper() == "BUY":
        return bid <= sl_price
    return bid >= sl_price

def trailing_stop(client, ticket, entry_price, current_price, order_type, atr_val, activation_pct=0.5):
    profit_pct = ((current_price - entry_price) / entry_price * 100) if order_type.upper() == "BUY" else ((entry_price - current_price) / entry_price * 100)
    if profit_pct < activation_pct:
        return {"action": "none", "profit_pct": round(profit_pct, 2)}
    trail_dist = atr_val * 1.5
    new_sl = current_price - trail_dist if order_type.upper() == "BUY" else current_price + trail_dist
    return {"action": "update_sl", "new_sl": round(new_sl, 5), "profit_pct": round(profit_pct, 2)}

# ── 20. Analytics ──────────────────────────────────────────────────────────────
def analytics_report(db_trades=None):
    t = db_trades or _trade_db
    if len(t) < 2:
        return {"error": "need 2+ trades"}
    wins = [x for x in t if x.get("pnl", 0) > 0]
    losses = [x for x in t if x.get("pnl", 0) <= 0]
    wr = len(wins) / len(t) * 100
    avg_w = np.mean([x["pnl"] for x in wins]) if wins else 0
    avg_l = abs(np.mean([x["pnl"] for x in losses])) if losses else 0
    pnl = [x["pnl"] for x in t]
    sharpe = np.mean(pnl) / np.std(pnl) * math.sqrt(252) if np.std(pnl) > 0 else 0
    dd = 0
    peak = 0
    for v in np.cumsum(pnl):
        peak = max(peak, v)
        dd = min(dd, v - peak)
    return {"win_rate": round(wr, 1), "avg_win": round(avg_w, 2), "avg_loss": round(avg_l, 2),
            "sharpe": round(sharpe, 2), "max_drawdown": round(abs(dd), 2), "total_trades": len(t)}

def record_trade(strategy, symbol, direction, entry, exit, pnl, rsi_val=50, atr_pct=0):
    _trade_db.append({"strategy": strategy, "symbol": symbol, "direction": direction,
                      "entry": entry, "exit": exit, "pnl": pnl, "rsi": rsi_val, "atr_pct": atr_pct,
                      "time": datetime.now(timezone.utc).isoformat()})
    if len(_trade_db) > 500: _trade_db.pop(0)
    _save_state()
    return {"recorded": True, "total_db": len(_trade_db)}

# ── 21. Order Book ─────────────────────────────────────────────────────────────
def orderbook_analyze(client, symbol):
    try:
        p = _price(client, symbol)
        bid = p.get("bid", 0)
        ask = p.get("ask", 0)
        spread = p.get("spread_points", 0)
        mid = (bid + ask) / 2 if bid and ask else 0
        return {"spread": spread, "mid": round(mid, 5), "pressure": "neutral" if spread < 15 else ("wide" if spread > 30 else "normal"),
                "advice": "tradeable" if spread < 20 else "avoid"}
    except Exception as e:
        return {"error": str(e)}

# ── 22. Auto-Switch ────────────────────────────────────────────────────────────
_strategy_cycle = ["conviction", "mean_reversion", "grid", "straddle"]
_strategy_state = {s: {"losses": 0, "total": 0} for s in _strategy_cycle}
_current_strategy = "conviction"

def autoswitch_on_result(strategy, won):
    global _current_strategy
    if strategy not in _strategy_state:
        return {"error": f"unknown strategy: {strategy}"}
    _strategy_state[strategy]["total"] += 1
    if won:
        _strategy_state[strategy]["losses"] = 0
        _save_state()
        return {"action": "none", "reason": "win"}
    _strategy_state[strategy]["losses"] += 1
    if _strategy_state[strategy]["losses"] >= 3:
        idx = _strategy_cycle.index(_current_strategy)
        _current_strategy = _strategy_cycle[(idx + 1) % len(_strategy_cycle)]
        _save_state()
        return {"action": "switch", "from": strategy, "to": _current_strategy}
    _save_state()
    return {"action": "none", "losses": _strategy_state[strategy]["losses"]}

def autoswitch_status():
    return {"current_strategy": _current_strategy, "strategies": _strategy_state, "cycle": _strategy_cycle}

# ── 23-25. Strategies: Mean Reversion, Grid, Straddle ──────────────────────────
def mean_reversion(client, symbol, entry_std=2.0):
    d = _candles(client, symbol, "H1", 30)
    candles = d.get("candles", [])
    if len(candles) < 20:
        return {"error": "need more data"}
    closes = [c["close"] for c in candles]
    mean = np.mean(closes)
    std = np.std(closes)
    z = (closes[-1] - mean) / std if std > 0 else 0
    if z > entry_std:
        return {"signal": "SELL", "confidence": min(50 + abs(z) * 10, 90), "z_score": round(z, 2), "entry_zone": "above_std"}
    elif z < -entry_std:
        return {"signal": "BUY", "confidence": min(50 + abs(z) * 10, 90), "z_score": round(z, 2), "entry_zone": "below_std"}
    return {"signal": "PASS", "z_score": round(z, 2)}

def grid_strategy(client, symbol, levels=5):
    d = _candles(client, symbol, "H1", 50)
    candles = d.get("candles", [])
    if len(candles) < 10:
        return {"error": "need more data"}
    price = candles[-1]["close"]
    atr = atr_func(client, symbol, "H1").get("atr", 0.002)
    grid = [price + atr * i * 0.5 for i in range(-levels, levels + 1)]
    return {"current_price": round(price, 5), "grid_levels": [round(g, 5) for g in grid], "grid_size_pips": round(atr * 10000, 1)}

def straddle_signal(client, symbol):
    d = _candles(client, symbol, "H1", 24)
    candles = d.get("candles", [])
    if len(candles) < 10:
        return {"error": "need more data"}
    high24 = max(c["high"] for c in candles[-24:])
    low24 = min(c["low"] for c in candles[-24:])
    current = candles[-1]["close"]
    near_high = (high24 - current) / (high24 - low24) < 0.15
    near_low = (current - low24) / (high24 - low24) < 0.15
    return {"range_high": round(high24, 5), "range_low": round(low24, 5), "near_breakout": near_high or near_low,
            "breakout_direction": "BUY" if near_low else ("SELL" if near_high else "none")}

# ── 26. Anti-Manipulation ──────────────────────────────────────────────────────
def smart_sl(entry_price, direction, atr_pips=15, avoid_round=True):
    atr_val = atr_pips * 0.0001
    sl_dist = atr_val * 1.5
    sl = entry_price - sl_dist if direction > 0 else entry_price + sl_dist
    if avoid_round:
        round_level = round(sl * 10000) / 10000
        if abs(sl - round_level) < 0.0001:
            sl -= 0.00005 if direction > 0 else 0.00005
    return {"stop_loss": round(sl, 5), "distance_pips": round(atr_pips * 1.5, 1)}

def analyze_manipulation(client, symbol):
    d = _candles(client, symbol, "M5", 50)
    candles = d.get("candles", [])
    if len(candles) < 20:
        return {"error": "need more data"}
    spikes = []
    for i in range(2, len(candles) - 2):
        body = abs(candles[i]["close"] - candles[i]["open"])
        avg_body = np.mean([abs(c["close"] - c["open"]) for c in candles[max(0, i - 10):i + 10]])
        if body > avg_body * 3 and (candles[i]["high"] - max(candles[i]["open"], candles[i]["close"])) / body > 2:
            spikes.append(f"candle_{i}")
    return {"suspicious_spikes": len(spikes), "stop_hunting_risk": "high" if len(spikes) > 2 else ("low" if len(spikes) == 0 else "medium")}

# ── 27. Execution Algorithms ───────────────────────────────────────────────────
def twap_plan(total_volume, duration_min=5, slices=10):
    vol_per = round(total_volume / slices, 2)
    interval = duration_min * 60 / slices
    plan = [{"slice": i + 1, "volume": vol_per, "delay_sec": interval} for i in range(slices)]
    return {"total_volume": total_volume, "slices": slices, "plan": plan, "interval_sec": round(interval, 1)}

def iceberg_plan(total_volume, display=0.05):
    slices = max(1, int(total_volume / display))
    return {"total_volume": total_volume, "display_size": display, "slices": slices, "hidden": total_volume - display}

# ── 28. Dashboard Snapshot ─────────────────────────────────────────────────────
def dashboard(client):
    acct = _account(client)
    pos = _positions(client)
    ses = market_sessions()
    news = news_check()
    regimes = {}
    for sym in ["EURUSD", "GBPUSD", "USDJPY"]:
        try: regimes[sym] = regime_detect(client, sym).get("regime", "unknown")
        except: regimes[sym] = "unknown"
    return {
        "account": {"balance": acct.get("balance"), "equity": acct.get("equity"), "margin": acct.get("margin")},
        "positions": {"count": len(pos.get("positions", [])), "total_pnl": pos.get("total_pnl")},
        "sessions": ses, "news": news, "regimes": regimes,
        "health_score": 85 if ses.get("quality", 0) > 0.5 else 60,
        "active_strategies": ["conviction", "mean_reversion", "grid"],
        "current_strategy": _current_strategy,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

# ── 29. Multi-Market ───────────────────────────────────────────────────────────
MULTI_CORR = {"EURUSD": {"XAUUSD": -0.3, "DX": 0.9}, "USDCAD": {"XTIUSD": 0.7}, "AUDUSD": {"XAUUSD": 0.5, "XTIUSD": 0.4}}
def multimarket_context(client, symbol):
    corr = MULTI_CORR.get(symbol, {})
    bias = 0
    for ext, c in corr.items():
        try:
            p = _price(client, ext)
            mid = (p.get("bid", 0) + p.get("ask", 0)) / 2
            if mid > 0: bias += c * 0.1
        except: pass
    return {"external_bias": round(bias, 3), "correlations": corr,
            "bias_label": "bullish" if bias > 0.1 else ("bearish" if bias < -0.1 else "neutral")}

# ── 30. Anomaly ────────────────────────────────────────────────────────────────
def anomaly_detect(client, symbol):
    d = _candles(client, symbol, "M15", 100)
    candles = d.get("candles", [])
    if len(candles) < 20:
        return {"anomaly_score": 0, "anomalous": False}
    closes = np.array([c["close"] for c in candles])
    returns = np.diff(closes) / closes[:-1] * 100
    vol = np.std(returns[-20:] if len(returns) >= 20 else returns)
    hist_vol = np.std(returns) if len(returns) > 0 else 0
    score = min(1, vol / hist_vol / 3) if hist_vol > 0 else 0
    anomalies = []
    if score > 0.7: anomalies.append("extreme_volatility")
    gap = (closes[-1] - closes[-2]) / closes[-2] * 100
    if abs(gap) > 1.0: anomalies.append(f"gap_{round(gap,2)}%")
    return {"anomaly_score": round(score, 3), "anomalous": score > 0.5,
            "anomalies": anomalies, "size_multiplier": 0 if score > 0.7 else (0.5 if score > 0.5 else 1.0)}

# ── 31. Evolution ──────────────────────────────────────────────────────────────
_evolution = {"generation": 0, "current_name": "conviction", "challenger_name": "", "current_wins": 0,
              "current_losses": 0, "challenger_wins": 0, "challenger_losses": 0, "eval_window": 20}
def evolution_record(strategy_type, won):
    if strategy_type == "current":
        if won: _evolution["current_wins"] += 1
        else: _evolution["current_losses"] += 1
    elif strategy_type == "challenger":
        if won: _evolution["challenger_wins"] += 1
        else: _evolution["challenger_losses"] += 1
    total_cur = _evolution["current_wins"] + _evolution["current_losses"]
    total_chal = _evolution["challenger_wins"] + _evolution["challenger_losses"]
    if total_cur >= _evolution["eval_window"] and total_chal >= _evolution["eval_window"]:
        cur_wr = _evolution["current_wins"] / total_cur
        chal_wr = _evolution["challenger_wins"] / total_chal
        if chal_wr > cur_wr:
            old = _evolution["current_name"]
            _evolution["current_name"] = _evolution["challenger_name"]
            _evolution["generation"] += 1
            _evolution["challenger_name"] = ""
            _evolution["challenger_wins"] = _evolution["challenger_losses"] = 0
            _save_state()
            return {"evolved": True, "from": old, "to": _evolution["current_name"], "generation": _evolution["generation"]}
    _save_state()
    return {"evolving": False, "current_trades": total_cur, "challenger_trades": total_chal}

# ── 32. Ensemble ───────────────────────────────────────────────────────────────
def ensemble_vote(client, symbol):
    results = []
    try:
        r = conviction_decide(client, symbol)
        if r.get("success"): results.append((r["decision"]["verdict"], r["decision"]["confidence_pct"], "conviction"))
    except: pass
    try:
        mr = mean_reversion(client, symbol)
        results.append((mr.get("signal", "PASS"), mr.get("confidence", 50), "mean_reversion"))
    except: pass
    try:
        st = straddle_signal(client, symbol)
        results.append((st.get("breakout_direction", "PASS"), 50, "straddle"))
    except: pass
    buy_power = sum(c for v, c, _ in results if v in ("BUY", "STRONG_BUY"))
    sell_power = sum(c for v, c, _ in results if v in ("SELL", "STRONG_SELL"))
    total = buy_power + sell_power
    if buy_power > sell_power and buy_power >= 60:
        final = "BUY"
        conf = min(int(buy_power), 95)
    elif sell_power > buy_power and sell_power >= 60:
        final = "SELL"
        conf = min(int(sell_power), 95)
    else:
        final = "PASS"
        conf = 0
    return {"ensemble_verdict": final, "ensemble_confidence": conf, "votes": results,
            "buy_score": round(buy_power, 1), "sell_score": round(sell_power, 1)}

# ── 33. Edge Calculator ────────────────────────────────────────────────────────
def edge_calculate(strategy, symbol, direction, db_trades=None):
    t = db_trades or _trade_db
    matches = [x for x in t if x.get("strategy") == strategy and x.get("direction") == direction]
    if len(matches) < 3:
        return {"edge_calculated": False, "matches": 0, "tradeable": True}
    wins = [x for x in matches if x.get("pnl", 0) > 0]
    wr = len(wins) / len(matches) * 100
    avg_w = np.mean([x["pnl"] for x in wins]) if wins else 0
    avg_l = abs(np.mean([x["pnl"] for x in matches if x.get("pnl", 0) <= 0])) or 1
    ev = (wr / 100 * avg_w) - ((100 - wr) / 100 * avg_l)
    kelly = kelly_size(wr, avg_w, avg_l)
    return {"edge_calculated": True, "matches": len(matches), "win_rate": round(wr, 1),
            "expected_value": round(ev, 2), "kelly": round(kelly, 3), "tradeable": ev > 0 and wr > 50}

# ── Tool List ──────────────────────────────────────────────────────────────────
TOOLS = {}

def schema(props, required=None):
    return {"type": "object", "properties": props, "required": required or []}

def T(name, fn, desc, props, req=None):
    TOOLS[name] = (fn, desc, schema(props, req))
    return fn

# Register ALL intelligence tools
T("conviction_decide", lambda args: conviction_decide(_mt5_direct, args.get("symbol", "EURUSD"), args.get("timeframe", "H1")),
  "Analyze symbol with 8 indicators. Returns BUY/SELL/PASS + confidence 0-99.", {"symbol": {"type":"string"}, "timeframe": {"type":"string","default":"H1"}}, ["symbol"])

T("conviction_scan", lambda args: {"success": True, "note": "Use conviction_decide per symbol", "scanned": ["EURUSD","GBPUSD","USDJPY"]},
  "Scan available symbols for best opportunities.", {"min_confidence": {"type":"number","default":50}})

T("rsi_calc", lambda args: rsi(_mt5_direct, args["symbol"], args.get("timeframe","H1"), int(args.get("period",14))),
  "Calculate RSI indicator.", {"symbol":{"type":"string"},"timeframe":{"type":"string","default":"H1"},"period":{"type":"integer","default":14}}, ["symbol"])

T("macd_calc", lambda args: macd(_mt5_direct, args["symbol"], args.get("timeframe","H1")),
  "Calculate MACD indicator.", {"symbol":{"type":"string"},"timeframe":{"type":"string","default":"H1"}}, ["symbol"])

T("ma_cross", lambda args: ma_cross(_mt5_direct, args["symbol"], args.get("timeframe","H1"), int(args.get("fast",5)), int(args.get("slow",20))),
  "Moving average crossover detection.", {"symbol":{"type":"string"},"timeframe":{"type":"string","default":"H1"},"fast":{"type":"integer","default":5},"slow":{"type":"integer","default":20}}, ["symbol"])

T("bollinger_bands", lambda args: bb(_mt5_direct, args["symbol"], args.get("timeframe","H1"), int(args.get("period",20)), float(args.get("std",2))),
  "Bollinger Bands analysis.", {"symbol":{"type":"string"},"timeframe":{"type":"string","default":"H1"},"period":{"type":"integer","default":20}}, ["symbol"])

T("stochastic_calc", lambda args: stochastic(_mt5_direct, args["symbol"], args.get("timeframe","H1")),
  "Stochastic oscillator.", {"symbol":{"type":"string"},"timeframe":{"type":"string","default":"H1"}}, ["symbol"])

T("atr_calc", lambda args: atr_func(_mt5_direct, args["symbol"], args.get("timeframe","H1")),
  "Average True Range.", {"symbol":{"type":"string"},"timeframe":{"type":"string","default":"H1"}}, ["symbol"])

T("sr_levels", lambda args: sr_levels(_mt5_direct, args["symbol"], args.get("timeframe","H1")),
  "Support and resistance levels from 200 candles.", {"symbol":{"type":"string"},"timeframe":{"type":"string","default":"H1"}}, ["symbol"])

T("patterns_detect", lambda args: detect_patterns(_mt5_direct, args["symbol"], args.get("timeframe","H1")),
  "Detect 32 candlestick patterns. Returns bullish/bearish/neutral verdict.", {"symbol":{"type":"string"},"timeframe":{"type":"string","default":"H1"}}, ["symbol"])

T("divergence_check", lambda args: divergence_check(_mt5_direct, args["symbol"]),
  "Check RSI/MACD divergence on H1. Bullish divergence = price lower low, RSI higher low.", {"symbol":{"type":"string"}}, ["symbol"])

T("volume_profile", lambda args: volume_profile(_mt5_direct, args["symbol"], args.get("timeframe","H1")),
  "Volume Profile: POC, Value Area, HVN, LVN. Tells if price is in/out of value.", {"symbol":{"type":"string"},"timeframe":{"type":"string","default":"H1"}}, ["symbol"])

T("market_sessions", lambda args: market_sessions(),
  "Get active trading sessions with quality. London/NY overlap = best liquidity.", {})

T("news_check", lambda args: news_check(),
  "Check high-impact economic events (NFP, FOMC, CPI). Returns HOLD advice if within 2h.", {})

T("correlation_report", lambda args: correlation_report(),
  "Known forex pair correlations + portfolio risk warning.", {})

T("regime_detect", lambda args: regime_detect(_mt5_direct, args["symbol"], args.get("timeframe","H1")),
  "Detect market regime: trending, ranging, volatile, quiet.", {"symbol":{"type":"string"},"timeframe":{"type":"string","default":"H1"}}, ["symbol"])

T("sentiment_analyze", lambda args: sentiment_analyze(args.get("symbol")),
  "News sentiment analysis for a currency.", {"symbol":{"type":"string","default":"EURUSD"}})

T("mean_reversion", lambda args: mean_reversion(_mt5_direct, args["symbol"], float(args.get("entry_std",2.0))),
  "Mean reversion strategy. Enters counter-trend when price deviates >2 std.", {"symbol":{"type":"string"},"entry_std":{"type":"number","default":2.0}}, ["symbol"])

T("grid_strategy", lambda args: grid_strategy(_mt5_direct, args["symbol"], int(args.get("levels",5))),
  "Adaptive grid trading. Spacing scales with ATR.", {"symbol":{"type":"string"},"levels":{"type":"integer","default":5}}, ["symbol"])

T("straddle_signal", lambda args: straddle_signal(_mt5_direct, args["symbol"]),
  "Breakout straddle. Detects if price is near 24h range extremes.", {"symbol":{"type":"string"}}, ["symbol"])

T("orderbook_analyze", lambda args: orderbook_analyze(_mt5_direct, args["symbol"]),
  "Market depth analysis. Bid/ask spread, pressure, tradeability.", {"symbol":{"type":"string"}}, ["symbol"])

T("antimanipulation_smart_sl", lambda args: smart_sl(float(args["entry_price"]), int(args.get("direction",1)), float(args.get("atr_pips",15))),
  "Calculate SL that avoids obvious levels (round numbers).", {"entry_price":{"type":"number"},"direction":{"type":"integer","default":1},"atr_pips":{"type":"number","default":15}}, ["entry_price"])

T("antimanipulation_analyze", lambda args: analyze_manipulation(_mt5_direct, args.get("symbol","EURUSD")),
  "Detect stop-hunting and spoofing patterns.", {"symbol":{"type":"string","default":"EURUSD"}})

T("execution_twap", lambda args: twap_plan(float(args["total_volume"]), int(args.get("duration_min",5)), int(args.get("slices",10))),
  "TWAP execution plan. Splits order into equal slices over time.", {"total_volume":{"type":"number"},"duration_min":{"type":"integer","default":5},"slices":{"type":"integer","default":10}}, ["total_volume"])

T("execution_iceberg", lambda args: iceberg_plan(float(args["total_volume"]), float(args.get("display_size",0.05))),
  "Iceberg order plan. Only shows display_size volume at a time.", {"total_volume":{"type":"number"},"display_size":{"type":"number","default":0.05}}, ["total_volume"])

T("pyramiding_evaluate", lambda args: pyramiding_evaluate(float(args["entry_price"]), float(args["current_price"]), args["order_type"], float(args["volume"]), int(args.get("level",0))),
  "Evaluate if position qualifies for pyramiding (add to winners).", {"entry_price":{"type":"number"},"current_price":{"type":"number"},"order_type":{"type":"string"},"volume":{"type":"number"}}, ["entry_price","current_price","order_type","volume"])

T("risk_kelly", lambda args: kelly_size(float(args["win_rate"]), float(args["avg_win"]), float(args["avg_loss"])),
  "Kelly Criterion optimal position size.", {"win_rate":{"type":"number"},"avg_win":{"type":"number"},"avg_loss":{"type":"number"}}, ["win_rate","avg_win","avg_loss"])

T("trailing_stop", lambda args: trailing_stop(_mt5_direct, int(args.get("ticket",0)), float(args["entry"]), float(args["current"]), args["order_type"], float(args["atr"]), float(args.get("activation_pct",0.5))),
  "Calculate trailing stop price. Moves SL behind price as profit grows.", {"entry":{"type":"number"},"current":{"type":"number"},"order_type":{"type":"string"},"atr":{"type":"number"}}, ["entry","current","order_type","atr"])

T("analytics_report", lambda args: analytics_report(),
  "Performance analytics: win rate, Sharpe, Sortino, max drawdown from trade history.", {})

T("analytics_trade_record", lambda args: record_trade(args["strategy"], args["symbol"], args["direction"], float(args["entry"]), float(args["exit"]), float(args["pnl"]), float(args.get("rsi",50)), float(args.get("atr_pct",0))),
  "Record a trade for analytics. Call after every closed trade.", {"strategy":{"type":"string"},"symbol":{"type":"string"},"direction":{"type":"string"},"entry":{"type":"number"},"exit":{"type":"number"},"pnl":{"type":"number"}}, ["strategy","symbol","direction","entry","exit","pnl"])

T("autoswitch_on_result", lambda args: autoswitch_on_result(args["strategy"], bool(args["won"])),
  "Report trade result to auto-switcher. Switches strategy after 3 consecutive losses.", {"strategy":{"type":"string"},"won":{"type":"boolean"}}, ["strategy","won"])

T("autoswitch_status", lambda args: autoswitch_status(),
  "Current strategy, loss counters, rotation cycle.", {})

T("dashboard", lambda args: dashboard(_mt5_direct),
  "Consolidated system snapshot: account, sessions, news, regimes, strategies.", {})

T("multimarket_context", lambda args: multimarket_context(_mt5_direct, args.get("symbol","EURUSD")),
  "External market context: Gold, Oil, SP500 correlation with forex pair.", {"symbol":{"type":"string","default":"EURUSD"}})

T("anomaly_detect", lambda args: anomaly_detect(_mt5_direct, args.get("symbol","EURUSD")),
  "Market anomaly detection. Score >0.5 = reduce size. Score >0.7 = skip trade.", {"symbol":{"type":"string","default":"EURUSD"}})

T("evolution_record", lambda args: evolution_record(args["strategy_type"], bool(args["won"])),
  "Record trade for evolutionary competition. Strategy_type: 'current' or 'challenger'.", {"strategy_type":{"type":"string"},"won":{"type":"boolean"}}, ["strategy_type","won"])

T("ensemble_vote", lambda args: ensemble_vote(_mt5_direct, args.get("symbol","EURUSD")),
  "Run ALL strategies simultaneously. Weighted vote produces ONE final decision.", {"symbol":{"type":"string","default":"EURUSD"}})

T("edge_calculate", lambda args: edge_calculate(args.get("strategy","conviction"), args.get("symbol","EURUSD"), args.get("direction","BUY")),
  "Expected Value + Kelly from historical similar setups. Only trade if EV > 0.", {"strategy":{"type":"string"},"symbol":{"type":"string"},"direction":{"type":"string"}}, ["strategy","symbol","direction"])

# ── Market Structure Tools ──

def swing_levels(mt5_direct_fn, symbol="EURUSD", lookback=100):
    """Detect swing highs and lows. Returns key support/resistance levels."""
    try:
        raw = mt5_direct_fn({"action": "candles", "symbol": symbol, "timeframe": "H1", "count": lookback})
        candles = raw.get("candles", raw.get("data", []))
    except Exception:
        try:
            raw = mt5_direct_fn({"action": "candles", "symbol": symbol, "timeframe": "H1", "count": lookback})
            candles = raw.get("candles", [])
        except Exception:
            return {"swing_highs": [], "swing_lows": [], "error": "no data"}
    if not candles:
        return {"swing_highs": [], "swing_lows": []}
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    swing_highs = []
    swing_lows = []
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            swing_highs.append({"price": highs[i], "index": i})
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            swing_lows.append({"price": lows[i], "index": i})
    return {"swing_highs": swing_highs[-10:] if len(swing_highs) > 10 else swing_highs,
            "swing_lows": swing_lows[-10:] if len(swing_lows) > 10 else swing_lows}


def order_blocks(mt5_direct_fn, symbol="EURUSD", lookback=60):
    """Detect bullish/bearish order blocks from last 60 candles."""
    try:
        raw = mt5_direct_fn({"action": "candles", "symbol": symbol, "timeframe": "H1", "count": lookback})
        candles = raw.get("candles", raw.get("data", []))
    except Exception:
        return {"bullish_blocks": [], "bearish_blocks": []}
    if not candles:
        return {"bullish_blocks": [], "bearish_blocks": []}
    bullish = []
    bearish = []
    for i in range(1, len(candles)):
        prev, cur = candles[i-1], candles[i]
        # Bullish order block: red candle followed by green that breaks above prev high
        if prev["close"] < prev["open"] and cur["close"] > cur["open"] and cur["high"] > prev["high"]:
            bullish.append({"price_range": [prev["open"], prev["close"]], "index": i, "strength": "strong" if cur["close"] > prev["high"] + (prev["high"] - prev["low"]) * 0.5 else "weak"})
        # Bearish order block: green candle followed by red that breaks below prev low
        if prev["close"] > prev["open"] and cur["close"] < cur["open"] and cur["low"] < prev["low"]:
            bearish.append({"price_range": [prev["open"], prev["close"]], "index": i, "strength": "strong" if cur["close"] < prev["low"] - (prev["high"] - prev["low"]) * 0.5 else "weak"})
    # Reduce to readable count
    return {"bullish_blocks": bullish[-5:] if len(bullish) > 5 else bullish,
            "bearish_blocks": bearish[-5:] if len(bearish) > 5 else bearish}


def fair_value_gaps(mt5_direct_fn, symbol="EURUSD", lookback=40):
    """Detect fair value gaps (FVG) from last 40 candles."""
    try:
        raw = mt5_direct_fn({"action": "candles", "symbol": symbol, "timeframe": "H1", "count": lookback})
        candles = raw.get("candles", raw.get("data", []))
    except Exception:
        return {"gaps": []}
    gaps = []
    for i in range(2, len(candles)):
        c1, c2, c3 = candles[i-2], candles[i-1], candles[i]
        # Bullish FVG: c3 low > c1 high (gap up)
        if c3["low"] > c1["high"]:
            gaps.append({"type": "bullish", "gap_high": c3["low"], "gap_low": c1["high"],
                         "mid": round((c3["low"] + c1["high"]) / 2, 5), "index": i})
        # Bearish FVG: c3 high < c1 low (gap down)
        elif c3["high"] < c1["low"]:
            gaps.append({"type": "bearish", "gap_high": c1["low"], "gap_low": c3["high"],
                         "mid": round((c1["low"] + c3["high"]) / 2, 5), "index": i})
    return {"gaps": gaps[-5:] if len(gaps) > 5 else gaps}


def trend_structure(mt5_direct_fn, symbol="EURUSD"):
    """Multi-timeframe trend: D1 → H4 → H1 alignment."""
    try:
        d1_raw = mt5_direct_fn({"action": "candles", "symbol": symbol, "timeframe": "D1", "count": 30})
        h4_raw = mt5_direct_fn({"action": "candles", "symbol": symbol, "timeframe": "H4", "count": 30})
        h1_raw = mt5_direct_fn({"action": "candles", "symbol": symbol, "timeframe": "H1", "count": 30})
        d1 = d1_raw.get("candles", d1_raw.get("data", []))
        h4 = h4_raw.get("candles", h4_raw.get("data", []))
        h1 = h1_raw.get("candles", h1_raw.get("data", []))
    except Exception:
        return {"trend": "unknown"}
    if not d1 or not h4 or not h1:
        return {"trend": "unknown"}

    def trend_dir(candles):
        if len(candles) < 10:
            return "flat"
        ema_fast = sum(c["close"] for c in candles[-5:]) / 5
        ema_slow = sum(c["close"] for c in candles[-10:]) / 10
        higher_highs = candles[-1]["high"] > candles[-3]["high"] > candles[-5]["high"]
        higher_lows = candles[-2]["low"] > candles[-4]["low"] > candles[-6]["low"]
        if ema_fast > ema_slow and higher_highs and higher_lows:
            return "uptrend"
        elif ema_fast < ema_slow and candles[-1]["low"] < candles[-3]["low"] < candles[-5]["low"]:
            return "downtrend"
        return "ranging"

    d1_trend = trend_dir(d1)
    h4_trend = trend_dir(h4)
    h1_trend = trend_dir(h1)
    aligned = d1_trend == h4_trend == h1_trend and d1_trend in ("uptrend", "downtrend")
    return {"d1": d1_trend, "h4": h4_trend, "h1": h1_trend,
            "aligned": aligned, "bias": d1_trend if aligned else "misaligned"}


T("market_swing_levels", lambda args: swing_levels(_mt5_direct, args.get("symbol", "EURUSD"), int(args.get("lookback", 100))),
  "Swing highs/lows for support/resistance.", {"symbol": {"type": "string", "default": "EURUSD"}, "lookback": {"type": "integer", "default": 100}})

T("market_order_blocks", lambda args: order_blocks(_mt5_direct, args.get("symbol", "EURUSD"), int(args.get("lookback", 60))),
  "Bullish/bearish order blocks. Price tends to react at these zones.", {"symbol": {"type": "string", "default": "EURUSD"}, "lookback": {"type": "integer", "default": 60}})

T("market_fair_value_gaps", lambda args: fair_value_gaps(_mt5_direct, args.get("symbol", "EURUSD"), int(args.get("lookback", 40))),
  "Fair value gaps (FVG). Price often retraces to fill these.", {"symbol": {"type": "string", "default": "EURUSD"}, "lookback": {"type": "integer", "default": 40}})

T("market_trend_structure", lambda args: trend_structure(_mt5_direct, args.get("symbol", "EURUSD")),
  "Multi-timeframe trend: D1 → H4 → H1 alignment. Only trade when aligned.", {"symbol": {"type": "string", "default": "EURUSD"}})

# ── Backtesting Engine ──

def backtest(mt5_direct_fn, symbol="EURUSD", timeframe="H1", strategy="ma_cross",
             start_idx=0, end_idx=0, fast_ma=5, slow_ma=20, rsi_period=14,
             rsi_overbought=70, rsi_oversold=30, sl_atr=1.5, tp_atr=3.0):
    """Simple backtesting engine. Simulates a strategy on historical data."""
    count = 200 if end_idx <= 0 else end_idx - start_idx
    raw = mt5_direct_fn({"action": "candles", "symbol": symbol, "timeframe": timeframe, "count": count + 100})
    candles = raw.get("candles", raw.get("data", []))
    if not candles or len(candles) < slow_ma + 10:
        return {"error": "insufficient data", "candles": len(candles)}
    
    if end_idx > 0 and end_idx <= len(candles):
        candles = candles[start_idx:end_idx]
    elif start_idx > 0:
        candles = candles[start_idx:]
    
    trades = []
    balance = 1000.0
    position = None
    closes = [c["close"] for c in candles]
    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    times = [c.get("time", i) for i, c in enumerate(candles)]
    
    # Precompute indicators
    def ema(data, period):
        if len(data) < period:
            return [None] * len(data)
        result = []
        mult = 2 / (period + 1)
        ema_val = sum(data[:period]) / period
        for i, val in enumerate(data):
            if i < period - 1:
                result.append(None)
            elif i == period - 1:
                result.append(ema_val)
            else:
                ema_val = (val - ema_val) * mult + ema_val
                result.append(ema_val)
        return result
    
    def rsi_vals(data, period):
        if len(data) < period + 1:
            return [None] * len(data)
        result = [None] * period
        gains, losses = 0, 0
        for i in range(1, period + 1):
            diff = data[i] - data[i-1]
            gains += max(diff, 0)
            losses += max(-diff, 0)
        avg_gain = gains / period
        avg_loss = losses / period
        for i in range(period, len(data)):
            diff = data[i] - data[i-1]
            avg_gain = (avg_gain * (period - 1) + max(diff, 0)) / period
            avg_loss = (avg_loss * (period - 1) + max(-diff, 0)) / period
            rs = avg_gain / avg_loss if avg_loss > 0 else 100
            result.append(100 - 100 / (1 + rs))
        return result
    
    # ATR
    def atr_vals(candles, period=14):
        if len(candles) < period + 1:
            return [None] * len(candles)
        trs = []
        for i in range(1, len(candles)):
            hl = candles[i]["high"] - candles[i]["low"]
            hc = abs(candles[i]["high"] - candles[i-1]["close"])
            lc = abs(candles[i]["low"] - candles[i-1]["close"])
            trs.append(max(hl, hc, lc))
        result = [None] * (period)
        atr_val = sum(trs[:period]) / period
        for i in range(period, len(trs)):
            result.append(atr_val)
            atr_val = (atr_val * (period - 1) + trs[i]) / period
        result.append(atr_val)
        while len(result) < len(candles):
            result.append(atr_val)
        return result[-len(candles):] if len(result) >= len(candles) else [None]*len(candles)
    
    fast_ema = ema(closes, fast_ma)
    slow_ema = ema(closes, slow_ma)
    rsi_arr = rsi_vals(closes, rsi_period)
    atr_arr = atr_vals(candles, 14)
    
    for i in range(max(slow_ma, rsi_period, 14), len(candles)):
        if strategy == "ma_cross":
            if fast_ema[i] is not None and slow_ema[i] is not None:
                prev_fast = fast_ema[i-1] if i > 0 else fast_ema[i]
                prev_slow = slow_ema[i-1] if i > 0 else slow_ema[i]
                is_buy = prev_fast <= prev_slow and fast_ema[i] > slow_ema[i]
                is_sell = prev_fast >= prev_slow and fast_ema[i] < slow_ema[i]
                if is_buy:
                    position = {"type": "BUY", "entry": closes[i], "index": i, "time": times[i]}
                elif is_sell:
                    position = {"type": "SELL", "entry": closes[i], "index": i, "time": times[i]}
        
        elif strategy == "rsi_mean_reversion":
            if rsi_arr[i] is not None:
                if rsi_arr[i] < rsi_oversold and position is None:
                    position = {"type": "BUY", "entry": closes[i], "index": i, "time": times[i]}
                elif rsi_arr[i] > rsi_overbought and position is None:
                    position = {"type": "SELL", "entry": closes[i], "index": i, "time": times[i]}
        
        elif strategy == "trend_follow":
            if fast_ema[i] is not None and rsi_arr[i] is not None:
                if fast_ema[i] > closes[i] and rsi_arr[i] < 50 and position is None:
                    position = {"type": "BUY", "entry": closes[i], "index": i, "time": times[i]}
                elif fast_ema[i] < closes[i] and rsi_arr[i] > 50 and position is None:
                    position = {"type": "SELL", "entry": closes[i], "index": i, "time": times[i]}
        
        if position is not None:
            atr_val = atr_arr[i] if atr_arr[i] is not None else 0.001
            sl_dist = atr_val * sl_atr
            tp_dist = atr_val * tp_atr
            entry = position["entry"]
            exit_price = None
            exit_reason = None
            
            for j in range(i + 1, min(i + 100, len(candles))):
                if position["type"] == "BUY":
                    if lows[j] <= entry - sl_dist:
                        exit_price = entry - sl_dist
                        exit_reason = "stop_loss"
                        break
                    if highs[j] >= entry + tp_dist:
                        exit_price = entry + tp_dist
                        exit_reason = "take_profit"
                        break
                else:
                    if highs[j] >= entry + sl_dist:
                        exit_price = entry + sl_dist
                        exit_reason = "stop_loss"
                        break
                    if lows[j] <= entry - tp_dist:
                        exit_price = entry - tp_dist
                        exit_reason = "take_profit"
                        break
            
            if exit_price is None and i + 100 < len(candles):
                exit_price = closes[min(i + 100, len(candles) - 1)]
                exit_reason = "time_exit"
            
            if exit_price is not None:
                pnl_pct = (exit_price - entry) / entry if position["type"] == "BUY" else (entry - exit_price) / entry
                pnl = balance * pnl_pct * 10  # assume 10x leverage for directional bet
                balance += pnl
                trades.append({
                    "entry_time": position.get("time", i),
                    "exit_time": times[min(j, len(candles)-1)] if exit_price else times[-1],
                    "type": position["type"],
                    "entry": round(entry, 5),
                    "exit": round(exit_price, 5),
                    "pnl": round(pnl, 2),
                    "pnl_pct": round(pnl_pct * 100, 2),
                    "reason": exit_reason,
                    "rsi_entry": round(rsi_arr[i], 1) if rsi_arr[i] is not None else 0,
                    "atr_entry": round(atr_val, 5),
                })
                position = None
    
    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    total_pnl = sum(t["pnl"] for t in trades)
    win_rate = len(wins) / len(trades) if trades else 0
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = abs(sum(t["pnl"] for t in losses) / len(losses)) if losses else 0
    profit_factor = sum(t["pnl"] for t in wins) / abs(sum(t["pnl"] for t in losses)) if losses and sum(t["pnl"] for t in losses) != 0 else float('inf') if wins else 0
    
    # Sharpe ratio (simplified)
    returns = [t["pnl_pct"] for t in trades]
    avg_return = sum(returns) / len(returns) if returns else 0
    std_return = (sum((r - avg_return)**2 for r in returns) / len(returns))**0.5 if len(returns) > 1 else 1
    sharpe = avg_return / std_return * (252**0.5) if std_return > 0 else 0
    
    # Max drawdown
    peak = 1000
    dd = 0
    for t in trades:
        peak = max(peak, 1000 + t["pnl"])
        dd = max(dd, peak - (1000 + sum(t2["pnl"] for t2 in trades[:trades.index(t)+1])))
    max_dd_pct = dd / peak * 100 if peak > 0 else 0
    
    return {
        "symbol": symbol, "timeframe": timeframe, "strategy": strategy,
        "total_trades": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate_pct": round(win_rate * 100, 1),
        "total_pnl": round(total_pnl, 2),
        "final_balance": round(balance, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else "inf",
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown_pct": round(max_dd_pct, 1),
        "best_trade": max(trades, key=lambda x: x["pnl"])["pnl"] if trades else 0,
        "worst_trade": min(trades, key=lambda x: x["pnl"])["pnl"] if trades else 0,
        "expectancy": round(total_pnl / len(trades), 2) if trades else 0,
    }


# ── Multi-Timeframe Combo Analysis ──

def multi_timeframe_combo(mt5_direct_fn, symbol="EURUSD", timeframes=None):
    """Evaluate conviction across M5, M15, H1, H4, D1. Returns alignment score 0-100."""
    if timeframes is None:
        timeframes = ["M5", "M15", "H1", "H4", "D1"]
    results = {}
    weights = {"M5": 0.10, "M15": 0.15, "H1": 0.25, "H4": 0.25, "D1": 0.25}
    
    for tf in timeframes:
        try:
            raw = mt5_direct_fn({"action": "candles", "symbol": symbol, "timeframe": tf, "count": 100})
            candles = raw.get("candles", raw.get("data", []))
            if not candles or len(candles) < 20:
                results[tf] = {"verdict": "unknown", "confidence": 0}
                continue
            closes = [c["close"] for c in candles]
            highs = [c["high"] for c in candles]
            lows = [c["low"] for c in candles]
            
            # RSI
            gains, losses = 0, 0
            for i in range(1, 15):
                diff = closes[-i] - closes[-i-1]
                gains += max(diff, 0)
                losses += max(-diff, 0)
            rsi = 50
            if losses > 0:
                rs = gains / losses
                rsi = 100 - 100 / (1 + rs)
            
            # EMA trend
            fast = sum(closes[-5:]) / 5
            slow = sum(closes[-10:]) / 10
            ema_trend = "up" if fast > slow else "down"
            
            # Higher highs/lows
            hh = highs[-1] > highs[-3] and highs[-3] > highs[-5]
            hl = lows[-1] > lows[-3] and lows[-3] > lows[-5]
            lh = highs[-1] < highs[-3] and highs[-3] < highs[-5]
            ll = lows[-1] < lows[-3] and lows[-3] < lows[-5]
            
            if hh and hl and ema_trend == "up":
                verdict, conf = "BUY", min(90, 50 + rsi)
            elif lh and ll and ema_trend == "down":
                verdict, conf = "SELL", min(90, 50 + (100 - rsi))
            elif ema_trend == "up" and rsi > 50:
                verdict, conf = "BUY", min(70, rsi)
            elif ema_trend == "down" and rsi < 50:
                verdict, conf = "SELL", min(70, 100 - rsi)
            else:
                verdict, conf = "PASS", 0
            
            results[tf] = {
                "verdict": verdict, "confidence": conf,
                "rsi": round(rsi, 1), "trend": ema_trend,
            }
        except Exception as e:
            results[tf] = {"verdict": "error", "confidence": 0, "error": str(e)}
    
    # Weighted vote
    buy_conf = sum(weights.get(tf, 0) * r.get("confidence", 0) 
                   for tf, r in results.items() if r.get("verdict") == "BUY")
    sell_conf = sum(weights.get(tf, 0) * r.get("confidence", 0)
                    for tf, r in results.items() if r.get("verdict") == "SELL")
    
    total_weight = sum(weights.get(tf, 0) for tf in timeframes if tf in results)
    if total_weight > 0:
        buy_conf /= total_weight
        sell_conf /= total_weight
    
    if buy_conf > sell_conf and buy_conf > 30:
        final = "BUY"
        alignment = round(buy_conf)
    elif sell_conf > buy_conf and sell_conf > 30:
        final = "SELL"
        alignment = round(sell_conf)
    else:
        final = "PASS"
        alignment = 0
    
    aligned_tfs = sum(1 for r in results.values() if r.get("verdict") == final)
    
    return {
        "symbol": symbol, "final_verdict": final, "alignment_pct": alignment,
        "aligned_timeframes": f"{aligned_tfs}/{len(timeframes)}",
        "buy_confidence": round(buy_conf, 1), "sell_confidence": round(sell_conf, 1),
        "timeframe_analysis": results,
    }


# ── ML Predictor (pattern-based) ──

ML_DATA_FILE = os.path.join(DATA_DIR, "ml_patterns.json")

def _load_ml_data():
    if not os.path.exists(ML_DATA_FILE):
        return {"patterns": [], "version": 1}
    try:
        return json.load(open(ML_DATA_FILE))
    except:
        return {"patterns": [], "version": 1}

def _save_ml_data(data):
    os.makedirs(os.path.dirname(ML_DATA_FILE), exist_ok=True)
    with open(ML_DATA_FILE, "w") as f:
        json.dump(data, f, indent=2)

def ml_train(mt5_direct_fn, symbol="EURUSD", timeframe="H1", lookback=500, min_patterns=20):
    """Extract patterns from historical data and train the ML model.
    Pattern = last N candle bodies + direction of next candle."""
    raw = mt5_direct_fn({"action": "candles", "symbol": symbol, "timeframe": timeframe, "count": lookback})
    candles = raw.get("candles", raw.get("data", []))
    if not candles or len(candles) < 30:
        return {"error": "insufficient data", "trained": False}
    
    data = _load_ml_data()
    new_count = 0
    pattern_len = 5
    
    for i in range(pattern_len, len(candles) - 1):
        pattern = []
        for j in range(pattern_len):
            c = candles[i - pattern_len + j]
            body = abs(c["close"] - c["open"])
            total_range = c["high"] - c["low"]
            body_pct = round(body / total_range * 100, 1) if total_range > 0 else 0
            direction = 1 if c["close"] > c["open"] else 0
            pattern.append({"body_pct": body_pct, "direction": direction,
                            "volume_ratio": round(c.get("tick_volume", c.get("volume", 0)) / 1000, 1)})
        
        next_dir = 1 if candles[i+1]["close"] > candles[i+1]["open"] else 0
        next_move_pct = round((candles[i+1]["close"] - candles[i+1]["open"]) / candles[i+1]["open"] * 100, 3)
        
        data["patterns"].append({
            "symbol": symbol, "timeframe": timeframe,
            "pattern": pattern, "next_direction": next_dir,
            "next_move_pct": next_move_pct,
            "timestamp": candles[i].get("time", ""),
        })
        new_count += 1
    
    # Keep only last 2000 per symbol/timeframe
    key = f"{symbol}_{timeframe}"
    all_p = [p for p in data["patterns"] if p.get("symbol") == symbol and p.get("timeframe") == timeframe]
    if len(all_p) > 2000:
        excess = len(all_p) - 2000
        data["patterns"] = [p for p in data["patterns"] if not (p.get("symbol") == symbol and p.get("timeframe") == timeframe)] + all_p[excess:]
    
    _save_ml_data(data)
    
    key_patterns = [p for p in data["patterns"] if p.get("symbol") == symbol and p.get("timeframe") == timeframe]
    return {
        "trained": True,
        "symbol": symbol, "timeframe": timeframe,
        "new_patterns": new_count,
        "total_patterns": len(key_patterns),
        "total_db": len(data["patterns"]),
    }


def ml_predict(mt5_direct_fn, symbol="EURUSD", timeframe="H1"):
    """Predict next candle direction using pattern matching against historical data."""
    # Get current pattern
    raw = mt5_direct_fn({"action": "candles", "symbol": symbol, "timeframe": timeframe, "count": 10})
    candles = raw.get("candles", raw.get("data", []))
    if not candles or len(candles) < 6:
        return {"error": "insufficient current data"}
    
    current = []
    pattern_len = 5
    for j in range(pattern_len):
        c = candles[-pattern_len + j]
        body = abs(c["close"] - c["open"])
        total_range = c["high"] - c["low"]
        body_pct = round(body / total_range * 100, 1) if total_range > 0 else 0
        direction = 1 if c["close"] > c["open"] else 0
        current.append({"body_pct": body_pct, "direction": direction})
    
    # Load historical data
    data = _load_ml_data()
    patterns = [p for p in data["patterns"] if p.get("symbol") == symbol and p.get("timeframe") == timeframe]
    
    if len(patterns) < 5:
        return {
            "symbol": symbol, "timeframe": timeframe,
            "prediction": "insufficient_data",
            "confidence": 0,
            "patterns_available": len(patterns),
            "note": "Train with ml_train first. Need at least 5 patterns.",
        }
    
    # Match: find most similar patterns using simple euclidean distance
    scored = []
    for p in patterns[-500:]:  # limit to last 500 for speed
        hist_pat = p["pattern"]
        if len(hist_pat) != len(current):
            continue
        distance = 0
        for a, b in zip(current, hist_pat):
            distance += (a["body_pct"] - b["body_pct"])**2 + (a["direction"] - b["direction"])**2 * 100
        scored.append((distance, p["next_direction"], p.get("next_move_pct", 0)))
    
    if not scored:
        return {"prediction": "no_match", "confidence": 0}
    
    scored.sort(key=lambda x: x[0])
    top_n = min(20, len(scored))
    top_matches = scored[:top_n]
    
    buys = sum(1 for _, d, _ in top_matches if d == 1)
    sells = top_n - buys
    buy_pct = buys / top_n * 100
    avg_move = sum(abs(pct) for _, _, pct in top_matches) / top_n
    
    if buy_pct > 60:
        prediction = "BUY"
        confidence = round(buy_pct)
    elif buy_pct < 40:
        prediction = "SELL"
        confidence = round(100 - buy_pct)
    else:
        prediction = "PASS"
        confidence = round(abs(buy_pct - 50) * 2)  # how far from 50/50
    
    similarity = round(1 / (1 + scored[0][0]) * 100, 1) if scored[0][0] > 0 else 99
    
    return {
        "symbol": symbol, "timeframe": timeframe,
        "prediction": prediction,
        "confidence": min(confidence, 99),
        "patterns_matched": top_n,
        "avg_move_pct": round(avg_move, 3),
        "bullish_ratio": round(buy_pct, 1),
        "similarity_pct": similarity,
        "total_patterns_db": len(patterns),
    }


# ── Smart Money Map: broker + order flow + manipulation ──

def smart_money_map(mt5_direct_fn, symbol="EURUSD"):
    """Analiza 4 capas: broker manipulation, smart money accumulator, order flow, stops clustering."""
    result = {"symbol": symbol, "timestamp": datetime.now(timezone.utc).isoformat()}
    sym = _fix_sym_local(symbol)
    
    # Fetch data
    try:
        price_raw = mt5_direct_fn({"action": "price", "symbol": sym})
        m1_raw = mt5_direct_fn({"action": "candles", "symbol": sym, "timeframe": "M1", "count": 300})
        h1_raw = mt5_direct_fn({"action": "candles", "symbol": sym, "timeframe": "H1", "count": 100})
    except Exception as e:
        return {"error": str(e)}
    
    bid = price_raw.get("bid", 0)
    ask = price_raw.get("ask", 0)
    spread = price_raw.get("spread", 99)
    m1 = m1_raw.get("candles", m1_raw.get("data", []))
    h1 = h1_raw.get("candles", h1_raw.get("data", []))
    
    if not m1 or not h1:
        return {"error": "insufficient data"}
    
    # ── Layer 1: Broker Manipulation Detection ──
    spreads = [c.get("spread", spread) for c in m1[-100:]]
    avg_spread = sum(spreads) / len(spreads)
    max_spread = max(spreads)
    recent_spreads = spreads[-20:]
    spread_volatility = (max(spreads) - min(spreads)) / (avg_spread or 1)
    
    # Spread widening events
    widenings = sum(1 for i in range(1, len(recent_spreads)) if recent_spreads[i] > recent_spreads[i-1] * 1.5)
    
    broker_flags = []
    if max_spread > avg_spread * 2:
        broker_flags.append("spread_spikes_detected")
    if widenings >= 3:
        broker_flags.append("frequent_widening")
    if spread > avg_spread * 1.3:
        broker_flags.append("currently_wide")
    
    # Stop-hunt detection: price spikes to obvious levels then reverses
    hunt_score = 0
    recent_highs = [c["high"] for c in m1[-30:]]
    recent_lows = [c["low"] for c in m1[-30:]]
    max_h = max(recent_highs)
    min_l = min(recent_lows)
    round_up = round(bid + 0.001, 3) if "JPY" not in symbol else round(bid + 0.1, 1)
    round_down = round(bid - 0.001, 3) if "JPY" not in symbol else round(bid - 0.1, 1)
    
    # Check if price hit a round number and reversed
    for c in m1[-20:]:
        if abs(c["high"] - round_up) / (round_up or 1) < 0.0003:
            hunt_score += 1
        if abs(c["low"] - round_down) / (round_down or 1) < 0.0003:
            hunt_score += 1
        # Wick detection: long wick into obvious level
        body = abs(c["close"] - c["open"])
        upper_wick = c["high"] - max(c["open"], c["close"])
        lower_wick = min(c["open"], c["close"]) - c["low"]
        if upper_wick > body * 2 and upper_wick > (c["high"] - c["low"]) * 0.6:
            hunt_score += 1
        if lower_wick > body * 2 and lower_wick > (c["high"] - c["low"]) * 0.6:
            hunt_score += 1
    
    broker_trust = max(0, min(100, 100 - spread_volatility * 20 - widenings * 5 - hunt_score * 2))
    broker_assessment = "reliable" if broker_trust > 70 else "suspicious" if broker_trust > 40 else "unreliable"
    result["broker"] = {
        "trust_score": round(broker_trust),
        "assessment": broker_assessment,
        "avg_spread": round(avg_spread, 1),
        "max_spread": max_spread,
        "spread_volatility": round(spread_volatility, 2),
        "widenings_20": widenings,
        "hunt_score": hunt_score,
        "flags": broker_flags,
        "original_spread": spread,
    }
    
    # ── Layer 2: Smart Money Accumulation/Distribution ──
    closes = [c["close"] for c in m1]
    volumes = [c.get("tick_volume", c.get("volume", 0)) for c in m1]
    
    # Volume profile of last 100 candles
    last_100 = m1[-100:] if len(m1) >= 100 else m1
    price_bins = {}
    for c in last_100:
        price_key = round(c["close"], 4) if "JPY" not in symbol else round(c["close"], 2)
        vol = c.get("tick_volume", c.get("volume", 0))
        price_bins[price_key] = price_bins.get(price_key, 0) + vol
    
    # POC (Point of Control)
    poc_price = max(price_bins, key=price_bins.get) if price_bins else bid
    poc_vol = price_bins.get(poc_price, 0)
    
    # Accumulation: price moving sideways with increasing volume on dips
    total_vol_50 = sum(volumes[-50:]) if len(volumes) >= 50 else sum(volumes)
    total_vol_prev = sum(volumes[-100:-50]) if len(volumes) >= 100 else sum(volumes)
    vol_trend = "increasing" if total_vol_50 > total_vol_prev * 1.1 else "decreasing" if total_vol_50 < total_vol_prev * 0.9 else "stable"
    
    # Price action characterization
    range_100 = (max(c["high"] for c in last_100) - min(c["low"] for c in last_100)) / (bid or 1) * 100
    last_10_closes = [c["close"] for c in m1[-10:]]
    price_drift = (last_10_closes[-1] - last_10_closes[0]) / (last_10_closes[0] or 1) * 100
    tight_range = range_100 < 0.3
    
    # Determine accumulation/distribution
    sm_action = "neutral"
    sm_confidence = 0
    sm_zone = None
    
    if tight_range and vol_trend == "increasing" and abs(price_drift) < 0.1:
        sm_action = "accumulating"
        sm_confidence = 70
        sm_zone = {"start": round(min(c["low"] for c in last_100), 5), "end": round(max(c["high"] for c in last_100), 5)}
    elif tight_range and vol_trend == "decreasing" and abs(price_drift) < 0.05:
        sm_action = "distributing"
        sm_confidence = 60
        sm_zone = {"start": round(min(c["low"] for c in last_100), 5), "end": round(max(c["high"] for c in last_100), 5)}
    elif price_drift > 0.3 and vol_trend == "increasing":
        sm_action = "institutional_buying"
        sm_confidence = 65
    elif price_drift < -0.3 and vol_trend == "increasing":
        sm_action = "institutional_selling"
        sm_confidence = 65
    
    # Absorption: price doesn't drop on selling volume (bullish)
    sell_clusters = 0
    buy_clusters = 0
    for i in range(10, len(m1[-50:])):
        c = m1[-50 + i]
        prev = m1[-51 + i] if i > 0 else c
        if c["close"] < c["open"] and c["close"] > prev["close"] and c.get("tick_volume", 0) > sum(v.get("tick_volume", 0) for v in m1[-55:-50]) / 5:
            sell_clusters += 1  # sold off but held above prev close = absorption
        if c["close"] > c["open"] and c["close"] < prev["close"] and c.get("tick_volume", 0) > sum(v.get("tick_volume", 0) for v in m1[-55:-50]) / 5:
            buy_clusters += 1  # bought but couldn't break above = distribution
    
    absorption_ratio = sell_clusters / (buy_clusters + 1)
    
    result["smart_money"] = {
        "action": sm_action,
        "confidence": sm_confidence,
        "accumulation_zone": sm_zone,
        "poc_price": round(poc_price, 5),
        "poc_volume": poc_vol,
        "volume_trend": vol_trend,
        "range_pct_100": round(range_100, 3),
        "absorption_ratio": round(absorption_ratio, 2),
        "vol_50": int(total_vol_50),
        "vol_prev_50": int(total_vol_prev),
    }
    
    # ── Layer 3: Order Flow Reconstruction ──
    
    # Tick velocity: acceleration/deceleration in price movement
    if len(m1) >= 20:
        recent_m = m1[-20:]
        velocities = []
        for i in range(1, len(recent_m)):
            dt = recent_m[i]["time"] - recent_m[i-1]["time"]
            dp = abs(recent_m[i]["close"] - recent_m[i-1]["close"])
            velocities.append(dp / max(dt, 1))
        
        if velocities:
            avg_v = sum(velocities) / len(velocities)
            recent_v = velocities[-5:]
            avg_recent = sum(recent_v) / len(recent_v)
            acceleration = (avg_recent - avg_v) / (avg_v or 1) * 100
        else:
            acceleration = 0
    else:
        acceleration = 0
    
    # Directional pressure
    up_volume = sum(c.get("tick_volume", 0) for c in m1[-10:] if c["close"] > c["open"])
    dn_volume = sum(c.get("tick_volume", 0) for c in m1[-10:] if c["close"] <= c["open"])
    total_v = up_volume + dn_volume
    pressure_ratio = up_volume / (dn_volume or 1)
    pressure = "bullish" if pressure_ratio > 1.3 else "bearish" if pressure_ratio < 0.7 else "balanced"
    
    # Momentum decay
    body_sizes = [abs(c["close"] - c["open"]) for c in m1[-10:]]
    body_trend = "increasing" if len(body_sizes) >= 3 and sum(body_sizes[-3:]) > sum(body_sizes[-6:-3]) else "decreasing" if len(body_sizes) >= 3 and sum(body_sizes[-3:]) < sum(body_sizes[-6:-3]) * 0.7 else "stable"
    
    result["order_flow"] = {
        "acceleration_pct": round(acceleration, 1),
        "directional_pressure": pressure,
        "pressure_ratio": round(pressure_ratio, 2),
        "up_volume_10": int(up_volume),
        "down_volume_10": int(dn_volume),
        "body_trend_10": body_trend,
    }
    
    # ── Layer 4: Retail Stop Clusters ──
    
    stops = []
    pip = 0.01 if "JPY" in symbol else 0.0001
    
    # Round numbers attract stops
    for mult in range(-20, 21):
        level = round(bid + mult * pip, 2 if "JPY" in symbol else 4)
        if level <= 0:
            continue
        # Distance from current price
        dist = abs(level - bid) / (pip or 1)
        if dist > 50:
            continue
        # Popular stop levels: just above round numbers for shorts, below for longs
        is_round = abs(level * 100 - round(level * 100)) < 0.01
        if is_round:
            stops.append({"price": round(level, 5), "type": "round_number", "distance_pips": round(dist, 1), "cluster_density": "high"})
    
    # Previous swing highs/lows attract stops
    swing_highs = []
    swing_lows = []
    for i in range(2, len(m1) - 2):
        if m1[i]["high"] > m1[i-1]["high"] > m1[i-2]["high"] and m1[i]["high"] > m1[i+1]["high"] > m1[i+2]["high"]:
            swing_highs.append(m1[i]["high"])
        if m1[i]["low"] < m1[i-1]["low"] < m1[i-2]["low"] and m1[i]["low"] < m1[i+1]["low"] < m1[i+2]["low"]:
            swing_lows.append(m1[i]["low"])
    
    for sh in swing_highs[-5:]:
        dist = abs(sh - bid) / (pip or 1)
        if dist < 30:
            stops.append({"price": round(sh, 5), "type": "swing_high", "distance_pips": round(dist, 1), "cluster_density": "medium"})
    for sl in swing_lows[-5:]:
        dist = abs(sl - bid) / (pip or 1)
        if dist < 30:
            stops.append({"price": round(sl, 5), "type": "swing_low", "distance_pips": round(dist, 1), "cluster_density": "medium"})
    
    # Stop-hunting recommendation: place SL BEYOND obvious zones
    near_stops_above = [s for s in stops if s["price"] > bid and s["distance_pips"] < 15]
    near_stops_below = [s for s in stops if s["price"] < bid and s["distance_pips"] < 15]
    safest_sl_long = min([s["price"] for s in near_stops_below], default=bid - 3*pip) if near_stops_below else bid - 3*pip
    safest_sl_short = max([s["price"] for s in near_stops_above], default=bid + 3*pip) if near_stops_above else bid + 3*pip
    
    result["retail_stops"] = {
        "total_clusters": len(stops),
        "stops_above_pips": len(near_stops_above),
        "stops_below_pips": len(near_stops_below),
        "safest_sl_long": round(safest_sl_long, 5),
        "safest_sl_short": round(safest_sl_short, 5),
        "clusters": sorted(stops, key=lambda x: x["distance_pips"])[:10],
    }
    
    # ── VERDICT ──
    verdict_parts = []
    
    if broker_trust < 50:
        verdict_parts.append(f"BROKER: {broker_assessment.upper()} (trust={broker_trust})")
    if sm_confidence > 50:
        verdict_parts.append(f"SMART_MONEY: {sm_action.upper()} (conf={sm_confidence})")
    if acceleration > 20:
        verdict_parts.append(f"ACCELERATING {pressure.upper()}")
    if near_stops_above and near_stops_below:
        verdict_parts.append(f"STOPS: {len(near_stops_below)} below / {len(near_stops_above)} above")
    
    # Final recommendation
    rec = None
    if sm_action in ("accumulating", "institutional_buying") and broker_trust > 40 and pressure == "bullish":
        rec = {"action": "BUY", "confidence": min(sm_confidence + 10, 90),
               "sl": round(safest_sl_long - pip, 5), "reason": "smart_money_accumulating + bullish_pressure"}
    elif sm_action in ("distributing", "institutional_selling") and broker_trust > 40 and pressure == "bearish":
        rec = {"action": "SELL", "confidence": min(sm_confidence + 10, 90),
               "sl": round(safest_sl_short + pip, 5), "reason": "smart_money_distributing + bearish_pressure"}
    elif broker_trust < 40:
        rec = {"action": "SKIP", "confidence": 30, "reason": f"broker_unreliable (trust={broker_trust})"}
    elif near_stops_below and len(m1[-5:]) >= 3 and all(c["close"] < c["open"] for c in m1[-3:]):
        rec = {"action": "BUY", "confidence": 55, "sl": round(safest_sl_long, 5),
               "reason": "stops_below + retail_capitulation"}
    
    result["verdict"] = " | ".join(verdict_parts) if verdict_parts else "neutral_signals"
    result["recommendation"] = rec
    
    return result


# ── Market Brain: Cerebro de Mercado ──

_BRAIN_CACHE = {"broker_profile": {}, "multi_pair": {}}

def market_brain(mt5_direct_fn, symbol="EURUSD"):
    """Sistema de visión total. 5 capas: liquidez, huella institucional, flujo multi-par, broker, predicción."""
    result = {"symbol": symbol, "timestamp": datetime.now(timezone.utc).isoformat()}
    sym = _fix_sym_local(symbol)
    global _BRAIN_CACHE
    
    try:
        price_raw = mt5_direct_fn({"action": "price", "symbol": sym})
        m1_raw = mt5_direct_fn({"action": "candles", "symbol": sym, "timeframe": "M1", "count": 500})
        h1_raw = mt5_direct_fn({"action": "candles", "symbol": sym, "timeframe": "H1", "count": 100})
        pos_raw = mt5_direct_fn({"action": "positions", "symbol": sym})
    except Exception as e:
        return {"error": str(e)}
    
    bid = price_raw.get("bid", 0)
    ask = price_raw.get("ask", 0)
    spread = price_raw.get("spread", 99)
    m1 = m1_raw.get("candles", m1_raw.get("data", []))
    h1 = h1_raw.get("candles", h1_raw.get("data", []))
    positions = pos_raw.get("positions", [])
    
    if not m1 or not h1:
        return {"error": "insufficient data"}
    
    closes = [c["close"] for c in m1]
    highs = [c["high"] for c in m1]
    lows = [c["low"] for c in m1]
    volumes = [c.get("tick_volume", c.get("volume", 0)) for c in m1]
    spreads = [c.get("spread", spread) for c in m1]
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LAYER 1: LIQUIDITY MAP
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    pip = 0.01 if "JPY" in symbol else 0.0001
    
    # Build liquidity clusters from round numbers + swing points
    liquidity = {"bid": [], "ask": [], "hot": [], "next_target": None}
    
    # Round number liquidity
    price_range = max(highs[-200:]) - min(lows[-200:]) if len(highs) >= 200 else 0.02
    step = pip * 5  # 5-pip steps
    min_p = max(0, bid - price_range * 0.5)
    max_p = bid + price_range * 0.5
    
    level = round(min_p / step) * step
    while level <= max_p:
        # Count touches and volume near this level
        touches = sum(1 for c in m1[-100:] if abs(c["high"] - level) < pip or abs(c["low"] - level) < pip)
        vol_near = sum(c.get("tick_volume", 0) for c in m1[-100:] if abs(c["high"] - level) < pip*2 or abs(c["low"] - level) < pip*2)
        
        if touches > 2 or vol_near > sum(volumes[-100:]) / 20:
            side = "ask" if level > bid else "bid"
            density = "high" if touches > 8 else "medium" if touches > 4 else "low"
            liquidity[side].append({
                "price": round(level, 5), "touches": touches,
                "volume_near": int(vol_near), "density": density,
            })
        level += step
    
    # Identify the hottest level (most volume + touches)
    for l in liquidity["bid"] + liquidity["ask"]:
        if l["touches"] > 5 and l["volume_near"] > sum(volumes[-100:]) / 15:
            liquidity["hot"].append(l)
    
    # Predict next hunted level (closest dense liquidity opposite current pressure)
    recent_mom = (closes[-1] - closes[-5]) / (closes[-5] or 1)
    if recent_mom > 0:
        below_bid = [l for l in liquidity["bid"] if l["price"] < bid]
        next_hunt = max(below_bid, key=lambda x: x["volume_near"]) if below_bid else None
        liquidity["next_target"] = {
            "direction": "down", "price": round(next_hunt["price"], 5),
            "reason": f"{next_hunt['touches']} touches, {next_hunt['volume_near']} volume below"
        } if next_hunt else {"direction": "down", "price": round(bid - 3*pip, 5), "reason": "no_dense_level"}
    else:
        above_ask = [l for l in liquidity["ask"] if l["price"] > bid]
        next_hunt = min(above_ask, key=lambda x: x["price"]) if above_ask else None
        liquidity["next_target"] = {
            "direction": "up", "price": round(next_hunt["price"], 5),
            "reason": f"{next_hunt['touches']} touches, {next_hunt['volume_near']} volume above"
        } if next_hunt else {"direction": "up", "price": round(bid + 3*pip, 5), "reason": "no_dense_level"}
    
    liquidity["dense_levels"] = sorted(liquidity["hot"], key=lambda x: x["volume_near"], reverse=True)[:5]
    result["liquidity_map"] = {
        "bid_levels": liquidity["bid"][-5:] if liquidity["bid"] else [],
        "ask_levels": liquidity["ask"][:5] if liquidity["ask"] else [],
        "hottest_levels": liquidity["dense_levels"],
        "next_target": liquidity["next_target"],
    }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LAYER 2: INSTITUTIONAL FOOTPRINT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    inst = {"icebergs": [], "absorption": 0, "smart_money_bias": "neutral", "confidence": 0}
    
    # Detect iceberg orders: large volume + narrow spread + price not moving = accumulation
    vol_chunks = []
    for i in range(0, len(volumes), 10):
        chunk = sum(volumes[i:i+10])
        vol_chunks.append(chunk)
    
    avg_chunk = sum(vol_chunks[-10:]) / 10 if len(vol_chunks) >= 10 else sum(vol_chunks) / max(len(vol_chunks), 1)
    recent_high_vol = [i for i, v in enumerate(vol_chunks[-5:]) if v > avg_chunk * 1.5]
    
    if recent_high_vol:
        for idx in recent_high_vol:
            start_idx = max(0, len(m1) - (5 - idx) * 10)
            end_idx = min(len(m1), start_idx + 10)
            seg = m1[start_idx:end_idx]
            if seg:
                price_range_seg = max(c["high"] for c in seg) - min(c["low"] for c in seg)
                vol_seg = sum(c.get("tick_volume", 0) for c in seg)
                spread_seg = sum(c.get("spread", 0) for c in seg) / len(seg)
                if price_range_seg < pip * 5 and spread_seg < spread * 0.8:
                    direction = "unknown"
                    mid_seg = (max(c["high"] for c in seg) + min(c["low"] for c in seg)) / 2
                    if mid_seg > bid:
                        direction = "buying"
                    elif mid_seg < bid:
                        direction = "selling"
                    inst["icebergs"].append({
                        "direction": direction, "volume_est": int(vol_seg),
                        "range_pips": round(price_range_seg / (pip or 1), 1),
                        "price_zone": f"{round(min(c['low'] for c in seg), 5)}-{round(max(c['high'] for c in seg), 5)}",
                    })
    
    # Absorption score
    buying_volume_50 = sum(volumes[i] for i in range(max(0, len(volumes)-50), len(volumes)) if closes[i] > closes[i-1] if i > 0)
    selling_volume_50 = sum(volumes[i] for i in range(max(0, len(volumes)-50), len(volumes)) if closes[i] <= closes[i-1] if i > 0)
    total_v_50 = buying_volume_50 + selling_volume_50
    absorption_ratio = buying_volume_50 / (selling_volume_50 or 1)
    
    # Price change over 50 periods
    price_change_50 = (closes[-1] - closes[-50]) / (closes[-50] or 1) * 100 if len(closes) >= 50 else 0
    
    # Absorption = high volume but little price movement
    if total_v_50 > sum(volumes[-150:-50]) / 3 and abs(price_change_50) < 0.1:
        inst["absorption"] = 1
        inst["smart_money_bias"] = "accumulating" if absorption_ratio > 1.2 else "distributing" if absorption_ratio < 0.8 else "neutral"
        inst["confidence"] = 75
    elif total_v_50 > sum(volumes[-150:-50]) / 2:
        inst["absorption"] = 0.5
        inst["smart_money_bias"] = "buying" if price_change_50 > 0.15 else "selling" if price_change_50 < -0.15 else "neutral"
        inst["confidence"] = 55
    else:
        inst["absorption"] = 0
        inst["smart_money_bias"] = price_change_50 > 0.1 and "buying" or price_change_50 < -0.1 and "selling" or "neutral"
        inst["confidence"] = 30
    
    inst["absorption_ratio"] = round(absorption_ratio, 2)
    inst["price_change_50pct"] = round(price_change_50, 3)
    inst["buy_vol_50"] = int(buying_volume_50)
    inst["sell_vol_50"] = int(selling_volume_50)
    
    result["institutional_footprint"] = inst
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LAYER 3: MULTI-PAIR FLOW
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    flow = {"pairs": {}}
    flow_pairs = ["EURUSD","GBPUSD","USDJPY","USDCAD","AUDUSD","NZDUSD","USDCHF"]
    
    for ps in flow_pairs:
        try:
            ps_sym = _fix_sym_local(ps)
            pp = mt5_direct_fn({"action": "price", "symbol": ps_sym})
            pm1 = mt5_direct_fn({"action": "candles", "symbol": ps_sym, "timeframe": "M1", "count": 20})
            pc = pm1.get("candles", pm1.get("data", []))
            p_closes = [c["close"] for c in pc] if pc else [pp.get("bid", 0)]
            p_change = (p_closes[-1] - p_closes[0]) / (p_closes[0] or 1) * 100 if len(p_closes) > 1 else 0
            flow["pairs"][ps] = {
                "bid": pp.get("bid", 0), "ask": pp.get("ask", 0),
                "spread": pp.get("spread", 99),
                "change_20pct": round(p_change, 3),
                "direction": "up" if p_change > 0.02 else "down" if p_change < -0.02 else "flat",
            }
        except:
            flow["pairs"][ps] = {"error": "failed"}
    
    # Detect money flow: which pairs are acting as leading indicators
    # EURUSD vs USD strength
    usd_strength = 0
    count_strength = 0
    for ps, pd in flow["pairs"].items():
        if isinstance(pd, dict) and "direction" in pd:
            if "USD" in ps:
                if ps.startswith("USD") and pd["direction"] == "up":
                    usd_strength -= 1  # USDJPY up = USD weak
                elif ps.startswith("USD") and pd["direction"] == "down":
                    usd_strength += 1  # USDJPY down = USD strong
                elif ps.endswith("USD") and pd["direction"] == "up":
                    usd_strength += 1  # EURUSD up = USD weak
                elif ps.endswith("USD") and pd["direction"] == "down":
                    usd_strength -= 1  # EURUSD down = USD strong
                count_strength += 1
    
    flow["usd_index"] = {
        "value": round(usd_strength / max(count_strength, 1) * 100, 1),
        "bias": "USD_STRONG" if usd_strength > 2 else "USD_WEAK" if usd_strength < -2 else "USD_NEUTRAL",
        "strength_count": usd_strength,
    }
    
    # Correlation: what moves first (leading pair)
    leaders = sorted(flow["pairs"].items(), key=lambda x: abs(x[1].get("change_20pct", 0)) if isinstance(x[1], dict) else 0, reverse=True)
    flow["leading_mover"] = leaders[0][0] if leaders else None
    
    _BRAIN_CACHE["multi_pair"] = flow
    result["multi_pair_flow"] = flow
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LAYER 4: BROKER PREDATOR
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    profile = _BRAIN_CACHE.get("broker_profile", {})
    if not profile:
        profile = {"spread_timeline": [], "hunt_hours": {}, "avg_spread_by_hour": {}}
    
    hour_now = datetime.now(timezone.utc).hour
    
    # Record spread for this hour
    if hour_now not in profile["avg_spread_by_hour"]:
        profile["avg_spread_by_hour"][hour_now] = []
    profile["avg_spread_by_hour"][hour_now].append(spread)
    if len(profile["avg_spread_by_hour"][hour_now]) > 50:
        profile["avg_spread_by_hour"][hour_now] = profile["avg_spread_by_hour"][hour_now][-50:]
    
    # Typical spread for this hour
    typical_spreads = profile["avg_spread_by_hour"].get(hour_now, [spread])
    typical_avg = sum(typical_spreads) / len(typical_spreads)
    anomaly = spread > typical_avg * 1.5 if typical_avg > 0 else False
    
    # Detect hunt events (price spikes with spread widening)
    hunt_events = 0
    if len(m1) >= 10:
        for i in range(1, 10):
            prev_c = m1[-i-1] if i+1 < len(m1) else m1[-1]
            curr_c = m1[-i] if i < len(m1) else m1[-1]
            spike = abs(curr_c["high"] - curr_c["low"]) > abs(prev_c["close"] - prev_c["open"]) * 3
            wide = curr_c.get("spread", spread) > typical_avg * 1.3
            if spike and wide:
                hunt_events += 1
    
    # Predict next broker manipulation
    broker_prediction = "normal"
    if anomaly:
        broker_prediction = "spread_widening_imminent"
    elif hunt_events >= 2:
        broker_prediction = "stop_hunting_active"
    elif spreads[-1] > sum(spreads[-10:]) / 10 * 1.2:
        broker_prediction = "spread_normalizing"
    
    broker_warning = None
    if anomaly and spread > 50:
        broker_warning = "BROKER WARNING: Spread {spread} vs typical {int(typical_avg)} for this hour"
    if hunt_events >= 3:
        broker_warning = "BROKER ALERT: Stop hunting detected ({hunt_events} events in 10 candles)"
    
    broker_score = max(0, min(100, 100 - anomaly * 30 - hunt_events * 10 - (spread / max(typical_avg, 1) - 1) * 20))
    
    _BRAIN_CACHE["broker_profile"] = profile
    
    result["broker_intel"] = {
        "current_spread": spread,
        "typical_spread_hour": round(typical_avg, 1),
        "anomaly": anomaly,
        "hunt_events_10": hunt_events,
        "prediction": broker_prediction,
        "warning": broker_warning,
        "trust_score": round(broker_score),
    }
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # LAYER 5: NEXT 3 MOVES PREDICTION
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    moves = []
    
    # RSI
    r15 = closes[-15:] if len(closes) >= 15 else closes
    g = l = 0
    for i in range(1, len(r15)):
        d = r15[i] - r15[i-1]
        g += max(d, 0)
        l += max(-d, 0)
    rsi = 50 if l == 0 else round(100 - 100 / (1 + g/l), 1)
    
    # EMA trend
    fast = sum(closes[-5:]) / 5 if len(closes) >= 5 else bid
    slow = sum(closes[-20:]) / 20 if len(closes) >= 20 else bid
    trend_up = fast > slow
    
    # Support/Resistance
    s1 = min(lows[-20:]) if len(lows) >= 20 else bid - pip*10
    r1 = max(highs[-20:]) if len(highs) >= 20 else bid + pip*10
    
    # Next target broker
    nt = liquidity.get("next_target", {})
    nt_price = nt.get("price", bid)
    nt_dir = nt.get("direction", "down")
    
    # Move 1: Broker hunt (market goes to liquidity then reverses)
    move1_dir = nt_dir
    move1_target = nt_price
    
    # Move 2: Smart money reaction (reversal from the hunt)
    move2_dir = "up" if move1_dir == "down" else "down"
    move2_target = round(bid + (bid - nt_price) * 0.5 if move1_dir == "down" else bid - (nt_price - bid) * 0.5, 5)
    
    # Move 3: True direction (where the real money is going)
    inst_bias = inst.get("smart_money_bias", "neutral")
    if inst_bias in ("accumulating", "buying"):
        move3_dir = "up"
        move3_target = round(r1 + pip * 5, 5)
    elif inst_bias in ("distributing", "selling"):
        move3_dir = "down"
        move3_target = round(s1 - pip * 5, 5)
    else:
        move3_dir = move2_dir
        move3_target = round(move2_target + (pip * 3 if move2_dir == "up" else -pip * 3), 5)
    
    # Confidence score
    m1_conf = min(90, 50 + inst["confidence"] * 0.4 + broker_score * 0.2 - anomaly * 20)
    
    moves.append({
        "move": 1, "direction": move1_dir, "target": round(move1_target, 5),
        "type": "liquidity_hunt", "timeframe": "next_1-3min",
        "confidence": min(round(m1_conf * 0.9), 85),
    })
    moves.append({
        "move": 2, "direction": move2_dir, "target": round(move2_target, 5),
        "type": "smart_money_reaction", "timeframe": "next_3-7min",
        "confidence": min(round(m1_conf * 0.75), 75),
    })
    moves.append({
        "move": 3, "direction": move3_dir, "target": round(move3_target, 5),
        "type": "real_move", "timeframe": "next_7-15min",
        "confidence": min(round(m1_conf * 0.6), 65),
    })
    
    result["predicted_moves"] = moves
    
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FINAL EDGE VERDICT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    
    edge_score = m1_conf
    if inst_bias in ("accumulating", "buying") and move3_dir == "up":
        edge_score += 10
    elif inst_bias in ("distributing", "selling") and move3_dir == "down":
        edge_score += 10
    if anomaly:
        edge_score -= 15
    edge_score = max(0, min(99, edge_score))
    
    # Recommendation
    if edge_score >= 70 and not anomaly:
        if move3_dir == "up":
            rec_action = "BUY"
            rec_sl = round(move1_target - pip * 1.5, 5) if move1_dir == "down" else round(s1 - pip * 2, 5)
            rec_tp = round(move3_target + pip * 2, 5)
        else:
            rec_action = "SELL"
            rec_sl = round(move1_target + pip * 1.5, 5) if move1_dir == "up" else round(r1 + pip * 2, 5)
            rec_tp = round(move3_target - pip * 2, 5)
        rec = {"action": rec_action, "edge": edge_score,
               "entry": round(bid if rec_action == "BUY" else ask, 5),
               "sl": rec_sl, "tp": rec_tp, "confidence": "HIGH" if edge_score > 80 else "MEDIUM",
               "reason": f"Smart money {inst_bias} | Next: {move1_dir.upper()}→{move2_dir.upper()}→{move3_dir.upper()}"}
    elif edge_score >= 50:
        rec = {"action": "WATCH", "edge": edge_score,
               "reason": f"Waiting for better alignment. Edge {edge_score}% > need 70%",
               "predicted_setup_in": move1_dir.upper() + " hunt at " + str(move1_target) + " then " + move2_dir.upper()}
    else:
        rec = {"action": "SKIP", "edge": edge_score,
               "reason": f"Low edge ({edge_score}%). Anomaly={anomaly}, broker_trust={broker_score}"}
    
    result["market_brain"] = {
        "edge_score": edge_score,
        "institutional_bias": inst_bias,
        "usd_bias": flow.get("usd_index", {}).get("bias", "unknown"),
        "broker_prediction": broker_prediction,
        "recommendation": rec,
        "timeline": f"Move1: {move1_dir.upper()} to {round(move1_target,5)} | Move2: {move2_dir.upper()} to {round(move2_target,5)} | Move3: {move3_dir.upper()} to {round(move3_target,5)}",
    }
    
    return result


T("market_brain", lambda args: market_brain(
    _mt5_direct, args.get("symbol","EURUSD")),
  "Cerebro de Mercado: liquidez, huella institucional, flujo multi-par, broker predator, prediccion 3 movimientos. Ve lo que nadie ve.",
  {"symbol":{"type":"string","default":"EURUSD"}})


# ── Helper ──
_FX_PAIRS_SET = {"EURUSD","GBPUSD","USDJPY","USDCAD","USDCHF","AUDUSD","NZDUSD",
                 "EURGBP","EURJPY","EURCHF","AUDJPY","GBPJPY","CHFJPY","EURAUD",
                 "EURCAD","GBPCHF","GBPAUD","AUDCAD","AUDCHF","AUDNZD","CADCHF",
                 "CADJPY","NZDCAD","NZDJPY","NZDCHF","GBPNZD","EURNZD"}
def _fix_sym_local(sym):
    return sym + ".FX" if sym in _FX_PAIRS_SET else sym

# ── Enhanced Auto-Journaling ──

JOURNAL_FILE = os.path.join(DATA_DIR, "auto_journal.json")

def _load_journal():
    if not os.path.exists(JOURNAL_FILE):
        return {"trades": [], "version": 2}
    try:
        return json.load(open(JOURNAL_FILE))
    except:
        return {"trades": [], "version": 2}

def _save_journal(data):
    os.makedirs(os.path.dirname(JOURNAL_FILE), exist_ok=True)
    with open(JOURNAL_FILE, "w") as f:
        json.dump(data, f, indent=2)

def journal_auto_record(mt5_direct_fn, trade_result):
    """Auto-record trade with full context. Call after every closed trade.
    trade_result must have: symbol, type, entry, exit, pnl, volume, strategy."""
    journal = _load_journal()
    
    # Enrich with market context
    symbol = trade_result.get("symbol", "")
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "type": trade_result.get("type"),
        "entry": trade_result.get("entry"),
        "exit": trade_result.get("exit"),
        "pnl": trade_result.get("pnl"),
        "volume": trade_result.get("volume", 0.01),
        "strategy": trade_result.get("strategy", "manual"),
        "reason": trade_result.get("reason", ""),
        "tags": trade_result.get("tags", []),
    }
    
    # Add market context
    try:
        raw = mt5_direct_fn({"action": "price", "symbol": _fix_sym(symbol)})
        entry["spread_at_exit"] = raw.get("spread", 0)
    except:
        pass
    try:
        raw = mt5_direct_fn({"action": "candles", "symbol": _fix_sym_local(symbol), "timeframe": "H1", "count": 30})
        candles = raw.get("candles", raw.get("data", []))
        if candles and len(candles) > 14:
            closes = [c["close"] for c in candles[-15:]]
            gains, losses = 0, 0
            for i in range(1, len(closes)):
                diff = closes[i] - closes[i-1]
                gains += max(diff, 0)
                losses += max(-diff, 0)
            if losses > 0:
                entry["rsi_exit"] = round(100 - 100 / (1 + gains/losses), 1)
            entry["regime"] = "unknown"
    except:
        pass
    try:
        from datetime import datetime as dt
        hour = dt.now(timezone.utc).hour
        if 8 <= hour < 17:
            entry["session"] = "London"
        elif 13 <= hour < 22:
            entry["session"] = "NY"
        elif 0 <= hour < 9:
            entry["session"] = "Asia/Pacific"
        else:
            entry["session"] = "off_hours"
    except:
        entry["session"] = "unknown"
    
    journal["trades"].append(entry)
    _save_journal(journal)
    
    return {"recorded": True, "trade_id": len(journal["trades"]), "entry": entry}


def journal_query(query_type="all", limit=20):
    """Query the auto-journal. Types: all, wins, losses, by_symbol, by_strategy."""
    journal = _load_journal()
    trades = journal.get("trades", [])
    
    if query_type == "wins":
        trades = [t for t in trades if t.get("pnl", 0) > 0]
    elif query_type == "losses":
        trades = [t for t in trades if t.get("pnl", 0) <= 0]
    
    trades = trades[-limit:] if len(trades) > limit else trades
    
    wins = len([t for t in journal.get("trades", []) if t.get("pnl", 0) > 0])
    losses = len([t for t in journal.get("trades", []) if t.get("pnl", 0) <= 0])
    total_pnl = sum(t.get("pnl", 0) for t in journal.get("trades", []))
    
    return {
        "total_trades": len(journal.get("trades", [])),
        "total_pnl": round(total_pnl, 2),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0,
        "recent_trades": trades,
    }


T("backtest_strategy", lambda args: backtest(
    _mt5_direct, args.get("symbol","EURUSD"), args.get("timeframe","H1"), args.get("strategy","ma_cross"),
    int(args.get("start_idx",0)), int(args.get("end_idx",0)),
    int(args.get("fast_ma",5)), int(args.get("slow_ma",20)),
    int(args.get("rsi_period",14)), int(args.get("rsi_overbought",70)), int(args.get("rsi_oversold",30)),
    float(args.get("sl_atr",1.5)), float(args.get("tp_atr",3.0))),
  "Backtest a strategy on historical data. Returns win rate, profit factor, Sharpe, drawdown.",
  {"symbol":{"type":"string","default":"EURUSD"},"timeframe":{"type":"string","default":"H1"},
   "strategy":{"type":"string","enum":["ma_cross","rsi_mean_reversion","trend_follow"],"default":"ma_cross"},
   "fast_ma":{"type":"integer","default":5},"slow_ma":{"type":"integer","default":20},
   "sl_atr":{"type":"number","default":1.5},"tp_atr":{"type":"number","default":3.0}})

T("multi_timeframe_analysis", lambda args: multi_timeframe_combo(
    _mt5_direct, args.get("symbol","EURUSD"), args.get("timeframes",None)),
  "Evaluate conviction across M5/M15/H1/H4/D1. Weighted vote with alignment score 0-100.",
  {"symbol":{"type":"string","default":"EURUSD"},"timeframes":{"type":"array","items":{"type":"string"},"default":[]}})

T("ml_train", lambda args: ml_train(
    _mt5_direct, args.get("symbol","EURUSD"), args.get("timeframe","H1"),
    int(args.get("lookback",500))),
  "Train ML predictor: extract candle patterns from historical data.",
  {"symbol":{"type":"string","default":"EURUSD"},"timeframe":{"type":"string","default":"H1"},"lookback":{"type":"integer","default":500}})

T("ml_predict", lambda args: ml_predict(
    _mt5_direct, args.get("symbol","EURUSD"), args.get("timeframe","H1")),
  "Predict next candle using pattern matching against trained historical data.",
  {"symbol":{"type":"string","default":"EURUSD"},"timeframe":{"type":"string","default":"H1"}})

T("journal_auto_record", lambda args: journal_auto_record(
    _mt5_direct, args),
  "Auto-record trade with full market context. Call after every closed trade.",
  {"symbol":{"type":"string"},"type":{"type":"string"},"entry":{"type":"number"},"exit":{"type":"number"},
   "pnl":{"type":"number"},"volume":{"type":"number","default":0.01},"strategy":{"type":"string","default":"manual"}},
  ["symbol","type","entry","exit","pnl"])

T("journal_query", lambda args: journal_query(
    args.get("query","all"), int(args.get("limit",20))),
  "Query auto-journal: wins, losses, by symbol, by strategy.",
  {"query":{"type":"string","enum":["all","wins","losses"],"default":"all"},"limit":{"type":"integer","default":20}})

T("smart_money_map", lambda args: smart_money_map(
    _mt5_direct, args.get("symbol","EURUSD")),
  "4-layer smart money analysis: broker manipulation, accumulation/distribution, order flow, stop clusters. Read the market's true intent.",
  {"symbol":{"type":"string","default":"EURUSD"}})


# Need _mt5_direct reference from the main server
_mt5_direct = None

# Load persistent state on import
_load_state()

def init(mt5_direct_fn):
    global _mt5_direct
    _mt5_direct = mt5_direct_fn
    return TOOLS
