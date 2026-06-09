import os
import sys
from typing import Any, Optional, Union

# Detect MetaTrader5 availability
_MT5_AVAILABLE = False
try:
    import MetaTrader5
    _MT5_AVAILABLE = True
except ImportError:
    pass

# Detect platform
_IS_WINDOWS = sys.platform.startswith("win")


class _NoopClient:
    """Client that returns helpful errors when MT5 is not connected.
    Prevents tools from crashing on Mac/Linux without a remote connection."""
    class _NoopSub:
        def __getattr__(self, name):
            return lambda *a, **kw: {"success": False, "error": "MT5 not connected. Export MT5_REMOTE_URL=http://WINDOWS_IP:8080/api"}
    def __init__(self):
        self.account = self._NoopSub()
        self.market = self._NoopSub()
        self.order = self._NoopSub()
        self.history = self._NoopSub()
    def connect(self): return False
    def disconnect(self): return True
    def is_connected(self): return False


def resolve_transport_config(transport=None, host=None, port=None):
	"""Resolve transport config: CLI flag > env var > default."""
	transport = transport or os.getenv("MCP_TRANSPORT", "sse")
	host = host or os.getenv("MCP_HOST", "0.0.0.0")
	port = port if port is not None else int(os.getenv("MCP_PORT", "8080"))
	return transport, host, port


def run_mcp(mcp, transport, host, port):
	"""Run the MCP server with the resolved transport config."""
	if transport == "stdio":
		mcp.run(transport="stdio")
	else:
		mcp.settings.host = host
		mcp.settings.port = port
		# Disable DNS rebinding protection when binding to all interfaces,
		# since the server is intended to be accessed remotely.
		if host == "0.0.0.0":
			mcp.settings.transport_security.enable_dns_rebinding_protection = False
		mcp.run(transport=transport)

def init(
	login: Optional[Union[str, int]],
	password: Optional[str],
	server: Optional[str],
	path: Optional[str] = None,
):
	"""
	Initialize MT5Client (Windows native) or RemoteMT5Client (Mac/Linux).

	On Windows with MetaTrader5 installed: uses native MT5Client.
	On Mac/Linux: uses RemoteMT5Client if MT5_REMOTE_URL is set.
	If neither works, returns _NoopClient (tools return helpful errors).

	Args:
		login (Optional[Union[str, int]]): Login ID
		password (Optional[str]): Password
		server (Optional[str]): Server name (Windows) or Remote URL (Mac/Linux)
		path (Optional[str]): Path to MT5 terminal executable (Windows only)

	Returns:
		MT5Client / RemoteMT5Client / _NoopClient instance
	"""
	remote_url = os.getenv("MT5_REMOTE_URL", "")

	# Remote mode
	if remote_url:
		try:
			from metatrader_client.remote import RemoteMT5Client
			config = {
				"remote_url": remote_url,
			}
			if login:
				config["login"] = str(login)
			if password:
				config["password"] = password
			client = RemoteMT5Client(config=config)
			client.connect()
			print(f"[MCP] Connected to remote MT5 server at {remote_url}")
			return client
		except Exception as e:
			print(f"[MCP] Remote connection failed: {e}")
			return _NoopClient()

	# Native mode (Windows only)
	if _MT5_AVAILABLE and login and password and server:
		from metatrader_client import client as mt5_client
		config = {
			"login": int(login),
			"password": password,
			"server": server,
		}
		if path:
			config["path"] = path

		mt5c = mt5_client.MT5Client(config=config)
		mt5c.connect()
		print(f"[MCP] Connected to native MetaTrader5 server={server}")
		return mt5c

	# No connection available
	if not _MT5_AVAILABLE and not remote_url:
		print("[MCP] WARNING: MetaTrader5 not available on this platform.")
		print("[MCP] Set MT5_REMOTE_URL env var to connect to a Windows machine:")
		print("[MCP]   export MT5_REMOTE_URL=http://WINDOWS_IP:8080/api")
		print("[MCP] All tools will return 'not connected' errors until configured.")
	return _NoopClient()
	
def get_client(ctx: Any):
	return ctx.request_context.lifespan_context.client