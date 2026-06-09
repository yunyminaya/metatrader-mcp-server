import click
import os
from dotenv import load_dotenv
from metatrader_mcp.server import mcp
from metatrader_mcp.utils import resolve_transport_config, run_mcp

@click.command()
@click.option("--login", required=False, type=int, default=None, help="MT5 login ID (Windows native; optional on Mac/Linux remote)")
@click.option("--password", required=False, default=None, help="MT5 password (Windows native; optional on Mac/Linux remote)")
@click.option("--server", required=False, default=None, help="MT5 server name (Windows native; optional on Mac/Linux remote)")
@click.option("--path", default=None, help="Path to MT5 terminal executable (optional, auto-detected if not provided)")
@click.option("--transport", default=None, type=click.Choice(["sse", "stdio", "streamable-http"], case_sensitive=False), help="MCP transport type (default: sse, env: MCP_TRANSPORT)")
@click.option("--host", default=None, help="Host to bind for SSE/HTTP transport (default: 0.0.0.0, env: MCP_HOST)")
@click.option("--port", default=None, type=int, help="Port to bind for SSE/HTTP transport (default: 8080, env: MCP_PORT)")
def main(login, password, server, path, transport, host, port):
    """Launch the MetaTrader MCP server.

    Mac/Linux: export MT5_REMOTE_URL=http://WINDOWS_IP:8080/api
    Windows: provide --login, --password, --server
    """
    load_dotenv()
    if login is not None:
        os.environ["login"] = str(login)
    if password is not None:
        os.environ["password"] = password
    if server is not None:
        os.environ["server"] = server
    if path:
        os.environ["MT5_PATH"] = path

    transport, host, port = resolve_transport_config(transport, host, port)
    run_mcp(mcp, transport, host, port)

if __name__ == "__main__":
    # pylint: disable=no-value-for-parameter
    main()
