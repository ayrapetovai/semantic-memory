"""Ranking engine for the benchmark fixture."""

from . import search


def rank_hybrid(query, k=8):
    """Merge exact-token and vector ranks with reciprocal rank fusion."""
    token_hits = search.search_facts(query, k)
    vector_hits = search.search_symbols(query, k)
    return merge_ranks([token_hits, vector_hits], k)


def merge_ranks(ranks, k=8):
    """Reciprocal rank fusion: 1/(window + rank) over each ranked list."""
    scores = {}
    for rank_list in ranks:
        for rank, hit in enumerate(rank_list, 1):
            scores[hit] = scores.get(hit, 0.0) + 1.0 / (60 + rank)
    return sorted(scores, key=scores.get, reverse=True)[:k]


class RankCache:
    """Tiny cache of the last query and its ranked results."""

    def __init__(self):
        self.query = None
        self.results = []

    def remember(self, query, results):
        self.query = query
        self.results = results

    def forget(self):
        self.query = None
        self.results = []
