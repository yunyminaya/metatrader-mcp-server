#!/usr/bin/env python3
"""Call an MCP tool directly via stdin/stdout protocol. Usage:
  python3 mcp_call.py <tool_name> <json_args>
  python3 mcp_call.py list            # list all tools
  python3 mcp_call.py account         # no-arg tool
"""
import json, sys, os, subprocess, time, socket, threading, select

MCP_SCRIPT = os.path.join(os.path.dirname(__file__), "mt5_mac_mcp.py")

def _call_mcp(tool: str, args: dict, timeout: float = 120) -> dict:
    """Start MCP, send one tools/call, read response, return result."""
    proc = subprocess.Popen(
        [sys.executable, MCP_SCRIPT],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    req_id = 1
    init = {"jsonrpc": "2.0", "id": req_id, "method": "initialize"}
    req_id += 1
    notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    tools_call = {
        "jsonrpc": "2.0", "id": req_id, "method": "tools/call",
        "params": {"name": tool, "arguments": args},
    }

    payload = json.dumps(init) + "\n" + json.dumps(notif) + "\n" + json.dumps(tools_call) + "\n"

    try:
        out, err = proc.communicate(input=payload, timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return {"error": "timeout"}
    except Exception as e:
        return {"error": str(e)}

    if err:
        sys.stderr.write(err + "\n")

    responses = []
    for line in out.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            responses.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    for r in responses:
        if r.get("id") == req_id:
            if "result" in r:
                content = r["result"].get("content", [])
                if content:
                    text = content[0].get("text", "{}")
                    try:
                        return json.loads(text)
                    except json.JSONDecodeError:
                        return {"text": text}
                return r["result"]
            if "error" in r:
                return {"error": r["error"]["message"]}
    if responses:
        return {"raw_responses": responses}
    return {"error": "no response from MCP"}


def _list_tools() -> list:
    proc = subprocess.Popen(
        [sys.executable, MCP_SCRIPT],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    out, err = proc.communicate(input=json.dumps(req) + "\n", timeout=15)
    for line in out.strip().split("\n"):
        if not line:
            continue
        try:
            resp = json.loads(line)
            if "result" in resp and "tools" in resp["result"]:
                return resp["result"]["tools"]
        except:
            pass
    return []


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: mcp_call.py <tool_name> [json_args]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "list":
        tools = _list_tools()
        for t in tools:
            print(f"  {t['name']}: {t['description'][:80]}")
        print(f"\nTotal: {len(tools)} tools")
        sys.exit(0)

    args = {}
    if len(sys.argv) >= 3:
        try:
            args = json.loads(sys.argv[2])
        except json.JSONDecodeError:
            print(f"Invalid JSON args: {sys.argv[2]}")
            sys.exit(1)

    result = _call_mcp(cmd, args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
