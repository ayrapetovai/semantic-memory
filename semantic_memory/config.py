r"""Paths, model and limits for semantic-memory.

The project root is the process working directory: there live memory\*.md — the
single source of memory — and .indexes (one index file per code root). The model
cache .models stays next to the package, shared by all projects. Both paths can be
overridden with SEMANTIC_MEMORY_MEMORY_DIR and SEMANTIC_MEMORY_INDEX_DIR.
"""

import hashlib
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEMORY_DIR = os.environ.get("SEMANTIC_MEMORY_MEMORY_DIR") or os.path.join(os.getcwd(), "memory")
DB = os.environ.get("SEMANTIC_MEMORY_DB")  # override: one shared file for all roots
INDEX_DIR = os.environ.get("SEMANTIC_MEMORY_INDEX_DIR") or os.path.join(os.getcwd(), ".indexes")


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
MAX_TOPIC_BULLETS = 200       # cap on active bullets; older ones move to ## Archive

# Retrieval quality: a fact found only by vector rank must be at least this similar
# to the query, otherwise a nonsense query injects unrelated memory.
MIN_VECTOR_SIM = float(os.environ.get("SEMANTIC_MEMORY_MIN_SIM", "0.45"))
# A vector-only hit with no shared query token needs stronger evidence than a
# moderate cosine: gibberish can score 0.5 against an unrelated short bullet.
MIN_VECTOR_STRONG_SIM = float(os.environ.get("SEMANTIC_MEMORY_STRONG_SIM", "0.70"))
# Two bullets in one topic above this cosine or with identical normalized text
# are considered the same fact.
DEDUPE_SIM = float(os.environ.get("SEMANTIC_MEMORY_DEDUPE_SIM", "0.92"))
# `memory_lint` marks facts older than this as stale candidates.
STALE_DAYS = int(os.environ.get("SEMANTIC_MEMORY_STALE_DAYS", "180"))
# Above this many symbols the brute-force vector scan deserves a warning.
ANN_WARN = 50_000

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


def safe_path(root, rel):
    """Absolute path inside root for a user-supplied relative path, else None.

    Rejects absolute paths, `~`, `..` escapes and symlinks that resolve outside
    the root. Used by file_read/index_rebuild so a tool argument cannot read or
    walk the rest of the filesystem.
    """
    if not rel:
        return None
    p = str(rel).replace("\\", "/")
    if p.startswith("~") or os.path.isabs(p):
        return None
    base = os.path.realpath(root)
    full = os.path.realpath(os.path.join(base, p))
    try:
        if os.path.commonpath([base, full]) != base:
            return None
    except ValueError:  # different drives on Windows
        return None
    return full
