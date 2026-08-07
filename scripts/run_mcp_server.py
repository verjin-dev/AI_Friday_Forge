#!/usr/bin/env python
"""CLI runner for the LogiPilot AI FastMCP Server."""

import argparse
import os
import sys
from pathlib import Path

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LogiPilot AI MCP Server")
    parser.add_argument(
        "--transport",
        default=os.getenv("MCP_TRANSPORT", "streamable-http"),
        choices=["streamable-http", "sse", "stdio"],
        help="Transport protocol (default: streamable-http)",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("MCP_HOST", "127.0.0.1"),
        help="Host address to bind (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MCP_PORT", "8020")),
        help="Port number to bind (default: 8020)",
    )
    parser.add_argument(
        "--path",
        default=os.getenv("MCP_PATH", "/mcp_server"),
        help="Endpoint path (default: /mcp_server)",
    )
    args = parser.parse_args()

    if args.transport == "stdio":
        import logging
        logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
        sys.stdout = sys.stderr

    from app.mcp.server import mcp

    if args.transport == "stdio":
        sys.stdout = sys.__stdout__
        mcp.run(transport="stdio")
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port, path=args.path)
