r"""Search: hybrid FTS5 (exact tokens) + vectors (meaning), merged by rank.

Two areas — facts (memory\*.md) and code symbols — are ranked separately and never
mixed: the LLM finds the code first, then reads a symbol in full.
"""

import re
import sqlite3

from . import codeparse, config, db, embed

TOKEN_RE = re.compile(r"[A-Za-zА-Яа-яЁё0-9_]+")


def query_tokens(query):
    tokens = TOKEN_RE.findall(query)
    out = list(tokens)
    for t in tokens:
        out.extend(codeparse.split_identifiers(t))
    return [t for t in dict.fromkeys(out) if len(t) > 1]


def _fts(con, table, query, limit, where=None, args=None):
    tokens = query_tokens(query)
    if not tokens:
        return []
    match = " OR ".join('"%s"' % t for t in tokens)
    sql = "SELECT rowid FROM %s WHERE text MATCH ?" % table
    params = [match]
    if where:
        sql += " AND " + where
        params += args or []
    sql += " ORDER BY bm25(%s) LIMIT ?" % table
    params.append(limit)
    try:
        return [r[0] for r in con.execute(sql, params).fetchall()]
    except sqlite3.OperationalError:
        return []


def _fts_count(con, table, query, limit, where=None, args=None):
    """Ranking without BM25: how many tokens appear in the document."""
    tokens = query_tokens(query)
    if not tokens:
        return []
    sql = "SELECT rowid, text FROM %s" % table
    if where:
        sql += " WHERE " + where
    rows = con.execute(sql, args or []).fetchall()
    scored = []
    for rid, text in rows:
        low = text.lower()
        hits = sum(1 for t in tokens if t in low)
        if hits:
            scored.append((hits, rid))
    scored.sort(reverse=True)
    return [rid for _, rid in scored[:limit]]


def _vec(con, table, query, limit, where=None, args=None, min_sim=None,
         return_scores=False):
    vectors = embed.embed([query[:config.MAX_EMBED_CHARS]])
    if not vectors:
        return []
    sql = "SELECT id, emb FROM %s" % table
    if where:
        sql += " WHERE " + where
    try:
        rows = con.execute(sql, args or []).fetchall()
    except sqlite3.OperationalError:
        return []
    if not rows:
        return []
    scores = embed.cosine(vectors[0], rows)
    if min_sim is not None:
        scores = [pair for pair in scores if pair[0] >= min_sim]
    scores = scores[:limit]
    return scores if return_scores else [rid for _, rid in scores]


def merge(ranks, window=60):
    """Reciprocal rank fusion: 1/(window+rank) over each list."""
    scores = {}
    for lst in ranks:
        for rank, rid in enumerate(lst, start=1):
            scores[rid] = scores.get(rid, 0.0) + 1.0 / (window + rank)
    return sorted(scores.items(), key=lambda kv: kv[1], reverse=True)


def _fact_where(topic, exclude_topics):
    """WHERE fragment over the fact/topic columns (None when nothing is filtered)."""
    clauses, args = [], []
    if topic:
        clauses.append("topic = ?")
        args.append(topic)
    if exclude_topics:
        clauses.append("topic NOT IN (%s)" % ", ".join("?" * len(exclude_topics)))
        args.extend(exclude_topics)
    return " AND ".join(clauses) if clauses else None, args


def _fact_vec_where(topic, exclude_topics):
    inner, args = _fact_where(topic, exclude_topics)
    return ("id IN (SELECT id FROM fact WHERE %s)" % inner, args) if inner else (None, [])


def search_facts(con, query, k=5, topic=None, use_embed=True, exclude_topics=None,
                 min_sim=None):
    """Facts ranked by hybrid FTS + vectors.

    Vector-only hits below `min_sim` (config.MIN_VECTOR_SIM by default) are
    dropped before fusion, so a nonsense query returns nothing instead of the
    nearest five bullets. A vector-only hit with no shared query token must be
    very similar (config.MIN_VECTOR_STRONG_SIM) — gibberish can score 0.5
    against an unrelated short bullet. `exclude_topics` keeps prompt/compaction
    noise out.
    """
    want = max(40, k * 4) if topic else max(20, k * 4)
    if min_sim is None:
        min_sim = config.MIN_VECTOR_SIM
    where, args = _fact_where(topic, exclude_topics)
    vec_where, vec_args = _fact_vec_where(topic, exclude_topics)
    if use_embed:
        fts = _fts(con, "fact_fts", query, want, where, args)
        vec_scored = _vec(con, "fact_vec", query, want, vec_where, vec_args,
                          min_sim, return_scores=True)
        vec = [rid for _, rid in vec_scored]
        ranks = [fts, vec]
    else:
        fts, vec = _fts_count(con, "fact_fts", query, want, where, args), []
        vec_scored = []
        ranks = [fts]
    fts_ids, vec_ids = set(fts), set(vec)
    sims = {rid: sim for sim, rid in vec_scored}
    query_words = set(query_tokens(query))
    out = []
    for rid, score in merge(ranks):
        row = con.execute("SELECT topic, num, text, added, superseded FROM fact "
                          "WHERE id = ?", (rid,)).fetchone()
        if not row:
            continue
        via = "both" if rid in fts_ids and rid in vec_ids else (
            "fts" if rid in fts_ids else "vec")
        if via == "vec" and not (query_words & set(query_tokens(row[2]))):
            if sims.get(rid, 0.0) < config.MIN_VECTOR_STRONG_SIM:
                continue
        out.append({"id": rid, "score": round(score, 4), "topic": row[0],
                    "num": row[1], "text": row[2], "added": row[3],
                    "superseded": row[4], "via": via})
        if len(out) >= k:
            break
    return out


def search_symbols(con, query, k=8, lang=None, path_filter=None, use_embed=True):
    want = max(k * 4, 24)
    clauses = []
    args = []
    if lang:
        clauses.append("lang = ?")
        args.append(lang)
    if path_filter:
        clauses.append("file LIKE ?")
        args.append("%" + path_filter + "%")
    where = " AND ".join(clauses) if clauses else None
    vec_clauses = []
    if lang:
        vec_clauses.append(
            "id IN (SELECT sym.id FROM sym JOIN file ON sym.file = file.path "
            "WHERE file.lang = ?)")
    if path_filter:
        vec_clauses.append("id IN (SELECT id FROM sym WHERE file LIKE ?)")
    vec_where = " AND ".join(vec_clauses) if vec_clauses else None
    if use_embed:
        ranks = [_fts(con, "sym_fts", query, want, where, args),
                 _vec(con, "sym_vec", query, want, vec_where, args)]
    else:
        ranks = [_fts_count(con, "sym_fts", query, want, where, args)]
    out = []
    for rid, score in merge(ranks)[:k]:
        row = con.execute(
            "SELECT file, qual, kind, start, end, sig, doc FROM sym WHERE id = ?",
            (rid,)).fetchone()
        if row:
            out.append({"id": rid, "score": round(score, 4), "file": row[0],
                        "qual": row[1], "kind": row[2], "start": row[3],
                        "end": row[4], "sig": row[5], "doc": row[6]})
    return out


def symbol_by_name(con, name, k=8):
    """Exact name or qual → symbols; exact matches first."""
    name = name.strip()
    rows = con.execute(
        "SELECT id, file, qual, kind, start, end, sig, doc FROM sym "
        "WHERE qual = ? OR name = ? ORDER BY length(qual), file LIMIT ?",
        (name, name, k)).fetchall()
    if not rows:
        rows = con.execute(
            "SELECT id, file, qual, kind, start, end, sig, doc FROM sym "
            "WHERE qual LIKE ? OR name LIKE ? ORDER BY length(qual), file LIMIT ?",
            ("%s%%" % name, "%s%%" % name, k)).fetchall()
    return [{"id": r[0], "file": r[1], "qual": r[2], "kind": r[3], "start": r[4],
             "end": r[5], "sig": r[6], "doc": r[7]} for r in rows]


def symbol_body(con, rid):
    row = con.execute("SELECT file, qual, kind, start, end, body FROM sym "
                      "WHERE id = ?", (rid,)).fetchone()
    if not row:
        return None
    return {"file": row[0], "qual": row[1], "kind": row[2], "start": row[3],
            "end": row[4], "body": row[5][:config.MAX_BODY_CHARS]}


def callers(con, name, k=20):
    """Call sites of a symbol, including module-level calls (owner is None).

    `line` is the call site, not the owner's start line.
    """
    # Module-level calls have sym_id NULL: use the call's own file when there is
    # no owner symbol to join to.
    rows = con.execute(
        "SELECT DISTINCT COALESCE(s.file, c.file) AS file, s.qual, s.kind, c.line "
        "FROM call c LEFT JOIN sym s ON s.id = c.sym_id WHERE c.callee = ? "
        "ORDER BY COALESCE(s.file, c.file), c.line LIMIT ?",
        (name, k)).fetchall()
    return [{"file": r[0], "qual": r[1], "kind": r[2], "line": r[3]} for r in rows]


def callees(con, qual, k=30):
    rows = con.execute(
        "SELECT DISTINCT c.callee FROM call c JOIN sym s ON s.id = c.sym_id "
        "WHERE s.qual = ? LIMIT ?", (qual, k)).fetchall()
    return [r[0] for r in rows]


def references(con, name, k=25):
    rows = con.execute("SELECT file, line FROM ref WHERE name = ? LIMIT ?",
                       (name, k)).fetchall()
    return [{"file": r[0], "line": r[1]} for r in rows]


def file_map(con, path_filter=None, limit=40):
    sql = "SELECT path, lang, lines, summary FROM file"
    args = []
    if path_filter:
        sql += " WHERE path LIKE ?"
        args.append("%" + path_filter + "%")
    sql += " ORDER BY path LIMIT ?"
    args.append(limit)
    rows = con.execute(sql, args).fetchall()
    return [{"path": r[0], "lang": r[1], "lines": r[2], "summary": r[3]}
            for r in rows]


def stats(con):
    facts = con.execute("SELECT COUNT(*) FROM fact").fetchone()[0]
    syms = con.execute("SELECT COUNT(*) FROM sym").fetchone()[0]
    files = con.execute("SELECT COUNT(*) FROM file").fetchone()[0]
    calls = con.execute("SELECT COUNT(*) FROM call").fetchone()[0]
    by_lang = con.execute(
        "SELECT lang, COUNT(*) FROM file GROUP BY lang ORDER BY 2 DESC").fetchall()
    return {"facts": facts, "symbols": syms, "files": files, "calls": calls,
            "languages": {lang: n for lang, n in by_lang},
            "embedded": db.meta_get(con, "embedded") == "1",
            "root": db.meta_get(con, "root")}
