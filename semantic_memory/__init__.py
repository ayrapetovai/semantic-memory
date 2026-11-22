r"""semantic-memory: index of project facts and code + MCP server for the harness.

Facts are memory\\*.md (one bullet = one fact); code comes from walking the project
root: symbols, calls, references, file summaries. Search is hybrid: SQLite FTS5 +
MiniLM vectors, merged by rank.
"""

from . import codeindex, codeparse, config, db, embed, facts, search

__all__ = ["codeindex", "codeparse", "config", "db", "embed", "facts", "search"]
