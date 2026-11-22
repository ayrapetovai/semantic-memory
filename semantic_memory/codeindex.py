r"""Индекс кода: обход файлов, символы, вызовы, ссылки, резюме файла.

id символа стабилен: meta "file:<path>" закрепляет номер файла, символ получает
SYMBOL_ID_BASE + file_index * 10000 + позиция. Файл перестраивается только если
изменился его sha256; удалённый файл снимается из индекса.
"""

import hashlib
import os

from . import codeparse, config, db, embed

MAX_SYMBOLS_PER_FILE = 10000


def rel(path, root):
    p = os.path.relpath(path, root).replace("\\", "/")
    return p


def iter_code(root):
    """Файлы, которые стоит индексировать (языки из config.CODE_EXT)."""
    root = os.path.abspath(root)
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in config.SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            ext = os.path.splitext(name)[1].lower()
            if ext in config.SKIP_SUFFIXES or ext not in config.CODE_EXT:
                continue
            full = os.path.join(dirpath, name)
            try:
                if os.path.getsize(full) > config.MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            out.append(full)
    return sorted(out)


def file_base(con, rel_path):
    stored = db.meta_get(con, "file:" + rel_path)
    if stored is not None:
        return int(stored) * MAX_SYMBOLS_PER_FILE
    used = [int(v) for _, v in con.execute(
        "SELECT k, v FROM meta WHERE k LIKE 'file:%'").fetchall()]
    db.meta_set(con, "file:" + rel_path, str(max(used, default=0) + 1))
    return (max(used, default=0) + 1) * MAX_SYMBOLS_PER_FILE


def purge_file(con, rel_path):
    con.execute("DELETE FROM sym_vec WHERE id IN "
                "(SELECT id FROM sym WHERE file = ?)", (rel_path,))
    con.execute("DELETE FROM sym_fts WHERE rowid IN "
                "(SELECT id FROM sym WHERE file = ?)", (rel_path,))
    con.execute("DELETE FROM call WHERE file = ?", (rel_path,))
    con.execute("DELETE FROM ref WHERE file = ?", (rel_path,))
    con.execute("DELETE FROM sym WHERE file = ?", (rel_path,))
    con.execute("DELETE FROM file WHERE path = ?", (rel_path,))


def summarize(fp):
    """Резюме файла: первый docstring/комментарий + counts + imports."""
    head = ""
    for line in fp.lines[:40]:
        s = line.strip()
        if s.startswith("#") and not s.startswith("#!"):
            head = s.lstrip("#").strip()
            break
        if s.startswith("//") or s.startswith("/*"):
            head = s.lstrip("/").lstrip("*").strip()
            break
        if s and not s.startswith(("import", "from", "package", "use", "@")):
            head = s[:120]
            break
    kinds = {}
    for sym in fp.symbols:
        kinds[sym.kind] = kinds.get(sym.kind, 0) + 1
    parts = ["%s: %s" % (fp.lang, ", ".join(
        "%d %s" % (n, k) for k, n in sorted(kinds.items())) or "без символов")]
    names = [s.name for s in fp.symbols][:12]
    if names:
        parts.append("символы: " + ", ".join(names))
    if fp.imports:
        parts.append("импорты: " + ", ".join(fp.imports[:12]))
    if head:
        parts.insert(0, head)
    return " · ".join(parts)[:400]


def embed_text(sym):
    text = "%s (%s) %s %s %s" % (sym.qual, sym.kind, sym.sig, sym.doc,
                                 sym.body[:260])
    return " ".join(text.split())[:config.MAX_EMBED_CHARS]


def fts_text(sym):
    tokens = " ".join(codeparse.split_identifiers(sym.qual))
    return "%s %s\n%s\n%s %s" % (sym.qual, sym.sig, sym.body, sym.doc, tokens)


def index_paths(con, root, paths, force=False):
    """Индексировать файлы; пропускает неизменённые. → (files, symbols, changed)."""
    parsed = []
    known_names = {n for (n,) in con.execute("SELECT name FROM sym").fetchall()}
    for full in paths:
        rp = rel(full, root)
        try:
            with open(full, "r", encoding="utf-8-sig", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        row = con.execute("SELECT hash FROM file WHERE path = ?", (rp,)).fetchone()
        if not force and row and row[0] == digest:
            continue
        fp = codeparse.parse(full, text)
        parsed.append((rp, fp, digest, len(text.encode("utf-8"))))
        known_names |= {s.name for s in fp.symbols}
    if not parsed:
        return 0, 0, []

    vectors = embed.embed([embed_text(s) for _, fp, _, _ in parsed
                           for s in fp.symbols[:MAX_SYMBOLS_PER_FILE]])
    files = 0
    symbols = 0
    for rp, fp, digest, size in parsed:
        purge_file(con, rp)
        base = file_base(con, rp)
        for i, sym in enumerate(fp.symbols[:MAX_SYMBOLS_PER_FILE]):
            rid = base + i + 1
            con.execute(
                "INSERT INTO sym (id, file, name, qual, kind, start, end, sig, doc, body)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (rid, rp, sym.name, sym.qual, sym.kind, sym.start, sym.end,
                 sym.sig, sym.doc, sym.body),
            )
            con.execute("INSERT INTO sym_fts (rowid, text, file, lang) VALUES (?, ?, ?, ?)",
                        (rid, fts_text(sym), rp, fp.lang))
            if vectors is not None and vectors:
                idx = symbols + i
                if idx < len(vectors):
                    con.execute("INSERT INTO sym_vec (id, emb) VALUES (?, ?)",
                                (rid, embed.pack(vectors[idx])))
            symbols += 1
            for qual, callee, line in fp.calls:
                if qual == sym.qual:
                    con.execute("INSERT INTO call (sym_id, callee, file, line)"
                                " VALUES (?, ?, ?, ?)", (rid, callee, rp, line))
        seen = set()
        for name, line in fp.refs:
            if name in known_names and (name, line) not in seen:
                seen.add((name, line))
                con.execute("INSERT INTO ref (name, file, line) VALUES (?, ?, ?)",
                            (name, rp, line))
        con.execute(
            "INSERT INTO file (path, lang, lines, bytes, hash, summary, imports)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rp, fp.lang, len(fp.lines), size, digest, summarize(fp),
             ", ".join(fp.imports[:20])),
        )
        files += 1
    con.commit()
    return files, symbols, [rp for rp, _, _, _ in parsed]


def index_root(con, root, force=False):
    """Полный обход корня; эмбеддинги пересчитываются только для новых файлов."""
    paths = iter_code(root)
    files, symbols, changed = index_paths(con, root, paths, force=force)
    stored = {p for (p,) in con.execute("SELECT path FROM file").fetchall()}
    removed = sorted(stored - {rel(p, root) for p in paths})
    for rp in removed:
        purge_file(con, rp)
        db.meta_set(con, "hash:" + rp, "")
    db.meta_set(con, "root", os.path.abspath(root))
    db.meta_set(con, "embedded", "1" if embed.available() else "0")
    con.commit()
    return files, symbols, changed, removed
