r"""Project facts: memory\<topic>.md, one bullet = one fact.

Format: "-" is a document, "##" is a section whose name is included in the fact
text. The index is rebuilt only for changed topics (sha256 over the content).
"""

import hashlib
import os
import re

from . import config, db, embed

AUTO = "## Decision stream"
TOKEN_RE = re.compile(r"[A-Za-zА-ЯА-ЯЁё0-9]+")


def topic_files():
    if not os.path.isdir(config.MEMORY_DIR):
        return []
    names = sorted(f for f in os.listdir(config.MEMORY_DIR) if f.endswith(".md"))
    return [(os.path.splitext(f)[0], os.path.join(config.MEMORY_DIR, f))
            for f in names]


def parse_file(path):
    """Topic file → [(num, text)]; num is the bullet position in the file."""
    docs, heading = [], ""
    with open(path, "r", encoding="utf-8") as f:
        for line in f.read().splitlines():
            s = line.strip()
            if s.startswith("#"):
                heading = s.lstrip("#").strip()
                continue
            if s.startswith("- "):
                docs.append("%s :: %s" % (heading, s[2:]))
    return docs


def read_entries():
    out = []
    for topic, path in topic_files():
        for num, text in enumerate(parse_file(path), start=1):
            out.append((topic, num, text))
    return out


def topic_base(con, topic):
    stored = db.meta_get(con, "topic:" + topic)
    if stored is not None:
        return int(stored) * config.TOPIC_SPAN
    used = [int(v) for _, v in con.execute(
        "SELECT k, v FROM meta WHERE k LIKE 'topic:%'").fetchall()]
    index = max(used, default=0) + 1
    db.meta_set(con, "topic:" + topic, str(index))
    return index * config.TOPIC_SPAN


def purge_topic(con, topic):
    con.execute("DELETE FROM fact_vec WHERE id IN "
                "(SELECT id FROM fact WHERE topic = ?)", (topic,))
    con.execute("DELETE FROM fact_fts WHERE rowid IN "
                "(SELECT id FROM fact WHERE topic = ?)", (topic,))
    con.execute("DELETE FROM fact WHERE topic = ?", (topic,))


def rebuild_facts(con, force=False):
    """Fact index; embeddings only for changed topics. Returns (n, changed)."""
    by_topic = {}
    for topic, num, text in read_entries():
        by_topic.setdefault(topic, []).append((num, text))
    stored = {t for (t,) in con.execute("SELECT DISTINCT topic FROM fact").fetchall()}
    changed = []
    for topic, items in sorted(by_topic.items()):
        digest = hashlib.sha256(
            "\n".join(t for _, t in items).encode("utf-8")).hexdigest()
        if not force and db.meta_get(con, "fact:" + topic) == digest and topic in stored:
            continue
        base = topic_base(con, topic)
        purge_topic(con, topic)
        vectors = embed.embed([t[:config.MAX_EMBED_CHARS] for _, t in items])
        for i, (num, text) in enumerate(items):
            rid = base + num
            con.execute("INSERT INTO fact (id, topic, num, text) VALUES (?, ?, ?, ?)",
                        (rid, topic, num, text))
            con.execute("INSERT INTO fact_fts (rowid, text, topic) VALUES (?, ?, ?)",
                        (rid, text, topic))
            if vectors is not None:
                con.execute("INSERT INTO fact_vec (id, emb) VALUES (?, ?)",
                            (rid, embed.pack(vectors[i])))
        db.meta_set(con, "fact:" + topic, digest)
        db.meta_set(con, "embedded", "1" if vectors is not None else "0")
        changed.append(topic)
    for topic in sorted(stored - set(by_topic)):
        purge_topic(con, topic)
        db.meta_set(con, "fact:" + topic, "")
        changed.append(topic)
    con.commit()
    return len(read_entries()), changed


def _trim_bullets(content):
    """Drop the oldest bullets beyond config.MAX_TOPIC_BULLETS (new ones stay)."""
    lines = content.splitlines()
    bullets = [i for i, l in enumerate(lines) if l.startswith("- ")]
    excess = len(bullets) - config.MAX_TOPIC_BULLETS
    if excess <= 0:
        return content
    drop = set(bullets[:excess])
    return "\n".join(l for i, l in enumerate(lines) if i not in drop) + "\n"


def append_fact(con, text, topic):
    topic = resolve_topic(con, topic)
    os.makedirs(config.MEMORY_DIR, exist_ok=True)
    path = os.path.join(config.MEMORY_DIR, topic + ".md")
    content = ""
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
    content = _trim_bullets(content)
    if not content:
        content = "# %s\n\n%s\n\n" % (topic, AUTO)
    if AUTO not in content:
        content = content.rstrip() + "\n\n%s\n\n" % AUTO
    if not content.endswith("\n"):
        content += "\n"
    content += "- %s\n" % text.strip()
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return topic


def known_topics(con):
    names = {os.path.splitext(p)[0] for _, p in topic_files()}
    names |= {t for (t,) in con.execute("SELECT DISTINCT topic FROM fact").fetchall()}
    names |= {k[len("topic:"):] for k, _ in con.execute(
        "SELECT k, v FROM meta WHERE k LIKE 'topic:%'").fetchall()}
    return sorted(names)


def resolve_topic(con, name):
    if not name:
        return config.DEFAULT_TOPIC
    names = known_topics(con)
    if name in names:
        return name
    hits = [t for t in names if name in t]
    if len(hits) == 1:
        return hits[0]
    if re.fullmatch(r"[A-Za-z0-9_-]+", name):
        return name
    raise ValueError("topic %r not found; known topics: %s" % (name, ", ".join(names)))
