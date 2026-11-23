r"""Tool arguments cannot escape the project root."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from semantic_memory import config, db, mcp_server  # noqa: E402


class PathSafetyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.root = os.path.join(self.tmp, "project")
        os.makedirs(self.root)
        with open(os.path.join(self.tmp, "secret.txt"), "w", encoding="utf-8") as f:
            f.write("TOP SECRET")
        with open(os.path.join(self.root, "ok.py"), "w", encoding="utf-8") as f:
            f.write("def ok():\n    return 1\n")
        self.con = db.connect(os.path.join(self.tmp, "index.db"))
        self.cwd = os.getcwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.cwd)
        self.con.close()

    def test_safe_path(self):
        self.assertIsNone(config.safe_path(self.root, "../secret.txt"))
        self.assertIsNone(config.safe_path(self.root, os.path.join(self.tmp, "secret.txt")))
        self.assertIsNone(config.safe_path(self.root, "~/secret.txt"))
        link = os.path.join(self.root, "link.txt")
        try:
            os.symlink(os.path.join(self.tmp, "secret.txt"), link)
            self.assertIsNone(config.safe_path(self.root, "link.txt"))
        except (OSError, NotImplementedError):
            pass
        self.assertTrue(config.safe_path(self.root, "src/../ok.py").endswith("ok.py"))

    def test_file_read_rejects_escape(self):
        text = mcp_server.call_tool(self.con, "file_read", {"path": "../secret.txt"})
        self.assertIn("escapes", text)
        self.assertNotIn("TOP SECRET", text)
        text = mcp_server.call_tool(self.con, "file_read", {"path": "ok.py"})
        self.assertIn("def ok()", text)

    def test_index_rebuild_rejects_escape(self):
        text = mcp_server.call_tool(self.con, "index_rebuild", {"path": ".."})
        self.assertIn("escapes", text)


if __name__ == "__main__":
    unittest.main()
