r"""Bootstrap semantic-memory: venv, packages, model cache, index, plugin.

  python init.py            create .venv, install the packages, build the index
  python init.py --check    only show the state, change nothing
  python init.py --reindex  rebuild the index completely
  python init.py --plugin   copy the plugin into the code root and check the bridge
  python init.py --print    print the opencode.jsonc block for the code root

The ONNX model cache lives in .models (not in git); if it is missing, fastembed
downloads the model on first use. The code root for the index is the working directory
where init.py is run (or the SEMANTIC_MEMORY_ROOT variable, expanduser `~`); the index
is a separate file per root in .indexes. Python in the venv: Windows —
.venv\Scripts\python.exe, Linux and macOS — .venv/bin/python.

The plugin (.opencode/plugins/semantic-memory) lives in this repository and is only
auto-discovered when OpenCode is opened here. For another project --plugin copies it
into that root; the copied plugin must be told which python has semantic-memory
(option python in opencode.jsonc), otherwise the bridge dies with ModuleNotFoundError.
"""

import hashlib
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from semantic_memory import config, db, facts

ROOT = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(ROOT, ".venv")
VENV_PY = os.path.join(VENV, "Scripts", "python.exe") if os.name == "nt" else \
    os.path.join(VENV, "bin", "python")
CACHE = os.path.join(ROOT, ".models")
PLUGIN_SRC = os.path.join(ROOT, ".opencode", "plugins", "semantic-memory")
PLUGIN_DST = os.path.join(".opencode", "plugins", "semantic-memory")


def run(cmd, cwd=None):
    print("$ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=cwd)


def code_root():
    value = os.environ.get("SEMANTIC_MEMORY_ROOT")
    return os.path.abspath(os.path.expanduser(value)) if value else os.getcwd()


def venv_python():
    """Python with semantic-memory installed: the venv if it exists, else this interpreter."""
    return VENV_PY if os.path.isfile(VENV_PY) else sys.executable


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


def index_state():
    """Number and size of per-root index files under the index directory."""
    directory = config.INDEX_DIR
    if not os.path.isdir(directory):
        return "0 files"
    files = [f for f in os.listdir(directory) if f.endswith(".db")]
    size = sum(os.path.getsize(os.path.join(directory, f)) for f in files)
    hint = " (gc available)" if len(files) > 1 else ""
    return "%d file(s), %.1f MB%s" % (len(files), size / (1024.0 * 1024.0), hint)


def lint_state():
    """Memory lint summary without touching a real index file."""
    con = db.connect(":memory:")
    try:
        report = facts.lint(con)
    except Exception as exc:  # a broken memory file must not break --check
        return "unavailable: %s" % exc
    finally:
        con.close()
    if not report["findings"]:
        return "clean (%d topics)" % report["topics"]
    return "%d finding(s) in %d topics" % (len(report["findings"]), report["topics"])


def check():
    print("venv: %s" % ("present" if os.path.isfile(VENV_PY) else "missing"))
    print("model cache: %s" % ("present" if os.path.isdir(CACHE) and os.listdir(CACHE) else "missing"))
    print("code root: %s" % code_root())
    print("db: %s" % config.db_path(code_root()))
    print("indexes: %s" % index_state())
    print("plugin: %s" % plugin_state())
    print("memory lint: %s" % lint_state())
    print("plugin defaults: captureChat=off, distill=off, inject=on, "
          "excludeTopics=chat,state, minSim=%.2f" % config.MIN_VECTOR_SIM)
    alive, info = bridge_ping()
    print("bridge: %s (%s)" % ("alive" if alive else "failed", info))


def reindex(force=False):
    """Index rebuild — inside the venv, where fastembed and numpy are installed."""
    py = venv_python()
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


def plugin_target():
    """Where the plugin must live: the code root's .opencode/plugins/semantic-memory."""
    return os.path.join(code_root(), PLUGIN_DST)


def plugin_files(base):
    """Files under base as [(relative path with '/', absolute path)]; node_modules is skipped."""
    out = []
    for dirpath, dirnames, names in os.walk(base):
        dirnames[:] = [d for d in dirnames if d != "node_modules" and not d.startswith(".")]
        for name in sorted(names):
            full = os.path.join(dirpath, name)
            out.append((os.path.relpath(full, base).replace("\\", "/"), full))
    return out


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def plugin_state():
    """Plugin in the code root: missing, matches the repository, or differs from it."""
    if not os.path.isdir(PLUGIN_SRC):
        return "source missing: %s" % PLUGIN_SRC
    dst = plugin_target()
    if os.path.normcase(dst) == os.path.normcase(PLUGIN_SRC):
        return "%s (the code root is the repository itself)" % dst
    files = plugin_files(dst)
    if not files:
        return "%s (missing)" % dst
    src = {rel: _sha(full) for rel, full in plugin_files(PLUGIN_SRC)}
    differs = any(_sha(full) != src.get(rel) for rel, full in files if rel in src)
    return "%s (%d files, %s)" % (dst, len(files), "differs from the repo" if differs
                                  else "matches the repo")


def install_plugin(force=False):
    """Copy the plugin into the code root: idempotent by sha256, foreign files only with force."""
    if not os.path.isdir(PLUGIN_SRC):
        print("plugin source missing: %s" % PLUGIN_SRC)
        return False
    dst = plugin_target()
    if os.path.normcase(dst) == os.path.normcase(PLUGIN_SRC):
        print("plugin: the code root is the repository itself, nothing to copy")
        return True
    copied = skipped = 0
    for rel, src_path in plugin_files(PLUGIN_SRC):
        target = os.path.join(dst, *rel.split("/"))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        if os.path.isfile(target) and _sha(target) == _sha(src_path):
            continue
        if os.path.isfile(target) and not force:
            print("skip %s: differs from the repository (use --force)" % target)
            skipped += 1
            continue
        with open(src_path, "rb") as f:
            data = f.read()
        with open(target, "wb") as f:
            f.write(data)
        copied += 1
    print("plugin: %s — %d copied, %d skipped" % (dst, copied, skipped))
    return True


def bridge_ping():
    """Does the bridge start in the code root? One NDJSON ping with the venv python."""
    env = dict(os.environ, PYTHONPATH=ROOT)
    proc = subprocess.Popen([venv_python(), "-m", "semantic_memory.bridge"], cwd=code_root(),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=env)
    try:
        # closing stdin makes the bridge exit once it has answered the ping
        out, _ = proc.communicate(json.dumps({"cmd": "ping"}) + "\n", timeout=20)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        return False, "bridge timeout"
    line = out.strip().splitlines()[0] if out and out.strip() else ""
    try:
        answer = json.loads(line)
    except ValueError:
        return False, line or "no answer"
    return bool(answer.get("ok")), answer.get("root") or code_root()


def plugin_snippet():
    """opencode.jsonc block for the code root: MCP server + plugin with the python option."""
    py = venv_python()
    return (
        "{\n"
        "  \"mcp\": {\n"
        "    \"servers\": {\n"
        "      \"semantic-memory\": {\n"
        "        \"type\": \"local\",\n"
        "        \"command\": [%s, %s]\n"
        "      }\n"
        "    }\n"
        "  },\n"
        "  \"plugins\": [\n"
        "    {\n"
        "      \"package\": %s,\n"
        "      \"options\": {\"python\": %s}\n"
        "    }\n"
        "  ]\n"
        "}\n"
    ) % (json.dumps(py), json.dumps(os.path.join(ROOT, "run_mcp.py")),
         json.dumps(plugin_target()), json.dumps(py))


def main(argv=None):
    argv = argv or sys.argv[1:]
    if "--check" in argv:
        check()
        if "--print" in argv:
            print(plugin_snippet())
        return 0
    if "--print" in argv and "--plugin" not in argv:
        print(plugin_snippet())
        return 0
    ensure_venv()
    ensure_model_cache()
    reindex(force="--reindex" in argv)
    if "--plugin" in argv:
        if install_plugin(force="--force" in argv):
            alive, info = bridge_ping()
            print("bridge: %s (%s)" % ("alive" if alive else "failed", info))
    print("done. MCP: %s %s (the root is the session cwd)"
          % (venv_python(), os.path.join(ROOT, "run_mcp.py")))
    if "--plugin" in argv:
        print(plugin_snippet())
    return 0


if __name__ == "__main__":
    sys.exit(main())
