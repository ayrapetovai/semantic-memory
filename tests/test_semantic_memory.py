r"""Smoke-тесты semantic-memory: разбор кода, индекс, поиск, MCP-инструменты.

Запуск: python tests/test_semantic_memory.py   (из папки semantic-memory)
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp()
os.environ["SEMANTIC_MEMORY_DB"] = os.path.join(_TMP, "index.db")

from semantic_memory import codeindex, codeparse, config, db, facts, mcp_server, search

SERVICE = '''"""Сервер поиска по строкам."""
import json


def build_index(rows):
    """Собрать индекс строк по id."""
    return {r["id"]: r for r in rows}


class Store:
    """Хранилище документов."""

    def load(self, path):
        """Прочитать документ из файла."""
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
        os.chdir(_TMP)  # корень кода для MCP и CLI — cwd процесса
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
        facts.append_fact(self.con, "Кулер: 120 мм, 45 дБ, 35 Вт.", "hardware")
        facts.rebuild_facts(self.con)
        hits = search.search_facts(self.con, "кулер", 3, "hardware")
        self.assertTrue(hits)
        self.assertEqual(hits[0]["topic"], "hardware")

    def test_mcp_tools(self):
        text = mcp_server.call_tool(self.con, "code_search", {"query": "build index"})
        self.assertIn("build_index", text)
        outline = mcp_server.call_tool(self.con, "file_outline", {"path": "src/service.py"})
        self.assertIn("Store.load", outline)
        stats = mcp_server.call_tool(self.con, "index_stats", {})
        self.assertIn("файлы", stats)
        self.assertEqual(len(mcp_server.TOOLS), 12)

    def test_mcp_root_follows_cwd(self):
        """MCP индексирует cwd процесса: смена директории перестраивает индекс.

        Факты остаются, код старого корня снимается.
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
            self.assertIn("не индексирован", outline)
        finally:
            os.chdir(_TMP)
            codeindex.index_root(self.con, _TMP)  # вернуть общий индекс для остальных тестов

    def test_paths_portable(self):
        """Индекс хранит «/»; аргумент пути принимается в любом виде."""
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
