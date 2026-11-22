# plugin

## Decision stream

- OpenCode V2: ctx.event.subscribe({types}) does not filter completely — session.execution.started and step events arrive too; drop the pending memory note only on failed/interrupted, otherwise the context hook never gets facts
- semantic-memory plugin: debug log (option `debug` or `SEMANTIC_MEMORY_DEBUG=1`), isEcho() drops the echo of the request's own text, bulletText() cuts the "Decision stream :: " prefix, distill goes through ctx.experimental.generate.text when `model` is set
- Automatic memory verified end-to-end: the `<memory>` block reaches the model (answer "Bridge token limit: ZZ-4211" without tools); commit 8bc3601 — bridge.py (NDJSON), the plugin, WAL+busy_timeout, MAX_TOPIC_BULLETS
- Plugin delivery: it is committed in the repository and auto-discovered only when OpenCode is opened inside semantic-memory itself; another project needs the path to the plugin directory in plugins[] of opencode.jsonc (absolute, or relative to the config), copying the file is not required
- A plugin outside the repository must name the interpreter: `options.python` or `SEMANTIC_MEMORY_PYTHON` pointing at the repository venv, otherwise the bridge dies with ModuleNotFoundError and the hooks degrade (no injection, no capture)
- The plugin files .opencode/plugins/semantic-memory/index.ts and package.json exist only in the repository clone: init.py does not copy them, OpenCode creates nothing — automatic memory does not work in another project
- Plan for delivering the plugin to another project: `init.py --plugin` copies PLUGIN_SRC→code_root()/.opencode/plugins/semantic-memory (idempotent by sha256, do not overwrite foreign edits without --force) and checks the bridge with a ping; `--plugin --print` prints an opencode.jsonc snippet with the absolute path and the python option
- The files memory/codeindex.md and memory/commit.md are created by the plugin's distill hook during the session — they land in the same commit that adds them.
