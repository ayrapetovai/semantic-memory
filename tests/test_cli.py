r"""CLI regression: options after the positional, lint and gc commands."""

import datetime
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        memory = os.path.join(self.tmp, "memory")
        os.makedirs(memory)
        with open(os.path.join(memory, "flow.md"), "w", encoding="utf-8") as f:
            f.write("# flow\n\n## Decision stream\n\n"
                    "- (%s) The code root is the process cwd.\n"
                    % datetime.date.today().isoformat())
        self.env = dict(os.environ)
        self.env.update({
            "PYTHONPATH": ROOT,
            "SEMANTIC_MEMORY_MEMORY_DIR": memory,
            "SEMANTIC_MEMORY_DB": os.path.join(self.tmp, "index.db"),
            "SEMANTIC_MEMORY_INDEX_DIR": os.path.join(self.tmp, "indexes"),
        })

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "semantic_memory.cli", *args],
            cwd=self.tmp, env=self.env, capture_output=True, text=True, timeout=120)

    def test_k_after_positional(self):
        result = self.run_cli("facts", "code root", "--k", "3")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("unrecognized", result.stderr)
        self.assertIn("flow", result.stdout)

    def test_index_rejects_escaping_path(self):
        project = os.path.join(self.tmp, "project")
        os.makedirs(project, exist_ok=True)
        with open(os.path.join(self.tmp, "outside.py"), "w", encoding="utf-8") as f:
            f.write("def outside():\n    pass\n")
        result = self.run_cli("index", "../outside.py", "--root", project)
        self.assertEqual(1, result.returncode)
        self.assertIn("escapes", result.stdout)

    def test_lint_and_gc_are_available(self):
        lint = self.run_cli("lint")
        self.assertEqual(0, lint.returncode, lint.stderr)
        self.assertIn("memory clean", lint.stdout)
        gc = self.run_cli("gc", "--dry-run")
        self.assertEqual(0, gc.returncode, gc.stderr)
        self.assertIn("gc:", gc.stdout)


if __name__ == "__main__":
    unittest.main()
