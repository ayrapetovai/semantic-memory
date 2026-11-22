# codeindex-bug

## Decision stream

- Bug in `codeindex.index_paths` (`idx = symbols + i`): the position is double-counted and runs past the end of `vectors`, so the tail symbols of every file lose their vector (12 symbols in semantic-memory, 5 in magpie); part of them store the vector of the neighbouring symbol.
