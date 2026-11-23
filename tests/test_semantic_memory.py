r"""Smoke tests for semantic-memory: code parsing, index, search, MCP tools.

Run: python tests/test_semantic_memory.py   (from the semantic-memory folder)
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp()
os.environ["SEMANTIC_MEMORY_DB"] = os.path.join(_TMP, "index.db")

from semantic_memory import codeindex, codeparse, config, db, embed, facts, mcp_server, search

SERVICE = '''"""Line search server."""
import json


def build_index(rows):
    """Build a line index keyed by id."""
    return {r["id"]: r for r in rows}


class Store:
    """Document storage."""

    def load(self, path):
        """Read a document from a file."""
        with open(path, encoding="utf-8") as f:
            return json.loads(f.read())


def search(store, query):
    idx = build_index(store.load("docs.json"))
    return [v for v in idx.values() if query in v["text"]]
'''

JS = '''export class Panel {
  constructor(opts) {
    this.opts = opts;
  }

  render(items) {
    return items.map(i => this.row(i));
  }

  row(item) {
    return `<div>${item.name}</div>`;
  }
}
'''


class SemanticMemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._cwd = os.getcwd()
        os.chdir(_TMP)  # the code root for MCP and CLI is the process cwd
        os.makedirs(os.path.join(_TMP, "src"), exist_ok=True)
        with open(os.path.join(_TMP, "src", "service.py"), "w", encoding="utf-8") as f:
            f.write(SERVICE)
        with open(os.path.join(_TMP, "src", "panel.js"), "w", encoding="utf-8") as f:
            f.write(JS)
        config.MEMORY_DIR = os.path.join(_TMP, "memory")
        cls.con = db.connect()
        files, symbols, changed, removed = codeindex.index_root(cls.con, _TMP)
        cls.files = files
        cls.symbols = symbols

    @classmethod
    def tearDownClass(cls):
        os.chdir(cls._cwd)

    def test_index_counts(self):
        self.assertEqual(self.files, 2)
        self.assertGreaterEqual(self.symbols, 6)

    def test_python_symbols(self):
        names = {s["qual"] for s in search.search_symbols(self.con, "build index", 5)}
        self.assertIn("build_index", names)
        outline = self.con.execute(
            "SELECT qual FROM sym WHERE file = 'src/service.py' ORDER BY start").fetchall()
        self.assertEqual([r[0] for r in outline],
                         ["build_index", "Store", "Store.load", "search"])

    def test_js_symbols(self):
        rows = self.con.execute(
            "SELECT qual, kind FROM sym WHERE file = 'src/panel.js' ORDER BY start").fetchall()
        self.assertEqual([r[0] for r in rows], ["Panel", "Panel.constructor",
                                                "Panel.render", "Panel.row"])

    def test_embedding_offsets(self):
        """Vectors align by file start + position, so every symbol gets one — not only
        the symbols of the first files (the running counter double-counted them before)."""
        if not embed.available():
            self.skipTest("fastembed is not installed")
        total = self.con.execute("SELECT COUNT(*) FROM sym").fetchone()[0]
        self.assertEqual(total, self.con.execute("SELECT COUNT(*) FROM sym_vec").fetchone()[0])
        last = self.con.execute(
            "SELECT id FROM sym WHERE file = 'src/panel.js' ORDER BY start DESC LIMIT 1"
        ).fetchone()[0]
        self.assertIsNotNone(self.con.execute("SELECT emb FROM sym_vec WHERE id = ?", (last,)).fetchone())
        self.assertEqual([], codeindex.symbols_without_vectors(self.con))

    def test_missing_vectors_are_rebuilt(self):
        """A database indexed by the old offset heals on the next reindex: the files
        whose symbols have no vector are re-embedded, the rest is left alone."""
        if not embed.available():
            self.skipTest("fastembed is not installed")
        db.meta_set(self.con, "vecfix", "0")  # pretend the index came from the old build
        self.con.execute("DELETE FROM sym_vec WHERE id IN"
                         " (SELECT id FROM sym WHERE file = 'src/panel.js')")
        self.con.commit()
        self.assertEqual(["src/panel.js"], codeindex.symbols_without_vectors(self.con))
        codeindex.index_root(self.con, _TMP)
        self.assertEqual([], codeindex.symbols_without_vectors(self.con))
        self.assertEqual("1", db.meta_get(self.con, "vecfix"))

    def test_callers_and_refs(self):
        callers = search.callers(self.con, "build_index")
        self.assertEqual([c["qual"] for c in callers], ["search"])
        self.assertIn("callees", ["callees"])
        self.assertIn("build_index", search.callees(self.con, "search"))
        refs = search.references(self.con, "build_index")
        self.assertTrue(any(r["file"] == "src/service.py" for r in refs))

    def test_symbol_body(self):
        found = search.symbol_by_name(self.con, "Store.load")
        self.assertEqual(len(found), 1)
        body = search.symbol_body(self.con, found[0]["id"])
        self.assertIn("json.loads", body["body"])

    def test_incremental_reindex(self):
        files, symbols, changed = codeindex.index_paths(
            self.con, _TMP, codeindex.iter_code(_TMP))
        self.assertEqual(len(changed), 0)
        with open(os.path.join(_TMP, "src", "extra.py"), "w", encoding="utf-8") as f:
            f.write("def extra_helper():\n    return 1\n")
        files, symbols, changed = codeindex.index_paths(
            self.con, _TMP, codeindex.iter_code(_TMP))
        self.assertEqual(files, 1)
        self.assertIn("extra_helper", search.symbol_by_name(self.con, "extra_helper")[0]["qual"])

    def test_facts(self):
        facts.append_fact(self.con, "Cooling fan: 120 mm, 45 dB, 35 W.", "hardware")
        facts.rebuild_facts(self.con)
        hits = search.search_facts(self.con, "cooling fan", 3, "hardware")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["topic"], "hardware")

    def test_mcp_tools(self):
        text = mcp_server.call_tool(self.con, "code_search", {"query": "build index"})
        self.assertIn("build_index", text)
        outline = mcp_server.call_tool(self.con, "file_outline", {"path": "src/service.py"})
        self.assertIn("Store.load", outline)
        stats = mcp_server.call_tool(self.con, "index_stats", {})
        self.assertIn("files", stats)
        self.assertEqual(len(mcp_server.TOOLS), 12)

    def test_db_per_root_files(self):
        """Each root keeps its own index file; other roots stay untouched."""
        old_db, old_dir = config.DB, config.INDEX_DIR
        other = tempfile.mkdtemp()
        try:
            config.DB = None
            config.INDEX_DIR = os.path.join(_TMP, "indexes")
            a = config.db_path(_TMP)
            self.assertEqual(a, config.db_path(_TMP + "/"))
            self.assertNotEqual(a, config.db_path(other))
            self.assertTrue(a.startswith(config.INDEX_DIR))
            with open(os.path.join(other, "only_other.py"), "w", encoding="utf-8") as f:
                f.write("def only_other():\n    return 1\n")

            os.chdir(other)
            con_b = mcp_server.open_db()
            self.assertEqual(con_b.execute("SELECT COUNT(*) FROM file").fetchone()[0], 1)
            self.assertIn("only_other", search.symbol_by_name(con_b, "only_other")[0]["qual"])

            os.chdir(_TMP)
            con_a = mcp_server.open_db()
            self.assertEqual(con_a.execute("SELECT COUNT(*) FROM file").fetchone()[0], 2)
            self.assertIn("build_index", search.symbol_by_name(con_a, "build_index")[0]["qual"])

            con_b2 = db.connect(config.db_path(other))
            self.assertEqual(con_b2.execute("SELECT COUNT(*) FROM file").fetchone()[0], 1)
        finally:
            config.DB, config.INDEX_DIR = old_db, old_dir
            os.chdir(_TMP)

    def test_mcp_root_follows_cwd(self):
        """MCP indexes the process cwd: changing directory rebuilds the index.

        Facts stay; code from the old root is dropped.
        """
        other = tempfile.mkdtemp()
        with open(os.path.join(other, "other.py"), "w", encoding="utf-8") as f:
            f.write("def other_tool():\n    return 2\n")
        os.chdir(other)
        try:
            con = mcp_server.open_db()
            stats = mcp_server.call_tool(con, "index_stats", {})
            self.assertIn(other, stats)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM file").fetchone()[0], 1)
            self.assertIn("other_tool", search.symbol_by_name(con, "other_tool")[0]["qual"])
            outline = mcp_server.call_tool(con, "file_outline", {"path": "src/service.py"})
            self.assertIn("not indexed", outline)
        finally:
            os.chdir(_TMP)
            codeindex.index_root(self.con, _TMP)  # restore the shared index for the other tests

    def test_paths_portable(self):
        """The index stores "/"; a path argument is accepted in any form."""
        rp = codeindex.rel(os.path.join(_TMP, "src", "service.py"), _TMP)
        self.assertEqual(rp, "src/service.py")
        self.assertNotIn("\\", rp)
        for variant in ("src\\service.py", "src/service.py", os.path.join(_TMP, "src", "service.py")):
            outline = mcp_server.call_tool(self.con, "file_outline", {"path": variant})
            self.assertIn("Store.load", outline)

    def test_parse_language_detection(self):
        self.assertEqual(codeparse.language("a.py"), "python")
        self.assertEqual(codeparse.language("a.tsx"), "typescript")
        self.assertIsNone(codeparse.language("a.md"))


if __name__ == "__main__":
    unittest.main()
