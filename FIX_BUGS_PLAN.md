# FIX_BUGS_PLAN.md — fixing the usefulness findings

Plan derived from the review of 2026-11-23. It is ordered by risk-of-harm first,
then by value: stop the memory store from degrading itself, make recall honest,
fix correctness/safety bugs, then scale, robustness, and finally measurement/CI.

Each phase is independently shippable. Every phase ends with tests that fail
before the change and pass after it. No phase changes the public MCP tool names.

---

## Phase summary

| # | Phase | Priority | Effort | Blocks |
|---|-------|----------|--------|--------|
| 0 | Repository memory hygiene (data only) | P0 | 0.5 d | — |
| 1 | Retrieval: noise, threshold, dedupe | P0 | 1.5 d | §6 benchmark |
| 2 | Facts lifecycle: dates, archive, lint | P1 | 1 d | §6 |
| 3 | Code index correctness + path safety | P1 | 1 d | — |
| 4 | Performance and index footprint | P2 | 2 d | — |
| 5 | Plugin defaults, robustness, privacy | P0/P1 | 1.5 d | — |
| 6 | Measurement, CI, packaging | P1 | 2–3 d | — |

Phases 1, 2 and 5 change observable behavior; they need a README + CHANGELOG note
and a migration note in `init.py --check`.

---

## Phase 0 — Repository memory hygiene (data only)

The store is currently ~50% noise: 24/78 facts are raw prompts in `chat.md`,
~15 are facts from `magpie`/`Makefile`/`ui`/`test-ui`/`config` (another repo and
throwaway fixtures), and `plugin.md` repeats the same three decisions 6 times.

Tasks:

1. Delete topics that belong to other repositories/fixtures:
   `memory/magpie.md`, `memory/Makefile.md`, `memory/ui.md`, `memory/test-ui.md`,
   `memory/config.md`. Keep `memory/memory-hygiene.md` as the recorded rule.
2. Rewrite `memory/chat.md`: keep only decisions that are true for this project
   (plugin delivery, workflow limitations); drop all raw user prompts
   (`ok.`, `go`, `Say exactly: hello`, questions).
3. Deduplicate by hand (scripts arrive in Phase 2): `plugin.md` (3× transcript
   re-send, 3× distill cost), `codeindex.md` vs `codeindex-bug.md`,
   `magpie`/`Makefile` residue.
4. Remove volatile counts from `memory/semantic-memory.md` ("16 facts, 11 files,
   91 symbols, language C" is false). Replace with a pointer:
   "index state → `index_stats`; never store counts in facts".
5. Add the rule to `memory/memory.md`: one bullet = one decision; no prompts, no
   per-run numbers, no facts from other repositories.
6. Commit the cleaned memory separately from code changes so the diff is
   reviewable.

Acceptance:

- `memory/` contains no topic from another repository.
- `grep -c '^\- ' memory/chat.md` is 0 or only real decisions.
- No fact contains an index count.
- `facts_search "asdkjhasd"` (in-memory DB) returns nothing after Phase 1; with
  only Phase 0 it must at least not return `chat` noise.

---

## Phase 1 — Retrieval: stop injecting noise

Root causes:

- `search_facts` merges FTS + vector ranks with reciprocal-rank fusion
  (`semantic_memory/search.py:77-104`). A vector hit at rank 1 scores
  `1/61 = 0.0164`, FTS at rank 1 scores `0.0328`; nothing filters low-value
  vector-only hits.
- `bridge.recall` always returns `k` facts (`semantic_memory/bridge.py:40-45`).
- The plugin injects all of them under "Rely on them"
  (`.opencode/plugins/semantic-memory/index.ts:235-263`).
- `chat` stores every prompt verbatim (`index.ts:240-248`), so it wins vector
  recall for arbitrary queries.

### 1.1 Similarity floor and provenance in `search_facts`

`semantic_memory/config.py`:

```python
MIN_VECTOR_SIM = float(os.environ.get("SEMANTIC_MEMORY_MIN_SIM", "0.30"))
```

`semantic_memory/search.py`:

- `_vec()` returns `[(sim, id)]` (it already gets this from `embed.cosine`) and
  drops `sim < min_sim` before merging.
- `search_facts(con, query, k=5, topic=None, use_embed=True, exclude_topics=None,
  min_sim=None)` — excluded topics are removed from both the FTS `where` and the
  vector `where`.
- Results gain `"via": "fts" | "vec" | "both"` so callers can debug precision.
- Keep `merge()` unchanged; RRF scores are for ordering, not thresholds.

### 1.2 Bridge and MCP surface

- `bridge.handle("recall")` passes `excludeTopics` (list, optional) and
  `minSim` (float, optional).
- Add `excludeTopics` / `minSim` to `facts_search` input schema in
  `mcp_server.py` (default: no exclusion, `config.MIN_VECTOR_SIM`).
- CLI: `facts` gains `--min-sim` and `--exclude-topic`; `--k` alias added (see
  Phase 3.5).

### 1.3 Server-side dedupe for every write

`semantic_memory/facts.py`:

```python
def add_fact(con, text, topic, dedupe=True, force=False):
    """Append a fact; return {"topic", "added", "duplicate_of"}.

    Duplicate = same normalized text in the topic, or cosine >= DEDUPE_SIM
    against an existing bullet of the topic. force=True bypasses the check.
    """
```

- `config.DEDUPE_SIM = 0.92` (env override).
- `normalize(text)`: lowercase, collapse whitespace/punctuation, strip the
  section prefix (`"Decision stream :: "`).
- Dedupe must be vector-based when embeddings are available and normalized-text
  based otherwise.
- `append_fact` stays as the low-level writer; `add_fact` wraps it and is used by
  `mcp_server.facts_add`, `bridge.add` and `cli add`.
- `mcp_server.facts_add` response: `"Fact written…"` or
  `"Skipped: duplicates #<topic>:<num>"`.

### 1.4 Plugin: no prompt store, no unfiltered injection

`.opencode/plugins/semantic-memory/index.ts`:

- `captureChat` default **false** (was true). When enabled, write to `chat` but
  keep it excluded from injection.
- Add option `excludeTopics?: string[]`, default `["chat", "state"]`; pass to
  every `recall` call.
- `distillInto` relies on server-side dedupe (`add` without `force`); log
  `distill → skipped` when `added === false`.
- Keep the existing exact-match chat dedupe as a second line of defense.
- Update the `<memory>` header to "Facts as of <date>" (Phase 2 adds dates;
  until then omit the date).

Tests:

- `tests/test_search_quality.py` (new): fixture facts incl. a noisy `chat`
  topic; assert nonsense queries return `[]`, assert `exclude_topics=["chat"]`
  removes chat hits, assert `via` is populated.
- `tests/test_dedupe.py` (new): exact and near-duplicate `add_fact` are skipped;
  `force=True` adds; different topics are independent.
- Extend `tests/test_bridge.py`: `add` twice → second `added == false`; recall
  with `excludeTopics` and `minSim`.

Acceptance:

- Nonsense query returns no facts in CLI and bridge.
- Two identical distill writes produce one bullet.
- `memory/chat.md` does not grow during a normal agent session with default
  options.

---

## Phase 2 — Facts lifecycle: dates, archive, lint

Problems: stale facts are indistinguishable from current ones; duplicate
detection exists only for new writes; the 200-bullet cap silently drops the
oldest decisions (`facts._trim_bullets`).

### 2.1 Dated bullets (backward compatible)

Format:

```
- (2026-11-23) Decision text.
- (2026-11-23, superseded 2026-11-24) Old decision.
```

- `facts.parse_file` returns `(num, text, added, superseded)`; a missing prefix
  means `added = None`. Do **not** include the date in the embedded/FTS text
  (keep `text` clean).
- `fact` table gains `added TEXT, superseded TEXT`. Migrate with
  `PRAGMA table_info(fact)` + `ALTER TABLE` inside `db.connect` (idempotent).
- `search_facts` returns `added`; `facts_search` output appends `(2026-11-23)`.
- New bullets written by `append_fact` get `(YYYY-MM-DD)`.
- Existing bullets without a date get the topic file mtime on the first rebuild
  so old memory is not "unknown age".

### 2.2 Archive sections

- `parse_file` skips bullets under headings matching `archive`/`superseded`
  (case-insensitive). `rebuild_facts` therefore drops them from the index.
- Users keep stale decisions in the file (git history/readability) without
  feeding them to the model.
- README: "To retire a fact, move it under `## Archive`."

### 2.3 Memory lint / compact

New CLI + MCP tool `memory_lint` (keeps tool count at 12 → extend
`facts_search`? Decision: add a 13th tool `memory_lint`; update the
`len(TOOLS) == 12` assertion to 13 and README).

Behavior:

- Report duplicates (normalized equality or cosine ≥ `DEDUPE_SIM` within a
  topic), stale candidates (dated older than `config.STALE_DAYS`, default 180,
  in topics not containing "architecture"/"convention"), and bullets missing a
  date.
- `memory_lint(apply=true)` removes exact duplicates and rewrites near-duplicates
  to the newest bullet; it never invents merges and never touches archived
  bullets.
- Same functionality in `python -m semantic_memory.cli lint [--apply]`.

Tests (`tests/test_facts_lifecycle.py`, new):

- Old-format bullets still parse and search.
- Dated bullets expose `added`.
- `## Archive` bullets disappear from the index after rebuild.
- Lint finds a planted duplicate and removes it with `--apply`.
- `_trim_bullets` moves excess bullets under `## Archive` instead of deleting
  them.

Acceptance:

- Every newly written fact is dated.
- Archived facts are not injected and not returned by `facts_search`.
- Lint on the cleaned Phase 0 memory reports zero duplicates.

---

## Phase 3 — Code index correctness and path safety

### 3.1 Module-level calls are lost

`codeindex.index_paths` inserts a call row only when `qual == sym.qual`
(`semantic_memory/codeindex.py:162-165`); calls parsed with `qual is None`
(top level, `if __name__` blocks) are never stored, so `callers` misses them.

- Allow `call.sym_id` to be `NULL`; insert rows for `qual is None` with
  `file`/`line` and no owner.
- `search.callers` uses `LEFT JOIN sym`; owner is `NULL` → render `(module)`.
- `search.callees` unchanged for owned symbols; add the module pseudo-owner only
  when asked (`callees("(module)")` optional).

### 3.2 References to symbols added later

`index_paths` filters refs through `known_names` **at the time the file is
indexed** (`codeindex.py:118,167-171`). A ref in an unchanged file is never
refreshed when the definition appears.

Decision: store all identifier references (already keyword-filtered and
`len > 1`) and let `references(name)` filter. This makes refs correct at the cost
of DB size; `ix_ref_name` already exists. Keep the per-file `(name, line)` seen
set.

Tests (`tests/test_calls_refs.py`, new):

- Module-level `main()` call to `build_index` appears in `callers` as
  `(module)`.
- Index file A (ref to `later`), then file B (defines `later`) without touching
  A → `references("later")` finds A.
- Index size check stays reasonable on the fixture repo (< 2× baseline); record
  the numbers in the test message, do not assert an exact size.

### 3.3 Path containment

`mcp_server.file_read` joins user input to root (`mcp_server.py:196-205`) and
`index_rebuild` does the same for `path` (`:233-240`). `../` escapes the project.

- Add `config.safe_path(root, rel)` → absolute path inside root or `None`
  (`os.path.realpath` + `os.path.commonpath`, reject absolute inputs and
  symlinks resolving outside).
- Use it in `file_read`, `index_rebuild`, and the CLI `index`/`map` path args.
- Return a plain error text: `"path escapes the project root: …"`.

### 3.4 Dead code and duplicate purge

Remove the second `for rp in removed: purge_file(...)` loop
(`codeindex.py:199-200`).

### 3.5 CLI option ergonomics

`python -m semantic_memory.cli facts "q" --k 3` fails with "unrecognized
arguments". Add `parser.add_argument("--k", dest="k", type=int)` alongside
`-k` and test both orders. Also verify `--k` after the positional for `code`.

Tests:

- `tests/test_path_safety.py` (new): `../secret` rejected for `file_read` and
  `index_rebuild`; absolute path rejected; `src/../src/x.py` allowed if it
  resolves inside root; symlink escaping root rejected.
- Extend CLI test: `facts "q" --k 3` exits 0.

Acceptance:

- `callers` on a fixture with a `main()` call reports the module call.
- Traversal attempts return an error, never file contents.
- No symbol/ref behaviour regressions in existing tests.

---

## Phase 4 — Performance and index footprint

### 4.1 Stop re-indexing the tree on every recall

`bridge.handle` calls `open_db()` unconditionally
(`semantic_memory/bridge.py:31-33`), and `open_db` always runs
`codeindex.index_root` (`mcp_server.py:106-121`): every `recall`/`add`/`stats`
hashes the whole tree (0.33 s on 17 files, seconds on a real repo).

- `open_db(scan_code=True)`; bridge uses `scan_code=False` for `ping`, `recall`,
  `add`, `stats`; only `reindex` scans.
- MCP `serve()` keeps one connection and scans once per process; document that
  freshness comes from `index_rebuild` and the plugin's post-edit reindex.
- Add `config.SCAN_TTL = 2.0` and an in-process `(root, monotonic)` guard for
  callers that want a cheap freshness check.
- Keep the existing post-edit `reindex` hook in the plugin (it is what makes the
  no-scan path correct).

Test: extend `tests/test_bridge.py` — `recall` does not pick up a newly written
`.py` file until `reindex`, while `stats` also does not scan; `reindex` does.

### 4.2 Vector search scale

`_vec` loads every vector and does a full NumPy matmul
(`search.py:60-74`, `embed.py:56-69`). Fine at 120 symbols, unusable at 100k.

- Try `sqlite_vec` first (optional dependency `semantic-memory[vec]`): create a
  virtual table per `sym_vec`/`fact_vec`, query `MATCH … ORDER BY distance
  LIMIT ?`; rebuild the vec tables on schema creation.
- Fallback stays brute force (current behavior).
- Guard: when `COUNT(*) > config.ANN_WARN` (50 000), log a one-line stderr
  warning and add the hint to `index_stats` output.
- Do not block Phase 4.1 on this; the TTL/no-scan change is the bigger win.

### 4.3 Index directory footprint

This repo has 12 stale DBs (8.5 MB) from previous roots; nothing cleans them.

- `python -m semantic_memory.cli gc [--days N] [--dry-run]`: open each
  `.indexes/*.db`, read `meta.root`; delete the DB (+ `-wal`/`-shm`) when the
  root no longer exists or `--days` exceeded. Never touch the current root.
- `init.py --check` prints `indexes: N files, X MB (gc available)`.
- Keep the default index location (cwd) for compatibility; the XDG-cache move is
  deferred until Phase 6 has data (recorded in README "Known limits").
- `init.py --plugin` prints a note when the target project does not ignore
  `.indexes/`; it does not edit the file.

Tests: `tests/test_index_gc.py` (new) — creates two root DBs, deletes one root
directory, `gc --dry-run` reports 1, `gc` removes it, current root survives.

Acceptance:

- `recall` latency on the fixture repo < 50 ms (excluding first model load).
- `index_stats` warns above the threshold.
- `gc` deletes only orphaned DBs.

---

## Phase 5 — Plugin: defaults, robustness, privacy

### 5.1 Safer defaults

`.opencode/plugins/semantic-memory/index.ts`:

| Option | Old | New | Rationale |
|---|---|---|---|
| `captureChat` | true | **false** | raw prompts are noise and a privacy leak |
| `distill` | true | **false** | extra model call per turn; opt-in |
| `inject` | true | true (with floor) | value only when recall is filtered |
| `excludeTopics` | — | `["chat","state"]` | never inject prompts/compaction notes |
| `minSim` | — | `0.30` | passed to `recall` |

Document the change prominently in README ("Upgrade note: capture/distill are
opt-in since 0.2").

### 5.2 Bridge lifecycle

- On `proc.on("exit")`, mark dead, reject waiters, and attempt **one** restart
  with exponential backoff (3 tries: 1 s, 5 s, 15 s) before giving up; log once
  via `console.error`.
- Queue requests while restarting; cap the queue; resolve failures as
  `{ok:false, error}` so hooks degrade instead of hanging.
- Reduce the per-request timeout to `config`-able 10 s (recall/index should never
  take 30 s; keep 30 s for `reindex` if needed).
- Surface one user-visible message (not just stderr) when memory is disabled:
  a single assistant-visible note in the first turn, or document that
  `debug:true` is required. Choose stderr-only to avoid polluting history;
  document it in README.

### 5.3 Bounded state and privacy

- `admitted` Set → `Map` capped at 512 entries (delete oldest).
- `pending` cleared on failed/interrupted/succeeded (succeeded already does it).
- Add `redact(text)` before any write and before the distill prompt: mask
  `sk-…`, `ghp_…`, `AKIA…`, `Bearer …`, `password=…`, `api_key=…`, and long
  base64/hex runs (> 40 chars). Replace with `***`.
- Log `redacted: N` in debug mode.
- README: explicit privacy section — what is written where, how to disable
  (`captureChat:false`, `distill:false`, `inject:false`), and that `memory/` is
  tracked by default.

Tests:

- Python-side redaction tests live with facts tests (the plugin duplicates the
  regex; keep both implementations small and documented, or move redaction into
  the bridge so there is one implementation — **decision: bridge redacts on
  `add`, plugin redacts only the distill prompt**).
- `tests/test_redaction.py` (new): secrets are masked in written bullets.
- Plugin helper tests: extract `norm`, `isEcho`, `parseFacts`, `formatMemory`
  into `.opencode/plugins/semantic-memory/lib.ts`; add
  `tests/plugin/lib.test.mjs` using `node:test` (CI only; locally skipped when
  `node` is absent).

Acceptance:

- Killing the bridge mid-session does not hang the agent; memory resumes after
  one restart.
- A bullet containing `api_key=…` is written masked.
- Default session does not append anything to `memory/chat.md`.

---

## Phase 6 — Measurement, CI, packaging

The repo's own notes (`memory/measurement.md`, `PLAN.md`) describe precision@k
and an A/B harness; none exists. Without this, "usefulness" stays a claim.

### 6.1 Offline quality benchmark (gating)

`bench/` (new):

```
bench/fixtures/{facts_topics/*.md, code_root/**}
bench/queries.json      # {"code": [{"q","expect":["file.py:qual"],"k"}],
                         #  "facts": [{"q","expect":["topic:num"],"k"}]}
bench/score.py          # recall@k, precision@k, MRR, noise rate
bench/test_quality.py   # pytest wrapper, runs against a temp index
```

Gates (initial, tune once and freeze):

- code `recall@5 ≥ 0.85`, `MRR ≥ 0.6`
- facts `precision@5 ≥ 0.80`
- nonsense queries: 0 injected facts
- chat excluded: prompt fixtures never appear in top-5
- latency: warm `recall` p95 < 200 ms on the fixture

Add ~20 code queries and ~20 fact queries, taken from real sessions but with
expected answers written by hand.

### 6.2 A/B session harness (reporting only)

`bench/ab.py` per `memory/measurement.md`:

- Condition A: plugin ON; B: plugin OFF / MCP disabled (the `disabled` flag does
  not work in 2.1.3 — use a `false` command override, already recorded in
  `memory/bench.md`).
- Scenarios: 3 deterministic tasks (find callers, apply a convention, recall a
  decision), 3 runs each.
- Score from `opencode session export` tokens/cost + deterministic checks; emit
  a Markdown table to `bench/results/<date>.md`.
- Not a CI gate; run before a release.

### 6.3 CI

`.github/workflows/tests.yml`:

- matrix: ubuntu-latest + windows-latest, Python 3.10/3.12.
- `pip install -r requirements.txt -e .` (after packaging), then
  `python -m unittest discover -s tests -p 'test_*.py'` and
  `pytest bench/test_quality.py`.
- Optional job for plugin helper tests with Node.
- Cache `.models` by model name; download once.
- `ruff check` + `ruff format --check` (add to requirements-dev).

### 6.4 Packaging

`pyproject.toml`:

- metadata, `requires-python = ">=3.10"`, `dependencies = ["numpy>=1.26,<3"]`,
  extra `embeddings = ["fastembed>=0.7,<1"]`, extra `vec = ["sqlite-vec"]`,
  extra `dev = ["ruff", "pytest"]`.
- console script `semantic-memory = semantic_memory.cli:main`.
- Version `0.2.0` for the breaking defaults/format changes; CHANGELOG with the
  Phase 0–5 notes.
- README: install `pipx install .[embeddings]`, first-run model download
  (241 MB), and the "Known limits" section (no ANN by default, approximate
  non-Python parsing).

### 6.5 Documentation refresh

- README "Why not just grep" → replace the assertion with the benchmark numbers
  and an honest comparison (grep/LSP/Serena): where this wins (persistent
  decisions + call graph + one round trip) and where it does not (large repos,
  non-Python precision).
- Update the tool list (13 tools if `memory_lint` lands), the defaults table, the
  dated-bullet format, and the archive convention.
- `init.py --check` prints: plugin state, bridge state, index count/size, memory
  lint summary, and whether `captureChat`/`distill` are default-off.

Acceptance:

- CI green on Linux and Windows.
- Benchmark gates fail on a seeded regression (verify by temporarily disabling
  the similarity floor).
- `pip install .` then `semantic-memory stats` works outside the repo.

---

## Cross-cutting test matrix

| Area | New/extended test | Phase |
|---|---|---|
| Vector floor, excluded topics, `via` | `tests/test_search_quality.py` | 1 |
| Dedupe on add | `tests/test_dedupe.py` | 1 |
| Bridge dedupe/recall options | `tests/test_bridge.py` | 1 |
| Dates, archive, lint | `tests/test_facts_lifecycle.py` | 2 |
| Module calls, late refs | `tests/test_calls_refs.py` | 3 |
| Path containment | `tests/test_path_safety.py` | 3 |
| CLI option order | `tests/test_semantic_memory.py` + CLI test | 3 |
| No scan on recall | `tests/test_bridge.py` | 4 |
| Index GC | `tests/test_index_gc.py` | 4 |
| Redaction | `tests/test_redaction.py` | 5 |
| Plugin helpers | `tests/plugin/lib.test.mjs` | 5 |
| Retrieval quality gates | `bench/test_quality.py` | 6 |

Rules for all tests: no network, no writing outside `tempfile.mkdtemp()`, follow
the existing `SEMANTIC_MEMORY_DB` / `SEMANTIC_MEMORY_MEMORY_DIR` pattern from
`tests/test_semantic_memory.py:11-13`.

---

## Migration and compatibility

- **Facts**: old bullets (no date) keep working; `added` is backfilled from file
  mtime. No file rewrite required. Archive sections are opt-in.
- **Schema**: additive `ALTER TABLE fact ADD COLUMN added/superseded`; detect via
  `PRAGMA table_info(fact)`. Old DBs keep working; `index_rebuild --force`
  re-embeds.
- **MCP**: all 12 existing tools keep their names; `facts_search` only gains
  optional args; `memory_lint` is additive.
- **Plugin**: breaking defaults — release as 0.2.0 with an upgrade note. Users
  who want the old behavior set `captureChat:true, distill:true`.
- **CLI**: additive flags; `-k`/`--k` both accepted.
- **Index location**: unchanged (cwd) in this plan; XDG move deferred.

---

## Risk register

| Risk | Mitigation |
|---|---|
| Similarity floor hides useful semantic hits | tune `MIN_VECTOR_SIM` on the Phase 6 fixture; make it configurable per call |
| Dated bullets change user-visible format | parser accepts both; tests cover old files |
| Storing all refs grows the DB | measure on the fixture and a mid-size repo; if > 2×, keep the old filter and instead refresh refs of files when new symbol names appear (documented fallback) |
| sqlite-vec availability differs per platform | optional extra with brute-force fallback, never a hard dependency |
| Plugin restart loop hammers the bridge | max 3 attempts with backoff, then stay degraded and log once |
| Redaction false positives | mask only high-confidence patterns; log count in debug |
| Moving to opt-in distill loses existing behavior | README upgrade note + CHANGELOG; A/B measures whether it was ever valuable |

---

## Definition of done

1. Nonsense queries never inject facts; `chat`/`state` never injected by default.
2. Repeated distill writes do not create duplicate bullets.
3. Every new fact is dated; archived facts are invisible to search.
4. `callers` includes module-level calls; `references` finds late-defined names.
5. `file_read` and `index_rebuild` cannot escape the project root.
6. `recall` does not hash the tree; index GC exists.
7. Defaults: no prompt capture, no distill; redaction on write.
8. Benchmark gates exist and are enforced in CI on Linux and Windows.
9. Memory in this repository is clean: no foreign topics, no prompts, no
   duplicates, no volatile counts.
10. README states what the tool demonstrably does better than grep/LSP, with
    numbers.

---

## Implementation status (2026-11-23)

Phases 0–5 and the Phase 6 deliverables are implemented. The 36 unit tests, the
5 offline quality gates and `ruff check` pass locally. Measured gate values on the
fixture: code recall@5 1.00 / MRR 0.97, facts precision@5 0.94, nonsense 0 facts,
warm recall p95 ≈ 8 ms.

Two deliberate deviations from the plan:

- `MIN_VECTOR_SIM` is 0.45 (not 0.30): measured gibberish-to-short-fact cosine was
  0.39–0.53, so a lexical-evidence guard (`MIN_VECTOR_STRONG_SIM = 0.70`) was
  added for vector-only hits without a shared query token. The plugin default
  `minSim` follows 0.45.
- sqlite-vec ANN is **not** wired in. Brute-force NumPy search remains the only
  backend; `index_stats` warns above `ANN_WARN` (50 000 symbols) and the `vec`
  extra is declared for a future backend. The `vec` column is created by Python
  regardless of backend availability.

Not run locally: the Node plugin helper tests (no Node runtime in this
environment) and the A/B harness (no `opencode` CLI). Both are wired into CI.
