# Hermes Bridge API — Full MCP Commands

Commands sent to `PythonBridge/orders.txt`, responses in `PythonBridge/results.txt`.

## Trading Commands

| Command | Format | Response | Description |
|---------|--------|----------|-------------|
| ORDER | `ORDER\|BUY\|0.01\|0\|0` | None | Market order BUY/SELL |
| CLOSEALL | `CLOSEALL` | None | Close all positions + cancel orders |
| CANCELALL | `CANCELALL` | None | Cancel pending orders |
| CONFIG | `CONFIG\|TP\|10` | `CONFIG\|OK\|TP=10.00` | Live parameter change |

## Data Commands

| Command | Format | Response | Description |
|---------|--------|----------|-------------|
| PRICE | `PRICE\|EURUSD.FX` | `PRICE\|sym\|bid\|ask\|spread\|digits` | Real-time bid/ask |
| RATES | `RATES\|EURUSD.FX\|M5\|50` | `RATES\|sym\|tf\|count\|timestamp,o,h,l,c,vol...` | OHLCV candles |
| RSI | `RSI\|EURUSD.FX\|M5\|14` | `RSI\|sym\|tf\|period\|val1\|val2...` | RSI indicator (50 values) |
| EMA | `EMA\|EURUSD.FX\|M5\|9` | `EMA\|sym\|tf\|period\|val1\|val2...` | EMA indicator (50 values) |
| ATR | `ATR\|EURUSD.FX\|M5\|14` | `ATR\|sym\|tf\|period\|val1\|val2...` | ATR indicator (50 values) |
| SYMBOLS | `SYMBOLS` | `SYMBOLS\|65\|EURUSD.FX\|GBPUSD.FX...` | All available symbols |

## Auto Data (every 2 seconds)

`ACCOUNT\|login\|balance\|equity\|margin\|free_margin\|profit\|leverage\|currency\|server`

## Live CONFIG Parameters

```
CONFIG|TP|12      → Set Take Profit to 12 pips
CONFIG|SL|2       → Set Stop Loss to 2 pips
CONFIG|TRAIL|0.5  → Set trailing stop threshold
CONFIG|BE|0.6     → Set breakeven threshold
CONFIG|SESSION|1  → Enable session filter (1=on, 0=off)
CONFIG|MARTINGALE|1 → Enable smart martingale
```

## EA Strategies

### CGGv16 Millionaire
- **Entry**: EMA 9/21 crossover + RSI filter
- **TP/SL**: 8/3 pips (adjustable via CONFIG)
- **Trailing Stop**: Moves SL to +1 pip when +$0.30
- **Breakeven**: Moves SL to entry when +$0.40
- **Session Filter**: Only trades 7am-9pm (London+NY)
- **Smart Martingale**: +50% lot after loss (max 3 levels)
- **Dynamic Lots**: 0.01→0.02→0.04→0.08 based on balance
- **ATR Adaptive**: TP scales with volatility
- **RSI Filter**: No buy above 70, no sell below 30
