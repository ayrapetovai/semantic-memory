r"""Fact lifecycle: dates, archive, superseded markers, lint and rotation."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from semantic_memory import config, db, facts, search  # noqa: E402


LIFECYCLE = """# lifecycle

## Decision stream

- (2020-01-01) Old but still valid decision.
- (2026-01-01, superseded 2026-02-01) Replaced decision.
- Current decision without a date.

## Archive

- (2019-01-01) Stale decision kept for history.
"""


class FactsLifecycleTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_memory = config.MEMORY_DIR
        config.MEMORY_DIR = os.path.join(self.tmp, "memory")
        os.makedirs(config.MEMORY_DIR)
        self.con = db.connect(os.path.join(self.tmp, "index.db"))
        self.path = os.path.join(config.MEMORY_DIR, "lifecycle.md")
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(LIFECYCLE)

    def tearDown(self):
        config.MEMORY_DIR = self.old_memory
        self.con.close()

    def test_parse_dates_superseded_and_archive(self):
        entries = facts.parse_entries(self.path)
        self.assertEqual(3, len(entries))  # active, superseded, current
        self.assertEqual("2020-01-01", entries[0]["added"])
        self.assertEqual("2026-02-01", entries[1]["superseded"])
        self.assertIsNone(entries[2]["added"])  # backfilled from mtime at index time
        self.assertFalse(any("Stale decision kept" in e["text"] for e in entries))
        self.assertTrue(all("superseded" not in e["text"] for e in entries))

    def test_archived_and_superseded_are_not_searchable(self):
        facts.rebuild_facts(self.con)
        self.assertFalse(any("Stale decision kept" in h["text"]
                             for h in search.search_facts(self.con, "stale", k=10)))
        self.assertFalse(any("Replaced decision" in h["text"]
                             for h in search.search_facts(self.con, "replaced", k=10)))
        self.assertTrue(any("Old but still valid" in h["text"]
                            for h in search.search_facts(self.con, "valid", k=10)))

    def test_lint_finds_and_removes_exact_duplicates(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("# lifecycle\n\n## Decision stream\n\n"
                    "- Same decision.\n- Same decision.\n- Different decision.\n")
        report = facts.lint(self.con)
        kinds = [f["kind"] for f in report["findings"]]
        self.assertIn("duplicate", kinds)
        self.assertIn("undated", kinds)
        applied = facts.lint(self.con, apply=True)
        self.assertEqual(1, applied["removed"])
        entries = facts.parse_entries(self.path)
        self.assertEqual(2, len(entries))
        self.assertFalse(any(f["kind"] == "duplicate"
                             for f in facts.lint(self.con)["findings"]))

    def test_trim_moves_oldest_bullets_to_archive(self):
        content = "# lifecycle\n\n## Decision stream\n\n" + "".join(
            "- Decision number %d.\n" % i for i in range(config.MAX_TOPIC_BULLETS + 3))
        result = facts._trim_bullets(content)
        active = result.split("## Archive")[0]
        self.assertEqual(config.MAX_TOPIC_BULLETS,
                         sum(1 for line in active.splitlines() if line.startswith("- ")))
        self.assertIn("## Archive", result)
        self.assertIn("Decision number 0.", result.split("## Archive")[1])


if __name__ == "__main__":
    unittest.main()
