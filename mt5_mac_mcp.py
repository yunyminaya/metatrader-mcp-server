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


def _guard_live_order(order_type: str, symbol: str, volume: float, sl: float, tp: float) -> Dict[str, Any]:
    account = _mt5_direct({"action": "account"})
    price = _mt5_direct({"action": "price", "symbol": symbol})
    positions = _mt5_direct({"action": "positions"})
    check = _mt5_direct({
        "action": "check_order",
        "symbol": symbol,
        "type": order_type.upper(),
        "volume": volume,
        "stop_loss": sl,
        "take_profit": tp,
        "comment": "mcp_preflight",
    })

    reasons = []
    if volume > MAX_LIVE_VOLUME:
        reasons.append(f"volume {volume:.2f} > max_live_volume {MAX_LIVE_VOLUME:.2f}")
    if positions.get("count", 0) >= MAX_OPEN_POSITIONS:
        reasons.append(f"open_positions {positions['count']} >= limit {MAX_OPEN_POSITIONS}")
    spread_points = int(price.get("spread", 0) or 0)
    if spread_points > MAX_SPREAD_POINTS:
        reasons.append(f"spread {spread_points} points > limit {MAX_SPREAD_POINTS}")
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

    return {
        "allowed": not reasons,
        "reasons": reasons,
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


def tool_strategy_delete(args: Dict[str, Any]) -> Dict[str, Any]:
    name = str(args["name"]).strip()
    strategies = _load_strategies()
    existed = name in strategies
    if existed:
        del strategies[name]
        _save_strategies(strategies)
    return {"deleted": existed, "name": name, "count": len(strategies)}


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
    "mt5_auto_trade": (tool_auto_trade, "ONE-SHOT: escanea, decide, verifica guardias + ejecuta el mejor trade. Sistema completo en un comando.", schema({
        "symbols": {"type": "array", "items": {"type": "string"}, "default": ["EURUSD","GBPUSD","USDJPY","USDCAD","AUDUSD"]},
        "volume": {"type": "number", "default": 0.01},
        "min_confidence": {"type": "integer", "default": 60},
        "max_spread_points": {"type": "integer", "default": 80},
        "dry_run": {"type": "boolean", "default": True},
        "confirm_live": {"type": "boolean", "default": False},
    })),
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
