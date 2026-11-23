# semantic-memory

Search memory for LLMs: project facts + code index, one hybrid engine,
MCP server for the harness. Version 0.2.0 — see the upgrade note under
"Automatic memory".

## What is here

- Project facts are read from `memory/*.md` in the project root (the single source
  of memory): one bullet = one decision, `##` = a section, the topic is the file name.
  New bullets are dated `- (YYYY-MM-DD) decision`; retired decisions go under
  `## Archive` (kept in git, not indexed or injected). Writes are deduplicated and
  secrets are redacted. The memory/index directories follow the process cwd;
  override them with `SEMANTIC_MEMORY_MEMORY_DIR` / `SEMANTIC_MEMORY_INDEX_DIR`.
- `semantic_memory/` — the package:
  - `facts.py` — facts by topic, the index is rebuilt only for changed topics;
  - `codeparse.py` — code parsing: Python through `ast`, other languages
    (js/ts/go/rust/c/cpp/cs/rb/php) — definition line + body extent by braces;
  - `codeindex.py` — walk of the root, symbols, calls, references, file summary;
    a file is rebuilt only when its sha256 changes;
  - `search.py` — hybrid: SQLite FTS5 over tokens (including camelCase splitting) +
    MiniLM vectors, merged by rank;
  - `mcp_server.py` — MCP server on stdio, 13 tools, no external dependencies;
  - `bridge.py` — NDJSON bridge for the plugin (`recall` / `add` / `reindex` / `lint` / `stats`);
  - `cli.py` — the same functionality from the command line;
  - `gc.py` — removes orphaned per-root index databases.
- `init.py` — venv, packages, model cache, index build, plugin delivery into the code root.
- `.opencode/plugins/semantic-memory/` — OpenCode V2 plugin: automatic capture of the
  dialogue and injection of recalled facts (see "Automatic memory").
- `tests/test_semantic_memory.py` — smoke tests for parsing, index, search and MCP.
- `tests/test_bridge.py` — smoke test for the NDJSON bridge.
- `tests/test_init_plugin.py` — smoke test for plugin delivery into a code root;
  the other `tests/test_*.py` cover retrieval quality, dedupe, fact lifecycle,
  path safety, call graph and index GC.
- `bench/` — offline retrieval gates (`test_quality.py`) and a reporting-only A/B
  session harness (`ab.py`).

## Why not just grep

The LLM reads code in fragments and loses the links: who calls a function, where its
definitions are, what a file does. Here, instead of fragments — the symbol in full
(`file:lines` + body), a call directory and a file map. Exact search over identifiers
and semantic search over descriptions work together: `code_search "hybrid ranking"`
finds `search_symbols` even if the word "ranking" never appears in the code.

The honest comparison:

| Tool | Persistent decisions | Call graph | One round trip | Large repos |
|---|---|---|---|---|
| `grep` / ripgrep | no | no | yes | excellent |
| LSP | no | yes | no (several requests) | good |
| Serena-like semantic tools | partial | partial | usually no | memory-heavy |
| semantic-memory | yes (`memory/*.md`) | yes (`callers`/`refs`) | yes (`index_stats` + recall) | brute-force vectors warn above 50k symbols |

On the offline fixture (`python -m pytest bench/test_quality.py`) the measured gates
are code recall@5 **1.00**, MRR **0.97**, facts precision@5 **0.94**, nonsense
queries **0** injected facts and warm recall p95 **~7 ms**. Those numbers describe a
17-symbol fixture, not a production monorepo: below 50k symbols vector search is a
brute-force NumPy scan, and non-Python parsing is brace-based and approximate.
Use grep/LSP for exact large-repo work; this tool is for small-to-medium projects
where decisions must survive between sessions.

## Install

```bash
pipx install .[embeddings]        # or: python -m pip install -e .[embeddings]
semantic-memory stats             # console script from pyproject.toml
python init.py --check            # plugin, bridge, index and memory state
```

The first embedding downloads the multilingual MiniLM model (~241 MB) into
`.models`. Extras: `embeddings` (fastembed), `vec` (sqlite-vec, optional) and
`dev` (pytest, ruff). Without fastembed, search still works on exact tokens.

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
.venv/bin/python -m semantic_memory.cli facts "cooling fan" --min-sim 0.5 --exclude-topic chat
.venv/bin/python -m semantic_memory.cli lint [--apply]
.venv/bin/python -m semantic_memory.cli gc [--days 90] [--dry-run]
.venv/bin/python -m semantic_memory.cli stats
```

`-k` and `--k` are both accepted, before or after the positional argument. The
`facts`, `lint` and `stats` commands refresh the fact index from `memory/*.md`
automatically; `gc` deletes only index databases whose project root no longer
exists (or that are older than `--days`), never the current root.

On Windows use `.venv\Scripts\python.exe` instead of `.venv/bin/python`, and
`$env:VAR = …` instead of `export VAR=…`. Tests:
`python -m unittest discover -s tests -p "test_*.py"` and
`python -m pytest bench/test_quality.py`.

## Facts and memory hygiene

```md
# flow

## Decision stream

- (2026-11-23) The code root is the process cwd.
- (2026-11-24, superseded 2026-11-25) Old decision, kept for history.

## Archive

- (2025-01-01) Retired decision: visible in git, not indexed or injected.
```

- One bullet is one decision. Undated bullets still work and are backfilled from
  the file mtime on the next rebuild.
- Vector-only fact hits below `MIN_VECTOR_SIM` (default 0.45, env
  `SEMANTIC_MEMORY_MIN_SIM`) are dropped before rank fusion. A vector-only hit
  with no shared query token must reach `MIN_VECTOR_STRONG_SIM` (0.70): nonsense
  queries must inject nothing.
- `memory_lint` / `cli lint` reports duplicates, near-duplicates, undated and
  stale facts (older than `STALE_DAYS`, default 180). `apply=true` removes only
  exact duplicates; near-duplicates are left for a human.
- Secrets (`api_key=…`, `sk-…`, `ghp_…`, `AKIA…`, `Bearer …`, long hex) are masked
  before a bullet is written to a git-tracked `memory/*.md`.

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
does not touch the MCP tools — it adds injection and optional capture:

- `prompt` hook — recalled facts for the turn (echo of the same text filtered).
  Raw prompt capture is **off** by default.
- `context` hook — the `<memory>` block is added to system on every turn of the agent
  loop (including continuation); the message history is not modified.
- `session.execution.succeeded` — end of a turn: LLM distillation into facts is
  **off** by default because it resends the session context without `options.model`.
- `compaction` hook — state extraction is likewise opt-in via `distill: true`.
- `tool.execute.after` — after `edit` / `write` / `apply_patch` calls reindex, so the
  code index stays fresh inside the turn.

**Upgrade note (0.2.0):** `captureChat` and `distill` now default to `false` (they
were `true`). Set both to `true` explicitly to restore the old behavior. `inject`
stays on, but recall is filtered with `excludeTopics: ["chat", "state"]` and a
similarity floor, so raw prompts are never injected by default.

The bridge is a separate process `python -m semantic_memory.bridge` on NDJSON (one
request line → one answer line with the same `id`): embeddings, FTS and the incremental
index stay in Python, the plugin only sends commands. It no longer hashes the code
tree on `recall`/`add`/`stats`; only `reindex` scans. The bridge opens the same database
as the MCP server; `journal_mode=WAL` and `busy_timeout` let them work in parallel. A
topic keeps at most `config.MAX_TOPIC_BULLETS` bullets; older ones move under
`## Archive` instead of being deleted. Requests during a bridge restart are queued
(bounded at 64); after three failed restarts memory stays disabled and logs once to
stderr. Python for the bridge: option `python` → `SEMANTIC_MEMORY_PYTHON` →
`.venv/bin/python` (`.venv\Scripts\python.exe` on Windows) → `python3`.

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
        "distill": false,
        "inject": true,
        "captureChat": false,
        "excludeTopics": ["chat", "state"],
        "minSim": 0.45,
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
extraction runs as a transit request `ctx.session.generate` on the session model — the
reason distill is opt-in. `captureChat: true` writes user turns (redacted) into
`memory/chat.md`; `distill: true` also extracts decisions. `debug: true` (or
`SEMANTIC_MEMORY_DEBUG=1`) writes recall/inject/distill/redaction counts to stderr —
together with `opencode run --print-logs` you see `[semantic-memory] inject → system: N`.
`SEMANTIC_MEMORY_TIMEOUT_MS` (default 10 000; reindex keeps at least 30 000) bounds a
request so a dead bridge never hangs a hook.

**Privacy:** with the defaults, the plugin writes nothing: recall is read-only and
`memory/*.md` is git-tracked by design. Enable capture/distill only for decisions you
want in the repository. Secrets are redacted both in the plugin (before the distill
prompt and chat bullet) and in Python (before the bullet is written). Disable the
whole layer by removing the plugin and the MCP server from the config.

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

Tools (13): `code_search`, `symbol`, `callers`, `callees`, `references`,
`file_outline`, `file_read`, `project_map`, `facts_search`, `facts_add`,
`memory_lint`, `index_rebuild`, `index_stats`. `facts_search` accepts
`excludeTopics` and `minSim` and reports `via` (`fts`, `vec`, `both`) so recall
quality is debuggable. `file_read` and `index_rebuild` reject paths that escape
the project root.

## Measurement and limits

Offline gates: `python -m pytest bench/test_quality.py`. The fixtures and expected
answers live in `bench/fixtures/` and `bench/queries.json`; the gate thresholds are
code recall@5 ≥ 0.85 / MRR ≥ 0.6, facts precision@5 ≥ 0.80, zero facts for nonsense
queries, chat excluded and warm recall p95 < 200 ms. `python bench/ab.py --model …`
runs the reporting-only A/B harness when the `opencode` CLI is installed; results go
to `bench/results/<date>.md`. CI runs unit tests and the offline gates on Linux and
Windows with Python 3.10/3.12, plus `ruff check` and the plugin helper tests.

Known limits:

- Parsing of non-Python files is approximate: the definition line and the extent by
  braces. Exact boundaries would need tree-sitter (not installed).
- Vectors need `fastembed` and the ONNX cache in `.models` (not in git); without them
  search works on text only.
- Embeddings are recomputed for changed files; deleted files are dropped from the
  index automatically.
- Files larger than `config.MAX_FILE_BYTES` and directories from `config.SKIP_DIRS`
  are not indexed.
- Vector search is a brute-force NumPy scan; above `ANN_WARN` (50 000) symbols
  `index_stats` prints a warning. The optional `vec` extra (`sqlite-vec`) is reserved
  for a future ANN backend; the fallback always works.
- The indexes are files in `.indexes` under the current working directory (not in
  git): sessions with different cwd work in parallel and do not interfere with each
  other. Run `cli gc` to remove databases whose project root no longer exists.
