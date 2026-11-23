r"""Retrieval quality: nonsense queries and noisy topics must not be injected."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from semantic_memory import config, db, facts, search  # noqa: E402


class SearchQualityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.old_memory = config.MEMORY_DIR
        config.MEMORY_DIR = os.path.join(cls.tmp, "memory")
        os.makedirs(config.MEMORY_DIR)
        with open(os.path.join(config.MEMORY_DIR, "plugin.md"), "w", encoding="utf-8") as f:
            f.write("# plugin\n\n## Decision stream\n\n"
                    "- Plugin delivery uses init.py --plugin.\n"
                    "- (2026-01-01) Hybrid ranking merges FTS and vector results.\n")
        with open(os.path.join(config.MEMORY_DIR, "chat.md"), "w", encoding="utf-8") as f:
            f.write("# chat\n\n## Decision stream\n\n"
                    "- Where do I install the plugin?\n- ok.\n")
        cls.con = db.connect(os.path.join(cls.tmp, "index.db"))
        facts.rebuild_facts(cls.con)

    @classmethod
    def tearDownClass(cls):
        config.MEMORY_DIR = cls.old_memory
        cls.con.close()

    def test_nonsense_query_returns_nothing(self):
        self.assertEqual([], search.search_facts(self.con, "asdkjhasd qwerty", k=5))
        self.assertEqual([], search.search_facts(self.con, "zzzqqqxxxyyy plugh", k=5))
        # the floor is configurable: a strict floor still leaves FTS hits
        self.assertTrue(search.search_facts(self.con, "hybrid ranking", k=5,
                                            min_sim=0.99))

    def test_exclude_topics_removes_prompt_noise(self):
        all_hits = search.search_facts(self.con, "install plugin", k=10)
        self.assertTrue(any(h["topic"] == "chat" for h in all_hits))
        clean = search.search_facts(self.con, "install plugin", k=10,
                                    exclude_topics=["chat"])
        self.assertTrue(clean)
        self.assertNotIn("chat", [h["topic"] for h in clean])

    def test_result_has_provenance_and_date(self):
        hits = search.search_facts(self.con, "hybrid ranking", k=3)
        self.assertTrue(hits)
        self.assertIn("via", hits[0])
        self.assertIn(hits[0]["via"], ("fts", "vec", "both"))
        self.assertEqual("2026-01-01", hits[0]["added"])


if __name__ == "__main__":
    unittest.main()
