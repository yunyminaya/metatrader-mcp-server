#!/usr/bin/env python3
"""
mt5_mcp_http.py — MCP over HTTP/SSE transport.

Corre como servidor web persistente. opencode se conecta via URL.
Así sobrevive a restart de opencode sin perder tools ni estado.

Uso:
    python3 mt5_mcp_http.py                        # :8000
    python3 mt5_mcp_http.py --port 8080             # puerto custom
    python3 mt5_mcp_http.py --host 127.0.0.1        # solo local

Luego en opencode.json:
    "mcp": { "metatrader": { "type": "url", "url": "http://127.0.0.1:8000/sse" } }
"""
import asyncio
import json
import os
import sys
import uuid
import signal
import argparse
from typing import Dict, Any, Optional

from fastapi import FastAPI, Request
from fastapi.responses import Response
from sse_starlette.sse import EventSourceResponse
import uvicorn

sys.path.insert(0, os.path.dirname(__file__))
from mt5_mac_mcp import TOOLS, handle, send


app = FastAPI(title="MT5 MCP HTTP Server")

# Active SSE connections: session_id -> asyncio.Queue
_sessions: Dict[str, asyncio.Queue] = {}
_shutdown = False


async def _sse_dispatcher(request: Request):
    session_id = str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()
    _sessions[session_id] = queue

    async def event_generator():
        try:
            # Tell client the POST endpoint
            yield {"event": "endpoint", "data": f"/messages?sessionId={session_id}"}
            yield {"event": "connected", "data": json.dumps({"sessionId": session_id})}

            while not _shutdown:
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    if msg is None:
                        break
                    yield {"event": "message", "data": msg}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        except asyncio.CancelledError:
            pass
        finally:
            _sessions.pop(session_id, None)

    return EventSourceResponse(event_generator())


@app.get("/sse")
async def sse_endpoint(request: Request):
    return await _sse_dispatcher(request)


@app.post("/messages")
async def messages_endpoint(request: Request):
    session_id = request.query_params.get("sessionId")
    if not session_id or session_id not in _sessions:
        return Response(status_code=404, content="Session not found")

    body = await request.json()
    resp = handle(body)
    if resp is not None:
        await _sessions[session_id].put(json.dumps(resp, ensure_ascii=False))
    return Response(status_code=202)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "sessions": len(_sessions),
        "tools": len(TOOLS),
    }


@app.get("/tools")
async def list_tools():
    return {
        "count": len(TOOLS),
        "tools": [
            {"name": name, "description": desc[:120]}
            for name, (_, desc, _) in TOOLS.items()
        ],
    }


def _shutdown_handler(signum, frame):
    global _shutdown
    _shutdown = True
    # Clear all session queues so SSE loops break
    for q in _sessions.values():
        q.put_nowait(None)
    sys.exit(0)


def main():
    global _shutdown
    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    print(f"MT5 MCP HTTP server: http://{args.host}:{args.port}/sse")
    print(f"Tools loaded: {len(TOOLS)}")
    print(f"Health: http://{args.host}:{args.port}/health")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
