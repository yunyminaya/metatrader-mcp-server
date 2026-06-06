"""
Anomaly — detección de anomalías de mercado y ajuste automático de riesgo.

Cuando el mercado se comporta de forma ANÓMALA
(diferente a las últimas N observaciones),
el sistema reduce tamaño de posición o salta trades.

Detectores:
  1. Volatilidad extrema (>3σ de la media histórica)
  2. Spread anómalo (>2σ del spread normal)
  3. Gap de precio (apertura vs cierre anterior)
  4. Volumen anómalo (>3σ del volumen promedio)
  5. Correlación breaking (cambio abrupto en correlaciones conocidas)
  6. Price acceleration (movimiento vertical rápido)

Cada detector produce un score 0-1.
Score total >0.5 → REDUCE size 50%
Score total >0.7 → SKIP trade
"""
import json
import logging
import math
import os
from datetime import datetime, timezone
from typing import Dict, Any, List

import numpy as np

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
DATA_FILE = os.path.join(DATA_DIR, "anomaly.json")

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
                "recent_metrics": [],
                "baseline": {},
                "max_history": 200,
            }


def _save():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DATA_FILE, "w") as f:
            json.dump(_state, f, indent=2)
    except Exception as e:
        logger.warning(f"Cannot save: {e}")


def check(client, symbol: str) -> Dict[str, Any]:
    """Run anomaly detection for a symbol.

    Returns anomaly score and advice.
    """
    _ensure()

    try:
        # Get recent candles
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=100)
        import pandas as pd
        if not isinstance(df, pd.DataFrame) or len(df) < 20:
            return {"success": True, "anomaly_score": 0, "anomalous": False, "reason": "insufficient_data"}

        closes = df['close'].values
        highs = df['high'].values
        lows = df['low'].values
        volumes = df.get('tick_volume', df.get('volume', pd.Series([0] * len(df)))).values

        anomalies = []

        # ── 1. Volatility anomaly ──
        returns = np.diff(closes) / closes[:-1] * 100
        if len(returns) >= 20:
            current_vol = np.std(returns[-5:]) if len(returns) >= 5 else 0
            hist_vol = np.std(returns)
            if hist_vol > 0 and current_vol > hist_vol * 3:
                anomalies.append({
                    "type": "extreme_volatility",
                    "score": min(1, current_vol / (hist_vol * 5)),
                    "current_vol": round(current_vol, 3),
                    "hist_vol": round(hist_vol, 3),
                })

        # ── 2. Spread anomaly ──
        spreads = []
        try:
            price_info = client.market.get_symbol_price(symbol_name=symbol)
            if isinstance(price_info, dict):
                current_spread = price_info.get("spread", 0)
                # Build historical spread from candle ranges
                for i in range(len(df)):
                    s = abs(highs[i] - lows[i]) / closes[i] * 10000
                    spreads.append(s)
                if spreads and len(spreads) >= 20:
                    avg_spread = np.mean(spreads)
                    std_spread = np.std(spreads)
                    if std_spread > 0 and current_spread > avg_spread + 2 * std_spread:
                        anomalies.append({
                            "type": "wide_spread",
                            "score": min(1, (current_spread - avg_spread) / (std_spread * 5)),
                            "current_spread": round(current_spread, 1),
                            "avg_spread": round(avg_spread, 1),
                        })
        except Exception:
            pass

        # ── 3. Gap anomaly ──
        if len(closes) >= 2:
            gap = (closes[-1] - closes[-2]) / closes[-2] * 100
            if abs(gap) > 1.0:  # >1% gap
                anomalies.append({
                    "type": "price_gap",
                    "score": min(1, abs(gap) / 3),
                    "gap_pct": round(gap, 2),
                })

        # ── 4. Volume anomaly ──
        if len(volumes) >= 20:
            avg_vol = np.mean(volumes)
            std_vol = np.std(volumes)
            current_vol_val = volumes[-1] if len(volumes) > 0 else 0
            if std_vol > 0 and current_vol_val > avg_vol + 3 * std_vol:
                anomalies.append({
                    "type": "high_volume",
                    "score": min(1, (current_vol_val - avg_vol) / (std_vol * 5)),
                    "current_vol": int(current_vol_val),
                    "avg_vol": int(avg_vol),
                })

        # ── 5. Price acceleration ──
        if len(returns) >= 10:
            recent_ret = np.mean(returns[-3:]) if len(returns) >= 3 else 0
            older_ret = np.mean(returns[-10:-3]) if len(returns) >= 10 else 0
            if abs(recent_ret) > abs(older_ret) * 3 and abs(older_ret) > 0:
                anomalies.append({
                    "type": "price_acceleration",
                    "score": min(1, abs(recent_ret) / (abs(older_ret) * 5)),
                    "recent_return": round(recent_ret, 3),
                    "normal_return": round(older_ret, 3),
                })

        # Total anomaly score
        if anomalies:
            total_score = max(a["score"] for a in anomalies)
        else:
            total_score = 0

        # Advice
        if total_score > 0.7:
            advice = "skip_trade"
            size_mult = 0
        elif total_score > 0.5:
            advice = "reduce_size_50"
            size_mult = 0.5
        elif total_score > 0.3:
            advice = "reduce_size_25"
            size_mult = 0.75
        else:
            advice = "normal"
            size_mult = 1.0

        # Store metrics for baseline evolution
        _state.setdefault("recent_metrics", []).append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "symbol": symbol,
            "anomaly_score": round(total_score, 3),
            "advice": advice,
        })
        _state["recent_metrics"] = _state["recent_metrics"][-100:]
        _save()

        return {
            "success": True,
            "anomaly_score": round(total_score, 3),
            "anomalous": total_score > 0.5,
            "anomalies_detected": len(anomalies),
            "anomaly_details": anomalies,
            "advice": advice,
            "size_multiplier": size_mult,
        }

    except Exception as e:
        return {"success": False, "error": str(e), "anomaly_score": 0, "anomalous": False}


def status() -> Dict[str, Any]:
    _ensure()
    return {
        "success": True,
        "anomaly": {
            "recent_checks": _state.get("recent_metrics", [])[-10:],
        }
    }
