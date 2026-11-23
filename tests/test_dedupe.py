r"""Writes: deduplication, redaction, force bypass."""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from semantic_memory import config, db, facts  # noqa: E402


class AddFactTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.old_memory = config.MEMORY_DIR
        config.MEMORY_DIR = os.path.join(self.tmp, "memory")
        os.makedirs(config.MEMORY_DIR)
        self.con = db.connect(os.path.join(self.tmp, "index.db"))

    def tearDown(self):
        config.MEMORY_DIR = self.old_memory
        self.con.close()

    def test_exact_and_normalized_duplicates_are_skipped(self):
        first = facts.add_fact(self.con, "Cooling fan: 120 mm", "hardware")
        self.assertTrue(first["added"])
        second = facts.add_fact(self.con, "cooling fan, 120 mm!", "hardware")
        self.assertFalse(second["added"])
        self.assertEqual(second["matched"], "text")
        self.assertEqual(1, second["duplicate_of"])
        self.assertEqual(1, len(facts.parse_entries(
            os.path.join(config.MEMORY_DIR, "hardware.md"))))

    def test_force_and_dedupe_false_bypass_the_check(self):
        facts.add_fact(self.con, "Registry is local", "delivery")
        self.assertTrue(facts.add_fact(self.con, "Registry is local", "delivery",
                                       force=True)["added"])
        self.assertTrue(facts.add_fact(self.con, "Registry is local", "delivery",
                                       dedupe=False)["added"])
        self.assertEqual(3, len(facts.parse_entries(
            os.path.join(config.MEMORY_DIR, "delivery.md"))))

    def test_secrets_are_redacted_before_the_write(self):
        result = facts.add_fact(
            self.con, "CI uses api_key=SUPERSECRET12345 and token: ghp_abcdefghijklmnopqrstuvwxyz012345",
            "secrets")
        self.assertTrue(result["added"])
        with open(os.path.join(config.MEMORY_DIR, "secrets.md"), encoding="utf-8") as f:
            text = f.read()
        self.assertNotIn("SUPERSECRET12345", text)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz012345", text)
        self.assertIn("api_key=***", text)

    def test_new_bullets_are_dated(self):
        facts.add_fact(self.con, "Use tabs in Makefile recipes", "build")
        with open(os.path.join(config.MEMORY_DIR, "build.md"), encoding="utf-8") as f:
            content = f.read()
        self.assertIn("- (", content)
        entry = facts.parse_entries(os.path.join(config.MEMORY_DIR, "build.md"))[0]
        self.assertIsNotNone(entry["added"])
        self.assertNotIn("(", entry["text"])


if __name__ == "__main__":
    unittest.main()
