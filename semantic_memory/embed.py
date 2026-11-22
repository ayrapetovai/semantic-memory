r"""Embeddings: fastembed + MiniLM (multilingual), cached in config.MODEL_CACHE.

Vectors are stored as float32 BLOBs; cosine is computed with a numpy matrix when
numpy is available, otherwise in pure Python. Without fastembed these functions
return None and search works on text only.
"""

import array
import os

from . import config

_model = None


def available():
    try:
        import fastembed  # noqa: F401
        return True
    except ImportError:
        return False


def model():
    global _model
    if _model is None:
        os.environ["FASTEMBED_CACHE_PATH"] = config.MODEL_CACHE
        from fastembed import TextEmbedding
        _model = TextEmbedding(model_name=config.MODEL_NAME)
    return _model


def embed(texts):
    """Vectors for a list of texts; None when the library is unavailable."""
    if not texts:
        return []
    try:
        m = model()
    except Exception:
        return None
    out = []
    step = 32
    for i in range(0, len(texts), step):
        out.extend(m.embed(texts[i:i + step], batch_size=step))
    return [list(v) for v in out]


def pack(vec):
    return array.array("f", vec).tobytes()


def unpack(blob):
    return array.array("f", blob).tolist()


def cosine(query, rows):
    """rows: [(id, blob)] → [(score, id)] ranked by cosine similarity to query."""
    if not rows:
        return []
    try:
        import numpy as np
        mat = np.array([unpack(b) for _, b in rows], dtype=np.float32)
        q = np.asarray(query, dtype=np.float32)
        norm = np.linalg.norm(mat, axis=1)
        norm[norm == 0] = 1.0
        scores = (mat @ q) / (np.linalg.norm(q) or 1.0) / norm
        order = sorted(zip(scores.tolist(), [r[0] for r in rows]),
                       key=lambda kv: kv[0], reverse=True)
        return order
    except ImportError:
        qn = sum(c * c for c in query) or 1.0
        out = []
        for rid, blob in rows:
            v = unpack(blob)
            dot = sum(a * b for a, b in zip(query, v))
            nn = sum(c * c for c in v) or 1.0
            out.append((dot / (qn ** 0.5 * nn ** 0.5), rid))
        out.sort(reverse=True)
        return out
