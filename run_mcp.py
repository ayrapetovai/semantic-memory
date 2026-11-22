"""Запуск MCP-сервера из любой директории: python /path/to/semantic-memory/run_mcp.py

`python -m semantic_memory.mcp` требует cwd внутри semantic-memory; этот скрипт
добавляет папку проекта в sys.path, поэтому harness может запустить его с cwd
сессии — сервер индексирует именно её.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from semantic_memory.mcp_server import serve

if __name__ == "__main__":
    serve()
