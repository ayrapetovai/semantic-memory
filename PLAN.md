# PLAN.md — automatic memory for dialogue + code

## Goal
Make semantic-memory work as **automatic memory**: conversation events captured
without the model calling tools, retrieved facts injected into the model context,
and the code index kept fresh incrementally. `memory/<topic>.md` stays the source
of truth for facts; Python core stays the owner of embeddings and SQLite index.

## Architecture

```
OpenCode server
 ├─ MCP stdio:  python -m semantic_memory.mcp      (explicit model tools, unchanged)
 └─ plugin:  .opencode/plugins/semantic-memory/     (auto capture + injection)
        │  NDJSON stdin/stdout
        └─ python -m semantic_memory.bridge         (recall / add / reindex / stats)
```

- `memory/<topic>.md` — the source of facts (bullet = fact, `##` — heading as context).
- SQLite index in `INDEX_DIR` (per cwd root), code is already indexed incrementally
  (sha256); changes are only for fact freshness and concurrency.
- Plugin API (V2): `ctx.session.hook("prompt"|"context"|"compaction")`,
  events `session.execution.succeeded`, `ctx.session.generate`/transient generate,
  `ctx.storage`, plugin options from `opencode.json`.

## Stage 1 — Python core

1. `semantic_memory/db.py`
   - `connect()`: `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`
     (MCP stdio and the bridge subprocess share one database at the same time).
2. `semantic_memory/mcp_server.py`
   - `open_db()`: instead of "rebuild only when there are 0 facts" — always
     `facts.rebuild_facts(con)` (incremental by topic hash; embeddings only for
     changed topics). External changes to `memory/*.md` are visible immediately.
3. New `semantic_memory/bridge.py`
   - `python -m semantic_memory.bridge` — NDJSON over stdin/stdout:
     one request line `{"cmd": ..., "query": ..., "k": ..., "topic": ..., ...}` →
     one JSON answer line.
   - commands: `ping`, `recall` (search_facts), `add` (append_fact + rebuild
     changed), `reindex` (incremental index_root), `stats`.
   - reuses `open_db()` + `call_tool()`; errors → `{"error": ...}`,
     the process does not die on a bad request.
4. Tests (`tests/test_bridge.py`):
   - start the subprocess, `ping`, `add` → `recall` sees the fact immediately;
   - an external appended bullet in `memory/<topic>.md` → `recall`/`facts_search`
     sees it without restarting the process;
   - two parallel writers (MCP call + bridge) do not conflict (WAL).

## Stage 2 — plugin (writing facts)

`.opencode/plugins/semantic-memory/`:
- `package.json` (dep `@opencode/plugin`), `index.ts`.
- `setup(ctx)`:
  - spawn `python -m semantic_memory.bridge` (the project's venv python when `.venv`
    exists; cwd — the project root), cleanup on unload;
  - helper `bridge(cmd, args)` → Promise JSON (NDJSON interface);
  - debounce/retry-safety: one write per messageID.
- Capture:
  - **heuristic**: `prompt` hook → bullet from the user text (truncate ~300 chars)
    into topic `chat`; before writing, `recall` dedupe (score above the threshold → skip).
  - **LLM distillation** (option `distill: true`): event
    `session.execution.succeeded` → the last messages of the turn from
    `ctx.session.context({sessionID})`, prompt template "extract 2-4 facts
    (decisions/agreements/facts) with topic" → `ctx.session.generate` with a cheap
    model from options → validate topic `[A-Za-z0-9_-]+` → `add`.
  - **compaction hook**: transcript (`event.messages`) → LLM distillation
    "what has been decided by this point" → bullets into topic `state`
    (decisions stay alive after compaction).
- Cap: in `add` limit the topic (for example 200 bullets, the oldest are removed).

## Stage 3 — plugin (injecting recalled facts)

- `prompt` hook: `recall(query=user text, k=options.recallK)` → the block is cached
  per sessionID for the turn.
- `context` hook: `event.system.push({ type: "text", text: "<memory>...</memory>" })`
  — silent for the user, affects only the outgoing model call.
- Cache cleared when the turn finishes (`session.execution.*` events).
- Block format: `## <topic>` + bullets; cap by number of facts (`recallK: 5`).

## Stage 4 — options, index, docs

- `opencode.json`:
  ```jsonc
  {
    "plugins": [
      { "package": "./plugins/semantic-memory",
        "options": { "model": "<cheap model>", "distill": true, "inject": true, "recallK": 5 } }
    ]
  }
  ```
- reindex trigger after edit/write: `ctx.tool.hook("execute.after")`
  → `reindex` (option `autoReindex`, default true; cheap hash pass).
- README.md: the "Automatic memory" section — how it is enabled, what gets written,
  privacy (`memory/` in `.gitignore` if the user does not want to commit facts).
- MCP tools (`facts_add`, `facts_search`, `index_*`) unchanged —
  they stay for explicit model requests.

## Risks
- prompt hooks can run again on retry → idempotency by messageID/requestID.
- two Python processes on one database → WAL + busy_timeout (Stage 1 is mandatory).
- the distill model call adds tokens per turn; a cheap model or `distill:false`
  turns it off.
- `memory/` grows → topic cap + rotation of old bullets (Stage 3/4).
- plugin python interpreter: if `.venv` is missing the bridge dies — the plugin logs
  it and degrades to "injection through the model's MCP tools".

## Acceptance
- a new bullet from the dialogue appears in `memory/` and is found by `facts_search`
  without restarting the server;
- the `<memory>` block is added to the system context of turns and does not enter history;
- after compaction the decisions remain in topic `state`;
- `python -m semantic_memory.mcp` works as before (MCP round-trip tests).
