#!/bin/bash
# ============================================================================
# install_launchd.sh — Instala plist de launchd para inicio automático
# ============================================================================
# Esto hace que el MCP HTTP server arranque solo al iniciar sesión
# y se reinicie automáticamente si falla.
# ============================================================================

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PLIST_DEST="$HOME/Library/LaunchAgents/com.metatrader.mcp.plist"

cat > "$PLIST_DEST" << PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
 "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.metatrader.mcp</string>

    <key>ProgramArguments</key>
    <array>
        <string>/Library/Frameworks/Python.framework/Versions/3.11/bin/python3</string>
        <string>${REPO_DIR}/mt5_mcp_http.py</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>8000</string>
    </array>

    <key>WorkingDirectory</key>
    <string>${REPO_DIR}</string>

    <key>StandardOutPath</key>
    <string>${REPO_DIR}/logs/mcp_http.log</string>

    <key>StandardErrorPath</key>
    <string>${REPO_DIR}/logs/mcp_http.log</string>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>EnvironmentVariables</key>
    <dict>
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/Library/Frameworks/Python.framework/Versions/3.11/bin</string>
    </dict>
</dict>
</plist>
PLISTEOF

chmod 644 "$PLIST_DEST"

# Load it
launchctl load "$PLIST_DEST" 2>/dev/null && echo "✅ launchd plist loaded" || echo "⚠️  Could not load plist (might already be loaded)"

# Verify
if launchctl list com.metatrader.mcp >/dev/null 2>&1; then
  echo "✅ Service registered: com.metatrader.mcp"
  echo "   Starts automatically on login + auto-restart on crash"
else
  echo "❌ Service not registered"
fi

echo ""
echo "To uninstall:"
echo "  launchctl unload $PLIST_DEST"
echo "  rm $PLIST_DEST"
