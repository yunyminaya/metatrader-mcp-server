"""
Reinforcement Learning Agent — Q-learning para entry/exit timing.

State: [position, pnl_pct, volatility_regime, session_hour, trend_strength, rsi_zone]
Action: 0=HOLD, 1=BUY, 2=SELL, 3=CLOSE_LONG, 4=CLOSE_SHORT, 5=ADD_TO_WINNER
Reward: change in Sharpe ratio over trailing window

Entrena online con cada trade completado.
No necesita librerías externas (Q-table pura con numpy).
"""
import json
import logging
import math
import os
import random
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data")
QTABLE_FILE = os.path.join(DATA_DIR, "rl_qtable.json")
CONFIG_FILE = os.path.join(DATA_DIR, "rl_config.json")

# Actions
HOLD = 0
BUY = 1
SELL = 2
CLOSE_LONG = 3
CLOSE_SHORT = 4
ADD_WINNER = 5

ACTION_NAMES = ["HOLD", "BUY", "SELL", "CLOSE_LONG", "CLOSE_SHORT", "ADD_TO_WINNER"]

# Discretization buckets
POSITION_BUCKETS = ["none", "long", "short"]
PNL_BUCKETS = ["loss_big", "loss_small", "breakeven", "profit_small", "profit_big"]
VOL_BUCKETS = ["low", "medium", "high", "extreme"]
SESSION_BUCKETS = ["asian", "london", "ny"]
TREND_BUCKETS = ["strong_down", "weak_down", "none", "weak_up", "strong_up"]
RSI_BUCKETS = ["oversold", "low", "mid", "high", "overbought"]

# State dimension: product of all bucket sizes
N_STATES = (len(POSITION_BUCKETS) * len(PNL_BUCKETS) * len(VOL_BUCKETS) *
            len(SESSION_BUCKETS) * len(TREND_BUCKETS) * len(RSI_BUCKETS))
N_ACTIONS = 6

# Hyperparameters
DEFAULT_ALPHA = 0.1
DEFAULT_GAMMA = 0.9
DEFAULT_EPSILON = 0.2
DEFAULT_EPSILON_DECAY = 0.995
DEFAULT_MIN_EPSILON = 0.01

_rl_state: Dict[str, Any] = {
    "qtable": None,
    "config": {},
    "total_steps": 0,
    "total_episodes": 0,
    "last_action": None,
    "last_state_idx": None,
    "cumulative_reward": 0,
    "episode_rewards": [],
}


def _state_index(pos: str, pnl: str, vol: str, session: str, trend: str, rsi: str) -> int:
    """Encode discrete state tuple into a single integer index."""
    p_idx = POSITION_BUCKETS.index(pos) if pos in POSITION_BUCKETS else 0
    pnl_idx = PNL_BUCKETS.index(pnl) if pnl in PNL_BUCKETS else 2
    v_idx = VOL_BUCKETS.index(vol) if vol in VOL_BUCKETS else 1
    s_idx = SESSION_BUCKETS.index(session) if session in SESSION_BUCKETS else 1
    t_idx = TREND_BUCKETS.index(trend) if trend in TREND_BUCKETS else 2
    r_idx = RSI_BUCKETS.index(rsi) if rsi in RSI_BUCKETS else 2

    return (p_idx * len(PNL_BUCKETS) * len(VOL_BUCKETS) * len(SESSION_BUCKETS) *
            len(TREND_BUCKETS) * len(RSI_BUCKETS) +
            pnl_idx * len(VOL_BUCKETS) * len(SESSION_BUCKETS) * len(TREND_BUCKETS) * len(RSI_BUCKETS) +
            v_idx * len(SESSION_BUCKETS) * len(TREND_BUCKETS) * len(RSI_BUCKETS) +
            s_idx * len(TREND_BUCKETS) * len(RSI_BUCKETS) +
            t_idx * len(RSI_BUCKETS) +
            r_idx)


def _discretize_pnl(pnl_pct: float) -> str:
    if pnl_pct <= -5:
        return "loss_big"
    elif pnl_pct <= -1:
        return "loss_small"
    elif pnl_pct <= 1:
        return "breakeven"
    elif pnl_pct <= 5:
        return "profit_small"
    else:
        return "profit_big"


def _discretize_vol(atr_pct: float) -> str:
    if atr_pct <= 0.05:
        return "low"
    elif atr_pct <= 0.15:
        return "medium"
    elif atr_pct <= 0.3:
        return "high"
    else:
        return "extreme"


def _discretize_trend(adx: float, slope: float) -> str:
    if adx < 20:
        return "none"
    if slope > 0.5:
        return "strong_up" if adx > 30 else "weak_up"
    elif slope < -0.5:
        return "strong_down" if adx > 30 else "weak_down"
    else:
        return "none"


def _discretize_rsi(rsi: float) -> str:
    if rsi <= 30:
        return "oversold"
    elif rsi <= 40:
        return "low"
    elif rsi <= 60:
        return "mid"
    elif rsi <= 70:
        return "high"
    else:
        return "overbought"


def _get_session(hour: int) -> str:
    if 1 <= hour < 9:
        return "asian"
    elif 9 <= hour < 17:
        return "london"
    else:
        return "ny"


def _ensure_qtable():
    if _rl_state["qtable"] is not None:
        return
    try:
        if os.path.exists(QTABLE_FILE):
            with open(QTABLE_FILE) as f:
                data = json.load(f)
                _rl_state["qtable"] = np.array(data.get("qtable", np.zeros((N_STATES, N_ACTIONS)).tolist()))
                _rl_state["config"] = data.get("config", {})
                _rl_state["total_steps"] = data.get("total_steps", 0)
                _rl_state["total_episodes"] = data.get("total_episodes", 0)
                _rl_state["cumulative_reward"] = data.get("cumulative_reward", 0)
                _rl_state["episode_rewards"] = data.get("episode_rewards", [])
        else:
            _rl_state["qtable"] = np.zeros((N_STATES, N_ACTIONS))
            _rl_state["config"] = {
                "alpha": DEFAULT_ALPHA, "gamma": DEFAULT_GAMMA,
                "epsilon": DEFAULT_EPSILON, "epsilon_decay": DEFAULT_EPSILON_DECAY,
                "min_epsilon": DEFAULT_MIN_EPSILON,
            }
    except Exception as e:
        logger.warning(f"RL state init error: {e}, creating fresh Q-table")
        _rl_state["qtable"] = np.zeros((N_STATES, N_ACTIONS))
        _rl_state["config"] = {
            "alpha": DEFAULT_ALPHA, "gamma": DEFAULT_GAMMA,
            "epsilon": DEFAULT_EPSILON, "epsilon_decay": DEFAULT_EPSILON_DECAY,
            "min_epsilon": DEFAULT_MIN_EPSILON,
        }


def _save_qtable():
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        data = {
            "qtable": _rl_state["qtable"].tolist(),
            "config": _rl_state["config"],
            "total_steps": _rl_state["total_steps"],
            "total_episodes": _rl_state["total_episodes"],
            "cumulative_reward": _rl_state["cumulative_reward"],
            "episode_rewards": _rl_state["episode_rewards"][-100:],
        }
        with open(QTABLE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        logger.warning(f"RL save error: {e}")


def _get_state(client, symbol: str) -> Tuple[int, Dict[str, str]]:
    """Get discretized state representation for current market conditions."""
    now = datetime.now(timezone.utc)
    hour = now.hour

    # Position status
    pos = "none"
    pnl = "breakeven"
    try:
        live_pos = client.account.get_positions(symbol=symbol)
        if live_pos and len(live_pos) > 0:
            p = live_pos[0]
            if hasattr(p, "type"):
                pos = "long" if p.type == 0 else "short"
            elif isinstance(p, dict):
                pos = "long" if p.get("type", 0) == 0 else "short"
            if hasattr(p, "profit"):
                pnl_pct = p.profit / max(getattr(p, "volume", 0.01) * 100000, 1) * 100
                pnl = _discretize_pnl(pnl_pct)
            elif isinstance(p, dict):
                profit = float(p.get("profit", 0))
                vol = float(p.get("volume", 0.01))
                pnl_pct = profit / max(vol * 100000, 1) * 100
                pnl = _discretize_pnl(pnl_pct)
    except Exception:
        pass

    # Get market data
    atr_pct = 0.1
    adx = 20
    slope = 0.0
    rsi_val = 50
    try:
        df = client.market.get_candles_latest(symbol_name=symbol, timeframe="H1", count=100)
        if df is not None:
            import pandas as pd
            if isinstance(df, pd.DataFrame) and not df.empty:
                closes = df["close"].dropna().values
                highs = df["high"].dropna().values
                lows = df["low"].dropna().values

                if len(closes) >= 14:
                    tr = np.maximum(highs[1:] - lows[1:],
                                    np.maximum(np.abs(highs[1:] - closes[:-1]),
                                               np.abs(lows[1:] - closes[:-1])))
                    atr = float(np.mean(tr[-14:])) if len(tr) >= 14 else 0
                    atr_pct = atr / max(closes[-1], 0.0001) * 100

                if len(closes) >= 20:
                    # Simple trend slope (linear regression)
                    x = np.arange(20)
                    y = closes[-20:]
                    slope = (np.sum(x * y) - 20 * x.mean() * y.mean()) / max(np.sum(x**2) - 20 * x.mean()**2, 0.0001)

                    # RSI approximation
                    gains = 0
                    losses = 0
                    for i in range(len(closes) - 14, len(closes) - 1):
                        diff = closes[i + 1] - closes[i]
                        if diff > 0:
                            gains += diff
                        else:
                            losses += abs(diff)
                    avg_gain = gains / 14
                    avg_loss = losses / 14
                    if avg_loss == 0:
                        rsi_val = 100
                    else:
                        rs = avg_gain / avg_loss
                        rsi_val = 100 - 100 / (1 + rs)

                    # ADX approximation
                    up = highs[1:] - highs[:-1]
                    down = lows[:-1] - lows[1:]
                    pos_dm = np.maximum(up, 0)
                    neg_dm = np.maximum(down, 0)
                    tr_smooth = np.mean(tr[-14:]) if len(tr) >= 14 else 1
                    di_pos = 100 * np.mean(pos_dm[-14:]) / max(tr_smooth, 0.0001)
                    di_neg = 100 * np.mean(neg_dm[-14:]) / max(tr_smooth, 0.0001)
                    dx = abs(di_pos - di_neg) / max(di_pos + di_neg, 0.0001) * 100
                    adx = dx
    except Exception:
        pass

    vol_bucket = _discretize_vol(atr_pct)
    session_bucket = _get_session(hour)
    trend_bucket = _discretize_trend(adx, slope)
    rsi_bucket = _discretize_rsi(rsi_val)

    state_desc = {
        "position": pos,
        "pnl": pnl,
        "volatility": vol_bucket,
        "session": session_bucket,
        "trend": trend_bucket,
        "rsi": rsi_bucket,
    }

    idx = _state_index(pos, pnl, vol_bucket, session_bucket, trend_bucket, rsi_bucket)
    return idx, state_desc


def _get_reward(action: int, pos: str, pnl_pct: float) -> float:
    """Calculate reward for an action given current state."""
    reward = 0.0

    # Positive reward for actions that make sense
    if action == HOLD:
        reward = 0  # Neutral
    elif action == BUY and pos == "none":
        reward = 1  # Opening a trade
    elif action == SELL and pos == "none":
        reward = 1
    elif action == CLOSE_LONG and pos == "long":
        reward = pnl_pct / 5  # Proportional to PnL
    elif action == CLOSE_SHORT and pos == "short":
        reward = pnl_pct / 5
    elif action == ADD_WINNER and pos != "none":
        # Reward adding to winners, penalize adding to losers
        if pnl_pct > 3:
            reward = 2
        elif pnl_pct < 0:
            reward = -2
        else:
            reward = 0.5
    else:
        # Invalid action (e.g., BUY when already long)
        reward = -1

    return reward


def configure(alpha: float = DEFAULT_ALPHA, gamma: float = DEFAULT_GAMMA,
              epsilon: float = DEFAULT_EPSILON,
              epsilon_decay: float = DEFAULT_EPSILON_DECAY,
              min_epsilon: float = DEFAULT_MIN_EPSILON) -> Dict[str, Any]:
    """Configure RL agent hyperparameters.

    Args:
        alpha: learning rate (0-1)
        gamma: discount factor (0-1)
        epsilon: exploration rate (0-1)
        epsilon_decay: decay per step
        min_epsilon: minimum exploration rate
    """
    _ensure_qtable()
    _rl_state["config"] = {
        "alpha": max(0.01, min(0.9, alpha)),
        "gamma": max(0.1, min(0.99, gamma)),
        "epsilon": max(0.01, min(1.0, epsilon)),
        "epsilon_decay": max(0.9, min(0.999, epsilon_decay)),
        "min_epsilon": max(0.001, min(0.5, min_epsilon)),
    }
    _save_qtable()
    return {"success": True, "config": _rl_state["config"]}


def train(episodes: int = 100, client=None, symbol: str = "EURUSD") -> Dict[str, Any]:
    """Train the RL agent via Q-learning updates.

    If client is provided, uses real market data for training.
    Otherwise, runs simulated episodes.

    Args:
        episodes: number of episodes to train
        client: optional MT5 client for real data
        symbol: symbol to train on
    """
    _ensure_qtable()
    q = _rl_state["qtable"]
    config = _rl_state["config"]
    alpha = config.get("alpha", DEFAULT_ALPHA)
    gamma = config.get("gamma", DEFAULT_GAMMA)
    epsilon = config.get("epsilon", DEFAULT_EPSILON)

    episode_rewards = []
    trained = 0

    for ep in range(episodes):
        state_idx, _ = _get_state(client, symbol) if client else (0, {})
        total_reward = 0
        steps = 0

        for step in range(50):  # Max 50 steps per episode
            # Epsilon-greedy
            if random.random() < epsilon:
                action = random.randint(0, N_ACTIONS - 1)
            else:
                action = int(np.argmax(q[state_idx]))

            # Simulate next state (in real use, this comes from environment)
            if client:
                next_idx, state_desc = _get_state(client, symbol)
                pnl_pct = 0
                pos = state_desc.get("position", "none")
                if pos != "none":
                    try:
                        live_pos = client.account.get_positions(symbol=symbol)
                        if live_pos and len(live_pos) > 0:
                            p = live_pos[0]
                            profit = float(getattr(p, "profit", 0) if hasattr(p, "profit") else 0)
                            vol = float(getattr(p, "volume", 0.01) if hasattr(p, "volume") else 0.01)
                            pnl_pct = profit / max(vol * 100000, 1) * 100
                    except Exception:
                        pass
                reward = _get_reward(action, pos, pnl_pct)
            else:
                # Simulated: random next state
                next_idx = random.randint(0, N_STATES - 1)
                reward = random.uniform(-1, 1)

            # Q-learning update
            best_next = np.max(q[next_idx])
            td_target = reward + gamma * best_next
            td_error = td_target - q[state_idx, action]
            q[state_idx, action] += alpha * td_error

            total_reward += reward
            steps += 1
            _rl_state["total_steps"] += 1

            # Decay epsilon
            epsilon = max(config.get("min_epsilon", DEFAULT_MIN_EPSILON),
                        epsilon * config.get("epsilon_decay", DEFAULT_EPSILON_DECAY))
            config["epsilon"] = epsilon

            if action in (CLOSE_LONG, CLOSE_SHORT):
                break  # Episode ends when position closes

            state_idx = next_idx

        episode_rewards.append(total_reward)
        _rl_state["total_episodes"] += 1
        _rl_state["cumulative_reward"] += total_reward
        trained += 1

    _rl_state["episode_rewards"] = _rl_state.get("episode_rewards", []) + episode_rewards
    _rl_state["episode_rewards"] = _rl_state["episode_rewards"][-1000:]

    _save_qtable()

    avg_reward = sum(episode_rewards) / max(len(episode_rewards), 1)
    best_reward = max(episode_rewards) if episode_rewards else 0

    return {
        "success": True,
        "episodes_trained": trained,
        "avg_reward": round(avg_reward, 2),
        "best_reward": round(best_reward, 2),
        "total_steps": _rl_state["total_steps"],
        "epsilon": round(config["epsilon"], 4),
        "qtable_nz_states": int(np.count_nonzero(q)),
    }


def decide(client, symbol: str) -> Dict[str, Any]:
    """Get RL agent's optimal action for current market state.

    Args:
        client: MT5 client
        symbol: symbol to decide on

    Returns:
        dict with recommended action, Q-values, and state info
    """
    _ensure_qtable()
    q = _rl_state["qtable"]
    epsilon = _rl_state["config"].get("epsilon", DEFAULT_EPSILON)

    state_idx, state_desc = _get_state(client, symbol)

    # Epsilon-greedy
    if random.random() < epsilon:
        action = random.randint(0, N_ACTIONS - 1)
        greedy = False
    else:
        action = int(np.argmax(q[state_idx]))
        greedy = True

    q_values = {ACTION_NAMES[i]: round(float(q[state_idx, i]), 3) for i in range(N_ACTIONS)}

    # Map RL action to trading signal
    if action == BUY:
        verdict = "BUY"
    elif action == SELL:
        verdict = "SELL"
    elif action == CLOSE_LONG:
        verdict = "CLOSE_LONG"
    elif action == CLOSE_SHORT:
        verdict = "CLOSE_SHORT"
    elif action == ADD_WINNER:
        verdict = "ADD_TO_WINNER"
    else:
        verdict = "HOLD"

    confidence = int((max(q[state_idx]) - min(q[state_idx])) / max(abs(max(q[state_idx])) + 0.001, 0.001) * 50 + 50)
    confidence = min(95, max(5, confidence))

    _rl_state["last_action"] = action
    _rl_state["last_state_idx"] = state_idx

    return {
        "success": True,
        "symbol": symbol,
        "verdict": verdict,
        "confidence_pct": confidence,
        "greedy": greedy,
        "action_index": action,
        "q_values": q_values,
        "state": state_desc,
        "total_episodes": _rl_state["total_episodes"],
        "total_steps": _rl_state["total_steps"],
        "cumulative_reward": round(_rl_state["cumulative_reward"], 2),
        "epsilon": round(epsilon, 4),
    }


def record_outcome(reward: float) -> Dict[str, Any]:
    """Record reward for the last action (for online learning).

    Args:
        reward: observed reward for the action taken

    Returns:
        dict with updated Q-table info
    """
    _ensure_qtable()
    q = _rl_state["qtable"]
    config = _rl_state["config"]
    alpha = config.get("alpha", DEFAULT_ALPHA)
    gamma = config.get("gamma", DEFAULT_GAMMA)

    state_idx = _rl_state.get("last_state_idx")
    action = _rl_state.get("last_action")

    if state_idx is None or action is None:
        return {"success": False, "error": "No prior action recorded"}

    # Assume next step is terminal for this update
    best_next = 0  # Terminal state
    td_target = reward + gamma * best_next
    td_error = td_target - q[state_idx, action]
    q[state_idx, action] += alpha * td_error

    _rl_state["total_steps"] += 1
    _rl_state["cumulative_reward"] += reward
    _rl_state["episode_rewards"].append(reward)

    # Decay epsilon
    config["epsilon"] = max(
        config.get("min_epsilon", DEFAULT_MIN_EPSILON),
        config.get("epsilon", DEFAULT_EPSILON) * config.get("epsilon_decay", DEFAULT_EPSILON_DECAY)
    )

    _save_qtable()

    return {
        "success": True,
        "reward": round(reward, 2),
        "td_error": round(td_error, 4),
        "updated_q": round(float(q[state_idx, action]), 4),
        "total_steps": _rl_state["total_steps"],
    }


def status() -> Dict[str, Any]:
    """Get RL agent status."""
    _ensure_qtable()
    q = _rl_state["qtable"]

    # Count states that have been visited
    visited = int(np.count_nonzero(np.max(q, axis=1)))

    # Recent performance
    recent = _rl_state.get("episode_rewards", [])[-50:]
    avg_recent = sum(recent) / max(len(recent), 1)

    # Best action distribution
    best_actions = np.argmax(q, axis=1)
    action_dist = {ACTION_NAMES[i]: int(np.sum(best_actions == i)) for i in range(N_ACTIONS)}

    return {
        "success": True,
        "total_episodes": _rl_state["total_episodes"],
        "total_steps": _rl_state["total_steps"],
        "cumulative_reward": round(_rl_state["cumulative_reward"], 2),
        "avg_recent_reward": round(avg_recent, 2),
        "visited_states": visited,
        "total_states": N_STATES,
        "coverage_pct": round(visited / max(N_STATES, 1) * 100, 1),
        "epsilon": round(_rl_state["config"].get("epsilon", DEFAULT_EPSILON), 4),
        "action_preference": action_dist,
        "config": _rl_state["config"],
    }


def reset() -> Dict[str, Any]:
    """Reset RL agent — clear Q-table."""
    _rl_state["qtable"] = np.zeros((N_STATES, N_ACTIONS))
    _rl_state["total_steps"] = 0
    _rl_state["total_episodes"] = 0
    _rl_state["cumulative_reward"] = 0
    _rl_state["episode_rewards"] = []
    _rl_state["last_action"] = None
    _rl_state["last_state_idx"] = None
    try:
        if os.path.exists(QTABLE_FILE):
            os.remove(QTABLE_FILE)
    except Exception:
        pass
    return {"success": True, "message": "Q-table reset"}
