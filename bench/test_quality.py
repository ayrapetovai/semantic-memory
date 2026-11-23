r"""Offline retrieval quality gates. Run: python -m pytest bench/test_quality.py -v.

The fixtures are copied to a temporary directory: no test writes into the repo.
The first call warms the embedding model; latency is measured afterwards.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

BENCH = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BENCH)
sys.path.insert(0, ROOT)

from semantic_memory import codeindex, config, db, facts, search  # noqa: E402
from bench import score  # noqa: E402

CODE_RECALL = 0.85
CODE_MRR = 0.60
FACTS_PRECISION = 0.80
LATENCY_P95 = 0.20  # seconds, warm recall on the small fixture


def load_queries():
    with open(os.path.join(BENCH, "queries.json"), encoding="utf-8") as f:
        return json.load(f)


class QualityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="sm-bench-")
        cls.old_memory = config.MEMORY_DIR
        cls.memory = os.path.join(cls.tmp, "memory")
        cls.code_root = os.path.join(cls.tmp, "code_root")
        shutil.copytree(os.path.join(BENCH, "fixtures", "facts_topics"), cls.memory)
        shutil.copytree(os.path.join(BENCH, "fixtures", "code_root"), cls.code_root)
        config.MEMORY_DIR = cls.memory
        cls.con = db.connect(os.path.join(cls.tmp, "index.db"))
        facts.rebuild_facts(cls.con)
        codeindex.index_root(cls.con, cls.code_root)
        cls.queries = load_queries()

    @classmethod
    def tearDownClass(cls):
        config.MEMORY_DIR = cls.old_memory
        cls.con.close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def code_rows(self):
        rows = []
        for case in self.queries["code"]:
            got = ["%s:%s" % (h["file"], h["qual"])
                   for h in search.search_symbols(self.con, case["q"], case["k"])]
            rows.append({"q": case["q"], "expect": case["expect"], "got": got})
        return rows

    def fact_rows(self, group="facts"):
        rows = []
        for case in self.queries[group]:
            got = ["%s:%d" % (h["topic"], h["num"])
                   for h in search.search_facts(
                       self.con, case["q"], case["k"],
                       exclude_topics=case.get("excludeTopics"))]
            rows.append({"q": case["q"], "expect": case["expect"], "got": got})
        return rows

    def test_code_recall_and_mrr(self):
        rows = self.code_rows()
        stats = score.summarize(rows, 5)
        print("code:", stats)
        self.assertGreaterEqual(stats["recall@k"], CODE_RECALL, stats)
        self.assertGreaterEqual(stats["mrr"], CODE_MRR, stats)

    def test_facts_precision(self):
        rows = self.fact_rows()
        stats = score.summarize(rows, 5)
        print("facts:", stats)
        self.assertGreaterEqual(stats["precision@k"], FACTS_PRECISION, stats)

    def test_nonsense_injects_nothing(self):
        for row in self.fact_rows("nonsense"):
            self.assertEqual([], row["got"], row)

    def test_chat_is_excluded(self):
        for row in self.fact_rows("chat_excluded"):
            self.assertFalse(any(g.startswith("chat:") for g in row["got"]), row)

    def test_warm_recall_latency(self):
        query = "hybrid ranking reciprocal rank fusion"
        search.search_facts(self.con, query, 5)  # warm the model and the cache
        times = []
        for _ in range(20):
            start = time.perf_counter()
            search.search_facts(self.con, query, 5)
            times.append(time.perf_counter() - start)
        measured = score.p95(times)
        print("recall p95: %.4fs" % measured)
        self.assertLess(measured, LATENCY_P95, "p95 %.4fs" % measured)


if __name__ == "__main__":
    unittest.main()
