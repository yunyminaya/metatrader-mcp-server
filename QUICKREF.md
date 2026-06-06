# MT5 MCP Quick Reference

## Commands (after `source ~/.zshrc`)

| Command | What it does |
|---------|-------------|
| `mt5:start` | Start MCP server + trading loop |
| `mt5:stop` | Stop everything |
| `mt5:restart` | Restart everything |
| `mt5:status` | Check what's running |
| `mt5:logs` | View recent logs |
| `mt5:tail` | Follow MCP log live |
| `mt5:mcp` | MCP HTTP server only |
| `mt5:trade` | Trading loop only (run_mac.py) |
| `mt5:dash` | Web dashboard on :5000 |

Or use directly: `./control.sh <command>`

## Services

| Service | Port | Purpose |
|---------|------|---------|
| MCP HTTP | :8000/sse | AI trading tools (86 tools) |
| Dashboard | :5000 | Web UI for account/positions/analytics |
| Stdio | stdin | Fallback mode (via opencode.json command) |

## Quick Trades (via chat when MCP is connected)

```
mt5_quick_buy symbol="EURUSD" sl_pips=15 tp_pips=30 confirm_live=false
mt5_quick_sell symbol="GBPUSD" sl_pips=20 tp_pips=40
mt5_account_summary
mt5_market_overview
```

## Live Trading

```bash
# Monday-Thursday: manual or auto
./control.sh start                    # MCP + trading loop

# The auto-trading loop scans every 5 min
# Only trades London/NY sessions
# Max 3 trades/day, min 60% confidence
```

## Architecture

```
opencode.json  →  mt5_mcp_http.py:8000  →  mt5_mac_mcp.py (86 tools)
                          ↓
                   mt5_direct_cli.py (Wine → MT5 API)
                          ↓
                   Wine → MetaTrader 5 → tastyfx-LIVE
```
