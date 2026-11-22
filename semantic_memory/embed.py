r"""Эмбеддинги: fastembed + MiniLM (multilingual), кэш в config.MODEL_CACHE.

Векторы хранятся как float32 BLOB; cosine считается numpy-матрицей, если numpy
есть, иначе чистым Python. Без fastembed функции возвращают None и поиск
работает только текстом.
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
    """Векторы для списка текстов; None, если библиотека недоступна."""
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
    """rows: [(id, blob)] → [(score, id)] по косинусу с query."""
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
