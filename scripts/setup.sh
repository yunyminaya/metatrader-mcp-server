#!/bin/bash
# Script de configuración inicial para MetaTrader MCP Autonomous

echo "=========================================="
echo "MetaTrader MCP Server - Autonomous Setup"
echo "=========================================="
echo ""

# Crear directorio de configuración
CONFIG_DIR="$HOME/.metatrader-mcp"
mkdir -p "$CONFIG_DIR"

# Verificar Python
echo "✓ Verificando Python..."
python3 --version || { echo "✗ Python 3 no encontrado"; exit 1; }

# Instalar dependencias
echo "✓ Instalando dependencias..."
pip install -q MetaTrader5 numpy scikit-learn aiohttp mcp

# Crear configuración inicial
echo "✓ Creando configuración inicial..."
CONFIG_FILE="$CONFIG_DIR/config.json"

if [ -f "$CONFIG_FILE" ]; then
    echo "⚠ Configuración ya existe en $CONFIG_FILE"
    read -p "¿Sobreescribir? (s/N): " overwrite
    if [ "$overwrite" != "s" ] && [ "$overwrite" != "S" ]; then
        echo "✓ Manteniendo configuración existente"
        exit 0
    fi
fi

# Pedir datos de MT5
echo ""
echo "Configuración de MetaTrader 5:"
echo "--------------------------------"
read -p "Número de cuenta (login): " login
read -s -p "Password: " password
echo ""
read -p "Servidor (ej: MetaQuotes-Demo): " server

cat > "$CONFIG_FILE" << EOF
{
  "autonomo": true,
  "ciclo_minutos": 15,
  "max_operaciones_dia": 5,
  "mt5": {
    "login": $login,
    "password": "$password",
    "server": "$server",
    "path": null
  },
  "pares": ["EURUSD", "XAUUSD", "GBPUSD"],
  "timeframes": ["M15", "H1"],
  "estrategia": {
    "tipo": "fenix",
    "score_minimo": 95
  },
  "riesgo": {
    "kelly_fraccion": 0.25,
    "drawdown_max": 15,
    "perdida_diaria_max": 2,
    "perdidas_consecutivas_max": 3,
    "cooling_minutes": 30,
    "spread_max": 50,
    "max_posiciones_simultaneas": 3
  },
  "horario_operacion": {
    "inicio": "08:00",
    "fin": "22:00"
  },
  "dias_operacion": [0, 1, 2, 3, 4],
  "telegram": {
    "token": null,
    "chat_id": null
  }
}
EOF

echo ""
echo "✓ Configuración guardada en: $CONFIG_FILE"
echo ""

# Configurar Telegram (opcional)
read -p "¿Configurar notificaciones Telegram? (s/N): " setup_telegram
if [ "$setup_telegram" = "s" ] || [ "$setup_telegram" = "S" ]; then
    echo "Para obtener tu bot token:"
    echo "1. Abre @BotFather en Telegram"
    echo "2. Crea un nuevo bot"
    echo "3. Copia el token aquí:"
    read -p "Bot Token: " telegram_token
    read -p "Chat ID: " chat_id
    
    # Actualizar config con sed (simplificado)
    echo "⚠ Actualiza manualmente el archivo $CONFIG_FILE con tu token de Telegram"
fi

# Configurar Claude Desktop
echo ""
echo "Configurando Claude Desktop..."
CLAUDE_CONFIG_DIR="$HOME/Library/Application Support/Claude"
CLAUDE_CONFIG="$CLAUDE_CONFIG_DIR/claude_desktop_config.json"

if [ -d "$CLAUDE_CONFIG_DIR" ]; then
    echo "✓ Directorio de Claude encontrado"
    
    # Verificar si existe config
    if [ -f "$CLAUDE_CONFIG" ]; then
        echo "⚠ claude_desktop_config.json ya existe"
        echo "Agrega manualmente esto a la sección 'mcpServers':"
        echo ""
        echo '  "metatrader": {'
        echo '    "command": "metatrader-mcp-autonomous",'
        echo '    "args": ["--daemon"]'
        echo '  }'
        echo ""
    else
        echo "Creando configuración inicial..."
        mkdir -p "$CLAUDE_CONFIG_DIR"
        cat > "$CLAUDE_CONFIG" << 'EOF'
{
  "mcpServers": {
    "metatrader": {
      "command": "metatrader-mcp-autonomous",
      "args": ["--daemon"]
    }
  }
}
EOF
        echo "✓ Configuración de Claude Desktop creada"
    fi
else
    echo "⚠ No se encontró Claude Desktop"
    echo "Configura manualmente agregando a claude_desktop_config.json:"
    echo '  "metatrader": { "command": "metatrader-mcp-autonomous", "args": ["--daemon"] }'
fi

echo ""
echo "=========================================="
echo "Setup completado!"
echo "=========================================="
echo ""
echo "Próximos pasos:"
echo "1. Abre MetaTrader 5"
echo "2. Habilita 'Allow algorithmic trading' en Tools > Options > Expert Advisors"
echo "3. Reinicia Claude Desktop"
echo "4. O ejecuta manualmente: metatrader-mcp-autonomous --daemon"
echo ""
echo "Archivos importantes:"
echo "  Config:  $CONFIG_FILE"
echo "  DB:      $CONFIG_DIR/trading.db"
echo "  Logs:    Ver terminal"
echo ""
echo "Para emergencias:"
echo "  - Parada de emergencia: emergency_stop() en Claude"
echo "  - Cerrar todo: close_all_positions()"
echo ""
