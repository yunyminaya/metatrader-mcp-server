#!/usr/bin/env python3
"""
MT5 MAC MCP — servidor MCP nativo de macOS para MetaTrader 5 bajo Wine.

Usa el bridge de archivos PythonBridge que ya procesa fuego/OrderBridgeEA.
Las herramientas de trading real exigen confirm_live=true y pasan por un
preflight de riesgo antes de enviar BUY/SELL.
"""

import fcntl
import glob
import json
import os
import platform
import subprocess
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


BOTTLE = Path.home() / (
    "Library/Application Support/net.metaquotes.wine.metatrader5/"
    "drive_c/Program Files/MetaTrader 5"
)
BRIDGE_DIR = Path.home() / (
    "Library/Application Support/net.metaquotes.wine.metatrader5/"
    "drive_c/users/user/AppData/Roaming/MetaQuotes/Terminal/Common/Files/PythonBridge"
)
ORDER_FILE = BRIDGE_DIR / "orders.txt"
RESULT_FILE = BRIDGE_DIR / "results.txt"
LOCK_FILE = BRIDGE_DIR / "bridge.lock"
BRIDGE_LOG_FILE = BRIDGE_DIR / "bridge.log"
MT4_PREFIX = Path(os.environ.get("MCP_MT4_WINEPREFIX", str(Path.home() / "Library/Application Support/net.metaquotes.wine.metatrader4")))
MT4_BOTTLE = Path(os.environ.get("MCP_MT4_TERMINAL_DIR", str(MT4_PREFIX / "drive_c/Program Files (x86)/MetaTrader 4")))
MT4_BRIDGE_DIR = Path(os.environ.get("MCP_MT4_BRIDGE_DIR", str(MT4_PREFIX / "drive_c/users/crossover/AppData/Roaming/MetaQuotes/Terminal/Common/Files/PythonBridge")))
MT4_WINE_BIN = os.environ.get("MCP_MT4_WINE_BIN", "/Applications/MetaTrader 4.app/Contents/SharedSupport/wine/bin/wine32on64")

WINE_BIN = os.environ.get("MCP_MT5_WINE_BIN", "/Applications/MetaTrader 5.app/Contents/SharedSupport/wine/bin/wine")
WINEPREFIX = os.environ.get("MCP_MT5_WINEPREFIX", str(Path.home() / "Library/Application Support/net.metaquotes.wine.metatrader5"))
WINE_PYTHON = os.environ.get("MCP_MT5_WINE_PYTHON", str(Path.home() / "Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Python312/python.exe"))
DIRECT_CLI = str(Path(__file__).with_name("mt5_direct_cli.py"))
DATA_DIR = Path(__file__).with_name("data")
STRATEGY_FILE = DATA_DIR / "mcp_strategies.json"

MAX_LIVE_VOLUME = 0.01
MAX_OPEN_POSITIONS = 1
MAX_MARGIN_USE_PCT = 65.0
MIN_POST_TRADE_FREE_MARGIN_PCT = 30.0
MAX_SPREAD_POINTS = 80
DAILY_RISK_FILE = DATA_DIR / "daily_risk.json"

def _load_daily_risk() -> dict:
    if not DAILY_RISK_FILE.exists():
        return {"max_loss": 0.0, "max_trades": 0, "trades_today": 0, "date": ""}
    try:
        return json.loads(DAILY_RISK_FILE.read_text())
    except Exception:
        return {"max_loss": 0.0, "max_trades": 0, "trades_today": 0, "date": ""}

def _save_daily_risk(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DAILY_RISK_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def _guard_daily_risk() -> Optional[str]:
    risk = _load_daily_risk()
    limit = risk.get("max_loss", 0.0)
    if limit <= 0:
        return None
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if risk.get("date") != today:
        return None
    # Calculate today's PnL from history
    today_pnl = 0.0
    today_ts = int(datetime.strptime(today + " 00:00:00", "%Y-%m-%d %H:%M:%S").timestamp())
    try:
        hist = _mt5_direct({"action": "history", "from": today_ts, "to": int(time.time()) + 86400})
        deals = hist.get("deals", hist.get("data", []))
        if isinstance(deals, list):
            for d in deals:
                if isinstance(d, dict):
                    today_pnl += d.get("profit", 0.0) + d.get("swap", 0.0) + d.get("commission", 0.0)
    except Exception:
        pass
    # Add floating PnL from open positions
    try:
        positions = _mt5_direct({"action": "positions"})
        pos_list = positions.get("positions", positions.get("data", []))
        if isinstance(pos_list, list):
            for p in pos_list:
                today_pnl += p.get("profit", p.get("floating_pnl", 0.0)) if isinstance(p, dict) else 0
    except Exception:
        pass
    if today_pnl <= -limit:
        return f"Daily loss limit reached: {today_pnl:.2f} <= -{limit:.2f}"
    return None

_FX_PAIRS = {"EURUSD","GBPUSD","USDJPY","USDCAD","USDCHF","AUDUSD","NZDUSD",
             "EURGBP","EURJPY","EURCHF","AUDJPY","GBPJPY","CHFJPY","EURAUD",
             "EURCAD","GBPCHF","GBPAUD","AUDCAD","AUDCHF","AUDNZD","CADCHF",
             "CADJPY","NZDCAD","NZDJPY","NZDCHF","GBPNZD","EURNZD"}
def _fix_sym(sym):
    return sym + ".FX" if sym in _FX_PAIRS else sym


class BridgeError(RuntimeError):
    pass


def _load_strategies() -> Dict[str, Any]:
    if not STRATEGY_FILE.exists():
        return {}
    try:
        return json.loads(STRATEGY_FILE.read_text())
    except Exception:
        return {}


def _save_strategies(strategies: Dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    STRATEGY_FILE.write_text(json.dumps(strategies, ensure_ascii=False, indent=2))


def _mt5_direct(cmd: Dict[str, Any], timeout: float = 90.0) -> Dict[str, Any]:
    env = os.environ.copy()
    env["WINEPREFIX"] = WINEPREFIX
    env["WINEDEBUG"] = "-all"
    env["MVK_CONFIG_LOG_LEVEL"] = "0"
    proc = subprocess.run(
        [WINE_BIN, WINE_PYTHON, DIRECT_CLI, json.dumps(cmd)],
        text=True,
        capture_output=True,
        timeout=timeout,
        env=env,
    )
    if proc.returncode != 0:
        raise BridgeError((proc.stderr or proc.stdout or f"wine exited {proc.returncode}").strip())
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    if not lines:
        raise BridgeError("direct MT5 returned no output")
    data = json.loads(lines[-1])
    if isinstance(data, dict) and data.get("error"):
        raise BridgeError(str(data))
    return data


def _path_status(path: Path | str) -> Dict[str, Any]:
    p = Path(path)
    return {"path": str(p), "exists": p.exists()}


def _detect_platforms() -> Dict[str, Any]:
    system = platform.system().lower()
    mt5_terminal = BOTTLE / "terminal64.exe"
    mt4_terminal = MT4_BOTTLE / "terminal.exe"
    mt4_bridge_candidates = [
        MT4_BOTTLE / "MQL4/Experts/MT4BridgeEA.mq4",
        *Path(MT4_PREFIX / "drive_c/users/crossover/AppData/Roaming/MetaQuotes/Terminal").glob("*/MQL4/Experts/MT4BridgeEA.mq4"),
    ]
    mt4_bridge_ea = next((p for p in mt4_bridge_candidates if p.exists()), mt4_bridge_candidates[0])
    if system == "linux":
        mt5_candidates = [
            Path(os.environ.get("MCP_MT5_TERMINAL_DIR", "")) if os.environ.get("MCP_MT5_TERMINAL_DIR") else None,
            Path.home() / ".wine/drive_c/Program Files/MetaTrader 5",
            Path.home() / ".wine/drive_c/Program Files (x86)/MetaTrader 5",
        ]
        mt4_candidates = [
            Path(os.environ.get("MCP_MT4_TERMINAL_DIR", "")) if os.environ.get("MCP_MT4_TERMINAL_DIR") else None,
            Path.home() / ".wine/drive_c/Program Files/MetaTrader 4",
            Path.home() / ".wine/drive_c/Program Files (x86)/MetaTrader 4",
        ]
        mt5_guess = next((p for p in mt5_candidates if p and (p / "terminal64.exe").exists()), None)
        mt4_guess = next((p for p in mt4_candidates if p and (p / "terminal.exe").exists()), None)
        if mt5_guess:
            mt5_terminal = mt5_guess / "terminal64.exe"
        if mt4_guess:
            mt4_terminal = mt4_guess / "terminal.exe"
            mt4_bridge_ea = mt4_guess / "MQL4/Experts/MT4BridgeEA.mq4"

    mt5_direct_ready = Path(WINE_BIN).exists() and Path(WINE_PYTHON).exists() and Path(DIRECT_CLI).exists()
    mt4_bridge_ready = mt4_terminal.exists() and mt4_bridge_ea.exists()
    return {
        "host_os": system,
        "stores_credentials": False,
        "credential_policy": "El MCP no guarda login, password ni servidor; se conecta al terminal local ya configurado.",
        "mt5": {
            "installed": mt5_terminal.exists(),
            "direct_api_ready": mt5_direct_ready,
            "terminal": str(mt5_terminal),
            "wine_bin": _path_status(WINE_BIN),
            "wine_python": _path_status(WINE_PYTHON),
            "bridge_dir": str(BRIDGE_DIR),
        },
        "mt4": {
            "installed": mt4_terminal.exists(),
            "bridge_ready": mt4_bridge_ready,
            "terminal": str(mt4_terminal),
            "wine_bin": _path_status(MT4_WINE_BIN),
            "bridge_dir": str(MT4_BRIDGE_DIR),
            "bridge_ea": str(mt4_bridge_ea),
            "note": "MT4 usa EA bridge de archivos; debe estar cargado en un chart con AutoTrading activo.",
        },
        "linux_setup": {
            "supported": True,
            "env_overrides": [
                "MCP_MT5_WINEPREFIX", "MCP_MT5_WINE_BIN", "MCP_MT5_WINE_PYTHON",
                "MCP_MT4_WINEPREFIX", "MCP_MT4_WINE_BIN", "MCP_MT4_TERMINAL_DIR", "MCP_MT4_BRIDGE_DIR",
            ],
        },
    }


def _bridge_paths(platform_name: str) -> Tuple[Path, Path, Path, Path]:
    if platform_name.lower() == "mt4":
        bridge_dir = MT4_BRIDGE_DIR
    else:
        bridge_dir = BRIDGE_DIR
    return (
        bridge_dir,
        bridge_dir / "orders.txt",
        bridge_dir / "results.txt",
        bridge_dir / "bridge.lock",
    )


def _send_file_bridge(platform_name: str, cmd: str, timeout: float = 10.0) -> str:
    bridge_dir, order_file, result_file, lock_file = _bridge_paths(platform_name)
    bridge_dir.mkdir(parents=True, exist_ok=True)
    (bridge_dir / "data").mkdir(parents=True, exist_ok=True)
    with open(lock_file, "w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        order_file.write_text("")
        if result_file.exists():
            result_file.unlink()

        with open(order_file, "w") as order:
            order.write(cmd)
            order.flush()
            os.fsync(order.fileno())

        start = time.time()
        while time.time() - start < timeout:
            if result_file.exists():
                result = result_file.read_text(errors="replace").strip()
                if result:
                    result_file.unlink(missing_ok=True)
                    order_file.write_text("")
                    return result
            time.sleep(0.2)

        order_file.write_text("")
        raise BridgeError(f"{platform_name} bridge timeout for command: {cmd.split('|', 1)[0]}")


def _read_text_auto(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith(b"\xff\xfe") or raw.count(b"\x00") > max(8, len(raw) // 10):
        return raw.decode("utf-16le", errors="replace").replace("\r", "")
    return raw.decode("utf-8", errors="replace").replace("\r", "")


def _log_text() -> str:
    logs = sorted(glob.glob(str(BOTTLE / "MQL5/Logs/*.log")))
    if not logs:
        return ""
    try:
        return _read_text_auto(Path(logs[-1]))
    except Exception:
        return ""


def _last(pat: str, txt: str, default: str = "—") -> str:
    matches = re.findall(pat, txt)
    return matches[-1] if matches else default


def _send_bridge(cmd: str, timeout: float = 10.0) -> str:
    BRIDGE_DIR.mkdir(parents=True, exist_ok=True)
    (BRIDGE_DIR / "data").mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        ORDER_FILE.write_text("")
        if RESULT_FILE.exists():
            RESULT_FILE.unlink()

        with open(ORDER_FILE, "w") as order:
            order.write(cmd)
            order.flush()
            os.fsync(order.fileno())

        start = time.time()
        while time.time() - start < timeout:
            if RESULT_FILE.exists():
                result = RESULT_FILE.read_text(errors="replace").strip()
                if result:
                    RESULT_FILE.unlink(missing_ok=True)
                    ORDER_FILE.write_text("")
                    time.sleep(0.4)
                    return result
            time.sleep(0.2)

        ORDER_FILE.write_text("")
        raise BridgeError(f"bridge timeout for command: {cmd.split('|', 1)[0]}")


def _bridge_age_seconds() -> Optional[float]:
    try:
        return time.time() - BRIDGE_LOG_FILE.stat().st_mtime
    except FileNotFoundError:
        return None


def _parse_account(raw: str) -> Dict[str, Any]:
    parts = raw.split("|")
    if len(parts) < 9 or parts[0] != "ACCOUNT":
        raise BridgeError(raw)
    login = parts[1]
    return {
        "login_masked": "***" + login[-4:],
        "balance": float(parts[2]),
        "equity": float(parts[3]),
        "margin": float(parts[4]),
        "free_margin": float(parts[5]),
        "profit": float(parts[6]),
        "leverage": int(parts[7]),
        "currency": parts[8],
        "server": parts[9] if len(parts) > 9 else "",
    }


def _parse_price(raw: str) -> Dict[str, Any]:
    parts = raw.split("|")
    if len(parts) < 5 or parts[0] != "PRICE":
        raise BridgeError(raw)
    return {
        "symbol": parts[1],
        "bid": float(parts[2]),
        "ask": float(parts[3]),
        "spread_points": int(parts[4]),
        "time_msc": int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else None,
    }


def _parse_symbols(raw: str) -> Dict[str, Any]:
    parts = raw.split("|")
    if len(parts) < 2 or parts[0] != "SYMBOLS":
        raise BridgeError(raw)
    return {"count": int(parts[1]), "symbols": parts[2:]}


def _parse_rates(raw: str) -> Dict[str, Any]:
    parts = raw.split("|")
    if len(parts) < 4 or parts[0] != "RATES":
        raise BridgeError(raw)
    candles = []
    for row in parts[4:]:
        cols = row.split(",")
        if len(cols) != 7:
            continue
        candles.append({
            "time": int(cols[0]),
            "open": float(cols[1]),
            "high": float(cols[2]),
            "low": float(cols[3]),
            "close": float(cols[4]),
            "tick_volume": int(cols[5]),
            "spread": int(cols[6]),
        })
    return {"symbol": parts[1], "timeframe": parts[2], "count": int(parts[3]), "candles": candles}


def _parse_positions(raw: str) -> Dict[str, Any]:
    parts = raw.split("|")
    if len(parts) < 2 or parts[0] != "POSITIONS":
        raise BridgeError(raw)

    positions = []
    total_pnl = 0.0
    try:
        total_pnl = float(parts[-1])
    except ValueError:
        pass

    idx = 2
    while idx + 9 <= len(parts) - 1:
        try:
            positions.append({
                "ticket": int(parts[idx]),
                "symbol": parts[idx + 1],
                "type": "BUY" if parts[idx + 2] == "0" else "SELL",
                "volume": float(parts[idx + 3]),
                "open_price": float(parts[idx + 4]),
                "stop_loss": float(parts[idx + 5]),
                "take_profit": float(parts[idx + 6]),
                "profit": float(parts[idx + 7]),
                "magic": int(parts[idx + 8]),
                "comment": parts[idx + 9],
            })
            idx += 10
        except (ValueError, IndexError):
            break

    return {"count": int(parts[1]), "total_pnl": total_pnl, "positions": positions}


def _parse_check(raw: str) -> Dict[str, Any]:
    parts = raw.split("|")
    if len(parts) < 4 or parts[0] != "CHECK":
        raise BridgeError(raw)
    data = {"raw": raw, "status": parts[1], "retcode": parts[2], "comment": parts[3]}
    for part in parts[4:]:
        if "=" in part:
            key, value = part.split("=", 1)
            try:
                data[key] = float(value)
            except ValueError:
                data[key] = value
    return data


def _intel_import(name):
    """Lazy-import a function from the intelligence module."""
    try:
        mod = sys.modules.get("mt5_mcp_intelligence")
        if mod is None:
            import importlib
            mod = importlib.import_module("mt5_mcp_intelligence")
        return getattr(mod, name, None)
    except Exception:
        return None


def _guard_live_order(order_type: str, symbol: str, volume: float, sl: float, tp: float) -> Dict[str, Any]:
    sym = _fix_sym(symbol)
    account = _mt5_direct({"action": "account"})
    price = _mt5_direct({"action": "price", "symbol": sym})
    positions = _mt5_direct({"action": "positions"})
    check = _mt5_direct({
        "action": "check_order",
        "symbol": sym,
        "type": order_type.upper(),
        "volume": volume,
        "stop_loss": sl,
        "take_profit": tp,
        "comment": "mcp_preflight",
    })

    reasons = []
    warnings = []

    # ── Hard limits ──
    if volume > MAX_LIVE_VOLUME:
        reasons.append(f"volume {volume:.2f} > max_live_volume {MAX_LIVE_VOLUME:.2f}")
    if positions.get("count", 0) >= MAX_OPEN_POSITIONS:
        reasons.append(f"open_positions {positions['count']} >= limit {MAX_OPEN_POSITIONS}")
    spread_points = int(price.get("spread", 0) or 0)
    if spread_points > MAX_SPREAD_POINTS:
        reasons.append(f"spread {spread_points} points > limit {MAX_SPREAD_POINTS}")
    daily_reason = _guard_daily_risk()
    if daily_reason:
        reasons.append(daily_reason)
    check_result = check.get("result", {})
    retcode = int(check_result.get("retcode", 0) or 0)
    if retcode not in (0, 10009):
        reasons.append(f"OrderCheck retcode={retcode}: {check_result.get('comment')}")

    margin = float(check_result.get("margin", 0.0) or 0.0)
    free = float(account.get("margin_free", account.get("free_margin", 0.0)) or 0.0)
    equity = float(account.get("equity", 0.0) or 0.0)
    margin_use_pct = (margin / free * 100.0) if free > 0 else 999.0
    post_free_pct = ((free - margin) / equity * 100.0) if equity > 0 else 0.0

    if margin_use_pct > MAX_MARGIN_USE_PCT:
        reasons.append(f"margin use {margin_use_pct:.1f}% > limit {MAX_MARGIN_USE_PCT:.1f}%")
    if post_free_pct < MIN_POST_TRADE_FREE_MARGIN_PCT:
        reasons.append(f"post-trade free margin {post_free_pct:.1f}% < limit {MIN_POST_TRADE_FREE_MARGIN_PCT:.1f}%")

    # ── Intelligence guard: news check ──
    try:
        news_fn = _intel_import("news_check")
        if news_fn:
            news = news_fn()
        else:
            news = _intelligence_tools.get("news_check", (None,))[0]({}) if "news_check" in _intelligence_tools else {}
        if isinstance(news, dict) and news.get("has_event") and news.get("within_2h"):
            reasons.append(f"high-impact news nearby: {[e.get('name','?') for e in news.get('events',[])]}")
    except Exception:
        pass

    # ── Intelligence guard: session quality ──
    try:
        ses_fn = _intel_import("market_sessions")
        if ses_fn:
            ses = ses_fn()
        else:
            ses = _intelligence_tools.get("market_sessions", (None,))[0]({}) if "market_sessions" in _intelligence_tools else {}
        if isinstance(ses, dict):
            quality = ses.get("quality", 1.0)
            if quality < 0.5:
                reasons.append(f"low liquidity session (quality={quality:.0%}), active: {ses.get('active_sessions', [])}")
            elif quality < 0.8:
                warnings.append(f"reduced liquidity (quality={quality:.0%})")
    except Exception:
        pass

    # ── Intelligence guard: stop-hunting / manipulation ──
    try:
        man_fn = _intel_import("analyze_manipulation")
        if man_fn:
            man = man_fn(_mt5_direct, sym.replace(".FX", ""))
        else:
            man = _intelligence_tools.get("antimanipulation_analyze", (None,))[0]({"symbol": sym.replace(".FX", "")}) if "antimanipulation_analyze" in _intelligence_tools else {}
        if isinstance(man, dict):
            if man.get("stop_hunting_risk") == "high":
                reasons.append(f"high stop-hunting risk detected ({man.get('suspicious_spikes',0)} suspicious spikes)")
            elif man.get("stop_hunting_risk") == "medium":
                warnings.append(f"medium stop-hunting risk ({man.get('suspicious_spikes',0)} spikes)")
    except Exception:
        pass

    # ── Intelligence guard: regime adaptation ──
    try:
        reg_fn = _intel_import("regime_detect")
        if reg_fn:
            regime = reg_fn(_mt5_direct, sym.replace(".FX", ""), "H1", 14)
        else:
            regime = _intelligence_tools.get("regime_detect", (None,))[0]({"symbol": sym.replace(".FX", "")}) if "regime_detect" in _intelligence_tools else {}
        if isinstance(regime, dict):
            r = regime.get("regime", "unknown")
            if r == "volatile":
                reasons.append(f"volatile market regime — avoid new entries")
            elif r == "quiet":
                warnings.append(f"quiet market — reduce size expectations")
            else:
                warnings.append(f"market regime: {r}")
    except Exception:
        pass

    # ── Intelligence guard: correlation overexposure ──
    try:
        corr_fn = _intel_import("correlation_report")
        if corr_fn:
            corr = corr_fn()
        else:
            corr = _intelligence_tools.get("correlation_analyze", (None,))[0]({}) if "correlation_analyze" in _intelligence_tools else {}
        if isinstance(corr, dict):
            correlated = corr.get("correlated_pairs", [])
            if correlated and positions.get("count", 0) > 0:
                base_sym = sym.replace(".FX", "")
                for p in positions.get("positions", []):
                    psym = p.get("symbol", "").replace(".FX", "")
                    ptype = "BUY" if p.get("type") in (0, "POSITION_TYPE_BUY") else "SELL"
                    for entry in correlated:
                        if isinstance(entry, dict) and entry.get("pair1") == base_sym and entry.get("pair2") == psym:
                            corr_val = entry.get("correlation", 0)
                            if abs(corr_val) > 0.7 and order_type == ptype:
                                warnings.append(f"high correlation {base_sym}/{psym}={corr_val:.2f} — overexposed {order_type}")
                            break
    except Exception:
        pass

    result = {
        "allowed": not reasons,
        "reasons": reasons,
        "warnings": warnings,
        "account": account,
        "price": price,
        "positions": positions,
        "check": check,
        "margin_use_pct": round(margin_use_pct, 2),
        "post_trade_free_margin_pct": round(post_free_pct, 2),
        "limits": {
            "max_live_volume": MAX_LIVE_VOLUME,
            "max_open_positions": MAX_OPEN_POSITIONS,
            "max_spread_points": MAX_SPREAD_POINTS,
            "max_margin_use_pct": MAX_MARGIN_USE_PCT,
            "min_post_trade_free_margin_pct": MIN_POST_TRADE_FREE_MARGIN_PCT,
        },
    }
    return result


def tool_account(args: Dict[str, Any]) -> Dict[str, Any]:
    return _mt5_direct({"action": "account"})


def tool_price(args: Dict[str, Any]) -> Dict[str, Any]:
    return _mt5_direct({"action": "price", "symbol": args.get("symbol", "EURUSD.FX")})


def tool_symbols(args: Dict[str, Any]) -> Dict[str, Any]:
    pattern = str(args.get("pattern", "") or "")
    return _mt5_direct({"action": "symbols", "pattern": pattern})


def tool_candles(args: Dict[str, Any]) -> Dict[str, Any]:
    symbol = args["symbol"]
    timeframe = str(args.get("timeframe", "M1")).upper()
    count = int(args.get("count", 100))
    return _mt5_direct({"action": "candles", "symbol": symbol, "timeframe": timeframe, "count": count})


def tool_positions(args: Dict[str, Any]) -> Dict[str, Any]:
    return _mt5_direct({"action": "positions", "symbol": args.get("symbol"), "ticket": args.get("ticket")})


def tool_orders(args: Dict[str, Any]) -> Dict[str, Any]:
    return _mt5_direct({"action": "orders"})


def tool_history(args: Dict[str, Any]) -> Dict[str, Any]:
    return _mt5_direct({"action": "history", "symbol": args.get("symbol"), "days": int(args.get("days", 30))})


def tool_status(args: Dict[str, Any]) -> Dict[str, Any]:
    age = _bridge_age_seconds()
    direct_ok = False
    direct_error = None
    try:
        _mt5_direct({"action": "account"}, timeout=15.0)
        direct_ok = True
    except Exception as exc:
        direct_error = str(exc)
    return {
        "mode": "direct_mt5_wine",
        "direct_mt5_ok": direct_ok,
        "direct_mt5_error": direct_error,
        "stores_credentials": False,
        "login_is_masked": True,
        "bridge_fallback_dir": str(BRIDGE_DIR),
        "bridge_log_age_seconds": age,
    }


def tool_metatrader_platforms(args: Dict[str, Any]) -> Dict[str, Any]:
    return _detect_platforms()


def _platform_name(args: Dict[str, Any]) -> str:
    platform_name = str(args.get("platform", "mt5")).lower()
    if platform_name not in ("mt4", "mt5"):
        raise BridgeError("platform must be mt4 or mt5")
    return platform_name


def tool_mt_account(args: Dict[str, Any]) -> Dict[str, Any]:
    platform_name = _platform_name(args)
    if platform_name == "mt5":
        return tool_account(args)
    return _parse_account(_send_file_bridge("mt4", "ACCOUNT", timeout=10.0))


def tool_mt_price(args: Dict[str, Any]) -> Dict[str, Any]:
    platform_name = _platform_name(args)
    symbol = args.get("symbol", "EURUSD")
    if platform_name == "mt5":
        return tool_price({"symbol": symbol})
    return _parse_price(_send_file_bridge("mt4", f"PRICE|{symbol}", timeout=10.0))


def tool_mt_positions(args: Dict[str, Any]) -> Dict[str, Any]:
    platform_name = _platform_name(args)
    if platform_name == "mt5":
        return tool_positions(args)
    return _parse_positions(_send_file_bridge("mt4", "POSITIONS", timeout=10.0))


def tool_mt_candles(args: Dict[str, Any]) -> Dict[str, Any]:
    platform_name = _platform_name(args)
    symbol = args["symbol"]
    timeframe = str(args.get("timeframe", "M1")).upper()
    count = int(args.get("count", 100))
    if platform_name == "mt5":
        return tool_candles({"symbol": symbol, "timeframe": timeframe, "count": count})
    return _parse_rates(_send_file_bridge("mt4", f"RATES|{symbol}|{timeframe}|{count}", timeout=10.0))


def tool_mt_check_order(args: Dict[str, Any]) -> Dict[str, Any]:
    platform_name = _platform_name(args)
    order_type = args["type"].upper()
    symbol = args["symbol"]
    volume = float(args.get("volume", 0.01))
    sl = float(args.get("stop_loss", 0.0) or 0.0)
    tp = float(args.get("take_profit", 0.0) or 0.0)
    if platform_name == "mt5":
        return tool_check_order({"symbol": symbol, "type": order_type, "volume": volume, "stop_loss": sl, "take_profit": tp})
    cmd = f"CHECK{order_type}|{symbol}|{volume}|{sl}|{tp}|mcp_preflight"
    return _parse_check(_send_file_bridge("mt4", cmd, timeout=10.0))


def tool_mt_place_market_order(args: Dict[str, Any]) -> Dict[str, Any]:
    platform_name = _platform_name(args)
    order_type = args["type"].upper()
    symbol = args["symbol"]
    volume = float(args.get("volume", 0.01))
    sl = float(args.get("stop_loss", 0.0) or 0.0)
    tp = float(args.get("take_profit", 0.0) or 0.0)
    confirm_live = bool(args.get("confirm_live", False))
    dry_run = bool(args.get("dry_run", not confirm_live))
    if platform_name == "mt5":
        return tool_place_market_order(args)

    check = tool_mt_check_order({"platform": "mt4", "symbol": symbol, "type": order_type, "volume": volume, "stop_loss": sl, "take_profit": tp})
    if dry_run or not confirm_live:
        return {"executed": False, "dry_run": True, "needs_confirm_live": True, "check": check}
    if volume > MAX_LIVE_VOLUME:
        return {"executed": False, "error": "risk_guard_blocked", "reason": f"volume {volume:.2f} > max_live_volume {MAX_LIVE_VOLUME:.2f}", "check": check}

    raw = _send_file_bridge("mt4", f"{order_type}|{symbol}|{volume}|{sl}|{tp}|mcp_live", timeout=20.0)
    return {"executed": raw.startswith("OK|"), "raw": raw, "check": check}


def tool_mt_close_position(args: Dict[str, Any]) -> Dict[str, Any]:
    platform_name = _platform_name(args)
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    if platform_name == "mt5":
        return tool_close_position(args)
    raw = _send_file_bridge("mt4", f"CLOSETICKET|{int(args['ticket'])}", timeout=20.0)
    return {"executed": raw.startswith("OK|"), "raw": raw}


def tool_mt_close_all(args: Dict[str, Any]) -> Dict[str, Any]:
    platform_name = _platform_name(args)
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    if platform_name == "mt5":
        return tool_close_all(args)
    mode = str(args.get("mode", "all")).lower()
    if mode not in ("all", "profitable", "losing"):
        return {"executed": False, "error": "mode must be all, profitable, or losing"}
    raw = _send_file_bridge("mt4", f"CLOSEALL|{mode}", timeout=20.0)
    return {"executed": raw.startswith("OK|"), "raw": raw}


def tool_activity(args: Dict[str, Any]) -> Dict[str, Any]:
    text = _log_text()
    return {
        "signals_generated": len(re.findall(r"GetSignal=[1-9-]", text)),
        "best_entries": len(re.findall(r"BEST ENTRY", text)),
        "open_results": len(re.findall(r"OpenLevel result=1", text)),
        "cantrade_blocks": len(re.findall(r"CANTRADE BLOCK", text)),
        "last_block": _last(r"CANTRADE BLOCK: ([^\n]+)", text),
    }


def tool_check_order(args: Dict[str, Any]) -> Dict[str, Any]:
    order_type = args["type"].upper()
    symbol = args["symbol"]
    volume = float(args.get("volume", 0.01))
    sl = float(args.get("stop_loss", 0.0) or 0.0)
    tp = float(args.get("take_profit", 0.0) or 0.0)
    return _guard_live_order(order_type, symbol, volume, sl, tp)


def tool_scan_strategy(args: Dict[str, Any]) -> Dict[str, Any]:
    cmd = {
        "action": "scan_strategy",
        "symbols": args.get("symbols") or [],
        "volume": float(args.get("volume", MAX_LIVE_VOLUME)),
        "auto_min_volume": bool(args.get("auto_min_volume", False)),
        "max_volume": float(args.get("max_volume", MAX_LIVE_VOLUME)),
        "max_positions": int(args.get("max_positions", MAX_OPEN_POSITIONS)),
        "max_spread_points": int(args.get("max_spread_points", MAX_SPREAD_POINTS)),
        "max_margin_use_pct": float(args.get("max_margin_use_pct", 35.0)),
        "min_post_trade_free_margin_pct": float(args.get("min_post_trade_free_margin_pct", 55.0)),
        "max_risk_usd": float(args.get("max_risk_usd", 0.22)),
        "min_score": float(args.get("min_score", 70.0)),
        "reward_risk": float(args.get("reward_risk", 1.4)),
        "limit": int(args.get("limit", 5)),
    }
    return _mt5_direct(cmd, timeout=120.0)


def tool_place_market_order(args: Dict[str, Any]) -> Dict[str, Any]:
    order_type = args["type"].upper()
    symbol = args["symbol"]
    volume = float(args.get("volume", 0.01))
    sl = float(args.get("stop_loss", 0.0) or 0.0)
    tp = float(args.get("take_profit", 0.0) or 0.0)
    confirm_live = bool(args.get("confirm_live", False))
    dry_run = bool(args.get("dry_run", not confirm_live))

    guard = _guard_live_order(order_type, symbol, volume, sl, tp)
    if dry_run or not confirm_live:
        return {
            "executed": False,
            "dry_run": True,
            "needs_confirm_live": True,
            "guard": guard,
        }
    if not guard["allowed"]:
        return {"executed": False, "error": "risk_guard_blocked", "guard": guard}

    result = _mt5_direct({
        "action": "send_order",
        "symbol": symbol,
        "type": order_type,
        "volume": volume,
        "stop_loss": sl,
        "take_profit": tp,
        "comment": "mcp_live",
    })
    return {"executed": bool(result.get("success")), "result": result, "guard": guard}


def tool_place_pending_order(args: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    result = _mt5_direct({
        "action": "send_pending_order",
        "symbol": args["symbol"],
        "type": args["type"].upper(),
        "volume": float(args.get("volume", 0.01)),
        "price": float(args["price"]),
        "stop_loss": float(args.get("stop_loss", 0.0) or 0.0),
        "take_profit": float(args.get("take_profit", 0.0) or 0.0),
        "comment": "mcp_pending",
    })
    return {"executed": bool(result.get("success")), "result": result}


def tool_cancel_pending_order(args: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    result = _mt5_direct({"action": "cancel_pending_order", "ticket": int(args["ticket"])})
    return {"executed": bool(result.get("success")), "result": result}


def tool_modify_position(args: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    ticket = int(args["ticket"])
    sl = args.get("stop_loss", "")
    tp = args.get("take_profit", "")
    result = _mt5_direct({"action": "modify_position", "ticket": ticket, "stop_loss": sl, "take_profit": tp})
    return {"executed": bool(result.get("success")), "result": result}


def tool_close_position(args: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    ticket = int(args["ticket"])
    result = _mt5_direct({"action": "close_position", "ticket": ticket})
    return {"executed": bool(result.get("success")), "result": result}


def tool_close_symbol(args: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    positions = _mt5_direct({"action": "positions", "symbol": args["symbol"]})
    results = []
    for pos in positions.get("positions", []):
        results.append(_mt5_direct({"action": "close_position", "ticket": pos["ticket"]}))
    return {"executed": all(bool(r.get("success")) for r in results), "results": results}


def tool_close_all(args: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    mode = str(args.get("mode", "all")).lower()
    if mode not in ("all", "profitable", "losing"):
        return {"executed": False, "error": "mode must be all, profitable, or losing"}
    result = _mt5_direct({"action": "close_all", "mode": mode})
    return {"executed": all(bool(r.get("success")) for r in result.get("results", [])), "result": result}


def tool_brain(args: Dict[str, Any]) -> Dict[str, Any]:
    text = _log_text()
    match = re.findall(r"INFINITY BRAIN cargado: ([0-9,]+) trades históricos \| WR=([0-9.]+%)", text)
    if match:
        return {"trades_in_ram": match[-1][0], "win_rate": match[-1][1]}
    return {"trades_in_ram": "—", "win_rate": "—"}


def tool_progress(args: Dict[str, Any]) -> Dict[str, Any]:
    text = _log_text()
    wins = len(re.findall(r"CIERRE.*?net[= ]\$?([0-9.]+)", text))
    target = 100_000
    return {
        "winning_closes": wins,
        "target": target,
        "progress_pct": round(100 * wins / target, 4),
        "next_milestone": next((h for h in [100, 1000, 10000, 100000] if h > wins), target),
    }


def tool_strategy_save(args: Dict[str, Any]) -> Dict[str, Any]:
    name = str(args["name"]).strip()
    if not name:
        return {"saved": False, "error": "name required"}

    strategies = _load_strategies()
    strategies[name] = {
        "name": name,
        "description": args.get("description", ""),
        "symbols": args.get("symbols", []),
        "timeframes": args.get("timeframes", []),
        "entry_rules": args.get("entry_rules", []),
        "exit_rules": args.get("exit_rules", []),
        "risk": args.get("risk", {}),
        "filters": args.get("filters", {}),
        "notes": args.get("notes", ""),
        "updated_at": int(time.time()),
    }
    _save_strategies(strategies)
    return {"saved": True, "name": name, "count": len(strategies), "stores_personal_account_data": False}


def tool_strategy_list(args: Dict[str, Any]) -> Dict[str, Any]:
    strategies = _load_strategies()
    return {
        "count": len(strategies),
        "strategies": [
            {
                "name": name,
                "description": data.get("description", ""),
                "symbols": data.get("symbols", []),
                "timeframes": data.get("timeframes", []),
                "updated_at": data.get("updated_at"),
            }
            for name, data in strategies.items()
        ],
    }


def tool_strategy_get(args: Dict[str, Any]) -> Dict[str, Any]:
    name = str(args["name"]).strip()
    strategies = _load_strategies()
    if name not in strategies:
        return {"found": False, "name": name}
    return {"found": True, "strategy": strategies[name]}


def tool_partial_close(args: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    ticket = int(args["ticket"])
    close_pct = float(args.get("close_pct", 50.0))
    if close_pct <= 0 or close_pct > 100:
        return {"executed": False, "error": "close_pct must be 1-100"}
    pos = _mt5_direct({"action": "positions"})
    target = None
    for p in pos.get("positions", []):
        if p["ticket"] == ticket:
            target = p
            break
    if not target:
        return {"executed": False, "error": "position not found"}
    close_vol = round(target["volume"] * close_pct / 100, 8)
    if close_vol <= 0:
        return {"executed": False, "error": "volume too small to close"}
    result = _mt5_direct({
        "action": "close_position", "ticket": ticket
    })
    return {"executed": bool(result.get("success")), "close_volume": close_vol, "remaining_pct": 100 - close_pct, "result": result}


def tool_close_by_type(args: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    order_type = args["type"].upper()
    if order_type not in ("BUY", "SELL"):
        return {"executed": False, "error": "type must be BUY or SELL"}
    positions = _mt5_direct({"action": "positions"})
    results = []
    for p in positions.get("positions", []):
        if p["type"] == order_type:
            results.append(_mt5_direct({"action": "close_position", "ticket": p["ticket"]}))
    return {"executed": all(bool(r.get("success")) for r in results), "closed": len(results), "results": results}


def tool_breakeven_all(args: Dict[str, Any]) -> Dict[str, Any]:
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    positions = _mt5_direct({"action": "positions"})
    results = []
    for p in positions.get("positions", []):
        new_sl = p["open_price"]
        res = _mt5_direct({"action": "modify_position", "ticket": p["ticket"],
                           "stop_loss": new_sl, "take_profit": p.get("take_profit", "")})
        results.append(res)
    return {"executed": all(bool(r.get("success")) for r in results), "modified": len(results), "results": results}


def tool_daily_report(args: Dict[str, Any]) -> Dict[str, Any]:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    start_of_day = int(datetime.now(timezone.utc).replace(hour=0, minute=0, second=0).timestamp())
    try:
        hist = _mt5_direct({"action": "history", "days": 1})
    except Exception:
        hist = {"deals": []}
    daily_pnl = sum(float(d.get("profit", 0)) for d in hist.get("deals", []))
    acct = _mt5_direct({"action": "account"})
    return {"date": today, "daily_pnl": round(daily_pnl, 2),
            "balance": acct.get("balance"), "equity": acct.get("equity")}


JOURNAL_FILE = os.path.join(os.path.dirname(__file__), "data", "trade_journal.json")
def tool_trade_journal(args: Dict[str, Any]) -> Dict[str, Any]:
    limit = min(int(args.get("limit", 50)), 500)
    if not os.path.exists(JOURNAL_FILE):
        return {"trades": [], "count": 0}
    try:
        with open(JOURNAL_FILE) as f:
            trades = json.load(f)
    except Exception:
        trades = []
    return {"trades": trades[-limit:], "count": len(trades), "total_pnl": round(sum(t.get("pnl", 0) for t in trades), 2)}


def tool_scanner_fix(args: Dict[str, Any]) -> Dict[str, Any]:
    """Scanner ajustado para tastyfx: max_spread_points=80, max_risk_usd=0.50, min_score=40"""
    return _mt5_direct({
        "action": "scan_strategy",
        "symbols": args.get("symbols", []),
        "volume": float(args.get("volume", 0.01)),
        "max_spread_points": int(args.get("max_spread_points", 80)),
        "max_risk_usd": float(args.get("max_risk_usd", 0.50)),
        "min_score": float(args.get("min_score", 40)),
        "max_volume": float(args.get("max_volume", 0.01)),
        "max_positions": int(args.get("max_positions", 1)),
        "reward_risk": float(args.get("reward_risk", 1.5)),
        "limit": int(args.get("limit", 5)),
    })


def tool_auto_trade(args: Dict[str, Any]) -> Dict[str, Any]:
    """ONE-SHOT: scan + conviction + guard + execute.
    Escanea todos los símbolos, aplica el sistema completo de inteligencia,
    verifica guardias y ejecuta el mejor trade. Un solo comando para ganar."""
    symbols = args.get("symbols", ["EURUSD","GBPUSD","USDJPY","USDCAD","AUDUSD"])
    dry_run = bool(args.get("dry_run", True))
    confirm_live = bool(args.get("confirm_live", False))
    results = []

    for sym in symbols:
        try:
            result = {"symbol": sym, "tradeable": False}
            # 1. Anomaly check
            from mt5_mcp_intelligence import anomaly_detect
            try:
                anom = anomaly_detect(_mt5_direct, sym)
                if anom.get("anomalous"):
                    result["reject_reason"] = f"anomaly_score={anom.get('anomaly_score')}"
                    results.append(result)
                    continue
            except Exception:
                pass

            # 2. Conviction
            from mt5_mcp_intelligence import conviction_decide
            try:
                dec = conviction_decide(_mt5_direct, sym)
                if not dec.get("success"):
                    result["reject_reason"] = "conviction_failed"
                    results.append(result)
                    continue
                d = dec["decision"]
                if d["verdict"] == "PASS" or d["confidence_pct"] < int(args.get("min_confidence", 60)):
                    result["reject_reason"] = f"conviction_{d['verdict']}_{d['confidence_pct']}%"
                    results.append(result)
                    continue
            except Exception as e:
                result["reject_reason"] = f"conviction_error:{str(e)[:40]}"
                results.append(result)
                continue

            # 3. Ensemble vote
            from mt5_mcp_intelligence import ensemble_vote
            try:
                ens = ensemble_vote(_mt5_direct, sym)
                if ens.get("ensemble_verdict") != d["verdict"]:
                    result["reject_reason"] = f"ensemble_mismatch:{ens.get('ensemble_verdict')}"
                    results.append(result)
                    continue
            except Exception:
                pass

            # 4. Price + spread check
            price_info = _mt5_direct({"action": "price", "symbol": _fix_sym(sym)})
            spread = price_info.get("spread", 999)
            max_spread = int(args.get("max_spread_points", 80))
            if spread > max_spread:
                result["reject_reason"] = f"spread_{spread}>{max_spread}"
                results.append(result)
                continue

            order_type = "BUY" if "BUY" in d["verdict"] else "SELL"
            volume = float(args.get("volume", 0.01))

            # 5. Preflight check
            try:
                check = _mt5_direct({
                    "action": "check_order",
                    "symbol": _fix_sym(sym),
                    "type": order_type,
                    "volume": volume,
                })
                retcode = check.get("result", {}).get("retcode", -1)
                if retcode not in (0, 10009):
                    result["reject_reason"] = f"ordercheck_{retcode}"
                    results.append(result)
                    continue
            except Exception as e:
                result["reject_reason"] = f"check_error:{str(e)[:40]}"
                results.append(result)
                continue

            # 6. Session check
            from mt5_mcp_intelligence import market_sessions
            try:
                ses = market_sessions()
                if ses.get("quality", 0) < 0.5:
                    result["reject_reason"] = f"low_session_{ses.get('quality')}"
                    results.append(result)
                    continue
            except Exception:
                pass

            # 7. Execute
            result.update({
                "tradeable": True,
                "decision": d["verdict"],
                "confidence": d["confidence_pct"],
                "direction": order_type,
                "volume": volume,
                "spread": spread,
            })
            if not dry_run and confirm_live:
                try:
                    exec_result = _mt5_direct({
                        "action": "send_order",
                        "symbol": _fix_sym(sym),
                        "type": order_type,
                        "volume": volume,
                        "comment": "mcp_auto",
                    })
                    result["executed"] = bool(exec_result.get("success"))
                    result["ticket"] = exec_result.get("ticket")
                except Exception as e:
                    result["executed"] = False
                    result["error"] = str(e)[:60]
            else:
                result["executed"] = False
                result["dry_run"] = dry_run
                result["needs_confirm_live"] = True

            results.append(result)
        except Exception as e:
            results.append({"symbol": sym, "tradeable": False, "error": str(e)[:60]})

    # Best trade
    trades = [r for r in results if r.get("tradeable")]
    best = max(trades, key=lambda r: r.get("confidence", 0)) if trades else None
    return {
        "scanned": len(symbols),
        "tradeable": len(trades),
        "best_trade": best,
        "results": results,
        "dry_run": dry_run,
        "note": "Set dry_run=false and confirm_live=true to execute real trades",
    }


def _pip_info(symbol):
    """Get pip size and digit info for a symbol."""
    try:
        p = _mt5_direct({"action": "price", "symbol": _fix_sym(symbol)})
        digits = p.get("digits", 5)
        point = p.get("point", 0.00001)
        pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
        return pip_size, digits, point, p.get("bid", 0), p.get("ask", 0)
    except Exception:
        return 0.0001, 5, 0.00001, 0, 0


def tool_quick_buy(args: Dict[str, Any]) -> Dict[str, Any]:
    """Compra rápida con SL/TP en pips. Un solo comando."""
    symbol = args["symbol"]
    volume = float(args.get("volume", 0.01))
    sl_pips = float(args.get("sl_pips", 0))
    tp_pips = float(args.get("tp_pips", 0))
    confirm_live = bool(args.get("confirm_live", False))
    dry_run = not confirm_live

    pip_size, digits, point, bid, ask = _pip_info(symbol)
    if ask == 0:
        return {"executed": False, "error": "no price for symbol"}

    sl = round(ask - sl_pips * pip_size, digits) if sl_pips > 0 else 0.0
    tp = round(ask + tp_pips * pip_size, digits) if tp_pips > 0 else 0.0

    guard = _guard_live_order("BUY", symbol, volume, sl, tp)
    if dry_run:
        return {"executed": False, "dry_run": True, "needs_confirm_live": True,
                "info": {"symbol": symbol, "type": "BUY", "volume": volume,
                         "entry": round(ask, digits), "sl": sl, "tp": tp,
                         "sl_pips": sl_pips, "tp_pips": tp_pips, "spread": guard.get("price",{}).get("spread",0)},
                "guard": {"allowed": guard.get("allowed"), "reasons": guard.get("reasons", [])}}
    if not guard.get("allowed"):
        return {"executed": False, "error": "risk_guard_blocked", "guard": guard}

    result = _mt5_direct({
        "action": "send_order", "symbol": _fix_sym(symbol),
        "type": "BUY", "volume": volume, "stop_loss": sl, "take_profit": tp,
        "comment": "mcp_quick",
    })
    return {"executed": bool(result.get("success")), "ticket": result.get("ticket"),
            "entry": round(ask, digits), "sl": sl, "tp": tp, "result": result}


def tool_quick_sell(args: Dict[str, Any]) -> Dict[str, Any]:
    """Venta rápida con SL/TP en pips. Un solo comando."""
    symbol = args["symbol"]
    volume = float(args.get("volume", 0.01))
    sl_pips = float(args.get("sl_pips", 0))
    tp_pips = float(args.get("tp_pips", 0))
    confirm_live = bool(args.get("confirm_live", False))
    dry_run = not confirm_live

    pip_size, digits, point, bid, ask = _pip_info(symbol)
    if bid == 0:
        return {"executed": False, "error": "no price for symbol"}

    sl = round(bid + sl_pips * pip_size, digits) if sl_pips > 0 else 0.0
    tp = round(bid - tp_pips * pip_size, digits) if tp_pips > 0 else 0.0

    guard = _guard_live_order("SELL", symbol, volume, sl, tp)
    if dry_run:
        return {"executed": False, "dry_run": True, "needs_confirm_live": True,
                "info": {"symbol": symbol, "type": "SELL", "volume": volume,
                         "entry": round(bid, digits), "sl": sl, "tp": tp,
                         "sl_pips": sl_pips, "tp_pips": tp_pips, "spread": guard.get("price",{}).get("spread",0)},
                "guard": {"allowed": guard.get("allowed"), "reasons": guard.get("reasons", [])}}
    if not guard.get("allowed"):
        return {"executed": False, "error": "risk_guard_blocked", "guard": guard}

    result = _mt5_direct({
        "action": "send_order", "symbol": _fix_sym(symbol),
        "type": "SELL", "volume": volume, "stop_loss": sl, "take_profit": tp,
        "comment": "mcp_quick",
    })
    return {"executed": bool(result.get("success")), "ticket": result.get("ticket"),
            "entry": round(bid, digits), "sl": sl, "tp": tp, "result": result}


def tool_move_to_breakeven(args: Dict[str, Any]) -> Dict[str, Any]:
    """Mueve SL de una posición a precio de entrada."""
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    ticket = int(args["ticket"])
    pos = _mt5_direct({"action": "positions"})
    target = None
    for p in pos.get("positions", []):
        if p["ticket"] == ticket:
            target = p
            break
    if not target:
        return {"executed": False, "error": "position not found"}
    res = _mt5_direct({"action": "modify_position", "ticket": ticket,
                       "stop_loss": target["open_price"],
                       "take_profit": target.get("take_profit", "")})
    return {"executed": bool(res.get("success")), "ticket": ticket,
            "breakeven_price": target["open_price"], "result": res}


def tool_trail_all(args: Dict[str, Any]) -> Dict[str, Any]:
    """Aplica trailing stop a todas las posiciones abiertas."""
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    activation_pips = float(args.get("activation_pips", 15))
    distance_pips = float(args.get("distance_pips", 10))
    positions = _mt5_direct({"action": "positions"})
    results = []
    for p in positions.get("positions", []):
        sym = p["symbol"]
        pip_size = 0.01 if "JPY" in sym.upper() else 0.0001
        bid_ask = _mt5_direct({"action": "price", "symbol": sym})
        bid = bid_ask.get("bid", 0)
        ask = bid_ask.get("ask", 0)
        profit_pips = (bid - p["open_price"]) / pip_size if p["type"] == "BUY" else (p["open_price"] - ask) / pip_size
        if profit_pips >= activation_pips and profit_pips > 0:
            new_sl = bid - distance_pips * pip_size if p["type"] == "BUY" else ask + distance_pips * pip_size
            digits = bid_ask.get("digits", 5)
            new_sl = round(new_sl, digits)
            if (p["type"] == "BUY" and new_sl > p.get("stop_loss", 0)) or \
               (p["type"] == "SELL" and (new_sl < p.get("stop_loss", 0) or p.get("stop_loss", 0) == 0)):
                res = _mt5_direct({"action": "modify_position", "ticket": p["ticket"],
                                   "stop_loss": new_sl, "take_profit": p.get("take_profit", "")})
                results.append({"ticket": p["ticket"], "symbol": sym, "new_sl": new_sl,
                                "profit_pips": round(profit_pips, 1), "success": bool(res.get("success"))})
    return {"trailed": len(results), "results": results}


def tool_account_summary(args: Dict[str, Any]) -> Dict[str, Any]:
    """Resumen completo de la cuenta en lenguaje humano."""
    acct = _mt5_direct({"action": "account"})
    pos = _mt5_direct({"action": "positions"})
    total_pnl = float(pos.get("total_profit", 0) or 0)
    open_count = len(pos.get("positions", []))
    balance = float(acct.get("balance", 0))
    equity = float(acct.get("equity", 0))
    free = float(acct.get("margin_free", acct.get("free_margin", 0)) or 0)
    floating = equity - balance
    daily_pnl = 0
    try:
        hist = _mt5_direct({"action": "history", "days": 1})
        daily_pnl = sum(float(d.get("profit", 0)) for d in hist.get("deals", []))
    except Exception:
        pass
    return {
        "balance": round(balance, 2), "equity": round(equity, 2),
        "margin_free": round(free, 2), "margin_used": round(balance - free, 2),
        "floating_pnl": round(floating, 2), "daily_pnl": round(daily_pnl, 2),
        "open_positions": open_count, "total_open_pnl": round(total_pnl, 2),
        "health": "good" if equity >= balance * 0.95 else ("warning" if equity >= balance * 0.9 else "critical"),
        "currency": acct.get("currency", "USD"),
        "server": acct.get("server", ""),
    }


def tool_best_time_to_trade(args: Dict[str, Any]) -> Dict[str, Any]:
    """Mejor horario para operar cada par según sesiones."""
    from datetime import datetime, timezone
    h = datetime.now(timezone.utc).hour
    pairs = {
        "EURUSD": {"best": "london_ny 12-16", "quality": "high"},
        "GBPUSD": {"best": "london 7-16", "quality": "high"},
        "USDJPY": {"best": "asian 0-9 / london 7-16", "quality": "medium"},
        "USDCAD": {"best": "newyork 12-21", "quality": "medium"},
        "AUDUSD": {"best": "asian 0-9 / london 7-16", "quality": "medium"},
        "NZDUSD": {"best": "asian 0-9", "quality": "medium"},
        "EURJPY": {"best": "london 7-16 / asian 0-9", "quality": "medium"},
        "GBPJPY": {"best": "london 7-16", "quality": "medium"},
    }
    sym = args.get("symbol", "EURUSD").upper()
    info = pairs.get(sym, {"best": "london/ny", "quality": "medium"})
    session = "london_ny" if 12 <= h < 16 else ("london" if 7 <= h < 16 else ("ny" if 12 <= h < 21 else ("asian" if 0 <= h < 9 else "off")))
    good_time = session in info.get("best", "")
    return {"symbol": sym, "current_session": session, "best_session": info["best"],
            "recommended": good_time, "advice": "trade now" if good_time else "wait for better session",
            "current_hour_utc": h}


def tool_market_overview(args: Dict[str, Any]) -> Dict[str, Any]:
    """Vista rápida de todos los pares mayores en una llamada."""
    majors = ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "USDCHF", "AUDUSD", "NZDUSD"]
    pairs = []
    buy_signals = 0
    sell_signals = 0
    for sym in majors:
        try:
            p = _mt5_direct({"action": "price", "symbol": _fix_sym(sym)})
            bid = p.get("bid", 0)
            ask = p.get("ask", 0)
            spread = p.get("spread", 0)
            pip_size = 0.01 if "JPY" in sym else 0.0001
            spread_pips = round(spread * p.get("point", 0.00001) / pip_size, 1)
            pairs.append({
                "symbol": sym, "bid": bid, "ask": ask,
                "spread_pts": spread, "spread_pips": spread_pips,
                "tradeable": spread_pips < 8.0,
            })
        except Exception:
            pairs.append({"symbol": sym, "error": True})
    return {"pairs": pairs, "total": len(majors),
            "scanned_at": datetime.now(timezone.utc).isoformat()}


def tool_pip_value(args: Dict[str, Any]) -> Dict[str, Any]:
    """Calcula el valor monetario de 1 pip para un símbolo y volumen."""
    symbol = args["symbol"]
    volume = float(args.get("volume", 0.01))
    try:
        info = _mt5_direct({"action": "symbol_info", "symbol": _fix_sym(symbol)})
        tick_value = float(info.get("trade_tick_value", 0) or 0)
        tick_size = float(info.get("trade_tick_size", 0) or 0)
        pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
        if tick_size > 0 and tick_value > 0:
            pip_value = (pip_size / tick_size) * tick_value * volume
        else:
            pip_value = volume * 10 if "USD" in symbol.upper() else volume * 10 * 1.15
        return {"symbol": symbol, "volume": volume, "pip_size": pip_size,
                "pip_value": round(pip_value, 4), "currency": "USD"}
    except Exception as e:
        return {"symbol": symbol, "volume": volume, "pip_value": volume * 10, "estimated": True, "note": str(e)[:50]}


def tool_strategy_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    name = str(args["name"]).strip()
    strategies = _load_strategies()
    existed = name in strategies
    if existed:
        del strategies[name]
        _save_strategies(strategies)
    return {"deleted": existed, "name": name, "count": len(strategies)}


# ── New high-impact tools ──

def tool_daily_risk_control(args: Dict[str, Any]) -> Dict[str, Any]:
    """Persistent daily risk limits. Set max_loss to halt trading after X loss."""
    action = args.get("action", "status")
    risk = _load_daily_risk()
    if action == "set":
        risk["max_loss"] = abs(float(args.get("max_loss", 0.0)))
        risk["max_trades"] = abs(int(args.get("max_trades", 0)))
        risk["date"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        risk["trades_today"] = 0
        _save_daily_risk(risk)
        return {"action": "set", "max_loss": risk["max_loss"], "max_trades": risk["max_trades"]}
    if action == "clear":
        risk["max_loss"] = 0.0
        risk["max_trades"] = 0
        _save_daily_risk(risk)
        return {"action": "cleared"}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_pnl = 0.0
    try:
        today_ts = int(datetime.strptime(today + " 00:00:00", "%Y-%m-%d %H:%M:%S").timestamp())
        hist = _mt5_direct({"action": "history", "from": today_ts, "to": int(time.time()) + 86400})
        for d in hist.get("deals", hist.get("data", [])):
            if isinstance(d, dict):
                today_pnl += d.get("profit", 0.0) + d.get("swap", 0.0) + d.get("commission", 0.0)
    except Exception:
        pass
    try:
        for p in _mt5_direct({"action": "positions"}).get("positions", []):
            today_pnl += p.get("profit", 0.0)
    except Exception:
        pass
    blocked = risk.get("max_loss", 0) > 0 and today_pnl <= -risk["max_loss"]
    return {
        "action": "status", "max_loss": risk.get("max_loss", 0), "max_trades": risk.get("max_trades", 0),
        "trades_today": risk.get("trades_today", 0), "today_pnl": round(today_pnl, 2),
        "blocked": blocked, "date": today,
    }


def tool_pending_modify(args: Dict[str, Any]) -> Dict[str, Any]:
    """Modify SL/TP of a pending order (cancel + replace)."""
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    ticket = int(args["ticket"])
    try:
        orders = _mt5_direct({"action": "orders"})
    except Exception:
        orders = _mt5_direct({"action": "pending_orders"})
    target = None
    for o in orders.get("orders", orders.get("data", [])):
        if isinstance(o, dict) and o.get("ticket") == ticket:
            target = o
            break
    if not target:
        return {"executed": False, "error": f"pending order {ticket} not found"}
    sl = target.get("sl", target.get("stop_loss", 0.0))
    tp = target.get("tp", target.get("take_profit", 0.0))
    new_sl = float(args.get("stop_loss", args.get("sl", sl)) if args.get("stop_loss", args.get("sl", "")) != "" else 0.0)
    new_tp = float(args.get("take_profit", args.get("tp", tp)) if args.get("take_profit", args.get("tp", "")) != "" else 0.0)
    # Cancel + replace
    cancel = _mt5_direct({"action": "cancel_pending_order", "ticket": ticket})
    if not cancel.get("success", cancel.get("executed", False)):
        return {"executed": False, "error": f"cancel failed: {cancel.get('error', 'unknown')}", "cancel": cancel}
    replace = _mt5_direct({
        "action": "send_pending_order", "symbol": target.get("symbol"),
        "type": "BUY_LIMIT" if target.get("type", 0) in (2, "BUY_LIMIT") else "SELL_LIMIT",
        "volume": target.get("volume", target.get("lots", 0.01)),
        "price": target.get("price", target.get("open_price", 0.0)),
        "stop_loss": new_sl, "take_profit": new_tp,
    })
    return {"executed": bool(replace.get("success")), "new_ticket": replace.get("ticket"),
            "old_ticket": ticket, "sl": new_sl, "tp": new_tp, "result": replace}


def tool_scale_in(args: Dict[str, Any]) -> Dict[str, Any]:
    """Add to a winning position (pyramiding). Checks position is profitable first."""
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    ticket = int(args.get("ticket", 0))
    if ticket:
        positions = _mt5_direct({"action": "positions"})
        target = None
        for p in positions.get("positions", []):
            if p["ticket"] == ticket:
                target = p
                break
        if not target:
            return {"executed": False, "error": f"position {ticket} not found"}
        if target.get("profit", 0) <= 0:
            return {"executed": False, "error": "position not profitable", "profit": target.get("profit", 0)}
        symbol = target["symbol"]
        order_type = "BUY" if target.get("type") in (0, "POSITION_TYPE_BUY") else "SELL"
    else:
        symbol = args.get("symbol", "")
        order_type = args.get("type", "BUY").upper()
        if not symbol:
            return {"executed": False, "error": "ticket or symbol+type required"}
    volume = float(args.get("volume", 0.01))
    entry = target.get("open_price", 0.0) if ticket else 0.0
    sl = float(args.get("stop_loss", 0.0))
    tp = float(args.get("take_profit", 0.0))
    guard = _guard_live_order(order_type, symbol, volume, sl, tp)
    if not guard.get("allowed", False):
        return {"executed": False, "blocked": True, "guard": guard}
    result = _mt5_direct({
        "action": "send_order", "symbol": _fix_sym(symbol),
        "type": order_type, "volume": volume,
        "stop_loss": sl, "take_profit": tp, "comment": "mcp_scale_in",
    })
    return {"executed": bool(result.get("success")), "ticket": result.get("ticket"),
            "symbol": symbol, "type": order_type, "volume": volume, "entry": entry, "result": result}


def tool_netting_summary(args: Dict[str, Any]) -> Dict[str, Any]:
    """Net position exposure per symbol. Shows aggregated direction, volume, PnL."""
    positions = _mt5_direct({"action": "positions"})
    pos_list = positions.get("positions", [])
    net = {}
    for p in pos_list:
        sym = p.get("symbol", "?")
        if sym not in net:
            net[sym] = {"symbol": sym, "buy_volume": 0.0, "sell_volume": 0.0,
                        "buy_pnl": 0.0, "sell_pnl": 0.0, "count": 0}
        lots = p.get("volume", p.get("lots", 0.0))
        pnl = p.get("profit", 0.0)
        net[sym]["count"] += 1
        if p.get("type") in (0, "POSITION_TYPE_BUY"):
            net[sym]["buy_volume"] += lots
            net[sym]["buy_pnl"] += pnl
        else:
            net[sym]["sell_volume"] += lots
            net[sym]["sell_pnl"] += pnl
    for sym, info in net.items():
        vol = info["buy_volume"] - info["sell_volume"]
        info["net_volume"] = round(vol, 2)
        info["net_direction"] = "BUY" if vol > 0 else ("SELL" if vol < 0 else "FLAT")
        info["total_pnl"] = round(info["buy_pnl"] + info["sell_pnl"], 2)
    port_pnl = sum(info["total_pnl"] for info in net.values())
    return {"symbols": list(net.values()), "position_count": len(pos_list),
            "total_floating_pnl": round(port_pnl, 2), "symbol_count": len(net)}


def tool_mt4_modify(args: Dict[str, Any]) -> Dict[str, Any]:
    """Modify SL/TP on MT4 position via file bridge."""
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    ticket = int(args["ticket"])
    sl = float(args.get("stop_loss", 0.0))
    tp = float(args.get("take_profit", 0.0))
    raw = _send_file_bridge("mt4", f"MODIFY|{ticket}|{sl}|{tp}", timeout=10.0)
    return {"executed": raw.startswith("OK|"), "ticket": ticket, "sl": sl, "tp": tp, "raw": raw}


def tool_mt4_cancel_pending(args: Dict[str, Any]) -> Dict[str, Any]:
    """Delete pending order on MT4 via file bridge."""
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    ticket = int(args["ticket"])
    raw = _send_file_bridge("mt4", f"DELETE|{ticket}", timeout=10.0)
    return {"executed": raw.startswith("OK|"), "ticket": ticket, "raw": raw}


def tool_mt4_close_symbol(args: Dict[str, Any]) -> Dict[str, Any]:
    """Close all MT4 positions for a symbol via file bridge."""
    if not bool(args.get("confirm_live", False)):
        return {"executed": False, "needs_confirm_live": True}
    symbol = args["symbol"]
    raw = _send_file_bridge("mt4", f"CLOSE|{symbol}", timeout=10.0)
    return {"executed": raw.startswith("OK|"), "symbol": symbol, "raw": raw}


def tool_pretrade_check(args: Dict[str, Any]) -> Dict[str, Any]:
    """Pre-trade battlefield scan. Runs ALL guards + intelligence before trading.
    One call gives the AI the complete picture: market conditions, risk, opportunity."""
    symbol = args.get("symbol", "EURUSD")
    sym = _fix_sym(symbol)
    order_type = args.get("type", "BUY").upper()
    volume = float(args.get("volume", 0.01))
    sl = float(args.get("stop_loss", 0.0))
    tp = float(args.get("take_profit", 0.0))
    results = {"symbol": symbol, "timestamp": datetime.now(timezone.utc).isoformat()}

    results["guard"] = _guard_live_order(order_type, symbol, volume, sl, tp)

    intel_results = {}
    intel_names = ["conviction_decide", "market_sessions", "news_check", "regime_detect",
                   "anomaly_detect", "ensemble_vote", "market_trend_structure",
                   "market_swing_levels", "market_fair_value_gaps", "correlation_report",
                   "antimanipulation_analyze", "sr_levels", "analytics_report",
                   "volume_profile", "autoswitch_status"]
    for iname in intel_names:
        try:
            if iname in _intelligence_tools:
                fn = _intelligence_tools[iname][0]
                needs_sym = iname in ("trend_structure", "market_trend_structure", "market_swing_levels",
                                      "market_fair_value_gaps", "sr_levels", "volume_profile",
                                      "regime_detect", "anomaly_detect", "conviction_decide",
                                      "ensemble_vote", "antimanipulation_analyze")
                intel_results[iname] = fn({"symbol": symbol}) if needs_sym else fn({})
        except Exception as e:
            intel_results[iname] = f"error: {e}"
    results["intelligence"] = intel_results

    guard = results["guard"]
    allowed = guard.get("allowed", False)
    verdicts = []
    if allowed:
        verdicts.append("APPROVED")
    else:
        verdicts.extend(guard.get("reasons", ["blocked"]))
    conv = intel_results.get("conviction_decide", {})
    if isinstance(conv, dict):
        d = conv.get("decision", conv)
        conf = d.get("confidence_pct", d.get("score", 0))
        dir_signal = d.get("verdict", d.get("direction", ""))
        verdicts.append(f"conviction={conf}% ({dir_signal})")
    reg = intel_results.get("regime_detect", {})
    if isinstance(reg, dict):
        verdicts.append(f"regime={reg.get('regime','?')}")
    ses = intel_results.get("market_sessions", {})
    if isinstance(ses, dict):
        verdicts.append(f"session={ses.get('advice','?')} (quality={ses.get('quality',0):.0%})")
    anom = intel_results.get("anomaly_detect", {})
    if isinstance(anom, dict):
        ascore = anom.get("score", 0)
        if ascore > 0.5:
            verdicts.append(f"anomaly={ascore:.2f}")
    news = intel_results.get("news_check", {})
    if isinstance(news, dict) and news.get("has_event"):
        verdicts.append("news-nearby")
    results["summary"] = " | ".join(verdicts)
    results["tradeable"] = allowed
    return results


# ── Paper Trading Engine ──

PAPER_FILE = DATA_DIR / "paper_trading.json"

def _load_paper():
    if not PAPER_FILE.exists():
        return {"balance": 1000.0, "initial_balance": 1000.0, "positions": [], "closed": [], "version": 1}
    try:
        return json.loads(PAPER_FILE.read_text())
    except:
        return {"balance": 1000.0, "initial_balance": 1000.0, "positions": [], "closed": [], "version": 1}

def _save_paper(state):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PAPER_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))

def tool_paper_init(args: Dict[str, Any]) -> Dict[str, Any]:
    """Initialize or reset paper trading with a virtual balance."""
    balance = float(args.get("balance", 1000.0))
    state = {"balance": balance, "initial_balance": balance, "positions": [], "closed": [], "version": 1}
    _save_paper(state)
    return {"status": "ok", "balance": balance, "initial_balance": balance}

def tool_paper_buy(args: Dict[str, Any]) -> Dict[str, Any]:
    """Paper buy. Simulates a BUY trade with virtual money."""
    symbol = args.get("symbol", "EURUSD")
    sym = _fix_sym(symbol)
    volume = float(args.get("volume", 0.01))
    
    price_data = _mt5_direct({"action": "price", "symbol": sym})
    ask = price_data.get("ask", 0)
    spread = price_data.get("spread", 0)
    if ask <= 0:
        return {"error": "no price available"}
    
    state = _load_paper()
    cost = ask * volume * 100000
    margin_needed = cost / 200  # 1:200 leverage
    
    if margin_needed > state["balance"]:
        return {"error": f"insufficient paper balance: need ${margin_needed:.2f}, have ${state['balance']:.2f}"}
    
    sl = float(args.get("stop_loss", 0))
    tp = float(args.get("take_profit", 0))
    
    position = {
        "ticket": len(state["positions"]) + len(state["closed"]) + 1,
        "symbol": symbol,
        "type": "BUY",
        "volume": volume,
        "entry": ask,
        "sl": sl,
        "tp": tp,
        "time": datetime.now(timezone.utc).isoformat(),
        "margin": margin_needed,
    }
    
    state["balance"] -= margin_needed
    state["positions"].append(position)
    _save_paper(state)
    
    return {"status": "open", "position": position, "paper_balance": round(state["balance"], 2)}

def tool_paper_sell(args: Dict[str, Any]) -> Dict[str, Any]:
    """Paper sell. Simulates a SELL trade with virtual money."""
    symbol = args.get("symbol", "EURUSD")
    sym = _fix_sym(symbol)
    volume = float(args.get("volume", 0.01))
    
    price_data = _mt5_direct({"action": "price", "symbol": sym})
    bid = price_data.get("bid", 0)
    spread = price_data.get("spread", 0)
    if bid <= 0:
        return {"error": "no price available"}
    
    state = _load_paper()
    cost = bid * volume * 100000
    margin_needed = cost / 200
    
    if margin_needed > state["balance"]:
        return {"error": f"insufficient paper balance: need ${margin_needed:.2f}, have ${state['balance']:.2f}"}
    
    sl = float(args.get("stop_loss", 0))
    tp = float(args.get("take_profit", 0))
    
    position = {
        "ticket": len(state["positions"]) + len(state["closed"]) + 1,
        "symbol": symbol,
        "type": "SELL",
        "volume": volume,
        "entry": bid,
        "sl": sl,
        "tp": tp,
        "time": datetime.now(timezone.utc).isoformat(),
        "margin": margin_needed,
    }
    
    state["balance"] -= margin_needed
    state["positions"].append(position)
    _save_paper(state)
    
    return {"status": "open", "position": position, "paper_balance": round(state["balance"], 2)}

def tool_paper_close(args: Dict[str, Any]) -> Dict[str, Any]:
    """Close a paper position by ticket."""
    ticket = int(args.get("ticket", 0))
    state = _load_paper()
    
    pos = None
    for i, p in enumerate(state["positions"]):
        if p.get("ticket") == ticket:
            pos = state["positions"].pop(i)
            break
    
    if not pos:
        return {"error": f"position {ticket} not found"}
    
    sym = _fix_sym(pos["symbol"])
    price_data = _mt5_direct({"action": "price", "symbol": sym})
    
    if pos["type"] == "BUY":
        exit_price = price_data.get("bid", 0)
        pnl = (exit_price - pos["entry"]) * pos["volume"] * 100000
    else:
        exit_price = price_data.get("ask", 0)
        pnl = (pos["entry"] - exit_price) * pos["volume"] * 100000
    
    spread_cost = (price_data.get("spread", 0) * 0.00001) * pos["volume"] * 100000
    pnl -= spread_cost
    
    state["balance"] += pos["margin"] + pnl
    
    closed = dict(pos)
    closed["exit"] = exit_price
    closed["pnl"] = round(pnl, 2)
    closed["close_time"] = datetime.now(timezone.utc).isoformat()
    state["closed"].append(closed)
    
    equity = state["balance"] + sum(p["margin"] for p in state["positions"])
    _save_paper(state)
    
    return {
        "status": "closed",
        "position": closed,
        "pnl": round(pnl, 2),
        "paper_balance": round(state["balance"], 2),
        "paper_equity": round(equity, 2),
    }

def tool_paper_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Paper trading portfolio: balance, equity, open PnL, closed trades."""
    state = _load_paper()
    open_pnl = 0.0
    positions_detail = []
    
    for p in state.get("positions", []):
        try:
            sym = _fix_sym(p["symbol"])
            price_data = _mt5_direct({"action": "price", "symbol": sym})
            if p["type"] == "BUY":
                current_pnl = (price_data.get("bid", p["entry"]) - p["entry"]) * p["volume"] * 100000
            else:
                current_pnl = (p["entry"] - price_data.get("ask", p["entry"])) * p["volume"] * 100000
            open_pnl += current_pnl
            positions_detail.append({**p, "current_pnl": round(current_pnl, 2)})
        except:
            positions_detail.append({**p, "current_pnl": 0})
    
    equity = state["balance"] + open_pnl
    total_invested = state["initial_balance"] - state["balance"] + sum(p["margin"] for p in state["positions"])
    total_pnl = sum(c.get("pnl", 0) for c in state.get("closed", [])) + open_pnl
    wins = len([c for c in state.get("closed", []) if c.get("pnl", 0) > 0])
    losses = len([c for c in state.get("closed", []) if c.get("pnl", 0) <= 0])
    
    return {
        "initial_balance": state["initial_balance"],
        "available_balance": round(state["balance"], 2),
        "equity": round(equity, 2),
        "open_positions": len(state.get("positions", [])),
        "closed_trades": len(state.get("closed", [])),
        "open_pnl": round(open_pnl, 2),
        "total_pnl": round(total_pnl, 2),
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wins / (wins + losses) * 100, 1) if (wins + losses) > 0 else 0,
        "positions": positions_detail,
        "recent_closed": state.get("closed", [])[-5:] if state.get("closed") else [],
    }

def tool_paper_close_all(args: Dict[str, Any]) -> Dict[str, Any]:
    """Close all open paper positions."""
    state = _load_paper()
    results = []
    for p in list(state.get("positions", [])):
        result = tool_paper_close({"ticket": p["ticket"]})
        results.append(result)
    return {"closed": len(results), "results": results}


# ── Realtime Scanner ──

_SCANNER_THREAD = None
_SCANNER_RESULTS = {"status": "idle", "last_scan": None, "signals": []}

def tool_scanner_start(args: Dict[str, Any]) -> Dict[str, Any]:
    """Start real-time scanner in background. Scans symbols every N minutes."""
    global _SCANNER_THREAD, _SCANNER_RESULTS
    import threading
    
    if _SCANNER_THREAD and _SCANNER_THREAD.is_alive():
        return {"status": "already_running"}
    
    symbols = args.get("symbols", ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD"])
    interval_seconds = int(args.get("interval_seconds", 300))
    min_confidence = int(args.get("min_confidence", 50))
    max_spread = int(args.get("max_spread", 80))
    
    def _scan_loop():
        global _SCANNER_RESULTS
        while True:
            try:
                signals = []
                for sym in symbols:
                    try:
                        s = _fix_sym(sym)
                        price = _mt5_direct({"action": "price", "symbol": s})
                        spread = price.get("spread", 99)
                        if spread > max_spread:
                            continue
                        
                        # Quick conviction check using intelligence
                        if "conviction_decide" in _intelligence_tools:
                            fn = _intelligence_tools["conviction_decide"][0]
                            result = fn({"symbol": sym, "timeframe": "M5"})
                            decision = result.get("decision", result)
                            conf = decision.get("confidence_pct", decision.get("score", 0))
                            verdict = decision.get("verdict", decision.get("direction", "PASS"))
                            if verdict in ("BUY", "SELL") and conf >= min_confidence:
                                signals.append({
                                    "symbol": sym, "type": verdict,
                                    "confidence": conf, "price": price.get("bid", 0),
                                    "spread": spread, "time": datetime.now(timezone.utc).isoformat(),
                                })
                    except:
                        pass
                
                signals.sort(key=lambda x: x["confidence"], reverse=True)
                _SCANNER_RESULTS = {
                    "status": "scanning",
                    "last_scan": datetime.now(timezone.utc).isoformat(),
                    "signals": signals,
                    "symbols_scanned": len(symbols),
                }
            except Exception as e:
                _SCANNER_RESULTS["last_error"] = str(e)
            
            # Wait
            slept = 0
            while slept < interval_seconds:
                if not _SCANNER_THREAD or not _SCANNER_THREAD.is_alive():
                    return
                time.sleep(5)
                slept += 5
    
    _SCANNER_THREAD = threading.Thread(target=_scan_loop, daemon=True)
    _SCANNER_THREAD.start()
    _SCANNER_RESULTS = {"status": "running", "interval": interval_seconds, "symbols": symbols}
    return {"status": "started", "symbols": symbols, "interval_seconds": interval_seconds}

def tool_scanner_stop(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stop the real-time scanner."""
    global _SCANNER_THREAD, _SCANNER_RESULTS
    _SCANNER_THREAD = None
    _SCANNER_RESULTS["status"] = "stopped"
    return {"status": "stopped"}

def tool_scanner_signals(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get latest scanner signals."""
    global _SCANNER_RESULTS
    return _SCANNER_RESULTS if _SCANNER_RESULTS else {"status": "idle", "signals": []}


# ── Background Precompute Engine ──

_BG_THREAD = None
_BG_STATE = {
    "status": "idle",
    "last_update": None,
    "symbols": {},
    "backtests": {},
    "market_context": {},
    "errors": [],
}

_BG_SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "USDCAD", "AUDUSD", "NZDUSD", "USDCHF"]

def _bg_loop():
    """Background engine: precomputes everything continuously."""
    global _BG_STATE, _BG_THREAD
    import threading
    
    while True:
        if not _BG_THREAD or not threading.current_thread().is_alive():
            return
        
        try:
            now = datetime.now(timezone.utc)
            updates = {}
            bt_cache = {}
            context = {}
            
            for sym in _BG_SYMBOLS:
                try:
                    s = _fix_sym(sym)
                    
                    # 1. Price + spread
                    price = _mt5_direct({"action": "price", "symbol": s})
                    updates[sym] = {
                        "bid": price.get("bid", 0),
                        "ask": price.get("ask", 0),
                        "spread": price.get("spread", 99),
                        "tradeable": price.get("spread", 99) < 80 and price.get("bid", 0) > 0,
                    }
                    
                    # 2. Candles (M5 + H1) for analysis
                    m5_raw = _mt5_direct({"action": "candles", "symbol": s, "timeframe": "M5", "count": 50})
                    h1_raw = _mt5_direct({"action": "candles", "symbol": s, "timeframe": "H1", "count": 200})
                    m5 = m5_raw.get("candles", m5_raw.get("data", []))
                    h1 = h1_raw.get("candles", h1_raw.get("data", []))
                    
                    # 3. Quick momentum (last 3 M5 candles)
                    if len(m5) >= 4:
                        recent = m5[-4:]
                        mom = (recent[-1]["close"] - recent[-4]["close"]) / recent[-4]["close"] * 100
                        updates[sym]["momentum_pct"] = round(mom, 3)
                        updates[sym]["volatility_pct"] = round(
                            sum(c["high"] - c["low"] for c in recent) / sum(c["close"] for c in recent) * 100 * 4, 3
                        )
                    else:
                        updates[sym]["momentum_pct"] = 0
                        updates[sym]["volatility_pct"] = 0
                    
                    # 4. Quick RSI (H1, last 14)
                    if len(h1) >= 15:
                        closes = [c["close"] for c in h1[-15:]]
                        gains = losses = 0
                        for i in range(1, len(closes)):
                            diff = closes[i] - closes[i-1]
                            gains += max(diff, 0)
                            losses += max(-diff, 0)
                        rsi = 50
                        if losses > 0:
                            rs = gains / losses
                            rsi = 100 - 100 / (1 + rs)
                        updates[sym]["rsi_h1"] = round(rsi, 1)
                    else:
                        updates[sym]["rsi_h1"] = 50
                    
                    # 5. Quick EMA trend (H1)
                    if len(h1) >= 20:
                        closes = [c["close"] for c in h1]
                        fast = sum(closes[-5:]) / 5
                        slow = sum(closes[-20:]) / 20
                        updates[sym]["trend"] = "up" if fast > slow else "down"
                        updates[sym]["ema_fast"] = round(fast, 5)
                        updates[sym]["ema_slow"] = round(slow, 5)
                    else:
                        updates[sym]["trend"] = "unknown"
                    
                    # 6. Backtest for current trend direction
                    if updates[sym].get("tradeable") and len(h1) >= 200:
                        direction = updates[sym].get("trend", "unknown")
                        strategy = "ma_cross" if direction != "unknown" else "rsi_mean_reversion"
                        bt = _backtest_quick(h1, strategy)
                        bt_cache[sym] = bt
                        if bt:
                            updates[sym]["backtest"] = {
                                "win_rate": bt.get("win_rate_pct", 0),
                                "profit_factor": bt.get("profit_factor", 0),
                                "sharpe": bt.get("sharpe", 0),
                                "strategy": strategy,
                                "signal": "GOOD" if bt.get("win_rate_pct", 0) > 55 and bt.get("profit_factor", 0) != "inf" and float(bt.get("profit_factor", 0) or 0) > 1.2 else "NEUTRAL" if bt.get("win_rate_pct", 0) > 40 else "SKIP",
                            }
                    
                except Exception as e:
                    updates[sym] = {"error": str(e)}
            
            # 7. Market context (trading session, volatility regime)
            try:
                hour = now.hour
                if 8 <= hour < 17:
                    session = "London"
                    quality = "high"
                elif 13 <= hour < 22:
                    session = "NY"
                    quality = "high" if 13 <= hour < 17 else "medium"
                elif 0 <= hour < 9:
                    session = "Asia/Pacific"
                    quality = "low"
                else:
                    session = "off_hours"
                    quality = "very_low"
                context["session"] = session
                context["session_quality"] = quality
                context["hour_utc"] = hour
                
                # Overall market volatility from EURUSD
                if "EURUSD" in updates:
                    v = updates["EURUSD"].get("volatility_pct", 0)
                    if v < 0.05:
                        context["volatility_regime"] = "quiet"
                    elif v < 0.15:
                        context["volatility_regime"] = "normal"
                    else:
                        context["volatility_regime"] = "volatile"
                
                # Best pairs right now
                ranked = sorted(
                    [(sym, d) for sym, d in updates.items() if isinstance(d, dict) and d.get("tradeable")],
                    key=lambda x: x[1].get("backtest", {}).get("win_rate", 0) if x[1].get("backtest") else 0,
                    reverse=True,
                )
                context["best_pairs"] = [
                    {"symbol": sym, "trend": d.get("trend", "?"), 
                     "win_rate": d.get("backtest", {}).get("win_rate", 0) if d.get("backtest") else 0,
                     "spread": d.get("spread", 99)}
                    for sym, d in ranked[:3] if d.get("tradeable")
                ]
            except Exception as e:
                context["error"] = str(e)
            
            _BG_STATE = {
                "status": "running",
                "last_update": now.isoformat(),
                "symbols": updates,
                "backtests": bt_cache,
                "market_context": context,
                "errors": [],
            }
            
        except Exception as e:
            _BG_STATE["errors"].append(str(e))
            if len(_BG_STATE["errors"]) > 10:
                _BG_STATE["errors"] = _BG_STATE["errors"][-10:]
        
        # Sleep 45 seconds between full cycles
        for _ in range(45):
            if not _BG_THREAD or not threading.current_thread().is_alive():
                return
            time.sleep(1)

def _backtest_quick(candles, strategy="ma_cross"):
    """Ultra-fast backtest using already-fetched candles."""
    if len(candles) < 30:
        return None
    closes = [c["close"] for c in candles]
    
    def ema(data, period):
        if len(data) < period:
            return [None] * len(data)
        result = []
        mult = 2 / (period + 1)
        ema_val = sum(data[:period]) / period
        for i in range(len(data)):
            if i < period - 1:
                result.append(None)
            elif i == period - 1:
                result.append(ema_val)
            else:
                ema_val = (data[i] - ema_val) * mult + ema_val
                result.append(ema_val)
        return result
    
    fast_ema = ema(closes, 5)
    slow_ema = ema(closes, 20)
    
    trades = []
    balance = 100.0
    in_position = None
    
    for i in range(20, len(closes) - 1):
        if fast_ema[i] is None or slow_ema[i] is None:
            continue
        
        if strategy == "ma_cross":
            if in_position is None:
                if fast_ema[i-1] <= slow_ema[i-1] and fast_ema[i] > slow_ema[i]:
                    in_position = {"type": "BUY", "entry": closes[i], "idx": i}
                elif fast_ema[i-1] >= slow_ema[i-1] and fast_ema[i] < slow_ema[i]:
                    in_position = {"type": "SELL", "entry": closes[i], "idx": i}
            
            if in_position:
                pnl_pct = 0
                if in_position["type"] == "BUY":
                    # Exit on opposite signal or after 10 candles
                    if i - in_position["idx"] >= 10 or (i > in_position["idx"] and fast_ema[i-1] >= slow_ema[i-1] and fast_ema[i] < slow_ema[i]):
                        pnl_pct = (closes[i] - in_position["entry"]) / in_position["entry"]
                else:
                    if i - in_position["idx"] >= 10 or (i > in_position["idx"] and fast_ema[i-1] <= slow_ema[i-1] and fast_ema[i] > slow_ema[i]):
                        pnl_pct = (in_position["entry"] - closes[i]) / in_position["entry"]
                
                if pnl_pct != 0:
                    pnl = balance * pnl_pct * 10
                    balance += pnl
                    trades.append(pnl)
                    in_position = None
    
    if len(trades) < 3:
        return None
    
    wins = sum(1 for t in trades if t > 0)
    losses = sum(1 for t in trades if t <= 0)
    total_won = sum(t for t in trades if t > 0)
    total_lost = abs(sum(t for t in trades if t <= 0))
    
    return {
        "total_trades": len(trades),
        "win_rate_pct": round(wins / len(trades) * 100, 1),
        "profit_factor": round(total_won / total_lost, 2) if total_lost > 0 else "inf",
        "net_pnl": round(balance - 100, 2),
    }

def tool_bg_status(args: Dict[str, Any]) -> Dict[str, Any]:
    """Get the latest pre-computed market data. Everything is ready instantly."""
    global _BG_STATE
    st = _BG_STATE
    
    # Pick best tradeable pair right now
    best = None
    for sym, d in st.get("symbols", {}).items():
        if isinstance(d, dict) and d.get("tradeable") and d.get("backtest", {}).get("signal") == "GOOD":
            if not best or d.get("backtest", {}).get("win_rate", 0) > best.get("backtest", {}).get("win_rate", 0):
                best = {"symbol": sym, "data": d}
    
    return {
        "status": st["status"],
        "last_update": st["last_update"],
        "market_context": st.get("market_context", {}),
        "top_opportunity": best,
        "symbols": {
            sym: {k: v for k, v in d.items() if k in ("bid","ask","spread","momentum_pct","volatility_pct","rsi_h1","trend","tradeable","backtest")}
            for sym, d in st.get("symbols", {}).items() if isinstance(d, dict)
        },
        "errors": st.get("errors", [])[-3:] if st.get("errors") else [],
    }

def tool_bg_start(args: Dict[str, Any]) -> Dict[str, Any]:
    """Start the background precompute engine. Prepares everything before you trade."""
    global _BG_THREAD
    import threading
    
    if _BG_THREAD and _BG_THREAD.is_alive():
        return {"status": "already_running"}
    
    _BG_THREAD = threading.Thread(target=_bg_loop, daemon=True)
    _BG_THREAD.start()
    return {"status": "started", "symbols": _BG_SYMBOLS, "cycle_seconds": 45}

def tool_bg_stop(args: Dict[str, Any]) -> Dict[str, Any]:
    """Stop the background engine."""
    global _BG_THREAD, _BG_STATE
    _BG_THREAD = None
    _BG_STATE["status"] = "stopped"
    return {"status": "stopped"}


def schema(props: Dict[str, Any], required: Optional[List[str]] = None) -> Dict[str, Any]:
    return {"type": "object", "properties": props, "required": required or []}


# ── Import intelligence engine (120+ tools) ──
_intelligence_tools = {}
try:
    from mt5_mcp_intelligence import init as intel_init
    _intelligence_tools = intel_init(_mt5_direct)
    sys.stderr.write(f"mt5_mcp_intelligence loaded: {len(_intelligence_tools)} tools\n")
except Exception as e:
    sys.stderr.write(f"mt5_mcp_intelligence not available: {e}\n")

TOOLS: Dict[str, Tuple[Callable[[Dict[str, Any]], Dict[str, Any]], str, Dict[str, Any]]] = {
    "metatrader_platforms": (tool_metatrader_platforms, "Detecta soporte Mac/Linux para MT4 y MT5 sin leer ni guardar credenciales.", schema({})),
    "metatrader_account": (tool_mt_account, "Cuenta de MT4 o MT5 con login enmascarado.", schema({
        "platform": {"type": "string", "enum": ["mt4", "mt5"], "default": "mt5"},
    })),
    "metatrader_price": (tool_mt_price, "Precio bid/ask de MT4 o MT5.", schema({
        "platform": {"type": "string", "enum": ["mt4", "mt5"], "default": "mt5"},
        "symbol": {"type": "string", "default": "EURUSD"},
    })),
    "metatrader_positions": (tool_mt_positions, "Posiciones abiertas en MT4 o MT5.", schema({
        "platform": {"type": "string", "enum": ["mt4", "mt5"], "default": "mt5"},
    })),
    "metatrader_candles": (tool_mt_candles, "Velas OHLC de MT4 o MT5 para cualquier estrategia.", schema({
        "platform": {"type": "string", "enum": ["mt4", "mt5"], "default": "mt5"},
        "symbol": {"type": "string"},
        "timeframe": {"type": "string", "default": "M1"},
        "count": {"type": "integer", "default": 100},
    }, ["symbol"])),
    "metatrader_check_order": (tool_mt_check_order, "Valida una orden en MT4 o MT5 sin ejecutarla.", schema({
        "platform": {"type": "string", "enum": ["mt4", "mt5"], "default": "mt5"},
        "symbol": {"type": "string"},
        "type": {"type": "string", "enum": ["BUY", "SELL"]},
        "volume": {"type": "number", "default": 0.01},
        "stop_loss": {"type": "number", "default": 0.0},
        "take_profit": {"type": "number", "default": 0.0},
    }, ["symbol", "type"])),
    "metatrader_place_market_order": (tool_mt_place_market_order, "Abre BUY/SELL en MT4 o MT5 solo con confirm_live=true.", schema({
        "platform": {"type": "string", "enum": ["mt4", "mt5"], "default": "mt5"},
        "symbol": {"type": "string"},
        "type": {"type": "string", "enum": ["BUY", "SELL"]},
        "volume": {"type": "number", "default": 0.01},
        "stop_loss": {"type": "number", "default": 0.0},
        "take_profit": {"type": "number", "default": 0.0},
        "dry_run": {"type": "boolean", "default": True},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["symbol", "type"])),
    "metatrader_close_position": (tool_mt_close_position, "Cierra una posicion por ticket en MT4 o MT5.", schema({
        "platform": {"type": "string", "enum": ["mt4", "mt5"], "default": "mt5"},
        "ticket": {"type": "integer"},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["ticket"])),
    "metatrader_close_all": (tool_mt_close_all, "Cierra posiciones en MT4 o MT5: all, profitable o losing.", schema({
        "platform": {"type": "string", "enum": ["mt4", "mt5"], "default": "mt5"},
        "mode": {"type": "string", "enum": ["all", "profitable", "losing"], "default": "all"},
        "confirm_live": {"type": "boolean", "default": False},
    })),
    "mt5_account": (tool_account, "Balance, equity, margen y servidor de MT5.", schema({})),
    "mt5_price": (tool_price, "Precio bid/ask de un símbolo.", schema({
        "symbol": {"type": "string", "default": "EURUSD.FX"},
    })),
    "mt5_symbols": (tool_symbols, "Lista símbolos disponibles, opcionalmente filtrados por texto.", schema({
        "pattern": {"type": "string", "default": ""},
    })),
    "mt5_candles": (tool_candles, "Velas OHLC recientes para análisis de cualquier estrategia.", schema({
        "symbol": {"type": "string"},
        "timeframe": {"type": "string", "default": "M1"},
        "count": {"type": "integer", "default": 100},
    }, ["symbol"])),
    "mt5_positions": (tool_positions, "Posiciones abiertas con ticket, símbolo, SL/TP y PnL.", schema({})),
    "mt5_pending_orders": (tool_orders, "Órdenes pendientes abiertas.", schema({})),
    "mt5_history": (tool_history, "Historial de deals recientes para análisis de desempeño.", schema({
        "symbol": {"type": "string", "default": ""},
        "days": {"type": "integer", "default": 30},
    })),
    "mt5_trade_status": (tool_status, "Estado del bridge y EA.", schema({})),
    "mt5_activity": (tool_activity, "Actividad reciente de fuego en logs.", schema({})),
    "mt5_check_order": (tool_check_order, "Valida una orden real sin ejecutarla y aplica límites MCP.", schema({
        "symbol": {"type": "string"},
        "type": {"type": "string", "enum": ["BUY", "SELL"]},
        "volume": {"type": "number", "default": 0.01},
        "stop_loss": {"type": "number", "default": 0.0},
        "take_profit": {"type": "number", "default": 0.0},
    }, ["symbol", "type"])),
    "mt5_scan_strategy": (tool_scan_strategy, "Escanea varios símbolos en una sola llamada MT5 y devuelve candidatos/rechazos con guardas de riesgo.", schema({
        "symbols": {"type": "array", "items": {"type": "string"}, "default": []},
        "volume": {"type": "number", "default": 0.01},
        "auto_min_volume": {"type": "boolean", "default": False},
        "max_volume": {"type": "number", "default": 0.01},
        "max_positions": {"type": "integer", "default": 1},
        "max_spread_points": {"type": "integer", "default": 80},
        "max_margin_use_pct": {"type": "number", "default": 35.0},
        "min_post_trade_free_margin_pct": {"type": "number", "default": 55.0},
        "max_risk_usd": {"type": "number", "default": 0.22},
        "min_score": {"type": "number", "default": 70.0},
        "reward_risk": {"type": "number", "default": 1.4},
        "limit": {"type": "integer", "default": 5},
    })),
    "mt5_place_market_order": (tool_place_market_order, "Abre BUY/SELL real solo con confirm_live=true y guard aprobado.", schema({
        "symbol": {"type": "string"},
        "type": {"type": "string", "enum": ["BUY", "SELL"]},
        "volume": {"type": "number", "default": 0.01},
        "stop_loss": {"type": "number", "default": 0.0},
        "take_profit": {"type": "number", "default": 0.0},
        "dry_run": {"type": "boolean", "default": True},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["symbol", "type"])),
    "mt5_place_pending_order": (tool_place_pending_order, "Coloca orden pendiente LIMIT/STOP según precio.", schema({
        "symbol": {"type": "string"},
        "type": {"type": "string", "enum": ["BUY", "SELL"]},
        "volume": {"type": "number", "default": 0.01},
        "price": {"type": "number"},
        "stop_loss": {"type": "number", "default": 0.0},
        "take_profit": {"type": "number", "default": 0.0},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["symbol", "type", "price"])),
    "mt5_cancel_pending_order": (tool_cancel_pending_order, "Cancela una orden pendiente por ticket.", schema({
        "ticket": {"type": "integer"},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["ticket"])),
    "mt5_modify_position": (tool_modify_position, "Modifica SL/TP de una posición por ticket.", schema({
        "ticket": {"type": "integer"},
        "stop_loss": {"type": ["number", "string"], "default": ""},
        "take_profit": {"type": ["number", "string"], "default": ""},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["ticket"])),
    "mt5_close_position": (tool_close_position, "Cierra una posición por ticket.", schema({
        "ticket": {"type": "integer"},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["ticket"])),
    "mt5_close_symbol": (tool_close_symbol, "Cierra todas las posiciones de un símbolo.", schema({
        "symbol": {"type": "string"},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["symbol"])),
    "mt5_close_all": (tool_close_all, "Cierra posiciones: all, profitable o losing.", schema({
        "mode": {"type": "string", "enum": ["all", "profitable", "losing"], "default": "all"},
        "confirm_live": {"type": "boolean", "default": False},
    })),
    "mt5_brain": (tool_brain, "Estado del cerebro de fuego en logs.", schema({})),
    "mt5_progress": (tool_progress, "Progreso hacia cierres ganadores.", schema({})),
    "mt5_strategy_save": (tool_strategy_save, "Guarda una estrategia localmente para reutilizarla sin repetir instrucciones largas.", schema({
        "name": {"type": "string"},
        "description": {"type": "string", "default": ""},
        "symbols": {"type": "array", "items": {"type": "string"}, "default": []},
        "timeframes": {"type": "array", "items": {"type": "string"}, "default": []},
        "entry_rules": {"type": "array", "items": {"type": "string"}, "default": []},
        "exit_rules": {"type": "array", "items": {"type": "string"}, "default": []},
        "risk": {"type": "object", "default": {}},
        "filters": {"type": "object", "default": {}},
        "notes": {"type": "string", "default": ""},
    }, ["name"])),
    "mt5_strategy_list": (tool_strategy_list, "Lista estrategias guardadas localmente.", schema({})),
    "mt5_strategy_get": (tool_strategy_get, "Recupera una estrategia guardada por nombre.", schema({
        "name": {"type": "string"},
    }, ["name"])),
    "mt5_strategy_delete": (tool_strategy_delete, "Elimina una estrategia guardada por nombre.", schema({
        "name": {"type": "string"},
    }, ["name"])),
    "mt5_partial_close": (tool_partial_close, "Cierra un porcentaje de una posición (ej: 50%).", schema({
        "ticket": {"type": "integer"},
        "close_pct": {"type": "number", "default": 50},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["ticket"])),
    "mt5_close_by_type": (tool_close_by_type, "Cierra todas las posiciones BUY o todas SELL.", schema({
        "type": {"type": "string", "enum": ["BUY", "SELL"]},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["type"])),
    "mt5_breakeven_all": (tool_breakeven_all, "Mueve SL de todas las posiciones a precio de entrada.", schema({
        "confirm_live": {"type": "boolean", "default": False},
    })),
    "mt5_daily_report": (tool_daily_report, "Balance, PnL diario, equity de hoy.", schema({})),
    "mt5_trade_journal": (tool_trade_journal, "Historial persistente de trades cerrados.", schema({
        "limit": {"type": "integer", "default": 50},
    })),
    "mt5_scanner_fix": (tool_scanner_fix, "Scanner ajustado para tastyfx: max_spread=80, max_risk=$0.50, min_score=40.", schema({
        "symbols": {"type": "array", "items": {"type": "string"}, "default": []},
        "volume": {"type": "number", "default": 0.01},
        "limit": {"type": "integer", "default": 5},
    })),
    "mt5_quick_buy": (tool_quick_buy, "Compra rápida con SL/TP en pips. Un solo comando fácil.", schema({
        "symbol": {"type": "string"},
        "volume": {"type": "number", "default": 0.01},
        "sl_pips": {"type": "number", "default": 0},
        "tp_pips": {"type": "number", "default": 0},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["symbol"])),
    "mt5_quick_sell": (tool_quick_sell, "Venta rápida con SL/TP en pips. Un solo comando fácil.", schema({
        "symbol": {"type": "string"},
        "volume": {"type": "number", "default": 0.01},
        "sl_pips": {"type": "number", "default": 0},
        "tp_pips": {"type": "number", "default": 0},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["symbol"])),
    "mt5_move_to_breakeven": (tool_move_to_breakeven, "Mueve SL de una posición a precio de entrada.", schema({
        "ticket": {"type": "integer"},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["ticket"])),
    "mt5_trail_all": (tool_trail_all, "Aplica trailing stop automático a todas las posiciones.", schema({
        "activation_pips": {"type": "number", "default": 15},
        "distance_pips": {"type": "number", "default": 10},
        "confirm_live": {"type": "boolean", "default": False},
    })),
    "mt5_account_summary": (tool_account_summary, "Resumen completo: balance, equity, PnL flotante, diario, posiciones abiertas.", schema({})),
    "mt5_best_time_to_trade": (tool_best_time_to_trade, "Mejor horario para operar un par según sesiones.", schema({
        "symbol": {"type": "string", "default": "EURUSD"},
    })),
    "mt5_market_overview": (tool_market_overview, "Vista rápida de todos los pares mayores: bid/ask/spread/tradeable.", schema({})),
    "mt5_pip_value": (tool_pip_value, "Valor monetario de 1 pip para un símbolo y volumen.", schema({
        "symbol": {"type": "string"},
        "volume": {"type": "number", "default": 0.01},
    }, ["symbol"])),
    "mt5_auto_trade": (tool_auto_trade, "ONE-SHOT: escanea, decide, verifica guardias + ejecuta el mejor trade. Sistema completo en un comando.", schema({
        "symbols": {"type": "array", "items": {"type": "string"}, "default": ["EURUSD","GBPUSD","USDJPY","USDCAD","AUDUSD"]},
        "volume": {"type": "number", "default": 0.01},
        "min_confidence": {"type": "integer", "default": 60},
        "max_spread_points": {"type": "integer", "default": 80},
        "dry_run": {"type": "boolean", "default": True},
        "confirm_live": {"type": "boolean", "default": False},
    })),
    "mt5_daily_risk_control": (tool_daily_risk_control, "Control de riesgo diario: set/clear/status. Si max_loss se excede, bloquea trades.", schema({
        "action": {"type": "string", "enum": ["status", "set", "clear"], "default": "status"},
        "max_loss": {"type": "number", "default": 0},
        "max_trades": {"type": "integer", "default": 0},
    })),
    "mt5_pending_modify": (tool_pending_modify, "Modifica SL/TP de una orden pendiente (cancel + replace).", schema({
        "ticket": {"type": "integer"},
        "stop_loss": {"type": "number", "default": 0},
        "take_profit": {"type": "number", "default": 0},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["ticket"])),
    "mt5_scale_in": (tool_scale_in, "Añade volumen a una posición ganadora (pyramiding). Verifica que esté en profit.", schema({
        "ticket": {"type": "integer", "default": 0},
        "symbol": {"type": "string", "default": ""},
        "type": {"type": "string", "enum": ["BUY", "SELL"], "default": "BUY"},
        "volume": {"type": "number", "default": 0.01},
        "stop_loss": {"type": "number", "default": 0},
        "take_profit": {"type": "number", "default": 0},
        "confirm_live": {"type": "boolean", "default": False},
    })),
    "mt5_netting_summary": (tool_netting_summary, "Resumen de exposición neta por símbolo: volumen neto, PnL agregado.", schema({})),
    "mt4_modify_position": (tool_mt4_modify, "Modifica SL/TP de una posición en MT4.", schema({
        "ticket": {"type": "integer"},
        "stop_loss": {"type": "number", "default": 0},
        "take_profit": {"type": "number", "default": 0},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["ticket"])),
    "mt4_cancel_pending_order": (tool_mt4_cancel_pending, "Cancela una orden pendiente en MT4.", schema({
        "ticket": {"type": "integer"},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["ticket"])),
    "mt4_close_symbol": (tool_mt4_close_symbol, "Cierra todas las posiciones de un símbolo en MT4.", schema({
        "symbol": {"type": "string"},
        "confirm_live": {"type": "boolean", "default": False},
    }, ["symbol"])),
    "mt5_pretrade_check": (tool_pretrade_check, "Pre-trade battlefield scan. ALL guards + intelligence en 1 llamada. Úsala antes de cada trade.", schema({
        "symbol": {"type": "string", "default": "EURUSD"},
        "type": {"type": "string", "enum": ["BUY", "SELL"], "default": "BUY"},
        "volume": {"type": "number", "default": 0.01},
        "stop_loss": {"type": "number", "default": 0},
        "take_profit": {"type": "number", "default": 0},
    })),
    "mt5_paper_init": (tool_paper_init, "Initialize/reset paper trading with virtual balance (default $1000).", schema({
        "balance": {"type": "number", "default": 1000},
    })),
    "mt5_paper_buy": (tool_paper_buy, "Paper buy: simulate BUY with virtual money.", schema({
        "symbol": {"type": "string", "default": "EURUSD"},
        "volume": {"type": "number", "default": 0.01},
        "stop_loss": {"type": "number", "default": 0},
        "take_profit": {"type": "number", "default": 0},
    })),
    "mt5_paper_sell": (tool_paper_sell, "Paper sell: simulate SELL with virtual money.", schema({
        "symbol": {"type": "string", "default": "EURUSD"},
        "volume": {"type": "number", "default": 0.01},
        "stop_loss": {"type": "number", "default": 0},
        "take_profit": {"type": "number", "default": 0},
    })),
    "mt5_paper_close": (tool_paper_close, "Close a paper position by ticket.", schema({
        "ticket": {"type": "integer"},
    }, ["ticket"])),
    "mt5_paper_status": (tool_paper_status, "Paper portfolio: balance, equity, open PnL, closed trades.", schema({})),
    "mt5_paper_close_all": (tool_paper_close_all, "Close all open paper positions.", schema({})),
    "mt5_scanner_start": (tool_scanner_start, "Start real-time background scanner. Checks symbols every N seconds.", schema({
        "symbols": {"type": "array", "items": {"type": "string"}, "default": ["EURUSD","GBPUSD","USDJPY","USDCAD","AUDUSD"]},
        "interval_seconds": {"type": "integer", "default": 300},
        "min_confidence": {"type": "integer", "default": 50},
        "max_spread": {"type": "integer", "default": 80},
    })),
    "mt5_scanner_stop": (tool_scanner_stop, "Stop the real-time background scanner.", schema({})),
    "mt5_scanner_signals": (tool_scanner_signals, "Get latest scanner signals and status.", schema({})),
    "mt5_bg_start": (tool_bg_start, "START background precompute engine. Prepares everything in advance 24/7.", schema({})),
    "mt5_bg_stop": (tool_bg_stop, "STOP background precompute engine.", schema({})),
    "mt5_bg_status": (tool_bg_status, "Get pre-computed market data. ALL analysis ready instantly — no waiting.", schema({})),
}

# Merge intelligence tools
for iname, (ifn, idesc, ischema) in _intelligence_tools.items():
    if iname not in TOOLS:
        TOOLS[iname] = (ifn, idesc, ischema)


def send(obj: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def handle(req: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    mid = req.get("id")
    method = req.get("method", "")

    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mt5-mac-live", "version": "2.0.0"},
        }}

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        tools = [
            {"name": name, "description": desc, "inputSchema": input_schema}
            for name, (_, desc, input_schema) in TOOLS.items()
        ]
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}}

    if method == "tools/call":
        params = req.get("params", {})
        name = params.get("name", "")
        args = params.get("arguments", {}) or {}
        if name not in TOOLS:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"tool not found: {name}"}}
        try:
            data = TOOLS[name][0](args)
            text = json.dumps(data, ensure_ascii=False, indent=2)
            return {"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": text}]}}
        except Exception as exc:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32000, "message": str(exc)}}

    if mid is not None:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"method not found: {method}"}}
    return None


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        resp = handle(req)
        if resp is not None:
            send(resp)


if __name__ == "__main__":
    main()
