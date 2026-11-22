# flow

## Decision stream

- The semantic-memory MCP server runs under Windows: `.venv\Scripts\python.exe run_mcp.py`; in `~/.config/opencode/opencode.jsonc` the `mcp.servers` block with `type: local` and a single `command`; do not set `cwd`, do not set `pool`.
