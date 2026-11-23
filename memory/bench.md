# bench

## Decision stream

- A/B harness plan for semantic-memory effectiveness: throwaway fixtures under /tmp/opencode; condition A loads plugin via absolute package path with python option pointing at repo .venv; condition B disables it with '-semantic-memory' and disables semantic-memory MCP in both conditions; fresh fixture per trial; multi-turn via opencode run --session; scoring parses --format json output for expected
