#!/bin/bash
# ============================================================================
# setup_aliases.sh — Instala aliases de shell para el sistema MT5 MCP
# ============================================================================
# Carga automática: agrega al final de ~/.zshrc (o ~/.bashrc)
# ============================================================================

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTROL="$REPO_DIR/control.sh"
ALIASES=$(cat <<EOF

# ── MT5 MCP Trading System ──
alias mt5="$CONTROL"
alias mt5:start="$CONTROL start"
alias mt5:stop="$CONTROL stop"
alias mt5:restart="$CONTROL restart"
alias mt5:status="$CONTROL status"
alias mt5:logs="$CONTROL logs"
alias mt5:mcp="$CONTROL mcp"
alias mt5:trade="$CONTROL trade"
alias mt5:dash="$CONTROL dashboard"
alias mt5:tail="$CONTROL tail"
# ───────────────────────────
EOF
)

RC_FILE="$HOME/.zshrc"
if [ ! -f "$RC_FILE" ]; then
  RC_FILE="$HOME/.bashrc"
fi

# Check if already installed
if grep -q "MT5 MCP Trading System" "$RC_FILE" 2>/dev/null; then
  echo "✅ Aliases already installed in $RC_FILE"
else
  echo "$ALIASES" >> "$RC_FILE"
  echo "✅ Aliases added to $RC_FILE"
  echo ""
  echo "Run 'source $RC_FILE' or open a new terminal for them to take effect."
fi
