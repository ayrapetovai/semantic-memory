r"""Retrieval metrics for the offline quality gates.

Rows are {"q", "expect": [...], "got": [...]}. For single-label query sets
(our fixtures) `precision_at_k` is the hit rate: 1.0 when the one expected item
is in the top k, 0.0 otherwise. Multi-label queries are scored as the fraction
of expected items found, capped at k.
"""

import statistics


def recall_at_k(got, expect, k):
    if not expect:
        return 1.0
    return len(set(got[:k]) & set(expect)) / len(set(expect))


def precision_at_k(got, expect, k):
    if not expect:
        return 1.0
    hits = len(set(got[:k]) & set(expect))
    return hits / min(len(set(expect)), k)


def reciprocal_rank(got, expect):
    for rank, item in enumerate(got, 1):
        if item in set(expect):
            return 1.0 / rank
    return 0.0


def mrr(rows):
    return statistics.mean(reciprocal_rank(r["got"], r["expect"]) for r in rows) if rows else 0.0


def noise_rate(got, expect, k):
    if not got[:k]:
        return 0.0
    return 1.0 - len(set(got[:k]) & set(expect)) / len(got[:k])


def p95(values):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))
    return ordered[index]


def summarize(rows, k):
    return {
        "queries": len(rows),
        "recall@k": statistics.mean(recall_at_k(r["got"], r["expect"], k) for r in rows) if rows else 0.0,
        "precision@k": statistics.mean(precision_at_k(r["got"], r["expect"], k) for r in rows)
        if rows else 0.0,
        "mrr": mrr(rows),
        "noise@k": statistics.mean(noise_rate(r["got"], r["expect"], k) for r in rows) if rows else 0.0,
    }
