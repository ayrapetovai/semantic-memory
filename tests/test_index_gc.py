r"""Garbage collection of per-root index databases."""

import os
import shutil
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from semantic_memory import gc  # noqa: E402


def make_db(index_dir, name, root, age_days=0):
    path = os.path.join(index_dir, name + ".db")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
    con.execute("INSERT INTO meta VALUES ('root', ?)", (root,))
    con.commit()
    con.close()
    if age_days:
        old = time.time() - age_days * 86400
        os.utime(path, (old, old))
    return path


class IndexGcTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.index_dir = os.path.join(self.tmp, "indexes")
        os.makedirs(self.index_dir)
        self.live = os.path.join(self.tmp, "live")
        self.dead = os.path.join(self.tmp, "dead")
        self.other = os.path.join(self.tmp, "other")
        os.makedirs(self.live)
        os.makedirs(self.dead)
        os.makedirs(self.other)
        self.live_db = make_db(self.index_dir, "live", self.live)
        self.dead_db = make_db(self.index_dir, "dead", self.dead)
        self.old_db = make_db(self.index_dir, "old", self.other, age_days=90)

    def test_collect_only_orphaned_and_old(self):
        shutil.rmtree(self.dead)  # the project directory was deleted
        found = gc.collect(self.index_dir, keep_roots=[self.live])
        self.assertEqual([self.dead_db], [p for p, _, _, _ in found])
        aged = gc.collect(self.index_dir, days=30, keep_roots=[self.live])
        self.assertEqual({self.dead_db, self.old_db}, {p for p, _, _, _ in aged})

    def test_remove_cleans_wal_and_shm(self):
        for suffix in ("-wal", "-shm"):
            with open(self.dead_db + suffix, "w", encoding="utf-8") as f:
                f.write("x")
        removed = gc.remove(self.dead_db)
        self.assertEqual(3, len(removed))
        self.assertFalse(os.path.exists(self.dead_db))
        self.assertFalse(os.path.exists(self.dead_db + "-wal"))
        self.assertTrue(os.path.exists(self.live_db))

    def test_collect_handles_missing_dir(self):
        shutil.rmtree(self.index_dir)
        self.assertEqual([], gc.collect(self.index_dir))


if __name__ == "__main__":
    unittest.main()
