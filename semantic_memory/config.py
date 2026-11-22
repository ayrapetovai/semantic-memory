r"""Пути, модель и лимиты semantic-memory.

Корень проекта — папка semantic-memory: здесь .indexes (файл индекса под каждый корень кода)
и .models (кэш ONNX-модели, не в git). Факты читаются из mem0-like\memory\*.md —
это единственный источник памяти проекта; своя папка memory\ используется только
если её нет (или задан SEMANTIC_MEMORY_MEMORY_DIR).
"""

import hashlib
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CANONICAL_MEMORY = os.path.join(os.path.dirname(ROOT), "mem0-like", "memory")
MEMORY_DIR = os.environ.get("SEMANTIC_MEMORY_MEMORY_DIR") or (
    CANONICAL_MEMORY if os.path.isdir(CANONICAL_MEMORY) else os.path.join(ROOT, "memory")
)
DB = os.environ.get("SEMANTIC_MEMORY_DB")  # override: один общий файл на все корни
INDEX_DIR = os.environ.get("SEMANTIC_MEMORY_INDEX_DIR") or os.path.join(ROOT, ".indexes")


def db_path(root):
    """Файл индекса под корень: .indexes/<sha256 корня>.db.

    Так каждый проект хранит свой индекс и смена cwd не трогает чужие.
    SEMANTIC_MEMORY_DB задаёт один общий файл для всех корней.
    """
    if DB:
        return DB
    key = hashlib.sha256(os.path.abspath(root).encode("utf-8")).hexdigest()[:16]
    return os.path.join(INDEX_DIR, key + ".db")
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
                 ".pdf", ".zip", ".log"}

CODE_EXT = {
    ".py": "python", ".pyi": "python",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".ts": "typescript", ".tsx": "typescript",
    ".go": "go", ".rs": "rust", ".java": "java", ".cs": "csharp",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp",
    ".hpp": "cpp", ".rb": "ruby", ".php": "php",
}
