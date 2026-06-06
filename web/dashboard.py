"""
Web Dashboard — panel de control en vivo del sistema de trading.

Servidor FastAPI autónomo que lee los archivos JSON de data/
y serve una interfaz web completa con:
  - Cuenta (balance, equity, margen)
  - Posiciones abiertas
  - Scheduler status
  - PaperTrade portfolio
  - Equity curve
  - Análisis (Sharpe, Sortino, Calmar)
  - Emergency / Insurance / Heartbeat status
  - Logs recientes

Ejecutar: uvicorn web.dashboard:app --host 0.0.0.0 --port 5000
"""
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Trading Dashboard")

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _read_data(name: str) -> dict:
    path = DATA_DIR / name
    try:
        if path.exists():
            with open(path) as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _read_logs(n: int = 50) -> list:
    log_path = DATA_DIR / "trades.log"
    try:
        if log_path.exists():
            with open(log_path) as f:
                lines = f.readlines()
            return [l.strip() for l in lines[-n:]]
    except Exception:
        pass
    return []


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{ font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
       background:#0d1117; color:#c9d1d9; padding:20px; }}
h1 {{ color:#58a6ff; margin-bottom:20px; }}
h2 {{ color:#8b949e; font-size:16px; margin:20px 0 10px;
       border-bottom:1px solid #21262d; padding-bottom:6px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(280px,1fr));
         gap:14px; margin-bottom:20px; }}
.card {{ background:#161b22; border:1px solid #21262d; border-radius:8px;
         padding:16px; }}
.card .label {{ color:#8b949e; font-size:12px; text-transform:uppercase; letter-spacing:.5px; }}
.card .value {{ font-size:22px; font-weight:600; margin-top:4px; }}
.card .value.green {{ color:#3fb950; }}
.card .value.red {{ color:#f85149; }}
.card .value.yellow {{ color:#d29922; }}
.card .value.blue {{ color:#58a6ff; }}
table {{ width:100%; border-collapse:collapse; font-size:13px; }}
th,td {{ text-align:left; padding:8px 10px; border-bottom:1px solid #21262d; }}
th {{ color:#8b949e; font-weight:500; }}
tr:hover {{ background:#1c2128; }}
.status-on {{ color:#3fb950; }}
.status-off {{ color:#f85149; }}
.status-warn {{ color:#d29922; }}
.ml-auto {{ margin-left:auto; }}
.flex {{ display:flex; align-items:center; }}
.chart-container {{ background:#161b22; border:1px solid #21262d; border-radius:8px;
                   padding:16px; margin-bottom:20px; height:300px; }}
.log-line {{ font-family:'SFMono-Regular',Consolas,monospace; font-size:11px;
             color:#484f58; padding:2px 0; }}
.log-line.now {{ color:#8b949e; }}
</style>
</head>
<body>
<h1>⚡ Trading Dashboard</h1>
<div id="ts" style="color:#484f58;font-size:13px;margin-bottom:20px;"></div>

<h2>📊 Account</h2>
<div class="grid" id="account"></div>

<h2>📈 Open Positions</h2>
<div id="positions">Loading...</div>

<h2>🛡️ System Status</h2>
<div class="grid" id="system"></div>

<h2>📉 Equity Curve</h2>
<div class="chart-container"><canvas id="equityChart"></canvas></div>

<h2>📋 Recent Logs</h2>
<div id="logs">Loading...</div>

<h2>🧠 Sentiment & ML</h2>
<div id="sentiment">Loading...</div>

<script>
const BASE = '';

async function fetchJSON(url) {{
    try {{
        const r = await fetch(BASE + url);
        if (!r.ok) return {{}};
        return await r.json();
    }} catch(e) {{ return {{}}; }}
}}

function val(v, cls='') {{
    const c = v !== undefined && v !== null ? 'value' + (cls ? ' '+cls : '') : 'value';
    const t = v !== undefined && v !== null ? v : '—';
    return `<div class="${{c}}">${{t}}</div>`;
}}

function fmtNum(n, d=2) {{
    if (n === null || n === undefined) return '—';
    return Number(n).toFixed(d);
}}

function fmtPct(n) {{ return fmtNum(n,1) + '%'; }}

async function loadDashboard() {{
    // Account info from scheduler/paper states
    const sch = await fetchJSON('/data/scheduler');
    const pt  = await fetchJSON('/data/papertrade');
    const ins = await fetchJSON('/data/insurance');
    const emg = await fetchJSON('/data/emergency');
    const hb  = await fetchJSON('/data/heartbeat');
    const opt = await fetchJSON('/data/optimizer');
    const sl  = await fetchJSON('/data/selflearn');

    // Account
    const acct = pt?.portfolio || {{}};
    const bal = acct.balance || 0;
    const eq  = acct.equity || bal;
    const mrg = acct.margin || 0;
    const frm = acct.free_margin || (eq - mrg);
    const pnl = acct.total_pnl || 0;
    const win = acct.win_rate || 0;
    const trd = acct.total_trades || 0;

    document.getElementById('account').innerHTML = `
        <div class="card"><div class="label">Balance</div>${{val('$'+fmtNum(bal,2),bal>=0?'green':'red')}}</div>
        <div class="card"><div class="label">Equity</div>${{val('$'+fmtNum(eq,2),eq>=bal?'green':'yellow')}}</div>
        <div class="card"><div class="label">Free Margin</div>${{val('$'+fmtNum(frm,2))}}</div>
        <div class="card"><div class="label">Margin Level</div>${{val(bal>0?fmtPct(eq/bal*100):'—')}}</div>
        <div class="card"><div class="label">Total PnL</div>${{val('$'+fmtNum(pnl,2),pnl>=0?'green':'red')}}</div>
        <div class="card"><div class="label">Win Rate</div>${{val(fmtPct(win))}}</div>
        <div class="card"><div class="label">Total Trades</div>${{val(trd)}}</div>
        <div class="card"><div class="label">Consecutive Losses</div>${{val(sch?.consecutive_losses || 0, (sch?.consecutive_losses||0)>=3?'red':'')}}</div>
    `;

    // Positions
    const positions = acct.positions || [];
    const pHtml = positions.length
        ? `<table><tr><th>Ticket</th><th>Symbol</th><th>Type</th><th>Volume</th><th>Entry</th><th>SL</th><th>TP</th><th>PnL</th></tr>
           ${{positions.map(p => `<tr>
               <td>${{p.id||p.ticket||'—'}}</td>
               <td>${{p.symbol||'—'}}</td>
               <td>${{p.type||'—'}}</td>
               <td>${{p.volume||'—'}}</td>
               <td>${{p.entry_price||p.price?fmtNum(p.price,5):'—'}}</td>
               <td>${{p.stop_loss?fmtNum(p.stop_loss,5):'—'}}</td>
               <td>${{p.take_profit?fmtNum(p.take_profit,5):'—'}}</td>
               <td class="${{(p.pnl||0)>=0?'green':'red'}}">${{p.pnl?fmtNum(p.pnl,2):'—'}}</td>
           </tr>`).join('')}}
           </table>`
        : '<p style="color:#484f58">No open positions</p>';
    document.getElementById('positions').innerHTML = pHtml;

    // System
    document.getElementById('system').innerHTML = `
        <div class="card"><div class="label">Scheduler</div>${{val(sch?.enabled?'ON':'OFF',sch?.enabled?'green':'red')}}</div>
        <div class="card"><div class="label">Trades Today</div>${{val((sch?.trades_today||0)+' / '+(sch?.daily_limit||'-'))}}</div>
        <div class="card"><div class="label">Insurance Fund</div>${{val('$'+fmtNum(ins?.balance||0,2),'green')}}</div>
        <div class="card"><div class="label">Emergency Brake</div>${{val(emg?.brake_active?'ACTIVE':'OK',emg?.brake_active?'red':'green')}}</div>
        <div class="card"><div class="label">Heartbeat</div>${{val(hb?.scheduler_alive?'ALIVE':'DEAD',hb?.scheduler_alive?'green':'red')}}</div>
        <div class="card"><div class="label">AutoOptimizer</div>${{val(opt?.autooptimizer?.enabled?'ON':'OFF',opt?.autooptimizer?.enabled?'green':'warn')}}</div>
        <div class="card"><div class="label">ML Samples</div>${{val(sl?.total_samples||0)}}</div>
        <div class="card"><div class="label">ML Accuracy</div>${{val(sl?.win_rate?fmtPct(sl.win_rate):'—')}}</div>
    `;

    // Logs
    const logs = await fetchJSON('/data/logs');
    const logLines = Array.isArray(logs) ? logs.slice(-30).reverse() : [];
    document.getElementById('logs').innerHTML = logLines.length
        ? logLines.map(l => `<div class="log-line now">${{l}}</div>`).join('')
        : '<p style="color:#484f58">No logs yet</p>';

    // ML & Sentiment status
    document.getElementById('sentiment').innerHTML = `
        <div class="grid" style="margin-bottom:0">
            <div class="card"><div class="label">Model Trained</div>${{val(sl?.model_trained?'Yes':'No',sl?.model_trained?'green':'yellow')}}</div>
            <div class="card"><div class="label">Total Predictions</div>${{val(sl?.total_predictions||0)}}</div>
            <div class="card"><div class="label">Calibration Score</div>${{val(sl?.calibration_score?fmtNum(sl.calibration_score,3):'—')}}</div>
            <div class="card"><div class="label">Avg Edge %</div>${{val(sl?.avg_edge?fmtPct(sl.avg_edge):'—')}}</div>
        </div>
    `;

    // Equity curve chart
    const ec = await fetchJSON('/data/analytics');
    const eqCurve = ec?.equity_curve || [];
    if (eqCurve.length > 1 && window.equityChart) {{
        window.equityChart.data.labels = eqCurve.map((_,i) => i);
        window.equityChart.data.datasets[0].data = eqCurve;
        window.equityChart.update();
    }}
}}

async function init() {{
    // Update timestamp
    document.getElementById('ts').textContent = 'Last updated: ' + new Date().toLocaleString();

    // Init chart
    const ctx = document.getElementById('equityChart').getContext('2d');
    window.equityChart = new Chart(ctx, {{
        type: 'line',
        data: {{
            labels: [],
            datasets: [{{
                label: 'Equity',
                data: [],
                borderColor: '#58a6ff',
                backgroundColor: 'rgba(88,166,255,0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 0,
            }}]
        }},
        options: {{
            responsive: true,
            maintainAspectRatio: false,
            plugins: {{ legend: {{ display: false }} }},
            scales: {{
                x: {{ display: false }},
                y: {{ grid: {{ color: '#21262d' }}, ticks: {{ color:'#8b949e' }} }}
            }}
        }}
    }});

    await loadDashboard();
    setInterval(loadDashboard, 10000);
}}

document.addEventListener('DOMContentLoaded', init);
</script>
</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTML_TEMPLATE


@app.get("/data/{name}")
async def get_data(name: str):
    mapping = {
        "scheduler": "scheduler.json",
        "papertrade": "papertrade.json",
        "insurance": "insurance.json",
        "emergency": "emergency.json",
        "heartbeat": "heartbeat.json",
        "optimizer": "optimizer.json",
        "selflearn": "selflearn.json",
        "predictor": "predictor.json",
        "analytics": "analytics.json",
    }
    fname = mapping.get(name)
    if not fname:
        return {}
    return _read_data(fname)


@app.get("/data/logs")
async def get_logs():
    return _read_logs(100)
