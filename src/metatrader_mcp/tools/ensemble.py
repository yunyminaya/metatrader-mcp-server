"""
Ensemble — motor de votación ponderada entre TODAS las estrategias.

Ejecuta conviction + mean reversion + grid + straddle + patterns
simultáneamente y produce UNA decisión final.

Cada estrategia vota: BUY/SELL/PASS con confianza 0-99.
El voto final es ponderado por el rendimiento RECIENTE de cada estrategia.

Las estrategias con mejor win rate reciente pesan más.
Las que llevan racha de pérdidas pesan menos o se excluyen.

Flujo:
  ensemble_evaluate(symbol)
    → conviction.decide()       peso W1
    → volatility.mean_reversion()  peso W2
    → volatility.adaptive_grid()   peso W3
    → volatility.straddle_signal() peso W4
    → patterns.detect_all()        peso W5
    → orderbook.analyze_depth()    peso W6
    → volumeprofile.calculate()  peso W7
    → sentiment.analyze_news()   peso W8

    → Voto ponderado → BUY/SELL/PASS final con confianza
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "ensemble.json")

_state: Dict[str, Any] = {}

_STRATEGIES = [
    "conviction", "mean_reversion", "adaptive_grid", "straddle",
    "patterns", "orderbook", "volumeprofile", "sentiment",
]


def _ensure():
    global _state
    if not _state:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE) as f:
                    _state = json.load(f)
        except Exception:
            _state = {
                "enabled": True,
                "weight_window": 20,
                "min_votes": 3,
                "min_confidence": 55,
                "weights": {s: 1.0 for s in _STRATEGIES},
                "history": {s: {"wins": 0, "losses": 0, "total": 0, "recent": []} for s in _STRATEGIES},
                "vote_log": [],
            }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def evaluate(client, symbol: str, bankroll: float = 1000) -> Dict[str, Any]:
    """Run ALL strategies and produce ONE ensemble decision."""
    _ensure()
    votes = []

    # ── 1. Conviction ──
    try:
        from metatrader_mcp.tools.conviction import decide
        d = decide(client, symbol, "H1", bankroll)
        if d.get("success"):
            dec = d.get("decision", {})
            votes.append({
                "strategy": "conviction",
                "verdict": dec.get("verdict", "PASS"),
                "confidence": dec.get("confidence_pct", 0),
                "details": {"direction": dec.get("direction")},
            })
    except Exception:
        pass

    # ── 2. Mean Reversion ──
    try:
        from metatrader_mcp.tools.volatility import mean_reversion
        mr = mean_reversion(client, symbol, entry_std=2.0)
        if mr.get("success") and mr.get("signal"):
            signal = mr["signal"]
            conf = mr.get("confidence_pct", 50)
            votes.append({
                "strategy": "mean_reversion",
                "verdict": signal,
                "confidence": conf,
                "details": {"entry_zone": mr.get("entry_zone")},
            })
    except Exception:
        pass

    # ── 3. Adaptive Grid ──
    try:
        from metatrader_mcp.tools.volatility import adaptive_grid
        g = adaptive_grid(client, symbol, 0.01, 5)
        if g.get("success"):
            grid_verdict = "PASS"
            if g.get("levels"):
                price = g.get("current_price", 0)
                levels = g.get("levels", [])
                if levels and len(levels) > 1:
                    nearest_buy = min([l for l in levels if l < price], default=None)
                    nearest_sell = min([l for l in levels if l > price], default=None)
                    if nearest_buy and price - nearest_buy < (levels[1] - levels[0]) * 0.3:
                        grid_verdict = "BUY"
                    elif nearest_sell and nearest_sell - price < (levels[1] - levels[0]) * 0.3:
                        grid_verdict = "SELL"
            votes.append({
                "strategy": "adaptive_grid",
                "verdict": grid_verdict,
                "confidence": 50,
                "details": {"grid_levels": len(g.get("levels", []))},
            })
    except Exception:
        pass

    # ── 4. Straddle ──
    try:
        from metatrader_mcp.tools.volatility import straddle_signal
        st = straddle_signal(client, symbol)
        if st.get("success") and st.get("near_breakout"):
            direction = st.get("breakout_direction", "PASS")
            conf = st.get("confidence", 50)
            votes.append({
                "strategy": "straddle",
                "verdict": direction,
                "confidence": conf,
                "details": {"range": st.get("range")},
            })
    except Exception:
        pass

    # ── 5. Candle Patterns ──
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=30)
        import pandas as pd
        if isinstance(df, pd.DataFrame):
            candles = [{"open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"]}
                       for _, r in df.iterrows()]
            from metatrader_mcp.tools.patterns import detect_all
            pat = detect_all(candles)
            if pat.get("success"):
                p_verdict = pat.get("verdict", "neutral")
                p_verdict_map = {"bullish": "BUY", "bearish": "SELL", "neutral": "PASS"}
                pconf = 50
                if pat.get("strongest_reliability", 0) > 0.6:
                    pconf = 60 + int(pat["strongest_reliability"] * 20)
                votes.append({
                    "strategy": "patterns",
                    "verdict": p_verdict_map.get(p_verdict, "PASS"),
                    "confidence": pconf,
                    "details": {"patterns_found": pat["total_patterns"], "strongest": pat.get("strongest")},
                })
    except Exception:
        pass

    # ── 6. Order Book ──
    try:
        from metatrader_mcp.tools.orderbook import analyze_depth
        ob = analyze_depth(client, symbol)
        if ob.get("success"):
            pressure = ob.get("pressure", "neutral")
            ob_verdict = {"bullish": "BUY", "bearish": "SELL", "neutral": "PASS"}.get(pressure, "PASS")
            ob_conf = 50 + int(abs(ob.get("imbalance", 0)) * 50)
            votes.append({
                "strategy": "orderbook",
                "verdict": ob_verdict,
                "confidence": min(ob_conf, 95),
                "details": {"imbalance": ob.get("imbalance"), "pressure": pressure},
            })
    except Exception:
        pass

    # ── 7. Volume Profile ──
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=48)
        import pandas as pd
        if isinstance(df, pd.DataFrame):
            candles_vp = [{"open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"],
                           "volume": r.get("tick_volume", r.get("volume", 0))}
                          for _, r in df.iterrows()]
            from metatrader_mcp.tools.volumeprofile import calculate
            vp = calculate(candles_vp)
            if vp.get("success"):
                pos = vp.get("price_position", "in_value")
                adv = vp.get("advice", "neutral")
                vp_verdict = {"potential_buy": "BUY", "potential_sell": "SELL"}.get(adv, "PASS")
                vp_conf = 55
                if adv == "potential_buy":
                    vp_conf = 65
                elif adv == "potential_sell":
                    vp_conf = 65
                votes.append({
                    "strategy": "volumeprofile",
                    "verdict": vp_verdict,
                    "confidence": vp_conf,
                    "details": {"position": pos, "poc": vp.get("point_of_control")},
                })
    except Exception:
        pass

    # ── 8. Sentiment ──
    try:
        from metatrader_mcp.tools.sentiment import analyze_news
        sn = analyze_news(symbol)
        if sn.get("success"):
            label = sn.get("label", "neutral")
            sent_verdict = {"bullish": "BUY", "bearish": "SELL", "neutral": "PASS"}.get(label, "PASS")
            sent_conf = 50 + int(abs(sn.get("sentiment", 0)) * 40)
            votes.append({
                "strategy": "sentiment",
                "verdict": sent_verdict,
                "confidence": min(sent_conf, 90),
                "details": {"sentiment": sn.get("sentiment"), "label": label},
            })
    except Exception:
        pass

    if not votes:
        return {"success": False, "error": "No strategies returned votes", "votes": []}

    # ── WEIGHTED VOTING ──
    total_buy = 0.0
    total_sell = 0.0
    total_weight = 0.0
    vote_details = []

    for v in votes:
        strat = v["strategy"]
        weight = _state["weights"].get(strat, 1.0)
        verdict = v["verdict"]
        conf = v["confidence"]

        # Reduce weight if strategy is on losing streak
        strat_history = _state["history"].get(strat, {})
        recent = strat_history.get("recent", [])[-10:]
        if recent:
            recent_losses = sum(1 for r in recent if not r)
            if recent_losses >= 3:
                weight *= 0.3
            elif recent_losses >= 2:
                weight *= 0.5

        weighted_conf = conf * weight

        vote_details.append({
            "strategy": strat,
            "verdict": verdict,
            "confidence": conf,
            "weight": round(weight, 2),
            "weighted_confidence": round(weighted_conf, 1),
        })

        if verdict == "BUY":
            total_buy += weighted_conf
        elif verdict == "SELL":
            total_sell += weighted_conf
        total_weight += weight

    # Normalize
    norm_buy = total_buy / total_weight if total_weight > 0 else 0
    norm_sell = total_sell / total_weight if total_weight > 0 else 0

    # Determine final
    min_votes = _state.get("min_votes", 3)
    buy_votes = sum(1 for v in votes if v["verdict"] == "BUY")
    sell_votes = sum(1 for v in votes if v["verdict"] == "SELL")
    total_votes = len(votes)

    if total_votes < min_votes:
        final = "PASS"
        confidence = 0
    elif norm_buy > norm_sell and norm_buy >= _state.get("min_confidence", 55) * 0.6:
        final = "BUY"
        confidence = min(int(norm_buy), 99)
    elif norm_sell > norm_buy and norm_sell >= _state.get("min_confidence", 55) * 0.6:
        final = "SELL"
        confidence = min(int(norm_sell), 99)
    elif norm_buy > norm_sell:
        final = "WEAK_BUY"
        confidence = max(30, min(int(norm_buy), 55))
    elif norm_sell > norm_buy:
        final = "WEAK_SELL"
        confidence = max(30, min(int(norm_sell), 55))
    else:
        final = "PASS"
        confidence = 0

    # Log
    _state.setdefault("vote_log", []).append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "final_verdict": final,
        "final_confidence": confidence,
        "buy_weight": round(norm_buy, 1),
        "sell_weight": round(norm_sell, 1),
        "votes": total_votes,
        "buy_votes": buy_votes,
        "sell_votes": sell_votes,
    })
    _state["vote_log"] = _state["vote_log"][-100:]
    _save()

    return {
        "success": True,
        "symbol": symbol,
        "ensemble_verdict": final,
        "ensemble_confidence": confidence,
        "buy_score": round(norm_buy, 1),
        "sell_score": round(norm_sell, 1),
        "total_votes": total_votes,
        "buy_votes": buy_votes,
        "sell_votes": sell_votes,
        "pass_votes": total_votes - buy_votes - sell_votes,
        "vote_details": vote_details,
        "advice": (
            "strong_buy" if final == "BUY" and confidence >= 75
            else "buy" if final == "BUY"
            else "strong_sell" if final == "SELL" and confidence >= 75
            else "sell" if final == "SELL"
            else "avoid"
        ),
    }


def on_trade_result(strategy: str, won: bool) -> Dict[str, Any]:
    """Record trade result for a strategy and recalculate weights."""
    _ensure()
    if strategy not in _state["history"]:
        return {"success": False, "error": f"Unknown strategy: {strategy}"}

    h = _state["history"][strategy]
    if won:
        h["wins"] += 1
    else:
        h["losses"] += 1
    h["total"] += 1
    h.setdefault("recent", []).append(won)
    h["recent"] = h["recent"][-_state.get("weight_window", 20):]

    # Recalculate weight based on recent win rate
    recent = h.get("recent", [])
    if len(recent) >= 5:
        win_rate = sum(recent) / len(recent)
        _state["weights"][strategy] = max(0.1, win_rate * 2)  # 0.1 to 2.0

    _save()
    return {"success": True, "new_weight": round(_state["weights"][strategy], 2),
            "recent_win_rate": round(sum(h["recent"]) / max(len(h["recent"]), 1), 2)}


def status() -> Dict[str, Any]:
    _ensure()
    return {
        "success": True,
        "ensemble": {
            "enabled": _state.get("enabled", True),
            "weights": _state["weights"],
            "history": {
                s: {
                    "wins": h["wins"], "losses": h["losses"],
                    "total": h["total"],
                    "recent_win_rate": round(sum(h["recent"]) / max(len(h["recent"]), 1), 2) if h["recent"] else 0,
                } for s, h in _state["history"].items()
            },
            "last_votes": _state.get("vote_log", [])[-5:],
        }
    }
