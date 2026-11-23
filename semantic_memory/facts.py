r"""Project facts: memory\<topic>.md, one bullet = one fact.

Format: "-" is a document, "##" is a section whose name is included in the fact
text. A bullet may carry a date prefix "- (YYYY-MM-DD) text" and an optional
"superseded YYYY-MM-DD" marker. Bullets under a heading containing "archive" or
"superseded" stay in the file (and in git) but are not indexed or injected.

The index is rebuilt only for changed topics (sha256 over the content). New
bullets are redacted (secrets masked) and deduplicated against the same topic.
"""

import datetime
import hashlib
import os
import re

from . import config, db, embed

AUTO = "## Decision stream"
DATE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\b")
SUPERSEDED_RE = re.compile(r"superseded\s+(\d{4}-\d{2}-\d{2})", re.I)
ARCHIVE_RE = re.compile(r"\b(archive|superseded)\b", re.I)
BULLET_RE = re.compile(r"^-\s+(?:\(([^)]*)\)\s*)?(.*)$")

# High-confidence secrets only: a false positive costs a masked word, a false
# negative commits a credential into memory/*.md.
_SECRETS = [
    (re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|secret|password|passwd)"
                r"\s*[:=]\s*\S+"), r"\1=***"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "sk-***"),
    (re.compile(r"\bghp_[A-Za-z0-9]{20,}"), "ghp_***"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "AKIA***"),
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{10,}"), "Bearer ***"),
    (re.compile(r"\b[A-Fa-f0-9]{40,}\b"), "***"),
]


def today():
    return datetime.date.today().isoformat()


def redact(text):
    """Mask credentials before a bullet is written to a git-tracked file."""
    for rx, repl in _SECRETS:
        text = rx.sub(repl, text)
    return text


def normalize(text):
    """Comparison key: bullet text without the "section ::" prefix or punctuation."""
    t = text.split(" :: ", 1)[-1].lower()
    return " ".join(re.sub(r"[^\w]+", " ", t).split())


def topic_files():
    if not os.path.isdir(config.MEMORY_DIR):
        return []
    names = sorted(f for f in os.listdir(config.MEMORY_DIR) if f.endswith(".md"))
    return [(os.path.splitext(f)[0], os.path.join(config.MEMORY_DIR, f))
            for f in names]


def _bullet(raw):
    """Bullet line -> (text, added, superseded)."""
    m = BULLET_RE.match(raw)
    if not m:
        return raw.strip(), None, None
    prefix, body = m.group(1), m.group(2)
    added = superseded = None
    if prefix:
        dm = DATE_RE.match(prefix.strip())
        if dm:
            added = dm.group(1)
            sm = SUPERSEDED_RE.search(prefix)
            if sm:
                superseded = sm.group(1)
        else:
            body = "(%s) %s" % (prefix, body)  # not a date: keep the text intact
    return body.strip(), added, superseded


def parse_entries(path):
    """Topic file -> [{"num","text","added","superseded"}]; archived bullets skipped."""
    entries, heading = [], ""
    with open(path, "r", encoding="utf-8") as f:
        for line in f.read().splitlines():
            s = line.strip()
            if s.startswith("#"):
                heading = s.lstrip("#").strip()
                continue
            if not s.startswith("- ") or ARCHIVE_RE.search(heading):
                continue
            text, added, superseded = _bullet(s)
            if not text:
                continue
            entries.append({
                "num": len(entries) + 1,
                "text": "%s :: %s" % (heading, text) if heading else text,
                "added": added,
                "superseded": superseded,
            })
    return entries


def parse_file(path):
    """Topic file -> [text]; kept for callers that only need the documents."""
    return [e["text"] for e in parse_entries(path)]


def read_entries():
    out = []
    for topic, path in topic_files():
        for entry in parse_entries(path):
            out.append((topic, entry["num"], entry["text"],
                        entry["added"], entry["superseded"]))
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


def _mtime_date(path):
    try:
        return datetime.date.fromtimestamp(os.path.getmtime(path)).isoformat()
    except OSError:
        return None


def rebuild_facts(con, force=False):
    """Fact index; embeddings only for changed topics. Returns (n, changed)."""
    by_topic, paths = {}, dict(topic_files())
    for topic, num, text, added, superseded in read_entries():
        if superseded:  # kept in the file for history, not indexed or injected
            continue
        by_topic.setdefault(topic, []).append((num, text, added, superseded))
    stored = {t for (t,) in con.execute("SELECT DISTINCT topic FROM fact").fetchall()}
    changed = []
    for topic, items in sorted(by_topic.items()):
        fallback = _mtime_date(paths[topic]) if topic in paths else None
        rows = [(num, text, added or fallback, superseded)
                for num, text, added, superseded in items]
        digest = hashlib.sha256(
            "\n".join("%s|%s|%s" % (text, added or "", superseded or "")
                      for _, text, added, superseded in rows).encode("utf-8")
        ).hexdigest()
        if not force and db.meta_get(con, "fact:" + topic) == digest and topic in stored:
            continue
        base = topic_base(con, topic)
        purge_topic(con, topic)
        vectors = embed.embed([text[:config.MAX_EMBED_CHARS] for _, text, _, _ in rows])
        for i, (num, text, added, superseded) in enumerate(rows):
            rid = base + num
            con.execute("INSERT INTO fact (id, topic, num, text, added, superseded)"
                        " VALUES (?, ?, ?, ?, ?, ?)",
                        (rid, topic, num, text, added, superseded))
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
    """Move active bullets beyond MAX_TOPIC_BULLETS into a ## Archive section."""
    lines = content.splitlines()
    cut = len(lines)
    for i, line in enumerate(lines):
        if re.match(r"^##\s+", line) and ARCHIVE_RE.search(line):
            cut = i
            break
    active, rest = lines[:cut], lines[cut:]
    bullets = [i for i, line in enumerate(active) if line.startswith("- ")]
    excess = len(bullets) - config.MAX_TOPIC_BULLETS
    if excess <= 0:
        return content
    drop = set(bullets[:excess])
    moved = [active[i] for i in bullets[:excess]]
    head = "\n".join(line for i, line in enumerate(active) if i not in drop).rstrip()
    if not rest:
        rest = ["", "## Archive", ""]
    tail = "\n".join(rest).rstrip()
    return head + "\n" + tail + "\n" + "\n".join(moved) + "\n"


def append_fact(con, text, topic):
    topic = resolve_topic(con, topic)
    text = text.strip()
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
    if not DATE_RE.match(text):
        text = "(%s) %s" % (today(), text)
    content += "- %s\n" % text
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return topic


def _cos(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def _vector_duplicate(text, entries, min_sim):
    if not entries or not embed.available():
        return None
    try:
        vectors = embed.embed([text] + [e["text"] for e in entries])
    except Exception:
        return None
    if not vectors or len(vectors) != len(entries) + 1:
        return None
    best = None
    for entry, vector in zip(entries, vectors[1:]):
        score = _cos(vectors[0], vector)
        if best is None or score > best[0]:
            best = (score, entry)
    if best and best[0] >= min_sim:
        return best
    return None


def add_fact(con, text, topic, dedupe=True, force=False):
    """Write one fact; return {"topic","added","duplicate_of"?,...}.

    Duplicate = same normalized text in the topic, or cosine >= DEDUPE_SIM
    against an existing active bullet of the topic. force=True bypasses both.
    """
    topic = resolve_topic(con, topic)
    text = redact((text or "").strip())
    if not text:
        raise ValueError("empty fact")
    path = os.path.join(config.MEMORY_DIR, topic + ".md")
    entries = parse_entries(path) if os.path.isfile(path) else []
    if dedupe and not force:
        key = normalize(text)
        for entry in entries:
            if normalize(entry["text"]) == key:
                return {"topic": topic, "added": False,
                        "duplicate_of": entry["num"], "matched": "text"}
        near = _vector_duplicate(text, entries, config.DEDUPE_SIM)
        if near:
            return {"topic": topic, "added": False,
                    "duplicate_of": near[1]["num"], "matched": "vector",
                    "score": round(near[0], 4)}
    append_fact(con, text, topic)
    return {"topic": topic, "added": True}


def _remove_exact_duplicates(path, nums):
    """Drop bullet lines whose (active) number is in nums; archive stays intact."""
    with open(path, "r", encoding="utf-8") as f:
        lines = f.read().splitlines()
    out, heading, active, removed = [], "", 0, 0
    for line in lines:
        s = line.strip()
        if s.startswith("#"):
            heading = s.lstrip("#").strip()
        if s.startswith("- ") and not ARCHIVE_RE.search(heading):
            active += 1
            if active in nums:
                removed += 1
                continue
        out.append(line)
    if removed:
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(out) + "\n")
    return removed


def lint(con, apply=False, min_sim=None, stale_days=None):
    """Report duplicates, near-duplicates, undated and stale facts.

    apply=True removes exact duplicates only; near-duplicates are reported for a
    human decision (merging two differently worded decisions automatically is
    too risky).
    """
    min_sim = config.DEDUPE_SIM if min_sim is None else min_sim
    stale_days = config.STALE_DAYS if stale_days is None else stale_days
    findings, exact = [], {}
    for topic, path in topic_files():
        entries = parse_entries(path)
        seen, dup_nums = {}, set()
        for entry in entries:
            key = normalize(entry["text"])
            if key in seen:
                findings.append({"kind": "duplicate", "topic": topic,
                                 "num": entry["num"], "of": seen[key],
                                 "text": entry["text"]})
                dup_nums.add(entry["num"])
            else:
                seen[key] = entry["num"]
        if dup_nums:
            exact[topic] = dup_nums
        if len(entries) > 1 and embed.available():
            try:
                vectors = embed.embed([e["text"] for e in entries])
            except Exception:
                vectors = None
            if vectors and len(vectors) == len(entries):
                for i in range(len(entries)):
                    if entries[i]["num"] in dup_nums:
                        continue
                    for j in range(i + 1, len(entries)):
                        if entries[j]["num"] in dup_nums:
                            continue
                        score = _cos(vectors[i], vectors[j])
                        if score >= min_sim:
                            findings.append({"kind": "near-duplicate", "topic": topic,
                                             "num": entries[j]["num"],
                                             "of": entries[i]["num"],
                                             "score": round(score, 4),
                                             "text": entries[j]["text"]})
                            dup_nums.add(entries[j]["num"])
        for entry in entries:
            if entry["num"] in dup_nums:
                continue
            if not entry["added"]:
                findings.append({"kind": "undated", "topic": topic,
                                 "num": entry["num"], "text": entry["text"]})
                continue
            try:
                age = (datetime.date.today()
                       - datetime.date.fromisoformat(entry["added"])).days
            except ValueError:
                continue
            if age > stale_days:
                findings.append({"kind": "stale", "topic": topic,
                                 "num": entry["num"], "age": age,
                                 "text": entry["text"]})
    applied = 0
    if apply and exact:
        for topic, nums in exact.items():
            applied += _remove_exact_duplicates(dict(topic_files())[topic], nums)
        if applied:
            rebuild_facts(con)
    findings.sort(key=lambda f: (f["topic"], f["num"], f["kind"]))
    return {"findings": findings, "removed": applied, "topics": len(topic_files())}


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
