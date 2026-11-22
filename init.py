r"""Bootstrap semantic-memory: venv, пакеты, кэш модели, индекс.

  python init.py            создать .venv, поставить пакеты, собрать индекс
  python init.py --check    только показать состояние, ничего не менять
  python init.py --reindex  перестроить индекс целиком

Кэш ONNX-модели копируется из ..\mem0-like\.models, если там есть MiniLM — так
не нужен повторный download. Корень кода для индекса — переменная
SEMANTIC_MEMORY_ROOT или родительская папка проекта.
"""

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(ROOT, ".venv")
VENV_PY = os.path.join(VENV, "Scripts", "python.exe") if os.name == "nt" else \
    os.path.join(VENV, "bin", "python")
CACHE = os.path.join(ROOT, ".models")
DONOR = os.path.join(os.path.dirname(ROOT), "mem0-like", ".models")


def run(cmd, cwd=None):
    print("$ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=cwd)


def code_root():
    return os.environ.get("SEMANTIC_MEMORY_ROOT") or os.path.dirname(ROOT)


def ensure_venv():
    if not os.path.exists(VENV_PY):
        run([sys.executable, "-m", "venv", VENV])
    run([VENV_PY, "-m", "pip", "install", "-r", os.path.join(ROOT, "requirements.txt")])


def ensure_model_cache():
    if os.path.isdir(CACHE) and any(
            d.startswith("models--") for d in os.listdir(CACHE)
            if os.path.isdir(os.path.join(CACHE, d))):
        return True
    if os.path.isdir(DONOR):
        os.makedirs(CACHE, exist_ok=True)
        for name in os.listdir(DONOR):
            src = os.path.join(DONOR, name)
            if os.path.isdir(src):
                shutil.copytree(src, os.path.join(CACHE, name), dirs_exist_ok=True)
        return True
    return False


def check():
    print("venv: %s" % ("есть" if os.path.exists(VENV_PY) else "нет"))
    print("кэш модели: %s" % ("есть" if os.path.isdir(CACHE) and os.listdir(CACHE) else "нет"))
    print("корень кода: %s" % code_root())
    print("БД: %s" % os.path.join(ROOT, "index.db"))


def reindex(force=False):
    """Перестройка индекса — внутри venv, где стоят fastembed и numpy."""
    py = VENV_PY if os.path.isfile(VENV_PY) else sys.executable
    code = (
        "import sys\nsys.path.insert(0, " + repr(ROOT) + ")\n"
        "from semantic_memory import codeindex, db, facts\n"
        "con = db.connect()\n"
        "files, symbols, changed, removed = codeindex.index_root(con, "
        + repr(code_root()) + ", force=" + repr(bool(force)) + ")\n"
        "n, topics = facts.rebuild_facts(con, force=" + repr(bool(force)) + ")\n"
        "print('индекс: файлов %d, символов %d, перестроено %d, удалено %d'\n"
        "      % (files, symbols, len(changed), len(removed)))\n"
        "print('факты: %d, темы: %s' % (n, ', '.join(topics)))\n"
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
    print("готово. MCP: %s -m semantic_memory.mcp (SEMANTIC_MEMORY_ROOT=%s)"
          % (VENV_PY, code_root()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
