r"""Test for init.py's plugin delivery: copy the plugin into a code root and ping the bridge.

Run: python tests/test_init_plugin.py
"""

import builtins
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp()

sys.path.insert(0, ROOT)

# an isolated memory store and index file for the fake code root
os.makedirs(os.path.join(TMP, "memory"), exist_ok=True)
with open(os.path.join(TMP, "memory", "notes.md"), "w", encoding="utf-8") as f:
    f.write("# notes\n\n- The test project has one file.\n")
os.environ["SEMANTIC_MEMORY_ROOT"] = TMP
os.environ["SEMANTIC_MEMORY_DB"] = os.path.join(TMP, "index.db")
os.environ["SEMANTIC_MEMORY_MEMORY_DIR"] = os.path.join(TMP, "memory")

import init  # noqa: E402  (environment is set above the import)


def capture(fn, *args, **kwargs):
    """Run fn and return (result, lines it printed)."""
    lines = []
    real_print = builtins.print
    try:
        builtins.print = lambda *a, **k: lines.append(" ".join(str(x) for x in a))
        return fn(*args, **kwargs), "\n".join(lines)
    finally:
        builtins.print = real_print


def test_state_and_install():
    assert os.path.normcase(init.plugin_target()) == os.path.normcase(
        os.path.join(TMP, ".opencode", "plugins", "semantic-memory")
    )
    assert "missing" in init.plugin_state(), init.plugin_state()

    ok, out = capture(init.install_plugin)
    assert ok, out
    assert "copied" in out, out
    assert any(rel == "index.ts" for rel, _ in init.plugin_files(init.plugin_target()))
    assert "matches the repo" in init.plugin_state(), init.plugin_state()

    # idempotent: the second run copies nothing
    ok, out = capture(init.install_plugin)
    assert ok and "0 copied" in out, out


def test_foreign_edit_is_not_overwritten():
    path = os.path.join(init.plugin_target(), "index.ts")
    with open(path, "a", encoding="utf-8") as f:
        f.write("\n// edited by the user\n")
    assert "differs from the repo" in init.plugin_state(), init.plugin_state()

    ok, out = capture(init.install_plugin)
    assert ok and "1 skipped" in out, out
    with open(path, encoding="utf-8") as f:
        assert "// edited by the user" in f.read(), "the foreign edit must survive"

    ok, out = capture(init.install_plugin, force=True)
    assert ok and "1 copied" in out, out
    assert "matches the repo" in init.plugin_state(), init.plugin_state()
    with open(path, encoding="utf-8") as f:
        assert "// edited by the user" not in f.read(), "--force overwrites"


def test_repository_root_is_not_copied():
    os.environ["SEMANTIC_MEMORY_ROOT"] = ROOT
    assert "repository itself" in init.plugin_state(), init.plugin_state()
    ok, out = capture(init.install_plugin)
    assert ok and "repository itself" in out, out
    os.environ["SEMANTIC_MEMORY_ROOT"] = TMP


def test_plugin_files_skips_node_modules():
    base = tempfile.mkdtemp()
    os.makedirs(os.path.join(base, "node_modules", "pkg"), exist_ok=True)
    os.makedirs(os.path.join(base, "sub"), exist_ok=True)
    for rel in ("index.ts", "package.json", "sub/deep.ts", "node_modules/pkg/index.js"):
        with open(os.path.join(base, rel), "w", encoding="utf-8") as f:
            f.write(rel)
    names = sorted(rel for rel, _ in init.plugin_files(base))
    assert names == ["index.ts", "package.json", "sub/deep.ts"], names


def test_snippet_is_valid_json():
    conf = json.loads(init.plugin_snippet())
    server = conf["mcp"]["servers"]["semantic-memory"]
    assert server["type"] == "local"
    assert server["command"][1] == os.path.join(ROOT, "run_mcp.py"), server["command"]
    plugin = conf["plugins"][0]
    assert os.path.isfile(os.path.join(plugin["package"], "index.ts")), plugin["package"]
    assert plugin["options"]["python"], plugin["options"]


def test_bridge_ping_answers_in_the_code_root():
    alive, info = init.bridge_ping()
    assert alive, info
    assert os.path.abspath(info) == os.path.abspath(TMP), info


def test_check_reports_the_plugin():
    ok, out = capture(init.check)
    assert ok is None
    assert "code root: %s" % TMP in out, out
    assert "plugin:" in out, out


if __name__ == "__main__":
    # the order matters: the state of the fake code root changes step by step
    for fn in (test_state_and_install, test_foreign_edit_is_not_overwritten,
               test_repository_root_is_not_copied, test_plugin_files_skips_node_modules,
               test_snippet_is_valid_json, test_bridge_ping_answers_in_the_code_root,
               test_check_reports_the_plugin):
        fn()
    print("init plugin OK")
