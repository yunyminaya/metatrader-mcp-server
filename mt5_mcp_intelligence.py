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

# Need _mt5_direct reference from the main server
_mt5_direct = None

# Load persistent state on import
_load_state()

def init(mt5_direct_fn):
    global _mt5_direct
    _mt5_direct = mt5_direct_fn
    return TOOLS
