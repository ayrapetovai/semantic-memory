r"""Пути, модель и лимиты semantic-memory.

Корень проекта — папка semantic-memory: здесь memory\*.md (факты проекта),
index.db (индекс фактов + кода) и .models\ (кэш ONNX-модели, не в git).
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_DIR = os.path.join(ROOT, "memory")
DB = os.environ.get("SEMANTIC_MEMORY_DB") or os.path.join(ROOT, "index.db")
MODEL_CACHE = os.environ.get("SEMANTIC_MEMORY_MODEL_CACHE") or os.path.join(ROOT, ".models")
MODEL_NAME = os.environ.get(
    "SEMANTIC_MEMORY_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)

DEFAULT_TOPIC = "flow"
TOPIC_SPAN = 1000000          # стабильный диапазон id темы фактов
SYMBOL_ID_BASE = 10_000_000   # id символов кода начинаются после фактов

MAX_EMBED_CHARS = 900         # эмбеддинг: сигнатура + docstring + начало тела
MAX_BODY_CHARS = 4000         # тело символа, возвращаемое LLM
MAX_FILE_BYTES = 220_000
MAX_EXTENT_LINES = 400

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "dist", "build", "out", "target",
    "__pycache__", ".models", ".next", ".cache", ".tox", "vendor", ".idea",
    ".vscode", "coverage", ".pytest_cache", "memory",
}
SKIP_SUFFIXES = {".min.js", ".min.css", ".map", ".bundle.js", ".lock", ".svg",
                 ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2",
                 ".pdf", ".zip", ".lock", ".log"}

CODE_EXT = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java", ".cs": "csharp",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".rb": "ruby", ".php": "php",
}
