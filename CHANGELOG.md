# Changelog

## 0.2.0 — 2026-11-23

Breaking defaults and facts format; see the upgrade note in the README.

- **Retrieval**: vector-only fact hits below `MIN_VECTOR_SIM` (0.45) are dropped
  before RRF fusion; `facts_search` accepts `excludeTopics`/`minSim` and reports
  `via` (`fts`, `vec`, `both`). Nonsense queries no longer inject random facts.
- **Writes**: every write goes through `facts.add_fact()` with normalized-text
  and vector dedupe plus secret redaction. `force=True` bypasses the check.
- **Facts lifecycle**: new bullets are dated `(YYYY-MM-DD)`; `## Archive` and
  `superseded` bullets remain in git but are not indexed or injected. Added
  `memory_lint` (MCP tool 13) and `cli lint [--apply]` / `cli gc`.
- **Code index**: module-level calls are indexed with `sym_id = NULL` and shown
  as `(module)`; all identifier refs are stored, so late definitions are found.
  Duplicate purge removed. `file_read`/`index_rebuild` now use `config.safe_path()`.
- **Performance**: the bridge no longer hashes the tree on `recall`/`add`/`stats`;
  only `reindex` scans. `index_stats` warns above `config.ANN_WARN` symbols.
  Index GC removes orphaned `.indexes/*.db` files.
- **Plugin**: `captureChat` and `distill` default to `false`; `inject` stays on
  with `excludeTopics: ["chat", "state"]` and a similarity floor. The bridge
  restarts with bounded backoff, `admitted` is capped at 512, and the distill
  prompt is redacted.
- **CLI**: `--k`/`-k`, `--min-sim`, `--exclude-topic`, `lint`, `gc`; fact commands
  refresh the index from `memory/*.md` automatically.
- **Measurement**: offline retrieval gates in `bench/test_quality.py` and a
  reporting-only A/B harness in `bench/ab.py`; CI on Linux/Windows, Python 3.10/3.12.
- **Packaging**: `pyproject.toml`, installable with `pipx install .[embeddings]`.

Upgrade note: users who want the old automatic capture/distill behavior must set
`captureChat: true, distill: true` explicitly. Old undated bullets keep working
and are backfilled from the file mtime on the next index rebuild.
