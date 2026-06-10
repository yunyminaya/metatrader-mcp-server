#!/usr/bin/env python3
"""
Local ML Scorer - Machine Learning local
Calcula score 0-100 para trades sin usar APIs externas.
"""

import json
import hashlib
import struct
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
        self.model_path = Path.home() / ".metatrader-mcp" / "ml_model.json"
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
        """Cargar modelo entrenado si existe (formato JSON seguro)."""
        if self.model_path.exists():
            try:
                with open(self.model_path, 'r') as f:
                    model_data = json.load(f)

                # Verificar integridad del modelo
                if self._verify_model(model_data):
                    self.model = model_data
                    print(f"[ML] Modelo cargado desde {self.model_path}")
                else:
                    print("[ML Warning] Modelo corrupto, ignorando")
                    self.model = None
            except Exception as e:
                print(f"[ML Error] Cargando modelo: {e}")
                self.model = None

    def _verify_model(self, model_data: Dict) -> bool:
        """Verificar integridad del modelo JSON."""
        required_keys = ["version", "features", "weights"]
        return all(k in model_data for k in required_keys)

    def _save_model(self):
        """Guardar modelo entrenado en formato JSON seguro."""
        try:
            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.model_path, 'w') as f:
                json.dump(self.model, f, indent=2)
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

        if not tick:
            return 0, {"error": "Sin datos de tick"}

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
        bb_range = features["bb_upper"] - features["bb_lower"]
        features["bb_position"] = (current_price - features["bb_lower"]) / bb_range if bb_range > 0 else 0.5

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
        features["symbol"] = symbol

        return min(100, max(0, score)), features

    def train(self, historical_trades: List[Dict]) -> Dict[str, Any]:
        """
        Entrenar modelo con trades históricos usando JSON (no pickle).
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
            features = trade.get("features", {})
            if isinstance(features, str):
                try:
                    features = json.loads(features)
                except (json.JSONDecodeError, TypeError):
                    features = {}

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
        rf = RandomForestClassifier(
            n_estimators=100,
            max_depth=10,
            min_samples_split=5,
            random_state=42
        )
        rf.fit(X_train, y_train)

        # Evaluar
        y_pred = rf.predict(X_test)
        accuracy = accuracy_score(y_test, y_pred)

        # Extraer pesos del modelo para guardar en JSON (seguro)
        model_data = self._extract_model_weights(rf, accuracy, len(X_train), len(X_test))
        self.model = model_data
        self._save_model()

        return {
            "accuracy": accuracy,
            "trains_used": len(X_train),
            "test_used": len(X_test)
        }

    def _extract_model_weights(self, rf, accuracy, n_train, n_test) -> Dict:
        """Extraer información útil del RandomForest para guardar en JSON."""
        # Guardar feature importances como modelo simplificado
        feature_names = ["rsi", "macd_histogram", "bb_position", "atr", "patterns_count"]
        importances = rf.feature_importances_.tolist()

        # Guardar árboles de decisión como reglas simplificadas
        trees_rules = []
        for i, tree in enumerate(rf.estimators_[:10]):  # Solo top 10 árboles
            tree_dict = {
                "tree_id": i,
                "n_nodes": tree.tree_.node_count,
                "importance": importances[i] if i < len(importances) else 0
            }
            trees_rules.append(tree_dict)

        return {
            "version": "1.0",
            "model_type": "random_forest",
            "n_estimators": rf.n_estimators,
            "feature_names": feature_names,
            "feature_importances": importances,
            "accuracy": accuracy,
            "n_train": n_train,
            "n_test": n_test,
            "weights": trees_rules
        }

    def _score_fenix(self, features: Dict, current_price: float) -> int:
        """
        Scoring ultra-selectivo tipo Fénix.
        Requiere alta confluencia de factores.
        Máximo teórico: 100 puntos.
        """
        score = 0

        # RSI en zona óptima (máx 15 puntos)
        rsi = features["rsi"]
        if rsi < 30 or rsi > 70:
            score += 15  # Sobrecompra/sobreventa extrema
        elif 40 <= rsi <= 60:
            score += 10  # Zona neutra para reversión

        # MACD alineado (máx 15 puntos)
        macd_hist = features["macd_histogram"]
        if abs(macd_hist) > 0.0001:
            score += 10
            if macd_hist > 0 and features["trend_ema"] == "up":
                score += 5  # Momentum alcista confirmado
            elif macd_hist < 0 and features["trend_ema"] == "down":
                score += 5  # Momentum bajista confirmado

        # Bollinger Bands (máx 15 puntos)
        bb_pos = features["bb_position"]
        if bb_pos < 0.2 or bb_pos > 0.8:
            score += 15  # Precio en extremos

        # Patrones de velas (máx 20 puntos - 5 por patrón, hasta 4 patrones)
        patterns = features.get("patterns", [])
        pattern_score = min(len(patterns) * 5, 20)
        score += pattern_score

        # ATR suficiente - volatilidad (máx 10 puntos)
        atr = features["atr"]
        if atr > 0.0005:
            score += 5
        if atr > 0.001:
            score += 5

        # Tendencia clara (máx 10 puntos)
        if features["trend_ema"] in ["up", "down"]:
            score += 10

        # EMA alignment bonus (máx 15 puntos)
        if features["trend_ema"] == "up" and current_price > features["ema_20"]:
            score += 15
        elif features["trend_ema"] == "down" and current_price < features["ema_20"]:
            score += 15

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

        # Consolidación previa (rango estrecho)
        if len(highs) >= 20 and len(lows) >= 20:
            recent_range = max(highs[-20:]) - min(lows[-20:])
            if recent_range < features["atr"] * 3:
                score += 15  # Rango estrecho = potencial breakout

        return score

    def _predict_with_model(self, features: Dict) -> int:
        """Predecir usando modelo entrenado (feature importances)."""
        if not self.model or "feature_importances" not in self.model:
            return 50

        try:
            importances = self.model["feature_importances"]
            feature_names = self.model.get("feature_names", [])

            # Weighted score basado en feature importances
            values = [
                features.get("rsi", 50) / 100,
                abs(features.get("macd_histogram", 0)) * 10000,
                features.get("bb_position", 0.5),
                features.get("atr", 0) * 10000,
                len(features.get("patterns", [])) / 7
            ]

            # Normalizar valores a 0-1
            normalized = []
            for v in values:
                normalized.append(min(1.0, max(0.0, v)))

            # Weighted sum
            if len(importances) == len(normalized):
                weighted_score = sum(n * w for n, w in zip(normalized, importances))
                return int(weighted_score * 100 / sum(importances)) if sum(importances) > 0 else 50

            return 50
        except Exception:
            return 50

    def _adjust_for_market_context(self, score: int, features: Dict, tick: Dict) -> int:
        """Ajustar score por contexto de mercado."""
        if not tick:
            return score

        # Reducir score si spread es alto
        spread = tick.get("ask", 0) - tick.get("bid", 0)
        if spread > 0.0010:  # Spread alto
            score -= 10
        elif spread > 0.0005:  # Spread moderado
            score -= 5

        return max(0, score)

    def _determine_direction(self, features: Dict, score: int) -> str:
        """Determinar dirección recomendada del trade."""
        if score < 50:
            return "none"

        # Lógica basada en indicadores
        bullish_signals = 0
        bearish_signals = 0

        if features["trend_ema"] == "up":
            bullish_signals += 1
        else:
            bearish_signals += 1

        if features["macd_histogram"] > 0:
            bullish_signals += 1
        else:
            bearish_signals += 1

        if features["rsi"] < 40:
            bullish_signals += 1  # Posible rebote
        elif features["rsi"] > 60:
            bearish_signals += 1

        if bullish_signals > bearish_signals:
            return "buy"
        elif bearish_signals > bullish_signals:
            return "sell"

        return "buy"  # Default en empate

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
        """
        Calcular MACD y señal correctamente.
        MACD = EMA(12) - EMA(26)
        Signal = EMA(9) del MACD
        """
        if len(closes) < 35:
            return 0.0, 0.0

        # Calcular serie completa de EMA12 y EMA26
        ema12_series = self._calculate_ema_series(closes, 12)
        ema26_series = self._calculate_ema_series(closes, 26)

        # MACD line = EMA12 - EMA26 (desde que ambas existen)
        min_len = min(len(ema12_series), len(ema26_series))
        macd_line = [ema12_series[-(min_len - i)] - ema26_series[-(min_len - i)]
                     for i in range(min_len)]

        # Signal line = EMA(9) del MACD
        if len(macd_line) >= 9:
            signal = self._calculate_ema(macd_line, 9)
        else:
            signal = macd_line[-1] if macd_line else 0.0

        macd = macd_line[-1] if macd_line else 0.0

        return float(macd), float(signal)

    def _calculate_ema_series(self, data: List[float], period: int) -> List[float]:
        """Calcular serie completa de EMA para MACD."""
        if len(data) < period:
            return [data[-1]] if data else [0.0]

        ema_values = [float(np.mean(data[:period]))]
        multiplier = 2 / (period + 1)

        for price in data[period:]:
            ema_values.append((price - ema_values[-1]) * multiplier + ema_values[-1])

        return ema_values

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
