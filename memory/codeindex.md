# codeindex

## Decision stream

- Code embedding bug: codeindex.py:155 `idx = symbols + i` double-counts the position, so the tail symbols of every file lose their vector (12 in semantic-memory, 5 in magpie); fix `idx = symbols`, add a test `count(sym)==count(sym_vec)`, then `--reindex` — separate commit
- Still open: bug in codeindex.index_paths — `idx = symbols + i` double-counts the index, 17 symbols without embeddings (12 in semantic-memory, 5 in magpie), some store the vector of a neighbouring symbol; fix `idx = symbols` + test `count(sym)==count(sym_vec)` + `init.py --reindex`
