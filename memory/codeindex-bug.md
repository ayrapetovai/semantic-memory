# codeindex-bug

## Decision stream

- Bug in `codeindex.index_paths` (`idx = symbols + i`): the position is double-counted and runs past the end of `vectors`, so the tail symbols of every file get no vector and the earlier ones get a neighbour's vector (12 symbols in semantic-memory, 5 in magpie)
- Fix: `start = symbols` before the loop over the symbols of a file, `idx = start + i`; `tests/test_embedding_offsets` checks that the number of vectors equals the number of symbols and that the last symbol of the last file has one
- A database indexed by the old offset heals itself: `index_root` re-embeds the files whose symbols have no vector (`sym LEFT JOIN sym_vec` for ids without a vector) and marks meta `vecfix=1`, so upgrading does not need a force reindex; a force reindex recomputes everything anyway
