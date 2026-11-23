# paths

## Decision stream

- Memory dir and index dir default to the process cwd (project root), not the package folder: the plugin used from another project writes memory/*.md and .indexes there. Overrides: SEMANTIC_MEMORY_MEMORY_DIR, SEMANTIC_MEMORY_INDEX_DIR. Commit 6b6f78f.
