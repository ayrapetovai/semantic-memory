# semantic-memory

Search memory for LLMs: project facts + code index, one hybrid engine,
MCP server for the harness.

## What is here

- Project facts are read from `memory/*.md` in the project root (the single source
  of memory): one bullet = one fact, `##` = a section, the topic is the file name.
  The memory directory and the index directory follow the process cwd; they are
  overridden with `SEMANTIC_MEMORY_MEMORY_DIR` and `SEMANTIC_MEMORY_INDEX_DIR`.
- `semantic_memory/` — the package:
  - `facts.py` — facts by topic, the index is rebuilt only for changed topics;
  - `codeparse.py` — code parsing: Python through `ast`, other languages
    (js/ts/go/rust/c/cpp/cs/rb/php) — definition line + body extent by braces;
  - `codeindex.py` — walk of the root, symbols, calls, references, file summary;
    a file is rebuilt only when its sha256 changes;
  - `search.py` — hybrid: SQLite FTS5 over tokens (including camelCase splitting) +
    MiniLM vectors, merged by rank;
  - `mcp_server.py` — MCP server on stdio, 12 tools, no external dependencies;
  - `bridge.py` — NDJSON bridge for the harness plugin (`recall` / `add` / `reindex` / `stats`);
  - `cli.py` — the same functionality from the command line.
- `init.py` — venv, packages, model cache, index build, plugin delivery into the code root.
- `.opencode/plugins/semantic-memory/` — OpenCode V2 plugin: automatic capture of the
  dialogue and injection of recalled facts (see "Automatic memory").
- `tests/test_semantic_memory.py` — smoke tests for parsing, index, search and MCP.
- `tests/test_bridge.py` — smoke test for the NDJSON bridge.
- `tests/test_init_plugin.py` — smoke test for plugin delivery into a code root.

## Why not just grep

The LLM reads code in fragments and loses the links: who calls a function, where its
definitions are, what a file does. Here, instead of fragments — the symbol in full
(`file:lines` + body), a call directory and a file map. Exact search over identifiers
and semantic search over descriptions work together: `code_search "hybrid ranking"`
finds `search_symbols` even if the word "ranking" never appears in the code.

## Running

It builds the same way on Windows and Linux; only the path to python in the venv
differs (`.venv\Scripts\python.exe` and `.venv/bin/python`).

Windows (PowerShell):

```powershell
python init.py                 # venv + packages + index (cwd)
python init.py --check         # state without any changes
python init.py --plugin        # copy the plugin into the code root, ping the bridge
python init.py --print         # the opencode.jsonc block for the code root
.venv\Scripts\python.exe -X utf8 -m semantic_memory.cli code "hybrid search" -k 6
```

Linux (bash):

```bash
python3 init.py                # venv + packages + index (cwd)
python3 init.py --check
python3 init.py --plugin       # copy the plugin into the code root, ping the bridge
python3 init.py --print        # the opencode.jsonc block for the code root
.venv/bin/python -m semantic_memory.cli code "hybrid search" -k 6
```

CLI (the same commands on both systems, from the semantic-memory folder; the code
root is the cwd, overridden with `--root`):

```bash
.venv/bin/python -m semantic_memory.cli index
.venv/bin/python -m semantic_memory.cli symbol Store.load
.venv/bin/python -m semantic_memory.cli callers build_index
.venv/bin/python -m semantic_memory.cli facts "cooling fan" --topic hardware
.venv/bin/python -m semantic_memory.cli add "PSU: 550 W, 80+" --topic hardware
.venv/bin/python -m semantic_memory.cli stats
```

On Windows use `.venv\Scripts\python.exe` instead of `.venv/bin/python`, and
`$env:VAR = …` instead of `export VAR=…`. Tests: `python tests/test_semantic_memory.py`
(on Windows `python tests/test_semantic_memory.py`).

## Cross-platform

- Paths in the index are stored relative and with `/` (`codeindex.rel`), queries like
  `semantic_memory/search.py` work on both systems; `~` is expanded in
  `--root` and `SEMANTIC_MEMORY_ROOT`.
- SQLite, `ast`, regex parsing and `fastembed` (ONNX) exist on Windows and Linux; the
  model cache lives in `.models` and is downloaded on the first run if there is no donor.
- Output is UTF-8: on Windows the CLI reconfigures stdout, on Linux the default encoding.
- `.gitattributes` pins LF for text files so a checkout on Windows does not rewrite
  line endings.
- Verified on Windows and Linux (tests + MCP over stdio).

## MCP for the harness

Server: `python /path/to/semantic-memory/run_mcp.py` — can be started from any
directory; or `python -m semantic_memory.mcp` from the semantic-memory folder (the same
`mcp_server.serve()` as in `semantic_memory.cli serve`). The code root is the working
directory of the process (the harness session cwd). The first tool call builds the
index for the current root into a separate file `.indexes/<hash of root>.db`: each
project keeps its own index, switching directories does not touch other ones, and
unchanged files are skipped by sha256. `SEMANTIC_MEMORY_DB` sets one shared file for
all roots (then a new root clears the index of the old one). Facts are read from
`memory/*.md` in the root of each project — the source is single.

### OpenCode

V2 reads servers under `mcp.servers` (not `mcpServers`), a local server is
`"type": "local"` with `command` as one array. In the global config
`~/.config/opencode/opencode.jsonc`:

```jsonc
{
  "mcp": {
    "servers": {
      "semantic-memory": {
        "type": "local",
        "command": ["/path/to/semantic-memory/.venv/bin/python",
                    "/path/to/semantic-memory/run_mcp.py"]
      }
    }
  }
}
```

On Windows the same block, only the paths: `.venv\\Scripts\\python.exe` and `run_mcp.py`.
The same block is written by the CLI:

```bash
opencode mcp add semantic-memory --global -- \
  /path/to/semantic-memory/.venv/bin/python \
  /path/to/semantic-memory/run_mcp.py
```

Do not set `cwd`: OpenCode starts the server in the working directory of the opened
project — that is exactly the directory being indexed. Do not set `pool: true` either:
it makes one connection for all directories and breaks the index by cwd.

Check: `/mcps` in the TUI or `opencode mcp list` — `✓ semantic-memory connected`.
The tools are visible in Code Mode as `tools.semantic-memory.*`.

### Automatic memory (OpenCode plugin)

OpenCode loads `.opencode/plugins/semantic-memory/` itself (plugins under
`.opencode/plugins/` are picked up automatically, no config edit needed). The plugin
does not touch the MCP tools — it adds capture and injection without tool calls:

- `prompt` hook — recalled facts by the text of the turn, plus a bullet in the `chat`
  topic; the echo of the request's own text from recall is filtered out, duplicate
  bullets are checked through recall.
- `context` hook — the `<memory>` block is added to system on every turn of the agent
  loop (including continuation); the message history is not modified.
- `session.execution.succeeded` — end of a turn: the model extracts 0-3 facts from the
  tail of the dialogue and writes them into the `chat` topic (or the topic it names).
- `compaction` hook — before the transcript is folded, writes the state into the `state` topic.
- `tool.execute.after` — after `edit` / `write` / `apply_patch` calls reindex, so the
  code index stays fresh inside the turn.

The bridge is a separate process `python -m semantic_memory.bridge` on NDJSON (one
request line → one answer line with the same `id`): embeddings, FTS and the incremental
index stay in Python, the plugin only sends commands. The bridge opens the same database
as the MCP server; `journal_mode=WAL` and `busy_timeout` in `db.connect` let the plugin
and MCP work in parallel. A topic keeps at most `config.MAX_TOPIC_BULLETS` bullets
(older ones are removed). Python for the bridge: option `python` →
`SEMANTIC_MEMORY_PYTHON` → `.venv/bin/python` (`.venv\Scripts\python.exe` on Windows) →
`python3`.

Options are only applied when the plugin is attached explicitly (auto-discovery uses
the defaults):

```jsonc
{
  "plugins": [
    {
      "package": "./.opencode/plugins/semantic-memory",
      "options": {
        "python": "/path/to/semantic-memory/.venv/bin/python",
        "model": { "providerID": "localapi", "id": "gyro-gyro-s" },
        "distill": true,
        "inject": true,
        "captureChat": true,
        "debug": false,
        "recallK": 5,
        "chatTopic": "chat",
        "stateTopic": "state",
        "reindexTools": ["edit", "write", "apply_patch"]
      }
    }
  ]
}
```

`model` is the model for fact extraction (`ctx.experimental.generate.text`); without it
extraction runs as a transit request `ctx.session.generate` on the session model.
`distill: false` keeps only capture of the turns and injection. `debug: true` (or
`SEMANTIC_MEMORY_DEBUG=1`) writes recall/inject/distill to stderr — together with
`opencode run --print-logs` you see `[semantic-memory] inject → system: N`.
Check: `memory/chat.md` grows with the turns, `memory/state.md` with the foldings.

### Installing into another project

OpenCode auto-discovers `.opencode/plugins/` only in the directory it opens, so in someone
else's project the plugin must be copied there and told which python has semantic-memory.
Run this from the semantic-memory folder with the project as the code root (cwd, or
`SEMANTIC_MEMORY_ROOT=/path/to/project`):

```bash
python init.py --plugin        # copy the plugin into the code root, then ping the bridge
python init.py --check --print # state of the code root + the ready opencode.jsonc block
```

`--plugin` copies `.opencode/plugins/semantic-memory` into
`<code root>/.opencode/plugins/semantic-memory` and is idempotent by sha256: files identical
to the repository are not copied, files edited by the user are skipped unless `--force`.
Then it starts `python -m semantic_memory.bridge` in the code root once and prints
`bridge: alive (<root>)` — the same command OpenCode will run. With `--print` added, the
config block is printed after the ping.

`--print` prints the block to merge into the project's `opencode.jsonc` (or the global config):

```jsonc
{
  "mcp": {
    "servers": {
      "semantic-memory": {
        "type": "local",
        "command": ["/path/to/semantic-memory/.venv/bin/python",
                    "/path/to/semantic-memory/run_mcp.py"]
      }
    }
  },
  "plugins": [
    {
      "package": "/path/to/project/.opencode/plugins/semantic-memory",
      "options": {"python": "/path/to/semantic-memory/.venv/bin/python"}
    }
  ]
}
```

The `python` option is the venv of this repository. The plugin derives the repository path
from it (`<root>/.venv/bin/python` → `<root>`) and gives it to the bridge as `PYTHONPATH`,
so the package is found even when the project lives elsewhere; on Windows the same is
`.venv\Scripts\python.exe`. Without `python` the plugin falls back to the project's own
`.venv`, and the bridge dies with `ModuleNotFoundError`.

### Other harnesses

`mcpServers` format, stdio. Do not set `cwd` — the harness starts the server in the
session's working directory, and that is the directory we index:

```json
{
  "mcpServers": {
    "semantic-memory": {
      "command": "C:\\Users\\user\\semantic-memory\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\user\\semantic-memory\\run_mcp.py"]
    }
  }
}
```

Linux — the same block, only the paths:

```json
{
  "mcpServers": {
    "semantic-memory": {
      "command": "/path/to/workspace/semantic-memory/.venv/bin/python",
      "args": ["/path/to/workspace/semantic-memory/run_mcp.py"]
    }
  }
}
```

Check without a harness — requests over stdin:

```powershell
Get-Content tests/mcp_requests.jsonl | .venv\Scripts\python.exe -m semantic_memory.mcp
```

```bash
cat tests/mcp_requests.jsonl | .venv/bin/python -m semantic_memory.mcp
```

Tools: `code_search`, `symbol`, `callers`, `callees`, `references`,
`file_outline`, `file_read`, `project_map`, `facts_search`, `facts_add`,
`index_rebuild`, `index_stats`.

## Limits

- Parsing of non-Python files is approximate: the definition line and the extent by
  braces. Exact boundaries would need tree-sitter (not installed).
- Vectors need `fastembed` and the ONNX cache in `.models` (not in git); without them
  search works on text only.
- Embeddings are recomputed for changed files; deleted files are dropped from the
  index automatically.
- Files larger than `config.MAX_FILE_BYTES` and directories from `config.SKIP_DIRS`
  are not indexed.
- The indexes are files in `.indexes` under the current working directory (not in
  git): sessions with different cwd work in parallel and do not interfere with each
  other.
