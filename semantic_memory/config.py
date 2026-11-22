r"""Paths, model and limits for semantic-memory.

The project root is the semantic-memory folder: here live .indexes (one index file
per code root) and .models (ONNX model cache, not in git). Facts are read from
memory\*.md under that root — the single source of memory; the path can be
overridden with SEMANTIC_MEMORY_MEMORY_DIR.
"""

import hashlib
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_DIR = os.environ.get("SEMANTIC_MEMORY_MEMORY_DIR") or os.path.join(ROOT, "memory")
DB = os.environ.get("SEMANTIC_MEMORY_DB")  # override: one shared file for all roots
INDEX_DIR = os.environ.get("SEMANTIC_MEMORY_INDEX_DIR") or os.path.join(ROOT, ".indexes")


def db_path(root):
    """Index file per code root: .indexes/<sha256 of root>.db.

    Every project keeps its own index, so changing cwd does not touch other ones.
    SEMANTIC_MEMORY_DB sets one shared file for all roots.
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
TOPIC_SPAN = 1000000          # stable id range for a fact topic
SYMBOL_ID_BASE = 10_000_000   # code symbol ids start after the fact ids
MAX_TOPIC_BULLETS = 200       # cap on bullets per topic file (oldest ones are dropped)

MAX_EMBED_CHARS = 900         # embedding: signature + docstring + start of body
MAX_BODY_CHARS = 4000         # symbol body returned to the LLM
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
