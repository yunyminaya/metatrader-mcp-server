# EA Companion — Signal Receiver

Ejecuta señales del Python MCP server a **velocidad tick** directamente en MT5.

## Instalación

1. Copiar `signal_receiver.ex5` a:
   ```
   C:\Users\<TU_USER>\AppData\Roaming\MetaQuotes\Terminal\<TERMINAL_ID>\MQL5\Experts\
   ```

2. Abrir MT5, arrastrar el EA a un gráfico (cualquier par, cualquier timeframe)

3. Configurar parámetros:
   - `SignalDirectory`: `MCP_Signals` (debe coincidir con Python)
   - `MagicNumber`: 20240601
   - `UseTrailingStop`: true
   - `UseBreakeven`: true
   - `VolatilityHedge`: true

4. Activar "Alow Automated Trading" en MT5

## Protocolo

```
Python escribe:  MCP_Signals\TRADE_SIGNAL_<id>.json
EA lee (cada tick) → ejecuta → escribe:
EA escribe:      MCP_Signals\TRADE_RESULT_<id>.json
Python lee resultado
```

## Señales soportadas

```json
{
  "symbol": "EURUSD",
  "type": "BUY",
  "volume": 0.1,
  "sl": 1.09500,
  "tp": 1.10500,
  "action": "market"
}
```

## Acciones
- `market` — abre orden BUY/SELL
- `close_all` — cierra todas las posiciones
- `modify_sl` — modifica SL de una posición por ticket

## Parámetros del EA

| Parámetro | Default | Descripción |
|-----------|---------|-------------|
| SignalDirectory | Signals | Directorio de señales |
| MagicNumber | 20240601 | Magic number del EA |
| UseTrailingStop | true | Trailing stop automático |
| TrailStartPips | 15 | Pips para activar trailing |
| TrailDistancePips | 10 | Distancia del trailing |
| UseBreakeven | true | Breakeven automático |
| BreakevenPips | 10 | Pips para breakeven |
| VolatilityHedge | true | Cerrar en spikes de volatilidad |
| MaxSpreadPips | 20 | Spread máximo aceptable |
| SignalTimeoutSec | 300 | Borrar señales viejas |
