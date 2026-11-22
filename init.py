r"""Bootstrap semantic-memory: venv, packages, model cache, index.

  python init.py            create .venv, install the packages, build the index
  python init.py --check    only show the state, change nothing
  python init.py --reindex  rebuild the index completely

The ONNX model cache lives in .models (not in git); if it is missing, fastembed
downloads the model on first use. The code root for the index is the working directory
where init.py is run (or the SEMANTIC_MEMORY_ROOT variable, expanduser `~`); the index
is a separate file per root in .indexes. Python in the venv: Windows —
.venv\Scripts\python.exe, Linux and macOS — .venv/bin/python.
"""

import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from semantic_memory import config

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(ROOT, ".venv")
VENV_PY = os.path.join(VENV, "Scripts", "python.exe") if os.name == "nt" else \
    os.path.join(VENV, "bin", "python")
CACHE = os.path.join(ROOT, ".models")


def run(cmd, cwd=None):
    print("$ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=cwd)


def code_root():
    value = os.environ.get("SEMANTIC_MEMORY_ROOT")
    return os.path.abspath(os.path.expanduser(value)) if value else os.getcwd()


def ensure_venv():
    if not os.path.exists(VENV_PY):
        run([sys.executable, "-m", "venv", VENV])
    run([VENV_PY, "-m", "pip", "install", "-r", os.path.join(ROOT, "requirements.txt")])


def ensure_model_cache():
    """Local model cache; if it is missing, fastembed downloads it on the first embedding."""
    return os.path.isdir(CACHE) and any(
        d.startswith("models--") for d in os.listdir(CACHE)
        if os.path.isdir(os.path.join(CACHE, d))
    )


def check():
    print("venv: %s" % ("present" if os.path.exists(VENV_PY) else "missing"))
    print("model cache: %s" % ("present" if os.path.isdir(CACHE) and os.listdir(CACHE) else "missing"))
    print("code root: %s" % code_root())
    print("db: %s" % config.db_path(code_root()))


def reindex(force=False):
    """Index rebuild — inside the venv, where fastembed and numpy are installed."""
    py = VENV_PY if os.path.isfile(VENV_PY) else sys.executable
    code = (
        "import sys\nsys.path.insert(0, " + repr(ROOT) + ")\n"
        "from semantic_memory import codeindex, config, db, facts\n"
        "con = db.connect(config.db_path(" + repr(code_root()) + "))\n"
        "files, symbols, changed, removed = codeindex.index_root(con, "
        + repr(code_root()) + ", force=" + repr(bool(force)) + ")\n"
        "n, topics = facts.rebuild_facts(con, force=" + repr(bool(force)) + ")\n"
        "print('index: %d files, %d symbols, %d rebuilt, %d removed'\n"
        "      % (files, symbols, len(changed), len(removed)))\n"
        "print('facts: %d, topics: %s' % (n, ', '.join(topics)))\n"
    )
    return run([py, "-c", code])


def main(argv=None):
    argv = argv or sys.argv[1:]
    if "--check" in argv:
        check()
        return 0
    ensure_venv()
    ensure_model_cache()
    reindex(force="--reindex" in argv)
    print("done. MCP: %s %s (the root is the session cwd)"
          % (VENV_PY, os.path.join(ROOT, "run_mcp.py")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
