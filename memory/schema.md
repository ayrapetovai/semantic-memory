# schema

## Decision stream

- There is no file embedding: the schema contains only `fact_vec` and `sym_vec`; `file.summary` is stored as text and participates only in `project_map`, not in FTS and not in vectors — there is no semantic search over files.
