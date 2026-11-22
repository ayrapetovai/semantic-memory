r"""semantic-memory: индекс фактов проекта и кода + MCP-сервер для harness.

Факты — memory\\*.md (один bullet = один факт), код — обход корня проекта:
символы, вызовы, ссылки, резюме файлов. Поиск гибрид: SQLite FTS5 + векторы
MiniLM, слияние по рангам.
"""

from . import codeindex, codeparse, config, db, embed, facts, search

__all__ = ["codeindex", "codeparse", "config", "db", "embed", "facts", "search"]
