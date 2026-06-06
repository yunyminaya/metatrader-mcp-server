#!/bin/bash
# ============================================================================
# control.sh — Control central del sistema MT5 MCP
# ============================================================================
# Uso: ./control.sh <comando>
#
# Comandos:
#   start       Inicia MCP HTTP server + trading loop
#   stop        Detiene todo
#   restart     Reinicia todo
#   status      Estado de todos los servicios
#   logs        Muestra logs en vivo
#   mcp         Solo el MCP HTTP server
#   trade       Solo el trading loop (run_mac.py)
#   dashboard   Web dashboard :5000
# ============================================================================

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
MCP_LOG="$REPO_DIR/logs/mcp_http.log"
TRADE_LOG="$REPO_DIR/logs/trade_loop.log"
PID_DIR="$REPO_DIR/logs"
MCP_PID_FILE="$PID_DIR/mcp_http.pid"
TRADE_PID_FILE="$PID_DIR/trade_loop.pid"
DASH_PID_FILE="$PID_DIR/dashboard.pid"

mkdir -p "$REPO_DIR/logs"

PYTHON="/Library/Frameworks/Python.framework/Versions/3.11/bin/python3"
LOGROTATE_LINES=10000

case "${1:-help}" in
  start)
    echo "🚀 Starting MT5 MCP system..."
    "$0" mcp
    sleep 2
    "$0" trade
    "$0" status
    ;;

  stop)
    echo "🛑 Stopping all services..."
    for pidfile in "$MCP_PID_FILE" "$TRADE_PID_FILE" "$DASH_PID_FILE"; do
      if [ -f "$pidfile" ]; then
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
          kill "$pid" 2>/dev/null && echo "  Killed PID $pid ($(basename $pidfile .pid))" || true
        fi
        rm -f "$pidfile"
      fi
    done
    echo "  Done"
    ;;

  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;

  mcp)
    if [ -f "$MCP_PID_FILE" ]; then
      old_pid=$(cat "$MCP_PID_FILE")
      if kill -0 "$old_pid" 2>/dev/null; then
        echo "MCP HTTP server already running (PID $old_pid)"
        exit 0
      fi
      rm -f "$MCP_PID_FILE"
    fi
    echo "Starting MCP HTTP server on 127.0.0.1:8000 ..."
    nohup "$PYTHON" "$REPO_DIR/mt5_mcp_http.py" --host 127.0.0.1 --port 8000 \
      >> "$MCP_LOG" 2>&1 &
    echo $! > "$MCP_PID_FILE"
    sleep 2
    if kill -0 $(cat "$MCP_PID_FILE") 2>/dev/null; then
      echo "  ✅ MCP HTTP server PID $(cat "$MCP_PID_FILE")"
    else
      echo "  ❌ MCP HTTP server failed to start — check $MCP_LOG"
      tail -5 "$MCP_LOG"
    fi
    ;;

  trade)
    if [ -f "$TRADE_PID_FILE" ]; then
      old_pid=$(cat "$TRADE_PID_FILE")
      if kill -0 "$old_pid" 2>/dev/null; then
        echo "Trading loop already running (PID $old_pid)"
        exit 0
      fi
      rm -f "$TRADE_PID_FILE"
    fi
    echo "Starting trading loop (run_mac.py) ..."
    nohup "$PYTHON" "$REPO_DIR/run_mac.py" \
      >> "$TRADE_LOG" 2>&1 &
    echo $! > "$TRADE_PID_FILE"
    sleep 1
    if kill -0 $(cat "$TRADE_PID_FILE") 2>/dev/null; then
      echo "  ✅ Trading loop PID $(cat "$TRADE_PID_FILE")"
    else
      echo "  ⚠️  Trading loop not running (normal if weekend)"
    fi
    ;;

  dashboard)
    if [ -f "$DASH_PID_FILE" ]; then
      old_pid=$(cat "$DASH_PID_FILE")
      if kill -0 "$old_pid" 2>/dev/null; then
        echo "Dashboard already running (PID $old_pid)"
        exit 0
      fi
      rm -f "$DASH_PID_FILE"
    fi
    echo "Starting web dashboard on 0.0.0.0:5000 ..."
    cd "$REPO_DIR" && nohup "$PYTHON" -m uvicorn web.dashboard:app --host 0.0.0.0 --port 5000 \
      >> "$REPO_DIR/logs/dashboard.log" 2>&1 &
    echo $! > "$DASH_PID_FILE"
    sleep 2
    if kill -0 $(cat "$DASH_PID_FILE") 2>/dev/null; then
      echo "  ✅ Dashboard PID $(cat "$DASH_PID_FILE") — http://localhost:5000"
    else
      echo "  ❌ Dashboard failed to start"
      tail -5 "$REPO_DIR/logs/dashboard.log"
    fi
    ;;

  status)
    echo "📊 MT5 MCP System Status"
    echo "========================"

    # Check each service
    for entry in "MCP HTTP|$MCP_PID_FILE|:8000" "Trading loop|$TRADE_PID_FILE" "Dashboard|$DASH_PID_FILE|:5000"; do
      IFS='|' read -r name pidfile extra <<< "$entry"
      if [ -f "$pidfile" ]; then
        pid=$(cat "$pidfile")
        if kill -0 "$pid" 2>/dev/null; then
          echo "  ✅ $name$extra — PID $pid"
        else
          echo "  ❌ $name — stale PID $pid (not running)"
          rm -f "$pidfile"
        fi
      else
        echo "  ⚪ $name — stopped"
      fi
    done

    # Quick health check
    if curl -sf http://127.0.0.1:8000/health >/dev/null 2>&1; then
      tools=$(curl -sf http://127.0.0.1:8000/tools 2>/dev/null | $PYTHON -c "import sys,json; print(json.load(sys.stdin)['count'])" 2>/dev/null || echo "?")
      echo "  📦 Tools: $tools"
      echo "  📊 Dashboard: http://localhost:5000"
    fi
    ;;

  logs)
    echo "📋 MCP log ($(wc -l < "$MCP_LOG" 2>/dev/null || echo 0) lines): $MCP_LOG"
    echo "📋 Trading log ($(wc -l < "$TRADE_LOG" 2>/dev/null || echo 0) lines): $TRADE_LOG"
    echo ""
    echo "--- MCP last 20 lines ---"
    tail -20 "$MCP_LOG" 2>/dev/null || echo "(no log yet)"
    echo ""
    echo "--- Trading loop last 10 lines ---"
    tail -10 "$TRADE_LOG" 2>/dev/null || echo "(no log yet)"
    ;;

  logrotate)
    echo "🔄 Rotating logs (keeping last $LOGROTATE_LINES lines)..."
    for log in "$MCP_LOG" "$TRADE_LOG" "$REPO_DIR/logs/dashboard.log"; do
      if [ -f "$log" ]; then
        lines=$(wc -l < "$log")
        if [ "$lines" -gt "$LOGROTATE_LINES" ]; then
          mv "$log" "${log}.old"
          tail -n "$LOGROTATE_LINES" "${log}.old" > "$log"
          rm -f "${log}.old"
          echo "  Rotated $log ($lines → $LOGROTATE_LINES lines)"
        else
          echo "  Skipped $log ($lines lines, under limit)"
        fi
      fi
    done
    ;;

  tail)
    echo "Following MCP log (Ctrl+C to stop)..."
    tail -f "$MCP_LOG"
    ;;

  help|*)
    echo "Uso: $0 {start|stop|restart|status|logs|logrotate|tail|mcp|trade|dashboard}"
    echo ""
    echo "  start       Inicia MCP + trading loop"
    echo "  stop        Detiene todo"
    echo "  restart     Reinicia todo"
    echo "  status      Estado de servicios"
    echo "  logs        Últimas líneas de logs"
    echo "  logrotate   Recorta logs a 10000 líneas"
    echo "  tail        Sigue log del MCP en vivo"
    echo "  mcp         Solo el servidor MCP HTTP"
    echo "  trade       Solo el trading loop"
    echo "  dashboard   Web dashboard en :5000"
    ;;
esac
