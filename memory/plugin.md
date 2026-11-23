# plugin

## Decision stream

- OpenCode V2: ctx.event.subscribe({types}) does not filter completely — session.execution.started and step events arrive too; drop the pending memory note only on failed/interrupted, otherwise the context hook never gets facts
- semantic-memory plugin: debug log (option `debug` or `SEMANTIC_MEMORY_DEBUG=1`), isEcho() drops the echo of the request's own text, bulletText() cuts the "Decision stream :: " prefix, distill goes through ctx.experimental.generate.text when `model` is set
- Automatic memory verified end-to-end: the `<memory>` block reaches the model (answer "Bridge token limit: ZZ-4211" without tools); commit 8bc3601 — bridge.py (NDJSON), the plugin, WAL+busy_timeout, MAX_TOPIC_BULLETS
- Plugin delivery: `init.py --plugin` copies `.opencode/plugins/semantic-memory` into the code root (cwd or `SEMANTIC_MEMORY_ROOT`), idempotent by sha256, foreign edits are skipped unless `--force`, then pings the bridge and prints `bridge: alive (<root>)`; auto-discovery still only works when OpenCode is opened inside semantic-memory itself
- A plugin outside the repository must name the interpreter: `options.python` or `SEMANTIC_MEMORY_PYTHON` pointing at the repository venv — the plugin derives the repository root from it (`<root>/.venv/bin/python` → `<root>`) and passes it to the bridge as `PYTHONPATH`, otherwise the bridge dies with ModuleNotFoundError and the hooks degrade (no injection, no capture)
- `init.py --print` (alone or after `--plugin`) prints the opencode.jsonc block for the code root: the MCP server plus plugins[] with the absolute plugin path and the python option
- tests/test_init_plugin.py checks the delivery: state before/after install, idempotent re-run, foreign edit skipped without --force, --plugin in the repository root copies nothing, snippet parses as JSON, bridge ping answers with the code root
- The files memory/codeindex.md and memory/commit.md are created by the plugin's distill hook during the session — they land in the same commit that adds them.
