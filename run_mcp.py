"""Run the MCP server from any directory: python /path/to/semantic-memory/run_mcp.py

`python -m semantic_memory.mcp` requires cwd inside semantic-memory; this script
adds the project folder to sys.path, so the harness can start it with the session
cwd — the server indexes exactly that directory.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from semantic_memory.mcp_server import serve

if __name__ == "__main__":
    serve()
