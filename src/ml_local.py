#!/usr/bin/env python3
"""
Local ML Scorer - Machine Learning local
Calcula score 0-100 para trades sin usar APIs externas.
"""

import json
import pickle
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import numpy as np


class LocalMLScorer:
    """
    Scorer de trades usando machine learning local.
    No requiere conexión a APIs externas.
    """

    def __init__(self, db):
        self.db = db
        self.model_path = Path.home() / ".metatrader-mcp" / "ml_model.pkl"
        self.model = None
        self._load_model()

        # Features predefinidas para análisis técnico
        self.patterns = self._init_patterns()

    def _init_patterns(self) -> Dict:
        """Inicializar patrones de velas a detectar."""
        return {
            "doji": self._detect_doji,
            "hammer": self._detect_hammer,
            "engulfing": self._detect_engulfing,
            "morning_star": self._detect_morning_star,
            "evening_star": self._detect_evening_star,
            "three_white_soldiers": self._detect_three_white_soldiers,
            "three_black_crows": self._detect_three_black_crows,
        }

    def _load_model(self):
        """Cargar modelo entrenado si existe."""
        if self.model_path.exists():
            try:
                with open(self.model_path, 'rb') as f:
                    self.model = pickle.load(f)
                print(f"[ML] Modelo cargado desde {self.model_path}")
            except Exception as e:
                print(f"[ML Error] Cargando modelo: {e}")
                self.model = None

    def _save_model(self):
        """Guardar modelo entrenado."""
        try:
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.model_path, 'wb') as f:
                pickle.dump(self.model, f)
            print(f"[ML] Modelo guardado en {self.model_path}")
        except Exception as e:
            print(f"[ML Error] Guardando modelo: {e}")

    def calculate_score(self, symbol: str, candles: List[Dict],
                       tick: Dict, setup_type: str = "fenix") -> Tuple[int, Dict]:
        """
        Calcular score 0-100 para un setup de trading.
        Retorna (score, features_dict).
        """
        features = {}

        if not candles or len(candles) < 20:
            return 0, {"error": "Datos insuficientes"}

        # 1. Calcular indicadores técnicos
        closes = [c["close"] for c in candles]
        highs = [c["high"] for c in candles]
        lows = [c["low"] for c in candles]
        opens = [c["open"] for c in candles]

        # RSI (14 períodos)
        features["rsi"] = self._calculate_rsi(closes, 14)

        # EMAs
        features["ema_20"] = self._calculate_ema(closes, 20)
        features["ema_50"] = self._calculate_ema(closes, 50)

        # Tendencia EMA
        features["trend_ema"] = "up" if features["ema_20"] > features["ema_50"] else "down"

        # MACD
        features["macd"], features["macd_signal"] = self._calculate_macd(closes)
        features["macd_histogram"] = features["macd"] - features["macd_signal"]

        # Bollinger Bands
        features["bb_upper"], features["bb_middle"], features["bb_lower"] = self._calculate_bollinger(closes)
        current_price = closes[-1]
        features["bb_position"] = (current_price - features["bb_lower"]) / (features["bb_upper"] - features["bb_lower"])

        # ATR (Average True Range)
        features["atr"] = self._calculate_atr(highs, lows, closes, 14)

        # Detectar patrones de velas
        patterns_detected = []
        for pattern_name, detector in self.patterns.items():
            if detector(candles[-5:]):
                patterns_detected.append(pattern_name)
        features["patterns"] = patterns_detected

        # 2. Calcular puntaje basado en setup
        score = 0

        if setup_type == "fenix":
            score = self._score_fenix(features, current_price)
        elif setup_type == "trend":
            score = self._score_trend_following(features, current_price)
        elif setup_type == "mean_reversion":
            score = self._score_mean_reversion(features, current_price)
        elif setup_type == "breakout":
            score = self._score_breakout(features, highs, lows, current_price)
        else:
            score = self._score_fenix(features, current_price)

        # 3. Aplicar predicción del modelo si existe
        if self.model:
            ml_score = self._predict_with_model(features)
            # Combinar scores (70% técnico, 30% ML)
            score = int(score * 0.7 + ml_score * 0.3)

        # 4. Ajustar por contexto de mercado
        score = self._adjust_for_market_context(score, features, tick)

        features["score_raw"] = score
        features["direction"] = self._determine_direction(features, score)

        return min(100, max(0, score)), features

    def train(self, historical_trades: List[Dict]) -> Dict[str, Any]:
        """
        Entrenar modelo con trades históricos.
        """
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.model_selection import train_test_split
        from sklearn.metrics import accuracy_score

        if len(historical_trades) < 50:
            return {"error": "Se necesitan al menos 50 trades para entrenar"}

        # Preparar datos
        X = []
        y = []

        for trade in historical_trades:
            # Extraer features del trade
            features = trade.get("features", {})
            if features:
                feature_vector = [
                    features.get("rsi", 50),
                    features.get("macd_histogram", 0),
                    features.get("bb_position", 0.5),
                    features.get("atr", 0),
                    len(features.get("patterns", []))
                ]
                X.append(feature_vector)

                # Label: 1 si fue profitable, 0 si no
                profit = trade.get("profit", 0)
                y.append(1 if profit > 0 else 0)

        if len(X) < 30:
            return {"error": "Datos insuficientes para entrenar"}

        # Dividir datos
        X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)

        # Entrenar modelo
        self.model = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=5,
            random_state=42
        )
        self.model.fit(X_train, y_train)

        # Evaluar
        y_pred = self.model.predict(X_test)
        accuracy = accuracy_score(y_test, y_pred)

        # Guardar modelo
        self._save_model()

        return {
            "accuracy": accuracy,
            "trains_used": len(X_train),
            "test_used": len(X_test)
        }

    def _score_fenix(self, features: Dict, current_price: float) -> int:
        """
        Scoring ultra-selectivo tipo Fénix.
        Requiere alta confluencia de factores.
        """
        score = 0

        # RSI en zona óptima (40-60 para reversión, <30 o >70 para momentum)
        rsi = features["rsi"]
        if 40 <= rsi <= 60:
            score += 10  # Zona neutra para reversión
        elif rsi < 30 or rsi > 70:
            score += 15  # Sobrecompra/sobreventa extrema

        # MACD alineado
        macd_hist = features["macd_histogram"]
        if abs(macd_hist) > 0.0001:
            score += 10
            if macd_hist > 0 and features["trend_ema"] == "up":
                score += 5  # Momentum alcista confirmado
            elif macd_hist < 0 and features["trend_ema"] == "down":
                score += 5  # Momentum bajista confirmado

        # Bollinger Bands
        bb_pos = features["bb_position"]
        if bb_pos < 0.2 or bb_pos > 0.8:
            score += 15  # Precio en extremos

        # Patrones de velas
        patterns = features.get("patterns", [])
        score += len(patterns) * 10  # Hasta 70 puntos por patrones

        # ATR suficiente (volatilidad)
        atr = features["atr"]
        if atr > 0.0005:  # Mínimo movimiento esperado
            score += 5

        # Tendencia clara
        if features["trend_ema"] in ["up", "down"]:
            score += 10

        return score

    def _score_trend_following(self, features: Dict, current_price: float) -> int:
        """Scoring para estrategia trend following."""
        score = 0

        # EMAs alineadas
        if features["trend_ema"] == "up":
            score += 20

        # MACD positivo
        if features["macd_histogram"] > 0:
            score += 15

        # RSI en zona de momentum (50-70)
        if 50 <= features["rsi"] <= 70:
            score += 15

        # Precio por encima de EMAs
        if current_price > features["ema_20"] > features["ema_50"]:
            score += 20

        # ATR adecuado
        if features["atr"] > 0.001:
            score += 10

        return score

    def _score_mean_reversion(self, features: Dict, current_price: float) -> int:
        """Scoring para estrategia mean reversion."""
        score = 0

        # RSI en extremos
        rsi = features["rsi"]
        if rsi < 25 or rsi > 75:
            score += 25
        elif rsi < 35 or rsi > 65:
            score += 15

        # Bollinger Bands extremos
        bb_pos = features["bb_position"]
        if bb_pos < 0.1 or bb_pos > 0.9:
            score += 25

        # Divergencia MACD (simplificada)
        if abs(features["macd_histogram"]) < 0.0001:
            score += 10

        return score

    def _score_breakout(self, features: Dict, highs: List[float],
                       lows: List[float], current_price: float) -> int:
        """Scoring para estrategia breakout."""
        score = 0

        # ATR alto (volatilidad creciente)
        if features["atr"] > 0.0015:
            score += 20

        # RSI en zona de momentum
        if features["rsi"] > 55:
            score += 15

        # Volumen creciente (simulado)
        score += 10

        # Consolidación previa (rango estrecho)
        recent_range = max(highs[-20:]) - min(lows[-20:])
        if recent_range < features["atr"] * 3:
            score += 15  # Rango estrecho = potencial breakout

        return score

    def _predict_with_model(self, features: Dict) -> int:
        """Predecir usando modelo entrenado."""
        if not self.model:
            return 50

        feature_vector = [
            features.get("rsi", 50),
            features.get("macd_histogram", 0),
            features.get("bb_position", 0.5),
            features.get("atr", 0),
            len(features.get("patterns", []))
        ]

        prediction = self.model.predict([feature_vector])[0]
        proba = self.model.predict_proba([feature_vector])[0]

        # Retornar score 0-100 basado en probabilidad
        return int(proba[1] * 100) if prediction == 1 else int(proba[0] * 100)

    def _adjust_for_market_context(self, score: int, features: Dict, tick: Dict) -> int:
        """Ajustar score por contexto de mercado."""
        # Reducir score si spread es alto
        spread = tick.get("ask", 0) - tick.get("bid", 0)
        if spread > 0.0010:  # Spread alto
            score -= 10

        return max(0, score)

    def _determine_direction(self, features: Dict, score: int) -> str:
        """Determinar dirección recomendada del trade."""
        if score < 50:
            return "none"

        # Lógica simplificada
        if features["trend_ema"] == "up" and features["macd_histogram"] > 0:
            return "buy"
        elif features["trend_ema"] == "down" and features["macd_histogram"] < 0:
            return "sell"

        # Default basado en RSI
        if features["rsi"] < 40:
            return "buy"  # Posible rebote
        elif features["rsi"] > 60:
            return "sell"

        return "buy"  # Default

    # ============ Indicadores Técnicos ============

    def _calculate_rsi(self, closes: List[float], period: int = 14) -> float:
        """Calcular RSI."""
        if len(closes) < period + 1:
            return 50.0

        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        return float(rsi)

    def _calculate_ema(self, closes: List[float], period: int) -> float:
        """Calcular EMA."""
        if len(closes) < period:
            return closes[-1] if closes else 0

        ema = np.mean(closes[:period])
        multiplier = 2 / (period + 1)

        for price in closes[period:]:
            ema = (price - ema) * multiplier + ema

        return float(ema)

    def _calculate_macd(self, closes: List[float]) -> Tuple[float, float]:
        """Calcular MACD y señal."""
        if len(closes) < 35:
            return 0.0, 0.0

        ema12 = self._calculate_ema(closes, 12)
        ema26 = self._calculate_ema(closes, 26)
        macd = ema12 - ema26

        # Señal = EMA 9 del MACD
        macd_series = [ema12 - ema26]  # Simplificado
        signal = self._calculate_ema(macd_series + [macd] * 9, 9)

        return float(macd), float(signal)

    def _calculate_bollinger(self, closes: List[float],
                            period: int = 20,
                            std_dev: int = 2) -> Tuple[float, float, float]:
        """Calcular Bollinger Bands."""
        if len(closes) < period:
            middle = np.mean(closes) if closes else 0
            return middle, middle, middle

        recent = closes[-period:]
        middle = np.mean(recent)
        std = np.std(recent)

        upper = middle + (std * std_dev)
        lower = middle - (std * std_dev)

        return float(upper), float(middle), float(lower)

    def _calculate_atr(self, highs: List[float], lows: List[float],
                      closes: List[float], period: int = 14) -> float:
        """Calcular Average True Range."""
        if len(closes) < period + 1:
            return 0.001

        tr1 = np.array(highs[1:]) - np.array(lows[1:])
        tr2 = np.abs(np.array(highs[1:]) - np.array(closes[:-1]))
        tr3 = np.abs(np.array(lows[1:]) - np.array(closes[:-1]))

        true_ranges = np.maximum(np.maximum(tr1, tr2), tr3)
        atr = np.mean(true_ranges[-period:])

        return float(atr)

    # ============ Detectores de Patrones ============

    def _detect_doji(self, candles: List[Dict]) -> bool:
        """Detectar patrón Doji."""
        if not candles:
            return False
        c = candles[-1]
        body = abs(c["close"] - c["open"])
        range_total = c["high"] - c["low"]
        return body < (range_total * 0.1) and range_total > 0

    def _detect_hammer(self, candles: List[Dict]) -> bool:
        """Detectar patrón Hammer."""
        if not candles:
            return False
        c = candles[-1]
        body = abs(c["close"] - c["open"])
        lower_shadow = min(c["open"], c["close"]) - c["low"]
        upper_shadow = c["high"] - max(c["open"], c["close"])
        return lower_shadow > (body * 2) and upper_shadow < body

    def _detect_engulfing(self, candles: List[Dict]) -> bool:
        """Detectar patrón Engulfing."""
        if len(candles) < 2:
            return False
        c1, c2 = candles[-2], candles[-1]

        body1 = abs(c1["close"] - c1["open"])
        body2 = abs(c2["close"] - c2["open"])

        bullish = c1["close"] < c1["open"] and c2["close"] > c2["open"]
        bullish = bullish and c2["open"] < c1["close"] and c2["close"] > c1["open"]

        bearish = c1["close"] > c1["open"] and c2["close"] < c2["open"]
        bearish = bearish and c2["open"] > c1["close"] and c2["close"] < c1["open"]

        return (bullish or bearish) and body2 > body1

    def _detect_morning_star(self, candles: List[Dict]) -> bool:
        """Detectar patrón Morning Star."""
        if len(candles) < 3:
            return False
        c1, c2, c3 = candles[-3], candles[-2], candles[-1]

        first_bearish = c1["close"] < c1["open"]
        second_small = abs(c2["close"] - c2["open"]) < abs(c1["close"] - c1["open"]) * 0.3
        third_bullish = c3["close"] > c3["open"]
        third_strong = c3["close"] > (c1["open"] + c1["close"]) / 2

        return first_bearish and second_small and third_bullish and third_strong

    def _detect_evening_star(self, candles: List[Dict]) -> bool:
        """Detectar patrón Evening Star."""
        if len(candles) < 3:
            return False
        c1, c2, c3 = candles[-3], candles[-2], candles[-1]

        first_bullish = c1["close"] > c1["open"]
        second_small = abs(c2["close"] - c2["open"]) < abs(c1["close"] - c1["open"]) * 0.3
        third_bearish = c3["close"] < c3["open"]
        third_strong = c3["close"] < (c1["open"] + c1["close"]) / 2

        return first_bullish and second_small and third_bearish and third_strong

    def _detect_three_white_soldiers(self, candles: List[Dict]) -> bool:
        """Detectar Three White Soldiers."""
        if len(candles) < 3:
            return False

        for c in candles[-3:]:
            if c["close"] <= c["open"]:
                return False

        return True

    def _detect_three_black_crows(self, candles: List[Dict]) -> bool:
        """Detectar Three Black Crows."""
        if len(candles) < 3:
            return False

        for c in candles[-3:]:
            if c["close"] >= c["open"]:
                return False

        return True
