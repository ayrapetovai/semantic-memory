# codeindex

## Decision stream

- Code embedding bug fixed in commit ee485f2: `index_paths` embeds with `start = symbols` per file and `idx = start + i` (the old `idx = symbols + i` double-counted the position); the detail and the self-healing of old databases are in the codeindex-bug topic
- The fix is covered by `tests/test_embedding_offsets` (`count(sym) == count(sym_vec)`) and `test_missing_vectors_are_rebuilt`; a plain `--reindex` (force) recomputes all vectors, a normal reindex heals only the files missing them
