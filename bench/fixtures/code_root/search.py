"""Search helpers for the benchmark fixture."""


def search_facts(query, k=5):
    """Search memory topics by exact tokens and semantic similarity."""
    return [{"query": query, "source": "facts", "limit": k}]


def search_symbols(query, k=8):
    """Search indexed code symbols by name or description."""
    return [{"query": query, "source": "symbols", "limit": k}]


def filter_topics(hits, excluded):
    """Drop fixture topics such as chat and state from recalled facts."""
    return [hit for hit in hits if hit.get("topic") not in excluded]
