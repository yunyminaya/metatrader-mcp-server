# CHECKLIST COMPLETO — MetaTrader MCP Server

## 1. DEPENDENCIAS (pip install)

```
metatrader-mcp-server/
├── requirements.txt (crear con:)
│   ├── mcp>=1.0.0
│   ├── httpx>=0.27.0
│   ├── python-dotenv>=1.0.0
│   ├── MetaTrader5>=5.0.45       # solo Windows
│   ├── pandas>=2.0.0
│   └── numpy>=1.24.0             # opcional, usado por pandas
│
├── pip install -e .              # instala en modo dev
```

## 2. ARCHIVOS DEL SISTEMA (19 módulos, 83 tools)

### Núcleo MT5
```
src/metatrader_mcp/
├── __init__.py                   # package marker
├── server.py                     # 83 tools registrados con @mcp.tool()
├── utils.py                      # init(), get_client(), run_mcp()
│
└── tools/
    ├── __init__.py               # package marker
    ├── conviction.py             # 10 indicadores + ML + MTF + spread
    ├── regime.py                 # MTF regime (D1/H4/H1) + sesión
    ├── backtest.py               # backtest + walk-forward + Monte Carlo
    ├── selflearn.py              # predicciones vs resultados
    ├── papertrade.py             # simulación + auto-insurance + ML data
    ├── builder.py                # constructor de estrategias
    ├── scheduler.py              # auto-ejecución + emergency + ML
    ├── dashboard.py              # snapshot completo del sistema
    ├── guard.py                  # SL/TP + trailing + breakeven + spread
    ├── insurance.py              # fondo de seguro 5%
    ├── emergency.py              # freno 5 pérdidas / 30% dd
    ├── heartbeat.py              # watchdog scheduler + guard
    ├── live.py                   # orden inteligente + ATR SL/TP
    ├── divergence.py             # divergencia RSI + MACD
    ├── market.py                 # sesiones + noticias + spread
    ├── correlation.py            # matriz de correlación
    ├── analytics.py              # Sharpe/Sortino/Calmar + Monte Carlo
    └── predictor.py              # ML Naive Bayes + auto-aprendizaje
```

### Archivos raíz
```
metatrader-mcp-server/
├── run_live.py                   # loop principal infinito
├── .env                          # credenciales MT5 (CREAR)
├── .env.template                 # template de configuración
├── CHECKLIST.md                  # este archivo
├── CLAUDE.md                     # guía del proyecto
├── setup.py                      # package setup
└── data/                         # se crea automáticamente
    ├── selflearn.json
    ├── papertrade.json
    ├── scheduler.json
    ├── guard.json
    ├── insurance.json
    ├── emergency.json
    ├── heartbeat.json
    ├── strategies.json
    ├── predictor_data.json
    └── predictor_model.json
```

## 3. CONFIGURACIÓN .env

```env
# ── MT5 Credentials (REQUIRED) ──
LOGIN=12345678
PASSWORD=tu_password
SERVER=TuBroker

# ── MT5 Path (opcional) ──
MT5_PATH=C:\Program Files\MetaTrader 5\terminal64.exe

# ── Transport ──
MCP_TRANSPORT=sse
MCP_HOST=0.0.0.0
MCP_PORT=8080

# ── Live Loop Timing ──
LIVE_SCHEDULER_INTERVAL=60
LIVE_GUARD_INTERVAL=60
LIVE_HEARTBEAT_INTERVAL=300
```

## 4. REQUISITOS DEL SISTEMA

```
□ Python 3.10+
□ Windows (MetaTrader 5 terminal SOLO Windows)
   o macOS/Linux vía Wine/VM con MT5
□ MetaTrader 5 instalado y conectado a broker
□ Cuenta demo o real activa en MT5
□ Conexión a internet estable
```

## 5. PASOS PARA ARRANCAR

```
1. □ Instalar MetaTrader 5 (Windows)
2. □ Conectar MT5 al broker (demo o real)
3. □ pip install -e .           # instalar dependencias
4. □ cp .env.template .env      # crear config
5. □ Editar .env con LOGIN/PASSWORD/SERVER
6. □ python run_live.py         # ARRANCA EL SISTEMA
```

## 6. VERIFICACIÓN (después de arrancar)

```
□ python -c "from metatrader_mcp.tools.conviction import decide; print('OK')"
□ python -c "from metatrader_mcp.tools.predictor import train; print('OK')"
□ python -c "from metatrader_mcp.tools.analytics import full_report; print('OK')"
□ python -c "import ast; ast.parse(open('src/metatrader_mcp/server.py').read()); print('83 tools syntax OK')"
```

## 7. FLUJO DE TRADING

```
run_live.py
├── cada 60 min → news_check()           ─ si hay evento → SKIP
│               → session_check()        ─ si baja liquidez → SKIP
│               → emergency_check()       ─ si brake activo → SKIP
│               → scheduler_tick()        ─ escanea convicción
│                   ├── conviction.decide() → 10 indicadores + ML
│                   ├── predictor.modulate() → blending 70/30
│                   └── papertrade.open() → registra features
│
├── cada 60 seg → guard_check()
│   ├── trailing_stop()       ─ mueve SL con el precio
│   ├── breakeven()           ─ SL = entry si en ganancia
│   ├── spread_check()        ─ alerta si spread alto
│   ├── partial_tp()          ─ cierra 50% a 1:1 R:R
│   └── correlation_risk()    ─ alerta si >30% correlacionado
│
└── cada hora → dashboard_log()
    └── health_score + balance + posiciones
```

## 8. CAPAS DE SEGURIDAD (orden de defensa)

```
1. Spread guard       → no tradea si spread >15 pips
2. Session filter     → no tradea fuera de London/NY
3. News filter        → no tradea 2h antes de NFP/FOMC/CPI
4. MTF alignment      → no tradea si D1 y H4 contradicen
5. Min confidence     → no tradea si convicción <60
6. Daily limit        → máximo 3 trades/día
7. Drawdown limit     → 10% drawdown diario → frena
8. Kelly sizing       → tamaño de lote óptimo
9. Trailing stop      → SL se mueve solo
10. Breakeven         → SL = entry si en ganancia
11. Partial TP        → 50% a 1:1, deja correr resto
12. Insurance fund    → 5% de cada profit guardado
13. Emergency brake   → 5 pérdidas O 30% dd → para TODO
14. Correlation risk  → >30% en mismo grupo → alerta
15. Heartbeat         → watchdog scheduler + guard
```

## 9. MANTENIMIENTO

```
□ predictor_train()     ─ después de 10+ trades cerrados
□ selflearn_report()    ─ revisar calibración semanal
□ emergency_reset()     ─ si el brake se activó
□ insurance_status()    ─ revisar fondo de seguro
□ analytics_report()    ─ revisar Sharpe, drawdown, Monte Carlo
□ backtest_run()        ─ validar estrategias nuevas
□ backtest_walk_forward() ─ validar en datos no vistos
```

## 10. COMANDOS RÁPIDOS

```bash
# Arrancar
python run_live.py

# Arrancar custom
python run_live.py --interval 30 --guard-interval 30

# Solo servidor MCP (para usar con Claude)
python -m metatrader_mcp.server --transport stdio

# Verificar todo
python -c "import ast; ast.parse(open('src/metatrader_mcp/server.py').read()); print('OK')"
```
