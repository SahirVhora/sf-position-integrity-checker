#!/usr/bin/env bash
# Launcher for the SF Position Integrity Checker MCP server.
# Use in an MCP client config as:
#
# mcp_servers:
#   sf-position-integrity:
#     command: "/home/sahirvhora/projects/sapsf/sf-position-integrity-checker/run_mcp_server.sh"
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -x "$DIR/venv/bin/python" ]; then
  exec "$DIR/venv/bin/python" "$DIR/mcp_server.py" "$@"
fi
exec python3 "$DIR/mcp_server.py" "$@"
