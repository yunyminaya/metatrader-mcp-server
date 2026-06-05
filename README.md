# MetaTrader MCP Server - Autónomo

Servidor MCP completo para trading **100% autónomo** en MetaTrader 5.

## Características

- **Modo Daemon** - Opera 24/7 sin intervención humana
- **Persistencia SQLite** - Estado guardado entre reinicios
- **Configuración Declarativa** - Setup inicial, luego autónomo
- **Circuit Breakers** - Protección automática de capital
- **ML Local** - Scoring 0-100 sin APIs externas
- **Notificaciones Telegram** - Alertas gratuitas
- **Reconexión Automática** - Si MT5 se cierra, reconecta solo

## Instalación

```bash
cd metatrader-mcp-autonomous
pip install -e .
```

## Configuración

### 1. Crear archivo de configuración

```bash
mkdir -p ~/.metatrader-mcp
cp config/default.json ~/.metatrader-mcp/config.json
```

### 2. Editar configuración

Edita `~/.metatrader-mcp/config.json` con tus datos:

```json
{
  "autonomo": true,
  "ciclo_minutos": 15,
  "mt5": {
    "login": 12345678,
    "password": "tu_password",
    "server": "MetaQuotes-Demo"
  },
  "pares": ["EURUSD", "XAUUSD", "GBPUSD"],
  "estrategia": {
    "tipo": "fenix",
    "score_minimo": 95
  },
  "riesgo": {
    "drawdown_max": 15,
    "perdida_diaria_max": 2
  },
  "telegram": {
    "token": "tu_bot_token",
    "chat_id": "tu_chat_id"
  }
}
```

### 3. Configurar Claude Desktop

Edita `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "metatrader": {
      "command": "metatrader-mcp-autonomous",
      "args": ["--daemon"]
    }
  }
}
```

## Uso

### Modo Autónomo (Daemon)

```bash
# Iniciar en modo autónomo
metatrader-mcp-autonomous --daemon --config mi_config.json
```

### Modo Manual (MCP Tools)

```bash
# Iniciar servidor MCP manual
metatrader-mcp-autonomous
```

Luego en Claude:
- `get_account_info()` - Ver balance
- `place_market_order("EURUSD", "buy", 0.1)` - Comprar
- `start_autonomous_mode(["EURUSD"], "fenix", 1.0, 5)` - Iniciar autónomo
- `stop_autonomous_mode()` - Detener autónomo
- `emergency_stop()` - Parada de emergencia

### Estrategias Disponibles

- `fenix` - Ultra-selectivo, score >= 95, 7 detectores anti-manipulación
- `trend` - Trend following con EMAs y MACD
- `mean_reversion` - Reversión a la media con RSI/Bollinger
- `breakout` - Breakout de rangos con ATR

## Protecciones Automáticas

| Protección | Descripción |
|------------|-------------|
| Circuit Breaker | Stop automático si drawdown > 15% |
| Daily Loss | Stop si pérdida diaria > 2% |
| Consecutive Losses | Cooling después de 3 pérdidas seguidas |
| Spread Check | No opera si spread > 50 pips |
| Kelly Criterion | Sizing automático fraccional |
| Breakeven Auto | Mueve SL a entrada tras +20 pips |
| Trailing Stop | Escalonado tras +50 pips |

## Base de Datos

Ubicación: `~/.metatrader-mcp/trading.db`

Tablas:
- `trades` - Historial completo de operaciones
- `risk_metrics` - Métricas de riesgo diarias
- `account_snapshots` - Snapshots de cuenta
- `daemon_state` - Estado del daemon

## Entrenar ML

```bash
# Entrenar modelo con trades históricos
python -c "
from src.ml_local import LocalMLScorer
from src.database import TradingDatabase
db = TradingDatabase('~/.metatrader-mcp/trading.db')
ml = LocalMLScorer(db)
result = ml.train(days=30)
print(f'Precisión: {result[\"accuracy\"]:.2%}')
"
```

## Comandos de Emergencia

- **Parada total**: `emergency_stop()`
- **Reset circuit breaker**: `reset_circuit_breaker()`
- **Cerrar todo**: `close_all_positions()`
- **Forzar ciclo**: `force_cycle_now()`

## Logs

- Terminal: output en tiempo real
- SQLite: `~/.metatrader-mcp/trading.db`
- Telegram: notificaciones push

## Arquitectura

```
Claude (setup inicial)
    ↓
MCP Server (24/7 daemon)
    ├─ Ciclo automático (15 min)
    ├─ ML local (scoring)
    ├─ Risk Manager (circuit breakers)
    ├─ SQLite (persistencia)
    └─ Notificador (Telegram)
    ↓
MetaTrader 5
```

## Troubleshooting

**MT5 no conecta:**
- Verificar credenciales
- Asegurar que MT5 esté abierto
- Habilitar "Allow algorithmic trading" en MT5

**Daemon no inicia:**
- Verificar `~/.metatrader-mcp/config.json`
- Revisar logs de error
- Probar en modo manual primero

**Circuit breaker activado:**
- Revisar métricas con `get_risk_metrics()`
- Reset manual con `reset_circuit_breaker()`
- Esperar cooling period

## License

MIT - Trading bajo tu propio riesgo.
