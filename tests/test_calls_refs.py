r"""Call graph and references: module-level calls and late definitions."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from semantic_memory import codeindex, db, search  # noqa: E402

MODULES = '''def helper():
    """Helper."""
    return 1


def main():
    """Entry point."""
    return helper()


main()
'''

REF = '''value = later()
'''

DEF = '''def later():
    """Defined after the file that uses it was indexed."""
    return 2
'''


class CallsRefsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.con = db.connect(os.path.join(self.tmp, "index.db"))
        for name, text in (("mods.py", MODULES), ("ref.py", REF), ("definition.py", DEF)):
            with open(os.path.join(self.tmp, name), "w", encoding="utf-8") as f:
                f.write(text)

    def tearDown(self):
        self.con.close()

    def index(self, *names):
        codeindex.index_paths(self.con, self.tmp,
                              [os.path.join(self.tmp, n) for n in names])

    def test_module_level_call_is_reported(self):
        self.index("mods.py")
        helpers = search.callers(self.con, "helper")
        self.assertIn("main", [c["qual"] for c in helpers])
        mains = search.callers(self.con, "main")
        self.assertTrue(any(c["qual"] is None and c["file"] == "mods.py"
                            for c in mains), mains)
        self.assertTrue(all(c["line"] for c in mains))

    def test_ref_to_late_definition_is_found_without_reindexing_the_file(self):
        self.index("ref.py")
        self.index("definition.py")
        rows = search.references(self.con, "later")
        self.assertTrue(any(r["file"] == "ref.py" for r in rows), rows)


if __name__ == "__main__":
    unittest.main()
