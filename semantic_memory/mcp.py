"""Точка входа MCP: python -m semantic_memory.mcp"""

from .mcp_server import serve

if __name__ == "__main__":
    serve()
